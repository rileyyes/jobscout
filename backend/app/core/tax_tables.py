"""Approximate US payroll tax data used for take-home *estimates*.

Sources (verify each January and update TAX_YEAR):
- Federal brackets / standard deduction: IRS Rev. Proc. 2025-32 (tax year 2026).
- Social Security wage base 2026: SSA ($184,500).
- FICA rates: 6.2% Social Security + 1.45% Medicare (employee share).

State income tax is only estimated for states with no tax on wages. For every
other state the estimate is flagged as incomplete unless the user supplies a
flat effective-rate override. That is deliberate: a wrong state number is worse
than a clearly missing one.
"""

TAX_YEAR = 2026

FEDERAL_STANDARD_DEDUCTION_SINGLE = 16_100

# (upper bound of bracket, marginal rate); last bracket upper bound is None.
FEDERAL_BRACKETS_SINGLE: list[tuple[float | None, float]] = [
    (12_400, 0.10),
    (50_400, 0.12),
    (105_700, 0.22),
    (201_775, 0.24),
    (256_225, 0.32),
    (640_600, 0.35),
    (None, 0.37),
]

SOCIAL_SECURITY_RATE = 0.062
SOCIAL_SECURITY_WAGE_BASE = 184_500
MEDICARE_RATE = 0.0145
ADDITIONAL_MEDICARE_RATE = 0.009
ADDITIONAL_MEDICARE_THRESHOLD_SINGLE = 200_000

# States with no broad tax on wage income.
NO_WAGE_TAX_STATES = frozenset({"AK", "FL", "NV", "NH", "SD", "TN", "TX", "WA", "WY"})
