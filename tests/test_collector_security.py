"""Régressions fonctionnelles des frontières de sécurité de la collecte."""

import io
import ssl
import sys
import urllib.request
import urllib.response
from datetime import datetime, timedelta
from email.message import Message
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models import Asset, IpamRecord, Run
from collector import exports, inventory_runner, netbox_client as netbox
from collector import proxmox_client as proxmox


BASE = "https://netbox.test"
ENTRY = BASE + netbox.API_PATH


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("NETBOX_URL", BASE)
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    monkeypatch.setenv("PROXMOX_URL", "https://proxmox.test")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "test-id")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "test-token")
    for name in ("NETBOX_VERIFY_SSL", "PROXMOX_VERIFY_SSL", "PROXMOX_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("socket.create_connection", Mock(side_effect=AssertionError("Real network forbidden")))


def page(next_url=None, **extra):
    return {"results": [], "next": next_url, **extra}


@pytest.mark.parametrize("next_url", [
    "https://user:password@netbox.test/api/ipam/ip-addresses/",
    "http://netbox.test/api/ipam/ip-addresses/",
    "https://attacker.test/api/ipam/ip-addresses/",
    "https://netbox.test:8443/api/ipam/ip-addresses/",
    "https://netbox.test:invalid/",
    "https://[invalid/",
])
def test_rejects_malicious_pagination_before_transport(next_url):
    transport = Mock(return_value=page(next_url))
    with pytest.raises(netbox.NetBoxClientError, match="interdite|invalide"):
        netbox.fetch_ipam_records(transport=transport)
    assert transport.call_count == 1
    assert transport.call_args.args[0] == ENTRY


@pytest.mark.parametrize("next_url", ["?page=2", BASE + netbox.API_PATH + "?page=2"])
def test_accepts_relative_and_same_origin_pagination(next_url):
    transport = Mock(side_effect=[page(next_url), page()])
    assert netbox.fetch_ipam_records(transport=transport) == []
    assert [call.args[0] for call in transport.call_args_list] == [ENTRY, ENTRY + "?page=2"]


def test_refuses_cross_origin_http_redirect_without_forwarding_token(monkeypatch):
    requests = []

    class FakeHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            assert request.full_url == ENTRY
            headers = Message()
            headers["Location"] = "https://attacker.test/stolen"
            response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
            response.msg = "Found"
            return response

    monkeypatch.setattr(urllib.request, "HTTPSHandler", FakeHTTPSHandler)
    with pytest.raises(netbox.NetBoxClientError, match="Redirection|HTTP 302"):
        netbox.fetch_ipam_records()
    assert len(requests) == 1


def test_proxmox_verifies_tls_by_default():
    transport = Mock(return_value={"data": []})
    assert proxmox.fetch_proxmox_vms(transport=transport) == []
    assert transport.call_count > 0
    assert all(call.args[3] is True for call in transport.call_args_list)


def test_proxmox_default_context_requires_certificates():
    context = proxmox._ssl_context(True)
    assert (context.verify_mode, context.check_hostname) == (ssl.CERT_REQUIRED, True)


def test_proxmox_loads_configured_ca(monkeypatch, tmp_path):
    ca = str(tmp_path / "ca.pem")
    monkeypatch.setenv("PROXMOX_CA_BUNDLE", ca)
    context = Mock()
    monkeypatch.setattr(ssl, "create_default_context", Mock(return_value=context))
    assert proxmox._ssl_context(True) is context
    context.load_verify_locations.assert_called_once_with(cafile=ca)


@pytest.mark.parametrize("invalid_contents", [None, "not a certificate"])
def test_invalid_ca_raises_runtimeerror_without_insecure_fallback(monkeypatch, tmp_path, invalid_contents):
    ca = tmp_path / "ca.pem"
    if invalid_contents is not None:
        ca.write_text(invalid_contents)
    monkeypatch.setenv("PROXMOX_CA_BUNDLE", str(ca))
    with pytest.raises(RuntimeError, match="bundle CA"):
        proxmox._ssl_context(True)


@pytest.mark.parametrize("module,setting,error", [
    (netbox, "NETBOX_VERIFY_SSL", netbox.NetBoxClientError),
    (proxmox, "PROXMOX_VERIFY_SSL", proxmox.ProxmoxClientError),
])
@pytest.mark.parametrize("value", ["false", "invalid"])
def test_production_rejects_insecure_or_invalid_tls(monkeypatch, module, setting, error, value):
    monkeypatch.setenv(setting, value)
    transport = Mock()
    fetch = module.fetch_ipam_records if module is netbox else module.fetch_proxmox_vms
    with pytest.raises(error, match="TLS"):
        fetch(transport=transport)
    transport.assert_not_called()


def test_rejects_page_over_one_mib():
    transport = Mock(return_value=page(padding="x" * (1024 * 1024)))
    with pytest.raises(netbox.NetBoxClientError, match="octets"):
        netbox.fetch_ipam_records(transport=transport)
    assert transport.call_count == 1


def test_rejects_total_over_five_mib():
    transport = Mock(side_effect=[
        page(f"?page={number + 1}", padding="x" * 900000)
        for number in range(6)
    ])
    with pytest.raises(netbox.NetBoxClientError, match="octets"):
        netbox.fetch_ipam_records(transport=transport)
    assert transport.call_count == 6


def test_stops_after_fifty_pages():
    transport = Mock(side_effect=[page(f"?page={number + 1}") for number in range(51)])
    with pytest.raises(netbox.NetBoxClientError, match="pages"):
        netbox.fetch_ipam_records(transport=transport)
    assert transport.call_count == 50


def test_rejects_pagination_loop():
    transport = Mock(return_value=page(ENTRY))
    with pytest.raises(netbox.NetBoxClientError, match="boucle"):
        netbox.fetch_ipam_records(transport=transport)
    assert transport.call_count == 1


def test_rejects_more_than_ten_thousand_records():
    transport = Mock(return_value={"results": [{}] * 10001, "next": None})
    with pytest.raises(netbox.NetBoxClientError, match="enregistrements"):
        netbox.fetch_ipam_records(transport=transport)


@pytest.mark.parametrize("module,error", [(netbox, netbox.NetBoxClientError), (proxmox, proxmox.ProxmoxClientError)])
def test_transport_reads_at_most_one_mib_plus_one(monkeypatch, module, error):
    response = Mock()
    response.read.return_value = b"x" * (1024 * 1024 + 1)
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    monkeypatch.setattr(urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(error, match="octets"):
        module._default_transport(BASE, {}, 1, True)
    response.read.assert_called_once_with(1024 * 1024 + 1)


def test_netbox_deadline_bounds_timeout_and_stops_late_response(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(netbox.time, "monotonic", lambda: clock[0])

    def delayed_response(*args):
        clock[0] = 103.0
        return page("?page=2")

    transport = Mock(side_effect=delayed_response)
    token = netbox.RUN_DEADLINE.set(102.0)
    try:
        with pytest.raises(TimeoutError, match="Budget"):
            netbox.fetch_ipam_records(transport=transport)
    finally:
        netbox.RUN_DEADLINE.reset(token)
    assert transport.call_count == 1
    assert 0 < transport.call_args.args[2] <= 2


def test_runner_shares_deadline_rolls_back_and_recovers(db, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(netbox.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(exports, "run_exports", Mock())
    monkeypatch.setattr("app.notifications.notify_run", Mock())
    deadlines = []
    previous = netbox.RUN_DEADLINE.get()

    def vms():
        deadlines.append(netbox.RUN_DEADLINE.get())
        clock[0] += inventory_runner.RUN_BUDGET - 1
        return []

    def ipam():
        deadlines.append(netbox.RUN_DEADLINE.get())
        clock[0] += 2
        return []

    failed = inventory_runner.run_inventory(collect_vms=vms, collect_ipam=ipam)
    assert failed.status == "FAIL"
    assert "Budget" in failed.error_message
    assert deadlines == [100.0 + inventory_runner.RUN_BUDGET] * 2
    assert Asset.query.count() == IpamRecord.query.count() == 0
    assert netbox.RUN_DEADLINE.get() == previous
    recovered = inventory_runner.run_inventory(collect_vms=lambda: [], collect_ipam=lambda: [])
    assert recovered.status == "SUCCESS"


def test_runner_closes_abandoned_running_but_preserves_recent_run(db, monkeypatch):
    now = datetime(2026, 1, 1, 12)
    abandoned = Run(status="RUNNING", start_date=now - timedelta(seconds=inventory_runner.RUN_BUDGET + 1))
    recent = Run(status="RUNNING", start_date=now)
    db.session.add_all([abandoned, recent])
    db.session.commit()
    monkeypatch.setattr(exports, "run_exports", Mock())
    monkeypatch.setattr("app.notifications.notify_run", Mock())
    result = inventory_runner.run_inventory(collect_vms=lambda: [], collect_ipam=lambda: [], now=lambda: now)
    db.session.refresh(abandoned)
    db.session.refresh(recent)
    assert (abandoned.status, recent.status, result.status) == ("FAIL", "RUNNING", "SUCCESS")
    assert abandoned.end_date == now
    assert "interrompue" in abandoned.error_message


@pytest.mark.parametrize("fails", [False, True])
def test_smb_requires_encryption_and_closes_session_even_on_error(monkeypatch, tmp_path, fails):
    smb = SimpleNamespace(
        register_session=Mock(), delete_session=Mock(),
        makedirs=Mock(side_effect=OSError("SMB unavailable") if fails else None),
        path=SimpleNamespace(islink=Mock(return_value=False)),
    )
    monkeypatch.setitem(sys.modules, "smbclient", smb)
    if fails:
        with pytest.raises(OSError, match="SMB unavailable"):
            exports._publish_smb(tmp_path, r"\\server\share", {}, {})
    else:
        exports._publish_smb(tmp_path, r"\\server\share", {}, {})
    smb.register_session.assert_called_once_with(
        "server", username=None, password=None, connection_timeout=10, encrypt=True,
    )
    smb.delete_session.assert_called_once_with("server")
