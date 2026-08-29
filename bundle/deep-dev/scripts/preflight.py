"""
.deep_dev Engine: Preflight & Environment Discovery Module (preflight.py)
=========================================================================
Verifies and fingerprints the Git baseline with valid HEAD commit, canonical project identity,
hash-locks config, discovers MCP capabilities, and checks knowledge graph freshness.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

try:
    from .config_lock import load_and_lock_config, ConfigLockResult, ConfigLockError
    from .graph_freshness import GraphFreshnessChecker
    from .memory_adapter import MemoryAdapter
except ImportError:
    from config_lock import load_and_lock_config, ConfigLockResult, ConfigLockError
    from graph_freshness import GraphFreshnessChecker
    from memory_adapter import MemoryAdapter


class PreflightCheckResult(BaseModel):
    project_id: str
    git_clean: bool
    git_head: Optional[str] = None
    workspace_fingerprint: Optional[str] = None
    config_sha256: str
    degraded_memory: bool = False
    degraded_graph: bool = False
    errors: List[str] = Field(default_factory=list)
    passed: bool


class PreflightChecker:
    """Performs comprehensive pre-flight verification before deep-dev runs."""

    @staticmethod
    def resolve_project_id(workspace_root: Path) -> str:
        """Derive a canonical safe project identifier from workspace path."""
        norm = str(workspace_root.resolve()).replace("\\", "/").lower()
        base_name = workspace_root.resolve().name.lower()
        clean_name = re.sub(r"[^a-z0-9_\-]", "_", base_name)
        short_hash = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]
        return f"{clean_name}_{short_hash}"

    @classmethod
    def check_git_clean_baseline(cls, workspace_root: Path) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Verify that workspace is a valid Git repository with an existing baseline commit (HEAD)
        and the working tree is 100% clean.
        Returns (is_clean, git_head, error_message).
        """
        # 1. Verify Git repository
        check_repo = ["git", "-C", str(workspace_root), "rev-parse", "--is-inside-work-tree"]
        repo_proc = subprocess.run(check_repo, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if repo_proc.returncode != 0:
            return False, None, f"Workspace is not a valid Git repository: '{workspace_root}'"

        # 2. Get HEAD commit (must exist)
        head_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if head_proc.returncode != 0 or not head_proc.stdout.strip():
            return (
                False,
                None,
                "Git repository has no baseline commit (HEAD does not exist). Please make an initial commit before running deep-dev.",
            )
        git_head = head_proc.stdout.strip()

        # 3. Check clean working directory
        status_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if status_proc.returncode != 0:
            return False, git_head, f"Failed to check git status: {status_proc.stderr}"

        if status_proc.stdout.strip():
            dirty_files = [line.strip() for line in status_proc.stdout.splitlines()[:5]]
            return (
                False,
                git_head,
                f"Workspace working directory is dirty. Please commit or stash changes before running deep-dev. "
                f"Uncommitted changes: {dirty_files}",
            )

        return True, git_head, None

    @classmethod
    def capture_git_baseline(
        cls,
        workspace_root: Path,
    ) -> Tuple[bool, Optional[str], Optional[str], bool, Optional[str]]:
        """Capture HEAD plus every tracked/untracked working-tree byte as a race fingerprint.

        A dirty workspace is a valid input. The fingerprint lets the orchestrator mirror
        that exact state into isolation and refuse the final write if anything changes
        concurrently.
        """
        repo_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if repo_proc.returncode != 0:
            return False, None, None, False, f"Workspace is not a valid Git repository: '{workspace_root}'"
        head_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if head_proc.returncode != 0 or not head_proc.stdout.strip():
            return False, None, None, False, "Git repository has no baseline commit (HEAD does not exist)."
        git_head = head_proc.stdout.strip()
        diff_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if diff_proc.returncode != 0:
            return False, git_head, None, False, "Failed to capture tracked working-tree baseline."
        untracked_proc = subprocess.run(
            ["git", "-C", str(workspace_root), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if untracked_proc.returncode != 0:
            return False, git_head, None, False, "Failed to enumerate untracked working-tree baseline."
        digest = hashlib.sha256()
        digest.update(git_head.encode("ascii"))
        digest.update(b"\0TRACKED\0")
        digest.update(diff_proc.stdout)
        root = workspace_root.resolve()
        untracked = [item for item in untracked_proc.stdout.split(b"\0") if item]
        for raw_path in sorted(untracked):
            relative = raw_path.decode("utf-8", errors="surrogateescape")
            candidate = (root / relative).resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError:
                return False, git_head, None, False, f"Untracked path escapes workspace: {relative}"
            if not candidate.is_file():
                return False, git_head, None, False, f"Unsupported untracked baseline entry: {relative}"
            digest.update(b"\0UNTRACKED\0")
            digest.update(raw_path)
            digest.update(b"\0")
            try:
                digest.update(candidate.read_bytes())
            except OSError as exc:
                return False, git_head, None, False, f"Failed to hash untracked path '{relative}': {exc}"
        fingerprint = digest.hexdigest()
        dirty = bool(diff_proc.stdout or untracked)
        return True, git_head, fingerprint, not dirty, None

    @classmethod
    def run_preflight(
        cls,
        workspace_root: Path,
        config_path: Optional[Path] = None,
    ) -> PreflightCheckResult:
        """Run all pre-flight verification checks."""
        errors: List[str] = []
        project_id = cls.resolve_project_id(workspace_root)

        # 1. Capture a stable Git baseline. Dirty state is supported and mirrored
        # into the isolated worktree; repository validity and a committed HEAD remain mandatory.
        git_valid, git_head, workspace_fingerprint, git_clean, git_err = cls.capture_git_baseline(workspace_root)
        if not git_valid:
            errors.append(git_err or "Git baseline check failed.")

        # 2. Hash-Lock Test Config
        cfg_file = config_path or (workspace_root / ".deep_dev" / "config.json")
        try:
            lock_res = load_and_lock_config(cfg_file)
            config_hash = lock_res.config_sha256
        except ConfigLockError as cle:
            errors.append(f"Config lock error: {cle}")
            config_hash = "unlocked_error"

        # 3. Graph Freshness
        is_fresh, graph_reason = GraphFreshnessChecker.check_freshness(workspace_root)
        degraded_graph = not is_fresh

        # 4. Memory Capability Check (real status from MemoryAdapter backend)
        degraded_memory = not MemoryAdapter.is_available()

        passed = len(errors) == 0 and git_valid and (git_head is not None) and (workspace_fingerprint is not None)

        return PreflightCheckResult(
            project_id=project_id,
            git_clean=git_clean,
            git_head=git_head,
            workspace_fingerprint=workspace_fingerprint,
            config_sha256=config_hash,
            degraded_memory=degraded_memory,
            degraded_graph=degraded_graph,
            errors=errors,
            passed=passed,
        )
