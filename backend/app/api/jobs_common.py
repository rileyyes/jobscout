"""Persistence helpers and response shapes shared by search, jobs, saved, and applications."""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, parse_board_list
from app.core.matching import MatchResult
from app.core.models import JobListing, VerificationResult, VerificationSignals
from app.db.base import utcnow
from app.db.models import HiringVerification, JobListingRecord, UserJobSource
from app.sources.base import JobSource
from app.sources.demo import DemoSource
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import LeverSource

# Sources able to re-verify any listing they produced (board token is in source_job_id).
VERIFIERS: dict[str, JobSource] = {"greenhouse": GreenhouseSource({}), "lever": LeverSource({}),
                                   "demo": DemoSource()}


class JobOut(BaseModel):
    id: str
    listing: JobListing
    is_demo: bool


class VerificationOut(VerificationResult):
    pass


class ResultOut(BaseModel):
    job: JobOut
    verification: VerificationResult | None
    match: MatchResult | None


def user_sources(db: Session, settings: Settings, user_id: uuid.UUID) -> tuple[list[JobSource], list[str]]:
    gh = parse_board_list(settings.greenhouse_boards)
    lv = parse_board_list(settings.lever_sites)
    for s in db.scalars(select(UserJobSource).where(UserJobSource.user_id == user_id, UserJobSource.enabled)):
        (gh if s.provider == "greenhouse" else lv)[s.token] = s.display_name
    sources: list[JobSource] = []
    notes: list[str] = []
    if gh:
        sources.append(GreenhouseSource(gh))
    if lv:
        sources.append(LeverSource(lv))
    if not sources:
        notes.append("No employer job boards are set up yet. Add employers in Settings → Job sources, "
                     "or try demo mode to see how results look.")
    return sources, notes


def upsert_listing(db: Session, job: JobListing) -> JobListingRecord:
    rec = db.scalar(select(JobListingRecord).where(JobListingRecord.source == job.source,
                                                   JobListingRecord.source_job_id == job.source_job_id))
    now = utcnow()
    data = job.model_dump(mode="json")
    if rec is None:
        rec = JobListingRecord(source=job.source, source_job_id=job.source_job_id,
                               canonical_url=job.canonical_url[:2000], title=job.title[:300],
                               company=job.company[:200], is_demo=job.is_demo, data=data,
                               first_seen_at=now, last_seen_at=now)
        db.add(rec)
        db.flush()
    else:
        rec.data, rec.title, rec.company = data, job.title[:300], job.company[:200]
        rec.canonical_url, rec.last_seen_at = job.canonical_url[:2000], now
    return rec


def record_verification(db: Session, job_id: uuid.UUID, v: VerificationResult,
                        signals: VerificationSignals | None = None) -> HiringVerification:
    row = HiringVerification(job_id=job_id, status=v.status.value, confidence=v.confidence, reasons=v.reasons,
                             evidence=[e.model_dump(mode="json") for e in v.evidence],
                             signals=signals.model_dump(mode="json") if signals else {}, checked_at=v.checked_at)
    db.add(row)
    return row


def latest_verification(db: Session, job_id: uuid.UUID) -> VerificationResult | None:
    row = db.scalar(select(HiringVerification).where(HiringVerification.job_id == job_id)
                    .order_by(HiringVerification.created_at.desc()).limit(1))
    if row is None:
        return None
    return VerificationResult(status=row.status, checked_at=row.checked_at, reasons=row.reasons,
                              confidence=row.confidence, evidence=row.evidence)


def job_out(rec: JobListingRecord) -> JobOut:
    return JobOut(id=str(rec.id), listing=JobListing.model_validate(rec.data), is_demo=rec.is_demo)
