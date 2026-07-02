# CLAUDE.md — linkedin-stats-v1

Local CLI that scrapes who **liked, commented on and reposted** a LinkedIn
**company's** posts (a personal profile also works) in a date range and exports a
CSV ranking (`likes_count` + `comments_count` + `repost_count` +
`repost_with_comment_count` per person). Playwright (Chrome/Chromium) + pandas.
No DB, no web UI, no network service. Personal/local use only. **Company pages
are the primary, canonical target**; `/in/` profile support is kept and must
keep working.

User-facing usage, setup, arguments and limitations live in [README.md](README.md).
This file is for working **in** the code.

## Commands

```bash
# Setup (use a 64-bit Python 3.12/3.13 venv — see the environment trap below)
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium

# Tests — run from the repo ROOT so `src` is importable
pytest

# Run the tool
python main.py --author "<profile-url>" --start YYYY-MM-DD --end YYYY-MM-DD
```

There is no linter/formatter. The only CI is the release workflow
(`.github/workflows/release.yml`, see **Versioning** below); there is no test/lint
gate. Hold the line manually: full type annotations on every function, English
code + comments, no `# TODO` in delivered code, no silently swallowed errors.

## Architecture: the pure / I/O split

The single most important invariant. **All DOM/browser/network I/O is confined
to `scraper.py` and `linkedin.py`.** Everything else is pure and unit-tested.

```
models.py, selectors.py, config.py  → no internal imports (config may use optional dotenv)
parser.py, statistics.py            → import models only          [PURE]
exporter.py                         → stdlib + pandas only        [PURE, no internal imports]
linkedin.py                         → config                      [I/O: browser/session]
scraper.py                          → config, selectors, parser, models   [I/O: DOM]
main.py                             → orchestrates everything
```

Rules when extending:
- **Never introduce an import that creates a cycle.** Keep `parser`/`statistics`/
  `exporter` free of any I/O or Playwright import.
- New parsing/normalization/aggregation logic goes in `parser.py` /
  `statistics.py` (pure, testable) — **not** inline in `scraper.py`. `scraper.py`
  extracts raw strings from the DOM and delegates all transformation to `parser`.
- **All LinkedIn selectors live in `src/selectors.py`** (`SELECTORS` dict). Never
  hardcode a DOM selector in `scraper.py` (`linkedin.py` queries no DOM — login is
  cookie/URL-based). Comma-separated values are intentional fallback lists,
  queried in priority order via `_split_selector`.

## Conventions that bite if ignored

- **Playwright sync API only** (`playwright.sync_api`). Do not introduce `async`/
  `await` or an event loop. Delays use `config.human_delay()` (blocking
  `time.sleep` with a random bound) between navigations/scrolls — keep new
  navigation steps paced the same way; it is the only anti-rate-limit measure.
- **Author resolution is centralized in `parser`.** `parser.author_kind(url)`
  classifies a URL into `"company"` (primary), `"profile"` or `None`;
  `parser.activity_url(url)` builds the matching feed (`/company/<slug>/posts/`
  or `/in/<id>/recent-activity/all/`). Both delegate to `_author_target` — keep
  them in sync. `main` rejects unsupported URLs up front. Reactors, however, are
  still **people**: `normalize_profile_url` keeps only `/in/` links, so the
  aggregation identity is a person's profile URL regardless of author kind.
- **Dates are approximate by construction.** LinkedIn exposes only relative
  labels ("2mo", "il y a 3 sem."), parsed in `parser.parse_relative_date` for
  **both FR and EN**. Footguns: month tokens (`mo`/`mois`) MUST match before
  minute (`m`) in the regex; seconds collapse to the reference instant;
  `in_range` compares by **date only** with an inclusive end day. Any change here
  must keep `tests/test_parser.py` green (it pins these edge cases).
- **`scraper.collect_post_engagement` is the per-post entry point.** It visits a
  post ONCE and returns a `PostEngagement(likers, commenters)`, or `None` only
  when the post itself could not be loaded (→ counts as skipped in `main`). Likers
  are deduped by profile (one reaction per person per post); commenters are
  **not** deduped (one `RawPerson` per comment, so multi-comment authors count
  multiple times). Reactor and comment collection are each best-effort and never
  abort the post.
- **Four engagement kinds flow through one record type.** `parser.build_records(
  post, people, kind)` emits `EngagementRecord`s tagged `KIND_LIKE`/`KIND_COMMENT`/
  `KIND_REPOST`/`KIND_REPOST_COMMENT`; `statistics.aggregate` splits them into
  per-profile `likes_count` + `comments_count` + `repost_count` +
  `repost_with_comment_count`. Aggregation identity key is the normalized profile
  URL (`person_profile`), not the display name. Names are display-only and
  de-duplicated/cleaned in `parser.clean_name`. The two repost kinds are mutually
  exclusive (a reshare is plain OR with a comment) and deduped by profile per post.
