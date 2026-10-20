"""Read owner-managed title alternatives as private, read-only runtime input.

Only encoded employment-title *values* for seed-matched roles may enter model
instructions. Invalid or missing configuration never widens the save guard.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BASENAME = "role-titles.yaml"
# TeX metacharacters other than the explicitly encoded ampersand are not title
# text. Do not accept commands, group delimiters, comments or math syntax.
_PUNCTUATION = frozenset(" -.,/'():+;!?&")


def literal_title(value: object) -> str | None:
    """Return safely encoded literal title text, or reject the whole value."""
    if not isinstance(value, str) or not value or len(value) > 160:
        return None
    if value != value.strip() or not any(ch.isalnum() for ch in value):
        return None
    if any(not (ch.isalnum() or ch in _PUNCTUATION) for ch in value):
        return None
    return value.replace("&", r"\&")


def load_approved_titles(local_dir: str | Path) -> dict[str, tuple[str, ...]]:
    """Load exact employer-keyed options; invalid input fails closed as empty.

    A company key is a candidate scope, not an identity for a particular job
    under that company. Unique seed-anchor matching is the validator's job.
    """
    path = Path(local_dir) / BASENAME
    try:
        # Avoid accidentally loading an unbounded local document as a prompt
        # input. No file bytes, diagnostics or filesystem paths go to an agent.
        if path.stat().st_size > 65536:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        return {}

    approved = {}
    for employer, values in data.items():
        if (not isinstance(employer, str) or not employer
                or employer != employer.strip()
                or any(c in employer for c in "\n\r\t")
                or not isinstance(values, list)):
            return {}
        encoded = []
        for value in values:
            title = literal_title(value)
            if title is None:
                return {}
            if title not in encoded:
                encoded.append(title)
        approved[employer] = tuple(encoded)
    return approved
