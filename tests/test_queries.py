"""T007 — Lectures du module app/queries.py sur base SQLite de test.

Critères : filtrage/tri/pagination/sérialisation de l'inventaire, statistiques
du dashboard, comparaison de runs, export CSV — plus le cas vide et l'isolation
des runs (une requête ne doit jamais voir les lignes d'un autre run).
Chaque test part d'un état semé localement : aucun test ne dépend d'un autre.
"""
import csv
import io
from datetime import datetime

import app.queries as queries
from app.extensions import db
from app.models import Anomaly, Asset, ConsolidatedAsset, IpamRecord, Run

NOW = datetime(2026, 1, 1, 12, 0, 0)

# En-tête attendu de l'export, aligné sur les colonnes de docs/modele/schema.sql
# (pas de colonne os/métriques/meta_zone dans ce schéma, cf. référence seule).
CSV_HEADER = [
    "Hostname", "Hote", "Etat", "Type", "IP", "DNS", "FQDN",
    "Role", "Tenant", "Site", "Match", "Source",
]


def _add_run(db_session, status="SUCCESS", **counts):
    """Run persistant ; counts = compteurs de match du run (RG)."""
    session = db_session.session
    run = Run(status=status, start_date=NOW, **counts)
    session.add(run)
    session.commit()
    return run


def _add_item(db_session, run, vm_name, *, vm_type="qemu", vm_status="running",
              node="pve1", tags=None, ip_reported=None, fqdn=None,
              match_status="MATCHED_NAME", role=None, anomaly_codes=None,
              source="VIRT", ipam=None):
    """Asset + ConsolidatedAsset (+ IpamRecord facultatif) pour un run."""
    session = db_session.session
    asset = Asset(
        vm_id=vm_name, vm_name=vm_name, node=node, type=vm_type,
        status=vm_status, tags=tags, ip_reported=ip_reported, fqdn=fqdn,
        source=source, consolidated_run_id=run.id,
    )
    session.add(asset)
    session.flush()

    record = None
    if ipam is not None:
        record = IpamRecord(**ipam)
        session.add(record)
        session.flush()

    ca = ConsolidatedAsset(
        asset_id=asset.id, ipam_record_id=record.id if record else None,
        match_status=match_status, role=role, anomaly_codes=anomaly_codes,
    )
    session.add(ca)
    session.commit()
    return ca, asset, record


def _seed(db_session):
    """Deux runs indépendants : A (3 items, dont 1 sans IPAM) et B (1 item)."""
    run_a = _add_run(
        db_session,
        matched_name_count=1, matched_fqdn_count=1,
        matched_ip_count=0, no_match_count=1,
    )
    item1 = _add_item(
        db_session, run_a, "web-a500", node="pve1", vm_type="qemu",
        vm_status="running", tags="prod,web", ip_reported="10.0.0.10",
        fqdn="web-a500.example.lan", role="Serveur",
        ipam={"ip": "10.0.0.10", "dns_name": "web-a500.internal",
              "tenant": "prod", "site": "dc1"},
    )
    item2 = _add_item(
        db_session, run_a, "db-b300", node="pve2", vm_type="lxc",
        vm_status="stopped", tags="db", match_status="NO_MATCH",
        ipam={"ip": "10.0.0.20", "dns_name": "db-b300.internal",
              "tenant": "prod", "site": "dc2"},
    )
    item3 = _add_item(
        db_session, run_a, "cache-n100", node="pve1", vm_type="qemu",
        vm_status="running", match_status="MATCHED_IP", role="Cache",
    )

    run_b = _add_run(db_session, matched_name_count=1, no_match_count=0)
    item4 = _add_item(
        db_session, run_b, "other-c700", node="pve3", vm_type="qemu",
        vm_status="running", tags="prod", ip_reported="10.0.0.70",
        role="Serveur",
        ipam={"ip": "10.0.0.70", "dns_name": "other-c700.internal",
              "tenant": "rec", "site": "dc1"},
    )
    return run_a, run_b, (item1, item2, item3, item4)


def _names(query):
    return [asset.vm_name for _ca, asset, _ipam in query.all()]


def test_inventory_query_returns_only_the_requested_run(db):
    run_a, run_b, _items = _seed(db)

    rows_a = queries.build_inventory_query(run_a.id).all()
    rows_b = queries.build_inventory_query(run_b.id).all()

    assert len(rows_a) == 3
    assert [asset.vm_name for _ca, asset, _ipam in rows_b] == ["other-c700"]


