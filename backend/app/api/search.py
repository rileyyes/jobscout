"""Job search: run the discovery pipeline for the signed-in user and record history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.matching import rank
from app.core.models import CareerProfile
from app.db.models import SearchRun
from app.sources.base import Fetcher, SearchQuery
from app.sources.demo import DemoSource
from app.sources.pipeline import run_search

from .deps import current_user_id, get_db, get_settings, not_found, rate_limit
from .jobs_common import ResultOut, job_out, record_verification, upsert_listing, user_sources
from .profile import load_career, load_financial

router = APIRouter(prefix="/api/search", tags=["search"])


def get_fetcher(request: Request) -> Fetcher:
    return request.app.state.fetcher


class SearchIn(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=10)
    demo: bool = False
    hide_dealbreakers: bool = False
    limit: int = Field(default=200, ge=1, le=500)


class SearchOut(BaseModel):
    run_id: str
    is_demo: bool
    profile_confirmed: bool
    limitations: list[str]
    results: list[ResultOut]


class SearchRunOut(BaseModel):
    id: str
    query: dict
    is_demo: bool
    result_count: int
    created_at: str


@router.post("", response_model=SearchOut, dependencies=[Depends(rate_limit("search", 30, 3600))])
def search(body: SearchIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings), fetcher: Fetcher = Depends(get_fetcher)):
    keywords = [k.strip()[:60] for k in body.keywords if k.strip()]
    profile, confirmed = load_career(db, user_id)
    limitations: list[str] = []
    if not confirmed:
        limitations.append("Your career profile isn't confirmed yet, so match scores use only what's saved. "
                           "Review and confirm your profile for accurate matching.")
    profile = profile or CareerProfile()

    if body.demo:
        sources = [DemoSource()]
        limitations.append("DEMO MODE: these are synthetic example jobs, not real openings.")
    else:
        sources, notes = user_sources(db, settings, user_id)
        limitations.extend(notes)

    outcome = run_search(sources, SearchQuery(keywords=keywords, limit=body.limit), profile, fetcher,
                         load_financial(db, user_id))
    limitations.extend(outcome.limitations)

    ids: dict[str, uuid.UUID] = {}
    for key, job in outcome.jobs.items():
        rec = upsert_listing(db, job)
        ids[key] = rec.id
        record_verification(db, rec.id, outcome.verifications[key])

    ranked = rank(outcome.results, include_dealbreakers=not body.hide_dealbreakers)
    run = SearchRun(user_id=user_id, query=body.model_dump(), is_demo=outcome.is_demo, result_count=len(ranked),
                    results=[{"job_id": str(ids[r.job_key]), "match": r.model_dump(mode="json")} for r in ranked],
                    limitations=limitations)
    db.add(run)
    db.commit()

    from app.db.models import JobListingRecord
    results = []
    for r in ranked:
        rec = db.get(JobListingRecord, ids[r.job_key])
        results.append(ResultOut(job=job_out(rec), verification=outcome.verifications[r.job_key], match=r))
    return SearchOut(run_id=str(run.id), is_demo=outcome.is_demo, profile_confirmed=confirmed,
                     limitations=limitations, results=results)


@router.get("/history", response_model=list[SearchRunOut])
def history(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(SearchRun).where(SearchRun.user_id == user_id)
                      .order_by(SearchRun.created_at.desc()).limit(50))
    return [SearchRunOut(id=str(r.id), query=r.query, is_demo=r.is_demo, result_count=r.result_count,
                         created_at=r.created_at.isoformat()) for r in rows]


@router.delete("/history/{run_id}", status_code=204)
def delete_run(run_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    run = db.get(SearchRun, run_id)
    if run is None or run.user_id != user_id:
        raise not_found("Search")
    db.delete(run)
    db.commit()


@router.delete("/history", status_code=204)
def clear_history(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    db.execute(delete(SearchRun).where(SearchRun.user_id == user_id))
    db.commit()
