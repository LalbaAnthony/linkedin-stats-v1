"""Unit tests for the pure parsing helpers in :mod:`src.parser`.

These tests are deterministic and require no network or browser. They pin the
behavior expected by the integration contract: FR/EN relative-date parsing,
profile-URL normalization, inclusive date-range checks, activity-URL building
and record construction.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.models import (
    KIND_COMMENT,
    KIND_LIKE,
    KIND_REPOST,
    KIND_REPOST_COMMENT,
    EngagementRecord,
    PostRef,
    RawPerson,
)
from src.parser import (
    activity_url,
    author_kind,
    build_records,
    clean_name,
    commenter_name_from_aria,
    in_range,
    is_safe_reposts_opener,
    normalize_profile_url,
    parse_relative_date,
    reactor_headline_from_aria,
    reactor_name_from_aria,
    urn_from_post_url,
)

# Fixed reference instant so every relative-date assertion is deterministic.
REFERENCE = datetime(2024, 6, 15, 12, 0, 0)


# --------------------------------------------------------------------------- #
# parse_relative_date
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text",
    ["now", "just now", "Just Now", "à l'instant", "maintenant", "  now  "],
)
def test_parse_relative_date_now_returns_reference(text: str) -> None:
    assert parse_relative_date(text, REFERENCE) == REFERENCE


def test_parse_relative_date_seconds() -> None:
    # Seconds collapse to the reference instant (sub-minute precision unused).
    result = parse_relative_date("30s", REFERENCE)
    assert result == REFERENCE


def test_parse_relative_date_minutes_en() -> None:
    assert parse_relative_date("5m", REFERENCE) == REFERENCE - timedelta(minutes=5)
    assert parse_relative_date("5 min", REFERENCE) == REFERENCE - timedelta(minutes=5)


def test_parse_relative_date_minutes_fr() -> None:
    assert parse_relative_date("5 mn", REFERENCE) == REFERENCE - timedelta(minutes=5)


def test_parse_relative_date_hours() -> None:
    assert parse_relative_date("2h", REFERENCE) == REFERENCE - timedelta(hours=2)
    assert parse_relative_date("2 hr", REFERENCE) == REFERENCE - timedelta(hours=2)


def test_parse_relative_date_hours_fr() -> None:
    assert parse_relative_date("2 heures", REFERENCE) == REFERENCE - timedelta(hours=2)


def test_parse_relative_date_days_en() -> None:
    assert parse_relative_date("5 d", REFERENCE) == REFERENCE - timedelta(days=5)


def test_parse_relative_date_days_fr() -> None:
    assert parse_relative_date("il y a 3 j", REFERENCE) == REFERENCE - timedelta(days=3)
    assert parse_relative_date("3 jours", REFERENCE) == REFERENCE - timedelta(days=3)


def test_parse_relative_date_weeks_en() -> None:
    assert parse_relative_date("3w", REFERENCE) == REFERENCE - timedelta(weeks=3)
    assert parse_relative_date("3 wk", REFERENCE) == REFERENCE - timedelta(weeks=3)


def test_parse_relative_date_weeks_fr() -> None:
    # "2 sem." with trailing punctuation, and "il y a 3 sem.".
    assert parse_relative_date("2 sem.", REFERENCE) == REFERENCE - timedelta(weeks=2)
    assert parse_relative_date("il y a 3 sem.", REFERENCE) == REFERENCE - timedelta(
        weeks=3
    )
    assert parse_relative_date("2 semaines", REFERENCE) == REFERENCE - timedelta(weeks=2)


def test_parse_relative_date_months_en() -> None:
    # months approximate to 30 days.
    assert parse_relative_date("2mo", REFERENCE) == REFERENCE - timedelta(days=60)
    assert parse_relative_date("2 months ago", REFERENCE) == REFERENCE - timedelta(
        days=60
    )


def test_parse_relative_date_months_fr() -> None:
    assert parse_relative_date("il y a 2 mois", REFERENCE) == REFERENCE - timedelta(
        days=60
    )


def test_parse_relative_date_years_en() -> None:
    # years approximate to 365 days.
    assert parse_relative_date("1 yr", REFERENCE) == REFERENCE - timedelta(days=365)
    assert parse_relative_date("1yr", REFERENCE) == REFERENCE - timedelta(days=365)


def test_parse_relative_date_years_fr() -> None:
    assert parse_relative_date("1 an", REFERENCE) == REFERENCE - timedelta(days=365)
    assert parse_relative_date("2 ans", REFERENCE) == REFERENCE - timedelta(days=730)


def test_parse_relative_date_month_vs_minute_distinction() -> None:
    """The critical ambiguity: "mo"/"mois" must be month, "m" must be minute."""
    month = parse_relative_date("2mo", REFERENCE)
    minute = parse_relative_date("2m", REFERENCE)
    assert month == REFERENCE - timedelta(days=60)
    assert minute == REFERENCE - timedelta(minutes=2)
    assert month != minute


def test_parse_relative_date_result_not_in_future() -> None:
    for text in ["2mo", "1 yr", "3w", "5 d", "5m", "il y a 2 mois"]:
        result = parse_relative_date(text, REFERENCE)
        assert result is not None
        assert result <= REFERENCE


@pytest.mark.parametrize("text", ["", "   ", "yesterday", "gibberish", "lundi"])
def test_parse_relative_date_unparseable_returns_none(text: str) -> None:
    assert parse_relative_date(text, REFERENCE) is None


# --------------------------------------------------------------------------- #
# clean_name
# --------------------------------------------------------------------------- #
def test_clean_name_collapses_whitespace() -> None:
    assert clean_name("  John   Doe  ") == "John Doe"


def test_clean_name_strips_connection_degree() -> None:
    assert clean_name("John Doe • 1st") == "John Doe"
    assert clean_name("Jane Martin • 2e") == "Jane Martin"


def test_clean_name_strips_view_profile_prefix() -> None:
    assert clean_name("View John Doe's profile") == "John Doe"


def test_clean_name_handles_empty() -> None:
    assert clean_name("") == ""
    assert clean_name("   ") == ""


# --------------------------------------------------------------------------- #
# reactor_name_from_aria
# --------------------------------------------------------------------------- #
def test_reactor_name_from_aria_fr() -> None:
    assert reactor_name_from_aria("Anthony LALBA a réagi avec J’aime") == "Anthony LALBA"
    assert reactor_name_from_aria("Solène Martin a réagi avec Bravo") == "Solène Martin"


def test_reactor_name_from_aria_en() -> None:
    assert reactor_name_from_aria("Jane Doe reacted with Like") == "Jane Doe"


def test_reactor_name_from_aria_plain_and_empty() -> None:
    # No reaction suffix -> treated as a plain name.
    assert reactor_name_from_aria("Jane Doe") == "Jane Doe"
    assert reactor_name_from_aria("") == ""


def test_reactor_name_from_aria_modal_with_headline_suffix() -> None:
    aria = (
        "Auriane Hingrez a réagi avec J’aime, Relation de 1er niveau, "
        "Chargée de Communication nationale chez AgoraVita"
    )
    assert reactor_name_from_aria(aria) == "Auriane Hingrez"


def test_reactor_name_from_aria_with_embedded_newlines() -> None:
    # LinkedIn labels can contain newlines; the name must still be isolated and
    # not collapse into the whole sentence (regression).
    aria = (
        "Simon EMEURY a réagi avec J’aime,\nRelation de 2e niveau,\n"
        "Expert Infrastructures Réseau @D-Link | réseaux performants"
    )
    assert reactor_name_from_aria(aria) == "Simon EMEURY"
    assert (
        reactor_headline_from_aria(aria)
        == "Expert Infrastructures Réseau @D-Link | réseaux performants"
    )


# --------------------------------------------------------------------------- #
# reactor_headline_from_aria
# --------------------------------------------------------------------------- #
def test_reactor_headline_from_aria_modal_fr() -> None:
    aria = (
        "Auriane Hingrez a réagi avec J’aime, Relation de 1er niveau, "
        "Chargée de Communication nationale chez AgoraVita"
    )
    assert (
        reactor_headline_from_aria(aria)
        == "Chargée de Communication nationale chez AgoraVita"
    )


def test_reactor_headline_from_aria_en() -> None:
    aria = "Jane Doe reacted with Like, 1st degree connection · Software Engineer"
    assert reactor_headline_from_aria(aria) == "Software Engineer"


def test_reactor_headline_from_aria_facepile_has_none() -> None:
    # Facepile labels carry only name + reaction type — no headline.
    assert reactor_headline_from_aria("Anthony LALBA a réagi avec J’aime") == ""
    assert reactor_headline_from_aria("") == ""


# --------------------------------------------------------------------------- #
# normalize_profile_url
# --------------------------------------------------------------------------- #
def test_normalize_profile_url_strips_query_and_fragment() -> None:
    result = normalize_profile_url(
        "https://www.linkedin.com/in/john-doe/?trk=abc#section"
    )
    assert result == "https://www.linkedin.com/in/john-doe/"


def test_normalize_profile_url_adds_https_prefix_for_relative() -> None:
    result = normalize_profile_url("/in/john-doe")
    assert result == "https://www.linkedin.com/in/john-doe/"


def test_normalize_profile_url_adds_trailing_slash() -> None:
    result = normalize_profile_url("https://www.linkedin.com/in/jane-martin")
    assert result == "https://www.linkedin.com/in/jane-martin/"


def test_normalize_profile_url_non_profile_returns_empty() -> None:
    assert normalize_profile_url("https://www.linkedin.com/company/acme/") == ""
    assert normalize_profile_url("") == ""
    assert normalize_profile_url("https://example.com/foo") == ""


# --------------------------------------------------------------------------- #
# in_range
# --------------------------------------------------------------------------- #
def test_in_range_inclusive_start_and_end_day() -> None:
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 31)
    # Boundary dates are inclusive.
    assert in_range(datetime(2024, 1, 1, 0, 0), start, end) is True
    assert in_range(datetime(2024, 1, 31, 23, 59), start, end) is True
    # Time-of-day on the end day must still count (date-level comparison).
    assert in_range(datetime(2024, 1, 15, 9, 30), start, end) is True


def test_in_range_outside_returns_false() -> None:
    start = datetime(2024, 1, 1)
    end = datetime(2024, 1, 31)
    assert in_range(datetime(2023, 12, 31, 23, 59), start, end) is False
    assert in_range(datetime(2024, 2, 1, 0, 0), start, end) is False


def test_in_range_end_day_with_time_component_on_bounds() -> None:
    # End bound provided with a time component; whole end day is still inclusive.
    start = datetime(2024, 3, 10, 8, 0)
    end = datetime(2024, 3, 10, 8, 0)
    assert in_range(datetime(2024, 3, 10, 23, 59), start, end) is True
    assert in_range(datetime(2024, 3, 10, 0, 0), start, end) is True


# --------------------------------------------------------------------------- #
# activity_url
# --------------------------------------------------------------------------- #
def test_activity_url_from_clean_profile() -> None:
    assert (
        activity_url("https://www.linkedin.com/in/john-doe/")
        == "https://www.linkedin.com/in/john-doe/recent-activity/all/"
    )


def test_activity_url_from_profile_without_trailing_slash() -> None:
    assert (
        activity_url("https://www.linkedin.com/in/john-doe")
        == "https://www.linkedin.com/in/john-doe/recent-activity/all/"
    )


def test_activity_url_strips_existing_segments_and_query() -> None:
    assert (
        activity_url("https://www.linkedin.com/in/john-doe/details/skills/?foo=bar")
        == "https://www.linkedin.com/in/john-doe/recent-activity/all/"
    )


def test_activity_url_company_page() -> None:
    assert (
        activity_url("https://www.linkedin.com/company/agoravita")
        == "https://www.linkedin.com/company/agoravita/posts/"
    )
    assert (
        activity_url("https://www.linkedin.com/company/agoravita/about/?foo=1")
        == "https://www.linkedin.com/company/agoravita/posts/"
    )


def test_activity_url_unsupported_returns_empty() -> None:
    assert activity_url("https://www.linkedin.com/feed/") == ""
    assert activity_url("") == ""


def test_author_kind_classifies_company_profile_and_unknown() -> None:
    assert author_kind("https://www.linkedin.com/company/agoravita") == "company"
    assert author_kind("https://www.linkedin.com/company/agoravita/about/") == "company"
    assert author_kind("https://www.linkedin.com/in/john-doe/") == "profile"
    assert author_kind("https://www.linkedin.com/in/john-doe") == "profile"
    assert author_kind("https://www.linkedin.com/feed/") is None
    assert author_kind("") is None


# --------------------------------------------------------------------------- #
# build_records
# --------------------------------------------------------------------------- #
def _post() -> PostRef:
    return PostRef(
        post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
        raw_date="2mo",
        post_date=datetime(2024, 4, 15, 10, 30),
    )


def test_build_records_formats_date_iso_and_maps_fields() -> None:
    post = _post()
    people = [
        RawPerson(
            name="John Doe",
            profile_url="https://www.linkedin.com/in/john-doe/",
            headline="CTO at Acme",
        )
    ]
    records = build_records(post, people, KIND_LIKE)
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, EngagementRecord)
    assert record.kind == KIND_LIKE
    assert record.post_url == post.post_url
    assert record.post_date == "2024-04-15"
    assert record.person_name == "John Doe"
    assert record.person_profile == "https://www.linkedin.com/in/john-doe/"
    assert record.person_headline == "CTO at Acme"


@pytest.mark.parametrize("kind", [KIND_COMMENT, KIND_REPOST, KIND_REPOST_COMMENT])
def test_build_records_tags_kind(kind: str) -> None:
    people = [
        RawPerson(
            name="Jane Martin",
            profile_url="https://www.linkedin.com/in/jane-martin/",
            headline="",
        )
    ]
    records = build_records(_post(), people, kind)
    assert [r.kind for r in records] == [kind]


def test_build_records_skips_empty_profile() -> None:
    post = _post()
    people = [
        RawPerson(name="No Profile", profile_url="", headline="ghost"),
        RawPerson(
            name="Jane Martin",
            profile_url="https://www.linkedin.com/in/jane-martin/",
            headline="",
        ),
    ]
    records = build_records(post, people, KIND_LIKE)
    assert len(records) == 1
    assert records[0].person_profile == "https://www.linkedin.com/in/jane-martin/"


def test_build_records_empty_people_returns_empty() -> None:
    assert build_records(_post(), [], KIND_LIKE) == []


# --------------------------------------------------------------------------- #
# commenter_name_from_aria
# --------------------------------------------------------------------------- #
def test_commenter_name_from_aria_fr() -> None:
    assert (
        commenter_name_from_aria(
            "Voir plus d'options pour le commentaire de Vanessa Clément."
        )
        == "Vanessa Clément"
    )


def test_commenter_name_from_aria_en_possessive() -> None:
    assert commenter_name_from_aria("More options for Jane Doe's comment") == "Jane Doe"


def test_commenter_name_from_aria_non_comment_returns_empty() -> None:
    assert commenter_name_from_aria("Commenter") == ""
    assert commenter_name_from_aria("") == ""


# --------------------------------------------------------------------------- #
# urn_from_post_url
# --------------------------------------------------------------------------- #
def test_urn_from_post_url_extracts_urn() -> None:
    assert (
        urn_from_post_url(
            "https://www.linkedin.com/feed/update/urn:li:activity:7452249817411489792/"
        )
        == "urn:li:activity:7452249817411489792"
    )


def test_urn_from_post_url_strips_query_and_fragment() -> None:
    assert (
        urn_from_post_url(
            "https://www.linkedin.com/feed/update/urn:li:activity:42/?utm=x#c"
        )
        == "urn:li:activity:42"
    )


def test_urn_from_post_url_non_update_returns_empty() -> None:
    assert urn_from_post_url("https://example.com/foo") == ""
    assert urn_from_post_url("") == ""


# --------------------------------------------------------------------------- #
# is_safe_reposts_opener (SAFETY-CRITICAL: must never green-light the composer)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "label",
    [
        "",
        "Republier",
        "Repost",
        "Reposter",
        "Partager",
        "Share",
        # Composer button carrying a COUNT must STILL be rejected (the bug the
        # review caught: a digit must not green-light the composer verb).
        "Republier 14",
        "Republier · 14",
        "Repost 14",
        "Repost — 14 reposts",
        # A bare count with no list noun is not positive evidence of a list.
        "14",
        # An unrelated label.
        "Voir toutes les réactions",
    ],
)
def test_is_safe_reposts_opener_rejects_composer_and_ambiguous(label: str) -> None:
    assert is_safe_reposts_opener(label) is False


@pytest.mark.parametrize(
    "label",
    [
        "14 republications",
        "1 republication",
        "Voir les 14 republications",
        "12 reposts",
        "3 partages",
        "Afficher les republications",
    ],
)
def test_is_safe_reposts_opener_accepts_list_openers(label: str) -> None:
    assert is_safe_reposts_opener(label) is True
