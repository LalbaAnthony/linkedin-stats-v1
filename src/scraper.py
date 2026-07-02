"""DOM collection helpers built on top of the Playwright sync API.

This module is the only place that walks the live LinkedIn DOM. It relies on
the best-effort selectors defined in :mod:`src.selectors` and on the pure
parsing helpers in :mod:`src.parser`. Because LinkedIn ships frequent DOM
changes and only exposes RELATIVE dates, the collection here is necessarily
approximate and defensive: every per-item extraction is wrapped in a
``try/except`` so a single malformed node never aborts a whole run.
"""

from __future__ import annotations

import logging
from datetime import datetime

from playwright.sync_api import ElementHandle, Page

from src import config
from src import parser
from src.models import PostEngagement, PostReposts, PostRef, RawPerson
from src.selectors import SELECTORS

logger = logging.getLogger("linkedin-stats")

# Number of consecutive too-old posts that triggers the scrolling early-stop.
# A small buffer avoids stopping prematurely when LinkedIn reorders items.
_EARLY_STOP_STREAK = 5

# Permalink template for a single feed update built from its activity URN.
_UPDATE_URL_TEMPLATE = "https://www.linkedin.com/feed/update/{urn}/"

# DEBUG-only diagnostic: dump the full post DOM of the first few posts to files
# so selectors can be derived from real markup (LinkedIn now uses obfuscated,
# hashed class names, so aria-labels/structure are the only durable hooks).
_DUMP_LIMIT = 3
_dump_count = 0
# Guards the one-time reactions-modal / comments HTML dumps (DEBUG runs only).
_modal_dumped = False
_comments_dumped = False
# Reposts modals are dumped to numbered files (DEBUG only) so that modals with a
# MIX of plain and quote reshares can be inspected, not just the first one.
_REPOSTS_DUMP_LIMIT = 15
_reposts_dump_count = 0
_repost_card_dumped = False
_repost_card_no_opener_dumped = False


def _query_first_text(scope: Page | ElementHandle, selector: str) -> str:
    """Return the trimmed inner text of the first matching element.

    ``selector`` may bundle several comma-separated fallbacks; they are tried in
    priority order and the first that yields non-empty text wins (a single
    combined query would instead return the first DOM-order match, ignoring the
    intended fallback priority). Returns an empty string when nothing matches or
    the text cannot be read. Never raises.
    """
    for part in _split_selector(selector):
        try:
            element = scope.query_selector(part)
            if element is None:
                continue
            text = element.inner_text()
            if text and text.strip():
                return text.strip()
        except Exception:  # noqa: BLE001 - defensive: DOM nodes may detach mid-read.
            continue
    return ""


def collect_posts_in_range(
    page: Page,
    author_url: str,
    start: datetime,
    end: datetime,
    reference: datetime | None = None,
) -> list[PostRef]:
    """Collect the author's posts whose approximate date falls within a range.

    Navigates to the author's recent-activity feed, scrolls the window to lazy
    load posts, and extracts a :class:`PostRef` for each post container. Post
    dates are RELATIVE on LinkedIn, so they are resolved approximately via
    :func:`src.parser.parse_relative_date` against ``reference`` (defaulting to
    ``datetime.now()``).

    Posts are deduplicated by their activity URN. Scrolling stops early once a
    streak of consecutive posts is older than ``start`` (the feed is roughly
    reverse-chronological). Posts whose date cannot be parsed are KEPT (a
    warning is logged) so that ambiguous items are not silently dropped; only
    posts with a parsed date outside the range are excluded.

    Args:
        page: An active Playwright page (already authenticated).
        author_url: The target author's profile URL.
        start: Inclusive start of the date range.
        end: Inclusive end of the date range (whole day inclusive).
        reference: Reference instant for relative-date resolution. Defaults to
            ``datetime.now()`` when ``None``.

    Returns:
        The list of in-range :class:`PostRef` objects, deduplicated by URN and
        ordered by first appearance in the feed.
    """
    ref = reference if reference is not None else datetime.now()
    activity_url = parser.activity_url(author_url)
    if not activity_url:
        logger.error(
            "Could not derive an activity feed URL from author=%r; expected a "
            "company page ('/company/<slug>') or a profile ('/in/<id>').",
            author_url,
        )
        return []
    kind = parser.author_kind(author_url) or "unknown"
    logger.info("Navigating to %s posts feed: %s", kind, activity_url)
    page.goto(activity_url, wait_until="domcontentloaded")
    config.human_delay()

    seen_urns: set[str] = set()
    in_range_posts: list[PostRef] = []
    too_old_streak = 0
    last_post_count = -1
    stale_scrolls = 0

    for scroll_index in range(config.MAX_FEED_SCROLLS):
        containers = page.query_selector_all(SELECTORS["post_container"])
        logger.debug(
            "Scroll %d: %d post container(s) currently in DOM",
            scroll_index,
            len(containers),
        )

        for container in containers:
            try:
                urn = container.get_attribute("data-urn")
                if not urn:
                    # Some impression containers carry the URN on a descendant.
                    inner = container.query_selector("[data-urn]")
                    urn = inner.get_attribute("data-urn") if inner else None
                if not urn or urn in seen_urns:
                    continue
                seen_urns.add(urn)

                raw_date = _query_first_text(container, SELECTORS["post_time"])
                resolved = parser.parse_relative_date(raw_date, ref)

                post_url = _UPDATE_URL_TEMPLATE.format(urn=urn)

                if resolved is None:
                    # Keep unparseable posts rather than dropping them silently.
                    logger.warning(
                        "Unparseable post date %r for %s; keeping post.",
                        raw_date,
                        post_url,
                    )
                    too_old_streak = 0
                    in_range_posts.append(
                        PostRef(post_url=post_url, raw_date=raw_date, post_date=ref)
                    )
                    continue

                if parser.in_range(resolved, start, end):
                    too_old_streak = 0
                    in_range_posts.append(
                        PostRef(
                            post_url=post_url,
                            raw_date=raw_date,
                            post_date=resolved,
                        )
                    )
                elif resolved.date() < start.date():
                    # Older than the window: feed is roughly reverse-chrono, so
                    # a streak of these means we can stop scrolling.
                    too_old_streak += 1
                else:
                    # Newer than the window (resolved > end): not yet in range,
                    # keep scrolling without growing the too-old streak.
                    too_old_streak = 0
            except Exception as exc:  # noqa: BLE001 - never abort the whole feed.
                logger.warning("Failed to extract a post container: %s", exc)
                continue

        if too_old_streak >= _EARLY_STOP_STREAK:
            logger.info(
                "Early stop: %d consecutive posts older than start date.",
                too_old_streak,
            )
            break

        # Detect a feed that no longer grows (end of activity / no more lazy
        # loading) and stop after a couple of stale scrolls.
        if len(seen_urns) == last_post_count:
            stale_scrolls += 1
            if stale_scrolls >= 2:
                logger.info("Feed stopped growing; ending scroll loop.")
                break
        else:
            stale_scrolls = 0
        last_post_count = len(seen_urns)

        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(config.SCROLL_PAUSE_MS)
        config.human_delay()

    logger.info(
        "Collected %d in-range post(s) out of %d unique post(s) seen.",
        len(in_range_posts),
        len(seen_urns),
    )
    return in_range_posts


