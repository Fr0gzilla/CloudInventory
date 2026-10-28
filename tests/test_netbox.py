import json
import socket
import ssl
from urllib.error import HTTPError

import pytest

from collector.netbox import NetBoxSource
from collector.source import IpamRecord


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example/")
    monkeypatch.setenv("NETBOX_TOKEN", "test-placeholder")


def test_paginates_maps_duplicates_and_returns_fresh_records():
    urls = []
    def transport(request, timeout):
        urls.append((request.full_url, request.get_header("Authorization"), timeout))
        row = {"address": "192.0.2.10/24", "dns_name": "host.example", "status": {"value": "active"}, "tenant": {"name": "team"}, "site": {"name": "paris"}, "tags": [{"name": "prod"}], "custom_fields": {"meta_zone": "dmz"}}
        page = {"results": [row], "next": "?page=2"} if "page=2" not in request.full_url else {"results": [dict(row, dns_name="HOST.EXAMPLE")], "next": None}
        return json.dumps(page).encode()
    source = NetBoxSource(transport=transport, timeout=5)
    rows = source.collect()
    assert len(rows) == 2
    assert rows[0] == {"ip": "192.0.2.10", "dns_name": "host.example", "status": "active", "tenant": "team", "site": "paris", "tags": "prod", "duplicate_dns": 1, "duplicate_ip": 1, "meta_zone": "dmz", "custom_fields": {"meta_zone": "dmz"}}
    assert all(set(row) == set(IpamRecord.__annotations__) | {"custom_fields"} for row in rows)
    assert len(urls) == 2 and all(auth == "Token test-placeholder" and timeout == 5 for _, auth, timeout in urls)
    rows[0]["ip"] = "changed"
    assert source.collect()[0]["ip"] == "192.0.2.10"


def test_netbox_preserves_nested_unicode_custom_fields_and_missing_fields():
    custom = {"meta_zone": "Étage", "nested": {"flags": [True, None, 2, 3.5], "label": "été"}}
    payload = [{"address": "192.0.2.1/24", "custom_fields": custom},
               {"address": "192.0.2.2/24", "custom_fields": None},
               {"address": "192.0.2.3/24", "custom_fields": {}},
               {"address": "192.0.2.4/24"}]
    source = NetBoxSource(transport=lambda request, timeout: json.dumps(
        {"results": payload, "next": None}).encode())
    rows = source.collect()
    assert [row["custom_fields"] for row in rows] == [custom, None, {}, None]
    rows[0]["custom_fields"]["nested"]["flags"].append("modified")
    assert source.collect()[0]["custom_fields"] == custom


@pytest.mark.parametrize("failure", [HTTPError("https://netbox.example", 403, "test-placeholder", {}, None), TimeoutError("test-placeholder"), ssl.SSLError("test-placeholder")])
def test_transport_failures_hide_token(failure):
    def transport(request, timeout):
        raise failure
    with pytest.raises(RuntimeError) as error:
        NetBoxSource(transport=transport).collect()
    assert "test-placeholder" not in str(error.value)


@pytest.mark.parametrize("next_url", ["https://evil.example/api/ipam/ip-addresses/", "//evil.example/steal", "/other", "?limit=100"])
def test_untrusted_or_cyclic_pagination_never_sends_token(next_url):
    urls = []
    def transport(request, timeout):
        urls.append(request.full_url)
        return json.dumps({"results": [], "next": next_url}).encode()
    with pytest.raises((ValueError, RuntimeError)):
        NetBoxSource(transport=transport).collect()
    assert len(urls) == 1 and urls[0].startswith("https://netbox.example/api/")


@pytest.mark.parametrize("url", ["http://netbox.example", "https://user@netbox.example"])
def test_rejects_insecure_url_before_sending_token(monkeypatch, url):
    monkeypatch.setenv("NETBOX_URL", url)
    with pytest.raises(ValueError):
        NetBoxSource(transport=lambda *args: pytest.fail("transport invoked")).collect()