def test_free_text_filter_matches_vm_name_ip_and_dns(db):
    run_a, _run_b, _items = _seed(db)

    by_name = queries.build_inventory_query(run_a.id, q="web-a500")
    by_asset_ip = queries.build_inventory_query(run_a.id, q="10.0.0.10")
    by_dns = queries.build_inventory_query(run_a.id, q="db-b300.internal")
    by_ipam_ip = queries.build_inventory_query(run_a.id, q="10.0.0.20")

    assert _names(by_name) == ["web-a500"]
    assert _names(by_asset_ip) == ["web-a500"]
    assert _names(by_dns) == ["db-b300"]
    assert _names(by_ipam_ip) == ["db-b300"]


def test_free_text_filter_with_no_hit_returns_no_row(db):
    run_a, _run_b, _items = _seed(db)

    assert queries.build_inventory_query(run_a.id, q="absent").all() == []


def test_filters_status_node_type_match_role_and_tag(db):
    run_a, _run_b, _items = _seed(db)

    assert _names(queries.build_inventory_query(run_a.id, status="stopped")) == ["db-b300"]
    assert _names(queries.build_inventory_query(run_a.id, node="pve2")) == ["db-b300"]
    assert _names(queries.build_inventory_query(run_a.id, vm_type="lxc")) == ["db-b300"]
    assert _names(queries.build_inventory_query(run_a.id, match="MATCHED_IP")) == ["cache-n100"]
    assert _names(queries.build_inventory_query(run_a.id, role="Serveur")) == ["web-a500"]
    assert _names(queries.build_inventory_query(run_a.id, tag="db")) == ["db-b300"]


def test_combined_filters_narrow_the_result_set(db):
    run_a, _run_b, _items = _seed(db)

    both = queries.build_inventory_query(
        run_a.id, node="pve1", vm_type="qemu", status="running"
    )
    contradiction = queries.build_inventory_query(
        run_a.id, node="pve1", vm_type="lxc"
    )

    assert _names(both) == ["cache-n100", "web-a500"]
    assert contradiction.all() == []


def test_sort_uses_whitelist_column_and_order(db):
    run_a, _run_b, _items = _seed(db)

    ascending = _names(queries.build_inventory_query(run_a.id, sort="vm_name", order="asc"))
    descending = _names(queries.build_inventory_query(run_a.id, sort="vm_name", order="desc"))
    by_node_rows = queries.build_inventory_query(
        run_a.id, sort="node", order="asc"
    ).all()

    nodes = [asset.node for _ca, asset, _ipam in by_node_rows]
    assert ascending == ["cache-n100", "db-b300", "web-a500"]
    assert descending == list(reversed(ascending))
    assert nodes == ["pve1", "pve1", "pve2"]  # tri sur la colonne « node »


def test_unknown_sort_column_falls_back_to_vm_name(db):
    run_a, _run_b, _items = _seed(db)

    fallback = queries.build_inventory_query(
        run_a.id, sort="evil; DROP TABLE asset", order="asc"
    )

    assert _names(fallback) == ["cache-n100", "db-b300", "web-a500"]


def test_query_object_supports_limit_and_offset(db):
    run_a, _run_b, _items = _seed(db)

    page = queries.build_inventory_query(run_a.id, sort="vm_name", order="asc")
    first_page = _names(page.limit(2).offset(0))
    second_page = _names(page.limit(2).offset(2))

    assert len(first_page) == 2
    assert second_page == ["web-a500"]
    assert set(first_page).isdisjoint(second_page)


def test_serialize_item_exposes_inventory_fields(db):
    run_a, _run_b, items = _seed(db)
    ca, asset, ipam = items[0]

    payload = queries.serialize_inventory_item(ca, asset, ipam)

    assert payload == {
        "id": asset.id,
        "vm_id": "web-a500",
        "vm_name": "web-a500",
        "node": "pve1",
        "status": "running",
        "type": "qemu",
        "tags": "prod,web",
        "ip": "10.0.0.10",
        "dns": "web-a500.internal",
        "fqdn": "web-a500.example.lan",
        "match_status": "MATCHED_NAME",
        "role": "Serveur",
        "anomaly_codes": "",
        "source": "VIRT",
        "tenant": "prod",
        "site": "dc1",
    }


def test_serialize_item_without_ipam_uses_safe_defaults(db):
    run_a, _run_b, items = _seed(db)
    ca, asset, ipam = items[2]  # cache-n100 : aucun IPAM, role renseigné
    ca_no_role, asset_no_role, ipam_no_role = items[1]  # db-b300 : sans rôle

    payload = queries.serialize_inventory_item(ca, asset, ipam)
    payload_no_role = queries.serialize_inventory_item(
        ca_no_role, asset_no_role, ipam_no_role
    )

    assert payload["dns"] == "" and payload["tenant"] is None
    assert payload["site"] is None and payload["ip"] == ""
    assert payload_no_role["role"] == "Indéterminé"


