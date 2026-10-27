from copy import deepcopy

from collector.anomalies import collect_anomalies, duplicate_anomalies, match_anomalies
from collector.matching import consolidate_asset


def asset(name="alpha", status="running", ip="192.0.2.1"):
    return {"vm_id": "1", "vm_name": name, "fqdn": "", "ip_reported": ip,
            "status": status}


def ipam(dns="alpha.example", ip="192.0.2.1", status="active"):
    return {"dns_name": dns, "ip": ip, "status": status}


def codes(entries):
    return [entry["code"] for entry in entries]


def test_no_match_emits_only_no_match_without_ipam():
    result = consolidate_asset(asset(name="missing", ip="192.0.2.2"), [ipam()])
    entries = match_anomalies(result)
    assert (codes(entries), entries[0]["ipam_record"]) == (["NO_MATCH"], None)


def test_matched_ip_emits_code_even_without_dns_name():
    result = consolidate_asset(asset(), [ipam(dns="")])
    assert codes(match_anomalies(result)) == ["MATCHED_IP"]


def test_hostname_mismatch_only_when_matched_by_ip():
    result = consolidate_asset(asset(name="other"), [ipam(dns="alpha.example")])
    assert codes(match_anomalies(result)) == ["MATCHED_IP", "HOSTNAME_MISMATCH"]


def test_hostname_mismatch_ignores_normalized_equivalent_names():
    result = consolidate_asset(asset(name=" ALPHA.example ", ip="192.0.2.1"),
                               [ipam(dns="alpha.other.example")])
    assert "HOSTNAME_MISMATCH" not in codes(match_anomalies(result))


def test_hostname_mismatch_requires_two_present_names():
    result = consolidate_asset(asset(name=""), [ipam(dns="alpha")])
    assert codes(match_anomalies(result)) == ["MATCHED_IP"]


def test_matched_name_and_fqdn_never_emit_hostname_mismatch():
    name_result = consolidate_asset(asset(name="alpha"), [ipam(dns="alpha.other")])
    fqdn_asset = asset(name="other")
    fqdn_asset["fqdn"] = "alpha.example"
    fqdn_result = consolidate_asset(fqdn_asset, [ipam(dns="alpha.other")])
    assert (codes(match_anomalies(name_result)), codes(match_anomalies(fqdn_result))) == ([], [])


def test_stopped_vm_with_active_ipam_emits_status_mismatch():
    result = consolidate_asset(asset(status="stopped"), [ipam()])
    assert codes(match_anomalies(result)) == ["STATUS_MISMATCH"]


def test_status_mismatch_requires_stopped_and_active():
    results = [consolidate_asset(asset(status=vm), [ipam(status=record)])
               for vm, record in [("running", "active"), ("stopped", "reserved")]]
    assert [codes(match_anomalies(result)) for result in results] == [[], []]


def test_duplicate_dns_counts_normalized_names_including_orphans_once_per_group():
    records = [ipam(" ALPHA.example ", "192.0.2.1"),
               ipam("alpha.other", "192.0.2.2"), ipam("alpha", "192.0.2.3")]
    entries = duplicate_anomalies(records)
    assert [(entry["code"], entry["asset"], entry["ipam_record"], entry["description"])
            for entry in entries] == [("DUPLICATE_DNS", None, records[0],
                                       "3 IPAM records share dns_name: alpha")]


def test_duplicate_ip_counts_ipam_addresses_not_vm_addresses():
    records = [ipam("one", "192.0.2.9"), ipam("two", "192.0.2.9")]
    result = consolidate_asset(asset(name="one", ip="192.0.2.1"), records)
    entries = collect_anomalies([result], records)
    assert [(entry["code"], entry["description"]) for entry in entries] == [
        ("DUPLICATE_IP", "2 IPAM records share ip: 192.0.2.9")]


def test_identical_ipam_observations_coexist_and_raise_both_duplicate_codes():
    records = [ipam(), ipam()]
    entries = duplicate_anomalies(records)
    assert [(entry["code"], entry["ipam_record"]) for entry in entries] == [
        ("DUPLICATE_DNS", records[0]), ("DUPLICATE_IP", records[0])]


def test_empty_dns_and_ip_never_form_duplicate_groups():
    records = [ipam("", ""), ipam("", "")]
    assert duplicate_anomalies(records) == []


def test_second_pass_includes_unmatched_ipam_and_uses_one_shot_iterables():
    records = [ipam("orphan", "192.0.2.8"), ipam("orphan.example", "192.0.2.8")]
    result = consolidate_asset(asset(name="lost", ip="192.0.2.1"), records)
    entries = collect_anomalies(iter([result]), iter(records))
    assert codes(entries) == ["NO_MATCH", "DUPLICATE_DNS", "DUPLICATE_IP"]


def test_results_are_repeatable_and_detached_from_source_observations():
    records = [ipam(), ipam()]
    result = consolidate_asset(asset(name="other"), records)
    original = deepcopy((result, records))
    first = collect_anomalies([result], records)
    second = collect_anomalies([result], records)
    first[0]["asset"]["vm_name"] = "changed"
    first[-1]["ipam_record"]["dns_name"] = "changed"
    assert ((result, records), second) == (original, collect_anomalies([result], records))
