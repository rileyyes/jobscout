# Job data providers

| Provider | Status | Access | Cost | What it can do | Limits |
|---|---|---|---|---|---|
| **Greenhouse Job Board API** | Implemented | Public, no key | Free | Lists every job an employer publishes on Greenhouse; per-job lookup for verification; pay ranges when the employer enables pay transparency | Per-employer: you need the employer's board token (the slug in `boards.greenhouse.io/<token>`). No cross-company keyword search |
| **Lever Postings API** | Implemented | Public, no key | Free | Lists published postings; structured salary ranges, workplace type, and commitment | Per-employer (site slug in `jobs.lever.co/<site>`) |
| **Demo** | Implemented | n/a | n/a | Clearly labeled synthetic jobs for trying the app | Never shown as verified; can't be mixed with live results |
| USAJOBS Search API | Planned | Free API key + User-Agent email | Free | Keyword search across US federal jobs | Federal roles only |
| Adzuna API | Planned | Free developer key | Free tier with request caps | Keyword + location search across aggregated listings | Aggregated data, so verification relies on the employer link |
| LinkedIn | **Not supported for jobs** | — | — | Only "Sign In with LinkedIn" (OIDC: name, email, photo) is generally available | No public job search/apply API; scraping is prohibited and not done |

## Finding an employer's board token

Open the employer's careers page and click any job. If the URL looks like `boards.greenhouse.io/acme/jobs/123` or `job-boards.greenhouse.io/acme/...`, the token is `acme` (Greenhouse). If it looks like `jobs.lever.co/acme/...`, the site is `acme` (Lever). Add it under **Settings → Job sources**, or for every user via `JOBSCOUT_GREENHOUSE_BOARDS` / `JOBSCOUT_LEVER_SITES`.

## How verification uses each provider

- Greenhouse: `GET /v1/boards/{token}/jobs/{id}` returns 404 once a job is taken down, which marks it **closed**.
- Lever: `GET /v0/postings/{site}/{id}` returns 404 once unpublished, which marks it **closed**.
- Both: the apply link is checked with HEAD (falling back to GET). 404/410 means **closed**; other errors mean **unconfirmed**.
- Requests identify themselves with a JobScout User-Agent and are throttled to one per second per host.
