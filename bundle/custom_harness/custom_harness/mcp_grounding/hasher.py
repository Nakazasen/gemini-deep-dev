"""Canonical text normalization and SHA-256 hashing utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union
import unicodedata


def normalize_canonical_text(text: str) -> str:
    """Normalize text into canonical form for deterministic hashing.

    Steps applied:
    1. Unicode NFKC normalization.
    2. Convert Windows CRLF (\\r\\n), multiple \\r, and classic Mac CR (\\r) to Unix LF (\\n).
    3. Strip trailing whitespace (spaces and tabs) from every individual line.
    4. Strip leading/trailing blank lines and ensure a single terminal newline if non-empty.
    """
    if not text:
        return ""

    # 1. Unicode NFKC normalization
    normalized = unicodedata.normalize("NFKC", text)

    # 2. Line ending normalization (collapses multiple CRs and converts CRLF/CR to LF)
    import re
    normalized = re.sub(r"\r+", "\r", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Strip trailing whitespace per line
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]

    # 4. Remove leading and trailing empty lines
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    if not lines:
        return ""

    return "\n".join(lines) + "\n"


# Export alias
normalize_content_for_hashing = normalize_canonical_text


def compute_canonical_hash(content: Union[str, bytes]) -> str:
    """Compute the 64-character lowercase SHA-256 hash of canonical UTF-8 content."""
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")
    else:
        text = content

    canonical_text = normalize_canonical_text(text)
    canonical_bytes = canonical_text.encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest().lower()


def compute_file_hash(filepath: Union[str, Path]) -> str:
    """Read a local file as UTF-8 and compute its canonical SHA-256 hash."""
    path = Path(filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Cannot hash non-existent file: {path}")

    raw_bytes = path.read_bytes()
    return compute_canonical_hash(raw_bytes)


def extract_short_hash(sha256_hash: str, length: int = 8) -> str:
    """Extract short hex prefix from SHA-256 hash."""
    clean = sha256_hash.strip().lower()
    return clean[:length]


def verify_content_hash(content: Union[str, bytes], expected_hash: str) -> bool:
    """Verify if content produces the expected canonical SHA-256 hash or hash prefix."""
    actual_hash = compute_canonical_hash(content)
    expected = expected_hash.strip().lower()

    if len(expected) == 64:
        return actual_hash == expected
    return actual_hash.startswith(expected)


__all__ = [
    "normalize_canonical_text",
    "normalize_content_for_hashing",
    "compute_canonical_hash",
    "compute_file_hash",
    "extract_short_hash",
    "verify_content_hash",
]