def collect_reposts(
    page: Page, author_url: str, posts: list[PostRef]
) -> dict[str, PostReposts]:
    """Collect, per post, who reposted it — split by reshare style.

    Reposts are exposed ONLY on the author's feed (the post detail page hides
    them), so this re-opens the feed and, for each target post, locates its card
    by activity URN, opens the reposts LIST and reads the reposters. Each is
    classified as a plain reshare or a reshare-with-a-comment.

    Runs as an isolated, fully defensive pass so it can never regress the
    discovery/likes/comments flow: any failure yields empty results for the
    affected post(s) and the function never raises.

    CRITICAL SAFETY INVARIANT: this never clicks the "Republier"/"Repost"
    composer action button (which would reshare the post as the logged-in user).
    Only a dedicated reposts-list opener is ever clicked — one whose accessible
    label names a reposts list/count and carries NO composer verb (enforced by
    :func:`src.parser.is_safe_reposts_opener`).

    Args:
        page: An active, authenticated Playwright page.
        author_url: The target author URL (used to rebuild the feed URL).
        posts: The in-range posts whose reposters should be collected.

    Returns:
        A mapping of ``post_url`` -> :class:`PostReposts`. Posts whose reposters
        could not be collected are simply absent from the mapping.
    """
    result: dict[str, PostReposts] = {}
    if not posts:
        return result

    # Map activity URN -> post_url so cards encountered while scrolling can be
    # matched back to the requested posts; drop posts with an unparseable URN.
    pending: dict[str, str] = {}
    for post in posts:
        urn = parser.urn_from_post_url(post.post_url)
        if urn:
            pending[urn] = post.post_url
    if not pending:
        logger.debug("No parseable activity URN among %d post(s).", len(posts))
        return result

    feed_url = parser.activity_url(author_url)
    if not feed_url:
        logger.warning("Could not rebuild feed URL from author=%r.", author_url)
        return result

    try:
        logger.info("Navigating to feed to collect reposts: %s", feed_url)
        page.goto(feed_url, wait_until="domcontentloaded")
        config.human_delay()
    except Exception as exc:  # noqa: BLE001 - reposts are best-effort.
        logger.warning("Could not reopen feed for reposts: %s", exc)
        return result

    # Mirror collect_posts_in_range's early-stop: track how many DISTINCT post
    # cards the feed has lazy-loaded (which GROWS as we scroll) and stop only when
    # that stops growing. Tracking pending count instead would abort early, since
    # pending only shrinks when a TARGET card is matched and the requested posts
    # may sit many non-target cards deep.
    seen_urns: set[str] = set()
    last_seen_count = -1
    stale_scrolls = 0
    for _ in range(config.MAX_FEED_SCROLLS):
        if not pending:
            break

        navigated = False
        for container in page.query_selector_all(SELECTORS["post_container"]):
            urn = _container_urn(container)
            if not urn:
                continue
            seen_urns.add(urn)
            if urn not in pending:
                continue
            post_url = pending.pop(urn)
            reposts, navigated = _collect_card_reposters(page, container, post_url)
            result[post_url] = reposts
            if navigated:
                # A click navigated the page and we reloaded the feed: every
                # remaining handle in this snapshot is now detached, so abandon
                # the snapshot and re-query from a fresh (top-of-feed) state.
                break
            if not pending:
                break

        if not pending:
            break

        if navigated:
            # The feed was reset to the top; do not count this against the
            # stale-scroll budget and re-scan before scrolling further.
            last_seen_count = -1
            stale_scrolls = 0
            config.human_delay()
            continue

        # Stop once the feed stops lazy-loading new cards (end of feed reached, or
        # the remaining posts are simply unreachable within the scroll cap).
        if len(seen_urns) == last_seen_count:
            stale_scrolls += 1
            if stale_scrolls >= 3:
                logger.info("Feed stopped growing; ending repost scroll loop.")
                break
        else:
            stale_scrolls = 0
        last_seen_count = len(seen_urns)

        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(config.SCROLL_PAUSE_MS)
        config.human_delay()

    if pending:
        logger.info(
            "Reposts: %d post(s) not relocated on the feed (no repost data).",
            len(pending),
        )
    return result


