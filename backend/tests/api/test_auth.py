from datetime import timedelta

from sqlalchemy import func, select, update

from app.db.base import utcnow
from app.db.models import CareerProfileRecord, Resume, User, UserSession

from .conftest import PASSWORD, Client


def test_register_sets_secure_session_cookie(app, anon):
    r = anon.register()
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    assert r.json()["csrf_token"]
    me = anon.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "riley@jobscout-test.com"


def test_password_is_hashed_with_argon2(anon, db):
    anon.register()
    user = db.scalar(select(User))
    assert user.password_hash.startswith("$argon2id$") and PASSWORD not in user.password_hash


def test_duplicate_and_weak_registration(anon, app):
    anon.register()
    other = Client(app)
    assert other.c.post("/api/auth/register", json={"email": "RILEY@jobscout-test.com", "password": PASSWORD}).status_code == 409
    assert other.c.post("/api/auth/register", json={"email": "x@jobscout-test.com", "password": "short"}).status_code == 422
    assert other.c.post("/api/auth/register", json={"email": "x@jobscout-test.com", "password": "aaaaaaaaaaaaaa"}).status_code == 422
    assert other.c.post("/api/auth/register", json={"email": "not-an-email", "password": PASSWORD}).status_code == 422


def test_login_logout_and_generic_errors(app, anon):
    anon.register()
    c = Client(app)
    bad_user = c.c.post("/api/auth/login", json={"email": "nobody@jobscout-test.com", "password": PASSWORD})
    bad_pw = c.c.post("/api/auth/login", json={"email": "riley@jobscout-test.com", "password": "wrong-password-123"})
    assert bad_user.status_code == bad_pw.status_code == 401
    assert bad_user.json() == bad_pw.json()
    ok = c.c.post("/api/auth/login", json={"email": "riley@jobscout-test.com", "password": PASSWORD})
    assert ok.status_code == 200
    c.csrf = ok.json()["csrf_token"]
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/auth/me").status_code == 401


def test_unauthenticated_requests_rejected(anon):
    for path in ["/api/auth/me", "/api/profile/career", "/api/saved", "/api/applications", "/api/resumes"]:
        assert anon.get(path).status_code == 401


def test_csrf_required_on_unsafe_methods(alice):
    body = {"name": "x", "keywords": []}
    assert alice.c.post("/api/alerts", json=body).status_code == 403  # no header
    assert alice.c.post("/api/alerts", json=body, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert alice.post("/api/alerts", json=body).status_code == 201


def test_csrf_rotation(alice):
    old = alice.csrf
    r = alice.c.post("/api/auth/csrf")
    assert r.status_code == 200 and r.json()["csrf_token"] != old
    assert alice.post("/api/alerts", json={"name": "x"}).status_code == 403  # old token dead
    alice.csrf = r.json()["csrf_token"]
    assert alice.post("/api/alerts", json={"name": "x"}).status_code == 201


def test_expired_session_rejected(alice, db):
    db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert alice.get("/api/auth/me").status_code == 401


def test_login_rate_limited(app, anon):
    anon.register()
    c = Client(app)
    codes = [c.c.post("/api/auth/login", json={"email": "riley@jobscout-test.com", "password": "nope-nope-nope"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_password_reset_flow(app, anon, db):
    anon.register()
    c = Client(app)
    assert c.c.post("/api/auth/password-reset/request", json={"email": "unknown@jobscout-test.com"}).status_code == 202
    assert app.state.email_sender.outbox == []  # no enumeration, nothing sent
    assert c.c.post("/api/auth/password-reset/request", json={"email": "riley@jobscout-test.com"}).status_code == 202
    body = app.state.email_sender.outbox[-1][2]
    token = body.split("#token=")[1].split()[0]
    new_pw = "a-brand-new-passphrase-42"
    assert c.c.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": new_pw}).status_code == 204
    # token is single-use, old sessions are revoked, old password fails
    assert c.c.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": new_pw}).status_code == 400
    assert anon.get("/api/auth/me").status_code == 401
    assert c.c.post("/api/auth/login", json={"email": "riley@jobscout-test.com", "password": PASSWORD}).status_code == 401
    assert c.c.post("/api/auth/login", json={"email": "riley@jobscout-test.com", "password": new_pw}).status_code == 200


def test_account_deletion_removes_everything(app, alice, db, docx_bytes):
    alice.put("/api/profile/career", json={"profile": {"skills": ["python"]}, "confirm": True})
    up = alice.post("/api/resumes", files={"file": ("r.docx", docx_bytes, "application/octet-stream")},
                    data={"keep_file": "true"})
    assert up.status_code == 201, up.text
    key = db.scalar(select(Resume.storage_key))
    path = app.state.file_store._path(key)
    assert path.exists()

    assert alice.delete("/api/auth/account", json={"password": "wrong", "confirm": True}).status_code == 403
    assert alice.delete("/api/auth/account", json={"password": PASSWORD, "confirm": True}).status_code == 204
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(CareerProfileRecord)) == 0
    assert db.scalar(select(func.count()).select_from(Resume)) == 0
    assert not path.exists()


def test_security_headers(anon):
    r = anon.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
