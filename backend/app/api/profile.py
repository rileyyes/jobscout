"""Career and financial profiles. Everything here is user-editable; matching only uses
a profile after the user confirms it."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.models import CareerProfile, FinancialProfile
from app.db.base import utcnow
from app.db.models import CareerProfileRecord, FinancialProfileRecord

from .deps import current_user_id, get_db

router = APIRouter(prefix="/api/profile", tags=["profile"])

Str100 = Field(max_length=100)


class EmploymentEntry(BaseModel):
    title: str = Field(max_length=150)
    employer: str = Field(max_length=150)
    start: str | None = Field(default=None, max_length=20)  # "2026-02" – free-form month
    end: str | None = Field(default=None, max_length=20)
    summary: str | None = Field(default=None, max_length=2000)


class EducationEntry(BaseModel):
    school: str = Field(max_length=150)
    credential: str = Field(max_length=150)
    year: str | None = Field(default=None, max_length=10)


class CareerProfileIn(BaseModel):
    profile: CareerProfile
    employment_history: list[EmploymentEntry] = Field(default_factory=list, max_length=30)
    education: list[EducationEntry] = Field(default_factory=list, max_length=15)
    career_goals: str | None = Field(default=None, max_length=3000)
    confirm: bool = False


class CareerProfileOut(CareerProfileIn):
    confirmed: bool
    confirmed_at: str | None


def _limit_lists(p: CareerProfile) -> CareerProfile:
    clip = lambda xs: [x.strip()[:100] for x in xs if x and x.strip()][:100]
    return p.model_copy(update={"skills": clip(p.skills), "certifications": clip(p.certifications),
                                "desired_titles": clip(p.desired_titles)[:20], "industries": clip(p.industries)[:20]})


def load_career(db: Session, user_id: uuid.UUID) -> tuple[CareerProfile | None, bool]:
    rec = db.get(CareerProfileRecord, user_id)
    if rec is None:
        return None, False
    return CareerProfile.model_validate(rec.data), rec.confirmed_at is not None


def load_financial(db: Session, user_id: uuid.UUID) -> FinancialProfile:
    rec = db.get(FinancialProfileRecord, user_id)
    return FinancialProfile.model_validate(rec.data) if rec else FinancialProfile()


@router.get("/career", response_model=CareerProfileOut)
def get_career(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rec = db.get(CareerProfileRecord, user_id)
    if rec is None:
        return CareerProfileOut(profile=CareerProfile(), confirmed=False, confirmed_at=None)
    return CareerProfileOut(profile=CareerProfile.model_validate(rec.data),
                            employment_history=rec.employment_history, education=rec.education,
                            career_goals=rec.career_goals, confirmed=rec.confirmed_at is not None,
                            confirmed_at=rec.confirmed_at.isoformat() if rec.confirmed_at else None)


@router.put("/career", response_model=CareerProfileOut)
def put_career(body: CareerProfileIn, user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    rec = db.get(CareerProfileRecord, user_id) or CareerProfileRecord(user_id=user_id, data={})
    rec.data = _limit_lists(body.profile).model_dump(mode="json")
    rec.employment_history = [e.model_dump() for e in body.employment_history]
    rec.education = [e.model_dump() for e in body.education]
    rec.career_goals = body.career_goals
    # Editing without confirming clears confirmation: unreviewed data isn't treated as accurate.
    rec.confirmed_at = utcnow() if body.confirm else None
    db.add(rec)
    db.commit()
    return get_career(user_id, db)


@router.get("/financial", response_model=FinancialProfile)
def get_financial(user_id: uuid.UUID = Depends(current_user_id), db: Session = Depends(get_db)):
    return load_financial(db, user_id)


@router.put("/financial", response_model=FinancialProfile)
def put_financial(body: FinancialProfile, user_id: uuid.UUID = Depends(current_user_id),
                  db: Session = Depends(get_db)):
    rec = db.get(FinancialProfileRecord, user_id) or FinancialProfileRecord(user_id=user_id, data={})
    if body.state:
        body = body.model_copy(update={"state": body.state.strip().upper()[:2]})
    rec.data = body.model_dump(mode="json")
    db.add(rec)
    db.commit()
    return body
