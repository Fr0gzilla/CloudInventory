import base64
import json
import socket
import ssl
from urllib.error import HTTPError

import pytest

from app import db
from app.models import IpamRecord
from collector import proxmox
from collector.efficientip import EfficientIpSource
from collector.inventory_runner import run_inventory
from collector.mock_efficientip import MockEfficientIpSource
from collector.mock_ipam import MockIpamSource
from collector.source import IpamRecord as IpamContract


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("network"))
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: pytest.fail("network"))
    monkeypatch.setenv("EFFICIENTIP_URL", "https://ipam.example/")
    monkeypatch.setenv("EFFICIENTIP_USERNAME", "tester")
    monkeypatch.setenv("EFFICIENTIP_PASSWORD", "test-placeholder")


def test_mock_and_real_have_identical_fields_and_independent_observations():
    source = MockEfficientIpSource()
    expected = source.collect()
    raw = [row["custom_fields"] for row in expected]
    real = EfficientIpSource(transport=lambda request, timeout: json.dumps(raw).encode()).collect()
    assert real == expected
    assert set(real[0]) == set(IpamContract.__annotations__) | {"custom_fields", "ipam_source"}
    expected[0]["ip"] = "changed"
    assert source.collect()[0]["ip"] == "192.0.2.50"


def test_solidserver_fields_and_original_attributes_are_preserved():
    raw = {"ip_addr": "192.0.2.18", "ip_name": "guest.example", "ip_status": "active",
           "tenant": "team", "site": "paris", "tags": "lab", "meta_zone": "zone",
           "site_id": "42", "extra": {"label": "été"}}
    record = EfficientIpSource(transport=lambda request, timeout: json.dumps([raw]).encode()).collect()[0]
    assert record == {"ip": "192.0.2.18", "dns_name": "guest.example", "status": "active",
                      "tenant": "team", "site": "paris", "tags": "lab", "meta_zone": "zone",
                      "duplicate_dns": 0, "duplicate_ip": 0, "ipam_source": "efficientip",
                      "custom_fields": raw}


def test_duplicate_dns_and_ip_are_marked_on_each_observation():
    payload = [{"ip_addr": "192.0.2.18", "ip_name": "GUEST.example"},
               {"ip_addr": "192.0.2.18", "ip_name": "guest.EXAMPLE"}]
    rows = EfficientIpSource(transport=lambda request, timeout: json.dumps(payload).encode()).collect()
    assert [(row["duplicate_dns"], row["duplicate_ip"]) for row in rows] == [(1, 1), (1, 1)]


def test_get_uses_configured_basic_auth_and_timeout():
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        return b"[]"

    assert EfficientIpSource(transport=transport, timeout=4).collect() == []
    request, timeout = calls.pop()
    assert (request.get_method(), request.full_url, timeout, request.get_header("Authorization")) == (
        "GET", "https://ipam.example/rest/ip_address_list", 4,
        "Basic " + base64.b64encode(b"tester:test-placeholder").decode())
    assert calls == []


@pytest.mark.parametrize("timeout", [0, -1, 31])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError, match="Invalid source timeout"):
        EfficientIpSource(timeout=timeout)


@pytest.mark.parametrize("url", ["", "http://ipam.example", "https://user@ipam.example",
                                      "https://ipam.example/?token=x", "https://ipam.example/#fragment"])
def test_invalid_url_never_sends_credentials(monkeypatch, url):
    monkeypatch.setenv("EFFICIENTIP_URL", url)
    with pytest.raises(ValueError):
        EfficientIpSource(transport=lambda *args: pytest.fail("transport invoked")).collect()


@pytest.mark.parametrize("key,value", [("EFFICIENTIP_USERNAME", ""), ("EFFICIENTIP_PASSWORD", ""),
                                        ("EFFICIENTIP_USERNAME", "bad\nname"),
                                        ("EFFICIENTIP_PASSWORD", "bad\rpassword")])
def test_invalid_credentials_never_reach_transport(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="EfficientIP credentials missing or invalid"):
        EfficientIpSource(transport=lambda *args: pytest.fail("transport invoked")).collect()


