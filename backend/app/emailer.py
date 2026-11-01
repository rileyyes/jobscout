"""Outgoing email. Replace ConsoleEmailSender with an SMTP/provider sender in production."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger("jobscout.email")


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """Development only: writes the message to the server log instead of sending it."""

    def send(self, to: str, subject: str, body: str) -> None:
        log.warning("DEV EMAIL (not sent) to=%s subject=%s\n%s", to, subject, body)


@dataclass
class MemoryEmailSender:
    """Test double."""

    outbox: list[tuple[str, str, str]] = field(default_factory=list)

    def send(self, to: str, subject: str, body: str) -> None:
        self.outbox.append((to, subject, body))
