"""Client pour l'API NetBox — récupère les IP addresses avec pagination.

Interface identique à ``collector.mock_netbox.fetch_mock_ipam()`` : aucun
argument obligatoire et mêmes clés de dict (ip, dns_name, status, tenant,
site, meta_zone), pour que la source réelle et la source simulée soient
interchangeables (cahier des charges §36).

Le réseau est isolé dans ``transport`` : signature
``transport(url, headers, timeout, verify_ssl) -> dict`` (corps JSON complet).
Les tests passent un objet de remplacement : aucun appel réseau sortant.
Cible et jeton viennent de l'environnement (NETBOX_URL, NETBOX_TOKEN,
NETBOX_VERIFY_SSL) — aucun secret en dur.
"""

import json
import os
import ssl
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 30  # secondes, par appel HTTP (pagination)
API_PATH = "/api/ipam/ip-addresses/"


class NetBoxClientError(RuntimeError):
    """Échec de collecte NetBox — message explicite, sans jeton ni en-tête."""


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
    with urllib.request.urlopen(
        request, timeout=timeout, context=_ssl_context(verify_ssl)
    ) as response:
        return json.loads(response.read())


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
    return {
        "base_url": os.getenv("NETBOX_URL", "http://127.0.0.1:8000").rstrip("/"),
        "headers": {
            "Authorization": f"{auth_prefix} {token}",
            "Accept": "application/json",
        },
        "verify_ssl": os.getenv("NETBOX_VERIFY_SSL", "true").lower() == "true",
    }


def _get_page(transport, url, settings, timeout=DEFAULT_TIMEOUT):
    """Une page paginée NetBox : dict avec au moins « results » (liste)."""
    try:
        payload = transport(url, settings["headers"], timeout, settings["verify_ssl"])
    except NetBoxClientError:
        raise
    except urllib.error.HTTPError as exc:
        raise NetBoxClientError(
            f"NetBox: HTTP {exc.code} sur {url} — vérifiez NETBOX_URL et le "
            "jeton NETBOX_TOKEN"
        ) from exc
    except urllib.error.URLError as exc:
        raise NetBoxClientError(
            f"NetBox: connexion impossible sur {url} ({exc.reason})"
        ) from exc
    except TimeoutError as exc:
        raise NetBoxClientError(
            f"NetBox: délai dépassé ({timeout} s) sur {url}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise NetBoxClientError(f"NetBox: réponse JSON invalide sur {url}") from exc
    except (ssl.SSLError, ValueError) as exc:
        raise NetBoxClientError(f"NetBox: échec du transport sur {url}") from exc
    if not isinstance(payload, dict):
        raise NetBoxClientError(
            f"NetBox: objet JSON attendu sur {url} — réponse inattendue"
        )
    results = payload.get("results")
    if not isinstance(results, list):
        raise NetBoxClientError(
            f"NetBox: liste « results » absente sur {url} — réponse inattendue"
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
    if transport is None:
        transport = _default_transport
    url = f"{settings['base_url']}{API_PATH}"

    records = []
    while url:
        payload, results = _get_page(transport, url, settings)
        for entry in results:
            if not isinstance(entry, dict):
                raise NetBoxClientError("NetBox: entrée de record invalide")
            records.append(_map_record(entry))
        url = payload.get("next")

    return records
