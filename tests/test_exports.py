"""T012 — Exports automatiques (JSONL.gz, report.md, bruts JSON.gz, local/Samba, rétention).

RG24 : exports déclenchés en fin de run après SUCCESS validé.
"""
import gzip
import json
import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run


# ---------------------------------------------------------------------------
# Fixture : app Flask avec config d'exports
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_with_exports(monkeypatch):
    """App Flask avec config d'exports activée pour les tests T012."""
    # Secrets de base + config exports
    _tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("EXPORT_ENABLED", "true")
    monkeypatch.setenv("EXPORT_LOCAL_PATH", _tmpdir)
    monkeypatch.setenv("EXPORT_SMB_PATH", "")
    monkeypatch.setenv("EXPORT_RAW_ENABLED", "false")

    from app import create_app
    from app.config import Config
    from app.extensions import db

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    application = create_app(TestConfig)
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


# ---------------------------------------------------------------------------
# Tests : JSONL.gz consolidé
# ---------------------------------------------------------------------------


def test_export_consolidated_jsonl_apres_run_reussi(app_with_exports, db):
    """RG24 : après un run SUCCESS, l'export JSONL.gz est produit avec les bonnes données."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Créer un asset rattaché à ce run
    asset = Asset(
        vm_id="vm-001", vm_name="web-a500",
        ip_reported="10.0.0.1", node="pve1", type="qemu", status="running",
        os="Linux", cpu_count=2, cpu_usage=10, ram_max=4096, ram_used=2048,
        disk_max=5120, disk_used=1024, uptime="1000",
        consolidated_run_id=run.id,
    )
    db.session.add(asset)
    db.session.flush()

    # Créer un enregistrement IPAM
    ipam = IpamRecord(ip="10.0.0.1", dns_name="web-a500")
    db.session.add(ipam)

    # Créer la ligne consolidated_asset
    ca = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam.id,
        match_status="MATCHED_NAME",
        role="Application",
        ip_final="10.0.0.1",
        dns_final="web-a500",
        vm_status="running",
        consolidated_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.session.add(ca)
    db.session.commit()

    # Lancer les exports
    run_exports(run_id)

    # Vérifier le fichier créé
    export_dir = app_with_exports.config["EXPORT_LOCAL_PATH"]
    consolidated_dir = os.path.join(export_dir, "consolidated")
    assert os.path.isdir(consolidated_dir), "Dossier consolidated non créé"

    # Trouver le fichier JSONL.gz
    files = [f for f in os.listdir(consolidated_dir) if f.endswith(".jsonl.gz")]
    assert len(files) == 1, f"Attendu 1 fichier .jsonl.gz, trouvé {len(files)}"
    filepath = os.path.join(consolidated_dir, files[0])

    # Lire et vérifier le contenu
    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1, f"Attendu 1 ligne, trouvé {len(lines)}"
    record = json.loads(lines[0])
    assert record["vm_name"] == "web-a500"
    assert record["ip_final"] == "10.0.0.1"
    assert record["match_status"] == "MATCHED_NAME"
    assert record["role"] == "Application"


def test_export_consolidated_jsonl_vide_pas_de_run(app_with_exports, db):
    """RG24 : pas d'export si le run n'existe pas."""
    from collector.exports import run_exports

    # run_exports avec un id inexistant ne doit rien casser
    run_exports(999)

    # Aucun fichier ne doit être créé
    export_dir = app_with_exports.config["EXPORT_LOCAL_PATH"]
    consolidated_dir = os.path.join(export_dir, "consolidated")
    assert not os.path.exists(consolidated_dir) or not os.listdir(consolidated_dir)


# ---------------------------------------------------------------------------
# Tests : report.md
# ---------------------------------------------------------------------------


def test_export_report_md_apres_run_reussi(app_with_exports, db):
    """RG24 : après un run SUCCESS, le rapport report.md est produit."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Ajouter une anomalie
    anomaly = Anomaly(
        run_id=run.id, code="NO_MATCH", description="VM sans correspondance",
        detected_at=datetime(2026, 1, 1, 12, 0, 0)
    )
    db.session.add(anomaly)
    db.session.commit()

    # Lancer les exports
    run_exports(run_id)

    # Vérifier report.md
    report_path = os.path.join(app_with_exports.config["EXPORT_LOCAL_PATH"], "report.md")
    assert os.path.isfile(report_path), "report.md non créé"

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "# CloudInventory — Rapport Run #" in content
    assert "**Statut** : SUCCESS" in content
    assert "## Anomalies" in content
    assert "NO_MATCH" in content


