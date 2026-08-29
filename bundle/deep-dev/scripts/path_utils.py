"""
.deep_dev Engine: Path Security & Canonicalization Utilities (path_utils.py)
============================================================================
Shared path validation and canonicalization rules across config_lock, snapshot,
patch_serializer, and test_executor.
Enforces strict fail-closed validation: no stripping of whitespace, no duplicate
separators, no trailing spaces/dots, anti-ADS, anti-traversal, and anti-device names.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

RESERVED_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class PathSecurityError(ValueError):
    """Raised when a path violates security constraints."""
    pass


def canonicalize_safe_relative_path(raw_path: str, allow_root_dot: bool = False) -> str:
    """
    Validate and normalize relative paths strictly (Fail-Closed, Zero-Mutation).

    Rejects:
    - Path with leading or trailing whitespace
    - Segments with leading or trailing whitespace
    - Duplicate separators / empty segments (e.g. 'src//file.py', '/file.py', 'file.py/')
    - Absolute paths (starts with / or \\)
    - UNC paths (//... or \\\\...)
    - Drive-qualified paths (C:... or D:...)
    - Path traversal (..) or redundant '.' segments
    - NTFS Alternate Data Streams (contains :)
    - Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) including variants
    - Segments with trailing dots
    - Forbidden characters (<, >, ", |, ?, *)

    Preserves:
    - Non-ambiguous internal whitespace (e.g. 'docs/my file.md')
    """
    if not isinstance(raw_path, str):
        raise PathSecurityError("Path must be a string.")

    if not raw_path:
        raise PathSecurityError("Path cannot be empty.")

    # 1. Reject leading or trailing whitespace on raw_path (Fail-Closed)
    if raw_path != raw_path.strip():
        raise PathSecurityError(f"Leading or trailing whitespace in path is forbidden: '{raw_path}'")

    # Check root dot
    if raw_path == ".":
        if allow_root_dot:
            return "."
        raise PathSecurityError("Root dot '.' is not allowed here.")

    # Check UNC paths
    if raw_path.startswith("//") or raw_path.startswith("\\\\"):
        raise PathSecurityError(f"UNC paths are forbidden: '{raw_path}'")

    # Check Drive letter paths (e.g. C:, D:)
    if re.match(r"^[a-zA-Z]:", raw_path):
        raise PathSecurityError(f"Drive-qualified paths are forbidden: '{raw_path}'")

    # Check absolute path prefixes
    if raw_path.startswith("/") or raw_path.startswith("\\"):
        raise PathSecurityError(f"Absolute paths are forbidden: '{raw_path}'")

    # Check NTFS ADS (Alternate Data Streams)
    if ":" in raw_path:
        raise PathSecurityError(f"NTFS Alternate Data Streams (ADS) containing ':' are forbidden: '{raw_path}'")

    # Normalize backslashes to forward slashes
    norm = raw_path.replace("\\", "/")

    # Split segments
    segments = norm.split("/")

    for seg in segments:
        # 2. Reject empty segments (caused by duplicate slashes 'src//file' or trailing slashes 'dir/')
        if seg == "":
            raise PathSecurityError(f"Empty segment or duplicate separator is forbidden: '{raw_path}'")

        # 3. Reject leading/trailing whitespace in individual segment
        if seg != seg.strip():
            raise PathSecurityError(f"Leading or trailing whitespace in segment is forbidden: '{seg}' in '{raw_path}'")

        # 4. Reject trailing dots in segment
        if seg.endswith("."):
            raise PathSecurityError(f"Trailing dots in segment are forbidden: '{seg}' in '{raw_path}'")

        # 5. Reject path traversal
        if seg == "..":
            raise PathSecurityError(f"Path traversal ('..') is forbidden: '{raw_path}'")

        # 6. Reject redundant dot segment
        if seg == ".":
            raise PathSecurityError(f"Redundant '.' segment in path is forbidden: '{raw_path}'")

        # 7. Check Windows Reserved Device Names
        base_stem = seg.split(".", 1)[0].rstrip(" .").upper()
        full_stem = seg.rstrip(" .").upper()
        if base_stem in RESERVED_DEVICE_NAMES or full_stem in RESERVED_DEVICE_NAMES:
            raise PathSecurityError(f"Windows reserved device name is forbidden: '{seg}' in '{raw_path}'")

        # 8. Check invalid characters
        if any(ord(c) < 32 or c in '<>"|?*' for c in seg):
            raise PathSecurityError(f"Invalid path characters in '{seg}' of '{raw_path}'")

    return "/".join(segments)
