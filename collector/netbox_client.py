"""Client pour l'API NetBox — récupère les IP addresses avec pagination.

Interface identique à ``collector.mock_netbox.fetch_mock_ipam()`` : aucun
argument obligatoire et mêmes clés de dict (ip, dns_name, status, tenant,
site, meta_zone), pour que la source réelle et la source simulée soient
interchangeables (cahier des charges §36).

Le réseau est isolé dans ``transport`` : signature
``transport(url, headers, timeout, verify_ssl) -> dict`` (corps JSON complet).
Les tests passent un objet de remplacement : aucune connexion réseau sortant.
Cible et jeton viennent de l'environnement (NETBOX_URL, NETBOX_TOKEN,
NETBOX_VERIFY_SSL) — aucun secret en dur.

Périmètre collecte (CODE-04) : budget par run, limites pages/enregistrements,
seuil d'octets par réponse, refus sans repli vers le suivant.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from contextvars import ContextVar
from urllib.parse import urljoin, urlparse

DEFAULT_TIMEOUT = 30  # secondes, par appel HTTP (pagination)
MAX_PAGES = 50  # limite absolue de pages par run
MAX_BYTES_PER_PAGE = 1024 * 1024  # 1 MiB max par réponse
MAX_TOTAL_BYTES = 5 * 1024 * 1024  # 5 MiB total pour tout le run
MAX_RECORDS = 10000
RUN_BUDGET = 300
RUN_DEADLINE = ContextVar("inventory_deadline", default=None)
API_PATH = "/api/ipam/ip-addresses/"


class NetBoxClientError(RuntimeError):
    """Échec de collecte NetBox — message explicite, sans jeton ni en-tête."""


def remaining_timeout(timeout, deadline=None):
    deadline = RUN_DEADLINE.get() if deadline is None else deadline
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Budget de collecte dépassé")
    return min(timeout, remaining)


def verify_tls_setting(name):
    value = (os.getenv(name) or "true").lower()
    if value not in {"true", "false"}:
        raise ValueError("Option de vérification TLS invalide")
    if value == "false" and os.getenv("APP_ENV", "production").lower() not in {
        "development", "local", "test", "testing",
    }:
        raise ValueError("La vérification TLS est obligatoire en production")
    return value == "true"


def https_origin(url):
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment or "\\" in url
            or any(char.isspace() or ord(char) < 32 for char in url)
        ):
            raise ValueError
        return parsed.scheme, parsed.hostname.lower(), parsed.port or 443
    except (ValueError, TypeError) as exc:
        raise ValueError("URL HTTPS invalide ou interdite") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise NetBoxClientError("Redirection HTTP interdite")


class _Page(dict):
    pass


def _ssl_context(verify_ssl):
    """Contexte SSL : vérification pilotée par NETBOX_VERIFY_SSL (défaut true)."""
    context = ssl.create_default_context()
    if not verify_ssl:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _default_transport(url, headers, timeout, verify_ssl):
    """Appel HTTP réel (urllib) — remplacé par un objet de remplacement en test."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=_ssl_context(verify_ssl))
    )
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES_PER_PAGE + 1)
        if len(body) > MAX_BYTES_PER_PAGE:
            raise NetBoxClientError("NetBox: plafond d'octets par page dépassé")
        payload = json.loads(body)
        if isinstance(payload, dict):
            payload = _Page(payload)
            payload.byte_size = len(body)
        return payload


def _settings():
    """Cible, jeton et options SSL lus dans l'environnement (aucun secret en dur)."""
    token = os.getenv("NETBOX_TOKEN", "")
    if not token.strip():
        raise NetBoxClientError(
            "NetBox: NETBOX_TOKEN doit être renseigné pour une collecte réelle "
            "(USE_MOCK_IPAM=false)"
        )
    # NetBox v4+ : tokens nbt_ en Bearer, les anciens en Token
    auth_prefix = "Bearer" if token.startswith("nbt_") else "Token"
    base_url = (os.getenv("NETBOX_URL") or "https://127.0.0.1:8000").rstrip("/")
    try:
        https_origin(base_url)
        verify_ssl = verify_tls_setting("NETBOX_VERIFY_SSL")
    except ValueError as exc:
        raise NetBoxClientError(str(exc)) from exc
    return {
        "base_url": base_url,
        "headers": {
            "Authorization": f"{auth_prefix} {token}",
            "Accept": "application/json",
        },
        "verify_ssl": verify_ssl,
    }


