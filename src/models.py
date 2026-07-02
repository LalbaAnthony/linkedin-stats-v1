"""Data models for the linkedin-stats CLI.

This module holds plain dataclasses and the engagement-kind constants only. It
must NOT import any other internal module so it can serve as a dependency-free
foundation for the rest of the package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Engagement kinds tracked per person. Used as the ``kind`` discriminator on
# :class:`EngagementRecord` and to label the scraped sources.
#
# Likes and comments are read from each post's detail page; reposts are read
# from the author's FEED (the detail page does not expose repost data). A repost
# is EITHER a plain reshare (:data:`KIND_REPOST`) OR a reshare with an added
# comment (:data:`KIND_REPOST_COMMENT`) — never both — so the two repost kinds
# are mutually exclusive and together account for every reshare of a post.
KIND_LIKE = "like"
KIND_COMMENT = "comment"
KIND_REPOST = "repost"
KIND_REPOST_COMMENT = "repost_comment"


@dataclass
class PostRef:
    """Reference to a single LinkedIn post discovered on an activity feed.

    Attributes:
        post_url: Canonical permalink to the post.
        raw_date: The relative date string as displayed by LinkedIn
            (e.g. "2mo", "il y a 3 sem.").
        post_date: Approximate datetime resolved from ``raw_date``. LinkedIn
            only exposes relative dates, so this value is necessarily an
            approximation.
    """

    post_url: str
    raw_date: str
    post_date: datetime


@dataclass
class RawPerson:
    """A person who engaged with a post (liked or commented), pre-normalization.

    The same shape covers both reactors and commenters — both are people with a
    name, a profile URL and an optional headline.

    Attributes:
        name: Display name of the person.
        profile_url: Absolute LinkedIn profile URL.
        headline: Free-text headline shown under the name (may be empty).
    """

    name: str
    profile_url: str
    headline: str


@dataclass
class PostEngagement:
    """All engagement collected from a single post in one visit.

    Likers are deduplicated by profile (one reaction per person per post),
    whereas commenters are NOT deduplicated: a person who comments several times
    appears once per comment, so their comment count is preserved.

    Attributes:
        likers: People who reacted to the post.
        commenters: One entry per comment (duplicates kept for multi-comment
            authors).
    """

    likers: list[RawPerson] = field(default_factory=list)
    commenters: list[RawPerson] = field(default_factory=list)


@dataclass
class PostReposts:
    """The people who reposted a single post, split by reshare style.

    Collected from the author's FEED (the post detail page does not expose
    repost data). A person reposts a given post at most once, and a repost is
    either a plain reshare or a reshare with an added comment — never both — so
    the two lists are disjoint and each is deduplicated by profile.

    Attributes:
        reposters: People who reshared the post WITHOUT adding a comment.
        reposters_with_comment: People who reshared the post WITH their own
            added comment ("repost with your thoughts").
    """

    reposters: list[RawPerson] = field(default_factory=list)
    reposters_with_comment: list[RawPerson] = field(default_factory=list)


@dataclass
class EngagementRecord:
    """A flattened (post, person, kind) tuple ready for aggregation and export.

    Attributes:
        post_url: Canonical permalink to the engaged post.
        post_date: ISO date string of the post, formatted as "YYYY-MM-DD".
        kind: Engagement kind, one of :data:`KIND_LIKE`, :data:`KIND_COMMENT`,
            :data:`KIND_REPOST` or :data:`KIND_REPOST_COMMENT`.
        person_name: Cleaned display name of the person.
        person_profile: Normalized absolute profile URL (the aggregation key).
        person_headline: Headline of the person (may be empty).
    """

    post_url: str
    post_date: str
    kind: str
    person_name: str
    person_profile: str
    person_headline: str
