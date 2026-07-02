"""Unit tests for :func:`src.exporter.export_csv`.

Pandas is an optional dependency at test time, so the whole module is skipped
when it is unavailable. Tests write to a temporary path and read the file back
to assert column order, sorting, UTF-8 BOM encoding and the empty-ranking case.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pandas = pytest.importorskip("pandas")

from src.exporter import export_csv  # noqa: E402  (import after importorskip)

EXPECTED_COLUMNS = [
    "name",
    "profile_url",
    "headline",
    "likes_count",
    "comments_count",
    "repost_count",
    "repost_with_comment_count",
]


def test_export_csv_columns_and_sort(tmp_path: Path) -> None:
    ranking = [
        {
            "name": "Bob",
            "profile_url": "https://www.linkedin.com/in/bob/",
            "headline": "Manager",
            "likes_count": 5,
            "comments_count": 0,
            "repost_count": 1,
            "repost_with_comment_count": 2,
        },
        {
            "name": "Alice",
            "profile_url": "https://www.linkedin.com/in/alice/",
            "headline": "Engineer",
            "likes_count": 1,
            "comments_count": 4,
            "repost_count": 0,
            "repost_with_comment_count": 0,
        },
    ]
    out = tmp_path / "results.csv"
    result_path = export_csv(ranking, out)

    assert isinstance(result_path, Path)
    assert result_path == out.resolve()
    assert result_path.exists()

    df = pandas.read_csv(result_path)
    assert list(df.columns) == EXPECTED_COLUMNS
    # likes_count DESC: Bob (5) before Alice (1); the other columns ride along.
    assert list(df["likes_count"]) == [5, 1]
    assert list(df["comments_count"]) == [0, 4]
    assert list(df["repost_count"]) == [1, 0]
    assert list(df["repost_with_comment_count"]) == [2, 0]
    assert list(df["name"]) == ["Bob", "Alice"]


def test_export_csv_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "deep" / "results.csv"
    ranking = [
        {
            "name": "Alice",
            "profile_url": "https://www.linkedin.com/in/alice/",
            "headline": "Engineer",
            "likes_count": 3,
            "comments_count": 1,
            "repost_count": 0,
            "repost_with_comment_count": 0,
        }
    ]
    result_path = export_csv(ranking, out)
    assert result_path.exists()
    assert result_path.parent.is_dir()


def test_export_csv_utf8_sig_encoding(tmp_path: Path) -> None:
    ranking = [
        {
            "name": "Élodie Dupré",
            "profile_url": "https://www.linkedin.com/in/elodie/",
            "headline": "Développeuse",
            "likes_count": 2,
            "comments_count": 0,
            "repost_count": 1,
            "repost_with_comment_count": 0,
        }
    ]
    out = tmp_path / "accents.csv"
    result_path = export_csv(ranking, out)

    raw = result_path.read_bytes()
    # utf-8-sig writes a BOM at the start (Excel-friendly for accented chars).
    assert raw.startswith(b"\xef\xbb\xbf")

    # Reading back with utf-8-sig must round-trip the accented values.
    df = pandas.read_csv(result_path, encoding="utf-8-sig")
    assert df.iloc[0]["name"] == "Élodie Dupré"
    assert df.iloc[0]["headline"] == "Développeuse"


def test_export_csv_sanitizes_embedded_whitespace(tmp_path: Path) -> None:
    ranking = [
        {
            "name": "Thomas\nBeuriot",
            "profile_url": "https://www.linkedin.com/in/thomas/",
            # Embedded newline + tab + non-breaking space must be flattened.
            "headline": "Directeur Agence\nchez BPM\tAGENCY | immersive",
            "likes_count": 2,
            "comments_count": 0,
            "repost_count": 0,
            "repost_with_comment_count": 0,
        }
    ]
    out = tmp_path / "ws.csv"
    export_csv(ranking, out)

    text = out.read_text(encoding="utf-8-sig")
    # Exactly one data row -> exactly two physical lines (header + row).
    assert len([line for line in text.splitlines() if line.strip()]) == 2

    df = pandas.read_csv(out)
    row = df.iloc[0]
    assert "\n" not in row["name"] and row["name"] == "Thomas Beuriot"
    assert "\n" not in row["headline"] and "\t" not in row["headline"]
    assert row["headline"] == "Directeur Agence chez BPM AGENCY | immersive"


def test_export_csv_empty_ranking_writes_header_only(tmp_path: Path) -> None:
    out = tmp_path / "empty.csv"
    result_path = export_csv([], out)
    assert result_path.exists()

    df = pandas.read_csv(result_path)
    assert list(df.columns) == EXPECTED_COLUMNS
    assert len(df) == 0


def test_export_csv_accepts_str_path(tmp_path: Path) -> None:
    out = tmp_path / "from_str.csv"
    ranking = [
        {
            "name": "Alice",
            "profile_url": "https://www.linkedin.com/in/alice/",
            "headline": "Engineer",
            "likes_count": 1,
            "comments_count": 2,
            "repost_count": 3,
            "repost_with_comment_count": 1,
        }
    ]
    result_path = export_csv(ranking, str(out))
    assert isinstance(result_path, Path)
    assert result_path.exists()
