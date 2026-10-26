from collections import Counter

import pytest

from collector.matching import consolidate_asset, match_asset
from collector.mock_ipam import MockIpamSource
from collector.mock_proxmox import MockProxmoxSource


@pytest.mark.parametrize("asset,records,expected", [
    ({"vm_name": " WEB-A500.prod.local ", "fqdn": "", "ip_reported": ""},
     [{"dns_name": "web-a500", "ip": ""}], "MATCHED_NAME"),
    ({"vm_name": "other", "fqdn": "web-a500.prod.local", "ip_reported": ""},
     [{"dns_name": "web-a500", "ip": ""}], "MATCHED_FQDN"),
    ({"vm_name": "other", "fqdn": "", "ip_reported": "192.0.2.5"},
     [{"dns_name": "different", "ip": "192.0.2.5"}], "MATCHED_IP"),
    ({"vm_name": "other", "fqdn": "", "ip_reported": "192.0.2.6"},
     [{"dns_name": "different", "ip": "192.0.2.5"}], "NO_MATCH"),
])
def test_match_asset_returns_exact_four_levels(asset, records, expected):
    assert match_asset(asset, records)[0] == expected


def test_name_wins_globally_over_earlier_fqdn_and_ip_candidates():
    asset = {"vm_name": "alpha", "fqdn": "beta.example", "ip_reported": "192.0.2.1"}
    records = [{"dns_name": "beta", "ip": "192.0.2.1"},
               {"dns_name": "alpha.example", "ip": "192.0.2.2"}]
    assert match_asset(asset, records) == ("MATCHED_NAME", records[1])


def test_fqdn_wins_globally_over_earlier_ip_candidate():
    asset = {"vm_name": "other", "fqdn": "beta.example", "ip_reported": "192.0.2.1"}
    records = [{"dns_name": "gamma", "ip": "192.0.2.1"},
               {"dns_name": "beta", "ip": "192.0.2.2"}]
    assert match_asset(asset, records) == ("MATCHED_FQDN", records[1])


def test_missing_names_and_ip_never_match_other_missing_fields():
    asset = {"vm_name": None, "fqdn": " ", "ip_reported": ""}
    assert match_asset(asset, [{"dns_name": "", "ip": ""}]) == ("NO_MATCH", None)


def test_no_match_keeps_only_hypervisor_data():
    asset = {"vm_name": "orphan", "fqdn": "orphan.example", "ip_reported": "192.0.2.7",
             "status": "stopped", "tags": ""}
    result = consolidate_asset(asset, [{"dns_name": "unrelated", "ip": "192.0.2.8",
                                        "status": "active"}])
    assert {key: result[key] for key in ("match_status", "ipam_record", "ip_final",
                                          "dns_final", "status_final")} == {
        "match_status": "NO_MATCH", "ipam_record": None,
        "ip_final": "192.0.2.7", "dns_final": "orphan.example",
        "status_final": "stopped",
    }


def test_consolidation_prefers_ipam_fields_and_does_not_alias_inputs():
    asset = {"vm_name": "web-a500", "fqdn": "web-a500.example",
             "ip_reported": "192.0.2.1", "status": "stopped", "tags": ""}
    ipam = {"dns_name": "web-a500", "ip": "192.0.2.2", "status": "active"}
    result = consolidate_asset(asset, [ipam])
    assert (result["ip_final"], result["dns_final"], result["status_final"],
            result["role"]) == ("192.0.2.2", "web-a500", "active", "Application")
    result["asset"]["vm_name"] = "changed"
    result["ipam_record"]["ip"] = "changed"
    assert asset["vm_name"] == "web-a500" and ipam["ip"] == "192.0.2.2"


def test_mock_sources_cover_name_ip_and_no_match_without_side_effects():
    assets = MockProxmoxSource().collect()
    records = MockIpamSource().collect()
    results = [consolidate_asset(asset, records) for asset in assets]
    assert Counter(result["match_status"] for result in results) == {
        "MATCHED_NAME": 40, "MATCHED_IP": 1, "NO_MATCH": 4,
    }
    assert all(asset["role"] is None for asset in assets)
