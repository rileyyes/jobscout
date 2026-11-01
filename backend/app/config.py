"""Environment-based configuration. All variables use the JOBSCOUT_ prefix (see .env.example)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-insecure-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JOBSCOUT_", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://jobscout:jobscout@localhost:5432/jobscout"
    secret_key: SecretStr = SecretStr(_DEV_SECRET)
    # Fernet key (urlsafe base64, 32 bytes) used to encrypt uploaded files at rest.
    file_encryption_key: SecretStr | None = None

    frontend_origin: str = "http://localhost:3000"
    cookie_secure: bool = True
    session_ttl_hours: int = 24 * 7
    password_reset_ttl_minutes: int = 30

    upload_dir: Path = Path("./var/uploads")
    max_upload_mb: int = 5
    resume_retention_hours: int = 24  # unsaved resumes + extracted text are purged after this

    # Optional ClamAV daemon for malware scanning, e.g. "/var/run/clamav/clamd.ctl" or "127.0.0.1:3310".
    clamd_address: str | None = None

    # Global employer boards searched for every user: "token:Display Name,token2:Name"
    greenhouse_boards: str = ""
    lever_sites: str = ""

    @model_validator(mode="after")
    def _prod_requirements(self) -> "Settings":
        if self.env == "prod":
            if self.secret_key.get_secret_value() == _DEV_SECRET or len(self.secret_key.get_secret_value()) < 32:
                raise ValueError("JOBSCOUT_SECRET_KEY must be set to a random value of 32+ characters in prod.")
            if self.file_encryption_key is None:
                raise ValueError("JOBSCOUT_FILE_ENCRYPTION_KEY is required in prod.")
            if not self.cookie_secure:
                raise ValueError("JOBSCOUT_COOKIE_SECURE must be true in prod.")
        return self

    @property
    def session_cookie_name(self) -> str:
        # __Host- prefix forces Secure, Path=/ and no Domain in browsers that support it.
        return "__Host-jobscout_session" if self.cookie_secure else "jobscout_session"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


def parse_board_list(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        token, _, name = item.partition(":")
        if token.strip():
            out[token.strip()] = name.strip() or token.strip()
    return out