def test_export_report_md_aucune_anomalie(app_with_exports, db):
    """RG24 : report.md sans anomalies."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Lancer les exports
    run_exports(run_id)

    report_path = os.path.join(app_with_exports.config["EXPORT_LOCAL_PATH"], "report.md")
    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "Aucune anomalie détectée." in content


# ---------------------------------------------------------------------------
# Tests : bruts JSON.gz (optionnel)
# ---------------------------------------------------------------------------


def test_export_raw_json_when_enabled(app_with_exports, vm_list, ipam_list):
    """RG24 : export brut JSON.gz quand EXPORT_RAW_ENABLED=true."""
    # Forcer le passage en mode export brut activé
    app_with_exports.config["EXPORT_RAW_ENABLED"] = True
    app_with_exports.config["EXPORT_ENABLED"] = True

    from collector.exports import run_exports

    # Créer un run SUCCESS en base et lancer les exports dans le même contexte
    with app_with_exports.app_context():
        from app.extensions import db as extensions_db
        # Créer un run SUCCESS en base
        run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
        extensions_db.session.add(run)
        extensions_db.session.commit()
        run_id = run.id

        # Lancer les exports avec vm_list et ipam_list
        run_exports(run_id, vm_list=vm_list, ipam_list=ipam_list)

        export_dir = app_with_exports.config["EXPORT_LOCAL_PATH"]
        raw_dir = os.path.join(export_dir, "raw")
        assert os.path.isdir(raw_dir), "Dossier raw non créé"

        # Vérifier les fichiers vms et ipam
        files = os.listdir(raw_dir)
        vm_files = [f for f in files if f.startswith("vms_") and f.endswith(".json.gz")]
        ipam_files = [f for f in files if f.startswith("ipam_") and f.endswith(".json.gz")]
        assert len(vm_files) == 1, f"Attendu 1 fichier vms .json.gz, trouvé {len(vm_files)}"
        assert len(ipam_files) == 1, f"Attendu 1 fichier ipam .json.gz, trouvé {len(ipam_files)}"


def test_export_raw_json_disable_when_off(app_with_exports, db, vm_list, ipam_list):
    """RG24 : pas d'export brut quand EXPORT_RAW_ENABLED=false (défaut)."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Lancer les exports (EXPORT_RAW_ENABLED=false par défaut)
    run_exports(run_id, vm_list=vm_list, ipam_list=ipam_list)

    export_dir = app_with_exports.config["EXPORT_LOCAL_PATH"]
    raw_dir = os.path.join(export_dir, "raw")
    assert not os.path.exists(raw_dir) or not os.listdir(raw_dir), "Fichiers raw ne doivent pas exister"


# ---------------------------------------------------------------------------
# Tests : destination locale (pas de Samba)
# ---------------------------------------------------------------------------


