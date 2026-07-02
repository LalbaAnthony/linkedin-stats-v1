"""LinkedIn likes statistics CLI entry point.

Orchestrates the full run:
  1. Parse and validate CLI arguments.
  2. Open an authenticated LinkedIn session (manual login on first run).
  3. Collect the author's posts in the requested date range.
  4. Collect, per post, who reposted it (from the feed) split by reshare style.
  5. For each post, collect the likers/commenters (from the post page) and build
     like, comment and repost records.
  6. Aggregate statistics and export a CSV ranking.

Run from the repository root:
    python main.py --author <url> --start YYYY-MM-DD --end YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from src import __version__
from src.config import (
    DEFAULT_OUTPUT,
    HEADLESS_DEFAULT,
    human_delay,
    load_env,
    setup_logging,
)
from src.exporter import export_csv
from src.linkedin import LinkedInClient
from src import parser as parser_mod
from src import scraper
from src import statistics as stats_mod
from src.models import (
    KIND_COMMENT,
    KIND_LIKE,
    KIND_REPOST,
    KIND_REPOST_COMMENT,
    EngagementRecord,
)


def parse_date(s: str) -> datetime:
    """Parse a CLI date string in the ``YYYY-MM-DD`` format.

    Raises ``argparse.ArgumentTypeError`` so argparse reports a clean error.
    """
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{s}', expected format YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="linkedin-stats",
        description=(
            "Analyse who liked a LinkedIn company's (or author's) posts within "
            "a date range and export a CSV ranking. Dates are approximate "
            "because LinkedIn only exposes relative timestamps."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the program version and exit.",
    )
    parser.add_argument(
        "--author",
        required=True,
        type=str,
        help=(
            "LinkedIn author URL: a company page "
            "(https://www.linkedin.com/company/<slug>) or a personal profile "
            "(https://www.linkedin.com/in/<id>)."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        type=parse_date,
        help="Start date (inclusive), format YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end",
        required=True,
        type=parse_date,
        help="End date (inclusive), format YYYY-MM-DD.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        type=str,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=HEADLESS_DEFAULT,
        help=(
            "Run the browser headless (requires an existing session). Use "
            "--no-headless to force a visible window. Defaults to the "
            "LINKEDIN_HEADLESS env var (currently: "
            f"{HEADLESS_DEFAULT})."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable verbose DEBUG logging.",
    )
    return parser


def main() -> int:
    """Run the full scrape/aggregate/export pipeline.

    Returns 0 on success, 1 on any fatal error.
    """
    argument_parser = build_parser()
    args = argument_parser.parse_args()

    log: logging.Logger = setup_logging(debug=args.debug)
    load_env()

    if args.start > args.end:
        # Validate ordering after parsing so we can emit a clean argparse error.
        argument_parser.error("--start must be earlier than or equal to --end")

    kind = parser_mod.author_kind(args.author)
    if kind is None:
        # Fail fast instead of scraping an empty feed: only company pages and
        # personal profiles can be resolved to a posts feed.
        argument_parser.error(
            "--author must be a LinkedIn company page "
            "('/company/<slug>') or personal profile ('/in/<id>') URL"
        )

    log.info(
        "Run parameters: author=%s (%s) start=%s end=%s output=%s headless=%s",
        args.author,
        kind,
        args.start.strftime("%Y-%m-%d"),
        args.end.strftime("%Y-%m-%d"),
        args.output,
        args.headless,
    )

    records: list[EngagementRecord] = []
    posts_analyzed: int = 0
    posts_skipped: int = 0

    try:
        with LinkedInClient(headless=args.headless) as client:
            posts = scraper.collect_posts_in_range(
                client.page, args.author, args.start, args.end
            )
            log.info("Collected %d post(s) in range.", len(posts))

            # Reposts live only on the feed (the post detail page hides them), so
            # collect them up front in one isolated, best-effort feed pass. An
            # empty mapping (no data / failure) simply yields zero repost counts.
            reposts_by_url = scraper.collect_reposts(client.page, args.author, posts)
            log.info(
                "Collected repost data for %d/%d post(s).",
                len(reposts_by_url),
                len(posts),
            )

            for index, post in enumerate(posts):
                if index > 0:
                    # Human-like pause between post visits to reduce detection.
                    human_delay()
                try:
                    engagement = scraper.collect_post_engagement(
                        client.page, post.post_url
                    )
                    if engagement is None:
                        # The post itself could not be loaded.
                        posts_skipped += 1
                        log.warning(
                            "Skipping post %s: could not load it.", post.post_url
                        )
                        continue
                    records.extend(
                        parser_mod.build_records(post, engagement.likers, KIND_LIKE)
                    )
                    records.extend(
                        parser_mod.build_records(
                            post, engagement.commenters, KIND_COMMENT
                        )
                    )
                    reposts = reposts_by_url.get(post.post_url)
                    if reposts is not None:
                        records.extend(
                            parser_mod.build_records(
                                post, reposts.reposters, KIND_REPOST
                            )
                        )
                        records.extend(
                            parser_mod.build_records(
                                post,
                                reposts.reposters_with_comment,
                                KIND_REPOST_COMMENT,
                            )
                        )
                    posts_analyzed += 1
                    log.info(
                        "Post %d/%d processed: %d liker(s), %d comment(s), "
                        "%d repost(s), %d repost(s) with comment.",
                        index + 1,
                        len(posts),
                        len(engagement.likers),
                        len(engagement.commenters),
                        len(reposts.reposters) if reposts is not None else 0,
                        (
                            len(reposts.reposters_with_comment)
                            if reposts is not None
                            else 0
                        ),
                    )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - per-post isolation, never abort the run
                    posts_skipped += 1
                    log.warning("Skipping post %s due to error: %s", post.post_url, exc)
                    continue

        stats = stats_mod.aggregate(records, posts_analyzed, posts_skipped)
        output_path = export_csv(stats.ranking, args.output)

        # Final run summary.
        print("Posts analysed: " + str(stats.posts_analyzed))
        print("Posts skipped: " + str(stats.posts_skipped))
        print("Likes collected: " + str(stats.total_likes))
        print("Comments collected: " + str(stats.total_comments))
        print("Reposts collected: " + str(stats.total_reposts))
        print(
            "Reposts with comment collected: " + str(stats.total_reposts_with_comment)
        )
        print("Unique people: " + str(stats.unique_people))
        print("CSV generated: " + str(output_path))

        return 0
    except Exception as exc:  # noqa: BLE001 - top-level guard, report and exit non-zero
        log.error("Fatal error: %s", exc, exc_info=args.debug)
        return 1


if __name__ == "__main__":
    sys.exit(main())