def _collect_card_reposters(
    page: Page, card: ElementHandle, post_url: str
) -> tuple[PostReposts, bool]:
    """Open one feed card's reposts list and read its reposters (best-effort).

    Returns ``(reposts, navigated)`` where ``navigated`` is ``True`` when an
    opener click navigated the page away from the feed (so the caller must
    discard its now-detached card snapshot and re-query). Never raises and never
    clicks the reshare composer.
    """
    instant: dict[str, RawPerson] = {}
    with_comment: dict[str, RawPerson] = {}
    navigated = False
    try:
        opener = _find_reposts_opener(card)
        if opener is None:
            # No safe list-opener on this card (e.g. 0 reposts). Dump it (DEBUG)
            # for reference, and return nothing.
            logger.debug("No safe reposts-list opener on card %s.", post_url)
            _dump_repost_card_dom(card, post_url, has_opener=False)
            return PostReposts(), False

        logger.debug(
            "Reposts opener for %s: aria=%r", post_url, opener.get_attribute("aria-label")
        )
        # Capture an opener-bearing card so the opener markup can be confirmed.
        _dump_repost_card_dom(card, post_url, has_opener=True)

        # A modal leaked from a previous card (or a document-viewer overlay open
        # on the feed) would intercept this click — the loop does NOT re-navigate
        # between cards — so clear any open overlay first.
        _dismiss_open_overlays(page)

        before_url = page.url
        opener.scroll_into_view_if_needed(timeout=2000)
        config.human_delay()
        try:
            opener.click(timeout=2000)
        except Exception as exc:  # noqa: BLE001 - blocked/non-clickable: fail fast.
            logger.debug("Reposts opener click failed for %s: %s", post_url, exc)
            _dismiss_open_overlays(page)
            return PostReposts(), False

        try:
            page.wait_for_selector(SELECTORS["reposts_dialog"], timeout=5000)
        except Exception:  # noqa: BLE001 - opener opened no recognised modal.
            navigated = page.url != before_url
            if navigated:
                logger.debug("Reposts opener navigated away for %s; recovering.", post_url)
                _recover_feed(page, before_url)
            else:
                logger.debug("Reposts opener opened no modal for %s.", post_url)
            return PostReposts(), navigated

        config.human_delay()
        _scroll_reposts_modal(page)
        _dump_reposts_dom(page, post_url)
        dialog = _visible_overlay(page)
        if dialog is not None:
            _extract_reposters(dialog, instant, with_comment)
        _dismiss_open_overlays(page)
    except Exception as exc:  # noqa: BLE001 - reposts are best-effort per post.
        logger.warning("Repost collection failed for %s: %s", post_url, exc)
        _dismiss_open_overlays(page)
    return (
        PostReposts(
            reposters=list(instant.values()),
            reposters_with_comment=list(with_comment.values()),
        ),
        navigated,
    )


def _find_reposts_opener(card: ElementHandle) -> ElementHandle | None:
    """Return a SAFE reposts-list opener within a card, or ``None``.

    Tries the comma-separated fallback selectors in priority order and returns
    the first candidate whose accessible label passes
    :func:`src.parser.is_safe_reposts_opener`. Returning ``None`` (rather than a
    guess) guarantees we never click the reshare composer.
    """
    for selector in _split_selector(SELECTORS["reposts_button"]):
        try:
            candidates = card.query_selector_all(selector)
        except Exception as exc:  # noqa: BLE001 - skip a malformed selector.
            logger.debug("Reposts selector %r failed: %s", selector, exc)
            continue
        for candidate in candidates:
            try:
                aria = candidate.get_attribute("aria-label") or ""
                if parser.is_safe_reposts_opener(aria):
                    return candidate
            except Exception as exc:  # noqa: BLE001 - skip a bad candidate.
                logger.debug("Reposts opener candidate check failed: %s", exc)
    return None


