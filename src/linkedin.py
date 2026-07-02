"""LinkedIn browser/session lifecycle using the Playwright synchronous API.

This module owns the Chromium browser lifecycle, the persistent
``storage_state`` session (so the user logs in only once), and the
manual first-run login flow. It exposes :class:`LinkedInClient`, which
can be used either explicitly (``start`` / ``ensure_login`` / ``close``)
or as a context manager.

Selectors used for login-state detection live in :mod:`src.selectors`.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

from src import config

logger = logging.getLogger("linkedin-stats")

# LinkedIn entry points used for navigation and login-state checks.
FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"

# How often we poll for a completed manual login, in milliseconds (used only by
# the non-interactive fallback; the interactive flow waits on an ENTER press).
_LOGIN_POLL_INTERVAL_MS = 2000

# LinkedIn actively rejects logins coming from obviously-automated browsers,
# which can silently bounce the login form back to /login. Reduce the most
# obvious fingerprints: hide the AutomationControlled flag and present a normal
# desktop Chrome user agent.
_AUTOMATION_ARGS = ["--disable-blink-features=AutomationControlled"]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class LinkedInClient:
    """Manage a Chromium session against LinkedIn with a persistent login.

    The session is stored on disk as a Playwright ``storage_state`` JSON
    file so that subsequent runs reuse the authenticated cookies and do
    not require logging in again.
    """

    def __init__(
        self,
        headless: bool | None = None,
        session_path: Path | None = None,
    ) -> None:
        """Initialise the client.

        Args:
            headless: Run Chromium headless. Defaults to
                :data:`config.HEADLESS_DEFAULT` when ``None``.
            session_path: Path to the ``storage_state`` JSON file.
                Defaults to :data:`config.SESSION_PATH` when ``None``.
        """
        self.headless: bool = (
            config.HEADLESS_DEFAULT if headless is None else headless
        )
        self.session_path: Path = (
            config.SESSION_PATH if session_path is None else session_path
        )

        # Playwright resources, populated by start() and torn down by close().
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self.page: Page | None = None

    def start(self) -> "LinkedInClient":
        """Launch Chromium and create a page.

        If a session file already exists it is loaded as the browser
        context ``storage_state`` so the user stays logged in. Missing
        session files are handled gracefully (a fresh context is created).

        Returns:
            This client instance, to allow fluent chaining.
        """
        logger.debug(
            "Starting Playwright (headless=%s, session_path=%s)",
            self.headless,
            self.session_path,
        )
        self._playwright = sync_playwright().start()

        # Prefer the locally installed Google Chrome (channel="chrome") over the
        # bundled Chromium: LinkedIn trusts it more, which avoids login bounces.
        # Fall back to bundled Chromium when Chrome is not installed.
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless, channel="chrome", args=_AUTOMATION_ARGS
            )
            logger.debug("Launched local Google Chrome.")
        except Exception:
            logger.debug("Chrome channel unavailable; using bundled Chromium.")
            self._browser = self._playwright.chromium.launch(
                headless=self.headless, args=_AUTOMATION_ARGS
            )

        context_kwargs: dict[str, object] = {
            "user_agent": _USER_AGENT,
            "viewport": {"width": 1280, "height": 900},
        }
        # Reuse a previously saved session when available; otherwise start
        # fresh. We do not pre-create the file: a missing session simply
        # means the user has not logged in yet.
        if self.session_path.exists():
            logger.debug("Loading existing session from %s", self.session_path)
            context_kwargs["storage_state"] = str(self.session_path)
        else:
            logger.debug("No session file found; starting a fresh context")

        self._context = self._browser.new_context(**context_kwargs)
        self.page = self._context.new_page()
        return self

    def ensure_login(self) -> None:
        """Ensure the current session is authenticated.

        Navigates to the feed and checks the login state. When not
        logged in:

        * In headless mode, raises :class:`RuntimeError` instructing the
          user to run once in non-headless mode to authenticate.
        * Otherwise, prints clear manual-login instructions, opens the
          login page and polls (up to :data:`config.LOGIN_TIMEOUT_MS`)
          until the login completes, then persists the session.

        Raises:
            RuntimeError: If the client has not been started, if login is
                required in headless mode, or if the login times out.
        """
        if self.page is None:
            raise RuntimeError("Client not started; call start() first.")

        self.page.goto(FEED_URL, wait_until="domcontentloaded")
        config.human_delay()

        if self.is_logged_in():
            logger.info("Existing LinkedIn session is valid.")
            return

        if self.headless:
            raise RuntimeError(
                "Not logged in to LinkedIn and running headless. "
                "Run the tool once WITHOUT --headless to log in manually; "
                "the session will then be saved and reused."
            )

        # Manual login flow. We intentionally use print() here (rather than
        # logging) so the instruction block is unmistakably visible to the
        # user in the terminal.
        self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("=" * 70)
        print("LinkedIn login required.")
        print(
            "Log in to LinkedIn IN THE BROWSER WINDOW THIS TOOL JUST OPENED\n"
            "(not your normal browser) and complete any 2FA / verification."
        )
        print("=" * 70)

        self._manual_login()
        self.save_session()
        logger.info("Login successful; session saved to %s", self.session_path)

    def _manual_login(self) -> None:
        """Drive the manual login, confirming completion explicitly.

        Prefers an interactive confirmation: the user logs in, presses ENTER in
        the terminal, and we then re-navigate our own page to the feed to verify
        authoritatively (independent of which tab the login landed in). Falls
        back to timed polling when stdin is not interactive (e.g. piped input).

        Raises:
            RuntimeError: If login cannot be confirmed.
        """
        attempts = 3
        for attempt in range(1, attempts + 1):
            try:
                input(
                    "\nWhen you have logged in, return here and press ENTER "
                    "to continue... "
                )
            except EOFError:
                # Non-interactive stdin: there is no ENTER to wait for, so fall
                # back to polling the login state until the timeout.
                logger.info("Non-interactive stdin; polling for login instead.")
                self._wait_for_login()
                return

            # Authoritative check: force OUR page to the feed and see if the
            # session sticks (logged-out users are bounced to /authwall/login).
            try:
                if self.page is not None:
                    self.page.goto(FEED_URL, wait_until="domcontentloaded")
                    config.human_delay()
            except Exception:
                logger.debug("Re-navigation to feed failed", exc_info=True)

            if self.is_logged_in():
                logger.info("Login detected.")
                return

            print(
                f"Login not detected yet (attempt {attempt}/{attempts}). "
                "Make sure you completed the login in the tool's own browser "
                "window, then press ENTER again."
            )

        raise RuntimeError(
            "Could not confirm a LinkedIn login (last page: "
            f"{self._safe_url()}). Be sure to log in inside the browser window "
            "this tool opens. Run with --debug for detection details."
        )

    def _wait_for_login(self) -> None:
        """Poll until the user is logged in or the timeout elapses.

        Raises:
            RuntimeError: If the login is not completed within
                :data:`config.LOGIN_TIMEOUT_MS`.
        """
        deadline = time.monotonic() + (config.LOGIN_TIMEOUT_MS / 1000)
        poll_seconds = _LOGIN_POLL_INTERVAL_MS / 1000
        next_progress = time.monotonic()

        while time.monotonic() < deadline:
            if self.is_logged_in():
                return

            now = time.monotonic()
            if now >= next_progress:
                logger.info(
                    "Waiting for login... (%ds left, current page: %s)",
                    int(deadline - now),
                    self._safe_url(),
                )
                next_progress = now + 15

            time.sleep(poll_seconds)

        raise RuntimeError(
            "Timed out waiting for manual LinkedIn login (last page: "
            f"{self._safe_url()}). Re-run and complete the login; if you reach "
            "your feed but it still times out, run with --debug to see the "
            "login-detection details."
        )

    def _safe_url(self) -> str:
        """Return the current page URL, or ``"?"`` if it cannot be read."""
        try:
            return self.page.url if self.page is not None else "?"
        except Exception:
            return "?"

    def is_logged_in(self) -> bool:
        """Return whether the current session is authenticated.

        Two independent positive signals are used so detection is robust to
        LinkedIn markup changes, the FR/EN UI, and any single API hiccup:

        1. **URL** — *any* tab in the context sitting on an authenticated-only
           app surface (``/feed``, ``/mynetwork``, ``/messaging``,
           ``/notifications``). Scanning every tab (not just ``self.page``)
           covers logins that land in a freshly opened tab. LinkedIn bounces
           logged-out users away from these to ``/authwall`` / ``/login``.
        2. **Cookie** — the ``li_at`` auth cookie, set only after a successful
           login (context-wide, so it is independent of which tab was used).

        Either signal is sufficient. Each sub-check is isolated so an exception
        in one (e.g. a cookie read racing a navigation) cannot mask the other.

        Returns:
            ``True`` if logged in, ``False`` otherwise.
        """
        if self._context is None and self.page is None:
            return False

        # Collect URLs from every tab in the context (fall back to self.page).
        urls: list[str] = []
        pages = list(self._context.pages) if self._context is not None else []
        if not pages and self.page is not None:
            pages = [self.page]
        for pg in pages:
            try:
                current = (pg.url or "").lower()
                if current:
                    urls.append(current)
            except Exception:
                continue

        app_markers = ("/feed", "/mynetwork", "/messaging", "/notifications")
        on_app_surface = any(marker in u for u in urls for marker in app_markers)

        has_li_at = False
        if self._context is not None:
            try:
                has_li_at = any(
                    cookie.get("name") == "li_at" and bool(cookie.get("value"))
                    for cookie in self._context.cookies()
                )
            except Exception:
                logger.debug("is_logged_in: cookie read failed", exc_info=True)

        logger.debug(
            "is_logged_in: urls=%s on_app=%s li_at=%s",
            urls,
            on_app_surface,
            has_li_at,
        )
        return on_app_surface or has_li_at

    def save_session(self) -> None:
        """Persist the current browser context to the session file.

        Creates parent directories as needed.

        Raises:
            RuntimeError: If the client has not been started.
        """
        if self._context is None:
            raise RuntimeError("Client not started; nothing to save.")

        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self._context.storage_state(path=str(self.session_path))
        logger.debug("Session saved to %s", self.session_path)

    def new_page(self) -> Page:
        """Open and return a new page in the current browser context.

        Returns:
            The newly created :class:`~playwright.sync_api.Page`.

        Raises:
            RuntimeError: If the client has not been started.
        """
        if self._context is None:
            raise RuntimeError("Client not started; call start() first.")
        return self._context.new_page()

    def close(self) -> None:
        """Tear down the page, context, browser and Playwright driver.

        Safe to call multiple times; each resource is guarded against
        being ``None`` and reset afterwards. Errors during teardown are
        logged but do not propagate, so cleanup is best-effort.
        """
        for label, closer in (
            ("context", self._context),
            ("browser", self._browser),
        ):
            if closer is not None:
                try:
                    closer.close()
                except Exception:
                    logger.warning("Error closing %s", label, exc_info=True)

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                logger.warning("Error stopping Playwright", exc_info=True)

        self.page = None
        self._context = None
        self._browser = None
        self._playwright = None

    def __enter__(self) -> "LinkedInClient":
        """Start the browser and ensure an authenticated session.

        Returns:
            This client instance.
        """
        self.start()
        try:
            self.ensure_login()
        except Exception:
            # __exit__ is not invoked when __enter__ raises, so tear down the
            # browser/driver started above to avoid leaking the Chromium process.
            self.close()
            raise
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Save the session (best-effort) and close all resources."""
        if self._context is not None:
            try:
                self.save_session()
            except Exception:
                logger.warning("Error saving session on exit", exc_info=True)
        self.close()
