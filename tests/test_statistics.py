"""Unit tests for :func:`src.statistics.aggregate`.

Deterministic and pure: build :class:`EngagementRecord` lists and assert the
resulting :class:`Stats` ranking, dedup behavior, like/comment counts and order.
"""

from __future__ import annotations

from src.models import (
    KIND_COMMENT,
    KIND_LIKE,
    KIND_REPOST,
    KIND_REPOST_COMMENT,
    EngagementRecord,
)
from src.statistics import Stats, aggregate


def _record(
    profile: str,
    name: str = "",
    headline: str = "",
    kind: str = KIND_LIKE,
    post_url: str = "https://www.linkedin.com/feed/update/urn:li:activity:1/",
    post_date: str = "2024-04-15",
) -> EngagementRecord:
    return EngagementRecord(
        post_url=post_url,
        post_date=post_date,
        kind=kind,
        person_name=name,
        person_profile=profile,
        person_headline=headline,
    )


def test_aggregate_empty() -> None:
    stats = aggregate([], posts_analyzed=0, posts_skipped=0)
    assert isinstance(stats, Stats)
    assert stats.total_likes == 0
    assert stats.total_comments == 0
    assert stats.total_reposts == 0
    assert stats.total_reposts_with_comment == 0
    assert stats.unique_reposters == 0
    assert stats.unique_people == 0
    assert stats.ranking == []
    assert stats.posts_analyzed == 0
    assert stats.posts_skipped == 0


def test_aggregate_totals_and_passthrough_counts() -> None:
    records = [
        _record("https://www.linkedin.com/in/a/", "Alice"),
        _record("https://www.linkedin.com/in/b/", "Bob"),
    ]
    stats = aggregate(records, posts_analyzed=5, posts_skipped=2)
    assert stats.total_likes == 2
    assert stats.total_comments == 0
    assert stats.unique_people == 2
    assert stats.posts_analyzed == 5
    assert stats.posts_skipped == 2


def test_aggregate_dedup_by_profile_counts_likes() -> None:
    p = "https://www.linkedin.com/in/alice/"
    records = [
        _record(p, "Alice", post_url="post-1"),
        _record(p, "Alice", post_url="post-2"),
        _record(p, "Alice", post_url="post-3"),
        _record("https://www.linkedin.com/in/bob/", "Bob", post_url="post-1"),
    ]
    stats = aggregate(records, posts_analyzed=3, posts_skipped=0)
    assert stats.total_likes == 4
    assert stats.unique_likers == 2

    by_profile = {row["profile_url"]: row for row in stats.ranking}
    assert by_profile[p]["likes_count"] == 3
    assert by_profile["https://www.linkedin.com/in/bob/"]["likes_count"] == 1


def test_aggregate_counts_likes_and_comments_separately() -> None:
    p = "https://www.linkedin.com/in/alice/"
    records = [
        _record(p, "Alice", kind=KIND_LIKE, post_url="p1"),
        _record(p, "Alice", kind=KIND_LIKE, post_url="p2"),
        # Same person comments three times (comments are NOT deduped per post).
        _record(p, "Alice", kind=KIND_COMMENT, post_url="p1"),
        _record(p, "Alice", kind=KIND_COMMENT, post_url="p1"),
        _record(p, "Alice", kind=KIND_COMMENT, post_url="p2"),
        # A pure commenter (never liked).
        _record("https://www.linkedin.com/in/bob/", "Bob", kind=KIND_COMMENT),
    ]
    stats = aggregate(records, posts_analyzed=2, posts_skipped=0)
    assert stats.total_likes == 2
    assert stats.total_comments == 4
    assert stats.unique_likers == 1
    assert stats.unique_commenters == 2
    assert stats.unique_people == 2

    by_profile = {row["profile_url"]: row for row in stats.ranking}
    assert by_profile[p]["likes_count"] == 2
    assert by_profile[p]["comments_count"] == 3
    assert by_profile["https://www.linkedin.com/in/bob/"]["likes_count"] == 0
    assert by_profile["https://www.linkedin.com/in/bob/"]["comments_count"] == 1


def test_aggregate_ranking_order_desc_likes_then_comments_then_name() -> None:
    records = [
        # Charlie: 1 like
        _record("https://www.linkedin.com/in/charlie/", "Charlie"),
        # Alice: 2 likes, 1 comment
        _record("https://www.linkedin.com/in/alice/", "Alice", post_url="p1"),
        _record("https://www.linkedin.com/in/alice/", "Alice", post_url="p2"),
        _record("https://www.linkedin.com/in/alice/", "Alice", kind=KIND_COMMENT),
        # Bob: 2 likes, 0 comments (ties Alice on likes -> more comments ranks Alice first)
        _record("https://www.linkedin.com/in/bob/", "Bob", post_url="p1"),
        _record("https://www.linkedin.com/in/bob/", "Bob", post_url="p2"),
    ]
    stats = aggregate(records, posts_analyzed=2, posts_skipped=0)
    names = [row["name"] for row in stats.ranking]
    likes = [row["likes_count"] for row in stats.ranking]
    assert likes == sorted(likes, reverse=True)
    # Alice and Bob both have 2 likes; Alice wins on comments_count tiebreak.
    assert names == ["Alice", "Bob", "Charlie"]
    assert likes == [2, 2, 1]


