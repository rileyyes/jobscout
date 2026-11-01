"""Deterministic extraction of structured facts from cleaned job text.

This is the baseline. The LLM layer may propose richer extractions later,
but those are validated against this vocabulary and never override
explicit structured provider fields.
"""

from __future__ import annotations

import re

from .models import EducationLevel, EmploymentType, ExperienceLevel, PayPeriod, PayRange, WorkMode

# canonical skill -> aliases (lowercase). Extend freely; matching uses canonical names.
SKILL_VOCAB: dict[str, list[str]] = {
    "python": ["python", "python3"],
    "sql": ["sql", "postgresql", "postgres", "mysql", "t-sql", "tsql"],
    "javascript": ["javascript", "js", "node.js", "nodejs"],
    "typescript": ["typescript"],
    "java": ["java"],
    "c#": ["c#", ".net", "dotnet"],
    "bash": ["bash", "shell scripting"],
    "powershell": ["powershell"],
    "linux": ["linux", "unix", "ubuntu", "red hat", "rhel"],
    "windows": ["windows server", "windows"],
    "active directory": ["active directory", "entra id", "azure ad"],
    "networking": ["networking", "tcp/ip", "dns", "dhcp", "routing", "switching", "vlan"],
    "firewalls": ["firewall", "firewalls", "palo alto", "fortinet"],
    "git": ["git", "github", "gitlab"],
    "aws": ["aws", "amazon web services"],
    "azure": ["azure"],
    "gcp": ["gcp", "google cloud"],
    "cloud computing": ["cloud computing", "cloud"],
    "docker": ["docker", "containers"],
    "kubernetes": ["kubernetes", "k8s"],
    "siem": ["siem", "splunk", "sentinel", "qradar", "elastic security"],
    "incident response": ["incident response", "incident handling"],
    "vulnerability management": ["vulnerability management", "vulnerability scanning", "nessus", "qualys", "tenable"],
    "edr": ["edr", "crowdstrike", "defender for endpoint", "sentinelone"],
    "threat intelligence": ["threat intelligence", "threat intel"],
    "appsec": ["application security", "appsec", "owasp", "sast", "dast"],
    "iam": ["identity and access management", "iam", "okta"],
    "nist": ["nist", "nist csf", "800-53"],
    "data analysis": ["data analysis", "data analytics", "pandas", "excel"],
    "help desk": ["help desk", "helpdesk", "service desk", "desktop support"],
    "ticketing": ["servicenow", "jira", "zendesk", "ticketing"],
    "customer service": ["customer service", "customer support"],
}

CERT_VOCAB = ["security+", "network+", "a+", "cysa+", "ccna", "cissp", "cism", "ceh", "gsec",
              "aws certified", "az-900", "sc-900", "google cybersecurity certificate"]

_ALIAS_INDEX = sorted(
    ((alias, canon) for canon, aliases in SKILL_VOCAB.items() for alias in aliases),
    key=lambda p: -len(p[0]),
)


def canonical_skill(name: str) -> str:
    n = name.strip().lower()
    for alias, canon in _ALIAS_INDEX:
        if n == alias:
            return canon
    return n