def _visible_overlay(page: Page) -> ElementHandle | None:
    """Return the first VISIBLE element matching the reposts overlay, or ``None``.

    ``query_selector`` returns the first DOM-order match regardless of visibility,
    but LinkedIn keeps hidden modal containers in the DOM; reading reposters from
    one would yield nothing while the real (visible) modal is open. Never raises.
    """
    try:
        for element in page.query_selector_all(SELECTORS["reposts_dialog"]):
            try:
                if element.is_visible():
                    return element
            except Exception:  # noqa: BLE001 - detached node mid-read.
                continue
    except Exception as exc:  # noqa: BLE001 - best-effort lookup.
        logger.debug("Visible-overlay lookup failed: %s", exc)
    return None


def _dismiss_open_overlays(page: Page) -> None:
    """Close any open modal overlay (native ``<dialog>`` OR LinkedIn artdeco modal).

    The reposts pass stays on the feed across cards, so a modal opened for one
    card — or a document-viewer overlay already open on the feed — MUST be
    dismissed before the next card's opener click; otherwise it intercepts pointer
    events and every later click times out (observed live as a cascade of
    ``artdeco-modal-overlay`` click interceptions). Tries, in order: Escape; the
    overlay's own close button; native ``dialog.close()``. Re-checks after each.
    Best-effort and never raises.
    """
    try:
        existing = page.query_selector(SELECTORS["reposts_dialog"])
        # Only act on a VISIBLE overlay: LinkedIn keeps hidden modal containers in
        # the DOM, and pressing Escape for those would be pointless noise.
        if existing is None or not existing.is_visible():
            return
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        overlay = page.query_selector(SELECTORS["reposts_dialog"])
        if overlay is None or not overlay.is_visible():
            return
        for selector in _split_selector(SELECTORS["overlay_close"]):
            button = overlay.query_selector(selector)
            if button is None:
                continue
            try:
                button.click(timeout=1500)
                page.wait_for_timeout(300)
            except Exception as exc:  # noqa: BLE001 - try the next close control.
                logger.debug("Overlay close-button click failed: %s", exc)
                continue
            if page.query_selector(SELECTORS["reposts_dialog"]) is None:
                return

        remaining = page.query_selector(SELECTORS["reposts_dialog"])
        if remaining is not None:
            remaining.evaluate(
                "el => { if (el && typeof el.close === 'function' && el.open) el.close(); }"
            )
            page.wait_for_timeout(150)
    except Exception as exc:  # noqa: BLE001 - non-fatal overlay cleanup.
        logger.debug("Dismissing open overlay failed: %s", exc)


def _extract_reposters(
    dialog: ElementHandle,
    instant: dict[str, RawPerson],
    with_comment: dict[str, RawPerson],
) -> None:
    """Read ONE reposter per repost entry from the open reposts list.

    Each entry in the reposts modal is the reshare itself (carries its own
    activity ``data-urn`` / ``role="article"``) and EMBEDS the original post it
    quotes. The reposter is the entry's actor; the embedded original's author, its
    reaction facepile and its commenters are NOT reposters. Reading every profile
    link in the modal would miscount those people as reposters (a single entry was
    seen to hold ~11 ``/in/`` links, only one being the reposter) — so for each
    entry we take the first ``/in/`` link that is NOT inside the embedded original
    (:data:`SELECTORS['repost_embedded_original']`), and classify the entry as
    repost-with-comment when it carries the reposter's own commentary text outside
    that embedded original. Deduplicated by normalized profile URL across BOTH
    buckets (a person reposts a given post at most once). Never raises.
    """
    for entry in dialog.query_selector_all(SELECTORS["repost_entry"]):
        try:
            info = entry.evaluate(
                """(entry, sels) => {
                    const [miniSel, linkSel, textSel, nameSel] = sels;
                    const minis = Array.from(entry.querySelectorAll(miniSel));
                    const inEmbedded = (n) => minis.some((m) => m.contains(n));
                    // Profile identity: first reposter /in/ link outside the
                    // embedded original (this is the avatar link; the name link
                    // points to the same profile).
                    let href = '';
                    for (const a of entry.querySelectorAll(linkSel)) {
                        if (inEmbedded(a)) continue;
                        href = a.href || '';
                        break;
                    }
                    // Name: prefer the dedicated actor-title element (just the
                    // name); fall back to the first actor link's visible text.
                    let name = '';
                    for (const el of entry.querySelectorAll(nameSel)) {
                        if (inEmbedded(el)) continue;
                        name = (el.innerText || '').trim();
                        if (name) break;
                    }
                    if (!name) {
                        for (const a of entry.querySelectorAll(linkSel)) {
                            if (inEmbedded(a)) continue;
                            const txt = (a.innerText || '').trim();
                            if (txt) { name = txt; break; }
                            if (!name) name = a.getAttribute('aria-label') || '';
                        }
                    }
                    // A reshare-WITH-comment has the reposter's own body text
                    // OUTSIDE the embedded original. Only trust that when an
                    // embedded-original wrapper actually bounds the quoted post:
                    // a PLAIN reshare renders the original without that wrapper, so
                    // its body text would otherwise be misread as the reposter's
                    // comment (every plain reshape wrongly tagged "with comment").
                    let hasComment = false;
                    if (minis.length > 0) {
                        for (const t of entry.querySelectorAll(textSel)) {
                            if (inEmbedded(t)) continue;
                            if ((t.innerText || '').trim()) { hasComment = true; break; }
                        }
                    }
                    return { href, name, hasComment, embedded: minis.length };
                }""",
                [
                    SELECTORS["repost_embedded_original"],
                    SELECTORS["reposter_info"],
                    SELECTORS["repost_comment_marker"],
                    SELECTORS["reposter_name"],
                ],
            )
            info = info or {}
            profile = parser.normalize_profile_url(info.get("href") or "")
            if not profile or profile in instant or profile in with_comment:
                continue
            # Take the first line only (innerText may append a headline) then run
            # the shared name cleaner ("Voir le profil de X" -> "X", degree strip).
            raw_name = (info.get("name") or "").split("\n", 1)[0]
            person = RawPerson(
                name=parser.clean_name(raw_name),
                profile_url=profile,
                headline="",
            )
            logger.debug(
                "Reposter: %r (%s) embedded=%s hasComment=%s",
                person.name,
                profile,
                info.get("embedded"),
                info.get("hasComment"),
            )
            if info.get("hasComment"):
                with_comment[profile] = person
            else:
                instant[profile] = person
        except Exception as exc:  # noqa: BLE001 - skip a single bad entry.
            logger.debug("Reposter extraction failed: %s", exc)


