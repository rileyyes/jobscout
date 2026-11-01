"""FastAPI application factory."""

from __future__ import annotations

import base64
import hashlib
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, jobs, profile, resumes, search, tracking
from app.config import Settings
from app.db.base import make_sessionmaker
from app.emailer import ConsoleEmailSender, EmailSender
from app.security import RateLimiter
from app.sources.base import Fetcher
from app.storage import ClamdScanner, EncryptedFileStore, NullScanner, Scanner

log = logging.getLogger("jobscout")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _file_key(settings: Settings) -> bytes:
    if settings.file_encryption_key is not None:
        return settings.file_encryption_key.get_secret_value().encode()
    # Dev/test only (prod requires an explicit key): derive a stable key from the secret.
    digest = hashlib.sha256(b"jobscout-files:" + settings.secret_key.get_secret_value().encode()).digest()
    return base64.urlsafe_b64encode(digest)


def create_app(settings: Settings | None = None, *, fetcher: Fetcher | None = None,
               email_sender: EmailSender | None = None, scanner: Scanner | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    app = FastAPI(title="JobScout API", version="0.2.0",
                  docs_url="/api/docs" if settings.env != "prod" else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if settings.env != "prod" else None)
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(settings.database_url)
    app.state.rate_limiter = RateLimiter()
    app.state.file_store = EncryptedFileStore(settings.upload_dir, _file_key(settings))
    app.state.email_sender = email_sender or ConsoleEmailSender()
    app.state.scanner = scanner or (ClamdScanner(settings.clamd_address) if settings.clamd_address else NullScanner())
    if fetcher is None:
        from app.sources.base import HttpxFetcher
        fetcher = HttpxFetcher()
    app.state.fetcher = fetcher

    app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")  # responses contain personal data
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Something went wrong on our side."}, status_code=500)

    @app.get("/api/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    for r in (auth.router, profile.router, resumes.router, search.router, jobs.router, tracking.router):
        app.include_router(r)
    return app