def test_aggregate_keeps_latest_nonempty_name_and_headline() -> None:
    p = "https://www.linkedin.com/in/x/"
    records = [
        _record(p, name="", headline=""),
        _record(p, name="Real Name", headline="Engineer"),
        _record(p, name="", headline=""),  # empty must not overwrite the kept values
    ]
    stats = aggregate(records, posts_analyzed=1, posts_skipped=0)
    assert stats.unique_people == 1
    row = stats.ranking[0]
    assert row["name"] == "Real Name"
    assert row["headline"] == "Engineer"
    assert row["likes_count"] == 3


def test_aggregate_ranking_row_shape() -> None:
    records = [_record("https://www.linkedin.com/in/a/", "Alice", "Headline")]
    stats = aggregate(records, posts_analyzed=1, posts_skipped=0)
    row = stats.ranking[0]
    assert set(row.keys()) == {
        "name",
        "profile_url",
        "headline",
        "likes_count",
        "comments_count",
        "repost_count",
        "repost_with_comment_count",
    }
    assert row["name"] == "Alice"
    assert row["profile_url"] == "https://www.linkedin.com/in/a/"
    assert row["headline"] == "Headline"
    assert row["likes_count"] == 1
    assert row["comments_count"] == 0
    assert row["repost_count"] == 0
    assert row["repost_with_comment_count"] == 0


def test_aggregate_counts_reposts_split_by_kind() -> None:
    p = "https://www.linkedin.com/in/alice/"
    records = [
        # Alice reshares two posts plainly and one with a comment.
        _record(p, "Alice", kind=KIND_REPOST, post_url="p1"),
        _record(p, "Alice", kind=KIND_REPOST, post_url="p2"),
        _record(p, "Alice", kind=KIND_REPOST_COMMENT, post_url="p3"),
        # Bob only reshares (with a comment) — he never liked/commented.
        _record(
            "https://www.linkedin.com/in/bob/",
            "Bob",
            kind=KIND_REPOST_COMMENT,
            post_url="p1",
        ),
    ]
    stats = aggregate(records, posts_analyzed=3, posts_skipped=0)
    assert stats.total_reposts == 2
    assert stats.total_reposts_with_comment == 2
    assert stats.total_likes == 0
    assert stats.total_comments == 0
    # A pure reposter still appears as a person in the ranking.
    assert stats.unique_reposters == 2
    assert stats.unique_people == 2

    by_profile = {row["profile_url"]: row for row in stats.ranking}
    assert by_profile[p]["repost_count"] == 2
    assert by_profile[p]["repost_with_comment_count"] == 1
    bob = by_profile["https://www.linkedin.com/in/bob/"]
    assert bob["repost_count"] == 0
    assert bob["repost_with_comment_count"] == 1
    # Reposts do not contribute to the like/comment columns.
    assert bob["likes_count"] == 0
    assert bob["comments_count"] == 0


def test_aggregate_all_four_kinds_for_one_person() -> None:
    p = "https://www.linkedin.com/in/multi/"
    records = [
        _record(p, "Multi", kind=KIND_LIKE, post_url="p1"),
        _record(p, "Multi", kind=KIND_COMMENT, post_url="p1"),
        _record(p, "Multi", kind=KIND_REPOST, post_url="p2"),
        _record(p, "Multi", kind=KIND_REPOST_COMMENT, post_url="p3"),
    ]
    stats = aggregate(records, posts_analyzed=3, posts_skipped=0)
    assert stats.unique_people == 1
    row = stats.ranking[0]
    assert row["likes_count"] == 1
    assert row["comments_count"] == 1
    assert row["repost_count"] == 1
    assert row["repost_with_comment_count"] == 1


def test_aggregate_reposts_do_not_change_sort_order() -> None:
    # Bob has many reposts but fewer likes; reposts must NOT lift him above Alice.
    records = [
        _record("https://www.linkedin.com/in/alice/", "Alice", post_url="p1"),
        _record("https://www.linkedin.com/in/alice/", "Alice", post_url="p2"),
        _record("https://www.linkedin.com/in/bob/", "Bob", post_url="p1"),
        _record(
            "https://www.linkedin.com/in/bob/", "Bob", kind=KIND_REPOST, post_url="p2"
        ),
        _record(
            "https://www.linkedin.com/in/bob/", "Bob", kind=KIND_REPOST, post_url="p3"
        ),
    ]
    stats = aggregate(records, posts_analyzed=3, posts_skipped=0)
    names = [row["name"] for row in stats.ranking]
    # Alice (2 likes) ranks before Bob (1 like) despite Bob's extra reposts.
    assert names == ["Alice", "Bob"]
