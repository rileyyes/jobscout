"""API test harness. Needs a real PostgreSQL database:

    export JOBSCOUT_TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/jobscout_test

Skipped automatically when FastAPI/SQLAlchemy or the database URL aren't available,
so the pure core tests still run anywhere.
"""

from __future__ import annotations

import io
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

DB_URL = os.environ.get("JOBSCOUT_TEST_DATABASE_URL")
if not DB_URL:
    pytest.skip("JOBSCOUT_TEST_DATABASE_URL not set", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.emailer import MemoryEmailSender  # noqa: E402
from app.main import create_app  # noqa: E402
from tests.conftest import FakeFetcher  # noqa: E402

PASSWORD = "correct-horse-battery-staple"


class SwitchableScanner:
    def __init__(self):
        self.verdict = "not_scanned"

    def scan(self, data):
        if self.verdict == "error":
            raise RuntimeError("scanner down")
        return self.verdict


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    settings = Settings(env="test", database_url=DB_URL, cookie_secure=False,
                        upload_dir=tmp_path_factory.mktemp("uploads"), max_upload_mb=1,
                        secret_key="test-secret-key-that-is-long-enough-123456")
    application = create_app(settings, fetcher=FakeFetcher(), email_sender=MemoryEmailSender(),
                             scanner=SwitchableScanner())
    engine = application.state.sessionmaker.kw["bind"]
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield application
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean(app):
    engine = app.state.sessionmaker.kw["bind"]
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} CASCADE"))
    app.state.rate_limiter.reset()
    app.state.email_sender.outbox.clear()
    app.state.fetcher.routes.clear()
    app.state.fetcher.url_status.clear()
    app.state.fetcher.default_url_status = 200
    app.state.scanner.verdict = "not_scanned"
    yield


@pytest.fixture
def db(app):
    s = app.state.sessionmaker()
    yield s
    s.close()


class Client:
    """TestClient wrapper that remembers the CSRF token and sends it on unsafe requests."""

    def __init__(self, app):
        self.c = TestClient(app)
        self.csrf: str | None = None

    def _h(self, extra=None):
        h = {"X-CSRF-Token": self.csrf} if self.csrf else {}
        h.update(extra or {})
        return h

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self._h(kw.pop("headers", None)), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self._h(kw.pop("headers", None)), **kw)

    def patch(self, url, **kw):
        return self.c.patch(url, headers=self._h(kw.pop("headers", None)), **kw)

    def delete(self, url, **kw):
        return self.c.request("DELETE", url, headers=self._h(kw.pop("headers", None)), **kw)

    def register(self, email="riley@jobscout-test.com", password=PASSWORD):
        r = self.c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, r.text
        self.csrf = r.json()["csrf_token"]
        return r


@pytest.fixture
def alice(app):
    c = Client(app)
    c.register("alice@jobscout-test.com")
    return c


@pytest.fixture
def bob(app):
    c = Client(app)
    c.register("bob@jobscout-test.com")
    return c


@pytest.fixture
def anon(app):
    return Client(app)


RESUME_TEXT = [
    "Riley Crenshaw - riley@jobscout-test.com",
    "Skills: Python, SQL, Git, Linux, networking, Splunk",
    "Education: Bachelor of Science (B.S.) in Information Technology",
    "Certifications: CompTIA Security+",
]


@pytest.fixture
def docx_bytes():
    import docx
    d = docx.Document()
    for line in RESUME_TEXT:
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


@pytest.fixture
def pdf_bytes():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in RESUME_TEXT:
        c.drawString(72, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


