# JobScout — Implementation Plan

JobScout is a personal AI job-search assistant. It finds jobs from authorized sources, checks that they are actually open, ranks them against a confirmed career profile with transparent reasoning, and estimates whether the pay is financially realistic. The user always submits applications themselves.

## Architecture

```
frontend/  Next.js + TypeScript (App Router)  ──HTTP/JSON──►  backend/  FastAPI
                                                                 │
                     ┌───────────────────────────────────────────┼─────────────────────┐
                     ▼                         ▼                 ▼                     ▼
              app/core/ (pure Python)   app/sources/       app/db/ (SQLAlchemy    app/llm/ (provider
              domain types, matching,   Greenhouse, Lever,  + Alembic, Postgres)   abstraction; AI only
              verification, pay math,   demo; fetcher is                           for parsing/summaries/
              dedup, untrusted-text     injected                                   explanations)
```

**Design rules**

1. **Deterministic core.** Scores, hiring status, and pay estimates come from plain, tested Python in `app/core`. The LLM never decides them. It only extracts, summarizes, and explains.
2. **Untrusted input stays untrusted.** Job descriptions, resumes, and provider responses are passed around as data. They are never treated as instructions. The LLM layer has no tools that can touch user data, and its output is validated against a schema.
3. **Authorized sources only.** Public ATS job-board APIs (Greenhouse, Lever) and licensed providers. No LinkedIn scraping, no CAPTCHA/login/rate-limit bypass.
4. **Demo data is quarantined.** Synthetic jobs carry `is_demo=True`, live under a separate source, and are never merged with live results.
5. **Honest labels.** Hiring status comes from evidence, pay is labeled as an estimate, and placeholders are listed in the README as placeholders.

## Hiring status model

| Status | Requirements |
|---|---|
| `VERIFIED_OPEN` | Authorized source still lists it, apply URL responded OK, not marked closed, checked within the freshness window (default 72h) |
| `RECENT_UNCONFIRMED` | Recent listing exists but one or more verification checks is missing or stale |
| `EXPECTED_HIRING` | Dated, sourced evidence of planned hiring; `evidence_kind` is `ANNOUNCED` (employer statement) or `INFERRED` (AI-inferred, shown as such) |
| `CLOSED` | Source dropped it, apply link 404/410, past deadline, or explicitly closed. Never recommended |

## Phases

| Phase | Scope | Verified by |
|---|---|---|
| 1 | Plan, core engine, source adapters | pytest here |
| 2 | Postgres schema + Alembic, auth (argon2 + server-side sessions, CSRF, rate limiting), per-user authorization, FastAPI routes, resume upload pipeline | GitHub Actions (Postgres service) |
| 3 | Next.js frontend: sign-in, onboarding, dashboard, results, details, saved, tracker, settings | Vitest + Playwright in CI |
| 4 | LLM provider abstraction, resume/cover-letter drafting, search agent, job alerts, deployment docs | CI + manual |

## Known external prerequisites

- Greenhouse/Lever public board APIs need no key, but each employer's board token has to be configured. They are per-company APIs, not keyword search.
- A broad keyword-search provider (e.g. Adzuna, USAJOBS) needs an API key and acceptance of its terms. Phase 2 adds the adapter interface for it.
- LinkedIn: only "Sign In with LinkedIn using OpenID Connect" (name, email, photo) is generally available. Full profile import is not. Resume upload is the primary onboarding path.
- An LLM API key (Anthropic or OpenAI-compatible) is needed for drafting features. Without one, the app still searches, verifies, ranks, and does the pay math.
