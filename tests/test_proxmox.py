import json
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from collector import proxmox
from collector.source import AssetRecord


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setenv("PROXMOX_URL", "https://pve.example/")
    monkeypatch.setenv("PROXMOX_TOKEN_ID", "test-id")
    monkeypatch.setenv("PROXMOX_TOKEN_SECRET", "test-placeholder")


def test_collects_qemu_and_lxc_as_fresh_source_records():
    calls = []
    def transport(request, timeout):
        calls.append((request.full_url, timeout, request.get_header("Authorization")))
        path = request.full_url
        if "cluster/resources" in path:
            data = [{"node": "pve1", "type": kind, "vmid": vmid} for kind, vmid in (("qemu", 101), ("lxc", 102))]
        elif path.endswith("/config"):
            data = {"name": "guest.example" if "/qemu/" in path else "container", "ipconfig0": "ip=192.0.2.1/24", "tags": "prod", "ostype": "linux", "description": "sample"}
        else:
            data = {"status": "running", "cpu": 0.25, "mem": 1048576, "maxdisk": 1073741824, "uptime": 42}
        return json.dumps({"data": data}).encode()
    source = proxmox.ProxmoxSource(transport=transport, timeout=3)
    first = source.collect()
    assert [{key: row[key] for key in ("vm_id", "type", "vm_name", "fqdn", "ip_reported", "ram", "disk", "role")} for row in first] == [
        {"vm_id": "101", "type": "qemu", "vm_name": "guest.example", "fqdn": "guest.example", "ip_reported": "192.0.2.1", "ram": 1.0, "disk": 1.0, "role": None},
        {"vm_id": "102", "type": "lxc", "vm_name": "container", "fqdn": "", "ip_reported": "192.0.2.1", "ram": 1.0, "disk": 1.0, "role": None},
    ]
    assert all(set(row) == set(AssetRecord.__annotations__) for row in first)
    assert all(timeout == 3 and auth == "PVEAPIToken=test-id=test-placeholder" for _, timeout, auth in calls)
    first[0]["vm_name"] = "changed"
    assert source.collect()[0]["vm_name"] == "guest.example"


@pytest.mark.parametrize("failure", [HTTPError("https://pve.example", 401, "test-placeholder", {}, None), TimeoutError("test-placeholder"), ssl.SSLError("test-placeholder")])
def test_transport_failures_do_not_expose_secret(failure):
    def transport(request, timeout):
        raise failure
    with pytest.raises(RuntimeError) as error:
        proxmox.ProxmoxSource(transport=transport).collect()
    assert "test-placeholder" not in str(error.value)


@pytest.mark.parametrize("url", ["http://pve.example", "https://other.example@pve.example", "https://pve.example/?token=x"])
def test_rejects_untrusted_source_url_before_transport(monkeypatch, url):
    monkeypatch.setenv("PROXMOX_URL", url)
    with pytest.raises(ValueError):
        proxmox.ProxmoxSource(transport=lambda *args: pytest.fail("transport invoked")).collect()


def test_default_transport_uses_verified_tls_timeout_and_rejects_redirects(monkeypatch):
    captured = {}
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b"{}"
    class Opener:
        def open(self, request, timeout):
            captured["timeout"] = timeout
            return Response()
    def build(*handlers):
        captured["handlers"] = handlers
        return Opener()
    monkeypatch.setattr(proxmox, "build_opener", build)
    proxmox._transport(proxmox.Request("https://pve.example/"), 4)
    https, redirect = captured["handlers"]
    assert https._context.verify_mode == ssl.CERT_REQUIRED
    assert https._context.check_hostname
    assert redirect.redirect_request(None, None, 302, "redirect", {}, "https://outside.example") is None
    assert captured["timeout"] == 4


def test_import_construction_and_app_start_do_not_connect(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "local-test-value")
    monkeypatch.setenv("JWT_SECRET_KEY", "local-test-value")
    monkeypatch.setenv("ADMIN_PASSWORD", "local-test-value")
    from app import create_app
    client = create_app().test_client()
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf_token"]
    login = client.post("/login", data={"username": "admin", "password": "local-test-value", "csrf_token": token})
    assert login.status_code == 302
    assert proxmox.ProxmoxSource() and client.get("/health").status_code == 200
