"""One shared HTTP client for every source.

Why a wrapper instead of calling httpx directly?
  * Politeness: a descriptive User-Agent, and a minimum gap between requests to
    the same host (slower for hosts known to rate-limit bots, like Workday).
  * Reliability: retries with exponential backoff + jitter on timeouts, 429 and
    5xx responses, honoring the server's Retry-After header.
  * Honesty: if a site's bot protection challenges us (Cloudflare's
    "cf-mitigated" header), we stop immediately instead of hammering it, and the
    error says so clearly in the source-health panel.

Tests pass a fake `transport` so no real network is used.
"""

from __future__ import annotations

import logging
import random
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class BlockedError(RuntimeError):
    """The site's bot protection blocked us. Retrying won't help."""


class Http:
    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        max_retries: int = 3,
        min_delay: float = 1.0,
        per_host_delay: dict[str, float] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.max_retries = max_retries
        self.min_delay = min_delay
        self.per_host_delay = per_host_delay or {}
        self._sleep = sleep
        self._last_request: dict[str, float] = {}   # host -> time of last request
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    @classmethod
    def from_config(cls, cfg, **kwargs) -> "Http":
        return cls(
            user_agent=cfg.get("http.user_agent", "OpportunityRadar/0.1"),
            timeout=float(cfg.get("http.timeout_seconds", 30)),
            max_retries=int(cfg.get("http.max_retries", 3)),
            min_delay=float(cfg.get("http.min_delay_seconds", 1.0)),
            per_host_delay=cfg.get("http.per_host_delay_seconds", {}) or {},
            **kwargs,
        )

    # ------------------------------------------------------------------ public
    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs):
        return self.get(url, **kwargs).json()

    def get_text(self, url: str, **kwargs) -> str:
        return self.get(url, **kwargs).text

    def post_json(self, url: str, payload: dict, **kwargs):
        headers = {"Content-Type": "application/json", "Accept": "application/json", **kwargs.pop("headers", {})}
        return self.request("POST", url, json=payload, headers=headers, **kwargs).json()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        for attempt in range(self.max_retries + 1):
            self._wait_for_turn(host)
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:   # timeouts, DNS, connection resets
                if attempt == self.max_retries:
                    raise
                log.warning("%s %s failed (%s); retrying", method, url, exc)
                self._backoff(attempt)
                continue

            if response.headers.get("cf-mitigated") == "challenge":
                raise BlockedError(f"bot protection challenge from {host} (HTTP {response.status_code})")

            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                log.warning("%s %s -> HTTP %s; retrying", method, url, response.status_code)
                self._backoff(attempt, response.headers.get("Retry-After"))
                continue

            response.raise_for_status()
            return response
        raise RuntimeError("unreachable")  # the loop always returns or raises

    def close(self) -> None:
        self.client.close()

    # ----------------------------------------------------------------- helpers
    def _delay_for(self, host: str) -> float:
        """Minimum gap for this host: the most specific matching suffix wins."""
        delay = self.min_delay
        for suffix, seconds in self.per_host_delay.items():
            if host == suffix or host.endswith("." + suffix):
                delay = max(delay, float(seconds))
        return delay

    def _wait_for_turn(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self._delay_for(host) - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = time.monotonic()

    def _backoff(self, attempt: int, retry_after: str | None = None) -> None:
        """Exponential backoff (2s, 4s, 8s...) plus random jitter, capped at 2 min."""
        delay = min(60.0, 2.0 * (2**attempt)) + random.uniform(0, 1)
        server_delay = _parse_retry_after(retry_after)
        if server_delay is not None:
            delay = max(delay, server_delay)
        self._sleep(min(delay, 120.0))


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After can be a number of seconds or an HTTP date."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
        return max(0.0, moment.timestamp() - time.time())
    except (TypeError, ValueError):
        return None
