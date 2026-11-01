"""Greenhouse public Job Board API adapter.

API: https://developers.greenhouse.io/job-board.html
- GET /v1/boards/{board_token}/jobs?content=true  (no auth; public listings only)
- GET /v1/boards/{board_token}/jobs/{id}          (404 once the job is removed)

This is a per-employer API: you configure which employers' boards to search
(board tokens), and keyword filtering happens locally.
"""

from __future__ import annotations

from datetime import datetime

from app.core.models import JobListing, PayPeriod, PayRange

from .base import Fetcher, JobSource, SearchQuery, SourceResult, SourceUnavailable, now_utc, raise_for_provider_status
from .normalize import build_listing, matches_query

API = "https://boards-api.greenhouse.io/v1/boards"


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pay(job: dict) -> PayRange | None:
    ranges = job.get("pay_input_ranges") or []
    if not ranges:
        return None
    r = ranges[0]
    lo, hi = r.get("min_cents"), r.get("max_cents")
    if lo is None and hi is None:
        return None
    # Greenhouse pay-transparency ranges are annual salaries unless the title says hourly.
    period = PayPeriod.HOUR if "hour" in (r.get("title") or "").lower() else PayPeriod.YEAR
    return PayRange(min=lo / 100 if lo is not None else None, max=hi / 100 if hi is not None else None,
                    period=period, currency=r.get("currency_type") or "USD")


class GreenhouseSource(JobSource):
    name = "greenhouse"
    terms_note = ("Greenhouse Job Board API: public, unauthenticated, read-only listings that employers "
                  "publish for candidates. Respect reasonable request rates.")

    def __init__(self, boards: dict[str, str]) -> None:
        """boards: {board_token: employer display name}"""
        self.boards = boards

    def search(self, query: SearchQuery, fetcher: Fetcher) -> SourceResult:
        listings: list[JobListing] = []
        errors: list[str] = []
        fetched = now_utc()
        for token, company in self.boards.items():
            try:
                resp = fetcher.get_json(f"{API}/{token}/jobs", {"content": "true", "pay_transparency": "true"})
                raise_for_provider_status(resp, f"Greenhouse ({company})")
                jobs = (resp.data or {}).get("jobs")
                if not isinstance(jobs, list):
                    raise SourceUnavailable(f"Greenhouse ({company}) returned an unexpected format.")
            except SourceUnavailable as e:
                errors.append(str(e))
                continue
            for j in jobs:
                try:
                    listing = build_listing(
                        source=self.name, source_job_id=str(j["id"]), url=j["absolute_url"],
                        apply_url=j["absolute_url"], title=j.get("title", ""),
                        company=j.get("company_name") or company,
                        location_text=(j.get("location") or {}).get("name", ""),
                        raw_description=j.get("content") or "", fetched_at=fetched,
                        posted_at=_dt(j.get("first_published")), updated_at=_dt(j.get("updated_at")),
                        pay=_pay(j),
                    )
                except (KeyError, TypeError, ValueError):
                    errors.append(f"Skipped a malformed Greenhouse posting from {company}.")
                    continue
                if matches_query(listing, query.keywords):
                    listings.append(listing.model_copy(update={"source_job_id": f"{token}/{j['id']}"}))
                if len(listings) >= query.limit:
                    break
        return SourceResult(source=self.name, listings=listings, errors=errors, fetched_at=fetched)

    def still_lists(self, job: JobListing, fetcher: Fetcher) -> bool | None:
        try:
            token, job_id = job.source_job_id.split("/", 1)
            resp = fetcher.get_json(f"{API}/{token}/jobs/{job_id}")
        except (ValueError, SourceUnavailable):
            return None
        if resp.status == 200:
            return True
        if resp.status == 404:
            return False
        return None