def test_stats_without_any_run_reports_no_data(db):
    assert queries.get_stats_data() == {"has_data": False}


def test_stats_report_last_run_matches_anomalies_and_evolution(db):
    run_a = _add_run(db, matched_name_count=1, no_match_count=1)
    run_failed = _add_run(db, status="FAIL")
    last_run = _add_run(db, matched_name_count=2, matched_ip_count=1)
    _ca, asset, _ipam = _add_item(db, last_run, "web-a500")
    _add_item(db, run_a, "old-a500")
    db.session.add(Anomaly(
        run_id=last_run.id, asset_id=asset.id, code="DUPLICATE_IP",
        detected_at=NOW,
    ))
    db.session.add(Anomaly(
        run_id=last_run.id, asset_id=asset.id, code="DUPLICATE_IP",
        detected_at=NOW,
    ))
    db.session.add(Anomaly(
        run_id=last_run.id, code="HOSTNAME_MISMATCH", detected_at=NOW,
    ))
    db.session.add(Anomaly(run_id=run_a.id, code="STATUS_MISMATCH", detected_at=NOW))
    db.session.commit()

    stats = queries.get_stats_data()

    assert stats["has_data"] is True
    assert stats["match"] == {
        "matched_name": 2, "matched_fqdn": 0,
        "matched_ip": 1, "no_match": 0,
    }
    assert stats["anomalies"] == {"DUPLICATE_IP": 2, "HOSTNAME_MISMATCH": 1}
    assert stats["evolution"]["labels"] == ["#%d" % run_a.id, "#%d" % last_run.id]
    assert stats["evolution"]["matched_name"] == [1, 2]


def test_stats_evolution_lists_only_the_last_ten_successful_runs(db):
    for index in range(12):
        _add_run(db, matched_name_count=index)
    failed = _add_run(db, status="FAIL")

    stats = queries.get_stats_data()
    labels = stats["evolution"]["labels"]

    assert len(labels) == 10
    assert labels[0] == "#3" and labels[-1] == "#12"
    assert "#%d" % failed.id not in labels  # run en échec exclu


def test_comparison_indexes_rows_by_vm_name_per_run(db):
    run_a, run_b, _items = _seed(db)

    comparison_a = queries.get_run_comparison_data(run_a.id)
    comparison_b = queries.get_run_comparison_data(run_b.id)

    assert set(comparison_a) == {"web-a500", "db-b300", "cache-n100"}
    ca, asset, ipam = comparison_a["cache-n100"]
    assert asset.vm_name == "cache-n100" and ipam is None
    assert ca.match_status == "MATCHED_IP"
    assert set(comparison_b) == {"other-c700"}


def test_comparison_of_a_run_without_rows_is_empty(db):
    empty_run = _add_run(db)

    assert queries.get_run_comparison_data(empty_run.id) == {}


def test_export_csv_returns_header_then_one_line_per_row(db):
    run_a, _run_b, _items = _seed(db)

    content = queries.export_inventory_csv(run_a.id)
    rows = list(csv.reader(io.StringIO(content), delimiter=";"))

    assert rows[0] == CSV_HEADER
    assert len(rows) == 4  # en-tête + 3 lignes
    assert rows[1][0] == "web-a500"
    assert rows[1][4:6] == ["10.0.0.10", "web-a500.internal"]
    assert rows[1][7:11] == ["Serveur", "prod", "dc1", "MATCHED_NAME"]


def test_export_csv_of_run_without_ipam_fills_absent_columns(db):
    run_a, _run_b, _items = _seed(db)

    rows = list(csv.reader(
        io.StringIO(queries.export_inventory_csv(run_a.id)), delimiter=";"
    ))
    cache_row = next(row for row in rows if row[0] == "cache-n100")
    db_row = next(row for row in rows if row[0] == "db-b300")

    assert cache_row[4:6] == ["", ""]          # ip_reported et dns absents
    assert cache_row[7:11] == ["Cache", "", "", "MATCHED_IP"]
    assert db_row[7] == ""                     # rôle absent → CSV vide (pas « Indéterminé »)


def test_export_csv_of_a_run_without_rows_is_header_only(db):
    empty_run = _add_run(db)
    run_with_rows, _run_b, _items = _seed(db)

    content = queries.export_inventory_csv(empty_run.id)
    rows = list(csv.reader(io.StringIO(content), delimiter=";"))

    assert rows == [CSV_HEADER]
    assert len(list(csv.reader(
        io.StringIO(queries.export_inventory_csv(run_with_rows.id)),
        delimiter=";",
    ))) == 4
