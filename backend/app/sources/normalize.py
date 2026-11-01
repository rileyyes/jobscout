"""Turn provider fields into a ``JobListing`` with deterministic enrichment."""

from __future__ import annotations

import re
from datetime import date, datetime

from app.core import extract
from app.core.dedup import canonical_url
from app.core.models import EmploymentType, JobListing, Location, PayRange, WorkMode
from app.core.untrusted import clean_text, detect_injection

_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
_ABBREVS = set(_US_STATES.values())


def parse_locations(text: str) -> list[Location]:
    """Best-effort 'City, ST' / 'City, State' parsing. Multiple locations split on ';', '|', ' or '."""
    out: list[Location] = []
    for chunk in re.split(r";|\||\bor\b|/", text or ""):
        chunk = chunk.strip()
        if not chunk or chunk.lower() in ("remote", "anywhere"):
            continue
        parts = [p.strip() for p in chunk.split(",")]
        state = None
        city = parts[0] if parts else None
        for p in parts[1:]:
            up = p.upper()
            if up in _ABBREVS:
                state = up
                break
            if p.lower() in _US_STATES:
                state = _US_STATES[p.lower()]
                break
        if state is None and parts and parts[0].lower() in _US_STATES:
            state, city = _US_STATES[parts[0].lower()], None
        if city and city.lower().startswith("remote"):
            city = None
        out.append(Location(city=city, state=state))
    return out


def build_listing(
    *,
    source: str,
    source_job_id: str,
    url: str,
    apply_url: str | None,
    title: str,
    company: str,
    location_text: str,
    raw_description: str,
    fetched_at: datetime,
    posted_at: datetime | None = None,
    updated_at: datetime | None = None,
    deadline: date | None = None,
    pay: PayRange | None = None,
    work_mode_hint: str = "",
    employment_hint: str = "",
    is_demo: bool = False,
) -> JobListing:
    title = clean_text(title, 300)
    company = clean_text(company, 200)
    location_text = clean_text(location_text, 300)
    desc = clean_text(raw_description)
    flags = detect_injection(desc + "\n" + title)

    required, preferred = extract.split_required_preferred(desc)
    min_years = extract.find_min_years(desc)
    edu, equiv = extract.find_education(desc)
    if pay is None or not pay.has_any():
        pay = extract.parse_pay_from_text(desc)

    mode = extract.infer_work_mode(work_mode_hint) if work_mode_hint else WorkMode.UNKNOWN
    if mode == WorkMode.UNKNOWN:
        mode = extract.infer_work_mode(location_text, title, desc[:1500])
    etype = extract.infer_employment_type(employment_hint) if employment_hint else EmploymentType.UNKNOWN
    if etype == EmploymentType.UNKNOWN:
        etype = extract.infer_employment_type(title, desc[:2000])

    closed = bool(re.search(r"\b(this (position|role|posting) (has been|is) (filled|closed)|no longer accepting applications)\b",
                            desc, re.IGNORECASE))

    return JobListing(
        source=source, source_job_id=str(source_job_id), canonical_url=canonical_url(url),
        apply_url=apply_url or url, title=title, company=company, location_text=location_text,
        locations=parse_locations(location_text), work_mode=mode, employment_type=etype,
        description_text=desc, posted_at=posted_at, updated_at=updated_at, deadline=deadline, pay=pay,
        required_skills=required, preferred_skills=preferred, min_years_experience=min_years,
        education_required=edu, education_allows_equivalent=equiv,
        experience_level=extract.infer_level(title, min_years), explicitly_closed=closed,
        is_demo=is_demo, fetched_at=fetched_at, content_flags=flags,
    )


def matches_query(job: JobListing, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = f"{job.title}\n{job.description_text[:3000]}".lower()
    return any(k.lower() in hay for k in keywords)
