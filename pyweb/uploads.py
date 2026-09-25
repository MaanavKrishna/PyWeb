"""Uploads: filename sanitizing, size/type validation, storage paths."""

from __future__ import annotations

import os
import re
import uuid

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name):
    base = os.path.basename((name or "").strip())
    base = _UNSAFE.sub("_", base).strip("._")
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
