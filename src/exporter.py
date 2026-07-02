"""CSV export of the liker ranking.

This module is self-contained: it only depends on pandas and the standard
library so it can be unit-tested in isolation without any internal imports.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Exact column order. Kept as a module constant so the empty-ranking case and
# the populated case share the same source of truth.
_COLUMNS: list[str] = [
    "name",
    "profile_url",
    "headline",
    "likes_count",
    "comments_count",
    "repost_count",
    "repost_with_comment_count",
]

# Any run of whitespace, incl. newlines, tabs, NBSP (\xa0) and the Unicode line /
# paragraph separators (  /  ). In Python's Unicode mode ``\s`` already
# matches all of these, so a single collapse covers them.
_WHITESPACE_RE = re.compile(r"\s+")


def _sanitize(value: object) -> object:
    """Flatten a cell value to a single CSV-safe line.

    Strings have every whitespace run collapsed to one space and are stripped,
    so no field can contain an embedded newline (which would make a row span
    several visual lines). Non-string values (the integer counts) pass through
    unchanged.
    """
    if isinstance(value, str):
        return _WHITESPACE_RE.sub(" ", value).strip()
    return value


def export_csv(ranking: list[dict], output_path: str | Path) -> Path:
    """Write the engagement ranking to a CSV file and return the resolved path.

    The DataFrame always uses the exact column order ``["name", "profile_url",
    "headline", "likes_count", "comments_count", "repost_count",
    "repost_with_comment_count"]`` and is sorted by ``likes_count`` descending
    using a stable sort, preserving the input order of ties (the ranking is
    expected to already be tie-broken upstream, including the comments_count
    tiebreak).

    Parent directories are created as needed. The file is written with
    ``encoding="utf-8-sig"`` so Excel correctly displays accented characters,
    and without the pandas row index. An empty ranking produces a header-only
    CSV with the same columns.
    """
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if ranking:
        # Sanitize every cell up front (collapse embedded newlines etc.) so no
        # field can span multiple lines in the CSV.
        rows = [{key: _sanitize(val) for key, val in row.items()} for row in ranking]
        # Force column order explicitly; reindex tolerates dicts missing keys
        # by inserting NaN, but the contract guarantees these keys are present.
        frame = pd.DataFrame(rows).reindex(columns=_COLUMNS)
        frame = frame.sort_values(
            by="likes_count",
            ascending=False,
            kind="mergesort",  # stable sort to keep upstream tie ordering
        )
    else:
        # Header-only output for an empty ranking.
        frame = pd.DataFrame(columns=_COLUMNS)

    frame.to_csv(output, index=False, encoding="utf-8-sig")
    return output
