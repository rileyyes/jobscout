"""Shared FastAPI dependencies: settings, DB session, authentication, CSRF, rate limits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.base import as_utc, utcnow
from app.db.models import User, UserSession
from app.security import RateLimiter, token_hash, tokens_match

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "X-CSRF-Token"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Session]:
    db: Session = request.app.state.sessionmaker()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@dataclass
class Auth:
    user: User
    session: UserSession


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")


def get_auth(request: Request, db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)) -> Auth:
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise _unauthorized()
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(raw)))
    now = utcnow()
    if sess is None or as_utc(sess.expires_at) <= now:
        raise _unauthorized()
    if request.method not in SAFE_METHODS and not tokens_match(request.headers.get(CSRF_HEADER), sess.csrf_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing or invalid CSRF token.")
    if now - as_utc(sess.last_seen_at) > timedelta(minutes=5):
        sess.last_seen_at = now
        db.commit()
    return Auth(user=sess.user, session=sess)


def current_user_id(auth: Auth = Depends(get_auth)) -> uuid.UUID:
    return auth.user.id


def client_ip(request: Request) -> str:
    # Behind a reverse proxy, run uvicorn with --proxy-headers --forwarded-allow-ips=<proxy>
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_s: float):
    def dep(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        if not limiter.hit(f"{name}:{client_ip(request)}", limit, window_s):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Please wait and try again.",
                                headers={"Retry-After": str(int(window_s))})
    return dep


def not_found(what: str = "Item") -> HTTPException:
    # 404 (not 403) for other users' records so IDs can't be probed.
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found.")
