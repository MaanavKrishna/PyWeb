"""Uploads: filename sanitizing, size/type validation, magic-byte
allowlist, storage paths."""

from __future__ import annotations

import os
import re
import uuid

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

MAX_BYTES = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                      ".txt", ".csv"}

_MAGIC_BYTES = (
    (b"\x89PNG\r\n\x1a\n", {".png"}),
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    (b"GIF87a", {".gif"}),
    (b"GIF89a", {".gif"}),
    (b"RIFF", {".webp"}),
    (b"%PDF-", {".pdf"}),
)


class UploadRejected(Exception):
    """Raised when an upload fails allowlist validation."""


class ValidatedUpload:
    def __init__(self, filename, content):
        self.filename = filename
        self.content = content

    @property
    def size(self):
        return len(self.content)


def _is_textual(ext, content):
    if ext not in {".txt", ".csv"}:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in content


def validate_content(filename, content, max_bytes=MAX_BYTES):
    """Allowlist uploads by extension AND magic bytes; enforce size caps."""
    if len(content) > max_bytes:
        raise UploadRejected(
            f"upload too large: {len(content)} > {max_bytes} bytes")
    base = os.path.basename(filename)
    if not base or base != filename or "/" in filename or "\\" in filename:
        raise UploadRejected("invalid filename")
    _, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadRejected(f"extension not allowed: {ext!r}")
    if _is_textual(ext, content):
        return ValidatedUpload(base, content)
    for magic, exts in _MAGIC_BYTES:
        if ext in exts and content.startswith(magic):
            if ext == ".webp" and b"WEBP" not in content[:16]:
                break
            return ValidatedUpload(base, content)
    raise UploadRejected("content does not match extension allowlist")


def safe_filename(name, max_length=100):
    base = os.path.basename((name or "").strip())
    base = _UNSAFE.sub("_", base).strip("._")
    if len(base) > max_length:  # filesystems refuse names over 255 bytes; keep the extension
        stem, ext = os.path.splitext(base)
        ext = ext[:16]
        base = stem[:max_length - len(ext)].rstrip("._") + ext
    return base or "file"


def validate_upload(*, filename, size, content_type, allowed_types=(), max_bytes=10 * 1024 * 1024):
    errors = []
    clean = safe_filename(filename)
    if clean != os.path.basename(filename or "") or ".." in (filename or ""):
        errors.append("unsafe filename")
    if size is None or size < 0:
        errors.append("unknown size")
    elif size > max_bytes:
        errors.append(f"too large (max {max_bytes} bytes)")
    if allowed_types and content_type not in allowed_types:
        errors.append(f"unsupported type {content_type!r}")
    return clean, errors


def storage_path(filename, *, prefix="uploads"):
    return f"{prefix}/{uuid.uuid4().hex[:12]}-{safe_filename(filename)}"
