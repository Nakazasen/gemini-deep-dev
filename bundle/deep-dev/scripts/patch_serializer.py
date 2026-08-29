"""
.deep_dev Engine: Patch Serializer Module (patch_serializer.py)
===============================================================
Validates in-memory FileOperations against WorkspaceSnapshot boundary, strict path
security rules, and base-hashes. Serializes verified operations into a Git-compatible
Unified Diff patch artifact.
"""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

try:
    from .snapshot import WorkspaceSnapshot
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from snapshot import WorkspaceSnapshot
    from path_utils import canonicalize_safe_relative_path, PathSecurityError


class PatchSerializationError(Exception):
    """Base exception for patch serialization failures."""
    pass


class UnauthorizedTargetPathError(PatchSerializationError):
    """Raised when an operation targets a file outside snapshot.allowed_paths or violates security policy."""
    pass


class CreateOnExistingFileError(PatchSerializationError):
    """Raised when create action targets a file that already existed during snapshot."""
    pass


class BaseHashConflictError(PatchSerializationError):
    """Raised when on-disk file hash no longer matches snapshot base_sha256."""
    pass


class PatchSerializer:
    """Validates operations and generates unified diffs."""

    @staticmethod
    def _normalize_path(raw_path: str) -> str:
        try:
            return canonicalize_safe_relative_path(raw_path, allow_root_dot=False)
        except PathSecurityError as pse:
            raise UnauthorizedTargetPathError(
                f"BLOCKED: UnauthorizedTargetPath. Path '{raw_path}' violates security policy: {pse}"
            ) from pse

    @classmethod
    def serialize_operations(
        cls,
        snapshot: WorkspaceSnapshot,
        file_operations: List[Dict[str, Any]],
        workspace_root: Path,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Validate all operations and generate a Git-compatible Unified Diff patch.
        """
        diff_blocks: List[str] = []
        op_metadata: List[Dict[str, Any]] = []

        for op in file_operations:
            raw_path = op.get("file_path", "")
            action = op.get("action", "noop").lower()
            # The canonical MCP proposal contract uses ``content``.  Older
            # harness emitters used ``content_or_diff``; accept both so a
            # verified operation can never be recorded while serializing an
            # empty patch.  Reject non-text content before it reaches the
            # diff generator.
            content = op.get("content", op.get("content_or_diff", ""))
            if not isinstance(content, str):
                raise ValueError(
                    f"BLOCKED: Operation content for '{raw_path}' must be text."
                )

            # 1. Path Security Normalization
            norm_path = cls._normalize_path(raw_path)

            if not norm_path:
                continue

            # 2. Boundary Check: Must be in snapshot.allowed_paths
            if norm_path not in snapshot.allowed_paths:
                raise UnauthorizedTargetPathError(
                    f"BLOCKED: UnauthorizedTargetPath. Operation on '{norm_path}' is not permitted. "
                    f"Allowed paths: {snapshot.allowed_paths}"
                )

            entry = snapshot.files.get(norm_path)
            if entry is None:
                raise UnauthorizedTargetPathError(
                    f"BLOCKED: UnauthorizedTargetPath. Path '{norm_path}' not registered in snapshot boundary."
                )

            target_file = workspace_root / norm_path

            # Gemini commonly uses the user-facing verb "create" even after
            # read-only discovery found a previous accepted output.  Within a
            # signed snapshot boundary, this is safely equivalent to modify:
            # the existing base hash must still match below before any patch is
            # produced.  Preserve strict create behavior only for absent files.
            if action == "create" and entry.exists:
                action = "modify"

            # 3. Action: NOOP
            if action == "noop":
                continue

            # 4. Action: CREATE
            elif action == "create":
                new_lines = content.replace("\r\n", "\n").splitlines(keepends=True)
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"

                diff = difflib.unified_diff(
                    [],
                    new_lines,
                    fromfile="/dev/null",
                    tofile=f"b/{norm_path}",
                    lineterm="\n",
                )
                diff_text = "".join(diff)
                if diff_text:
                    diff_blocks.append(diff_text)
                op_metadata.append({"file_path": norm_path, "action": "create", "base_sha256": None})

            # 5. Action: MODIFY
            elif action == "modify":
                if not entry.exists:
                    raise BaseHashConflictError(
                        f"BLOCKED: ModifyOnMissingFile. Action 'modify' attempted on missing file '{norm_path}'."
                    )

                if not target_file.exists():
                    raise BaseHashConflictError(
                        f"BLOCKED: BaseHashConflict. File '{norm_path}' missing from disk at serialization."
                    )

                current_text = target_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
                current_sha256 = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
                if current_sha256 != entry.base_sha256:
                    raise BaseHashConflictError(
                        f"BLOCKED: BaseHashConflict. Base SHA-256 mismatch on '{norm_path}'. "
                        f"Expected {entry.base_sha256}, got {current_sha256}."
                    )

                old_lines = current_text.splitlines(keepends=True)
                new_lines = content.replace("\r\n", "\n").splitlines(keepends=True)
                if new_lines and not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"

                diff = difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{norm_path}",
                    tofile=f"b/{norm_path}",
                    lineterm="\n",
                )
                diff_text = "".join(diff)
                if diff_text:
                    diff_blocks.append(diff_text)
                op_metadata.append({"file_path": norm_path, "action": "modify", "base_sha256": entry.base_sha256})

            # 6. Action: DELETE
            elif action == "delete":
                if not entry.exists or not target_file.exists():
                    raise BaseHashConflictError(
                        f"BLOCKED: DeleteOnMissingFile. File '{norm_path}' does not exist to delete."
                    )

                current_text = target_file.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
                current_sha256 = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
                if current_sha256 != entry.base_sha256:
                    raise BaseHashConflictError(
                        f"BLOCKED: BaseHashConflict. Base SHA-256 mismatch on '{norm_path}' before delete."
                    )

                old_lines = current_text.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines,
                    [],
                    fromfile=f"a/{norm_path}",
                    tofile="/dev/null",
                    lineterm="\n",
                )
                diff_text = "".join(diff)
                if diff_text:
                    diff_blocks.append(diff_text)
                op_metadata.append({"file_path": norm_path, "action": "delete", "base_sha256": entry.base_sha256})

        final_patch = "\n".join(diff_blocks)
        if final_patch and not final_patch.endswith("\n"):
            final_patch += "\n"

        meta = {
            "run_id": snapshot.run_id,
            "git_head": snapshot.git_head,
            "total_operations": len(op_metadata),
            "operations": op_metadata,
        }
        return final_patch, meta