@pytest.mark.parametrize("failure", [HTTPError("https://ipam.example", 403, "test-placeholder", {}, None),
                                           HTTPError("https://ipam.example", 302, "test-placeholder", {}, None),
                                           TimeoutError("test-placeholder"), ssl.SSLError("test-placeholder")])
def test_http_redirect_and_transport_failures_hide_credentials(failure):
    def transport(request, timeout):
        raise failure

    with pytest.raises(RuntimeError) as error:
        EfficientIpSource(transport=transport).collect()
    assert "test-placeholder" not in str(error.value)


@pytest.mark.parametrize("payload", [b"{}", b"[null]", b"not-json", b'[{"ip_addr": 3}]'])
def test_invalid_api_response_is_rejected(payload):
    with pytest.raises((TypeError, RuntimeError)):
        EfficientIpSource(transport=lambda request, timeout: payload).collect()


def test_default_transport_verifies_https_and_blocks_redirect(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"[]"

    class Opener:
        def open(self, request, timeout):
            captured["timeout"] = timeout
            return Response()

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(proxmox, "build_opener", build)
    assert EfficientIpSource().collect() == []
    https, redirect = captured["handlers"]
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.check_hostname
    assert redirect.redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.example") is None
    assert captured["timeout"] == 10


def test_default_ipam_source_preserves_historical_netbox_mock(orm_app, monkeypatch):
    monkeypatch.delenv("IPAM_SOURCE", raising=False)
    monkeypatch.delenv("IPAM_MODE", raising=False)
    run = run_inventory()
    assert run.status == "SUCCESS"
    assert {row.ip for row in db.session.query(IpamRecord).filter_by(run_id=run.id)} == {
        row["ip"] for row in MockIpamSource().collect()}


def test_configuration_selects_efficientip_mock_and_persists_unmatched_observation(orm_app, monkeypatch):
    monkeypatch.setenv("IPAM_SOURCE", "efficientip")
    monkeypatch.setenv("IPAM_MODE", "mock")
    run = run_inventory()
    assert run.status == "SUCCESS", run.error_message
    assert {(row.ipam_source, row.ip, row.dns_name) for row in db.session.query(IpamRecord).filter_by(run_id=run.id)} == {
        ("efficientip", "192.0.2.50", "offline.example.org"),
        ("efficientip", "192.0.2.51", "orphan.example.org")}


def test_configuration_selects_real_efficientip_with_offline_transport(orm_app, monkeypatch):
    def transport(request, timeout):
        return b'[{"ip_addr":"192.0.2.61","ip_name":"remote.example","site_id":"42"}]'

    monkeypatch.setattr(EfficientIpSource, "__init__", lambda self: setattr(self, "_transport", transport) or setattr(self, "_timeout", 10))
    monkeypatch.setenv("IPAM_SOURCE", "efficientip")
    monkeypatch.setenv("IPAM_MODE", "real")
    run = run_inventory()
    assert run.status == "SUCCESS", run.error_message
    row = db.session.query(IpamRecord).filter_by(run_id=run.id, ip="192.0.2.61").one()
    assert (row.ipam_source, json.loads(row.custom_fields)["site_id"]) == ("efficientip", "42")


@pytest.mark.parametrize("key,value", [("IPAM_SOURCE", "unknown"), ("IPAM_MODE", "unknown")])
def test_invalid_ipam_selection_is_rejected(orm_app, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="Invalid IPAM source configuration"):
        run_inventory()


def test_identical_ip_and_dns_from_distinct_origins_are_not_merged(orm_app):
    class Source:
        def __init__(self, records):
            self.records = records

        def collect(self):
            return self.records

    observations = [{"ip": "192.0.2.90", "dns_name": "same.example", "ipam_source": origin}
                    for origin in ("netbox", "efficientip")]
    run = run_inventory(virtualization_source=Source([]), ipam_source=Source(observations))
    assert run.status == "SUCCESS", run.error_message
    assert {(row.ipam_source, row.ip, row.dns_name) for row in db.session.query(IpamRecord).filter_by(run_id=run.id)} == {
        ("netbox", "192.0.2.90", "same.example"),
        ("efficientip", "192.0.2.90", "same.example")}
