"""Discovery → normalization → dedup → verification → matching → ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.core.dedup import dedupe
from app.core.matching import MatchResult, MatchWeights, rank, score_job
from app.core.models import CareerProfile, FinancialProfile, JobListing, VerificationResult, VerificationSignals
from app.core.verification import classify

from .base import Fetcher, JobSource, SearchQuery, SourceUnavailable, now_utc


def gather_signals(job: JobListing, source: JobSource, fetcher: Fetcher, now: datetime | None = None) -> VerificationSignals:
    if job.is_demo:
        return VerificationSignals(checked_at=None)
    still = source.still_lists(job, fetcher)
    status = None
    if job.apply_url:
        try:
            status = fetcher.check_url(job.apply_url).status or None
        except SourceUnavailable:
            status = None
    return VerificationSignals(source_still_lists=still, apply_url_status=status,
                               checked_at=now or now_utc())


def verify(job: JobListing, source: JobSource, fetcher: Fetcher, now: datetime | None = None) -> VerificationResult:
    signals = gather_signals(job, source, fetcher, now)
    result = classify(job, signals, now=now)
    if job.is_demo:
        result.reasons.insert(0, "Demo listing: synthetic data, not a real job.")
    return result


@dataclass
class SearchOutcome:
    results: list[MatchResult]
    jobs: dict[str, JobListing]
    verifications: dict[str, VerificationResult]
    limitations: list[str] = field(default_factory=list)
    is_demo: bool = False


def run_search(
    sources: list[JobSource],
    query: SearchQuery,
    profile: CareerProfile,
    fetcher: Fetcher,
    financial: FinancialProfile | None = None,
    weights: MatchWeights = MatchWeights(),
    now: datetime | None = None,
) -> SearchOutcome:
    demo = [s for s in sources if s.is_demo]
    if demo and len(demo) != len(sources):
        raise ValueError("Demo sources can't be combined with live sources in one search.")

    limitations: list[str] = []
    gathered: list[JobListing] = []
    by_source = {s.name: s for s in sources}
    for s in sources:
        res = s.search(query, fetcher)
        gathered.extend(res.listings)
        limitations.extend(res.errors)
    if not sources:
        limitations.append("No job sources are configured.")

    jobs = dedupe(gathered)
    verifications: dict[str, VerificationResult] = {}
    results: list[MatchResult] = []
    for job in jobs:
        v = verify(job, by_source[job.source], fetcher, now)
        verifications[job.key] = v
        results.append(score_job(profile, job, v, financial, weights))

    ranked = rank(results)
    closed = len(results) - len(ranked)
    if closed:
        limitations.append(f"{closed} listing(s) were closed or expired and are not shown.")
    if not ranked:
        limitations.append("No open listings matched this search. Try broader keywords or more sources.")
    return SearchOutcome(results=ranked, jobs={j.key: j for j in jobs}, verifications=verifications,
                         limitations=limitations, is_demo=bool(demo))
