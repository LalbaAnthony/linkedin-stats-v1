# linkedin-stats

A local Python 3.12+ command-line tool that analyses **who liked, commented on and reposted a LinkedIn company's posts** (a personal profile also works) within a given date range and exports a CSV ranking of the most active people (likes + comments + reposts).

> **Non-technical user?** Follow the step-by-step
> [USER_GUIDE.md](USER_GUIDE.md) instead — it covers installing and running the
> tool from scratch, no programming knowledge required.

## What it does

Given a LinkedIn author URL — primarily a **company page** (`/company/<slug>`),
or a personal profile (`/in/<id>`) — and a date range, the tool:

1. Opens your authenticated LinkedIn session in a real Chrome browser via
   Playwright (falls back to bundled Chromium).
2. Navigates to the author's posts feed (`/company/<slug>/posts/` for a company,
   `/in/<id>/recent-activity/all/` for a profile) and collects the posts that
   fall (approximately) within the requested date range.
3. Reads, per post, **who reposted it** directly from the feed — the only place
   LinkedIn exposes repost data — splitting each reshare into a **plain repost**
   and a **repost with an added comment** ("repost with your thoughts").
4. For each post, collects every reactor (from the full reactions list, falling
   back to the in-page facepile) **and** every commenter (from the comments
   section, loading more comments as needed). For each person it keeps the name,
   profile URL and headline.
5. Aggregates per person — counting **likes**, **comments**, **plain reposts**
   and **reposts with a comment** — and exports a ranking sorted by likes, then
   comments.

The final CSV contains the columns:
`name`, `profile_url`, `headline`, `likes_count`, `comments_count`,
`repost_count`, `repost_with_comment_count`.

Each count is per person: how many of the analysed posts that person liked /
commented on / reposted. A like or a repost is counted once per person per post
(you can react or reshare a post only once); a comment is counted once per
comment, so a person who comments several times on a post adds several to their
`comments_count`. `repost_count` and `repost_with_comment_count` are mutually
exclusive (a reshare is either plain or with a comment) and together they equal
the total number of times that person reshared the analysed posts. The ranking
order is driven by likes then comments; the repost columns are reported alongside
but do not change the sort.

A run summary is printed at the end:

```
Posts analysed: <n>
Posts skipped: <n>
Likes collected: <n>
Comments collected: <n>
Reposts collected: <n>
Reposts with comment collected: <n>
Unique people: <n>
CSV generated: <path>
```

## Limitations

Read these before relying on the output.

- **Approximate date filtering.** LinkedIn only exposes *relative* dates
  ("2mo", "1yr", "il y a 3 sem."). There is no exact post timestamp in the DOM,
  so date-range filtering is necessarily **approximate**: months are treated as
  30 days and years as 365 days. A post near a range boundary may be wrongly
  included or excluded.
- **Selector fragility (LinkedIn ships obfuscated class names).** LinkedIn's CSS
  classes are randomized hashes that change without notice, so the scraper anchors
  on durable hooks instead: `data-urn` (posts), `data-testid` and `aria-label`
  (reactions and comments), and `/in/` profile links. All selectors live in a
  single file (`src/selectors.py`). When LinkedIn changes its markup and scraping
  breaks, run with `--debug`: it writes the real DOM to `output/debug_post_<n>.html`,
  the open reactions modal to `output/debug_modal.html`, the comments section to
  `output/debug_comments.html`, each open reposts list to `output/debug_reposts_<n>.html`,
  and a feed card with / without a reposts opener to `output/debug_repost_card.html`
  / `output/debug_repost_card_no_opener.html`, from which the selectors can be
  corrected.
- **Reposts are collected from the feed and are best-effort.** LinkedIn shows
  repost data only on the feed (the per-post detail page hides it), so the tool
  re-opens the feed and opens each post's reposts list to read who reshared it.
  The split between a plain reshare and a reshare-with-a-comment is read from that
  list. If your LinkedIn renders the reposts opener differently, the repost
  columns may come back as `0`; run once with `--debug` and inspect/share the
  `output/debug_reposts_*.html` / `output/debug_repost_card.html` dumps so the
  selectors can be tuned. The tool never reshares a post on your behalf — it only
  ever opens the *list* of existing reposts, never the "Repost" action.
