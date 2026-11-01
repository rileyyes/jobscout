# JobScout

A personal AI job-search assistant. JobScout finds jobs from authorized sources, checks that they're actually open, ranks them against your confirmed career profile with plain-language reasons, and estimates whether the pay covers your real expenses. You always review and submit applications yourself.

> **Status: Phase 2 of 4 — backend API complete.** The web UI is Phase 3. See [docs/PLAN.md](docs/PLAN.md).

## Run the backend locally

Requires Python 3.12+ and PostgreSQL 16.

```bash
createdb jobscout                       # or use any Postgres you have
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                    # then set JOBSCOUT_DATABASE_URL and the two keys
alembic upgrade head
uvicorn app.asgi:app --reload
```

Interactive API docs: http://localhost:8000/api/docs. Try `POST /api/search` with `{"demo": true}` after registering.

### Tests

```bash
cd backend
python -m pytest -q                     # core engine tests run anywhere
export JOBSCOUT_TEST_DATABASE_URL=postgresql+psycopg://jobscout:jobscout@localhost:5432/jobscout_test
python -m pytest -q                     # adds the API tests (needs that database to exist)
```

CI runs everything against Postgres 16 on each push, including `alembic upgrade`, `alembic check` (migration matches the models), and `alembic downgrade`.

## Implemented vs. placeholder

| Feature | Status |
|---|---|
| Greenhouse + Lever job sources, per-user employer list | ✅ Implemented |
| Hiring verification (verified open / unconfirmed / expected / closed), recheck before applying | ✅ Implemented |
| Transparent matching (qualification, compensation, hiring confidence, overall) | ✅ Implemented |
| Pay annualizing, take-home + affordability estimates | ✅ Implemented (federal + FICA; state tax only for no-wage-tax states or a user override) |
| Dedup across sources | ✅ Implemented |
| Accounts: register, login, logout, sessions, CSRF, rate limits, password reset, account deletion | ✅ Implemented |
| Career + financial profiles with explicit confirmation | ✅ Implemented |
| Resume upload (PDF/DOCX): validation, encryption at rest, retention, proposed fields | ✅ Implemented |
| Saved jobs, application tracker, notes, search history | ✅ Implemented |
| Malware scanning | ⚙️ Works with ClamAV if configured; otherwise files are marked `not_scanned` |
| Password-reset email delivery | ⚙️ Dev sender logs the link; plug in SMTP/provider for prod |
| Job alerts | 🟡 Saved alert settings + no-repeat delivery table; the scheduler that runs them is Phase 4 |
| Web UI | ⏳ Phase 3 |
| LLM features (resume/cover-letter drafts, search agent, smarter extraction) | ⏳ Phase 4 |
| Keyword-search providers (USAJOBS, Adzuna) | ⏳ Planned; need free API keys |
| Commute distance by geocoding | ⏳ Planned; currently city/state matching |
| LinkedIn sign-in (OIDC) | ⏳ Planned; job search/apply via LinkedIn is not possible and not attempted |

## Docs

- [Plan & architecture](docs/PLAN.md)
- [Security design](docs/SECURITY.md)
- [Job data providers, permissions & costs](docs/PROVIDERS.md)
