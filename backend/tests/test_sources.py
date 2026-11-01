from datetime import timedelta

import pytest

from app.core.models import EmploymentType, HiringStatus, PayPeriod, WorkMode
from app.sources.base import FetchResponse, SearchQuery, SourceUnavailable
from app.sources.demo import DemoSource
from app.sources.greenhouse import API as GH
from app.sources.greenhouse import GreenhouseSource
from app.sources.lever import API as LV
from app.sources.lever import LeverSource
from app.sources.pipeline import run_search

from .conftest import NOW, FakeFetcher, load_fixture

GH_LIST = f"{GH}/acmesec/jobs"
LV_LIST = f"{LV}/northwind"


def gh_fetcher(**extra):
    routes = {GH_LIST: FetchResponse(200, load_fixture("greenhouse_jobs.json")),
              f"{GH}/acmesec/jobs/4001": FetchResponse(200, {}),
              f"{GH}/acmesec/jobs/4002": FetchResponse(200, {})}
    routes.update(extra)
    return FakeFetcher(routes)


def test_greenhouse_normalizes_and_flags():
    res = GreenhouseSource({"acmesec": "Acme Security"}).search(SearchQuery(), gh_fetcher())
    assert len(res.listings) == 3
    assert any("malformed" in e for e in res.errors)
    j = next(x for x in res.listings if x.source_job_id == "acmesec/4001")
    assert j.canonical_url == "https://boards.greenhouse.io/acmesec/jobs/4001"
    assert j.company == "Acme Security" and j.work_mode == WorkMode.ONSITE
    assert j.employment_type == EmploymentType.FULL_TIME
    assert "powershell|python" in j.required_skills and "siem" in j.preferred_skills
    assert j.pay.min == 62_000 and j.pay.period == PayPeriod.YEAR and j.pay.origin == "structured"
    assert j.education_allows_equivalent and j.min_years_experience == 0
    bad = next(x for x in res.listings if x.source_job_id == "acmesec/4002")
    assert "override_instructions" in bad.content_flags
    assert "<" not in bad.description_text


def test_greenhouse_keyword_filter():
    res = GreenhouseSource({"acmesec": "Acme"}).search(SearchQuery(keywords=["security"]), gh_fetcher())
    assert {j.source_job_id for j in res.listings} == {"acmesec/4001", "acmesec/4002"}


@pytest.mark.parametrize("failure,msg", [
    (FetchResponse(503), "having problems"),
    (FetchResponse(429), "rate-limiting"),
    (SourceUnavailable("Network error contacting x"), "Network error"),
    (FetchResponse(200, {"unexpected": True}), "unexpected format"),
])
def test_provider_outages_are_reported_not_raised(failure, msg):
    src = GreenhouseSource({"acmesec": "Acme", "other": "Other"})
    f = gh_fetcher(**{GH_LIST: failure, f"{GH}/other/jobs": FetchResponse(200, {"jobs": []})})
    res = src.search(SearchQuery(), f)
    assert res.listings == [] and any(msg in e for e in res.errors)


def test_lever_normalizes_structured_and_text_pay():
    res = LeverSource({"northwind": "Northwind"}).search(SearchQuery(), FakeFetcher({LV_LIST: FetchResponse(200, load_fixture("lever_postings.json"))}))
    it = next(j for j in res.listings if "IT Support" in j.title)
    assert it.pay.period == PayPeriod.HOUR and it.pay.min == 25 and it.pay.hours_per_week is None
    assert it.apply_url.endswith("/apply") and it.work_mode == WorkMode.ONSITE
    assert {"windows", "active directory", "customer service", "ticketing"} <= set(it.required_skills)
    assert "powershell" in it.preferred_skills
    sec = next(j for j in res.listings if "Security" in j.title)
    assert sec.pay.min == 60_000 and sec.pay.origin == "parsed_from_description"
    assert sec.work_mode == WorkMode.HYBRID


def test_end_to_end_pipeline_verifies_and_ranks(riley):
    f = gh_fetcher(**{LV_LIST: FetchResponse(200, load_fixture("lever_postings.json")),
                      f"{LV}/northwind/aaaa-1111": FetchResponse(200, {}),
                      f"{LV}/northwind/bbbb-2222": FetchResponse(404)})
    out = run_search([GreenhouseSource({"acmesec": "Acme Security"}), LeverSource({"northwind": "Northwind"})],
                     SearchQuery(), riley, f, now=NOW)
    statuses = {k: v.status for k, v in out.verifications.items()}
    assert statuses["greenhouse:acmesec/4001"] == HiringStatus.VERIFIED_OPEN
    assert statuses["lever:northwind/bbbb-2222"] == HiringStatus.CLOSED  # removed from board
    assert statuses["greenhouse:acmesec/4003"] == HiringStatus.CLOSED  # 404 on recheck (not routed)
    keys = [r.job_key for r in out.results]
    assert "lever:northwind/bbbb-2222" not in keys
    assert keys[0] == "greenhouse:acmesec/4001"
    assert any("closed or expired" in m for m in out.limitations)
    hourly = next(r for r in out.results if r.job_key == "lever:northwind/aaaa-1111")
    assert hourly.compensation is None  # no stated hours → no assumed full-time


def test_dead_apply_link_means_closed(riley):
    f = gh_fetcher()
    f.url_status = {"https://boards.greenhouse.io/acmesec/jobs/4001?gh_src=abc123": 410}
    out = run_search([GreenhouseSource({"acmesec": "Acme"})], SearchQuery(), riley, f, now=NOW)
    assert out.verifications["greenhouse:acmesec/4001"].status == HiringStatus.CLOSED


def test_empty_results_explained(riley):
    f = FakeFetcher({GH_LIST: FetchResponse(200, {"jobs": []})})
    out = run_search([GreenhouseSource({"acmesec": "Acme"})], SearchQuery(), riley, f, now=NOW)
    assert out.results == [] and any("No open listings" in m for m in out.limitations)


def test_demo_mode_is_labeled_and_isolated(riley):
    out = run_search([DemoSource()], SearchQuery(), riley, FakeFetcher(), now=NOW)
    assert out.is_demo and out.results
    assert all(j.is_demo and "(Demo)" in j.title for j in out.jobs.values())
    assert all(v.status != HiringStatus.VERIFIED_OPEN for v in out.verifications.values())
    assert all(v.reasons[0].startswith("Demo listing") for v in out.verifications.values())
    with pytest.raises(ValueError):
        run_search([DemoSource(), GreenhouseSource({"a": "A"})], SearchQuery(), riley, FakeFetcher(), now=NOW)
