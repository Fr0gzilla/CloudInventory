import base64
import json
import socket
import ssl
from urllib.error import HTTPError

import pytest

from app import db
from app.models import Anomaly, Asset
from collector.inventory_runner import run_inventory
from collector.mock_vsphere import MockVsphereSource
from collector.source import AssetRecord
from collector.vsphere import VsphereSource
from collector import proxmox


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("network"))
    monkeypatch.setenv("VSPHERE_URL", "https://vc.example/")
    monkeypatch.setenv("VSPHERE_USERNAME", "test-user")
    monkeypatch.setenv("VSPHERE_PASSWORD", "test-placeholder")


def transport_for(items):
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        return json.dumps("session-placeholder" if request.get_method() == "POST" else items).encode()

    return transport, calls


def test_mock_is_deterministic_and_matches_real_fields():
    mock = MockVsphereSource()
    expected = mock.collect()
    transport, _ = transport_for([{"vm": "100", "name": "vsphere-web.example.org", "power_state": "POWERED_ON", "cpu_count": 2, "memory_size_MiB": 4096}])
    assert VsphereSource(transport=transport).collect() == expected
    assert set(expected[0]) == set(AssetRecord.__annotations__)
    expected[0]["vm_name"] = "changed"
    assert mock.collect()[0]["vm_name"] == "vsphere-web.example.org"


def test_real_conversion_keeps_missing_attributes_null():
    transport, _ = transport_for([{"vm": "vm-7", "name": "guest", "power_state": "POWERED_OFF"}])
    record = VsphereSource(transport=transport).collect()[0]
    assert record == {"vm_id": "vm-7", "vm_name": "guest", "fqdn": None, "ip_reported": None,
                      "node": None, "type": "qemu", "status": "stopped", "tags": None,
                      "role": None, "os": None, "annotation": None, "cpu": None,
                      "ram": None, "disk": None, "uptime": None}


def test_session_authentication_and_reused_transport():
    transport, calls = transport_for([])
    assert VsphereSource(transport=transport, timeout=4).collect() == []
    session, inventory = (entry[0] for entry in calls)
    assert [(r.get_method(), r.full_url, t) for r, t in calls] == [
        ("POST", "https://vc.example/api/session", 4),
        ("GET", "https://vc.example/api/vcenter/vm", 4)]
    assert session.get_header("Authorization") == "Basic " + base64.b64encode(b"test-user:test-placeholder").decode()
    assert inventory.get_header("Vmware-api-session-id") == "session-placeholder"
    assert inventory.get_header("Authorization") is None


@pytest.mark.parametrize("url", ["http://vc.example", "https://user@vc.example", "https://vc.example/?key=x", ""])
def test_invalid_url_never_reaches_transport(monkeypatch, url):
    monkeypatch.setenv("VSPHERE_URL", url)
    with pytest.raises(ValueError):
        VsphereSource(transport=lambda *args: pytest.fail("transport invoked")).collect()


@pytest.mark.parametrize("key,value", [("VSPHERE_USERNAME", ""), ("VSPHERE_PASSWORD", ""), ("VSPHERE_USERNAME", "bad\nname")])
def test_missing_or_invalid_credentials_never_reach_transport(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="vSphere credentials missing or invalid"):
        VsphereSource(transport=lambda *args: pytest.fail("transport invoked")).collect()


@pytest.mark.parametrize("payload,expected", [(b"{}", "Invalid vSphere session response"), (b'"session"', "Invalid vSphere VM list")])
def test_malformed_responses_are_rejected(payload, expected):
    with pytest.raises(RuntimeError, match=expected):
        VsphereSource(transport=lambda request, timeout: payload).collect()


@pytest.mark.parametrize("item", [{}, {"vm": 7}, {"vm": "7", "cpu_count": True}, {"vm": "7", "power_state": "UNKNOWN"}])
def test_invalid_vm_fields_are_rejected(item):
    transport, _ = transport_for([item])
    with pytest.raises(RuntimeError, match="Invalid vSphere VM"):
        VsphereSource(transport=transport).collect()


def test_transport_errors_hide_credentials():
    def fail(request, timeout):
        raise HTTPError(request.full_url, 302, "test-placeholder", {}, None)

    with pytest.raises(RuntimeError) as error:
        VsphereSource(transport=fail).collect()
    assert "test-placeholder" not in str(error.value)


