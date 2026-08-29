"""
.deep_dev Engine: Workspace Snapshot Module (snapshot.py)
=========================================================
Captures and locks allowed_paths boundary and base hashes before Coder executes.
Enforces immutable snapshot state and strict path security rules (anti-ADS, anti-traversal).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

try:
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from path_utils import canonicalize_safe_relative_path, PathSecurityError


class SnapshotFileEntry(BaseModel):
    exists: bool
    base_sha256: Optional[str] = None


class WorkspaceSnapshot(BaseModel):
    run_id: str
    git_head: Optional[str] = None
    captured_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    allowed_paths: List[str]
    files: Dict[str, SnapshotFileEntry]

    @classmethod
    def capture(
        cls,
        workspace_root: Path,
        allowed_paths: List[str],
        run_id: str,
        git_head: Optional[str] = None,
    ) -> WorkspaceSnapshot:
        """
        Capture the base state of all declared allowed_paths.
        Rejects paths violating security policy (ADS, traversal, reserved names, absolute).
        """
        normalized_allowed: List[str] = []
        files_map: Dict[str, SnapshotFileEntry] = {}
        ws_root_resolved = workspace_root.resolve()

        for raw_path in allowed_paths:
            try:
                norm = canonicalize_safe_relative_path(raw_path, allow_root_dot=False)
            except PathSecurityError as pse:
                raise ValueError(f"Security violation: invalid path '{raw_path}': {pse}") from pse

            target_full = (workspace_root / norm).resolve()
            # Ensure path is strictly within workspace_root (anti-traversal)
            try:
                target_full.relative_to(ws_root_resolved)
            except ValueError as ve:
                raise ValueError(f"Security violation: path '{raw_path}' escapes workspace root.") from ve

            normalized_allowed.append(norm)

            if target_full.exists() and target_full.is_file():
                normalized_text = target_full.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
                h = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
                files_map[norm] = SnapshotFileEntry(exists=True, base_sha256=h)
            else:
                files_map[norm] = SnapshotFileEntry(exists=False, base_sha256=None)

        return cls(
            run_id=run_id,
            git_head=git_head,
            captured_at=datetime.now(timezone.utc).isoformat(),
            allowed_paths=sorted(list(set(normalized_allowed))),
            files=files_map,
        )

    def save(self, output_path: Path) -> None:
        """Persist snapshot to JSON."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, input_path: Path) -> WorkspaceSnapshot:
        """Load snapshot from JSON."""
        content = input_path.read_text(encoding="utf-8")
        data = json.loads(content)
        return cls.model_validate(data)
