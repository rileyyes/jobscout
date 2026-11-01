"""Job details and on-demand re-verification ('recheck before applying')."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.matching import score_job
from app.core.models import CareerProfile, JobListing
from app.core.verification import classify
from app.db.models import JobListingRecord
from app.sources.base import Fetcher
from app.sources.pipeline import gather_signals

from .deps import current_user_id, get_db, not_found, rate_limit
from .jobs_common import VERIFIERS, ResultOut, job_out, latest_verification, record_verification
from .profile import load_career, load_financial
from .search import get_fetcher

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _job(db: Session, job_id: uuid.UUID) -> JobListingRecord:
    rec = db.get(JobListingRecord, job_id)
    if rec is None:
        raise not_found("Job")
    return rec


def _result(db: Session, rec: JobListingRecord, user_id: uuid.UUID) -> ResultOut:
    v = latest_verification(db, rec.id)
    profile, _ = load_career(db, user_id)
    match = score_job(profile or CareerProfile(), JobListing.model_validate(rec.data), v,
                      load_financial(db, user_id)) if v else None
    return ResultOut(job=job_out(rec), verification=v, match=match)


@router.get("/{job_id}", response_model=ResultOut)
def get_job(job_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    return _result(db, _job(db, job_id), user_id)


@router.post("/{job_id}/verify", response_model=ResultOut, dependencies=[Depends(rate_limit("verify", 60, 3600))])
def reverify(job_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db),
             fetcher: Fetcher = Depends(get_fetcher)):
    rec = _job(db, job_id)
    job = JobListing.model_validate(rec.data)
    source = VERIFIERS.get(job.source)
    if source is None:
        raise not_found("Verifier for this source")
    signals = gather_signals(job, source, fetcher)
    v = classify(job, signals)
    if job.is_demo:
        v.reasons.insert(0, "Demo listing: synthetic data, not a real job.")
    record_verification(db, rec.id, v, signals)
    db.commit()
    return _result(db, rec, user_id)
