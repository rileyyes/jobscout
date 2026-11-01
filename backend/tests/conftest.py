from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.models import (
    CareerProfile,
    EducationLevel,
    EmploymentType,
    ExperienceLevel,
    JobListing,
    Location,
    WorkMode,
)
from app.sources.base import FetchResponse, SourceUnavailable

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 1, 15, 0, tzinfo=timezone.utc)


class FakeFetcher:
    """Offline fetcher: routes map URL -> FetchResponse | Exception."""

    def __init__(self, routes: dict[str, FetchResponse | Exception] | None = None,
                 url_status: dict[str, int] | None = None, default_url_status: int = 200):
        self.routes = routes or {}
        self.url_status = url_status or {}
        self.default_url_status = default_url_status
        self.calls: list[str] = []

    def get_json(self, url, params=None):
        self.calls.append(url)
        r = self.routes.get(url, FetchResponse(status=404))
        if isinstance(r, Exception):
            raise r
        return r

    def check_url(self, url):
        self.calls.append("CHECK " + url)
        st = self.url_status.get(url, self.default_url_status)
        if st == -1:
            raise SourceUnavailable("down")
        return FetchResponse(status=st)


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def riley() -> CareerProfile:
    return CareerProfile(
        skills=["Python", "SQL", "Git", "networking", "cloud computing", "Linux"],
        certifications=[],
        years_experience=0,
        education_level=EducationLevel.BACHELOR,
        desired_titles=["SOC Analyst", "Security Engineer", "Cybersecurity Analyst", "IT Support"],
        experience_level=ExperienceLevel.ENTRY,
        home=Location(city="Derry", state="NH", lat=42.8806, lon=-71.3273),
        commute_radius_miles=35,
        work_modes=[WorkMode.ONSITE, WorkMode.HYBRID, WorkMode.REMOTE],
        employment_types=[EmploymentType.FULL_TIME],
        min_annual_salary=55_000,
    )


def make_job(**kw) -> JobListing:
    base = dict(source="greenhouse", source_job_id="acme/1", canonical_url="https://boards.greenhouse.io/acme/jobs/1",
                apply_url="https://boards.greenhouse.io/acme/jobs/1", title="SOC Analyst I", company="Acme",
                location_text="Manchester, NH", fetched_at=NOW)
    base.update(kw)
    return JobListing(**base)