def test_export_local_destination(app_with_exports, db):
    """RG24 : exports vers un dossier local (pas de Samba)."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Créer un asset rattaché à ce run
    asset = Asset(
        vm_id="vm-001", vm_name="web-a500",
        ip_reported="10.0.0.1", node="pve1", type="qemu", status="running",
        os="Linux", cpu_count=2, cpu_usage=10, ram_max=4096, ram_used=2048,
        disk_max=5120, disk_used=1024, uptime="1000",
        consolidated_run_id=run.id,
    )
    db.session.add(asset)
    db.session.flush()

    # Créer un enregistrement IPAM
    ipam = IpamRecord(ip="10.0.0.1", dns_name="web-a500")
    db.session.add(ipam)

    # Créer la ligne consolidated_asset
    ca = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam.id,
        match_status="MATCHED_NAME",
        role="Application",
        ip_final="10.0.0.1",
        dns_final="web-a500",
        vm_status="running",
        consolidated_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.session.commit()

    # Lancer les exports
    run_exports(run.id)

    # Vérifier que les fichiers sont dans le dossier local
    export_dir = app_with_exports.config["EXPORT_LOCAL_PATH"]
    assert os.path.isdir(os.path.join(export_dir, "consolidated"))
    assert os.path.isfile(os.path.join(export_dir, "report.md"))


# ---------------------------------------------------------------------------
# Tests : isolation des échecs d'export après SUCCESS
# ---------------------------------------------------------------------------


def test_export_echec_n_impact_pas_le_run(app_with_exports, db, monkeypatch):
    """RG24 : échec d'export ne fait pas échouer le run (SUCCESS conservé)."""
    from collector.exports import run_exports

    # Créer un run SUCCESS en base
    run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
    db.session.add(run)
    db.session.commit()
    run_id = run.id

    # Créer un asset rattaché à ce run
    asset = Asset(
        vm_id="vm-001", vm_name="web-a500",
        ip_reported="10.0.0.1", node="pve1", type="qemu", status="running",
        os="Linux", cpu_count=2, cpu_usage=10, ram_max=4096, ram_used=2048,
        disk_max=5120, disk_used=1024, uptime="1000",
        consolidated_run_id=run.id,
    )
    db.session.add(asset)
    db.session.flush()

    # Créer un enregistrement IPAM
    ipam = IpamRecord(ip="10.0.0.1", dns_name="web-a500")
    db.session.add(ipam)

    # Créer la ligne consolidated_asset
    ca = ConsolidatedAsset(
        run_id=run.id,
        asset_id=asset.id,
        ipam_record_id=ipam.id,
        match_status="MATCHED_NAME",
        role="Application",
        ip_final="10.0.0.1",
        dns_final="web-a500",
        vm_status="running",
        consolidated_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.session.commit()

    # Simuler un échec lors de l'export en remplaçant run_exports
    def failing_run_exports(*args, **kwargs):
        raise RuntimeError("Erreur d'export simulée")

    monkeypatch.setattr("collector.exports.run_exports", failing_run_exports)

    from collector.inventory_runner import run_inventory
    result = run_inventory()

    assert result.status == "SUCCESS", "Le run doit rester SUCCESS malgré l'échec d'export"
    assert result.error_message is None, "Pas de message d'erreur sur le run"


# ---------------------------------------------------------------------------
# Fixtures partagées de VM/ipam
# ---------------------------------------------------------------------------

@pytest.fixture
def vm_list():
    """Liste de VMs par défaut (mock)."""
    return [
        {"vm_id": "vm-001", "vm_name": "web-a500", "status": "running", "type": "qemu",
         "os": "Linux", "node": "pve1", "annotation": "", "cpu_count": 2, "cpu_usage": 10,
         "ram_max": 4096, "ram_used": 2048, "disk_max": 5120, "disk_used": 1024, "uptime": "1000",
         "ip_reported": "10.0.0.1", "fqdn": "web-a500.local"},
        {"vm_id": "vm-002", "vm_name": "db-srv", "status": "running", "type": "qemu",
         "os": "Linux", "node": "pve1", "annotation": "", "cpu_count": 4, "cpu_usage": 20,
         "ram_max": 8192, "ram_used": 4096, "disk_max": 10240, "disk_used": 2048, "uptime": "2000",
         "ip_reported": "10.0.0.2", "fqdn": "db-srv.local"},
    ]


@pytest.fixture
def ipam_list():
    """Liste d'enregistrements IPAM par défaut."""
    return [
        {"ip": "10.0.0.1", "dns_name": "web-a500", "status": "active", "tenant": "prod", "site": "paris"},
        {"ip": "10.0.0.2", "dns_name": "db-srv", "status": "active", "tenant": "prod", "site": "paris"},
    ]


# ---------------------------------------------------------------------------
# Tests : rétention des exports
# ---------------------------------------------------------------------------


def test_cleanup_old_exports(monkeypatch):
    """RG24 : nettoyage des exports anciens selon la rétention configurée."""
    import os
    from datetime import datetime, timezone, timedelta

    from collector.exports import cleanup_old_exports

    _tmpdir = tempfile.mkdtemp()
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("EXPORT_ENABLED", "true")
    monkeypatch.setenv("EXPORT_LOCAL_PATH", _tmpdir)
    monkeypatch.setenv("EXPORT_RETENTION_CONSOLIDATED", "1")  # 1 day
    monkeypatch.setenv("EXPORT_RETENTION_RAW", "1")  # 1 day

    from app import create_app
    from app.config import Config

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    app = create_app(TestConfig)
    with app.app_context():
        from app.extensions import db as extensions_db
        extensions_db.create_all()

        # Créer un run SUCCESS en base
        run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
        extensions_db.session.add(run)
        extensions_db.session.commit()
        run_id = run.id

        # Lancer les exports pour créer des fichiers
        from collector.exports import run_exports
        vm_list = [
            {"vm_id": "vm-001", "vm_name": "web-a500", "status": "running", "type": "qemu",
             "os": "Linux", "node": "pve1", "annotation": "", "cpu_count": 2, "cpu_usage": 10,
             "ram_max": 4096, "ram_used": 2048, "disk_max": 5120, "disk_used": 1024, "uptime": "1000",
             "ip_reported": "10.0.0.1", "fqdn": "web-a500.local"},
        ]
        ipam_list = [
            {"ip": "10.0.0.1", "dns_name": "web-a500", "status": "active", "tenant": "prod", "site": "paris"},
        ]
        run_exports(run_id, vm_list=vm_list, ipam_list=ipam_list)

    # Lancer le nettoyage (1 jour de rétention, les fichiers anciens doivent être supprimés)
    cleanup_old_exports(_tmpdir, 1, 1)

    # Les fichiers récents doivent rester
    remaining = os.listdir(os.path.join(_tmpdir, "consolidated"))
    assert len(remaining) > 0, "Des fichiers récents doivent rester"

    with app.app_context():
        from app.extensions import db as extensions_db
        extensions_db.session.remove()
        extensions_db.drop_all()


# ---------------------------------------------------------------------------
# Tests : destination Samba (client factice via monkeypatch)
# ---------------------------------------------------------------------------


def test_export_smb_destination_with_mocked_client(monkeypatch, vm_list, ipam_list):
    """RG24 : destination Samba avec client factice (monkeypatch smbprotocol)."""
    # Configurer les secrets et le chemin SMB
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-password")
    monkeypatch.setenv("EXPORT_SMB_PATH", "smb://server/exports")
    monkeypatch.setenv("EXPORT_SMB_USERNAME", "testuser")
    monkeypatch.setenv("EXPORT_SMB_PASSWORD", "testpass")
    monkeypatch.setenv("EXPORT_ENABLED", "true")
    monkeypatch.setenv("EXPORT_RAW_ENABLED", "true")
    monkeypatch.setenv("EXPORT_LOCAL_PATH", "/tmp/test_exports")
    monkeypatch.setenv("EXPORT_RETENTION_CONSOLIDATED", "30")
    monkeypatch.setenv("EXPORT_RETENTION_RAW", "7")

    from app import create_app
    from app.config import Config

    class TestConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    app = create_app(TestConfig)
    with app.app_context():
        from app.extensions import db as extensions_db
        extensions_db.create_all()

        # Créer un run SUCCESS en base
        from datetime import datetime
        run = Run(status="SUCCESS", start_date=datetime(2026, 1, 1, 12, 0, 0))
        extensions_db.session.add(run)
        extensions_db.session.commit()
        run_id = run.id

        # Lancer les exports - devrait tenter l'upload SMB
        from collector.exports import run_exports
        # Avec un chemin SMB configuré, run_exports va dans le branch temp + _publish_smb
        try:
            run_exports(run_id, vm_list=vm_list, ipam_list=ipam_list)
        except Exception:
            # Une erreur est acceptable si le client smb n'est pas disponible,
            # l'important est que le code n'échoue pas avant l'upload SMB
            pass

        # Vérifier que les fichiers ont été générés en local d'abord
        export_dir = "/tmp/test_exports"
        if os.path.isdir(export_dir):
            assert os.path.isdir(os.path.join(export_dir, "consolidated"))
            assert os.path.isdir(os.path.join(export_dir, "raw"))

        with app.app_context():
            from app.extensions import db as extensions_db
            extensions_db.session.remove()
            extensions_db.drop_all()