def _get_page(transport, url, settings, timeout=DEFAULT_TIMEOUT):
    """Une page paginée NetBox : dict avec au moins « results » (liste)."""
    try:
        payload = transport(url, settings["headers"], timeout, settings["verify_ssl"])
    except NetBoxClientError:
        raise
    except urllib.error.HTTPError as exc:
        raise NetBoxClientError(
            f"NetBox: HTTP {exc.code} (NETBOX_URL)"
        ) from exc
    except urllib.error.URLError as exc:
        raise NetBoxClientError(
            "NetBox: connexion impossible"
        ) from exc
    except TimeoutError as exc:
        raise NetBoxClientError(
            f"NetBox: délai dépassé ({timeout} s) (NETBOX_URL)"
        ) from exc
    except json.JSONDecodeError as exc:
        raise NetBoxClientError("NetBox: réponse JSON invalide") from exc
    except (ssl.SSLError, ValueError) as exc:
        raise NetBoxClientError("NetBox: échec du transport") from exc
    if not isinstance(payload, dict):
        raise NetBoxClientError(
            "NetBox: objet JSON attendu — réponse inattendue"
        )
    results = payload.get("results")
    if not isinstance(results, list):
        raise NetBoxClientError(
            "NetBox: liste « results » absente — réponse inattendue"
        )
    return payload, results


def _map_record(entry):
    """Normalise une entrée NetBox vers le format commun mock/réel."""
    address = entry.get("address", "")
    ip = address.split("/")[0] if address else ""

    dns_name = (entry.get("dns_name") or "").strip()
    status_obj = entry.get("status")
    status = status_obj.get("value", "") if isinstance(status_obj, dict) else str(status_obj or "")

    tenant_obj = entry.get("tenant")
    tenant = tenant_obj.get("name", "") if isinstance(tenant_obj, dict) and tenant_obj else None

    site_obj = entry.get("site")
    site = site_obj.get("name", "") if isinstance(site_obj, dict) and site_obj else None

    custom_fields = entry.get("custom_fields")
    meta_zone = custom_fields.get("meta_zone") if isinstance(custom_fields, dict) else None

    return {
        "ip": ip,
        "dns_name": dns_name,
        "status": status,
        "tenant": tenant,
        "site": site,
        "meta_zone": meta_zone,
    }


def fetch_ipam_records(transport=None):
    """Récupère toutes les IP addresses depuis NetBox (avec pagination).

    Args:
        transport: callable(url, headers, timeout, verify_ssl) -> dict ; par
            défaut l'appel HTTP urllib. Les tests fournissent un objet de
            remplacement : aucune connexion réseau n'est ouverte.

    Returns:
        list[dict]: records ip, dns_name, status, tenant, site, meta_zone —
            mêmes clés que le mock.
    """
    settings = _settings()
    deadline = RUN_DEADLINE.get()
    if deadline is None:
        deadline = time.monotonic() + RUN_BUDGET
    if transport is None:
        transport = _default_transport
    url = f"{settings['base_url']}{API_PATH}"

    records = []
    origin = https_origin(settings["base_url"])
    total_bytes = 0
    page_count = 0
    seen = set()
    while url:
        if page_count >= MAX_PAGES or url in seen:
            raise NetBoxClientError("NetBox: boucle ou plafond de pages atteint")
        try:
            if https_origin(url) != origin:
                raise ValueError("NetBox: origine de pagination interdite")
        except ValueError as exc:
            raise NetBoxClientError(str(exc)) from exc
        seen.add(url)
        page_count += 1
        payload, results = _get_page(
            transport, url, settings, remaining_timeout(DEFAULT_TIMEOUT, deadline)
        )
        remaining_timeout(DEFAULT_TIMEOUT, deadline)
        page_bytes = (
            payload.byte_size if isinstance(payload, _Page)
            else len(json.dumps(payload).encode("utf-8"))
        )
        total_bytes += page_bytes
        if page_bytes > MAX_BYTES_PER_PAGE or total_bytes > MAX_TOTAL_BYTES:
            raise NetBoxClientError("NetBox: dépassement du budget d'octets")
        if len(records) + len(results) > MAX_RECORDS:
            raise NetBoxClientError("NetBox: plafond d'enregistrements atteint")
        for entry in results:
            if not isinstance(entry, dict):
                raise NetBoxClientError("NetBox: entrée de record invalide")
            records.append(_map_record(entry))
        # Suivre le lien 'next' en le résolvant relativement à l'URL courante
        next_raw = payload.get("next")
        if next_raw is not None and not isinstance(next_raw, str):
            raise NetBoxClientError("NetBox: lien de pagination invalide")
        if next_raw:
            try:
                if any(char.isspace() or ord(char) < 32 for char in next_raw):
                    raise ValueError
                url = urljoin(url, next_raw)
            except ValueError as exc:
                raise NetBoxClientError("NetBox: lien de pagination invalide") from exc
        else:
            url = None

    remaining_timeout(DEFAULT_TIMEOUT, deadline)
    return records