- **Engagement completeness depends on what loads.** Reactions: the tool opens
  each post's full reactions list; if it cannot, it falls back to the in-page
  facepile (top ~8 reactors, no headline). Comments: it loads as many comments
  (and replies) as it can before counting. If a list cannot be fully loaded, that
  post may be undercounted — logged as a warning.
- **Scroll depth vs. old date ranges.** The feed loads newest-first and the
  scraper scrolls a bounded number of times (`LINKEDIN_MAX_FEED_SCROLLS`,
  default 60). Targeting a date range far in the past on an active company feed
  may exceed that bound before reaching the requested posts — raise the env var
  (see [.env.example](.env.example)) if `Posts analysed` comes back lower than
  expected.
- **FR / EN user interface.** The LinkedIn UI may be in French or English
  depending on your account. Relative-date parsing and text matching handle
  both languages.
- **LinkedIn Terms of Service.** This tool automates a logged-in browser
  session. It is intended for **personal use** on your own account only. You are
  responsible for complying with LinkedIn's Terms of Service. Aggressive use may
  lead to rate limiting or account restrictions; the tool inserts random
  human-like delays to reduce that risk but cannot eliminate it.

## Requirements

- 64-bit **Python 3.12+** (recommended).
- **Google Chrome** (recommended): the tool uses your locally installed Chrome
  for the most reliable login — LinkedIn is more likely to reject an obviously
  automated browser. If Chrome is not installed it falls back to the Chromium
  that Playwright installs (see setup).
- Works on **Windows 11** and **Linux (Debian)**.

## Setup

Create and activate a virtual environment, then install dependencies and the
Chromium browser used by Playwright.

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

### Linux (Debian)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
```

Optionally copy `.env.example` to `.env` to override defaults (headless mode,
session/output paths, action delays, and scroll depth — raise
`LINKEDIN_MAX_FEED_SCROLLS` to reach posts further back in time).

## First run: manual login and session reuse

LinkedIn requires an authenticated session. On the **first run**, do **not** use
headless mode:

1. Run the tool (a visible browser is the default). Your Google Chrome opens on
   the LinkedIn login page.
2. Log in manually **in that window** (credentials + any 2FA / checkpoint).
3. Return to the terminal and **press ENTER** when prompted. The tool re-checks
   the feed, confirms the login, **saves the session** to
   `sessions/linkedin.json`, and continues.

Login is detected from the `li_at` session cookie / your feed URL, so it works
regardless of LinkedIn's UI language. On subsequent runs the saved session is
reused, so you normally won't need to log in again. When the session is valid
you may run with `--headless`. If the session expires, run once non-headless
again to refresh it.

## Usage

Company page (primary use case):

```bash
python main.py --author "https://www.linkedin.com/company/agoravita" --start 2024-01-01 --end 2024-03-31 --output output/results.csv
```

Personal profile (also supported):

```bash
python main.py --author "https://www.linkedin.com/in/some-author/" --start 2024-01-01 --end 2024-03-31
```

### Arguments

| Argument                       | Required | Default                                | Description                                                                                                                                                  |
| ------------------------------ | -------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--author`                     | yes      | —                                      | LinkedIn author URL: a company page (`/company/<slug>`) or a personal profile (`/in/<id>`).                                                                  |
| `--start`                      | yes      | —                                      | Start date (inclusive), format `YYYY-MM-DD`.                                                                                                                 |
| `--end`                        | yes      | —                                      | End date (inclusive), format `YYYY-MM-DD`.                                                                                                                   |
| `--output`                     | no       | `output/results.csv`                   | Path of the CSV file to generate (env: `LINKEDIN_OUTPUT_PATH`).                                                                                              |
| `--headless` / `--no-headless` | no       | env `LINKEDIN_HEADLESS` (else `false`) | Run the browser with/without a visible window. `--headless` requires a saved session; `--no-headless` forces a window even when the env default is headless. |
| `--debug`                      | no       | `false`                                | Enable verbose debug logging.                                                                                                                                |
| `--version`                    | no       | —                                      | Print the program version (e.g. `linkedin-stats 0.1.0`) and exit.                                                                                            |