def _scroll_reposts_modal(page: Page) -> None:
    """Scroll the open reposts list until its reposter count stabilizes.

    Mirrors :func:`_scroll_reactions_modal`: the list lazy-loads reposters in
    pages, and class names are obfuscated, so every scrollable descendant of the
    dialog is scrolled to the bottom up to ``config.MAX_MODAL_SCROLLS`` times,
    stopping once the number of reposter links stops growing.
    """
    dialog = _visible_overlay(page)
    if dialog is None:
        return

    last_count = -1
    stale_scrolls = 0
    for _ in range(config.MAX_MODAL_SCROLLS):
        count = len(dialog.query_selector_all(SELECTORS["reposter_info"]))
        if count == last_count:
            stale_scrolls += 1
            if stale_scrolls >= 3:
                break
        else:
            stale_scrolls = 0
        last_count = count

        try:
            dialog.evaluate(
                """dlg => {
                    for (const el of [dlg, ...dlg.querySelectorAll('*')]) {
                        if (el.scrollHeight > el.clientHeight + 20) {
                            el.scrollTop = el.scrollHeight;
                        }
                    }
                }"""
            )
        except Exception as exc:  # noqa: BLE001 - keep trying remaining scrolls.
            logger.debug("Reposts modal scroll step failed: %s", exc)

        page.wait_for_timeout(config.SCROLL_PAUSE_MS)
        config.human_delay()


def _recover_feed(page: Page, feed_url: str) -> None:
    """Return to the feed if a click navigated away from it (best-effort)."""
    try:
        if page.url != feed_url:
            page.goto(feed_url, wait_until="domcontentloaded")
            config.human_delay()
    except Exception as exc:  # noqa: BLE001 - non-fatal.
        logger.debug("Feed recovery failed: %s", exc)


def _container_urn(container: ElementHandle) -> str:
    """Return a post container's activity URN, or ``""`` (mirrors discovery)."""
    try:
        urn = container.get_attribute("data-urn")
        if not urn:
            inner = container.query_selector("[data-urn]")
            urn = inner.get_attribute("data-urn") if inner else None
        return urn or ""
    except Exception:  # noqa: BLE001 - detached node mid-read.
        return ""


