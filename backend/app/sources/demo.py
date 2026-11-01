"""Clearly labeled synthetic jobs for demo mode.

Every listing has ``is_demo=True``, a 'Demo' company name, and an example.invalid
URL that can never resolve. The pipeline refuses to merge these with live results.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.models import JobListing

from .base import Fetcher, JobSource, SearchQuery, SourceResult, now_utc
from .normalize import build_listing, matches_query

_DEMO_JOBS = [
    ("1", "SOC Analyst I (Demo)", "Demo Northwind Security", "Manchester, NH",
     "Full-time, on-site. Monitor SIEM alerts (Splunk), triage incidents, escalate per runbook.\n"
     "Requirements\n- Networking fundamentals (TCP/IP, DNS)\n- Linux and Windows basics\n- Python or PowerShell scripting\n"
     "- Bachelor's degree in IT or equivalent experience\nPreferred\n- Security+ certification\n- EDR experience\n"
     "Pay: $58,000 - $68,000 per year."),
    ("2", "Junior Security Engineer (Demo)", "Demo Contoso Health", "Boston, MA",
     "Hybrid, full-time. Help run vulnerability management (Tenable) and cloud security reviews in AWS.\n"
     "Requirements\n- Python\n- SQL\n- Git\n- 1+ years of experience in IT or security\nNice to have\n- Docker\n- IAM\n"
     "Salary $72,000 - $85,000 annually."),
    ("3", "IT Support Specialist (Demo)", "Demo Fabrikam Logistics", "Nashua, NH",
     "Full-time help desk role. Requirements\n- Customer service\n- Windows\n- Active Directory\n- Ticketing (ServiceNow)\n"
     "$24 - $28 per hour, 40 hours per week."),
    ("4", "Senior Detection Engineer (Demo)", "Demo Tailspin Cyber", "Remote",
     "Fully remote. Requirements\n- 6+ years of security experience\n- SIEM content engineering\n- Python\n"
     "- Threat intelligence\nCompensation: $150,000 - $175,000."),
    ("5", "Part-time Data Entry Clerk (Demo)", "Demo Litware Retail", "Derry, NH",
     "Part-time, on-site. Requirements\n- Excel\n- Attention to detail\n$17/hour."),
]


class DemoSource(JobSource):
    name = "demo"
    is_demo = True
    terms_note = "Synthetic data for demonstration. Not real jobs."

    def search(self, query: SearchQuery, fetcher: Fetcher | None = None) -> SourceResult:
        fetched = now_utc()
        listings: list[JobListing] = []
        for i, (jid, title, company, loc, desc) in enumerate(_DEMO_JOBS):
            job = build_listing(
                source=self.name, source_job_id=jid, url=f"https://jobs.example.invalid/demo/{jid}",
                apply_url=f"https://jobs.example.invalid/demo/{jid}/apply", title=title, company=company,
                location_text=loc, raw_description=desc, fetched_at=fetched,
                posted_at=fetched - timedelta(days=3 + 5 * i), is_demo=True,
            )
            if matches_query(job, query.keywords):
                listings.append(job)
        return SourceResult(source=self.name, listings=listings, errors=[], fetched_at=fetched, is_demo=True)

    def still_lists(self, job: JobListing, fetcher: Fetcher | None = None) -> bool | None:
        return None  # demo jobs are never 'verified open'
