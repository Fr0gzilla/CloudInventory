"""T034 — Appel HTTP réel sur l'URL NetBox configurée (NETBOX_URL).

Le transport par défaut de ``collector/netbox_client`` ouvre une vraie connexion
vers l'URL lue dans l'environnement : un serveur HTTPS local (certificat
auto-signé fabriqué dans ``tmp_path`` par openssl) sert l'API et trace les
requêtes reçues. Aucune connexion sortante ; tous les artefacts restent dans
``tmp_path``.
"""
import json
import shutil
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collector import NetBoxClientError, fetch_ipam_records
from collector.netbox_client import API_PATH

# Jeton factice — jamais un secret réel (C7).
_TOKEN = "unit-test-netbox-token"

# Réponse servie par le serveur local.
_PAGE = {
    "count": 1, "next": None, "previous": None,
    "results": [{
        "address": "10.0.7.10/32",
        "dns_name": "web-http",
        "status": {"value": "active"},
        "tenant": {"name": "Production"},
        "site": {"name": "DC1"},
        "custom_fields": {"meta_zone": "ZM"},
    }],
}


class _Handler(BaseHTTPRequestHandler):
    """Serveur d'API : trace les requêtes, répond selon la spécification du test."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # nom imposé par BaseHTTPRequestHandler
        spec = self.server.spec
        spec["requests"].append({
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
        })
        body = json.dumps(spec["payload"]).encode("utf-8")
        self.send_response(spec["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """Silencieux : la sortie du serveur ne pollue pas la suite."""


@pytest.fixture()
def netbox_https(tmp_path, monkeypatch):
    """Serveur HTTPS local sur 127.0.0.1 et NETBOX_URL pointée dessus."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl absent : certificat de test non fabriquable")

    key = tmp_path / "key.pem"
    cert = tmp_path / "cert.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=127.0.0.1"],
        check=True, capture_output=True,
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.spec = {"status": 200, "payload": _PAGE, "requests": []}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    monkeypatch.delenv("USE_MOCK_IPAM", raising=False)
    monkeypatch.setenv("NETBOX_URL", f"https://127.0.0.1:{port}/")
    monkeypatch.setenv("NETBOX_TOKEN", _TOKEN)
    monkeypatch.setenv("NETBOX_VERIFY_SSL", "false")
    # Certificat auto-signé : vérification TLS désactivée uniquement hors production.
    monkeypatch.setenv("APP_ENV", "test")
    # Loopback uniquement : la cible de test ne passe jamais par un proxy.
    for name in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    try:
        yield server.spec
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_fetch_ipam_records_reel_sur_url_configuree(netbox_https):
    """T034 : la collecte réelle contacte bien NETBOX_URL et rend le format commun des records."""
    records = fetch_ipam_records()

    assert [record["ip"] for record in records] == ["10.0.7.10"]
    assert records[0]["dns_name"] == "web-http"
    assert records[0]["meta_zone"] == "ZM"
    assert len(netbox_https["requests"]) == 1
    request = netbox_https["requests"][0]
    assert request["path"] == API_PATH
    assert request["authorization"] == f"Token {_TOKEN}"


def test_erreur_http_reelle_est_signallee_avec_le_statut(netbox_https):
    """T034 : une réponse d'erreur du serveur configuré devient une NetBoxClientError explicite."""
    netbox_https["status"] = 403

    with pytest.raises(NetBoxClientError) as excinfo:
        fetch_ipam_records()

    assert "HTTP 403" in str(excinfo.value)
    assert _TOKEN not in str(excinfo.value)
