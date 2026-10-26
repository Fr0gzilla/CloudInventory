import pytest

from collector.normalization import normalize


@pytest.mark.parametrize("value,expected", [
    ("web-a500.prod.local", "web-a500"),
    ("  WEB-A500 .Prod.Local  ", "web-a500"),
    (None, ""),
    ("   ", ""),
])
def test_normalize_removes_case_spaces_and_domain_or_returns_empty(value, expected):
    assert normalize(value) == expected
