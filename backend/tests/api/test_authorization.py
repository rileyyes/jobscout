"""Bob must not be able to read, change, or delete anything Alice owns (IDOR checks)."""

import pytest

from .test_search_tracking import setup_live


@pytest.fixture
def alices_stuff(app, alice, docx_bytes):
    setup_live(app, alice)
    job_id = alice.post("/api/search", json={}).json()["results"][0]["job"]["id"]
    return {
        "job_id": job_id,
        "saved": alice.post("/api/saved", json={"job_id": job_id}).json()["id"],
        "app": alice.post("/api/applications", json={"job_id": job_id}).json()["id"],
        "note": alice.post("/api/notes", json={"job_id": job_id, "body": "private"}).json()["id"],
        "resume": alice.post("/api/resumes", files={"file": ("r.docx", docx_bytes, "application/octet-stream")},
                             data={"keep_file": "true"}).json()["resume"]["id"],
        "source": alice.get("/api/sources").json()[0]["id"],
        "alert": alice.post("/api/alerts", json={"name": "SOC jobs"}).json()["id"],
        "run": alice.get("/api/search/history").json()[0]["id"],
    }


def test_bob_cannot_touch_alices_records(alice, bob, alices_stuff):
    s = alices_stuff
    checks = [
        ("delete", f"/api/saved/{s['saved']}", None),
        ("patch", f"/api/applications/{s['app']}", {"status": "withdrawn"}),
        ("delete", f"/api/applications/{s['app']}", None),
        ("delete", f"/api/notes/{s['note']}", None),
        ("get", f"/api/resumes/{s['resume']}/download", None),
        ("post", f"/api/resumes/{s['resume']}/keep", None),
        ("delete", f"/api/resumes/{s['resume']}", None),
        ("delete", f"/api/sources/{s['source']}", None),
        ("put", f"/api/alerts/{s['alert']}", {"name": "hijack"}),
        ("delete", f"/api/alerts/{s['alert']}", None),
        ("delete", f"/api/search/history/{s['run']}", None),
    ]
    for method, url, body in checks:
        kw = {"json": body} if body is not None else {}
        r = getattr(bob, method)(url, **kw)
        assert r.status_code == 404, f"{method.upper()} {url} -> {r.status_code}"

    # Bob's own lists are empty
    for path in ["/api/saved", "/api/applications", "/api/notes", "/api/resumes", "/api/sources",
                 "/api/alerts", "/api/search/history"]:
        assert bob.get(path).json() == [], path

    # Alice's data is intact
    assert len(alice.get("/api/applications").json()) == 1
    assert alice.get("/api/applications").json()[0]["status"] == "interested"
    assert len(alice.get("/api/notes").json()) == 1
    assert alice.get(f"/api/resumes/{s['resume']}/download").status_code == 200


def test_bob_profile_is_separate(alice, bob, alices_stuff):
    assert bob.get("/api/profile/career").json()["profile"]["skills"] == []
    assert alice.get("/api/profile/career").json()["profile"]["skills"]


def test_shared_job_catalog_scores_per_user(bob, alices_stuff):
    # Job postings are public data; Bob can view one, but the match uses Bob's (empty) profile.
    r = bob.get(f"/api/jobs/{alices_stuff['job_id']}").json()
    assert r["match"]["qualification"] < 50
