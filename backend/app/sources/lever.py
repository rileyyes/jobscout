"""Lever public Postings API adapter.

API: https://github.com/lever/postings-api
- GET https://api.lever.co/v0/postings/{site}?mode=json      (published postings, no auth)
- GET https://api.lever.co/v0/postings/{site}/{id}?mode=json (404 when unpublished)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import JobListing, PayPeriod, PayRange

from .base import Fetcher, JobSource, SearchQuery, SourceResult, SourceUnavailable, now_utc, raise_for_provider_status
from .normalize import build_listing, matches_query

API = "https://api.lever.co/v0/postings"

_INTERVALS = {
    "per-year-salary": PayPeriod.YEAR,
    "per-month-salary": PayPeriod.MONTH,
    "per-week-salary": PayPeriod.WEEK,
    "per-hour-wage": PayPeriod.HOUR,
}


def _pay(p: dict) -> PayRange | None:
    sr = p.get("salaryRange") or {}
    period = _INTERVALS.get(sr.get("interval", ""))
    if period is None or (sr.get("min") is None and sr.get("max") is None):
        return None  # per-day or one-time pay isn't safely annualizable; fall back to text parsing
    return PayRange(min=sr.get("min"), max=sr.get("max"), period=period, currency=sr.get("currency") or "USD")


def _description(p: dict) -> str:
    parts = [p.get("descriptionPlain") or p.get("description") or ""]
    for lst in p.get("lists") or []:
        parts.append(f"\n{lst.get('text', '')}\n{lst.get('content', '')}")
    parts.append(p.get("additionalPlain") or p.get("additional") or "")
    if p.get("salaryDescriptionPlain"):
        parts.append(p["salaryDescriptionPlain"])
    return "\n".join(parts)


class LeverSource(JobSource):
    name = "lever"
    terms_note = ("Lever Postings API: public, unauthenticated access to postings employers publish on "
                  "their Lever job sites. Respect reasonable request rates.")

    def __init__(self, sites: dict[str, str]) -> None:
        """sites: {lever site slug: employer display name}"""
        self.sites = sites

    def search(self, query: SearchQuery, fetcher: Fetcher) -> SourceResult:
        listings: list[JobListing] = []
        errors: list[str] = []
        fetched = now_utc()
        for site, company in self.sites.items():
            try:
                resp = fetcher.get_json(f"{API}/{site}", {"mode": "json"})
                raise_for_provider_status(resp, f"Lever ({company})")
                if not isinstance(resp.data, list):
                    raise SourceUnavailable(f"Lever ({company}) returned an unexpected format.")
            except SourceUnavailable as e:
                errors.append(str(e))
                continue
            for p in resp.data:
                try:
                    cats = p.get("categories") or {}
                    created = p.get("createdAt")
                    listing = build_listing(
                        source=self.name, source_job_id=f"{site}/{p['id']}", url=p["hostedUrl"],
                        apply_url=p.get("applyUrl") or p["hostedUrl"], title=p.get("text", ""),
                        company=company, location_text=cats.get("location") or ", ".join(cats.get("allLocations") or []),
                        raw_description=_description(p), fetched_at=fetched,
                        posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
                        pay=_pay(p), work_mode_hint=p.get("workplaceType") if p.get("workplaceType") != "unspecified" else "",
                        employment_hint=cats.get("commitment") or "",
                    )
                except (KeyError, TypeError, ValueError):
                    errors.append(f"Skipped a malformed Lever posting from {company}.")
                    continue
                if matches_query(listing, query.keywords):
                    listings.append(listing)
                if len(listings) >= query.limit:
                    break
        return SourceResult(source=self.name, listings=listings, errors=errors, fetched_at=fetched)

    def still_lists(self, job: JobListing, fetcher: Fetcher) -> bool | None:
        try:
            resp = fetcher.get_json(f"{API}/{job.source_job_id}", {"mode": "json"})
        except SourceUnavailable:
            return None
        if resp.status == 200:
            return True
        if resp.status == 404:
            return False
        return None
