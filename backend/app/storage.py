"""Private, encrypted file storage plus upload validation and malware-scan hook.

Files are written outside any served directory under random names, encrypted
with Fernet (AES-128-CBC + HMAC-SHA256), and only ever returned through an
authenticated, owner-checked endpoint. There are no public file URLs.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import socket
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

_KEY_RX = re.compile(r"^[0-9a-f]{32}$")


class EncryptedFileStore:
    def __init__(self, root: Path, key: bytes) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._f = Fernet(key)

    def _path(self, key: str) -> Path:
        if not _KEY_RX.match(key):
            raise ValueError("invalid storage key")
        return self.root / key[:2] / key

    def save(self, data: bytes) -> str:
        key = secrets.token_hex(16)
        p = self._path(key)
        p.parent.mkdir(exist_ok=True, mode=0o700)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(self._f.encrypt(data))
        return key

    def load(self, key: str) -> bytes:
        return self._f.decrypt(self._path(key).read_bytes())

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def encrypt_text(self, text: str) -> str:
        return self._f.encrypt(text.encode()).decode()

    def decrypt_text(self, token: str) -> str:
        return self._f.decrypt(token.encode()).decode()


# ---------------------------------------------------------------- validation

class UploadRejected(ValueError):
    pass


PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED = {".pdf": PDF, ".docx": DOCX}

_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)\b")
MAX_ZIP_ENTRIES = 500
MAX_ZIP_UNCOMPRESSED = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100
MAX_PDF_PAGES = 20


@dataclass
class ValidatedFile:
    content_type: str
    extension: str
    safe_name: str


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "resume").replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "resume"
    return base[:200]


def validate_upload(filename: str, data: bytes, max_bytes: int) -> ValidatedFile:
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED:
        raise UploadRejected("Only PDF and DOCX files are accepted.")
    if ext == ".pdf":
        _check_pdf(data)
    else:
        _check_docx(data)
    return ValidatedFile(content_type=ALLOWED[ext], extension=ext, safe_name=safe_filename(filename))


def _check_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-"):
        raise UploadRejected("This file isn't a valid PDF.")
    if _PDF_ACTIVE.search(data):
        raise UploadRejected("PDFs with scripts, attachments, or interactive forms aren't accepted. "
                             "Try 'Print to PDF' to make a plain copy.")
    if b"/Encrypt" in data:
        raise UploadRejected("Password-protected PDFs aren't accepted.")
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if len(reader.pages) > MAX_PDF_PAGES:
            raise UploadRejected(f"Resumes over {MAX_PDF_PAGES} pages aren't accepted.")
    except UploadRejected:
        raise
    except Exception as e:  # pypdf raises many types on malformed input
        raise UploadRejected("This PDF couldn't be read.") from e


def _check_docx(data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadRejected("This file isn't a valid DOCX.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise UploadRejected("This DOCX has too many parts.")
            total = sum(i.file_size for i in infos)
            if total > MAX_ZIP_UNCOMPRESSED or total > MAX_ZIP_RATIO * max(len(data), 1):
                raise UploadRejected("This DOCX expands to an unsafe size.")
            names = {i.filename for i in infos}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise UploadRejected("This file isn't a valid DOCX.")
            if any(n.lower().endswith("vbaproject.bin") or n.lower().startswith("word/embeddings/") for n in names):
                raise UploadRejected("Documents with macros or embedded objects aren't accepted.")
            if any(".." in n or n.startswith("/") for n in names):
                raise UploadRejected("This DOCX has invalid internal paths.")
    except zipfile.BadZipFile as e:
        raise UploadRejected("This file isn't a valid DOCX.") from e


def extract_text(data: bytes, ext: str, max_chars: int = 60_000) -> str:
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
        text = "\n".join((p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES])
    else:
        import docx  # python-docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        text = "\n".join(parts)
    return text[:max_chars]


# ----------------------------------------------------------------- scanning

class Scanner(Protocol):
    def scan(self, data: bytes) -> str:
        """Return 'clean', 'infected', or 'not_scanned'."""


class NullScanner:
    def scan(self, data: bytes) -> str:
        return "not_scanned"


class ClamdScanner:
    """Minimal clamd INSTREAM client. address: unix socket path or host:port."""

    def __init__(self, address: str, timeout: float = 30.0) -> None:
        self.address, self.timeout = address, timeout

    def _connect(self) -> socket.socket:
        if ":" in self.address and not self.address.startswith("/"):
            host, port = self.address.rsplit(":", 1)
            return socket.create_connection((host, int(port)), timeout=self.timeout)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.address)
        return s

    def scan(self, data: bytes) -> str:
        with self._connect() as s:
            s.sendall(b"zINSTREAM\0")
            for i in range(0, len(data), 64 * 1024):
                chunk = data[i:i + 64 * 1024]
                s.sendall(struct.pack("!L", len(chunk)) + chunk)
            s.sendall(struct.pack("!L", 0))
            reply = s.recv(4096).decode(errors="replace").strip("\0 \n")
        if reply.endswith("OK"):
            return "clean"
        if "FOUND" in reply:
            return "infected"
        raise RuntimeError(f"clamd error: {reply[:100]}")
