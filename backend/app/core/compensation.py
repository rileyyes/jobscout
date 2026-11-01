"""Deterministic pay math: annualizing, take-home estimates, and affordability.

Every output is an *estimate*. Rules enforced here:
- The bottom of a pay range is the basis for affordability; the top is never assumed.
- Hourly pay is only annualized with stated hours or hours the user explicitly
  chose as an assumption. Full-time hours are never silently assumed.
- Missing inputs are reported, not filled in.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from . import tax_tables as tt
from .models import EmploymentType, FinancialProfile, JobListing, PayPeriod, PayRange

WEEKS_PER_YEAR = 52


class AnnualPay(BaseModel):
    low: float | None
    high: float | None
    assumptions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)

    @property
    def basis(self) -> float | None:
        """Conservative figure used for decisions: the low end (or the only figure)."""
        return self.low if self.low is not None else None


def annualize(
    pay: PayRange | None,
    employment_type: EmploymentType = EmploymentType.UNKNOWN,
    user_assumed_hours: float | None = None,
) -> AnnualPay:
    if pay is None or not pay.has_any():
        return AnnualPay(low=None, high=None, missing=["The listing does not state pay."])

    assumptions: list[str] = []
    missing: list[str] = []
    if pay.origin == "parsed_from_description":
        assumptions.append("Pay was read from the description text, not a structured field.")
    if pay.min is not None and pay.max is None:
        assumptions.append("Only one pay figure was given; treated as the full range.")
    if pay.min is None and pay.max is not None:
        missing.append("Only a maximum was stated; the low end of the range is unknown.")

    if pay.period == PayPeriod.YEAR:
        factor = 1.0
    elif pay.period == PayPeriod.MONTH:
        factor = 12.0
    elif pay.period == PayPeriod.WEEK:
        factor = float(WEEKS_PER_YEAR)
        if employment_type == EmploymentType.PART_TIME:
            assumptions.append("Weekly pay for a part-time role may vary week to week.")
    else:  # HOUR
        hours = pay.hours_per_week
        if hours is not None:
            assumptions.append(f"Uses the listing's stated {hours:g} hours/week.")
        elif user_assumed_hours is not None:
            hours = user_assumed_hours
            assumptions.append(f"Uses your assumed {hours:g} hours/week (the listing doesn't say).")
        else:
            missing.append(
                "Hourly role without stated hours: annual pay can't be estimated. "
                "Set an assumed hours/week to see an estimate."
            )
            return AnnualPay(low=None, high=None, assumptions=assumptions, missing=missing)
        factor = hours * WEEKS_PER_YEAR
        assumptions.append(f"Assumes {WEEKS_PER_YEAR} paid weeks/year.")

    low = pay.min * factor if pay.min is not None else None
    high = pay.max * factor if pay.max is not None else None
    if low is not None and high is None:
        high = low
    if pay.max is not None and pay.min is not None and pay.max < pay.min:
        # Provider data error: don't guess which is right; flag and use the lower.
        low, high = min(low, high), max(low, high)
        assumptions.append("Pay range was reversed in the source; corrected min/max order.")
    return AnnualPay(low=round(low, 2) if low is not None else None,
                     high=round(high, 2) if high is not None else None,
                     assumptions=assumptions, missing=missing)


def federal_income_tax(annual_gross: float) -> float:
    taxable = max(0.0, annual_gross - tt.FEDERAL_STANDARD_DEDUCTION_SINGLE)
    tax = 0.0
    lower = 0.0
    for upper, rate in tt.FEDERAL_BRACKETS_SINGLE:
        top = taxable if upper is None else min(taxable, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or taxable <= upper:
            break
        lower = upper
    return tax


def fica_tax(annual_gross: float) -> float:
    ss = min(annual_gross, tt.SOCIAL_SECURITY_WAGE_BASE) * tt.SOCIAL_SECURITY_RATE
    medicare = annual_gross * tt.MEDICARE_RATE
    extra = max(0.0, annual_gross - tt.ADDITIONAL_MEDICARE_THRESHOLD_SINGLE) * tt.ADDITIONAL_MEDICARE_RATE
    return ss + medicare + extra


class TakeHomeEstimate(BaseModel):
    annual_gross: float
    federal_income_tax: float
    fica: float
    state_income_tax: float | None
    annual_net: float
    monthly_net: float
    complete: bool
    notes: list[str]
    label: str = "Estimate only — not a guaranteed amount."


def estimate_take_home(
    annual_gross: float,
    state: str | None,
    state_rate_override: float | None = None,
) -> TakeHomeEstimate:
    notes = [
        f"Federal tax uses {tt.TAX_YEAR} single-filer brackets and the standard deduction.",
        "Excludes pre-tax benefits, retirement contributions, credits, and local taxes.",
    ]
    fed = federal_income_tax(annual_gross)
    fica = fica_tax(annual_gross)
    st_code = (state or "").upper() or None
    complete = True
    if state_rate_override is not None:
        state_tax: float | None = annual_gross * state_rate_override
        notes.append(f"State tax uses your {state_rate_override:.1%} effective-rate override.")
    elif st_code in tt.NO_WAGE_TAX_STATES:
        state_tax = 0.0
        notes.append(f"{st_code} has no state tax on wages.")
    else:
        state_tax = None
        complete = False
        notes.append(
            "State income tax not included"
            + (f" for {st_code}" if st_code else " (state unknown)")
            + "; real take-home will be lower. Add an effective-rate override for a fuller estimate."
        )
    net = annual_gross - fed - fica - (state_tax or 0.0)
    return TakeHomeEstimate(
        annual_gross=round(annual_gross, 2),
        federal_income_tax=round(fed, 2),
        fica=round(fica, 2),
        state_income_tax=round(state_tax, 2) if state_tax is not None else None,
        annual_net=round(net, 2),
        monthly_net=round(net / 12, 2),
        complete=complete,
        notes=notes,
    )


class AffordabilityVerdict(str, Enum):
    LIKELY_COVERS = "likely_covers"
    TIGHT = "tight"  # within 10% of need
    SHORTFALL = "shortfall"
    UNKNOWN = "unknown"


class Affordability(BaseModel):
    verdict: AffordabilityVerdict
    annual: AnnualPay
    take_home: TakeHomeEstimate | None
    monthly_need: float | None
    monthly_net_after_job_costs: float | None
    monthly_margin: float | None
    explanation: list[str]
    missing: list[str]
    label: str = "Estimate only. This is not a promise that the job will cover your expenses."


def monthly_need(fin: FinancialProfile) -> tuple[float | None, list[str]]:
    """The larger of desired take-home and listed expenses. Commute and premiums are
    treated as job costs and subtracted from take-home separately."""
    parts = [fin.monthly_rent, fin.other_monthly_expenses]
    expenses = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None
    candidates = [v for v in (fin.desired_monthly_take_home, expenses) if v is not None]
    missing = []
    if not candidates:
        missing.append("Add desired take-home pay or monthly expenses to check affordability.")
        return None, missing
    if fin.monthly_rent is None and fin.desired_monthly_take_home is None:
        missing.append("Monthly rent not provided.")
    return max(candidates), missing


def assess_affordability(job: JobListing, fin: FinancialProfile) -> Affordability:
    annual = annualize(job.pay, job.employment_type, fin.assumed_hours_per_week)
    need, need_missing = monthly_need(fin)
    missing = list(annual.missing) + need_missing
    explanation: list[str] = list(annual.assumptions)

    if annual.basis is None:
        return Affordability(verdict=AffordabilityVerdict.UNKNOWN, annual=annual, take_home=None,
                             monthly_need=need, monthly_net_after_job_costs=None, monthly_margin=None,
                             explanation=explanation, missing=missing)

    take_home = estimate_take_home(annual.basis, fin.state, fin.state_tax_rate_override)
    explanation.append(
        f"Based on the low end of the range (${annual.basis:,.0f}/yr), not the maximum."
    )
    job_costs = (fin.monthly_commute_cost or 0.0) + (fin.monthly_benefit_premiums or 0.0)
    net = take_home.monthly_net - job_costs
    if fin.monthly_commute_cost is None:
        missing.append("Commute cost not provided.")
    if fin.monthly_benefit_premiums is None:
        missing.append("Benefit/insurance premiums unknown.")

    if need is None:
        verdict, margin = AffordabilityVerdict.UNKNOWN, None
    else:
        margin = net - need
        if not take_home.complete and margin >= 0:
            # Without state tax we only know an upper bound on take-home.
            verdict = AffordabilityVerdict.TIGHT if margin < 0.15 * need else AffordabilityVerdict.LIKELY_COVERS
            explanation.append("State tax isn't included, so the real margin is smaller than shown.")
        elif margin >= 0.10 * need:
            verdict = AffordabilityVerdict.LIKELY_COVERS
        elif margin >= 0:
            verdict = AffordabilityVerdict.TIGHT
        else:
            verdict = AffordabilityVerdict.SHORTFALL
        explanation.append(
            f"Estimated ${net:,.0f}/mo after taxes and job costs vs. ${need:,.0f}/mo needed."
        )

    return Affordability(
        verdict=verdict, annual=annual, take_home=take_home, monthly_need=need,
        monthly_net_after_job_costs=round(net, 2),
        monthly_margin=round(margin, 2) if margin is not None else None,
        explanation=explanation, missing=missing,
    )


def compensation_score(
    annual: AnnualPay, min_annual: float | None, affordability: Affordability | None = None
) -> tuple[int | None, list[str]]:
    """0-100, or None when pay is unknown. Uses the low end of the range."""
    reasons: list[str] = []
    if annual.basis is None:
        return None, ["Pay unknown — compensation can't be scored."]
    score = 70
    if min_annual:
        ratio = annual.basis / min_annual
        if ratio >= 1:
            score = min(100, 80 + int((ratio - 1) * 100))
            reasons.append(f"Low end ${annual.basis:,.0f} meets your ${min_annual:,.0f} minimum.")
        elif annual.high is not None and annual.high >= min_annual:
            score = 55
            reasons.append(
                f"Your ${min_annual:,.0f} minimum is inside the range, but above its low end."
            )
        else:
            score = max(0, int(ratio * 50))
            reasons.append(f"Pays below your ${min_annual:,.0f} minimum.")
    if affordability is not None:
        if affordability.verdict == AffordabilityVerdict.SHORTFALL:
            score = min(score, 30)
        elif affordability.verdict == AffordabilityVerdict.TIGHT:
            score = min(score, 65)
    return score, reasons
