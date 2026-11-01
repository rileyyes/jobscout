"""Domain types shared by the core engine, source adapters, and API layer.

These are framework-free Pydantic models. Persistence models live in app/db and
map to/from these.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl


class HiringStatus(str, Enum):
    VERIFIED_OPEN = "verified_open"
    RECENT_UNCONFIRMED = "recent_unconfirmed"
    EXPECTED_HIRING = "expected_hiring"
    CLOSED = "closed"


class EvidenceKind(str, Enum):
    ANNOUNCED = "announced"  # employer announcement / dated recruiting notice
    INFERRED = "inferred"  # AI-inferred possibility; must be labeled as such


class WorkMode(str, Enum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class EmploymentType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


class PayPeriod(str, Enum):
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    HOUR = "hour"


class EducationLevel(int, Enum):
    NONE = 0
    HIGH_SCHOOL = 1
    ASSOCIATE = 2
    BACHELOR = 3
    MASTER = 4
    DOCTORATE = 5


class ExperienceLevel(str, Enum):
    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    UNKNOWN = "unknown"


class PayRange(BaseModel):
    """Compensation as stated by the source. ``max`` is never treated as guaranteed."""

    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    period: PayPeriod
    currency: str = "USD"
    hours_per_week: float | None = Field(default=None, gt=0, le=80)
    origin: str = "structured"  # "structured" (provider field) | "parsed_from_description"

    def has_any(self) -> bool:
        return self.min is not None or self.max is not None


class Location(BaseModel):
    city: str | None = None
    state: str | None = None  # 2-letter for US
    country: str | None = "US"
    lat: float | None = None
    lon: float | None = None


class JobListing(BaseModel):
    source: str
    source_job_id: str
    canonical_url: str
    apply_url: str | None = None
    title: str
    company: str
    location_text: str = ""
    locations: list[Location] = Field(default_factory=list)
    work_mode: WorkMode = WorkMode.UNKNOWN
    employment_type: EmploymentType = EmploymentType.UNKNOWN
    description_text: str = ""
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    deadline: date | None = None
    pay: PayRange | None = None
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    min_years_experience: float | None = None
    education_required: EducationLevel | None = None
    education_allows_equivalent: bool = False
    experience_level: ExperienceLevel = ExperienceLevel.UNKNOWN
    explicitly_closed: bool = False
    is_demo: bool = False
    fetched_at: datetime
    also_seen_at: list[str] = Field(default_factory=list)  # other source keys after dedup
    content_flags: list[str] = Field(default_factory=list)  # e.g. prompt-injection markers

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_job_id}"


class HiringEvidence(BaseModel):
    kind: EvidenceKind
    source_name: str
    source_url: HttpUrl
    evidence_date: date
    summary: str


class VerificationSignals(BaseModel):
    """Raw observations gathered by the verifier. ``None`` means 'not checked'."""

    source_still_lists: bool | None = None
    apply_url_status: int | None = None
    source_marked_closed: bool = False
    checked_at: datetime | None = None


class VerificationResult(BaseModel):
    status: HiringStatus
    checked_at: datetime | None
    reasons: list[str]
    confidence: int = Field(ge=0, le=100)
    evidence: list[HiringEvidence] = Field(default_factory=list)


class CareerProfile(BaseModel):
    """User-confirmed career profile. Only confirmed data should be passed to matching."""

    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    years_experience: float = Field(default=0, ge=0)
    education_level: EducationLevel = EducationLevel.NONE
    desired_titles: list[str] = Field(default_factory=list)
    experience_level: ExperienceLevel = ExperienceLevel.ENTRY
    home: Location | None = None
    commute_radius_miles: float | None = Field(default=None, ge=0)
    work_modes: list[WorkMode] = Field(
        default_factory=lambda: [WorkMode.ONSITE, WorkMode.HYBRID, WorkMode.REMOTE]
    )
    employment_types: list[EmploymentType] = Field(
        default_factory=lambda: [EmploymentType.FULL_TIME]
    )
    min_annual_salary: float | None = Field(default=None, ge=0)
    min_hourly: float | None = Field(default=None, ge=0)
    willing_to_relocate: bool = False
    industries: list[str] = Field(default_factory=list)


class FinancialProfile(BaseModel):
    desired_monthly_take_home: float | None = Field(default=None, ge=0)
    monthly_rent: float | None = Field(default=None, ge=0)
    other_monthly_expenses: float | None = Field(default=None, ge=0)
    monthly_commute_cost: float | None = Field(default=None, ge=0)
    monthly_benefit_premiums: float | None = Field(default=None, ge=0)
    state: str | None = None
    state_tax_rate_override: float | None = Field(default=None, ge=0, le=0.2)
    assumed_hours_per_week: float | None = Field(default=None, gt=0, le=80)
