"""Polite HTTP client: one User-Agent, per-host rate limiting, robots.txt (RFC 9309) and retry with backoff."""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


@dataclass
class FetchResult:
    status: int
    data: Any = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.data is not None


class HttpClient:
    def __init__(self, user_agent: str, min_delay: float = 1.0, timeout: float = 30, retries: int = 3, pool: int = 8):
        self.user_agent = user_agent
        self.agent_token = user_agent.split("/")[0].strip() or "*"
        self.min_delay = max(0.2, float(min_delay))
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._registry_lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}
        self._next_slot: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None | bool] = {}
        self.request_count = 0

    def _lock_for(self, key: str) -> threading.Lock:
        with self._registry_lock:
            return self._host_locks.setdefault(key, threading.Lock())

    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None | bool:
        """Parser if robots.txt exists, None if there are no rules, False if the host must be avoided this run."""
        lock = self._lock_for("robots:" + host)
        with lock:
            if host in self._robots:
                return self._robots[host]
            url = f"{scheme}://{host}/robots.txt"
            result: RobotFileParser | None | bool
            try:
                self._throttle(host)
                r = self.session.get(url, timeout=self.timeout, headers={"Accept": "text/plain"})
                self.request_count += 1
                if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                    result = rp
                    delay = rp.crawl_delay(self.agent_token)
                    if delay:
                        self._delay[host] = max(self.min_delay, float(delay))
                elif 400 <= r.status_code < 500 or r.status_code == 200:
                    # RFC 9309 2.3.1.3: an "unavailable" robots.txt (4xx) means no crawl restrictions.
                    result = None
                else:
                    # RFC 9309 2.3.1.4: "unreachable" (5xx) means assume complete disallow.
                    log.warning("robots.txt for %s returned %s; skipping host this run", host, r.status_code)
                    result = False
            except requests.RequestException as exc:
                log.warning("robots.txt for %s unreachable (%s); skipping host this run", host, exc)
                result = False
            self._robots[host] = result
            return result

    def _throttle(self, host: str) -> None:
        with self._lock_for("rate:" + host):
            delay = self._delay.get(host, self.min_delay)
            wait = self._next_slot.get(host, 0.0) - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_slot[host] = time.monotonic() + delay

    def get_json(self, url: str, params: dict | None = None) -> FetchResult:
        return self._get(url, params, as_json=True)

    def get_text(self, url: str, params: dict | None = None,
                 accept: str = "application/rss+xml, application/xml, text/xml, */*") -> FetchResult:
        """Like get_json but returns raw bytes (RSS/XML, or an HTML page for the link importer)."""
        return self._get(url, params, as_json=False, accept=accept)

    def _get(self, url: str, params: dict | None, as_json: bool, accept: str = "") -> FetchResult:
        parts = urlsplit(url)
        robots = self._robots_for(parts.scheme, parts.netloc)
        if robots is False:
            raise RobotsDisallowed(f"robots.txt for {parts.netloc} could not be read")
        if robots is not None and not robots.can_fetch(self.agent_token, url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")

        last_error = ""
        for attempt in range(self.retries + 1):
            self._throttle(parts.netloc)
            try:
                headers = None if as_json else {"Accept": accept}
                r = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
                self.request_count += 1
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = type(exc).__name__
                if attempt < self.retries:
                    self._backoff(attempt, None)
                    continue
                return FetchResult(0, None, f"{last_error} after {attempt + 1} attempts")
            if r.status_code in RETRY_STATUSES and attempt < self.retries:
                self._backoff(attempt, r.headers.get("Retry-After"))
                continue
            if r.status_code != 200:
                return FetchResult(r.status_code, None, f"HTTP {r.status_code}")
            if not as_json:
                return FetchResult(200, r.content)
            try:
                return FetchResult(200, r.json())
            except ValueError:
                return FetchResult(200, None, "Response was not JSON")
        return FetchResult(0, None, last_error or "Request failed")

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> None:
        try:
            wait = float(retry_after) if retry_after else 0.0
        except ValueError:
            wait = 0.0
        wait = wait or min(30.0, 2 ** attempt + random.random())
        time.sleep(min(wait, 60.0))
