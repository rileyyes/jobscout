import pytest

from app.core.compensation import (
    AffordabilityVerdict,
    annualize,
    assess_affordability,
    compensation_score,
    estimate_take_home,
    federal_income_tax,
    fica_tax,
)
from app.core.models import EmploymentType, FinancialProfile, PayPeriod, PayRange

from .conftest import make_job


def test_annual_salary_passthrough():
    a = annualize(PayRange(min=60_000, max=75_000, period=PayPeriod.YEAR))
    assert (a.low, a.high, a.basis) == (60_000, 75_000, 60_000)


def test_hourly_with_stated_hours():
    a = annualize(PayRange(min=25, max=29, period=PayPeriod.HOUR, hours_per_week=40))
    assert a.low == 25 * 40 * 52 and a.high == 29 * 40 * 52


def test_hourly_without_hours_is_not_assumed_full_time():
    a = annualize(PayRange(min=25, period=PayPeriod.HOUR), EmploymentType.FULL_TIME)
    assert a.low is None and a.basis is None
    assert any("hours" in m.lower() for m in a.missing)


def test_hourly_with_user_assumption_is_labeled():
    a = annualize(PayRange(min=20, period=PayPeriod.HOUR), user_assumed_hours=30)
    assert a.low == 20 * 30 * 52
    assert any("your assumed 30" in s for s in a.assumptions)


def test_missing_pay():
    a = annualize(None)
    assert a.basis is None and a.missing


def test_max_only_range_has_no_basis():
    a = annualize(PayRange(max=90_000, period=PayPeriod.YEAR))
    assert a.basis is None and a.high == 90_000
    assert any("maximum" in m for m in a.missing)


def test_reversed_range_is_corrected():
    a = annualize(PayRange(min=80_000, max=60_000, period=PayPeriod.YEAR))
    assert (a.low, a.high) == (60_000, 80_000)
    assert any("reversed" in s for s in a.assumptions)


def test_federal_and_fica_values():
    # 65,000 - 16,100 = 48,900 taxable: 1,240 + 36,500*12% = 5,620
    assert federal_income_tax(65_000) == pytest.approx(5_620)
    assert fica_tax(65_000) == pytest.approx(65_000 * 0.0765)
    assert federal_income_tax(10_000) == 0


def test_take_home_nh_is_complete_but_ma_is_flagged():
    nh = estimate_take_home(65_000, "NH")
    assert nh.complete and nh.state_income_tax == 0
    assert nh.monthly_net == pytest.approx((65_000 - 5_620 - 4_972.5) / 12, abs=0.01)
    ma = estimate_take_home(65_000, "MA")
    assert not ma.complete and ma.state_income_tax is None
    assert "Estimate" in ma.label
    ma2 = estimate_take_home(65_000, "MA", state_rate_override=0.05)
    assert ma2.complete and ma2.state_income_tax == pytest.approx(3_250)


def test_affordability_uses_low_end_not_max():
    job = make_job(pay=PayRange(min=40_000, max=120_000, period=PayPeriod.YEAR))
    fin = FinancialProfile(monthly_rent=1_800, other_monthly_expenses=1_200, monthly_commute_cost=150,
                           monthly_benefit_premiums=100, state="NH")
    a = assess_affordability(job, fin)
    # 40k gross nets ~2,800/mo; minus 250 job costs < 3,000 need
    assert a.verdict == AffordabilityVerdict.SHORTFALL
    assert a.monthly_margin < 0
    assert "not a promise" in a.label


def test_affordability_likely_covers_and_tight():
    fin = FinancialProfile(monthly_rent=1_500, other_monthly_expenses=1_000, monthly_commute_cost=0,
                           monthly_benefit_premiums=0, state="NH")
    good = assess_affordability(make_job(pay=PayRange(min=70_000, period=PayPeriod.YEAR)), fin)
    assert good.verdict == AffordabilityVerdict.LIKELY_COVERS
    fin_tight = fin.model_copy(update={"monthly_rent": 2_100})
    tight = assess_affordability(make_job(pay=PayRange(min=48_000, period=PayPeriod.YEAR)), fin_tight)
    # 48k nets ~3,262/mo vs 3,100 need → within 10%
    assert tight.verdict == AffordabilityVerdict.TIGHT


def test_affordability_unknown_when_inputs_missing():
    a = assess_affordability(make_job(pay=None), FinancialProfile(monthly_rent=1_500))
    assert a.verdict == AffordabilityVerdict.UNKNOWN
    b = assess_affordability(make_job(pay=PayRange(min=60_000, period=PayPeriod.YEAR)), FinancialProfile())
    assert b.verdict == AffordabilityVerdict.UNKNOWN
    assert any("desired take-home" in m for m in b.missing)


def test_compensation_score_bands():
    above, _ = compensation_score(annualize(PayRange(min=66_000, period=PayPeriod.YEAR)), 60_000)
    inside, _ = compensation_score(annualize(PayRange(min=50_000, max=70_000, period=PayPeriod.YEAR)), 60_000)
    below, r = compensation_score(annualize(PayRange(min=30_000, max=40_000, period=PayPeriod.YEAR)), 60_000)
    unknown, _ = compensation_score(annualize(None), 60_000)
    assert above > inside > below and unknown is None
    assert "below" in r[0]
