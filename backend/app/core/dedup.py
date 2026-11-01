"""Canonical URLs and duplicate removal across sources."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import JobListing

_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|lever-source.*|source|src|ref|refid|trk|fbclid|gclid|mc_.*)$",
                              re.IGNORECASE)

# Higher wins when the same job is found in several places. Employer ATS sources
# beat aggregators because they are closest to the employer.
SOURCE_PRIORITY = {"greenhouse": 30, "lever": 30, "employer": 30, "usajobs": 25, "adzuna": 10}


class DemoLiveMixError(ValueError):
    """Raised if demo and live listings are ever passed together."""


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not _TRACKING_PARAMS.match(k)]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path,
                       urlencode(sorted(query)), ""))


_COMPANY_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh)\b\.?", re.IGNORECASE)


def _norm(s: str) -> str:
    s = _COMPANY_SUFFIX.sub("", s.lower())
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def fingerprint(job: JobListing) -> str:
    title = re.sub(r"\b(sr|snr)\b", "senior", _norm(job.title))
    title = re.sub(r"\b(jr)\b", "junior", title)
    loc = _norm(job.location_text)
    return f"{_norm(job.company)}|{title}|{loc}"


def dedupe(listings: list[JobListing]) -> list[JobListing]:
    """Collapse duplicates by canonical URL, then by company/title/location fingerprint.

    The surviving record comes from the highest-priority source; other copies are
    recorded in ``also_seen_at``. Demo and live listings may never be mixed.
    """
    if listings and len({j.is_demo for j in listings}) > 1:
        raise DemoLiveMixError("Demo and live listings must not be merged.")

    by_key: dict[str, JobListing] = {}
    url_to_key: dict[str, str] = {}
    for job in listings:
        cu = canonical_url(job.canonical_url)
        fp = fingerprint(job)
        key = url_to_key.get(cu) or (fp if fp in by_key else None) or fp
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = job.model_copy(update={"canonical_url": cu})
        else:
            winner, loser = (job, existing) if (
                SOURCE_PRIORITY.get(job.source, 0) > SOURCE_PRIORITY.get(existing.source, 0)
            ) else (existing, job)
            seen = sorted(set(existing.also_seen_at + job.also_seen_at + [loser.key]) - {winner.key})
            by_key[key] = winner.model_copy(update={
                "canonical_url": canonical_url(winner.canonical_url),
                "also_seen_at": seen,
                "pay": winner.pay or loser.pay,
                "deadline": winner.deadline or loser.deadline,
            })
        url_to_key[cu] = key
    return list(by_key.values())
