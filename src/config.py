"""Configuration, environment loading, delays and logging setup.

This module exposes module-level constants (overridable via environment
variables), plus a few small helpers. It depends only on the standard library
and, optionally, on ``python-dotenv`` (loaded lazily and gracefully skipped
when not installed).

It must NOT import other internal modules to keep the dependency graph acyclic.
"""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path

# python-dotenv is an optional dependency. Import it defensively so the package
# remains usable without it.
try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover - exercised only when dotenv is absent
    _load_dotenv = None


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable.

    Truthy values are ``1``, ``true``, ``yes`` (case-insensitive). Any other
    non-empty value is treated as ``False``. When the variable is unset, the
    provided default is returned.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable, falling back to ``default``.

    Invalid values are ignored (the default is used) rather than raising, so a
    malformed env var cannot crash the whole CLI at import time.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logging.getLogger("linkedin-stats").warning(
            "Invalid float for env var %s=%r; using default %s", name, raw, default
        )
        return default


def _env_int(name: str, default: int) -> int:
    """Read an int environment variable, falling back to ``default``.

    Invalid values are ignored (the default is used) rather than raising, so a
    malformed env var cannot crash the whole CLI at import time.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger("linkedin-stats").warning(
            "Invalid int for env var %s=%r; using default %s", name, raw, default
        )
        return default


def load_env() -> None:
    """Load variables from a local ``.env`` file when python-dotenv is present.

    Safe to call multiple times and a no-op when the dependency is missing.
    """

    if _load_dotenv is not None:
        _load_dotenv()


# Load .env (if available) before deriving env-based constants below so that a
# .env file influences the module constants on first import.
load_env()


# --- Paths -----------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Storage-state file used to persist a logged-in LinkedIn session.
SESSION_PATH: Path = Path(
    os.environ.get("LINKEDIN_SESSION_PATH", str(PROJECT_ROOT / "sessions" / "linkedin.json"))
)

# Default CSV output location. The CLI --output flag overrides this per run;
# LINKEDIN_OUTPUT_PATH overrides the default itself (symmetry with SESSION_PATH).
DEFAULT_OUTPUT: Path = Path(
    os.environ.get(
        "LINKEDIN_OUTPUT_PATH", str(PROJECT_ROOT / "output" / "results.csv")
    )
)


# --- Browser behaviour ------------------------------------------------------

# Run the browser headless by default? Login requires a visible browser at
# least once, so this defaults to False.
HEADLESS_DEFAULT: bool = env_bool("LINKEDIN_HEADLESS", False)


# --- Human-like delays ------------------------------------------------------

MIN_ACTION_DELAY: float = _env_float("LINKEDIN_MIN_DELAY", 1.0)
MAX_ACTION_DELAY: float = _env_float("LINKEDIN_MAX_DELAY", 2.5)


# --- Scrolling / scraping limits -------------------------------------------

# Pause between scroll steps (ms). Raise on slow connections.
SCROLL_PAUSE_MS: int = _env_int("LINKEDIN_SCROLL_PAUSE_MS", 1200)

# Max scroll steps on the author feed. A company that posts often, combined
# with a date range far in the past, can need many more than the default to
# reach old posts (the feed loads newest-first). Raise via the env var.
MAX_FEED_SCROLLS: int = _env_int("LINKEDIN_MAX_FEED_SCROLLS", 60)

# Max scroll steps inside the reactions modal (caps very-high-reaction posts).
MAX_MODAL_SCROLLS: int = _env_int("LINKEDIN_MAX_MODAL_SCROLLS", 80)

# How long to wait for the user to complete a manual login (milliseconds).
LOGIN_TIMEOUT_MS: int = _env_int("LINKEDIN_LOGIN_TIMEOUT_MS", 180000)


def human_delay(min_seconds: float | None = None, max_seconds: float | None = None) -> None:
    """Sleep for a random duration to mimic human interaction.

    When bounds are omitted the configured ``MIN_ACTION_DELAY`` /
    ``MAX_ACTION_DELAY`` are used. The bounds are normalized so that a
    ``min`` greater than ``max`` does not raise.
    """

    low = MIN_ACTION_DELAY if min_seconds is None else min_seconds
    high = MAX_ACTION_DELAY if max_seconds is None else max_seconds
    if low > high:
        low, high = high, low
    time.sleep(random.uniform(low, high))


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configure root logging and return the project logger.

    Uses level ``DEBUG`` when ``debug`` is True, otherwise ``INFO``. The format
    includes a timestamp and level. ``force=True`` ensures the configuration is
    applied even if logging was previously initialised elsewhere.
    """

    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    return logging.getLogger("linkedin-stats")
