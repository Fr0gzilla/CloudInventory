"""Client pour l'API Proxmox VE — récupère les VM/CT avec métriques.

Interface identique à ``collector.mock_virtualisation.fetch_mock_vms()`` :
aucun argument obligatoire et mêmes clés de dict (os, fqdn et annotation
compris), pour que la source réelle et la source simulée soient
interchangeables (cahier des charges §36).

Le réseau est isolé dans ``transport`` : signature
``transport(url, headers, timeout, verify_ssl) -> dict`` (corps JSON complet).
Les tests passent un objet de remplacement : aucun appel réseau sortant.
Cible et jetons viennent de l'environnement (PROXMOX_URL, PROXMOX_TOKEN_ID,
PROXMOX_TOKEN_SECRET, PROXMOX_VERIFY_SSL) — aucun secret en dur.
"""

import json
import os
import ssl
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 15  # secondes, par appel HTTP


class ProxmoxClientError(RuntimeError):
    """Échec de collecte Proxmox — message explicite, sans jeton ni en-tête."""


def _ssl_context(verify_ssl):
    """Contexte SSL : vérification pilotée par PROXMOX_VERIFY_SSL (défaut false)."""
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
    token_id = os.getenv("PROXMOX_TOKEN_ID", "")
    token_secret = os.getenv("PROXMOX_TOKEN_SECRET", "")
    if not token_id.strip() or not token_secret.strip():
        raise ProxmoxClientError(
            "Proxmox: PROXMOX_TOKEN_ID et PROXMOX_TOKEN_SECRET doivent être "
            "renseignés pour une collecte réelle (USE_MOCK_VIRT=false)"
        )
    return {
        "base_url": os.getenv("PROXMOX_URL", "https://pve.local:8006").rstrip("/"),
        "headers": {
            "Authorization": f"PVEAPIToken={token_id}={token_secret}",
            "Accept": "application/json",
        },
        "verify_ssl": os.getenv("PROXMOX_VERIFY_SSL", "false").lower() == "true",
    }


