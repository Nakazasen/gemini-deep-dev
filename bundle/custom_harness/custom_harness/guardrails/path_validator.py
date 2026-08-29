"""
Custom Harness Path Boundary Validator
======================================
Implements strict path canonicalization, directory traversal defense,
Windows NTFS attack prevention (ADS, Device Names, UNC), and sandbox boundary containment.
"""

import fnmatch
import os
import re
from pathlib import Path, PurePath
from typing import List, Literal, Optional, Union

from .exceptions import (
    PathTraversalError,
    ProtectedPathAccessError,
    SecurityDenialError,
)
from .models import ActionType, GuardrailPolicy

# Regex for matching Windows reserved device names in any path segment
WINDOWS_DEVICE_NAMES_PATTERN = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$",
    re.IGNORECASE
)


class PathBoundaryValidator:
    """
    Validates filesystem paths against declarative policy rules,
    preventing directory traversal attacks, device name collisions,
    alternate data streams, and unauthorized file access.
    """

    def __init__(self, policy: GuardrailPolicy):
        self.policy = policy
        self.workspace_root = policy.workspace_root.resolve()
        self.allowed_read_roots = [
            p.resolve() for p in (policy.allowed_read_roots or [self.workspace_root])
        ]
        self.allowed_write_roots = [
            p.resolve() for p in (policy.allowed_write_roots or [self.workspace_root])
        ]

    def validate_path(
        self,
        raw_path: Union[str, Path],
        mode: Union[Literal["read", "write", "delete", "execute", "list", "mkdir", "move"], ActionType] = "write"
    ) -> Path:
        """
        Validates and canonicalizes a path for the requested access mode.
        Raises PathTraversalError or ProtectedPathAccessError if the path violates security policies.
        Returns the canonical, resolved Path.
        """
        if raw_path is None or not str(raw_path).strip():
            raise PathTraversalError("Path cannot be empty or whitespace.")

        path_str = str(raw_path).strip()

        # 1. Null byte injection check
        if "\x00" in path_str:
            raise PathTraversalError("Null byte injection detected in path.", {"path": path_str})

        # 2. Alternate Data Stream (ADS) & Illegal colon check
        # Colons are only permitted at index 1 for Windows drive letters (e.g. 'C:\path')
        colon_count = path_str.count(":")
        if colon_count > 1:
            raise PathTraversalError(
                f"Illegal multiple colons or Alternate Data Stream detected in path: '{path_str}'",
                {"path": path_str}
            )
        elif colon_count == 1:
            is_valid_drive = (
                len(path_str) >= 2
                and path_str[1] == ":"
                and path_str[0].isalpha()
            )
            if not is_valid_drive:
                raise PathTraversalError(
                    f"Alternate Data Stream (ADS) or illegal colon detected in path: '{path_str}'",
                    {"path": path_str}
                )

        # 3. UNC Network Path check
        if path_str.startswith(r"\\") or path_str.startswith("//"):
            raise PathTraversalError(
                f"UNC network paths are prohibited: '{path_str}'",
                {"path": path_str}
            )

        # 4. Windows Reserved Device Names check across all components
        parts = re.split(r"[\\/]", path_str)
        for part in parts:
            if not part:
                continue
            if WINDOWS_DEVICE_NAMES_PATTERN.match(part):
                raise PathTraversalError(
                    f"Windows reserved device name prohibited in path: '{part}' in '{path_str}'",
                    {"path": path_str, "segment": part}
                )

        # 5. Canonical Path Resolution
        target_path = Path(path_str)
        if not target_path.is_absolute():
            target_path = (self.workspace_root / target_path)

        try:
            resolved_target = target_path.resolve(strict=False)
        except Exception as e:
            raise PathTraversalError(
                f"Failed to canonicalize path '{path_str}': {e}",
                {"path": path_str, "error": str(e)}
            ) from e

        # Normalize casing for platform-independent comparison
        norm_resolved = Path(os.path.normcase(str(resolved_target)))

        # 6. Action mode normalization
        action_mode = mode.value if isinstance(mode, ActionType) else str(mode).lower()
        is_write_op = action_mode in {"write", "delete", "execute", "mkdir", "move"}
        roots_to_check = self.allowed_write_roots if is_write_op else self.allowed_read_roots

        # 7. Sandbox Containment Check
        is_contained = False
        for root in roots_to_check:
            norm_root = Path(os.path.normcase(str(root)))
            try:
                if norm_resolved.is_relative_to(norm_root):
                    is_contained = True
                    break
            except AttributeError:
                # Python < 3.9 fallback
                try:
                    common = os.path.commonpath([str(norm_root), str(norm_resolved)])
                    if common == str(norm_root):
                        is_contained = True
                        break
                except ValueError:
                    continue

        if not is_contained:
            raise PathTraversalError(
                f"Path '{path_str}' (resolved to '{resolved_target}') escapes allowed {action_mode} sandbox boundaries.",
                {
                    "path": path_str,
                    "resolved": str(resolved_target),
                    "allowed_roots": [str(r) for r in roots_to_check]
                }
            )

        # 8. Symlink Escape Verification (if symlink exists and symlinks disallowed)
        if not self.policy.allow_symlinks and resolved_target.is_symlink():
            try:
                symlink_target = resolved_target.readlink().resolve()
                norm_sym_target = Path(os.path.normcase(str(symlink_target)))
                sym_contained = any(
                    norm_sym_target.is_relative_to(Path(os.path.normcase(str(r))))
                    for r in roots_to_check
                )
                if not sym_contained:
                    raise PathTraversalError(
                        f"Symlink target '{symlink_target}' escapes allowed sandbox.",
                        {"path": path_str, "symlink_target": str(symlink_target)}
                    )
            except OSError as e:
                raise PathTraversalError(f"Error inspecting symlink '{path_str}': {e}") from e

        # 9. Allowed File Extensions Check (for write operations if configured)
        if is_write_op and self.policy.allowed_extensions is not None:
            suffix = resolved_target.suffix.lower()
            allowed_exts = [
                ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                for ext in self.policy.allowed_extensions
            ]
            if suffix not in allowed_exts:
                raise ProtectedPathAccessError(
                    f"File extension '{suffix}' for path '{path_str}' is not in allowed extensions list.",
                    {"path": path_str, "allowed_extensions": self.policy.allowed_extensions}
                )

        # 10. Denied Patterns & Protected Paths Check
        relative_to_ws: Optional[str] = None
        try:
            relative_to_ws = resolved_target.relative_to(self.workspace_root).as_posix()
        except ValueError:
            relative_to_ws = resolved_target.as_posix()

        filename = resolved_target.name
        norm_rel = relative_to_ws.lower()
        norm_name = filename.lower()

        for pattern in self.policy.denied_path_patterns:
            norm_pattern = pattern.lower()
            if (
                fnmatch.fnmatch(norm_rel, norm_pattern)
                or fnmatch.fnmatch(norm_name, norm_pattern)
                or fnmatch.fnmatch(f"/{norm_rel}", norm_pattern)
                or (pattern.startswith("**/") and fnmatch.fnmatch(norm_rel, norm_pattern[3:]))
            ):
                raise ProtectedPathAccessError(
                    f"Access to path '{path_str}' (target: '{relative_to_ws}') is blocked by denied pattern rule '{pattern}'.",
                    {"path": path_str, "relative_path": relative_to_ws, "matched_pattern": pattern}
                )

        return resolved_target

    def is_safe_path(
        self,
        raw_path: Union[str, Path],
        mode: Union[Literal["read", "write", "delete", "execute", "list", "mkdir", "move"], ActionType] = "write"
    ) -> bool:
        """
        Check if a path is safe and compliant without raising exceptions.
        Returns True if allowed, False if any security violation occurs.
        """
        try:
            self.validate_path(raw_path, mode=mode)
            return True
        except (SecurityDenialError, PathTraversalError, ProtectedPathAccessError):
            return False
        except Exception:
            return False


# Alias for backward compatibility
PathValidator = PathBoundaryValidator
