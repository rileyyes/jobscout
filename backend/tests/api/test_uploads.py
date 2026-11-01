import io
import zipfile
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.api.resumes import purge_expired
from app.db.base import utcnow
from app.db.models import Resume

def up(client, name, data, keep=False):
    return client.post("/api/resumes", files={"file": (name, data, "application/octet-stream")},
                       data={"keep_file": str(keep).lower()})


@pytest.mark.parametrize("fixture,name", [("docx_bytes", "resume.docx"), ("pdf_bytes", "resume.pdf")])
def test_valid_upload_proposes_unconfirmed_fields(alice, request, fixture, name):
    r = up(alice, name, request.getfixturevalue(fixture))
    assert r.status_code == 201, r.text
    p = r.json()["proposed"]
    assert {"python", "sql", "git", "linux", "networking", "siem"} <= set(p["skills"])
    assert "security+" in p["certifications"] and p["education_level"] == 3
    assert "Review and correct" in p["note"]
    assert r.json()["resume"]["scan_status"] == "not_scanned"
    # Extraction must not silently write the profile
    assert alice.get("/api/profile/career").json()["confirmed"] is False


def test_file_is_encrypted_at_rest_and_downloadable_by_owner(app, alice, docx_bytes, db):
    rid = up(alice, "resume.docx", docx_bytes).json()["resume"]["id"]
    key = db.scalar(select(Resume.storage_key))
    raw = app.state.file_store._path(key).read_bytes()
    assert raw != docx_bytes and b"word/document.xml" not in raw
    dl = alice.get(f"/api/resumes/{rid}/download")
    assert dl.status_code == 200 and dl.content == docx_bytes
    assert dl.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("name,data,msg", [
    ("resume.exe", b"MZ....", "Only PDF and DOCX"),
    ("resume.pdf", b"not really a pdf", "valid PDF"),
    ("resume.pdf", b"%PDF-1.4\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", "scripts"),
    ("resume.pdf", b"%PDF-1.4\n/Encrypt 5 0 R", "Password-protected"),
    ("resume.docx", b"PK\x03\x04garbage", "valid DOCX"),
    ("resume.pdf", b"", "empty"),
])
def test_invalid_uploads_rejected(alice, name, data, msg):
    r = up(alice, name, data)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_macro_docx_rejected(alice, docx_bytes):
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item))
        dst.writestr("word/vbaProject.bin", b"\x00" * 100)
    r = up(alice, "resume.docx", buf.getvalue())
    assert r.status_code == 422 and "macros" in r.json()["detail"]


def test_oversized_upload_rejected(alice):
    r = up(alice, "big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10))
    assert r.status_code == 422 and "larger than" in r.json()["detail"]


def test_scanner_verdicts(app, alice, docx_bytes):
    app.state.scanner.verdict = "infected"
    assert up(alice, "r.docx", docx_bytes).status_code == 422
    app.state.scanner.verdict = "error"
    assert up(alice, "r.docx", docx_bytes).status_code == 503
    app.state.scanner.verdict = "clean"
    assert up(alice, "r.docx", docx_bytes).json()["resume"]["scan_status"] == "clean"


def test_filename_is_sanitized(alice, docx_bytes):
    r = up(alice, "../../etc/pa\"ss<wd>.docx", docx_bytes)
    assert r.json()["resume"]["original_filename"] == "pa_ss_wd_.docx"


def test_retention_purges_unsaved_files_only(app, alice, docx_bytes, db):
    temp = up(alice, "temp.docx", docx_bytes).json()["resume"]["id"]
    kept = up(alice, "kept.docx", docx_bytes, keep=True).json()["resume"]["id"]
    db.execute(update(Resume).values(purge_after=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert purge_expired(db, app.state.file_store) == 2
    db.expire_all()
    rows = {str(r.id): r for r in db.scalars(select(Resume))}
    assert rows[temp].storage_key is None and rows[temp].extracted_text_enc is None
    assert rows[kept].storage_key is not None and rows[kept].extracted_text_enc is None
    assert alice.get(f"/api/resumes/{temp}/download").status_code == 410


def test_delete_resume_removes_file(app, alice, docx_bytes, db):
    rid = up(alice, "r.docx", docx_bytes).json()["resume"]["id"]
    path = app.state.file_store._path(db.scalar(select(Resume.storage_key)))
    assert alice.delete(f"/api/resumes/{rid}").status_code == 204
    assert not path.exists() and alice.get("/api/resumes").json() == []