`--start` must be less than or equal to `--end`.

## Project structure

```
linkedin-stats/
├── main.py                # CLI entry point + orchestration
├── requirements.txt
├── pyproject.toml         # Project metadata + semantic-release config
├── CHANGELOG.md           # Auto-generated by semantic-release (do not edit)
├── README.md
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── release.yml    # Automatic versioning/tagging/release on push to main
├── src/
│   ├── __init__.py        # __version__ (kept in sync by semantic-release)
│   ├── models.py          # Dataclasses (PostRef, RawPerson, PostEngagement, PostReposts, EngagementRecord)
│   ├── config.py          # Paths, delays, logging, dotenv, human_delay
│   ├── selectors.py       # SELECTORS dict (LinkedIn DOM selectors)
│   ├── parser.py          # Pure functions: date parsing, cleaning, records
│   ├── linkedin.py        # Browser/session lifecycle (Playwright)
│   ├── scraper.py         # DOM collection (posts, reactions + comments, reposts)
│   ├── statistics.py      # Aggregation / ranking
│   └── exporter.py        # CSV export via pandas
├── tests/
│   ├── __init__.py
│   ├── test_parser.py
│   ├── test_statistics.py
│   └── test_exporter.py
├── output/                # Generated CSV files (git-ignored)
└── sessions/              # Persisted browser sessions (git-ignored)
```

## Running tests

Tests are pure (no network, no browser). Run them from the **repository root**
so that the `src` package is importable:

```bash
pytest
```

## Versioning & releases

Versioning is **fully automatic** — nobody bumps a version, writes a changelog
or cuts a tag by hand. It is driven entirely by your
[Conventional Commits](https://www.conventionalcommits.org/) via
[python-semantic-release](https://python-semantic-release.readthedocs.io/).

On every push to `main`, the [release workflow](.github/workflows/release.yml)
inspects the commits since the last tag and, if a release is warranted:

1. computes the next version,
2. updates `version` in [pyproject.toml](pyproject.toml) and `__version__` in
   [src/__init__.py](src/__init__.py),
3. writes the entries to `CHANGELOG.md`,
4. commits that as `chore(release): vX.Y.Z [skip ci]`,
5. tags `vX.Y.Z` and publishes a **GitHub Release** with the changelog notes.

### What your commit messages do

Write commits as `type(scope): subject`. The **type** decides the bump:

| Commit prefix                                                | Version effect (while in `0.x`)    |
| ------------------------------------------------------------ | ---------------------------------- |
| `feat:`                                                      | minor — `0.1.0` → `0.2.0`          |
| `fix:` / `perf:`                                             | patch — `0.1.0` → `0.1.1`          |
| `feat!:` / `BREAKING CHANGE:` in body                        | minor while `0.x` (see note below) |
| `docs:` `style:` `refactor:` `test:` `chore:` `ci:` `build:` | no release                         |

Notes:

- Use the standard plural **`docs:`** (not `doc:`) and **`style:`** — a
  non-standard type is treated as "no release" and is left out of the changelog.
- A commit that does not parse as a conventional commit simply produces **no
  release** (a safe no-op), so a bad message never breaks the build.
- The project stays on `0.x` on purpose: breaking changes bump the **minor**
  version, not `1.0.0`. Cut `1.0.0` deliberately by setting
  `major_on_zero = true` in [pyproject.toml](pyproject.toml) once the CLI is
  stable.

### Requirements / gotchas

- The workflow uses the built-in `GITHUB_TOKEN` (no secret to configure). If you
  later **protect `main`** with required reviews, the token can no longer push
  the release commit — switch the workflow to a Personal Access Token secret, or
  allow the actions bot to bypass the protection.
- Check the version of the running CLI anytime with `python main.py --version`.
