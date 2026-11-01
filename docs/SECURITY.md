# Security design

| Concern | How JobScout handles it | Where |
|---|---|---|
| Passwords | argon2id, 12–128 chars, rejects passwords containing your email or that are too repetitive; rehashed automatically when parameters change | `app/security.py`, `app/api/auth.py` |
| Sessions | Random 256-bit token in an `HttpOnly`, `SameSite=Lax`, `Secure` cookie (`__Host-` prefixed in prod). Only its SHA-256 hash is stored, so a database leak doesn't expose live sessions. 7-day expiry | `app/api/deps.py` |
| CSRF | Per-session token returned in the login response and required in `X-CSRF-Token` on every non-GET request. Stored hashed and can be rotated with `POST /api/auth/csrf` | `app/api/deps.py` |
| Account enumeration | Login gives the same error for unknown emails and wrong passwords, with timing equalized by verifying against a dummy hash. Password-reset requests always return 202 | `app/api/auth.py` |
| Rate limiting | Login 10/5 min, register 5/h, reset 5/h, uploads 20/h, searches 30/h, rechecks 60/h, all per IP | `app/security.py` (in-memory; use Redis if running more than one instance) |
| Password reset | Single-use token stored hashed, valid 30 min, delivered in the URL fragment (not sent to servers in logs or Referer). Signs out every session on success | `app/api/auth.py` |
| Authorization | Every user-owned query filters by `user_id`. Other users' records return 404 so IDs can't be probed. Covered by IDOR tests | `app/api/tracking.py`, `tests/api/test_authorization.py` |
| Account deletion | Requires your password. Deletes stored files, then the user row; foreign-key cascades remove every user-owned row | `app/api/auth.py` |
| Uploads | PDF/DOCX only, checked by magic bytes and structure. PDFs with JavaScript, launch actions, attachments, XFA, or encryption are rejected. DOCX files with macros or embedded objects, zip bombs, or path traversal are rejected. 5 MB and 20-page limits. Filenames are sanitized | `app/storage.py` |
| Malware scanning | ClamAV via `JOBSCOUT_CLAMD_ADDRESS`. Without it, files are recorded as `not_scanned` rather than claimed clean. A scanner error fails closed (503) | `app/storage.py` |
| Files at rest | Fernet-encrypted (AES-128-CBC + HMAC), random names, `0600` permissions, outside any web root. Downloads only through an owner-checked endpoint with `Content-Disposition: attachment`. No public URLs | `app/storage.py`, `app/api/resumes.py` |
| Resume retention | Unsaved resumes and all extracted text are purged after 24h (configurable). Keeping a file needs an explicit choice | `app/api/resumes.py` |
| Untrusted job text | HTML/script stripped, control and zero-width characters removed, injection phrases flagged and shown to the user. Text sent to an LLM is wrapped in random boundaries. Scores are deterministic, so job text can't change them | `app/core/untrusted.py` |
| Headers | CSP `default-src 'none'`, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, HSTS when secure, `Cache-Control: no-store` on all API responses | `app/main.py` |
| Secrets | Environment only. Prod refuses to start with the dev secret, without a file key, or with insecure cookies | `app/config.py` |
| LinkedIn | No scraping and no password collection. Only official OIDC sign-in (name/email) is planned, and no LinkedIn credentials or cookies are ever stored | `docs/PLAN.md` |

## Known limitations

- The rate limiter lives in process memory, so it resets on restart and isn't shared across instances.
- No email verification on sign-up yet. Password reset proves control of the address.
- No MFA yet.
- Without ClamAV configured, uploads aren't malware-scanned. They are still structurally validated and never executed or served inline.
