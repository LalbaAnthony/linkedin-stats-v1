"""Aggregation of engagement records into ranking statistics.

Pure module: no Playwright, no I/O. Groups :class:`EngagementRecord` entries by
person profile URL (the stable identity key) and produces a ranking with, per
person, a like count, a comment count and two repost counts (plain reshares and
reshares-with-a-comment). The ranking is sorted by likes then comments; the
repost columns are reported alongside but do not change the sort order.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from src.models import (
    KIND_COMMENT,
    KIND_LIKE,
    KIND_REPOST,
    KIND_REPOST_COMMENT,
    EngagementRecord,
)


@dataclass
class Stats:
    """Aggregated statistics over a collection of engagement records."""

    total_likes: int
    total_comments: int
    total_reposts: int
    total_reposts_with_comment: int
    unique_likers: int
    unique_commenters: int
    unique_reposters: int
    unique_people: int
    ranking: list[dict]
    posts_analyzed: int
    posts_skipped: int


def aggregate(
    records: Iterable[EngagementRecord],
    posts_analyzed: int,
    posts_skipped: int,
) -> Stats:
    """Aggregate engagement records into a :class:`Stats` ranking.

    Records are grouped by ``person_profile`` (the identity key). For each unique
    profile, ``likes_count``, ``comments_count``, ``repost_count`` and
    ``repost_with_comment_count`` are the number of like / comment / plain-repost
    / repost-with-comment records respectively, and the latest non-empty name and
    headline encountered are kept.

    The ranking is sorted by ``likes_count`` descending, then ``comments_count``
    descending, then ``name`` ascending (case-insensitive). The repost columns
    are reported but do NOT participate in the sort order.

    Args:
        records: Iterable of engagement records (likes, comments and reposts).
        posts_analyzed: Number of posts successfully analyzed.
        posts_skipped: Number of posts skipped due to errors.

    Returns:
        A :class:`Stats` instance describing the aggregation.
    """
    likes: dict[str, int] = defaultdict(int)
    comments: dict[str, int] = defaultdict(int)
    reposts: dict[str, int] = defaultdict(int)
    reposts_with_comment: dict[str, int] = defaultdict(int)
    names: dict[str, str] = {}
    headlines: dict[str, str] = {}

    total_likes = 0
    total_comments = 0
    total_reposts = 0
    total_reposts_with_comment = 0
    for record in records:
        profile = record.person_profile
        if record.kind == KIND_COMMENT:
            comments[profile] += 1
            total_comments += 1
        elif record.kind == KIND_REPOST:
            reposts[profile] += 1
            total_reposts += 1
        elif record.kind == KIND_REPOST_COMMENT:
            reposts_with_comment[profile] += 1
            total_reposts_with_comment += 1
        else:
            # Default any unrecognised record to a like (KIND_LIKE).
            likes[profile] += 1
            total_likes += 1

        # Keep the latest non-empty name/headline seen for this profile.
        if record.person_name:
            names[profile] = record.person_name
        elif profile not in names:
            names[profile] = ""

        if record.person_headline:
            headlines[profile] = record.person_headline
        elif profile not in headlines:
            headlines[profile] = ""

    profiles = (
        set(likes) | set(comments) | set(reposts) | set(reposts_with_comment)
    )
    reposters = set(reposts) | set(reposts_with_comment)
    ranking: list[dict] = [
        {
            "name": names.get(profile, ""),
            "profile_url": profile,
            "headline": headlines.get(profile, ""),
            "likes_count": likes.get(profile, 0),
            "comments_count": comments.get(profile, 0),
            "repost_count": reposts.get(profile, 0),
            "repost_with_comment_count": reposts_with_comment.get(profile, 0),
        }
        for profile in profiles
    ]

    # likes DESC, then comments DESC, then name ASC (case-insensitive). Reposts
    # are reported but intentionally do not affect ordering.
    ranking.sort(
        key=lambda row: (
            -row["likes_count"],
            -row["comments_count"],
            row["name"].lower(),
        )
    )

    return Stats(
        total_likes=total_likes,
        total_comments=total_comments,
        total_reposts=total_reposts,
        total_reposts_with_comment=total_reposts_with_comment,
        unique_likers=len(likes),
        unique_commenters=len(comments),
        unique_reposters=len(reposters),
        unique_people=len(profiles),
        ranking=ranking,
        posts_analyzed=posts_analyzed,
        posts_skipped=posts_skipped,
    )