def _get_data(transport, url, settings, timeout=DEFAULT_TIMEOUT):
    """Appel Proxmox via le transport, erreur convertie en ProxmoxClientError."""
    try:
        payload = transport(url, settings["headers"], timeout, settings["verify_ssl"])
    except ProxmoxClientError:
        raise
    except urllib.error.HTTPError as exc:
        raise ProxmoxClientError(
            f"Proxmox: HTTP {exc.code} sur {url} — vérifiez PROXMOX_URL et les "
            "jetons PROXMOX_TOKEN_ID / PROXMOX_TOKEN_SECRET"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProxmoxClientError(
            f"Proxmox: connexion impossible sur {url} ({exc.reason})"
        ) from exc
    except TimeoutError as exc:
        raise ProxmoxClientError(
            f"Proxmox: délai dépassé ({timeout} s) sur {url}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProxmoxClientError(f"Proxmox: réponse JSON invalide sur {url}") from exc
    except (ssl.SSLError, ValueError) as exc:
        raise ProxmoxClientError(f"Proxmox: échec du transport sur {url}") from exc
    if not isinstance(payload, dict):
        raise ProxmoxClientError(
            f"Proxmox: objet JSON attendu sur {url} — réponse inattendue"
        )
    if "data" not in payload:
        raise ProxmoxClientError(
            f"Proxmox: champ « data » absent sur {url} — réponse inattendue"
        )
    return payload["data"]


def _parse_tags(tag_string):
    """Convertit les tags Proxmox (séparés par ';') en format CSV."""
    if not tag_string:
        return None
    tags = [t.strip() for t in tag_string.split(";") if t.strip()]
    return ", ".join(tags) if tags else None


def _fetch_vm_status(transport, settings, node, vmid, vm_type):
    """Récupère le statut détaillé d'une VM ou d'un CT."""
    url = f"{settings['base_url']}/api2/json/nodes/{node}/{vm_type}/{vmid}/status/current"
    data = _get_data(transport, url, settings)
    if not isinstance(data, dict):
        raise ProxmoxClientError(
            f"Proxmox: statut inattendu pour {vm_type}/{vmid} sur {node}"
        )
    return data


def _fetch_vm_config(transport, settings, node, vmid, vm_type):
    """Récupère la config d'une VM ou CT (tags, ostype, searchdomain, description)."""
    url = f"{settings['base_url']}/api2/json/nodes/{node}/{vm_type}/{vmid}/config"
    data = _get_data(transport, url, settings)
    if not isinstance(data, dict):
        raise ProxmoxClientError(
            f"Proxmox: config inattendue pour {vm_type}/{vmid} sur {node}"
        )
    return data


def _extract_ip(vm_type, status_data, config_data):
    """Extrait l'IP reportée depuis les données disponibles."""
    # Pour les VM QEMU : via QEMU Guest Agent (si disponible dans status)
    if vm_type == "qemu":
        nics = status_data.get("nics", {})
        for nic_data in nics.values():
            for ip_info in nic_data.get("ip-addresses", []):
                addr = ip_info.get("ip-address", "")
                if (
                    addr
                    and not addr.startswith("127.")
                    and ip_info.get("ip-address-type") == "ipv4"
                ):
                    return addr

    # Pour LXC : l'IP est souvent dans la config (net0, net1, etc.)
    for key, val in config_data.items():
        if key.startswith("net") and isinstance(val, str) and "ip=" in val:
            for part in val.split(","):
                if part.strip().startswith("ip="):
                    ip = part.strip().split("=", 1)[1]
                    return ip.split("/")[0]  # retirer le masque CIDR

    return None


def fetch_proxmox_vms(transport=None):
    """Récupère toutes les VM et CT depuis l'API Proxmox VE.

    Args:
        transport: callable(url, headers, timeout, verify_ssl) -> dict ; par
            défaut l'appel HTTP urllib. Les tests fournissent un objet de
            remplacement : aucune connexion réseau n'est ouverte.

    Returns:
        list[dict]: VMs/CT avec vm_id, vm_name, type, node, status, tags,
            ip_reported, os, fqdn, annotation, cpu_count, cpu_usage, ram_max,
            ram_used, disk_max, disk_used, uptime — mêmes clés que le mock.
    """
    settings = _settings()
    if transport is None:
        transport = _default_transport
    base_url = settings["base_url"]

    # 1. Lister les nœuds du cluster
    nodes = _get_data(transport, f"{base_url}/api2/json/nodes", settings)
    if not isinstance(nodes, list):
        raise ProxmoxClientError("Proxmox: liste de nœuds inattendue")

    results = []

    for node_info in nodes:
        if not isinstance(node_info, dict) or "node" not in node_info:
            raise ProxmoxClientError("Proxmox: entrée de nœud invalide")
        node = node_info["node"]

        # 2. Pour chaque nœud, lister les VM (qemu) et les CT (lxc)
        for vm_type in ("qemu", "lxc"):
            list_url = f"{base_url}/api2/json/nodes/{node}/{vm_type}"
            vms = _get_data(transport, list_url, settings)
            if not isinstance(vms, list):
                raise ProxmoxClientError(
                    f"Proxmox: liste de {vm_type} inattendue sur {node}"
                )

            for vm in vms:
                vmid = str(vm.get("vmid", ""))
                name = vm.get("name", f"vm-{vmid}")
                status = vm.get("status", "unknown")

                # 3. Statut détaillé et config (erreur propagée, jamais ignorée)
                status_data = _fetch_vm_status(transport, settings, node, vmid, vm_type)
                config_data = _fetch_vm_config(transport, settings, node, vmid, vm_type)

                # CPU
                cpu_count = config_data.get("cores", vm.get("cpus", 1))
                if vm_type == "lxc":
                    cpu_count = config_data.get("cores", 1)
                cpu_usage_raw = status_data.get("cpu", vm.get("cpu", 0))
                cpu_usage = round(cpu_usage_raw * 100, 1) if cpu_usage_raw else 0.0

                # RAM / disque / uptime
                ram_max = status_data.get("maxmem", vm.get("maxmem", 0))
                ram_used = status_data.get("mem", vm.get("mem", 0))
                disk_max = status_data.get("maxdisk", vm.get("maxdisk", 0))
                disk_used = status_data.get("disk", vm.get("disk", 0))
                uptime = status_data.get("uptime", vm.get("uptime", 0))

                # Tags, identité Proxmox équivalente aux champs du mock
                tags = _parse_tags(config_data.get("tags", vm.get("tags", "")))
                os_name = config_data.get("ostype") or None
                search = config_data.get("searchdomain") or ""
                fqdn = f"{name}.{search}" if search else None
                annotation = config_data.get("description") or None

                results.append({
                    "vm_id": vmid,
                    "vm_name": name,
                    "type": vm_type,
                    "node": node,
                    "status": status,
                    "tags": tags,
                    "ip_reported": _extract_ip(vm_type, status_data, config_data),
                    "os": os_name,
                    "fqdn": fqdn,
                    "annotation": annotation,
                    "cpu_count": cpu_count,
                    "cpu_usage": cpu_usage,
                    "ram_max": ram_max,
                    "ram_used": ram_used,
                    "disk_max": disk_max,
                    "disk_used": disk_used,
                    "uptime": uptime,
                })

    return results
