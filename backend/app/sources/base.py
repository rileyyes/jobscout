"""Job source integration interface.

Every source is an *authorized* channel: a public job-board API, a licensed
provider, or a permitted feed. Network access goes through an injected
``Fetcher`` so adapters are testable offline and share one polite HTTP policy
(timeouts, user agent, per-host rate limit, no auth/CAPTCHA bypass).
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.core.models import JobListing


class SourceUnavailable(Exception):
    """The provider is down, rate-limiting us, or returned something unusable."""


@dataclass
class FetchResponse:
    status: int
    data: Any = None  # parsed JSON when requested
    final_url: str | None = None


class Fetcher(Protocol):
    def get_json(self, url: str, params: dict | None = None) -> FetchResponse: ...
    def check_url(self, url: str) -> FetchResponse: ...


@dataclass
class SearchQuery:
    keywords: list[str] = field(default_factory=list)
    location: str | None = None
    remote_ok: bool = True
    limit: int = 200


@dataclass
class SourceResult:
    source: str
    listings: list[JobListing]
    errors: list[str]
    fetched_at: datetime
    is_demo: bool = False


class JobSource(ABC):
    name: str = "base"
    is_demo: bool = False
    #: Human-readable terms/permission note shown in docs and the UI.
    terms_note: str = ""

    @abstractmethod
    def search(self, query: SearchQuery, fetcher: Fetcher) -> SourceResult: ...

    @abstractmethod
    def still_lists(self, job: JobListing, fetcher: Fetcher) -> bool | None:
        """True/False if the source can answer authoritatively, None if it can't."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class HttpxFetcher:
    """Production fetcher. Polite by default: identifies itself, honors a per-host
    minimum interval, never sends user credentials, follows a bounded number of redirects."""

    USER_AGENT = "JobScout/0.1 (personal job-search assistant; +https://github.com/rileyyes/jobscout)"

    def __init__(self, timeout: float = 15.0, min_interval_s: float = 1.0) -> None:
        import httpx  # imported lazily so the core stays dependency-light

        self._httpx = httpx
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, max_redirects=5,
                                    headers={"User-Agent": self.USER_AGENT})
        self._min_interval = min_interval_s
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def get_json(self, url: str, params: dict | None = None) -> FetchResponse:
        self._throttle(url)
        try:
            r = self._client.get(url, params=params, headers={"Accept": "application/json"})
        except self._httpx.HTTPError as e:
            raise SourceUnavailable(f"Network error contacting {urlsplit(url).netloc}: {type(e).__name__}") from e
        data = None
        if r.status_code == 200:
            try:
                data = r.json()
            except ValueError as e:
                raise SourceUnavailable(f"{urlsplit(url).netloc} returned invalid JSON") from e
        return FetchResponse(status=r.status_code, data=data, final_url=str(r.url))

    def check_url(self, url: str) -> FetchResponse:
        if urlsplit(url).scheme not in ("http", "https"):
            return FetchResponse(status=0)
        self._throttle(url)
        try:
            r = self._client.head(url)
            if r.status_code in (403, 405, 501):  # some ATSs reject HEAD
                r = self._client.get(url)
        except self._httpx.HTTPError:
            return FetchResponse(status=0)
        return FetchResponse(status=r.status_code, final_url=str(r.url))


def raise_for_provider_status(resp: FetchResponse, provider: str) -> None:
    if resp.status == 429:
        raise SourceUnavailable(f"{provider} is rate-limiting requests; try again later.")
    if resp.status >= 500:
        raise SourceUnavailable(f"{provider} is having problems (HTTP {resp.status}).")
    if resp.status != 200:
        raise SourceUnavailable(f"{provider} returned HTTP {resp.status}.")
