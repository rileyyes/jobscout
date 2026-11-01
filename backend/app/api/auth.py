"""Registration, login/logout, password reset, and account deletion."""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.base import as_utc, utcnow
from app.db.models import PasswordResetToken, Resume, User, UserSession
from app.security import (
    PASSWORD_MAX,
    PASSWORD_MIN,
    hash_password,
    needs_rehash,
    new_token,
    token_hash,
    verify_password,
)

from .deps import Auth, get_auth, get_db, get_settings, rate_limit

log = logging.getLogger("jobscout.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])

Password = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Password
    display_name: str | None = Field(default=None, max_length=100)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class MeOut(BaseModel):
    id: str
    email: str
    display_name: str | None
    csrf_token: str | None = None


class ResetRequestIn(BaseModel):
    email: EmailStr


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Password


class DeleteAccountIn(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)
    confirm: bool


def _weak(password: str, email: str) -> str | None:
    if password.lower() in email.lower() or email.split("@")[0].lower() in password.lower():
        return "Password must not contain your email address."
    if len(set(password)) < 5:
        return "Password is too repetitive."
    return None


def _start_session(db: Session, user: User, request: Request, response: Response, settings: Settings) -> str:
    raw, csrf = new_token(), new_token()
    db.add(UserSession(user_id=user.id, token_hash=token_hash(raw), csrf_hash=token_hash(csrf),
                       expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
                       user_agent=(request.headers.get("user-agent") or "")[:300]))
    db.commit()
    response.set_cookie(settings.session_cookie_name, raw, max_age=settings.session_ttl_hours * 3600,
                        httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    return csrf


def _clear_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/", secure=settings.cookie_secure,
                           httponly=True, samesite="lax")


@router.post("/register", response_model=MeOut, status_code=201,
             dependencies=[Depends(rate_limit("register", 5, 3600))])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings)):
    email = body.email.lower()
    if problem := _weak(body.password, email):
        raise HTTPException(422, problem)
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email may already exist. Try signing in.")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    db.flush()
    csrf = _start_session(db, user, request, response, settings)
    return MeOut(id=str(user.id), email=user.email, display_name=user.display_name, csrf_token=csrf)


@router.post("/login", response_model=MeOut, dependencies=[Depends(rate_limit("login", 10, 300))])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db),
          settings: Settings = Depends(get_settings)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    csrf = _start_session(db, user, request, response, settings)
    return MeOut(id=str(user.id), email=user.email, display_name=user.display_name, csrf_token=csrf)


@router.post("/logout", status_code=204)
def logout(response: Response, auth: Auth = Depends(get_auth), db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings)):
    db.execute(delete(UserSession).where(UserSession.id == auth.session.id))
    db.commit()
    _clear_cookie(response, settings)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(auth: Auth = Depends(get_auth)):
    return MeOut(id=str(auth.user.id), email=auth.user.email, display_name=auth.user.display_name)


@router.post("/csrf", response_model=MeOut)
def rotate_csrf(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Issue a fresh CSRF token for an existing session (e.g. after a page reload).

    Safe without a CSRF header: the response body is only readable same-origin (CORS),
    and it changes no user data.
    """
    from app.db.models import UserSession as S

    raw = request.cookies.get(settings.session_cookie_name)
    sess = db.scalar(select(S).where(S.token_hash == token_hash(raw))) if raw else None
    if sess is None or as_utc(sess.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in.")
    csrf = new_token()
    sess.csrf_hash = token_hash(csrf)
    db.commit()
    return MeOut(id=str(sess.user.id), email=sess.user.email, display_name=sess.user.display_name, csrf_token=csrf)


@router.post("/password-reset/request", status_code=202,
             dependencies=[Depends(rate_limit("reset", 5, 3600))])
def request_reset(body: ResetRequestIn, request: Request, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings)):
    """Always 202, whether or not the account exists (no account enumeration)."""
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user:
        raw = new_token()
        db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash(raw),
                                  expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes)))
        db.commit()
        link = f"{settings.frontend_origin}/reset-password#token={raw}"
        request.app.state.email_sender.send(
            user.email, "Reset your JobScout password",
            f"Use this link within {settings.password_reset_ttl_minutes} minutes to reset your password:\n{link}\n\n"
            "If you didn't ask for this, you can ignore this email.")
    return {"detail": "If that account exists, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=204,
             dependencies=[Depends(rate_limit("reset-confirm", 10, 3600))])
def confirm_reset(body: ResetConfirmIn, db: Session = Depends(get_db)):
    tok = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash(body.token)))
    if tok is None or tok.used_at is not None or as_utc(tok.expires_at) <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired.")
    user = db.get(User, tok.user_id)
    if problem := _weak(body.new_password, user.email):
        raise HTTPException(422, problem)
    user.password_hash = hash_password(body.new_password)
    tok.used_at = utcnow()
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))  # sign out everywhere
    db.commit()
    return Response(status_code=204)


@router.delete("/account", status_code=204)
def delete_account(body: DeleteAccountIn, request: Request, response: Response, auth: Auth = Depends(get_auth),
                   db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    if not body.confirm or not verify_password(auth.user.password_hash, body.password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password confirmation failed.")
    store = request.app.state.file_store
    for key in db.scalars(select(Resume.storage_key).where(Resume.user_id == auth.user.id)):
        if key:
            store.delete(key)
    db.delete(auth.user)  # FK cascades remove all user-owned rows
    db.commit()
    log.info("account deleted user_id=%s", auth.user.id)
    _clear_cookie(response, settings)
    response.status_code = 204
    return response
