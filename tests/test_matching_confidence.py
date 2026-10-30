import pytest

from collector.matching import consolidate_asset


def matched(name, candidates, **asset_fields):
    asset = {"vm_name": name, "fqdn": "", "ip_reported": "", **asset_fields}
    return consolidate_asset(asset, ({"dns_name": candidate, "ip": ""} for candidate in candidates))


@pytest.mark.parametrize("field,status", [
    ("vm_name", "MATCHED_NAME"), ("fqdn", "MATCHED_FQDN"),
    ("ip_reported", "MATCHED_IP"),
])
def test_exact_matches_take_priority_and_score_100(field, status):
    asset = {"vm_name": "other", "fqdn": "", "ip_reported": "", field: "web-a500"}
    record = {"dns_name": "web-a500" if field != "ip_reported" else "unrelated",
              "ip": "web-a500" if field == "ip_reported" else ""}
    result = consolidate_asset(asset, iter([{"dns_name": "web-a500-old", "ip": ""}, record]))
    assert (result["match_status"], result["match_confidence"], result["match_doubt"],
            result["ipam_record"]) == (status, 100, False, record)


def test_web_a500_and_web_a500_old_are_doubtful_at_80():
    result = matched("web-a500", ["web-a500-old"])
    assert (result["match_status"], result["match_confidence"], result["match_doubt"]) == (
        "MATCHED_FUZZY", 80, True)


@pytest.mark.parametrize("name,candidate,score,status,doubt", [
    ("aaaa", "bbbb", 0, "NO_MATCH", False),
    ("a" * 69 + "b" * 31, "a" * 69 + "c" * 31, 69, "NO_MATCH", False),
    ("a" * 70 + "b" * 30, "a" * 70 + "c" * 30, 70, "MATCHED_FUZZY", True),
    ("a" * 89 + "b" * 11, "a" * 89 + "c" * 11, 89, "MATCHED_FUZZY", True),
    ("a" * 90 + "b" * 10, "a" * 90 + "c" * 10, 90, "MATCHED_FUZZY", False),
])
def test_confidence_boundaries(name, candidate, score, status, doubt):
    result = matched(name, [candidate])
    assert (result["match_confidence"], result["match_status"], result["match_doubt"]) == (
        score, status, doubt)


def test_confidence_rounds_half_up():
    result = matched("a" * 79 + "b" * 41, ["a" * 79 + "c" * 41])
    assert result["match_confidence"] == 66


def test_tied_candidates_remain_unmatched_but_doubtful():
    result = matched("web-a500", ["web-a500-old", "web-a500-new"])
    assert (result["match_status"], result["ipam_record"], result["match_confidence"],
            result["match_doubt"]) == ("NO_MATCH", None, 80, True)


def test_missing_comparable_names_have_no_fictitious_score():
    result = matched(None, ["web-a500"])
    assert (result["match_status"], result["match_confidence"], result["match_doubt"]) == (
        "NO_MATCH", None, False)
