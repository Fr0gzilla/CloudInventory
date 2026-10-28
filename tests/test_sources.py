from collections import Counter
import socket

import pytest

from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource
from collector.source import AssetRecord, IpamRecord, Source


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("outbound network access is forbidden")

    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def test_source_contract_requires_collect():
    with pytest.raises(TypeError):
        Source()


@pytest.mark.parametrize("source,record_type", [
    (MockProxmoxSource, AssetRecord), (MockIpamSource, IpamRecord),
])
def test_sources_implement_collect_contract(source, record_type):
    instance: Source[record_type] = source()
    assert isinstance(instance.collect(), list)


def test_proxmox_has_45_unique_assets_nine_per_node_and_both_types():
    assets = MockProxmoxSource().collect()
    assert len(assets) == 45
    assert len({asset["vm_id"] for asset in assets}) == 45
    assert Counter(asset["node"] for asset in assets) == {
        f"pve{i}": 9 for i in range(1, 6)
    }
    assert {asset["type"] for asset in assets} == {"qemu", "lxc"}


@pytest.mark.parametrize("scenario,changed,created", [
    ("unchanged", 0, set()),
    ("renamed", 1, set()),
    ("renamed_with_creation", 1, {"new-100"}),
])
def test_proxmox_scenarios_keep_ids_and_distinguish_renames_from_creations(scenario, changed, created):
    previous = {item["vm_id"]: item["vm_name"] for item in MockProxmoxSource().collect()}
    current = {item["vm_id"]: item["vm_name"] for item in MockProxmoxSource(scenario).collect()}
    assert len([key for key in previous.keys() & current.keys() if previous[key] != current[key]]) == changed
    assert current.keys() - previous.keys() == created


def test_proxmox_exposes_source_fields_without_persistence_ids():
    expected = set(AssetRecord.__annotations__)
    assets = MockProxmoxSource().collect()
    assert all(set(asset) == expected and asset["role"] is None for asset in assets)


def test_ipam_has_44_records_and_source_fields_without_persistence_ids():
    records = MockIpamSource().collect()
    assert len(records) == 44
    assert all(set(record) == set(IpamRecord.__annotations__) | {"custom_fields"} for record in records)


def test_ipam_contains_40_named_assets_and_two_orphans():
    assets = MockProxmoxSource().collect()
    records = MockIpamSource().collect()
    named = {asset["vm_name"] for asset in assets}
    assert len({record["dns_name"] for record in records if record["dns_name"] in named}) == 40
    assert {record["dns_name"] for record in records if record["dns_name"] not in named} == {
        "decom-server", "orphan-lab", "shared-address"
    }


def test_decom_server_is_reserved_and_has_no_vm():
    record = next(r for r in MockIpamSource().collect() if r["dns_name"] == "decom-server")
    assert record["status"] == "reserved"
    assert record["dns_name"] not in {a["vm_name"] for a in MockProxmoxSource().collect()}


def test_ipam_covers_tenants_sites_and_zones():
    records = MockIpamSource().collect()
    assert {r["tenant"] for r in records} == {
        "Production", "Infra", "Dev", "Staging", "Supervision", "DevOps"
    }
    assert {r["site"] for r in records} == {"DC1", "DC2"}
    assert all(r["meta_zone"] for r in records)


def test_mock_ipam_exposes_custom_fields_for_every_observation_including_orphans():
    records = MockIpamSource().collect()
    orphan = next(row for row in records if row["dns_name"] == "decom-server")
    assert orphan["custom_fields"] is not None
    assert all("custom_fields" in row for row in records)


def test_four_unmatched_block_vms_have_no_matching_name_fqdn_or_ip():
    records = MockIpamSource().collect()
    assets = MockProxmoxSource().collect()
    unmatched = [a for a in assets if a["vm_id"] in {"995", "996", "997", "998"}]
    assert len(unmatched) == 4
    assert all(not any(
        r["dns_name"] in {a["vm_name"], a["fqdn"].split(".")[0]}
        or r["ip"] == a["ip_reported"] for r in records
    ) for a in unmatched)


def test_ghost_vm_matches_only_by_ip_with_different_hostname():
    ghost = next(a for a in MockProxmoxSource().collect() if a["vm_name"] == "ghost-vm")
    matches = [r for r in MockIpamSource().collect() if r["ip"] == ghost["ip_reported"]]
    assert ghost["vm_id"] == "999"
    assert matches and all(r["dns_name"] != ghost["vm_name"] for r in matches)
    assert not any(r["dns_name"] == ghost["fqdn"].split(".")[0]
                   for r in MockIpamSource().collect())


def test_stopped_vm_has_active_ipam_address():
    records = MockIpamSource().collect()
    assert any(a["status"] == "stopped" and any(
        r["ip"] == a["ip_reported"] and r["status"] == "active" for r in records
    ) for a in MockProxmoxSource().collect())


def test_monitoring_dns_is_on_two_distinct_ips():
    ips = [r["ip"] for r in MockIpamSource().collect() if r["dns_name"] == "monitoring"]
    assert len(ips) == 2 and len(set(ips)) == 2


def test_duplicate_ip_has_two_different_dns_names():
    names = [r["dns_name"] for r in MockIpamSource().collect() if r["ip"] == "10.0.4.16"]
    assert len(names) == 2 and len(set(names)) == 2


@pytest.mark.parametrize("source", [MockProxmoxSource, MockIpamSource])
def test_collections_are_deterministic_and_isolated_between_calls_and_instances(source):
    instance = source()
    original = instance.collect()
    assert original == instance.collect() == source().collect()
    original[0][next(iter(original[0]))] = "changed"
    original.clear()
    assert instance.collect() == source().collect()
    assert instance.collect()
