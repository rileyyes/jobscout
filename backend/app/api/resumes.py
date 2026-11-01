"""Resume upload → validation → scan → encrypted storage → proposed profile fields.

Extracted details are *proposals*: the user reviews and confirms them through
PUT /api/profile/career. Unsaved resumes (file and extracted text) are purged
after JOBSCOUT_RESUME_RETENTION_HOURS.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.core import extract
from app.core.models import EducationLevel
from app.core.untrusted import clean_text, detect_injection
from app.db.base import utcnow
from app.db.models import Resume
from app.storage import UploadRejected, extract_text, validate_upload

from .deps import current_user_id, get_db, get_settings, not_found, rate_limit

log = logging.getLogger("jobscout.resumes")
router = APIRouter(prefix="/api/resumes", tags=["resumes"])


class ResumeOut(BaseModel):
    id: str
    original_filename: str
    content_type: str
    size_bytes: int
    scan_status: str
    keep_file: bool
    file_available: bool
    purge_after: str | None
    created_at: str


class ProposedProfile(BaseModel):
    """Unconfirmed values extracted from the resume. Never used for matching until confirmed."""

    skills: list[str]
    certifications: list[str]
    education_level: EducationLevel | None
    emails_found: int
    content_flags: list[str]
    note: str = "These details were extracted automatically. Review and correct them before saving."


class UploadOut(BaseModel):
    resume: ResumeOut
    proposed: ProposedProfile


def _out(r: Resume) -> ResumeOut:
    return ResumeOut(id=str(r.id), original_filename=r.original_filename, content_type=r.content_type,
                     size_bytes=r.size_bytes, scan_status=r.scan_status, keep_file=r.keep_file,
                     file_available=r.storage_key is not None,
                     purge_after=r.purge_after.isoformat() if r.purge_after else None,
                     created_at=r.created_at.isoformat())


def propose_from_text(text: str) -> ProposedProfile:
    text = clean_text(text)
    low = text.lower()
    certs = [c for c in extract.CERT_VOCAB if re.search(r"(?<![a-z])" + re.escape(c) + r"(?![a-z])", low)]
    levels = [lvl for lvl, rx in extract._EDU if re.search(rx, text, re.IGNORECASE)]
    return ProposedProfile(
        skills=extract.find_skills(text), certifications=certs,
        education_level=max(levels, key=lambda lv: lv.value) if levels else None,
        emails_found=len(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)),
        content_flags=detect_injection(text),
    )


def purge_expired(db: Session, store) -> int:
    """Remove unsaved resume files and all extracted text past their retention window."""
    now = utcnow()
    n = 0
    for r in db.scalars(select(Resume).where(Resume.purge_after.is_not(None), Resume.purge_after <= now)):
        if not r.keep_file and r.storage_key:
            store.delete(r.storage_key)
            r.storage_key = None
        r.extracted_text_enc = None
        r.purge_after = None
        n += 1
    db.commit()
    return n


def _owned(db: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
    r = db.get(Resume, resume_id)
    if r is None or r.user_id != user_id:
        raise not_found("Resume")
    return r


@router.post("", response_model=UploadOut, status_code=201, dependencies=[Depends(rate_limit("upload", 20, 3600))])
def upload(request: Request, file: UploadFile = File(...), keep_file: bool = Form(False),
           user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    # Sync handler (runs in a worker thread) so parsing and DB work don't block the event loop.
    data = file.file.read(settings.max_upload_bytes + 1)
    try:
        meta = validate_upload(file.filename or "", data, settings.max_upload_bytes)
    except UploadRejected as e:
        raise HTTPException(422, str(e)) from e

    try:
        verdict = request.app.state.scanner.scan(data)
    except Exception:
        log.exception("malware scanner failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "File scanning is unavailable; try again later.")
    if verdict == "infected":
        raise HTTPException(422, "This file was flagged by the malware scanner.")

    try:
        text = extract_text(data, meta.extension)
    except Exception as e:
        raise HTTPException(422, "Couldn't read text from this file.") from e

    store = request.app.state.file_store
    purge_expired(db, store)
    resume = Resume(
        user_id=user_id, original_filename=meta.safe_name, content_type=meta.content_type, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), storage_key=store.save(data), scan_status=verdict,
        extracted_text_enc=store.encrypt_text(text), keep_file=keep_file,
        purge_after=utcnow() + timedelta(hours=settings.resume_retention_hours),
    )
    db.add(resume)
    db.commit()
    return UploadOut(resume=_out(resume), proposed=propose_from_text(text))


@router.get("", response_model=list[ResumeOut])
def list_resumes(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rows = db.scalars(select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc()))
    return [_out(r) for r in rows]


@router.post("/{resume_id}/keep", response_model=ResumeOut)
def keep(resume_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    r = _owned(db, user_id, resume_id)
    if r.storage_key is None:
        raise HTTPException(status.HTTP_410_GONE, "This file was already removed under the retention policy.")
    r.keep_file = True
    db.commit()
    return _out(r)


@router.get("/{resume_id}/download")
def download(resume_id: uuid.UUID, request: Request, user_id: uuid.UUID = Depends(current_user_id),
             db: Session = Depends(get_db)):
    r = _owned(db, user_id, resume_id)
    if r.storage_key is None:
        raise HTTPException(status.HTTP_410_GONE, "This file was removed under the retention policy.")
    data = request.app.state.file_store.load(r.storage_key)
    return Response(content=data, media_type=r.content_type, headers={
        "Content-Disposition": f'attachment; filename="{r.original_filename}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: uuid.UUID, request: Request, user_id: uuid.UUID = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    r = _owned(db, user_id, resume_id)
    if r.storage_key:
        request.app.state.file_store.delete(r.storage_key)
    db.execute(delete(Resume).where(Resume.id == r.id))
    db.commit()
    return Response(status_code=204)
