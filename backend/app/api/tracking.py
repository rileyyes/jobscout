"""Saved jobs, application tracker, notes, user job sources, and job alerts.

Every query is scoped to the signed-in user; other users' records return 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.models import Application, JobAlert, JobListingRecord, SavedJob, UserJobSource, UserNote

from .deps import current_user_id, get_db, not_found
from .jobs_common import JobOut, job_out

router = APIRouter(prefix="/api", tags=["tracking"])


def _job_exists(db: Session, job_id: uuid.UUID) -> JobListingRecord:
    rec = db.get(JobListingRecord, job_id)
    if rec is None:
        raise not_found("Job")
    return rec


def _owned(db: Session, model, obj_id: uuid.UUID, user_id: uuid.UUID, what: str):
    obj = db.get(model, obj_id)
    if obj is None or obj.user_id != user_id:
        raise not_found(what)
    return obj


# ------------------------------------------------------------------ saved

class SavedOut(BaseModel):
    id: str
    saved_at: str
    job: JobOut


class SaveIn(BaseModel):
    job_id: uuid.UUID


@router.get("/saved", response_model=list[SavedOut])
def list_saved(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.execute(select(SavedJob, JobListingRecord).join(JobListingRecord, SavedJob.job_id == JobListingRecord.id)
                      .where(SavedJob.user_id == user_id).order_by(SavedJob.created_at.desc())).all()
    return [SavedOut(id=str(s.id), saved_at=s.created_at.isoformat(), job=job_out(j)) for s, j in rows]


@router.post("/saved", response_model=SavedOut, status_code=201)
def save(body: SaveIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    job = _job_exists(db, body.job_id)
    existing = db.scalar(select(SavedJob).where(SavedJob.user_id == user_id, SavedJob.job_id == body.job_id))
    if existing is None:
        existing = SavedJob(user_id=user_id, job_id=body.job_id)
        db.add(existing)
        db.commit()
    return SavedOut(id=str(existing.id), saved_at=existing.created_at.isoformat(), job=job_out(job))


@router.delete("/saved/{saved_id}", status_code=204)
def unsave(saved_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    db.delete(_owned(db, SavedJob, saved_id, user_id, "Saved job"))
    db.commit()
    return Response(status_code=204)


# ----------------------------------------------------------- applications

class AppStatus(str, Enum):
    INTERESTED = "interested"
    PREPARING = "preparing"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ApplicationIn(BaseModel):
    job_id: uuid.UUID
    status: AppStatus = AppStatus.INTERESTED
    follow_up_at: datetime | None = None


class ApplicationPatch(BaseModel):
    status: AppStatus | None = None
    follow_up_at: datetime | None = None
    clear_follow_up: bool = False


class ApplicationOut(BaseModel):
    id: str
    status: AppStatus
    submitted_at: str | None
    follow_up_at: str | None
    updated_at: str
    job: JobOut


def _app_out(a: Application, job: JobListingRecord) -> ApplicationOut:
    return ApplicationOut(id=str(a.id), status=a.status,
                          submitted_at=a.submitted_at.isoformat() if a.submitted_at else None,
                          follow_up_at=a.follow_up_at.isoformat() if a.follow_up_at else None,
                          updated_at=(a.updated_at or a.created_at).isoformat(), job=job_out(job))


def _apply_status(a: Application, new: AppStatus) -> None:
    # 'submitted' is only ever set by the user saying they submitted it themselves.
    a.status = new.value
    if new == AppStatus.SUBMITTED and a.submitted_at is None:
        a.submitted_at = utcnow()


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.execute(select(Application, JobListingRecord)
                      .join(JobListingRecord, Application.job_id == JobListingRecord.id)
                      .where(Application.user_id == user_id).order_by(Application.updated_at.desc())).all()
    return [_app_out(a, j) for a, j in rows]


@router.post("/applications", response_model=ApplicationOut, status_code=201)
def create_application(body: ApplicationIn, user_id: uuid.UUID = Depends(current_user_id),
                       db: Session = Depends(get_db)):
    job = _job_exists(db, body.job_id)
    a = Application(user_id=user_id, job_id=body.job_id, status=AppStatus.INTERESTED.value,
                    follow_up_at=body.follow_up_at)
    _apply_status(a, body.status)
    db.add(a)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You're already tracking this job.")
    return _app_out(a, job)


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
def update_application(app_id: uuid.UUID, body: ApplicationPatch, user_id: uuid.UUID = Depends(current_user_id),
                       db: Session = Depends(get_db)):
    a = _owned(db, Application, app_id, user_id, "Application")
    if body.status:
        _apply_status(a, body.status)
    if body.clear_follow_up:
        a.follow_up_at = None
    elif body.follow_up_at:
        a.follow_up_at = body.follow_up_at
    a.updated_at = utcnow()
    db.commit()
    return _app_out(a, db.get(JobListingRecord, a.job_id))


@router.delete("/applications/{app_id}", status_code=204)
def delete_application(app_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id),
                       db: Session = Depends(get_db)):
    db.delete(_owned(db, Application, app_id, user_id, "Application"))
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ notes

class NoteIn(BaseModel):
    job_id: uuid.UUID | None = None
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: str
    job_id: str | None
    body: str
    created_at: str


def _note_out(n: UserNote) -> NoteOut:
    return NoteOut(id=str(n.id), job_id=str(n.job_id) if n.job_id else None, body=n.body,
                   created_at=n.created_at.isoformat())


@router.get("/notes", response_model=list[NoteOut])
def list_notes(job_id: uuid.UUID | None = None, user_id: uuid.UUID = Depends(current_user_id),
               db: Session = Depends(get_db)):
    q = select(UserNote).where(UserNote.user_id == user_id)
    if job_id:
        q = q.where(UserNote.job_id == job_id)
    return [_note_out(n) for n in db.scalars(q.order_by(UserNote.created_at.desc()))]


@router.post("/notes", response_model=NoteOut, status_code=201)
def add_note(body: NoteIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    if body.job_id:
        _job_exists(db, body.job_id)
    n = UserNote(user_id=user_id, job_id=body.job_id, body=body.body)
    db.add(n)
    db.commit()
    return _note_out(n)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    db.delete(_owned(db, UserNote, note_id, user_id, "Note"))
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------ job sources

class Provider(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"


class SourceIn(BaseModel):
    provider: Provider
    token: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    display_name: str = Field(min_length=1, max_length=200)


class SourceOut(BaseModel):
    id: str
    provider: Provider
    token: str
    display_name: str
    enabled: bool


def _src_out(s: UserJobSource) -> SourceOut:
    return SourceOut(id=str(s.id), provider=s.provider, token=s.token, display_name=s.display_name, enabled=s.enabled)


@router.get("/sources", response_model=list[SourceOut])
def list_sources(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    return [_src_out(s) for s in db.scalars(select(UserJobSource).where(UserJobSource.user_id == user_id)
                                            .order_by(UserJobSource.display_name))]


@router.post("/sources", response_model=SourceOut, status_code=201)
def add_source(body: SourceIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    s = UserJobSource(user_id=user_id, provider=body.provider.value, token=body.token.lower(),
                      display_name=body.display_name.strip())
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That job board is already in your sources.")
    return _src_out(s)


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    db.delete(_owned(db, UserJobSource, source_id, user_id, "Source"))
    db.commit()
    return Response(status_code=204)


# ----------------------------------------------------------------- alerts

class Frequency(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class AlertIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    keywords: list[str] = Field(default_factory=list, max_length=10)
    frequency: Frequency = Frequency.DAILY
    min_annual_salary: float | None = Field(default=None, ge=0)
    min_hiring_confidence: int = Field(default=70, ge=0, le=100)
    enabled: bool = True


class AlertOut(AlertIn):
    id: str
    last_run_at: str | None


def _alert_out(a: JobAlert) -> AlertOut:
    return AlertOut(id=str(a.id), name=a.name, keywords=a.query.get("keywords", []), frequency=a.frequency,
                    min_annual_salary=a.min_annual_salary, min_hiring_confidence=a.min_hiring_confidence,
                    enabled=a.enabled, last_run_at=a.last_run_at.isoformat() if a.last_run_at else None)


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    return [_alert_out(a) for a in db.scalars(select(JobAlert).where(JobAlert.user_id == user_id))]


@router.post("/alerts", response_model=AlertOut, status_code=201)
def add_alert(body: AlertIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    a = JobAlert(user_id=user_id, name=body.name, query={"keywords": [k.strip()[:60] for k in body.keywords]},
                 frequency=body.frequency.value, min_annual_salary=body.min_annual_salary,
                 min_hiring_confidence=body.min_hiring_confidence, enabled=body.enabled)
    db.add(a)
    db.commit()
    return _alert_out(a)


@router.put("/alerts/{alert_id}", response_model=AlertOut)
def update_alert(alert_id: uuid.UUID, body: AlertIn, user_id: uuid.UUID = Depends(current_user_id),
                 db: Session = Depends(get_db)):
    a = _owned(db, JobAlert, alert_id, user_id, "Alert")
    a.name, a.query, a.frequency = body.name, {"keywords": [k.strip()[:60] for k in body.keywords]}, body.frequency.value
    a.min_annual_salary, a.min_hiring_confidence, a.enabled = body.min_annual_salary, body.min_hiring_confidence, body.enabled
    db.commit()
    return _alert_out(a)


@router.delete("/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    db.delete(_owned(db, JobAlert, alert_id, user_id, "Alert"))
    db.commit()
    return Response(status_code=204)
