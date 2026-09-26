"""PyWeb upload validation: extension + magic-byte allowlist with size caps."""
import os

MAX_BYTES = 5 * 1024 * 1024

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
                      ".txt", ".csv"}

MAGIC_BYTES = (
    (b"\x89PNG\r\n\x1a\n", {".png"}),
    (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    (b"GIF87a", {".gif"}),
    (b"GIF89a", {".gif"}),
    (b"RIFF", {".webp"}),
    (b"%PDF-", {".pdf"}),
)


class UploadRejected(Exception):
    """Raised when an upload fails validation."""


class ValidatedUpload:
    def __init__(self, filename: str, content: bytes):
        self.filename = filename
        self.content = content

    @property
    def size(self) -> int:
        return len(self.content)


def _is_textual(ext: str, content: bytes) -> bool:
    if ext not in {".txt", ".csv"}:
        return False
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in content


def validate_upload(filename: str, content: bytes,
                    max_bytes: int = MAX_BYTES) -> ValidatedUpload:
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
    for magic, exts in MAGIC_BYTES:
        if ext in exts and content.startswith(magic):
            if ext == ".webp" and b"WEBP" not in content[:16]:
                break
            return ValidatedUpload(base, content)
    raise UploadRejected("content does not match extension allowlist")