def _alias_regex(alias: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9+#])", re.IGNORECASE)


_ALIAS_RX = [(_alias_regex(a), c) for a, c in _ALIAS_INDEX]


def find_skills(text: str) -> list[str]:
    found: list[str] = []
    for rx, canon in _ALIAS_RX:
        if canon not in found and rx.search(text):
            found.append(canon)
    return found


_PREFERRED_HEAD = re.compile(
    r"^\s*(preferred|nice[- ]to[- ]have|bonus|desired|pluses?|additional|it'?s a plus)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)
_REQUIRED_HEAD = re.compile(
    r"^\s*(requirements?|required|minimum qualifications?|basic qualifications?|qualifications|"
    r"what you'?ll need|must[- ]haves?|you have)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)


def split_required_preferred(text: str) -> tuple[list[str], list[str]]:
    """Skills under 'preferred/nice to have' headings are preferred; everything else counts as required
    only if it appears in a requirements section. If no sections exist, all found skills are required."""
    heads = sorted(
        [(m.start(), "req") for m in _REQUIRED_HEAD.finditer(text)]
        + [(m.start(), "pref") for m in _PREFERRED_HEAD.finditer(text)]
    )
    if not heads:
        return find_skills(text), []
    required: list[str] = []
    preferred: list[str] = []
    for i, (start, kind) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        target = required if kind == "req" else preferred
        for item in _section_items(text[start:end]):
            if item not in target:
                target.append(item)
    covered = {s for item in required for s in item.split("|")}
    preferred = [p for p in preferred if not set(p.split("|")) <= covered]
    return required, preferred


def _section_items(section: str) -> list[str]:
    """Skills per line; 'X or Y' on one line becomes the alternative group 'x|y'."""
    items: list[str] = []
    for line in section.splitlines():
        skills = find_skills(line)
        if len(skills) >= 2 and re.search(r"\bor\b|/", line, re.IGNORECASE) and not re.search(r"\band\b", line, re.IGNORECASE):
            items.append("|".join(sorted(skills)))
        else:
            items.extend(skills)
    return items


_YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-\s*\d{1,2}\s*)?years?(?:'| of)?\s+(?:of\s+)?(?:\w+\s+){0,4}?experience",
                    re.IGNORECASE)


def find_min_years(text: str) -> float | None:
    vals = [int(m.group(1)) for m in _YEARS.finditer(text)]
    vals = [v for v in vals if v <= 20]
    return float(min(vals)) if vals else None


_EDU = [
    (EducationLevel.DOCTORATE, r"\b(ph\.?\s?d|doctorate)\b"),
    (EducationLevel.MASTER, r"\bmaster'?s\b|\bmaster of\b|\bm\.\s?s\.|\bmba\b"),
    (EducationLevel.BACHELOR, r"\bbachelor(?:'?s)?\b|\bb\.\s?[sa]\.|\b4[- ]year degree\b|\bundergraduate degree\b"),
    (EducationLevel.ASSOCIATE, r"\bassociate(?:'?s)? (?:degree|of)\b|\b2[- ]year degree\b|\ba\.\s?a\.\s?s?\.?"),
    (EducationLevel.HIGH_SCHOOL, r"\bhigh school\b|\bged\b"),
]


def find_education(text: str) -> tuple[EducationLevel | None, bool]:
    """Lowest degree level mentioned (postings often say 'Bachelor's; Master's preferred'),
    plus whether equivalent experience is accepted."""
    levels = [lvl for lvl, rx in _EDU if re.search(rx, text, re.IGNORECASE)]
    equivalent = bool(re.search(r"or equivalent|equivalent (work |practical )?experience|in lieu of",
                                text, re.IGNORECASE))
    return (min(levels, key=lambda lv: lv.value) if levels else None), equivalent


_MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?)\s*([kK])?"
_RANGE = re.compile(_MONEY + r"(?:\s*(?:-|–|—|to)\s*" + _MONEY + r")?\s*(?:/|per|an|a)?\s*(hour|hr|year|yr|annually|annum|month|week)?",
                    re.IGNORECASE)


def _num(v: str, k: str | None) -> float:
    n = float(v.replace(",", ""))
    return n * 1000 if k else n


def parse_pay_from_text(text: str) -> PayRange | None:
    for m in _RANGE.finditer(text):
        lo = _num(m.group(1), m.group(2))
        hi = _num(m.group(3), m.group(4)) if m.group(3) else None
        unit = (m.group(5) or "").lower()
        if unit in ("hour", "hr"):
            period = PayPeriod.HOUR
        elif unit in ("month",):
            period = PayPeriod.MONTH
        elif unit in ("week",):
            period = PayPeriod.WEEK
        elif unit in ("year", "yr", "annually", "annum") or lo >= 15_000:
            period = PayPeriod.YEAR
        elif 7 <= lo <= 200:
            period = PayPeriod.HOUR
        else:
            continue  # e.g. "$500 sign-on bonus" – not a wage
        if period == PayPeriod.YEAR and lo < 15_000:
            continue
        hours = None
        hm = re.search(r"(\d{1,2})\s*(?:hours|hrs)\s*(?:per|a|/)\s*week", text, re.IGNORECASE)
        if hm:
            hours = float(hm.group(1))
        return PayRange(min=lo, max=hi, period=period, hours_per_week=hours,
                        origin="parsed_from_description")
    return None


def infer_work_mode(*texts: str) -> WorkMode:
    t = " ".join(texts).lower()
    if "hybrid" in t:
        return WorkMode.HYBRID
    if re.search(r"\b(fully remote|remote[- ]first|100% remote|remote)\b", t) and "not remote" not in t:
        return WorkMode.REMOTE
    if re.search(r"\b(on[- ]site|onsite|in[- ]office|in person)\b", t):
        return WorkMode.ONSITE
    return WorkMode.UNKNOWN


def infer_employment_type(*texts: str) -> EmploymentType:
    t = " ".join(texts).lower()
    for rx, et in [
        (r"\bintern(ship)?\b", EmploymentType.INTERNSHIP),
        (r"\bpart[- ]time\b", EmploymentType.PART_TIME),
        (r"\b(contract|contractor|1099|c2h|contract[- ]to[- ]hire)\b", EmploymentType.CONTRACT),
        (r"\b(temporary|temp|seasonal)\b", EmploymentType.TEMPORARY),
        (r"\bfull[- ]time\b", EmploymentType.FULL_TIME),
    ]:
        if re.search(rx, t):
            return et
    return EmploymentType.UNKNOWN


def infer_level(title: str, min_years: float | None) -> ExperienceLevel:
    t = title.lower()
    if re.search(r"\bintern\b", t):
        return ExperienceLevel.INTERN
    if re.search(r"\b(principal|staff|lead|manager|director|head of)\b", t):
        return ExperienceLevel.LEAD
    if re.search(r"\b(senior|sr\.?|iii|iv)\b", t):
        return ExperienceLevel.SENIOR
    if re.search(r"\b(junior|jr\.?|entry|associate|tier 1|level 1|i)\b", t):
        return ExperienceLevel.ENTRY
    if min_years is not None:
        if min_years <= 2:
            return ExperienceLevel.ENTRY
        if min_years <= 5:
            return ExperienceLevel.MID
        return ExperienceLevel.SENIOR
    return ExperienceLevel.UNKNOWN
