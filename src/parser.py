"""Pure parsing/transformation helpers for LinkedIn data.

This module contains ONLY pure functions (no Playwright, no I/O, no network).
It is the most heavily unit-tested part of the project, so everything here must
stay deterministic and side-effect free.

Important domain note: LinkedIn never exposes exact post timestamps, only
RELATIVE labels (e.g. "2mo", "il y a 3 sem."). Date resolution is therefore
APPROXIMATE by construction (months are treated as 30 days, years as 365 days).
The user's LinkedIn UI may be in FRENCH or ENGLISH, so both languages are
handled here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

from src.models import EngagementRecord, PostRef, RawPerson

# Canonical LinkedIn profile host used to absolutize and normalize URLs.
_LINKEDIN_BASE = "https://www.linkedin.com"

# Phrases meaning "right now" across EN and FR. Matched on the lowercased,
# accent-preserving text after trimming surrounding noise.
_NOW_TOKENS = frozenset(
    {
        "now",
        "just now",
        "a l'instant",
        "à l'instant",
        "maintenant",
    }
)

# Number + unit relative-date matcher.
#
# Ordering of the unit alternation is CRITICAL: the month tokens ("mo", "mois",
# "mth") MUST come before the minute tokens ("m", "min", "mn"), otherwise "2mo"
# would greedily match the minute unit "m" and leave a dangling "o". Years are
# also placed before minutes for the same reason ("an"/"ans" vs "m").
#
# Each alternative is anchored so it does not consume a following letter that
# belongs to a different word. Trailing dots ("sem.") are tolerated.
_RELATIVE_RE = re.compile(
    r"""
    (?P<value>\d+)            # numeric amount
    \s*
    (?P<unit>
        months? | mois | mth | mo               # MONTH  (before minute!)
      | années? | annees? | ans? | years? | yrs? | yr | y   # YEAR (before minute!)
      | semaines? | weeks? | sem | wks? | wk | w   # WEEK
      | jours? | days? | j | d                    # DAY
      | heures? | hours? | hrs? | hr | h          # HOUR
      | minutes? | min | mn | m                    # MINUTE
      | secondes? | seconds? | secs? | sec | s     # SECOND
    )
    \.?                       # optional trailing dot ("sem.")
    """,
    re.IGNORECASE | re.VERBOSE,
)


def parse_relative_date(text: str, reference: datetime) -> datetime | None:
    """Resolve a LinkedIn relative-time label to an approximate datetime.

    The returned datetime is always <= ``reference`` (the label describes time
    elapsed in the past). Handles English and French, compact LinkedIn forms
    ("2mo", "3w"), spaced forms ("5 d"), "ago"/"il y a" wrappers and trailing
    punctuation ("2 sem.").

    Approximations: month -> 30 days, year -> 365 days.

    Returns ``None`` when the text cannot be parsed.
    """
    if not text:
        return None

    normalized = text.strip().lower()
    if not normalized:
        return None

    # Strip the directional wrappers so the numeric matcher only sees the core.
    # "il y a 2 mois" -> "2 mois", "2 months ago" -> "2 months".
    stripped = re.sub(r"\bil\s+y\s+a\b", " ", normalized)
    stripped = re.sub(r"\bago\b", " ", stripped)
    stripped = stripped.strip(" \t••·-")

    # Immediate-present tokens map to the reference instant.
    if stripped in _NOW_TOKENS or normalized in _NOW_TOKENS:
        return reference

    match = _RELATIVE_RE.search(stripped)
    if match is None:
        return None

    try:
        value = int(match.group("value"))
    except ValueError:
        return None

    unit = match.group("unit").lower().rstrip(".")
    delta = _unit_to_timedelta(value, unit)
    if delta is None:
        return None

    return reference - delta


def _unit_to_timedelta(value: int, unit: str) -> timedelta | None:
    """Map a (value, unit) pair to a timedelta. Returns None for unknown units."""
    # Months and years first to mirror the regex ordering and keep intent clear.
    if unit in {"mo", "mois", "mth", "month", "months"}:
        return timedelta(days=value * 30)
    if unit in {
        "y",
        "yr",
        "yrs",
        "year",
        "years",
        "an",
        "ans",
        "annee",
        "annees",
        "année",
        "années",
    }:
        return timedelta(days=value * 365)
    if unit in {"w", "wk", "wks", "week", "weeks", "sem", "semaine", "semaines"}:
        return timedelta(weeks=value)
    if unit in {"d", "day", "days", "j", "jour", "jours"}:
        return timedelta(days=value)
    if unit in {"h", "hr", "hrs", "hour", "hours", "heure", "heures"}:
        return timedelta(hours=value)
    if unit in {"m", "min", "mn", "minute", "minutes"}:
        return timedelta(minutes=value)
    if unit in {"s", "sec", "secs", "seconde", "secondes", "second", "seconds"}:
        # Seconds-scale labels collapse to the reference instant: sub-minute
        # precision is irrelevant for date-range filtering.
        return timedelta(0)
    return None


# Trailing connection-degree artifacts LinkedIn appends to reactor names, e.g.
# "Jane Doe • 1st", "Jean Dupont • 2e". Matches a bullet/separator followed by
# a degree token in EN ("1st", "2nd", "3rd") or FR ("1er", "2e", "3e").
_DEGREE_SUFFIX_RE = re.compile(
    r"\s*[•·\-–|]\s*\d+(?:st|nd|rd|th|er|ere|ère|e|eme|ème)?\s*$",
    re.IGNORECASE,
)

# Accessible labels LinkedIn injects around a reactor name. The name is EMBEDDED
# in the label, so we CAPTURE it rather than strip it:
#   EN: "View Jane Doe's profile"        -> "Jane Doe"
#   FR: "Voir le profil de Jean Dupont"  -> "Jean Dupont"
# The EN form is anchored to the first "'s profile" so trailing duplicates are
# ignored; the FR form is a pure prefix with the name trailing.
_VIEW_PROFILE_EN_RE = re.compile(
    r"^\s*view\s+(?P<name>.+?)(?:’s|'s)\s+profile\b",
    re.IGNORECASE,
)
_VIEW_PROFILE_FR_RE = re.compile(
    r"^\s*voir\s+le\s+profil\s+de\s+",
    re.IGNORECASE,
)

# LinkedIn reactor accessible labels: "<Name> a réagi avec <Type>[, …]" (FR) /
# "<Name> reacted with <Type>…" (EN). The reactor's name is everything BEFORE
# the reaction verb. We match ONLY the verb (not "…verb.*$"): these labels can
# contain embedded newlines, and a ".*$" tail would fail to match past a "\n"
# (default dot, non-multiline), leaving the whole sentence as the "name".
_REACTED_VERB_RE = re.compile(
    r"\s+(?:a\s+r[ée]agi\s+avec|reacted\s+with)\b",
    re.IGNORECASE,
)


def clean_name(text: str) -> str:
    """Normalize a reactor display name.

    Collapses whitespace, extracts the name from "View X's profile" /
    "Voir le profil de X" accessible labels, removes trailing connection-degree
    artifacts ("• 1st", "• 2e") and de-duplicates the common case where
    LinkedIn renders the same name twice back-to-back.
    """
    if not text:
        return ""

    # Collapse all whitespace (including newlines) into single spaces.
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""

    en_match = _VIEW_PROFILE_EN_RE.match(cleaned)
    if en_match:
        cleaned = en_match.group("name").strip()
    else:
        # FR label is a leading prefix; the name follows it.
        cleaned = _VIEW_PROFILE_FR_RE.sub("", cleaned).strip()

    cleaned = _DEGREE_SUFFIX_RE.sub("", cleaned).strip()
    cleaned = _dedupe_repeated_name(cleaned)
    return cleaned.strip()


# Reaction verb + type prefix inside a reactor aria-label, up to the first
# comma ("a réagi avec J'aime" / "reacted with Like"). Used to locate where the
# headline tail begins.
_REACTION_PREFIX_RE = re.compile(
    r"(?:a\s+r[ée]agi\s+avec|reacted\s+with)\s+[^,]+",
    re.IGNORECASE,
)

# A leading connection-degree descriptor in the headline tail ("Relation de 1er
# niveau" / "1st degree connection"). Dropped so only the headline remains.
_CONNECTION_DEGREE_RE = re.compile(
    r"\bniveau\b|\bdegree\b|relation\s+de", re.IGNORECASE
)


def reactor_headline_from_aria(aria_label: str) -> str:
    """Extract the professional headline from a reactor aria-label.

    Modal reactor labels look like
    "<Name> a réagi avec <Type>, Relation de 1er niveau, <Headline>" (FR) or
    "<Name> reacted with <Type>, 1st degree connection · <Headline>" (EN). The
    name+reaction prefix is dropped, then a leading connection-degree clause, and
    the remainder is the headline. Returns "" when no headline is present (e.g.
    facepile labels, which carry only the name + reaction type).
    """
    if not aria_label:
        return ""
    prefix = _REACTION_PREFIX_RE.search(aria_label)
    if prefix is None:
        return ""
    tail = aria_label[prefix.end():].strip(" ,·•")
    parts = [p.strip() for p in re.split(r"[,·•]", tail) if p.strip()]
    if parts and _CONNECTION_DEGREE_RE.search(parts[0]):
        parts = parts[1:]
    return ", ".join(parts).strip()


# Comment "more options" buttons carry the author's name in their accessible
# label: "…commentaire de <Name>" (FR) / "…comment by <Name>" (EN) or the
# possessive "<Name>'s comment". Used to count comments and name their authors.
_COMMENT_AUTHOR_RE = re.compile(
    r"(?:commentaire\s+de|comment\s+(?:by|from)|commentaire\s+par)\s+(?P<name>.+?)\s*\.?\s*$",
    re.IGNORECASE,
)
_COMMENT_AUTHOR_POSSESSIVE_RE = re.compile(
    r"\bfor\s+(?P<name>.+?)(?:’s|'s)\s+comment\b",
    re.IGNORECASE,
)


def commenter_name_from_aria(aria_label: str) -> str:
    """Extract a commenter's display name from a comment-options aria-label.

    Handles "Voir plus d'options pour le commentaire de Jean Dupont" (FR),
    "More options for Jane Doe's comment" and "comment by Jane Doe" (EN).
    Returns "" when the label does not look like a comment-author label.
    """
    if not aria_label:
        return ""
    match = _COMMENT_AUTHOR_RE.search(aria_label)
    if match is None:
        match = _COMMENT_AUTHOR_POSSESSIVE_RE.search(aria_label)
    if match is None:
        return ""
    return clean_name(match.group("name"))


def reactor_name_from_aria(aria_label: str) -> str:
    """Extract a reactor's display name from a reaction accessible label.

    Handles LinkedIn's facepile/reactor labels in both languages:
    "Anthony LALBA a réagi avec J'aime" / "Anthony LALBA reacted with Like"
    -> "Anthony LALBA". When the label has no reaction suffix it is treated as a
    plain name and just cleaned. Returns "" for an empty label.
    """
    if not aria_label:
        return ""
    match = _REACTED_VERB_RE.search(aria_label)
    base = aria_label[: match.start()] if match is not None else aria_label
    return clean_name(base)


def _dedupe_repeated_name(text: str) -> str:
    """Collapse an exact "Name Name" duplication into a single "Name".

    LinkedIn frequently emits the visible name and the screen-reader name
    concatenated (e.g. "Jane Doe Jane Doe"). Only collapse when the string is
    exactly two identical halves AND each half is itself multi-word, so genuine
    single-token repeated names ("Li Li", "Anna Anna") are never halved.
    """
    if not text:
        return text
    # Even-length token split: compare first half against second half.
    if " " in text:
        half = text[: len(text) // 2].strip()
        other = text[len(text) // 2 :].strip()
        if half and half == other and " " in half:
            return half
    return text


def normalize_profile_url(href: str) -> str:
    """Normalize a LinkedIn profile URL.

    - Strips query string and fragment.
    - Forces an absolute ``https://www.linkedin.com`` prefix.
    - Ensures a trailing slash on ``/in/<id>/``.

    Returns ``""`` when the href is not a profile link.
    """
    if not href:
        return ""

    candidate = href.strip()
    # Drop query and fragment.
    candidate = candidate.split("?", 1)[0].split("#", 1)[0]

    # Locate the "/in/<id>" segment; everything before it is host noise.
    match = re.search(r"/in/([^/?#]+)", candidate)
    if match is None:
        return ""

    profile_id = match.group(1).strip()
    if not profile_id:
        return ""

    return f"{_LINKEDIN_BASE}/in/{profile_id}/"


def in_range(d: datetime, start: datetime, end: datetime) -> bool:
    """Return True when ``d`` falls within [start, end] comparing by DATE only.

    The end day is fully inclusive (time-of-day is ignored on all three values).
    """
    day = d.date()
    return start.date() <= day <= end.date()


def build_records(
    post: PostRef, people: Iterable[RawPerson], kind: str
) -> list[EngagementRecord]:
    """Build flat :class:`EngagementRecord` rows for one post.

    ``kind`` is the engagement discriminator (``KIND_LIKE`` / ``KIND_COMMENT``).
    The post date is formatted as ISO ``YYYY-MM-DD``. People without a profile
    URL are skipped (they cannot be aggregated by identity). The caller controls
    deduplication: likers arrive deduplicated by profile (one record each),
    while commenters arrive one-per-comment so multi-comment authors yield
    multiple records.
    """
    post_date = post.post_date.strftime("%Y-%m-%d")
    records: list[EngagementRecord] = []
    for person in people:
        if not person.profile_url:
            continue
        records.append(
            EngagementRecord(
                post_url=post.post_url,
                post_date=post_date,
                kind=kind,
                person_name=person.name,
                person_profile=person.profile_url,
                person_headline=person.headline,
            )
        )
    return records


def urn_from_post_url(post_url: str) -> str:
    """Extract the activity URN embedded in a feed-update permalink.

    Inverse of the ``.../feed/update/<urn>/`` permalink shape used to build post
    URLs. Strips a trailing slash and any query/fragment. Returns ``""`` when the
    URL contains no ``/feed/update/`` segment.
    """
    if not post_url:
        return ""
    marker = "/feed/update/"
    index = post_url.find(marker)
    if index == -1:
        return ""
    tail = post_url[index + len(marker):]
    return tail.split("?", 1)[0].split("#", 1)[0].strip("/")


# Words that identify the RESHARE COMPOSER action (the button that reshares a
# post AS THE LOGGED-IN USER). If ANY of these appears as a word in a candidate
# opener's accessible label, the candidate is rejected outright — even if the
# label also carries a count. The composer must never be clicked.
_REPOST_COMPOSER_WORDS = frozenset(
    {"republier", "repost", "reposter", "partager", "share"}
)

# Words that positively identify a LIST/COUNT of EXISTING reposts (the affordance
# that opens the reposters list). A safe opener must contain one of these AND no
# composer word. Note the EN singular "repost" is intentionally a composer word,
# not a list word, so an ambiguous "1 repost" label is rejected (safe miss)
# rather than risking a reshare.
_REPOST_LIST_WORDS = frozenset(
    {
        "republication",
        "republications",
        "reposts",
        "repartage",
        "repartages",
        "partage",
        "partages",
    }
)

# Unicode letter runs (accent-aware), excluding digits/underscore, used to split
# an accessible label into words for the safe-opener check.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def is_safe_reposts_opener(aria: str) -> bool:
    """Whether an accessible label denotes a reposts-LIST opener, never the composer.

    Safety-critical: this gate is the only thing standing between the scraper and
    accidentally clicking the "Republier"/"Repost" composer (which would reshare
    the post as the user). The decision is word-based, NOT substring/digit-based:

    * Reject if any word is a composer verb (``republier``/``repost``/… ), so a
      composer button that happens to carry a count ("Republier 14") is rejected.
    * Otherwise accept only on POSITIVE evidence of a reposts list — a list/count
      noun such as "republications" / "reposts" / "partages".

    An empty label, a bare verb, or a label with no list noun all return ``False``
    (fail safe). Words are matched case-insensitively and accent-aware.
    """
    if not aria:
        return False
    words = {match.group(0).lower() for match in _WORD_RE.finditer(aria)}
    if not words or words & _REPOST_COMPOSER_WORDS:
        return False
    return bool(words & _REPOST_LIST_WORDS)


def _author_target(author_url: str) -> tuple[str, str] | None:
    """Resolve an author URL to a ``(kind, identifier)`` pair.

    * ``("company", "<slug>")`` for a ``/company/<slug>`` page (the primary,
      company-first target).
    * ``("profile", "<id>")`` for a ``/in/<id>`` personal profile.
    * ``None`` when the URL is neither.

    Robust against trailing slashes, extra path segments and query/fragment.
    """
    if not author_url:
        return None

    candidate = author_url.strip().split("?", 1)[0].split("#", 1)[0]

    company = re.search(r"/company/([^/?#]+)", candidate)
    if company is not None and company.group(1).strip():
        return ("company", company.group(1).strip())

    profile = re.search(r"/in/([^/?#]+)", candidate)
    if profile is not None and profile.group(1).strip():
        return ("profile", profile.group(1).strip())

    return None


def author_kind(author_url: str) -> str | None:
    """Classify an author URL as ``"company"``, ``"profile"`` or ``None``.

    Used for early CLI validation and for logging which kind of author feed is
    being scraped. Returns ``None`` for unsupported URLs (feed, search, etc.).
    """
    target = _author_target(author_url)
    return target[0] if target is not None else None


def activity_url(author_url: str) -> str:
    """Build the posts/activity feed URL from an author URL.

    Supports both author kinds (company is the primary target):

    * company page ``/company/<slug>`` -> ``/company/<slug>/posts/``
    * personal profile ``/in/<id>``    -> ``/in/<id>/recent-activity/all/``

    Returns ``""`` when neither a ``/company/<slug>`` nor an ``/in/<id>``
    segment is present.
    """
    target = _author_target(author_url)
    if target is None:
        return ""

    kind, identifier = target
    if kind == "company":
        return f"{_LINKEDIN_BASE}/company/{identifier}/posts/"
    return f"{_LINKEDIN_BASE}/in/{identifier}/recent-activity/all/"