- **Reposts come ONLY from the feed; `scraper.collect_reposts` is their entry
  point.** The post detail page hides repost data, so `collect_reposts(page,
  author_url, posts)` re-opens the feed, relocates each post card by activity URN,
  opens its reposts LIST and reads the reposters, returning `{post_url:
  PostReposts(reposters, reposters_with_comment)}`. It is an isolated, best-effort
  pass: any failure yields no repost data for that post and never regresses the
  likes/comments flow. `collect_posts_in_range` and `collect_post_engagement` are
  intentionally untouched by reposts. **HARD SAFETY INVARIANT:** never click the
  `Republier`/`Repost` composer action button (that reshares the post as the
  user). Only a dedicated reposts-LIST opener is clicked, gated by the pure
  `parser.is_safe_reposts_opener`: it tokenizes the aria-label into words, REJECTS
  any candidate containing a composer verb word (`republier`/`repost`/`partager`/
  …) even when it also carries a count ("Republier 14"), and requires a positive
  reposts-list noun (`republications`/`reposts`/`partages`) before allowing a
  click. The pure URL helper `parser.urn_from_post_url` (inverse of the feed-update
  permalink) maps cards back to posts. The reposts list opens as LinkedIn's Ember
  **`artdeco-modal-overlay`** (NOT the native `<dialog>` the reactions modal uses —
  confirmed from a live run), so `reposts_dialog` matches both and the loop must
  `_dismiss_open_overlays` (Escape → close button → native `close()`) before AND
  after each click: it does not re-navigate per card, so a leaked overlay would
  otherwise intercept every later opener click (observed as a cascade of timeouts).
  `_visible_overlay` is used to skip hidden persistent modal containers. The repost
  loop tracks feed *growth* (not pending count) for early-stop. Repost rows are
  built in `main` via the same `build_records` path as likes/comments.
- **CSV is written `utf-8-sig`** (BOM) for Excel + accents, fixed column order
  `name, profile_url, headline, likes_count, comments_count, repost_count,
  repost_with_comment_count`, sorted `likes_count` DESC then `comments_count` DESC
  (reposts are reported but DO NOT affect sort order). Empty ranking → header-only
  file. Every string cell is whitespace-collapsed at export (`exporter._sanitize`)
  so no field contains an embedded newline (headlines otherwise wrap across rows
  in Excel).
- **Defensive scraping**: per-item extraction is wrapped so one bad node never
  aborts a run; errors are **logged, never silently swallowed**. Session lifecycle
  in `linkedin.py` must not leak the Chromium process (see `__enter__`/`close`).
- **Browser = local Google Chrome, de-fingerprinted.** `linkedin.start()` launches
  the installed Chrome (`channel="chrome"`, falling back to bundled Chromium) with
  `--disable-blink-features=AutomationControlled` and a desktop UA. LinkedIn bounces
  obviously-automated browsers back to `/login`, so do not drop these.
- **Login = manual, ENTER-confirmed.** On first run the user logs in in the visible
  window, then presses ENTER in the terminal (`linkedin._manual_login`), which
  re-navigates to `/feed` and verifies. `is_logged_in` is signal-based — the `li_at`
  cookie **or** an authenticated app URL (`/feed`, `/messaging`, …) on **any** tab —
  never a CSS hook; do not regress it. Non-interactive stdin falls back to polling.
- **LinkedIn class names are obfuscated hashes — anchor only on durable hooks.**
  Posts: `data-urn`. Facepile/modal: `data-testid`. Reactors / opener / post time:
  `aria-label`. Reactor identity: `href*="/in/"`. **Never select on hashed classes.**
  The reactions modal is a NATIVE `<dialog open>` (not `div[role="dialog"]`), opened
  via the `aria-label="Voir toutes les réactions"` summary. Reactors are read from
  `reactor_info` aria-labels ("<Name> a réagi avec <Type>[, degree, headline]") with
  `parser.reactor_name_from_aria` / `reactor_headline_from_aria`; the in-page facepile
  (`data-testid^="ReactionFacepileCollection"`, top ~8, no headline) is the fallback.
  Both sources are merged and deduped by profile, so a post is not capped at 8 likers.
- **Comments are read the same aria-label way.** Each comment's "more options"
  control carries `aria-label="…commentaire de <Name>"` (FR) / "…<Name>'s comment"
  (EN) — `selectors["comment_author_info"]`, one per comment. `parser.commenter_
  name_from_aria` extracts the name; the author profile is the nearest enclosing
  comment's first `/in/` link. `selectors["load_more_comments"]` expands comments
  /replies and is deliberately narrow so it NEVER clicks `J'aime`/`Répondre`/
  `Commenter` (which would react or open an editor as the user).
- **Scroll/limit knobs are env-tunable** via `config._env_int`
  (`LINKEDIN_MAX_FEED_SCROLLS`, `LINKEDIN_MAX_MODAL_SCROLLS`,
  `LINKEDIN_SCROLL_PAUSE_MS`, `LINKEDIN_LOGIN_TIMEOUT_MS`). The feed is
  newest-first, so a far-past date range on an active company feed needs a
  higher `MAX_FEED_SCROLLS` to reach old posts.

## Versioning & releases (automatic — do not hand-edit version strings)

