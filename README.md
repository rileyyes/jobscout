# JobScout

A personal AI job-search assistant. JobScout finds jobs from authorized sources, checks that they're actually open, ranks them against your confirmed career profile with plain-language reasons, and estimates whether the pay covers your real expenses. You always review and submit applications yourself.

> **Status: Phase 1 of 4 — core engine.** The engine below works and is tested. The database, auth, API, and web UI arrive in Phases 2–3. See [docs/PLAN.md](docs/PLAN.md).

## What works now (Phase 1)

| Area | What it does |
|---|---|
| **Job sources** | Greenhouse Job Board API and Lever Postings API adapters (public, no key). Provider outages, rate limits, and malformed data are reported as search limitations instead of crashing the search. |
| **Hiring verification** | Sorts each listing into `verified_open` / `recent_unconfirmed` / `expected_hiring` / `closed`. "Verified open" means the board still lists the job, the apply link responds, and the check is under 72 hours old. Expected hiring needs dated, sourced evidence and marks AI-inferred evidence as such. |
| **Matching** | Four separate scores (qualification, compensation, hiring confidence, overall) with a reason for every point gained or lost. Handles "X or Y" requirements. Missing a *preferred* qualification never excludes a job. Weights are configurable. |
| **Pay math** | Annualizes salary, monthly, weekly, and hourly pay. Hourly pay is never annualized without stated hours or hours you chose. Take-home uses 2026 federal brackets + FICA. Affordability is based on the low end of the range, and every output is labeled an estimate. |
| **Dedup** | Canonical URLs with tracking parameters stripped, plus company/title/location fingerprints. The listing from the source closest to the employer is kept. |
| **Untrusted text** | HTML/script stripping, zero-width character removal, prompt-injection detection, and randomized data boundaries for anything sent to an LLM. |
| **Demo mode** | Synthetic jobs labeled "(Demo)" with `example.invalid` URLs. They're never shown as verified and can't be mixed with live results. |

## Run the tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q
```

## Known limitations (Phase 1)

- Greenhouse and Lever are **per-employer** APIs: you choose which employers' boards to search. A broad keyword-search provider (such as USAJOBS or Adzuna, which need API keys) is planned for Phase 2.
- Commute distance needs coordinates. Without geocoding (Phase 2), out-of-city jobs in your state get a neutral "distance unknown" score, and out-of-state jobs are flagged as outside your commute area.
- State income tax is only estimated for states without a wage tax (e.g. NH). Other states show an incomplete estimate unless you enter an effective rate.
- Skill extraction is a deterministic keyword vocabulary (`app/core/extract.py`). An LLM-assisted extractor, validated against that vocabulary, comes in Phase 4.
