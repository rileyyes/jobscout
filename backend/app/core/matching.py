"""Transparent, configurable job matching and ranking.

Each job gets four separate indicators: qualification, compensation, hiring
confidence, and overall relevance. Every point gained or lost carries a
plain-language reason. Missing a *preferred* qualification only lowers the score
slightly and never excludes a job. Only CLOSED jobs are excluded from rankings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .compensation import Affordability, annualize, assess_affordability, compensation_score
from .extract import CERT_VOCAB, canonical_skill
from .models import (
    CareerProfile,
    EmploymentType,
    ExperienceLevel,
    FinancialProfile,
    HiringStatus,
    JobListing,
    VerificationResult,
    WorkMode,
)


@dataclass(frozen=True)
class MatchWeights:
    qualification: float = 0.35
    compensation: float = 0.20
    hiring: float = 0.20
    title: float = 0.10
    location: float = 0.10
    employment_type: float = 0.05


class Reason(BaseModel):
    kind: str  # "strength" | "gap" | "note"
    factor: str
    text: str


class MatchResult(BaseModel):
    job_key: str
    qualification: int = Field(ge=0, le=100)
    compensation: int | None
    hiring_confidence: int = Field(ge=0, le=100)
    overall: int = Field(ge=0, le=100)
    hiring_status: HiringStatus
    reasons: list[Reason]
    dealbreakers: list[str] = Field(default_factory=list)
    affordability: Affordability | None = None


_LEVEL_ORDER = {ExperienceLevel.INTERN: 0, ExperienceLevel.ENTRY: 1, ExperienceLevel.MID: 2,
                ExperienceLevel.SENIOR: 3, ExperienceLevel.LEAD: 4}


def _qualification(profile: CareerProfile, job: JobListing, reasons: list[Reason]) -> int:
    have = {canonical_skill(s) for s in profile.skills}
    canon = lambda item: "|".join(canonical_skill(s) for s in item.split("|"))
    req = [canon(s) for s in job.required_skills]
    pref = [canon(s) for s in job.preferred_skills]
    met = lambda item: any(s in have for s in item.split("|"))
    show = lambda item: next((s for s in item.split("|") if s in have), item.replace("|", " or "))

    if req:
        hit = [show(s) for s in req if met(s)]
        miss = [show(s) for s in req if not met(s)]
        score = 100 * len(hit) / len(req)
        if hit:
            reasons.append(Reason(kind="strength", factor="skills",
                                  text=f"You have {len(hit)} of {len(req)} required skills: {', '.join(hit)}."))
        if miss:
            reasons.append(Reason(kind="gap", factor="skills",
                                  text=f"Required skills you haven't listed: {', '.join(miss)}."))
    else:
        score = 70.0
        reasons.append(Reason(kind="note", factor="skills",
                              text="The listing doesn't name specific required skills."))

    if pref:
        phit = [show(s) for s in pref if met(s)]
        pmiss = [show(s) for s in pref if not met(s)]
        score += 10 * len(phit) / len(pref)
        if phit:
            reasons.append(Reason(kind="strength", factor="skills",
                                  text=f"Preferred skills you have: {', '.join(phit)}."))
        if pmiss:
            score -= 3  # small, bounded: missing 'preferred' never disqualifies
            reasons.append(Reason(kind="note", factor="skills",
                                  text=f"Preferred (not required): {', '.join(pmiss)}."))

    if job.min_years_experience is not None:
        gap = job.min_years_experience - profile.years_experience
        if gap <= 0:
            reasons.append(Reason(kind="strength", factor="experience",
                                  text=f"Meets the {job.min_years_experience:g}+ years of experience asked for."))
        else:
            score -= min(35, 10 * gap)
            reasons.append(Reason(kind="gap", factor="experience",
                                  text=f"Asks for {job.min_years_experience:g}+ years; you've listed "
                                       f"{profile.years_experience:g}."))

    if job.education_required is not None:
        if profile.education_level.value >= job.education_required.value:
            reasons.append(Reason(kind="strength", factor="education",
                                  text=f"Meets the {job.education_required.name.replace('_', ' ').lower()} education requirement."))
        elif job.education_allows_equivalent:
            score -= 5
            reasons.append(Reason(kind="note", factor="education",
                                  text="Below the stated degree level, but equivalent experience is accepted."))
        else:
            score -= 20
            reasons.append(Reason(kind="gap", factor="education",
                                  text=f"Asks for a {job.education_required.name.replace('_', ' ').lower()} degree."))

    jl, ul = _LEVEL_ORDER.get(job.experience_level), _LEVEL_ORDER.get(profile.experience_level)
    if jl is not None and ul is not None and jl - ul >= 2:
        score -= 20
        reasons.append(Reason(kind="gap", factor="level",
                              text=f"This is a {job.experience_level.value}-level role; you're targeting "
                                   f"{profile.experience_level.value}-level."))
    elif jl is not None and ul is not None and jl <= ul:
        reasons.append(Reason(kind="strength", factor="level",
                              text=f"{job.experience_level.value.capitalize()}-level role fits your target level."))

    desc = job.description_text.lower()
    certs = [c for c in profile.certifications if c.lower() in desc]
    if certs:
        score += 5
        reasons.append(Reason(kind="strength", factor="certifications",
                              text=f"Mentions certifications you hold: {', '.join(certs)}."))
    wanted = [c for c in CERT_VOCAB if re.search(r"(?<![a-z])" + re.escape(c) + r"(?![a-z])", desc)
              and c not in {x.lower() for x in profile.certifications}]
    if wanted:
        reasons.append(Reason(kind="note", factor="certifications",
                              text=f"Mentions certifications you don't list: {', '.join(wanted)}."))
    return int(max(0, min(100, round(score))))


def _haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _location(profile: CareerProfile, job: JobListing, reasons: list[Reason], dealbreakers: list[str]) -> int:
    mode = job.work_mode
    if mode != WorkMode.UNKNOWN and mode not in profile.work_modes:
        dealbreakers.append(f"{mode.value} work isn't in your preferences")
        reasons.append(Reason(kind="gap", factor="location", text=f"This role is {mode.value}; you prefer "
                              f"{', '.join(m.value for m in profile.work_modes)}."))
        return 10
    if mode == WorkMode.REMOTE:
        reasons.append(Reason(kind="strength", factor="location", text="Remote role."))
        return 100

    home = profile.home
    if home is None:
        reasons.append(Reason(kind="note", factor="location", text="Add your location to check commute distance."))
        return 60
    for loc in job.locations:
        if None not in (home.lat, home.lon, loc.lat, loc.lon) and profile.commute_radius_miles:
            d = _haversine_miles(home.lat, home.lon, loc.lat, loc.lon)
            if d <= profile.commute_radius_miles:
                reasons.append(Reason(kind="strength", factor="location",
                                      text=f"About {d:.0f} miles away, inside your {profile.commute_radius_miles:g}-mile radius."))
                return 100
    loc_text = job.location_text.lower()
    same_city = home.city and home.city.lower() in loc_text
    same_state = home.state and re.search(rf"\b{re.escape(home.state.lower())}\b", loc_text)
    if same_city:
        reasons.append(Reason(kind="strength", factor="location", text=f"Located in {home.city}."))
        return 90
    if same_state:
        reasons.append(Reason(kind="note", factor="location",
                              text=f"In {home.state.upper()}; exact commute distance unknown."))
        return 65
    if profile.willing_to_relocate:
        reasons.append(Reason(kind="note", factor="location",
                              text=f"Outside your area ({job.location_text or 'location not stated'}); you're open to relocating."))
        return 45
    if not loc_text:
        reasons.append(Reason(kind="note", factor="location", text="Location not stated."))
        return 50
    dealbreakers.append("outside your commute area")
    reasons.append(Reason(kind="gap", factor="location",
                          text=f"Outside your commute area ({job.location_text})."))
    return 15


def _title(profile: CareerProfile, job: JobListing, reasons: list[Reason]) -> int:
    if not profile.desired_titles:
        return 60
    tokens = lambda s: {w for w in re.findall(r"[a-z0-9+#]+", s.lower()) if len(w) > 1 and w not in {"and", "of", "the", "i", "ii"}}
    jt = tokens(job.title)
    best, best_title = 0.0, ""
    for t in profile.desired_titles:
        dt = tokens(t)
        if not dt:
            continue
        overlap = len(jt & dt) / len(dt)
        if overlap > best:
            best, best_title = overlap, t
    if best >= 0.99:
        reasons.append(Reason(kind="strength", factor="title", text=f"Matches your target title \"{best_title}\"."))
    elif best > 0:
        reasons.append(Reason(kind="note", factor="title", text=f"Related to your target \"{best_title}\"."))
    return int(round(20 + 80 * best))


def _employment(profile: CareerProfile, job: JobListing, reasons: list[Reason], dealbreakers: list[str]) -> int:
    et = job.employment_type
    if et == EmploymentType.UNKNOWN:
        reasons.append(Reason(kind="note", factor="employment_type", text="Employment type not stated."))
        return 60
    if et in profile.employment_types:
        return 100
    dealbreakers.append(f"{et.value.replace('_', '-')} isn't a type you're looking for")
    reasons.append(Reason(kind="gap", factor="employment_type",
                          text=f"{et.value.replace('_', '-').capitalize()} role."))
    return 20


def score_job(
    profile: CareerProfile,
    job: JobListing,
    verification: VerificationResult,
    financial: FinancialProfile | None = None,
    weights: MatchWeights = MatchWeights(),
) -> MatchResult:
    reasons: list[Reason] = []
    dealbreakers: list[str] = []

    qual = _qualification(profile, job, reasons)

    fin = financial or FinancialProfile()
    afford = assess_affordability(job, fin) if job.pay else None
    annual = afford.annual if afford else annualize(job.pay, job.employment_type, fin.assumed_hours_per_week)
    min_annual = profile.min_annual_salary
    if min_annual is None and profile.min_hourly and fin.assumed_hours_per_week:
        min_annual = profile.min_hourly * fin.assumed_hours_per_week * 52
    comp, comp_reasons = compensation_score(annual, min_annual, afford)
    for r in comp_reasons:
        reasons.append(Reason(kind="gap" if "below" in r.lower() else ("note" if comp is None else "strength"),
                              factor="compensation", text=r))
    for m in annual.missing:
        reasons.append(Reason(kind="note", factor="compensation", text=m))

    hiring = verification.confidence
    loc = _location(profile, job, reasons, dealbreakers)
    title = _title(profile, job, reasons)
    emp = _employment(profile, job, reasons, dealbreakers)

    parts = [(weights.qualification, qual), (weights.hiring, hiring), (weights.title, title),
             (weights.location, loc), (weights.employment_type, emp)]
    if comp is not None:
        parts.append((weights.compensation, comp))
    total_w = sum(w for w, _ in parts)
    overall = sum(w * v for w, v in parts) / total_w if total_w else 0
    # A job you're far from qualified for shouldn't rank high just because it's remote and pays well.
    overall = min(overall, qual + 30)
    if verification.status == HiringStatus.CLOSED:
        overall = 0
    elif dealbreakers:
        overall = min(overall, 40)

    if job.content_flags:
        reasons.append(Reason(kind="note", factor="safety",
                              text="This listing contains instruction-like text; it was treated as plain data."))

    return MatchResult(
        job_key=job.key, qualification=qual, compensation=comp, hiring_confidence=hiring,
        overall=int(round(overall)), hiring_status=verification.status, reasons=reasons,
        dealbreakers=dealbreakers, affordability=afford,
    )


def rank(results: list[MatchResult], include_dealbreakers: bool = True) -> list[MatchResult]:
    """Sort by overall relevance. CLOSED jobs are always removed."""
    kept = [r for r in results if r.hiring_status != HiringStatus.CLOSED
            and (include_dealbreakers or not r.dealbreakers)]
    status_order = {HiringStatus.VERIFIED_OPEN: 0, HiringStatus.RECENT_UNCONFIRMED: 1,
                    HiringStatus.EXPECTED_HIRING: 2}
    return sorted(kept, key=lambda r: (-r.overall, status_order.get(r.hiring_status, 9), r.job_key))
