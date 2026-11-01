from datetime import date, timedelta

from app.core.models import EvidenceKind, HiringEvidence, HiringStatus, VerificationSignals
from app.core.verification import classify, classify_expected, needs_recheck

from .conftest import NOW, make_job


def sig(**kw):
    base = dict(source_still_lists=True, apply_url_status=200, checked_at=NOW - timedelta(hours=2))
    base.update(kw)
    return VerificationSignals(**base)


def test_verified_open_requires_all_checks():
    r = classify(make_job(posted_at=NOW - timedelta(days=5)), sig(), now=NOW)
    assert r.status == HiringStatus.VERIFIED_OPEN and r.confidence == 90


def test_search_presence_alone_is_not_verified():
    r = classify(make_job(), VerificationSignals(), now=NOW)
    assert r.status == HiringStatus.RECENT_UNCONFIRMED
    assert "Never checked." in r.reasons


def test_stale_check_downgrades():
    r = classify(make_job(), sig(checked_at=NOW - timedelta(days=5)), now=NOW)
    assert r.status == HiringStatus.RECENT_UNCONFIRMED


def test_unverifiable_board_downgrades():
    r = classify(make_job(), sig(source_still_lists=None), now=NOW)
    assert r.status == HiringStatus.RECENT_UNCONFIRMED


def test_expired_and_closed_cases():
    assert classify(make_job(), sig(source_still_lists=False), now=NOW).status == HiringStatus.CLOSED
    assert classify(make_job(), sig(apply_url_status=404), now=NOW).status == HiringStatus.CLOSED
    assert classify(make_job(), sig(apply_url_status=410), now=NOW).status == HiringStatus.CLOSED
    assert classify(make_job(deadline=date(2026, 10, 15)), sig(), now=NOW).status == HiringStatus.CLOSED
    assert classify(make_job(explicitly_closed=True), sig(), now=NOW).status == HiringStatus.CLOSED


def test_broken_link_not_verified():
    r = classify(make_job(), sig(apply_url_status=500), now=NOW)
    assert r.status == HiringStatus.RECENT_UNCONFIRMED
    assert any("HTTP 500" in x for x in r.reasons)


def test_old_posting_lower_confidence():
    r = classify(make_job(posted_at=NOW - timedelta(days=90)), sig(), now=NOW)
    assert r.status == HiringStatus.VERIFIED_OPEN and r.confidence == 70


def test_expected_hiring_requires_evidence():
    assert classify_expected([], now=NOW) is None
    old = HiringEvidence(kind=EvidenceKind.ANNOUNCED, source_name="Press release",
                         source_url="https://example.com/pr", evidence_date=date(2025, 1, 1), summary="Old")
    assert classify_expected([old], now=NOW) is None


def test_expected_hiring_labels_inferred_vs_announced():
    ann = HiringEvidence(kind=EvidenceKind.ANNOUNCED, source_name="Acme newsroom",
                         source_url="https://example.com/news", evidence_date=date(2026, 10, 10),
                         summary="Opening a Manchester SOC with 40 roles in Q1.")
    inf = HiringEvidence(kind=EvidenceKind.INFERRED, source_name="Funding news",
                         source_url="https://example.com/funding", evidence_date=date(2026, 10, 20),
                         summary="Raised Series B; may expand security team.")
    r = classify_expected([ann, inf], now=NOW)
    assert r.status == HiringStatus.EXPECTED_HIRING and r.confidence == 45
    assert any(x.startswith("AI-inferred (unconfirmed)") for x in r.reasons)
    assert classify_expected([inf], now=NOW).confidence == 15


def test_needs_recheck():
    r = classify(make_job(), sig(), now=NOW)
    assert not needs_recheck(r, now=NOW)
    assert needs_recheck(r, now=NOW + timedelta(days=4))
