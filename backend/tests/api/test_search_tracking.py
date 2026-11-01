from sqlalchemy import func, select

from app.db.models import HiringVerification, JobListingRecord
from app.sources.base import FetchResponse
from app.sources.greenhouse import API as GH
from tests.conftest import load_fixture

GH_LIST = f"{GH}/acmesec/jobs"
PROFILE = {
    "profile": {"skills": ["Python", "SQL", "Git", "networking", "Linux"], "education_level": 3,
                "desired_titles": ["Security Analyst", "SOC Analyst"], "experience_level": "entry",
                "home": {"city": "Derry", "state": "NH"}, "commute_radius_miles": 35,
                "min_annual_salary": 55000},
    "confirm": True,
}


def route_board(app, gone=()):
    f = app.state.fetcher
    f.routes[GH_LIST] = FetchResponse(200, load_fixture("greenhouse_jobs.json"))
    for jid in ("4001", "4002", "4003"):
        f.routes[f"{GH}/acmesec/jobs/{jid}"] = FetchResponse(404 if jid in gone else 200, {})


def setup_live(app, client):
    assert client.put("/api/profile/career", json=PROFILE).status_code == 200
    assert client.post("/api/sources", json={"provider": "greenhouse", "token": "acmesec",
                                             "display_name": "Acme Security"}).status_code == 201
    route_board(app)


def test_profile_confirmation_rules(alice):
    r = alice.put("/api/profile/career", json=PROFILE).json()
    assert r["confirmed"] and r["profile"]["skills"][0] == "Python"
    unconfirmed = alice.put("/api/profile/career", json={**PROFILE, "confirm": False}).json()
    assert unconfirmed["confirmed"] is False  # edits need re-confirmation


def test_financial_profile_roundtrip(alice):
    r = alice.put("/api/profile/financial", json={"monthly_rent": 1500, "state": "nh"})
    assert r.status_code == 200 and r.json()["state"] == "NH"
    assert alice.get("/api/profile/financial").json()["monthly_rent"] == 1500


def test_search_without_sources_explains(alice):
    r = alice.post("/api/search", json={"keywords": ["security"]}).json()
    assert r["results"] == [] and any("No employer job boards" in m for m in r["limitations"])
    assert any("isn't confirmed" in m for m in r["limitations"])


def test_demo_search_is_labeled_and_never_verified(alice):
    r = alice.post("/api/search", json={"demo": True}).json()
    assert r["is_demo"] and r["results"]
    assert any(m.startswith("DEMO MODE") for m in r["limitations"])
    for res in r["results"]:
        assert res["job"]["is_demo"] and "(Demo)" in res["job"]["listing"]["title"]
        assert res["verification"]["status"] != "verified_open"


def test_live_search_verifies_ranks_and_persists(app, alice, db):
    setup_live(app, alice)
    r = alice.post("/api/search", json={}).json()
    assert not r["is_demo"] and r["profile_confirmed"]
    top = r["results"][0]
    assert top["job"]["listing"]["title"] == "Security Analyst I"
    assert top["verification"]["status"] == "verified_open"
    assert top["match"]["qualification"] >= 60
    flagged = next(x for x in r["results"] if x["job"]["listing"]["source_job_id"] == "acmesec/4002")
    assert "override_instructions" in flagged["job"]["listing"]["content_flags"]
    assert any(reason["factor"] == "safety" for reason in flagged["match"]["reasons"])
    # Repeat search doesn't duplicate listings
    alice.post("/api/search", json={})
    assert db.scalar(select(func.count()).select_from(JobListingRecord)) == 3
    assert len(alice.get("/api/search/history").json()) == 2


def test_provider_outage_reported(app, alice):
    setup_live(app, alice)
    app.state.fetcher.routes[GH_LIST] = FetchResponse(503)
    r = alice.post("/api/search", json={}).json()
    assert r["results"] == [] and any("having problems" in m for m in r["limitations"])


def test_recheck_before_applying_detects_closure(app, alice, db):
    setup_live(app, alice)
    job_id = alice.post("/api/search", json={}).json()["results"][0]["job"]["id"]
    route_board(app, gone={"4001"})
    r = alice.post(f"/api/jobs/{job_id}/verify").json()
    assert r["verification"]["status"] == "closed"
    assert db.scalar(select(func.count()).select_from(HiringVerification)
                     .where(HiringVerification.job_id == job_id)) == 2
    assert alice.get(f"/api/jobs/{job_id}").json()["verification"]["status"] == "closed"


def test_saved_and_application_workflow(app, alice):
    setup_live(app, alice)
    job_id = alice.post("/api/search", json={}).json()["results"][0]["job"]["id"]
    saved = alice.post("/api/saved", json={"job_id": job_id})
    assert saved.status_code == 201
    assert alice.post("/api/saved", json={"job_id": job_id}).json()["id"] == saved.json()["id"]  # idempotent
    a = alice.post("/api/applications", json={"job_id": job_id}).json()
    assert a["status"] == "interested" and a["submitted_at"] is None  # never auto-submitted
    assert alice.post("/api/applications", json={"job_id": job_id}).status_code == 409
    a2 = alice.patch(f"/api/applications/{a['id']}", json={"status": "submitted"}).json()
    assert a2["status"] == "submitted" and a2["submitted_at"]
    note = alice.post("/api/notes", json={"job_id": job_id, "body": "Follow up with recruiter Friday"})
    assert note.status_code == 201
    assert len(alice.get(f"/api/notes?job_id={job_id}").json()) == 1
    assert alice.delete(f"/api/saved/{saved.json()['id']}").status_code == 204


def test_source_token_validation(alice):
    bad = alice.post("/api/sources", json={"provider": "greenhouse", "token": "../../admin", "display_name": "x"})
    assert bad.status_code == 422
    assert alice.post("/api/sources", json={"provider": "linkedin", "token": "x", "display_name": "x"}).status_code == 422


def test_unknown_job_404(alice):
    assert alice.get("/api/jobs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert alice.post("/api/saved", json={"job_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404
