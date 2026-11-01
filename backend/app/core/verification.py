"""Hiring-status classification from observed signals and dated evidence.

A listing is never 'verified open' just because it appeared in search results:
the source must still list it, the apply link must respond, it must not be
closed or past deadline, and the check must be recent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    EvidenceKind,
    HiringEvidence,
    HiringStatus,
    JobListing,
    VerificationResult,
    VerificationSignals,
)

DEFAULT_FRESHNESS = timedelta(hours=72)
RECENT_POSTING_WINDOW = timedelta(days=30)
EVIDENCE_MAX_AGE = timedelta(days=180)

OK_STATUSES = range(200, 400)
GONE_STATUSES = {404, 410}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify(
    job: JobListing,
    signals: VerificationSignals,
    now: datetime | None = None,
    freshness: timedelta = DEFAULT_FRESHNESS,
) -> VerificationResult:
    now = _aware(now) or datetime.now(timezone.utc)
    checked_at = _aware(signals.checked_at)
    reasons: list[str] = []

    # --- Hard closures first -------------------------------------------------
    if job.explicitly_closed or signals.source_marked_closed:
        return VerificationResult(status=HiringStatus.CLOSED, checked_at=checked_at,
                                  reasons=["The source marks this posting as closed."], confidence=0)
    if job.deadline and job.deadline < now.date():
        return VerificationResult(status=HiringStatus.CLOSED, checked_at=checked_at,
                                  reasons=[f"Application deadline {job.deadline.isoformat()} has passed."],
                                  confidence=0)
    if signals.source_still_lists is False:
        return VerificationResult(status=HiringStatus.CLOSED, checked_at=checked_at,
                                  reasons=["The employer's job board no longer lists this posting."],
                                  confidence=0)
    if signals.apply_url_status in GONE_STATUSES:
        return VerificationResult(status=HiringStatus.CLOSED, checked_at=checked_at,
                                  reasons=[f"The application link returns HTTP {signals.apply_url_status}."],
                                  confidence=0)

    # --- Verified open requires every check to pass ---------------------------
    fresh = checked_at is not None and now - checked_at <= freshness
    listed = signals.source_still_lists is True
    link_ok = signals.apply_url_status in OK_STATUSES if signals.apply_url_status is not None else False

    if listed:
        reasons.append("Still listed on the employer's job board.")
    else:
        reasons.append("Couldn't confirm the employer's board still lists it.")
    if link_ok:
        reasons.append("Application link responds.")
    elif signals.apply_url_status is None:
        reasons.append("Application link not checked yet.")
    else:
        reasons.append(f"Application link returned HTTP {signals.apply_url_status}.")
    if fresh:
        reasons.append(f"Checked {_ago(now - checked_at)} ago.")
    else:
        reasons.append("Not checked recently." if checked_at else "Never checked.")

    if listed and link_ok and fresh:
        confidence = 90
        posted = _aware(job.posted_at)
        if posted and now - posted > timedelta(days=60):
            confidence = 70
            reasons.append("Posted over 60 days ago; may be slow-moving or evergreen.")
        return VerificationResult(status=HiringStatus.VERIFIED_OPEN, checked_at=checked_at,
                                  reasons=reasons, confidence=confidence)

    confidence = 40 if listed else 25
    posted = _aware(job.posted_at) or _aware(job.updated_at)
    if posted and now - posted > RECENT_POSTING_WINDOW:
        confidence -= 10
        reasons.append("Posting is more than 30 days old.")
    return VerificationResult(status=HiringStatus.RECENT_UNCONFIRMED, checked_at=checked_at,
                              reasons=reasons, confidence=max(confidence, 5))


def classify_expected(
    evidence: list[HiringEvidence], now: datetime | None = None
) -> VerificationResult | None:
    """EXPECTED_HIRING requires at least one dated, sourced, recent piece of evidence.

    Returns None when there is no usable evidence: no evidence, no opportunity.
    """
    now = _aware(now) or datetime.now(timezone.utc)
    usable = [e for e in evidence if now.date() - e.evidence_date <= EVIDENCE_MAX_AGE
              and e.evidence_date <= now.date()]
    if not usable:
        return None
    announced = [e for e in usable if e.kind == EvidenceKind.ANNOUNCED]
    reasons = []
    for e in sorted(usable, key=lambda e: e.evidence_date, reverse=True):
        label = "Employer announcement" if e.kind == EvidenceKind.ANNOUNCED else "AI-inferred (unconfirmed)"
        reasons.append(f"{label}, {e.evidence_date.isoformat()}, {e.source_name}: {e.summary}")
    confidence = 45 if announced else 15
    return VerificationResult(status=HiringStatus.EXPECTED_HIRING, checked_at=now,
                              reasons=reasons, confidence=confidence, evidence=usable)


def needs_recheck(result: VerificationResult, now: datetime | None = None,
                  freshness: timedelta = DEFAULT_FRESHNESS) -> bool:
    now = _aware(now) or datetime.now(timezone.utc)
    if result.status == HiringStatus.CLOSED:
        return False
    checked = _aware(result.checked_at)
    return checked is None or now - checked > freshness


def _ago(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "under an hour"
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours // 24} days"
