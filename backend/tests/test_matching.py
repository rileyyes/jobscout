from datetime import timedelta

from app.core.matching import MatchWeights, rank, score_job
from app.core.models import (
    EducationLevel,
    EmploymentType,
    ExperienceLevel,
    FinancialProfile,
    HiringStatus,
    Location,
    PayPeriod,
    PayRange,
    VerificationResult,
    WorkMode,
)

from .conftest import NOW, make_job

OPEN = VerificationResult(status=HiringStatus.VERIFIED_OPEN, checked_at=NOW, reasons=[], confidence=90)
UNCONF = VerificationResult(status=HiringStatus.RECENT_UNCONFIRMED, checked_at=None, reasons=[], confidence=40)
CLOSED = VerificationResult(status=HiringStatus.CLOSED, checked_at=NOW, reasons=[], confidence=0)


def good_job(**kw):
    base = dict(required_skills=["python", "networking"], preferred_skills=["siem"],
                education_required=EducationLevel.BACHELOR, experience_level=ExperienceLevel.ENTRY,
                employment_type=EmploymentType.FULL_TIME, work_mode=WorkMode.ONSITE,
                locations=[Location(city="Manchester", state="NH", lat=42.9956, lon=-71.4548)],
                pay=PayRange(min=62_000, max=74_000, period=PayPeriod.YEAR))
    base.update(kw)
    return make_job(**base)


def test_strong_match_scores_high_with_reasons(riley):
    r = score_job(riley, good_job(), OPEN)
    assert r.qualification >= 85 and r.overall >= 75
    assert r.compensation >= 80 and r.hiring_confidence == 90
    texts = " ".join(x.text for x in r.reasons)
    assert "2 of 2 required skills" in texts and "inside your 35-mile radius" in texts


def test_missing_preferred_never_rejects(riley):
    r = score_job(riley, good_job(preferred_skills=["siem", "edr", "kubernetes"]), OPEN)
    assert r.qualification >= 80 and not r.dealbreakers
    assert any("Preferred (not required)" in x.text for x in r.reasons)


def test_missing_required_skills_is_a_gap(riley):
    r = score_job(riley, good_job(required_skills=["siem", "edr", "incident response", "python"]), OPEN)
    assert r.qualification <= 30
    assert any(x.kind == "gap" and "siem" in x.text for x in r.reasons)


def test_senior_role_penalized_for_entry_user(riley):
    r = score_job(riley, good_job(experience_level=ExperienceLevel.SENIOR, min_years_experience=6), OPEN)
    assert r.qualification < 50
    assert any(x.factor == "level" and x.kind == "gap" for x in r.reasons)


def test_equivalent_experience_softens_education_gap(riley):
    hs = riley.model_copy(update={"education_level": EducationLevel.HIGH_SCHOOL})
    strict = score_job(hs, good_job(), OPEN).qualification
    lenient = score_job(hs, good_job(education_allows_equivalent=True), OPEN).qualification
    assert lenient > strict


def test_unknown_pay_reweights_instead_of_zeroing(riley):
    r = score_job(riley, good_job(pay=None), OPEN)
    assert r.compensation is None and r.overall >= 70
    assert any(x.factor == "compensation" for x in r.reasons)


def test_dealbreakers_cap_overall(riley):
    far = good_job(locations=[Location(city="Austin", state="TX", lat=30.27, lon=-97.74)],
                   location_text="Austin, TX")
    r = score_job(riley, far, OPEN)
    assert r.dealbreakers and r.overall <= 40
    pt = score_job(riley, good_job(employment_type=EmploymentType.PART_TIME), OPEN)
    assert any("part-time" in d for d in pt.dealbreakers)


def test_relocation_softens_location(riley):
    mover = riley.model_copy(update={"willing_to_relocate": True})
    far = good_job(locations=[Location(city="Austin", state="TX")], location_text="Austin, TX")
    assert not score_job(mover, far, OPEN).dealbreakers


def test_hiring_confidence_affects_overall(riley):
    assert score_job(riley, good_job(), OPEN).overall > score_job(riley, good_job(), UNCONF).overall


def test_affordability_feeds_compensation(riley):
    fin = FinancialProfile(monthly_rent=3_500, other_monthly_expenses=1_500, state="NH")
    r = score_job(riley, good_job(), OPEN, fin)
    assert r.affordability.verdict.value == "shortfall" and r.compensation <= 30


def test_rank_excludes_closed_and_orders(riley):
    a = score_job(riley, good_job(source_job_id="a/1"), OPEN)
    b = score_job(riley, good_job(source_job_id="b/1", required_skills=["siem"]), OPEN)
    c = score_job(riley, good_job(source_job_id="c/1"), CLOSED)
    ranked = rank([b, c, a])
    assert [r.job_key for r in ranked] == [a.job_key, b.job_key]
    assert c.overall == 0


def test_weights_are_configurable(riley):
    job = good_job(required_skills=["siem"])
    heavy = score_job(riley, job, OPEN, weights=MatchWeights(qualification=0.9, compensation=0.02, hiring=0.02,
                                                              title=0.02, location=0.02, employment_type=0.02))
    assert heavy.overall < score_job(riley, job, OPEN).overall


def test_injection_flag_surfaces_as_note(riley):
    r = score_job(riley, good_job(content_flags=["override_instructions"]), OPEN)
    assert any(x.factor == "safety" for x in r.reasons)


def test_alternative_skills_satisfied_by_either(riley):
    r = score_job(riley, good_job(required_skills=["powershell|python", "networking"]), OPEN)
    assert any("2 of 2 required" in x.text for x in r.reasons)


def test_unqualified_remote_high_pay_cannot_rank_high(riley):
    job = good_job(required_skills=["siem", "threat intelligence", "edr"], work_mode=WorkMode.REMOTE,
                   pay=PayRange(min=160_000, period=PayPeriod.YEAR))
    r = score_job(riley, job, OPEN)
    assert r.qualification < 20 and r.overall <= r.qualification + 30
