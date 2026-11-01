"""Password hashing, opaque session/CSRF tokens, and in-process rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-recommended family)
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")

PASSWORD_MIN = 12
PASSWORD_MAX = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-ish time: verifies against a dummy hash when the user doesn't exist."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    """Tokens are stored only as SHA-256 hashes; a DB leak doesn't reveal live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str | None, stored_hash: str) -> bool:
    if not token:
        return False
    return hmac.compare_digest(token_hash(token), stored_hash)


class RateLimiter:
    """Sliding-window limiter, in process memory.

    Fine for a single API process. For multiple instances, back this with Redis
    (documented in docs/DEPLOYMENT.md).
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """Record a hit; return False if the key is over its limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
