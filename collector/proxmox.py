"""Read-only Proxmox observations; never contacted until collect is called."""

import json
import os
import re
import ssl
from collections.abc import Callable
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from collector.source import AssetRecord

Transport = Callable[[Request, float], bytes]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _transport(request: Request, timeout: float) -> bytes:
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context()), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("HTTPS source URL required")
    return value.rstrip("/") + "/"


def _read_json(url: str, base: str, authorization: str, transport: Transport, timeout: float,
               *, method: str = "GET", auth_header: str = "Authorization"):
    target = urlsplit(url)
    origin = urlsplit(base)
    if (target.scheme != "https" or target.netloc != origin.netloc
            or not target.path.startswith(origin.path) or target.fragment
            or target.username or target.password):
        raise ValueError("Source URL outside configured HTTPS API")
    request = Request(url, headers={auth_header: authorization, "Accept": "application/json"},
                      method=method)
    try:
        payload = transport(request, timeout)
        return json.loads(payload)
    except (OSError, ValueError, TypeError):
        raise RuntimeError("Source API request failed") from None


def _ip(value: object) -> str:
    if not isinstance(value, str):
        return ""
    match = re.search(r"(?:^|,)ip=([^,/]+)", value)
    if match and match.group(1).lower() not in ("dhcp", "auto", "manual"):
        return match.group(1)
    return ""


class ProxmoxSource:
    """Use PROXMOX_URL, PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET."""

    def __init__(self, transport: Transport | None = None, timeout: float = 10.0):
        if not 0 < timeout <= 30:
            raise ValueError("Invalid source timeout")
        self._transport = transport or _transport
        self._timeout = timeout

    def collect(self) -> list[AssetRecord]:
        base = _base_url(os.environ.get("PROXMOX_URL", ""))
        token_id = os.environ.get("PROXMOX_TOKEN_ID", "")
        secret = os.environ.get("PROXMOX_TOKEN_SECRET", "")
        if not token_id or not secret or "\n" in token_id or "\n" in secret:
            raise ValueError("Proxmox token configuration missing or invalid")
        auth = f"PVEAPIToken={token_id}={secret}"
        api = urljoin(base, "api2/json/")

        def fetch(path: str):
            result = _read_json(urljoin(api, path), api, auth, self._transport, self._timeout)
            if not isinstance(result, dict) or not isinstance(result.get("data"), (dict, list)):
                raise TypeError("Invalid Proxmox API response")
            return result["data"]

        resources = fetch("cluster/resources?type=vm")
        if not isinstance(resources, list):
            raise TypeError("Invalid Proxmox resource list")
        assets: list[AssetRecord] = []
        for item in resources:
            if not isinstance(item, dict) or item.get("type") not in ("qemu", "lxc"):
                continue
            node, kind, vmid = (str(item.get(key, "")) for key in ("node", "type", "vmid"))
            if not node or not vmid or not vmid.isdigit():
                raise RuntimeError("Invalid Proxmox resource identifier")
            path = f"nodes/{quote(node, safe='')}/{kind}/{vmid}/"
            config = fetch(path + "config")
            current = fetch(path + "status/current")
            if not isinstance(config, dict) or not isinstance(current, dict):
                raise TypeError("Invalid Proxmox VM response")
            name = str(config.get("name") or item.get("name") or "")
            ip = _ip(config.get("ipconfig0") or config.get("net0"))
            assets.append({
                "vm_id": vmid, "vm_name": name,
                "fqdn": name if "." in name else "", "ip_reported": ip,
                "node": node, "type": kind, "status": str(current.get("status") or item.get("status") or ""),
                "tags": str(config.get("tags") or item.get("tags") or ""), "role": None,
                "os": str(config.get("ostype") or ""),
                "annotation": str(config.get("description") or ""),
                "cpu": float(current.get("cpu") or 0),
                "ram": float(current.get("mem") or 0) / (1024 ** 2),
                "disk": float(current.get("maxdisk") or 0) / (1024 ** 3),
                "uptime": float(current.get("uptime") or 0),
            })
        return assets
