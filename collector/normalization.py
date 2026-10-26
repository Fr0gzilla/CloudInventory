"""Name normalization for matching observations across sources."""

import re


def normalize(value: str | None) -> str:
    """Ignore case, whitespace and the domain suffix of a DNS name."""
    return re.sub(r"\s+", "", value or "").lower().split(".", 1)[0]