- **The version is machine-managed by python-semantic-release (PSR), driven by
  Conventional Commits.** Config lives in `[tool.semantic_release]` in
  `pyproject.toml`; the release runs in `.github/workflows/release.yml` on every
  push to `main`. PSR computes the next semver from the commits since the last
  tag, rewrites the version in **two** places, regenerates `CHANGELOG.md`, commits
  `chore(release): vX.Y.Z [skip ci]`, tags `vX.Y.Z` and publishes a GitHub Release.
- **Two version locations, kept in lockstep by PSR — never edit either by hand:**
  `pyproject.toml` `[project].version` (`version_toml`) and `src/__init__.py`
  `__version__` (`version_variables`). `main.py` imports `__version__` for the
  `--version` flag; `src/__init__.py` must stay import-free so that stays cheap and
  cycle-free (it is the one module with no internal imports besides
  models/selectors/config).
- **Bump rules** (parser = `conventional`): `feat:` → minor, `fix:`/`perf:` →
  patch, breaking (`feat!:` or `BREAKING CHANGE:` footer) → **minor while in 0.x**
  because `major_on_zero = false` + `allow_zero_version = true`. `docs:`/`style:`/
  `refactor:`/`test:`/`chore:`/`ci:`/`build:` → no release. A non-conventional or
  unknown-type message (e.g. the legacy `doc:`/`style` without colon) is a silent
  no-op — no bump, absent from the changelog. Use the plural `docs:`.
- **`build_command = ""` on purpose** — this is a local CLI, not a distributable
  package, so PSR must not try to build a wheel/sdist (there is no `[build-system]`).
  `upload_to_vcs_release = true` attaches the changelog as the GitHub Release notes;
  there are no artifacts to upload, so the `publish-action` step is effectively a
  no-op kept only for forward-compat.
- **Gotchas:** PSR pushes the release commit with the built-in `GITHUB_TOKEN`; if
  `main` becomes a protected branch with required reviews, that push fails — switch
  to a PAT secret or grant the bot a bypass. The action/`publish-action` refs are
  pinned to `@v10.5.3`; bump both together. Validate config changes offline with
  `semantic-release -c pyproject.toml --noop version --print` in a throwaway venv.

## Testing

- Tests are **pure** (no network, no browser) and live in `tests/`. Run from the
  repo root. Add tests alongside any change to a pure module — `parser` and
  `statistics` especially.
- `tests/test_exporter.py` guards with `pytest.importorskip("pandas")`, so it is
  skipped when pandas is absent (e.g. on the wrong interpreter — see below).
- **When scraping breaks (LinkedIn changed its markup), run with `--debug`:** it
  writes the post DOM to `output/debug_post_<n>.html` (first 3 posts), the open
  reactions modal to `output/debug_modal.html`, the comments section to
  `output/debug_comments.html`, each open reposts list to `output/debug_reposts_<n>.html`
  (numbered so modals with a mix of plain and quote reshares can be inspected, not
  just the first), the first card that exposes a reposts opener to `output/debug_repost_card.html`,
  and the first card without one to `output/debug_repost_card_no_opener.html`.
  Re-derive selectors from those real dumps — do not guess. This is how the current
  aria-label/`data-testid` hooks were found. The DOM modules can't be unit-tested,
  so this is the verification loop.
- **Reposter extraction is scoped per entry (confirmed for quote reshares).** Each
  repost entry in the modal carries its own activity `data-urn` / `role="article"`
  (`repost_entry`) and a quote reshare EMBEDS the original post it quotes
  (`repost_embedded_original`, e.g. `update-components-mini-update`). A single
  quote entry holds ~11 `/in/` links — original author, its reaction facepile, its
  commenters — of which only ONE is the reposter, so `_extract_reposters` reads
  **one reposter per entry**: the first `/in/` link OUTSIDE the embedded original,
  named from the non-hashed `update-components-actor__title` (`reposter_name`).
  Reading every `/in/` link in the modal instead (an earlier bug) miscounts the
  embedded post's commenters/reactors as reposters — do not regress this scoping.
- **With/without-comment split: a comment is counted only when an embedded-original
  wrapper is present.** A quote reshare bounds the original in
  `repost_embedded_original`, so the reposter's own body text
  (`update-components-text`, `repost_comment_marker`) sits OUTSIDE it. A PLAIN
  reshare renders the original without that wrapper, so without the guard its body
  text is misread as the reposter's comment (every plain reshare wrongly tagged
  with-comment — the symptom that surfaced live). The plain-reshare entry markup
  (and that plain-reshare reposter identity/naming still resolves) is being
  confirmed from numbered `output/debug_reposts_<n>.html` captures.

## Environment trap (real, non-obvious)

The machine's default `python` is **32-bit Python 3.14** — there are **no
`pandas`/`playwright` wheels** for it, so `pip install -r requirements.txt`
fails and `pytest` only runs the parser/statistics suites (exporter skipped).
Always work in a **64-bit Python 3.12 or 3.13** venv. If pandas/playwright
imports fail, suspect the interpreter first.
