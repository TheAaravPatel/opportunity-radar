"""Downloading watched pages politely.

1. robots.txt: if the site asks bots not to fetch a page, we don't.
2. A plain HTTP request through the shared client (User-Agent, rate limits,
   retries - see radar/http.py).
3. Only if that page is (almost) empty - it's built by JavaScript - or the
   page entry says `render: playwright`, a headless Chromium browser
   (Playwright) loads it instead. The browser starts once, on first use.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from radar.http import Http
from radar.watcher.text import page_lines

log = logging.getLogger(__name__)

EMPTY_PAGE_CHARS = 300   # less visible text than this -> probably needs JavaScript


class RobotsDisallowed(RuntimeError):
    """robots.txt asks bots not to fetch this page."""


class PageFetcher:
    """Use as `with PageFetcher(http) as fetcher: html, how = fetcher.fetch(url, "auto")`."""

    def __init__(self, http: Http, user_agent: str | None = None):
        self.http = http
        self.user_agent = user_agent or http.client.headers.get("User-Agent", "OpportunityRadar")
        self._robots: dict[str, RobotFileParser | None] = {}
        self._playwright = None
        self._browser = None
        self.browser_error: str | None = None

    def __enter__(self) -> "PageFetcher":
        return self

    def __exit__(self, *exc) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    # ------------------------------------------------------------ robots.txt
    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                parser.parse(self.http.get_text(f"{origin}/robots.txt").splitlines())
            except httpx.HTTPStatusError:
                parser = None          # no robots.txt (404 etc.) -> everything allowed
            except Exception as exc:  # noqa: BLE001 - unreachable robots.txt: the page fetch will tell
                log.info("robots.txt for %s unavailable (%s)", origin, exc)
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self.user_agent, url)

    # ----------------------------------------------------------------- fetch
    def fetch(self, url: str, render: str = "auto") -> tuple[str, int, str]:
        """Returns (html, http_status, method) where method is "http" or "browser"."""
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt asks bots not to fetch {url}")
        if render == "playwright":
            return self._browser_fetch(url)
        response = self.http.get(url)
        html = response.text
        # Judge emptiness on the whole page: a job list can legitimately be short.
        if render == "auto" and sum(len(line) for line in page_lines(html)) < EMPTY_PAGE_CHARS:
            try:
                return self._browser_fetch(url)
            except Exception as exc:  # noqa: BLE001 - keep the plain result if the browser can't help
                self.browser_error = f"{type(exc).__name__}: {exc}"
                log.info("browser fetch of %s failed: %s", url, self.browser_error)
        return html, response.status_code, "http"

    def _browser_fetch(self, url: str) -> tuple[str, int, str]:
        if self._browser is None:
            # Imported here so the rest of the radar works without Playwright.
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        context = self._browser.new_context(user_agent=self.user_agent)
        try:
            page = context.new_page()
            response = page.goto(url, wait_until="networkidle", timeout=45_000)
            status = response.status if response else 0
            if status >= 400:
                raise RuntimeError(f"HTTP {status} from {url} (in the browser)")
            return page.content(), status, "browser"
        finally:
            context.close()