def _dump_reposts_dom(page: Page, post_url: str) -> None:
    """Write the open reposts-list HTML to a file (DEBUG only, once).

    Lets the reposter-row extraction and the with-comment classification be
    refined against real markup. No-op unless DEBUG logging is enabled.
    """
    global _reposts_dump_count
    if (
        not logger.isEnabledFor(logging.DEBUG)
        or _reposts_dump_count >= _REPOSTS_DUMP_LIMIT
    ):
        return
    try:
        overlay = _visible_overlay(page)
        if overlay is None:
            return
        html = overlay.evaluate("el => el.outerHTML")
        _reposts_dump_count += 1
        out_path = (
            config.PROJECT_ROOT / "output" / f"debug_reposts_{_reposts_dump_count}.html"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html or "", encoding="utf-8")
        logger.debug(
            "Wrote reposts list DOM for %s to %s (%d chars).",
            post_url,
            out_path,
            len(html or ""),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only.
        logger.debug("Reposts DOM dump failed for %s: %s", post_url, exc)


def _dump_repost_card_dom(
    card: ElementHandle, post_url: str, has_opener: bool
) -> None:
    """Write a feed card's HTML to a file (DEBUG only, once per kind).

    Captures, separately and once each, the first card that DOES expose a
    reposts-list opener (``debug_repost_card.html`` — lets the opener / modal
    trigger be confirmed) and the first card that does NOT
    (``debug_repost_card_no_opener.html``). Keeping the kinds in separate slots
    guarantees a no-opener card cannot consume the (more useful) opener-bearing
    capture. No-op unless DEBUG logging is enabled.
    """
    global _repost_card_dumped, _repost_card_no_opener_dumped
    if not logger.isEnabledFor(logging.DEBUG):
        return
    if has_opener:
        if _repost_card_dumped:
            return
        filename = "debug_repost_card.html"
    else:
        if _repost_card_no_opener_dumped:
            return
        filename = "debug_repost_card_no_opener.html"
    try:
        html = card.evaluate("el => el.outerHTML")
        out_path = config.PROJECT_ROOT / "output" / filename
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html or "", encoding="utf-8")
        if has_opener:
            _repost_card_dumped = True
        else:
            _repost_card_no_opener_dumped = True
        logger.debug(
            "Wrote repost card DOM (has_opener=%s) for %s to %s (%d chars).",
            has_opener,
            post_url,
            out_path,
            len(html or ""),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only.
        logger.debug("Repost card DOM dump failed for %s: %s", post_url, exc)


def collect_post_engagement(page: Page, post_url: str) -> PostEngagement | None:
    """Collect a post's likers and commenters in a single visit.

    Navigates to the post once, then:

    * reads every reactor from the reactions modal (falling back to the in-page
      facepile), deduplicated by profile (one reaction per person per post); and
    * reads every comment's author, NOT deduplicated (a person who comments
      several times is counted once per comment).

    Returns ``None`` only when the post itself could not be loaded (a navigation
    failure -> the caller skips the post). Otherwise returns a
    :class:`PostEngagement` whose lists may be empty. Never raises.
    """
    try:
        logger.info("Navigating to post: %s", post_url)
        page.goto(post_url, wait_until="domcontentloaded")
        config.human_delay()
    except Exception as exc:  # noqa: BLE001 - navigation failure -> skip post.
        logger.warning("Failed to open post %s: %s", post_url, exc)
        return None

    likers = _collect_reactors(page, post_url)
    commenters = _collect_commenters(page, post_url)
    logger.info(
        "Post engagement for %s: %d liker(s), %d comment(s).",
        post_url,
        len(likers),
        len(commenters),
    )
    return PostEngagement(likers=likers, commenters=commenters)


def _collect_reactors(page: Page, post_url: str) -> list[RawPerson]:
    """Collect the unique reactors of a post (modal, facepile fallback).

    Returns the deduplicated reactors (empty list when there are none or on a
    best-effort failure). Never raises.
    """
    reactors: dict[str, RawPerson] = {}
    try:
        _reveal_social_bar(page)

        # Primary source: open the full reactions modal and read every reactor.
        if _open_reactions_modal(page, post_url):
            _scroll_reactions_modal(page)
            _dump_modal_dom(page, post_url)
            dialog = page.query_selector(SELECTORS["reactions_dialog"])
            if dialog is not None:
                _extract_reactors(
                    dialog.query_selector_all(SELECTORS["reactor_info"]), reactors
                )
            # Close the modal so it no longer overlays the comments section.
            _close_overlay(page)

        # Fallback / supplement: the in-page reactor facepile (top reactors).
        facepile = page.query_selector(SELECTORS["reaction_facepile"])
        if facepile is not None:
            _extract_reactors(
                facepile.query_selector_all(SELECTORS["reactor_info"]), reactors
            )
    except Exception as exc:  # noqa: BLE001 - reactors are best-effort.
        logger.warning("Reactor collection failed for %s: %s", post_url, exc)
    return list(reactors.values())


def _close_overlay(page: Page) -> None:
    """Dismiss an open modal/dialog (e.g. the reactions list) via Escape."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    except Exception as exc:  # noqa: BLE001 - non-fatal.
        logger.debug("Closing overlay failed: %s", exc)


def _extract_reactors(
    info_elements: list[ElementHandle], into: dict[str, RawPerson]
) -> None:
    """Extract reactors from their accessible-label elements into ``into``.

    Each element carries an aria-label of the form
    "<Name> a réagi avec <Type>[, <degree>, <headline>]" (FR) / "<Name> reacted
    with <Type>…" (EN), from which the name and (in the modal) the headline are
    parsed. The profile URL is resolved from the closest enclosing — or nearest
    descendant — "/in/" anchor. Mutates ``into`` in place, deduplicating by
    normalized profile URL.
    """
    for element in info_elements:
        try:
            aria = element.get_attribute("aria-label") or ""
            href = element.evaluate(
                """el => {
                    const a = el.closest('a[href*="/in/"]')
                        || el.querySelector('a[href*="/in/"]');
                    return a ? a.href : '';
                }"""
            )
            profile = parser.normalize_profile_url(href or "")
            if not profile or profile in into:
                continue

            name = parser.reactor_name_from_aria(aria)
            if not name:
                text = element.inner_text() or ""
                name = parser.clean_name(text.split("\n", 1)[0])
            headline = parser.reactor_headline_from_aria(aria)

            into[profile] = RawPerson(
                name=name, profile_url=profile, headline=headline
            )
        except Exception as exc:  # noqa: BLE001 - skip a single bad reactor.
            logger.debug("Reactor extraction failed: %s", exc)


def _collect_commenters(page: Page, post_url: str) -> list[RawPerson]:
    """Collect one :class:`RawPerson` per comment on the post (duplicates kept).

    Loads as many comments as possible (scrolling + "load more comments"/replies)
    then reads each comment's "more options" control, whose aria-label names the
    author ("…commentaire de <Name>"). The author profile is resolved from the
    nearest enclosing comment's first "/in/" link. NOT deduplicated, so a person
    who comments several times is counted once per comment. Never raises.
    """
    commenters: list[RawPerson] = []
    try:
        _load_all_comments(page)
        _dump_comments_dom(page, post_url)
        for element in page.query_selector_all(SELECTORS["comment_author_info"]):
            try:
                aria = element.get_attribute("aria-label") or ""
                name = parser.commenter_name_from_aria(aria)
                # Author link: climb to the smallest ancestor that contains a
                # profile link and take that subtree's first "/in/" anchor.
                href = element.evaluate(
                    """el => {
                        let n = el;
                        for (let i = 0; i < 8 && n; i++) {
                            const a = n.querySelector
                                && n.querySelector('a[href*="/in/"]');
                            if (a) return a.href;
                            n = n.parentElement;
                        }
                        return '';
                    }"""
                )
                profile = parser.normalize_profile_url(href or "")
                if not profile:
                    continue
                commenters.append(
                    RawPerson(name=name, profile_url=profile, headline="")
                )
            except Exception as exc:  # noqa: BLE001 - skip a single bad comment.
                logger.debug("Comment extraction failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - comments are best-effort.
        logger.warning("Comment collection failed for %s: %s", post_url, exc)
    return commenters


def _load_all_comments(page: Page) -> None:
    """Scroll to the comments and repeatedly reveal more, up to the scroll cap.

    Stops once no "load more" control fires and the comment count is stable for
    a few iterations. Bounded by ``config.MAX_MODAL_SCROLLS``.
    """
    last_count = -1
    stale = 0
    for _ in range(config.MAX_MODAL_SCROLLS):
        clicked = _click_load_more_comments(page)
        count = len(page.query_selector_all(SELECTORS["comment_author_info"]))
        if not clicked and count == last_count:
            stale += 1
            if stale >= 3:
                break
        else:
            stale = 0
        last_count = count

        try:
            page.mouse.wheel(0, 12000)
        except Exception as exc:  # noqa: BLE001 - keep going.
            logger.debug("Comment scroll failed: %s", exc)
        page.wait_for_timeout(config.SCROLL_PAUSE_MS)
        config.human_delay()


def _click_load_more_comments(page: Page) -> bool:
    """Click any visible "load more comments"/"show replies" control.

    Returns True if at least one was clicked. Selectors are deliberately narrow
    so the reply/comment ACTION buttons (Répondre, Commenter, J'aime) are never
    clicked — that would open an editor or react as the user.
    """
    clicked = False
    for selector in _split_selector(SELECTORS["load_more_comments"]):
        for button in page.query_selector_all(selector):
            try:
                button.scroll_into_view_if_needed(timeout=1500)
                button.click(timeout=1500)
                clicked = True
                page.wait_for_timeout(600)
            except Exception as exc:  # noqa: BLE001 - skip a stubborn button.
                logger.debug("Load-more-comments click failed: %s", exc)
    return clicked


def _dump_comments_dom(page: Page, post_url: str) -> None:
    """Write the comments-section HTML to a file (DEBUG only, once).

    Lets the comment-author selectors be refined against real markup. No-op
    unless DEBUG logging is enabled.
    """
    global _comments_dumped
    if not logger.isEnabledFor(logging.DEBUG) or _comments_dumped:
        return
    try:
        html = page.evaluate(
            """() => {
                const c = document.querySelector('[data-testid*="commentList" i]')
                    || document.querySelector('main') || document.body;
                return c.outerHTML;
            }"""
        )
        out_path = config.PROJECT_ROOT / "output" / "debug_comments.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html or "", encoding="utf-8")
        _comments_dumped = True
        logger.debug(
            "Wrote comments DOM to %s (%d chars).", out_path, len(html or "")
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only.
        logger.debug("Comments DOM dump failed for %s: %s", post_url, exc)


def _open_reactions_modal(page: Page, post_url: str) -> bool:
    """Find and click the reactions button, then wait for the modal.

    Tries the comma-separated fallback selectors one at a time.

    Returns ``True`` once the reactions dialog is open, ``False`` otherwise
    (no opener found, or none of the candidates opened the dialog). Never
    raises — the caller falls back to the in-page facepile when this is False.
    """
    candidates = _find_reaction_buttons(page)
    if not candidates:
        logger.debug("No 'see all reactions' opener found for %s.", post_url)
        _dump_reactions_dom(page, post_url)
        return False

    # Try each candidate until one actually opens the reactions dialog. Wrong
    # candidates (e.g. a comments count) simply do not open a dialog, so we move
    # on rather than failing.
    for candidate in candidates:
        try:
            candidate.scroll_into_view_if_needed(timeout=2000)
            config.human_delay()
            # Short timeout: a non-visible / wrong candidate must fail fast
            # rather than blocking ~30s on Playwright's default click timeout.
            candidate.click(timeout=2500)
        except Exception as exc:  # noqa: BLE001 - candidate may be non-clickable.
            logger.debug("Reaction candidate click failed: %s", exc)
            continue
        try:
            page.wait_for_selector(SELECTORS["reactions_dialog"], timeout=6000)
            config.human_delay()
            return True
        except Exception:  # noqa: BLE001 - this candidate did not open a dialog.
            logger.debug("Candidate did not open the reactions dialog; trying next.")
            continue

    # Candidates existed but none opened the dialog. Don't fail hard: the caller
    # still extracts the in-page facepile. Dump the DOM (debug) for refinement.
    logger.debug(
        "Found %d reaction opener candidate(s) but none opened the dialog for %s.",
        len(candidates),
        post_url,
    )
    _dump_reactions_dom(page, post_url)
    return False


def _reveal_social_bar(page: Page) -> None:
    """Scroll the post's social action bar into view so it lazy-renders."""
    try:
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(min(config.SCROLL_PAUSE_MS, 1200))
        # If the counts container exists, bring it fully into view.
        bar = page.query_selector(".social-details-social-counts")
        if bar is not None:
            bar.scroll_into_view_if_needed()
        config.human_delay()
    except Exception as exc:  # noqa: BLE001 - non-fatal: detection continues.
        logger.debug("Revealing social bar failed: %s", exc)


def _find_reaction_buttons(page: Page) -> list[ElementHandle]:
    """Return reaction-opener candidates, in selector-priority order, deduped."""
    found: list[ElementHandle] = []
    for selector in _split_selector(SELECTORS["reactions_button"]):
        try:
            found.extend(page.query_selector_all(selector))
        except Exception as exc:  # noqa: BLE001 - skip a malformed selector.
            logger.debug("Reaction selector %r failed: %s", selector, exc)
    return found


def _dump_reactions_dom(page: Page, post_url: str) -> None:
    """Write the full post DOM to a file (DEBUG only) to aid selector fixes.

    LinkedIn now ships obfuscated, hashed class names, so the only way to derive
    correct selectors is to inspect real markup. The first ``_DUMP_LIMIT`` posts
    are saved (UTF-8) to ``output/debug_post_<n>.html`` — multiple files because
    the first post(s) may have zero reactions and thus no social-proof element.
    No-op unless DEBUG logging is enabled.
    """
    global _dump_count
    if not logger.isEnabledFor(logging.DEBUG) or _dump_count >= _DUMP_LIMIT:
        return
    try:
        html = page.evaluate(
            "() => (document.querySelector('main') || document.body).outerHTML"
        )
        _dump_count += 1
        out_path = config.PROJECT_ROOT / "output" / f"debug_post_{_dump_count}.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html or "", encoding="utf-8")
        logger.debug(
            "Wrote post DOM for %s to %s (%d chars).",
            post_url,
            out_path,
            len(html or ""),
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must never break a run.
        logger.debug("Post DOM dump failed for %s: %s", post_url, exc)


def _scroll_reactions_modal(page: Page) -> None:
    """Scroll the open reactions modal until the reactor count stabilizes.

    The modal lazy-loads reactors in pages (~10 at a time). Class names are
    obfuscated, so rather than target the list container we scroll every
    scrollable descendant of the dialog to the bottom, up to
    ``config.MAX_MODAL_SCROLLS`` times, stopping once the number of reactor
    entries stops growing for a few iterations.
    """
    dialog = page.query_selector(SELECTORS["reactions_dialog"])
    if dialog is None:
        return

    last_count = -1
    stale_scrolls = 0
    for _ in range(config.MAX_MODAL_SCROLLS):
        count = len(dialog.query_selector_all(SELECTORS["reactor_info"]))
        if count == last_count:
            stale_scrolls += 1
            if stale_scrolls >= 3:
                break
        else:
            stale_scrolls = 0
        last_count = count

        try:
            dialog.evaluate(
                """dlg => {
                    for (const el of [dlg, ...dlg.querySelectorAll('*')]) {
                        if (el.scrollHeight > el.clientHeight + 20) {
                            el.scrollTop = el.scrollHeight;
                        }
                    }
                }"""
            )
        except Exception as exc:  # noqa: BLE001 - keep trying remaining scrolls.
            logger.debug("Modal scroll step failed: %s", exc)

        page.wait_for_timeout(config.SCROLL_PAUSE_MS)
        config.human_delay()


def _dump_modal_dom(page: Page, post_url: str) -> None:
    """Write the open reactions-modal HTML to a file (DEBUG only, once).

    Lets the reactor-row name/headline extraction be refined against real modal
    markup. No-op unless DEBUG logging is enabled.
    """
    global _modal_dumped
    if not logger.isEnabledFor(logging.DEBUG) or _modal_dumped:
        return
    try:
        html = page.eval_on_selector(
            SELECTORS["reactions_dialog"], "el => el.outerHTML"
        )
        out_path = config.PROJECT_ROOT / "output" / "debug_modal.html"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html or "", encoding="utf-8")
        _modal_dumped = True
        logger.debug(
            "Wrote reactions modal DOM to %s (%d chars).", out_path, len(html or "")
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only.
        logger.debug("Modal DOM dump failed for %s: %s", post_url, exc)


def _split_selector(selector: str) -> list[str]:
    """Split a comma-separated CSS selector group into individual selectors.

    LinkedIn selectors bundle several fallbacks separated by commas. Querying
    them one at a time lets us pick the first that actually matches an element,
    which is more robust than relying on a single combined query.
    """
    return [part.strip() for part in selector.split(",") if part.strip()]