def test_default_transport_verifies_tls_and_rejects_redirect(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'"session"'

    class Opener:
        def open(self, request, timeout):
            captured["timeout"] = timeout
            return Response()

    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(proxmox, "build_opener", build)
    monkeypatch.setattr(proxmox, "ssl", ssl)
    VsphereSource()._transport(proxmox.Request("https://vc.example/api/session"), 5)
    https, redirect = captured["handlers"]
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.check_hostname
    assert redirect.redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.example") is None
    assert captured["timeout"] == 5


def test_environment_selects_vsphere_mock_and_persists_provenance(orm_app, monkeypatch):
    monkeypatch.setenv("VIRTUALIZATION_SOURCE", "vsphere")
    monkeypatch.setenv("VIRTUALIZATION_MODE", "mock")
    run = run_inventory()
    assert run.status == "SUCCESS"
    assert [(a.virtual_source, a.vm_id, a.node) for a in db.session.query(Asset).filter_by(run_id=run.id)] == [("vsphere", "100", None)]


def test_environment_selects_real_vsphere_without_network(orm_app, monkeypatch):
    transport, calls = transport_for([{"vm": "remote-7", "name": "remote.example", "power_state": "POWERED_ON"}])
    monkeypatch.setattr(VsphereSource, "__init__", lambda self: setattr(self, "_transport", transport) or setattr(self, "_timeout", 10))
    monkeypatch.setenv("VIRTUALIZATION_SOURCE", "vsphere")
    monkeypatch.setenv("VIRTUALIZATION_MODE", "real")
    run = run_inventory()
    assert run.status == "SUCCESS"
    assert [(a.virtual_source, a.vm_id) for a in db.session.query(Asset).filter_by(run_id=run.id)] == [("vsphere", "remote-7")]
    assert len(calls) == 2


def test_explicit_source_takes_priority_over_invalid_environment(orm_app, monkeypatch):
    monkeypatch.setenv("VIRTUALIZATION_SOURCE", "unknown")
    run = run_inventory(virtualization_source=MockVsphereSource())
    assert [(a.virtual_source, a.vm_id) for a in db.session.query(Asset).filter_by(run_id=run.id)] == [("vsphere", "100")]


def test_invalid_source_configuration_is_rejected(orm_app, monkeypatch):
    monkeypatch.setenv("VIRTUALIZATION_MODE", "unsupported")
    with pytest.raises(ValueError, match="Invalid virtualization source configuration"):
        run_inventory()


def test_same_vm_id_across_sources_remains_distinct_without_false_rename(orm_app):
    class Source:
        def __init__(self, records):
            self.records = records

        def collect(self):
            return self.records

    old = run_inventory(Source([{"vm_id": "shared", "vm_name": "old", "virtual_source": "proxmox", "type": "qemu"}]), Source([]))
    assert old.status == "SUCCESS"
    db.session.rollback()
    new = run_inventory(Source([{"vm_id": "shared", "vm_name": "other", "virtual_source": "vsphere", "type": "qemu"},
                                {"vm_id": "shared", "vm_name": "old", "virtual_source": "proxmox", "type": "qemu"}]), Source([]))
    assert new.status == "SUCCESS", new.error_message
    assert {(a.virtual_source, a.vm_id, a.vm_name) for a in db.session.query(Asset).filter_by(run_id=new.id)} == {
        ("proxmox", "shared", "old"), ("vsphere", "shared", "other")}
    assert not db.session.query(Anomaly).filter_by(run_id=new.id, code="RENAMED").all()


def test_same_vm_id_across_sources_does_not_inherit_anomaly_state(orm_app):
    class Source:
        def __init__(self, source):
            self.virtual_source = source

        def collect(self):
            return [{"vm_id": "shared", "vm_name": "unmatched", "type": "qemu"}]

    class Empty:
        def collect(self):
            return []

    first = run_inventory(Source("proxmox"), Empty())
    original = db.session.query(Anomaly).filter_by(run_id=first.id, code="NO_MATCH").one()
    original.state = "acquittée"
    original.state_changed_at = original.created_at
    db.session.commit()
    second = run_inventory(Source("vsphere"), Empty())
    current = db.session.query(Anomaly).filter_by(run_id=second.id, code="NO_MATCH").one()
    assert current.state == "ouverte"
