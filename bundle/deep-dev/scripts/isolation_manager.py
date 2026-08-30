r"""
.deep_dev Engine: Isolation Manager Module (isolation_manager.py)
=================================================================
Manages isolated Git worktrees in external storage (%LOCALAPPDATA%\deep-dev\worktrees\),
ensuring the main repository workspace is NEVER modified during staging, patch testing,
or verification. Enforces fail-closed snapshot commit boundary and verifiable
evidence-based worktree cleanup.
"""

from __future__ import annotations

import os
import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .snapshot import WorkspaceSnapshot
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from snapshot import WorkspaceSnapshot
    from path_utils import canonicalize_safe_relative_path, PathSecurityError


class IsolationManagerError(Exception):
    """Raised when git worktree or patch application operations fail."""
    pass


class IsolationManager:
    """Controls isolated external git worktrees with strict boundary enforcement."""

    @staticmethod
    def get_runtime_root() -> Path:
        """Resolve external root directory in %LOCALAPPDATA%."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data) / "deep-dev"
        else:
            base = Path.home() / ".deep-dev"
        return base

    @classmethod
    def get_worktree_path(cls, project_id: str, run_id: str) -> Path:
        return cls.get_runtime_root() / "worktrees" / project_id / run_id

    @classmethod
    def get_runs_path(cls, project_id: str, run_id: str) -> Path:
        return cls.get_runtime_root() / "runs" / project_id / run_id

    @classmethod
    def is_git_repository(cls, repo_path: Path) -> Tuple[bool, str]:
        """Check if path is a valid git repository."""
        if not repo_path.exists() or not repo_path.is_dir():
            return False, f"Directory does not exist: '{repo_path}'"
        cmd = ["git", "-C", str(repo_path), "rev-parse", "--is-inside-work-tree"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if proc.returncode != 0:
            return False, f"Not a valid git repository: '{repo_path}' ({proc.stderr.strip() or proc.stdout.strip()})"
        return True, "Valid git repository"

    @classmethod
    def create_worktree(
        cls,
        main_repo: Path,
        project_id: str,
        run_id: str,
    ) -> Path:
        """Create a dedicated external git worktree on a temporary branch."""
        is_git, err = cls.is_git_repository(main_repo)
        if not is_git:
            raise IsolationManagerError(err)

        wt_path = cls.get_worktree_path(project_id, run_id)
        if wt_path.exists():
            ok, msg = cls.cleanup_worktree(main_repo, wt_path, project_id=project_id, run_id=run_id, delete_branch=True)
            if not ok:
                raise IsolationManagerError(f"Pre-existing worktree at '{wt_path}' could not be cleaned: {msg}")

        wt_path.parent.mkdir(parents=True, exist_ok=True)
        branch_name = f"deep-dev/{run_id}"

        cmd = [
            "git",
            "-C", str(main_repo),
            "worktree", "add",
            "-b", branch_name,
            str(wt_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=180)
        if proc.returncode != 0:
            raise IsolationManagerError(
                f"Failed to create git worktree at '{wt_path}': {proc.stderr or proc.stdout}"
            )

        return wt_path

    @classmethod
    def mirror_workspace_baseline(
        cls,
        main_repo: Path,
        worktree_path: Path,
        normalize_text_paths: Optional[set[str]] = None,
    ) -> Tuple[bool, str]:
        """Mirror committed, modified, deleted, staged, and untracked user state into isolation.

        The mirrored state is committed only on the disposable worktree branch so the
        candidate patch is evaluated against what the user actually has open without
        changing or committing the main workspace.
        """
        diff_proc = subprocess.run(
            ["git", "-C", str(main_repo), "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        if diff_proc.returncode != 0:
            return False, "Failed to capture tracked baseline for isolation."
        if diff_proc.stdout:
            apply_proc = subprocess.run(
                ["git", "-C", str(worktree_path), "apply", "--binary", "-"],
                input=diff_proc.stdout,
                capture_output=True,
                check=False,
            )
            if apply_proc.returncode != 0:
                return False, f"Failed to mirror tracked baseline: {apply_proc.stderr.decode(errors='replace')}"

        untracked_proc = subprocess.run(
            ["git", "-C", str(main_repo), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        if untracked_proc.returncode != 0:
            return False, "Failed to enumerate untracked baseline for isolation."
        source_root = main_repo.resolve()
        target_root = worktree_path.resolve()
        normalize_paths = normalize_text_paths or set()
        ignored_prefixes = (
            ".venv", "venv", "env", "node_modules", "local_cases", "local_runs",
            ".pytest_cache", "__pycache__", ".git", ".deep_dev", "dist", "build",
            ".agents", ".system_generated",
        )
        for raw_path in (item for item in untracked_proc.stdout.split(b"\0") if item):
            relative = raw_path.decode("utf-8", errors="surrogateescape")
            rel_norm = relative.replace("\\", "/").strip("/")
            if any(rel_norm == p or rel_norm.startswith(f"{p}/") for p in ignored_prefixes):
                continue
            source = (source_root / relative).resolve(strict=False)
            destination = (target_root / relative).resolve(strict=False)
            try:
                source.relative_to(source_root)
                destination.relative_to(target_root)
            except ValueError:
                return False, f"Untracked baseline path escaped repository boundary: {relative}"
            if not source.is_file():
                return False, f"Unsupported untracked baseline entry: {relative}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative in normalize_paths:
                try:
                    normalized = source.read_bytes().decode("utf-8").replace("\r\n", "\n")
                except UnicodeDecodeError:
                    return False, f"Scoped text baseline is not valid UTF-8: {relative}"
                # PatchSerializer emits normalized LF text. Normalize only the
                # disposable scoped copy, otherwise git apply cannot match a
                # Windows CRLF context against the serialized patch.
                destination.write_bytes(normalized.encode("utf-8"))
            else:
                shutil.copy2(source, destination)

        status_proc = subprocess.run(
            ["git", "-C", str(worktree_path), "status", "--porcelain=v1", "-uall"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if status_proc.returncode != 0:
            return False, f"Failed to verify mirrored baseline: {status_proc.stderr}"
        if not status_proc.stdout.strip():
            return True, "Main workspace baseline was already clean."
        add_proc = subprocess.run(
            ["git", "-C", str(worktree_path), "add", "-A", "--"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if add_proc.returncode != 0:
            return False, f"Failed to stage mirrored baseline: {add_proc.stderr or add_proc.stdout}"
        commit_proc = subprocess.run(
            [
                "git", "-C", str(worktree_path),
                "-c", "user.name=Deep Dev Baseline",
                "-c", "user.email=deep-dev-baseline@localhost",
                "commit", "-m", "deep-dev: isolated user baseline",
            ],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if commit_proc.returncode != 0:
            return False, f"Failed to commit isolated baseline: {commit_proc.stderr or commit_proc.stdout}"
        return True, "Mirrored dirty workspace baseline into isolated worktree."

    @classmethod
    def apply_patch(
        cls,
        worktree_path: Path,
        patch_text: str,
    ) -> Tuple[bool, str]:
        """
        Apply patch to worktree with pre-flight --check.
        """
        if not patch_text.strip():
            return True, "Empty patch (noop)."

        temp_patch = worktree_path / ".temp_incoming.patch"
        try:
            temp_patch.write_text(patch_text, encoding="utf-8")

            # 1. Pre-flight check
            check_cmd = ["git", "-C", str(worktree_path), "apply", "--check", str(temp_patch)]
            check_proc = subprocess.run(check_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
            if check_proc.returncode != 0:
                return False, f"git apply --check failed: {check_proc.stderr or check_proc.stdout}"

            # 2. Apply patch (unstaged)
            apply_cmd = ["git", "-C", str(worktree_path), "apply", str(temp_patch)]
            apply_proc = subprocess.run(apply_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
            if apply_proc.returncode != 0:
                return False, f"git apply failed: {apply_proc.stderr or apply_proc.stdout}"

            return True, "Patch applied successfully."
        finally:
            if temp_patch.exists():
                try:
                    temp_patch.unlink()
                except Exception:
                    pass

    @classmethod
    def apply_verified_patch_to_main(
        cls,
        main_repo: Path,
        patch_text: str,
        project_id: str,
        run_id: str,
    ) -> Tuple[bool, str]:
        """Apply the already-tested candidate patch to the main workspace fail-closed."""
        if not patch_text.strip():
            return True, "Verified patch was empty (noop)."
        run_dir = cls.get_runs_path(project_id, run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        patch_path = run_dir / "verified-main-apply.patch"
        patch_path.write_text(patch_text, encoding="utf-8")
        check_proc = subprocess.run(
            ["git", "-C", str(main_repo), "apply", "--check", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if check_proc.returncode != 0:
            return False, f"Verified patch no longer applies to main workspace: {check_proc.stderr or check_proc.stdout}"
        apply_proc = subprocess.run(
            ["git", "-C", str(main_repo), "apply", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        if apply_proc.returncode != 0:
            return False, f"Final main-workspace apply failed: {apply_proc.stderr or apply_proc.stdout}"
        return True, "Verified patch applied to the main workspace."

    @classmethod
    def apply_verified_operations_to_main(
        cls,
        main_repo: Path,
        snapshot: WorkspaceSnapshot,
        file_operations: List[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """Apply verified full-content operations against snapshot hashes with rollback.

        `git apply` rejects an otherwise valid delta when the target file is already
        modified relative to the index. Full-content operations let us validate the
        exact dirty baseline and update only authorized paths.
        """
        root = main_repo.resolve()
        plans: List[Tuple[str, Path, Optional[bytes], Optional[bytes]]] = []
        seen: set[str] = set()
        try:
            for operation in file_operations:
                relative = canonicalize_safe_relative_path(str(operation.get("file_path", "")), allow_root_dot=False)
                if relative in seen:
                    raise IsolationManagerError(f"Duplicate final operation for '{relative}'.")
                seen.add(relative)
                if relative not in snapshot.allowed_paths or relative not in snapshot.files:
                    raise IsolationManagerError(f"Operation escaped verified snapshot scope: '{relative}'.")
                target = (root / relative).resolve(strict=False)
                target.relative_to(root)
                if target.is_symlink():
                    raise IsolationManagerError(f"Symbolic-link target is not supported: '{relative}'.")
                action = str(operation.get("action", "noop")).lower()
                entry = snapshot.files[relative]
                original = target.read_bytes() if target.is_file() else None
                if entry.exists:
                    if original is None:
                        raise IsolationManagerError(f"Snapshot target disappeared: '{relative}'.")
                    normalized = original.decode("utf-8", errors="replace").replace("\r\n", "\n")
                    current_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
                    if current_hash != entry.base_sha256:
                        raise IsolationManagerError(f"Snapshot hash changed before final apply: '{relative}'.")
                elif original is not None:
                    raise IsolationManagerError(f"Create target appeared after snapshot: '{relative}'.")

                if action == "noop":
                    continue
                if action in {"create", "modify"}:
                    # Canonical MCP proposals serialize full file contents under
                    # `content`; `content_or_diff` is retained only for legacy
                    # proposals.  Prefer the canonical field so the bytes that
                    # passed in the isolated worktree are the bytes applied here.
                    raw_content = operation.get("content", operation.get("content_or_diff", ""))
                    if not isinstance(raw_content, str):
                        raise IsolationManagerError(f"Verified content is not text for '{relative}'.")
                    content = raw_content.replace("\r\n", "\n")
                    if content and not content.endswith("\n"):
                        content += "\n"
                    plans.append((action, target, original, content.encode("utf-8")))
                elif action == "delete":
                    plans.append((action, target, original, None))
                else:
                    raise IsolationManagerError(f"Unsupported verified action '{action}' for '{relative}'.")
        except (OSError, UnicodeError, ValueError, PathSecurityError, IsolationManagerError) as exc:
            return False, f"Final operation validation failed: {exc}"

        completed: List[Tuple[Path, Optional[bytes]]] = []
        try:
            for _, target, original, replacement in plans:
                target.parent.mkdir(parents=True, exist_ok=True)
                if replacement is None:
                    target.unlink()
                else:
                    temp = target.with_name(f".{target.name}.deep-dev.tmp")
                    temp.write_bytes(replacement)
                    os.replace(temp, target)
                completed.append((target, original))
            # A successful worktree test is not enough: verify that the exact
            # tested bytes were actually installed in the main workspace.
            for action, target, _, replacement in plans:
                if action == "delete":
                    if target.exists():
                        raise IsolationManagerError(f"Final delete did not remove '{target}'.")
                elif replacement is None or not target.is_file() or target.read_bytes() != replacement:
                    raise IsolationManagerError(f"Final content verification failed for '{target}'.")
        except (OSError, IsolationManagerError) as exc:
            rollback_errors: List[str] = []
            for target, original in reversed(completed):
                try:
                    if original is None:
                        target.unlink(missing_ok=True)
                    else:
                        rollback = target.with_name(f".{target.name}.deep-dev.rollback")
                        rollback.write_bytes(original)
                        os.replace(rollback, target)
                except OSError as rollback_exc:
                    rollback_errors.append(f"{target}: {rollback_exc}")
            suffix = f" Rollback errors: {rollback_errors}" if rollback_errors else " Rollback completed."
            return False, f"Final operation apply failed: {exc}.{suffix}"
        return True, f"Applied {len(plans)} verified operation(s) to the main workspace."

    @classmethod
    def optional_commit(
        cls,
        worktree_path: Path,
        snapshot: WorkspaceSnapshot,
        commit_message: str,
    ) -> Tuple[bool, str]:
        """
        Stage and commit ONLY files in snapshot.allowed_paths.
        Fails closed if any modified, staged, or untracked file in worktree
        is outside snapshot.allowed_paths.
        """
        if not snapshot.allowed_paths:
            return False, "No allowed paths declared in snapshot boundary."

        # 1. Inspect full worktree status for unapproved changes
        status_cmd = ["git", "-C", str(worktree_path), "status", "--porcelain=v1", "-uall"]
        status_proc = subprocess.run(status_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if status_proc.returncode != 0:
            return False, f"git status failed in worktree: {status_proc.stderr or status_proc.stdout}"

        if not status_proc.stdout.strip():
            return True, "No changes to commit (working tree clean)."

        unapproved_files: List[str] = []
        for line in status_proc.stdout.splitlines():
            if len(line) < 4:
                continue
            file_entry = line[3:].strip()
            # Handle rename format "old -> new"
            if " -> " in file_entry:
                targets = [p.strip() for p in file_entry.split(" -> ")]
            else:
                targets = [file_entry]

            for raw_t in targets:
                # Ignore ephemeral bytecode files generated during test execution
                if "__pycache__" in raw_t or raw_t.endswith(".pyc") or raw_t.endswith(".pyo"):
                    continue

                try:
                    norm_t = canonicalize_safe_relative_path(raw_t, allow_root_dot=False)
                except PathSecurityError as pse:
                    return False, f"BLOCKED: Security violation in worktree file '{raw_t}': {pse}"

                if norm_t not in snapshot.allowed_paths:
                    unapproved_files.append(norm_t)

        if unapproved_files:
            return False, (
                f"BLOCKED: Unapproved modifications detected in worktree: {unapproved_files}. "
                f"Allowed paths: {snapshot.allowed_paths}"
            )

        # 2. Stage only snapshot.allowed_paths
        add_cmd = ["git", "-C", str(worktree_path), "add", "-A", "--"] + snapshot.allowed_paths
        add_proc = subprocess.run(add_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if add_proc.returncode != 0:
            return False, f"git add failed: {add_proc.stderr or add_proc.stdout}"

        # 3. Commit
        commit_cmd = ["git", "-C", str(worktree_path), "commit", "-m", commit_message]
        commit_proc = subprocess.run(commit_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if commit_proc.returncode != 0:
            return False, f"git commit failed: {commit_proc.stderr or commit_proc.stdout}"

        return True, "Committed in isolated worktree branch."

    @classmethod
    def is_worktree_registered(cls, main_repo: Path, worktree_path: Path) -> Tuple[bool, Optional[str]]:
        """
        Check if worktree path is still registered in git worktree list.
        Returns (is_registered, error_message).
        """
        list_cmd = ["git", "-C", str(main_repo), "worktree", "list", "--porcelain"]
        proc = subprocess.run(list_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
        if proc.returncode != 0:
            return False, f"git worktree list failed: {proc.stderr.strip() or proc.stdout.strip()}"

        target_norm = str(worktree_path.resolve()).replace("\\", "/").lower()
        for line in proc.stdout.splitlines():
            if line.startswith("worktree "):
                wt_entry = line[len("worktree "):].strip().replace("\\", "/").lower()
                if wt_entry == target_norm:
                    return True, None
        return False, None

    @classmethod
    def cleanup_worktree(
        cls,
        main_repo: Path,
        worktree_path: Path,
        project_id: str,
        run_id: str,
        delete_branch: bool = True,
    ) -> Tuple[bool, str]:
        r"""
        Evidence-based cleanup of worktree and branch.
        Strictly validates that worktree_path matches expected path under %LOCALAPPDATA%\deep-dev\worktrees.
        Verifies return codes and confirms git worktree registry, disk state, and branch deletion.
        Never reports success if main_repo is invalid, path is out of bounds, or cleanup fails.
        """
        # 1. Verify main_repo is a valid Git repository
        is_git, err = cls.is_git_repository(main_repo)
        if not is_git:
            return False, f"Cleanup aborted: {err}"

        if not project_id or not run_id:
            return False, "Cleanup aborted: 'project_id' and 'run_id' are strictly required."

        # 2. Strict path validation: Must exactly match expected external path under runtime worktrees
        runtime_worktrees_root = (cls.get_runtime_root() / "worktrees").resolve()
        expected_path = cls.get_worktree_path(project_id, run_id).resolve()
        resolved_wt = worktree_path.resolve()

        if resolved_wt != expected_path:
            return False, (
                f"Cleanup security violation: worktree_path '{worktree_path}' (resolved: '{resolved_wt}') "
                f"does not match expected path '{expected_path}' for project '{project_id}' and run '{run_id}'."
            )

        try:
            expected_path.relative_to(runtime_worktrees_root)
        except ValueError:
            return False, (
                f"Cleanup security violation: expected path '{expected_path}' is outside runtime root '{runtime_worktrees_root}'."
            )

        errors: List[str] = []

        # 3. Check registration in git worktree list
        is_registered, reg_err = cls.is_worktree_registered(main_repo, resolved_wt)
        if reg_err:
            return False, f"Cleanup aborted: cannot query worktree registry ({reg_err})"

        # 4. git worktree remove --force if registered or directory exists
        if resolved_wt.exists() or is_registered:
            rm_cmd = ["git", "-C", str(main_repo), "worktree", "remove", "--force", str(resolved_wt)]
            rm_proc = subprocess.run(rm_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=30)
            if rm_proc.returncode != 0:
                errors.append(f"git worktree remove failed: {rm_proc.stderr.strip() or rm_proc.stdout.strip()}")

        # 5. Verify registry state: target must NOT be registered anymore
        is_still_registered, reg_err2 = cls.is_worktree_registered(main_repo, resolved_wt)
        if reg_err2:
            errors.append(f"Could not verify registry post-removal: {reg_err2}")
        elif is_still_registered:
            errors.append(f"Worktree '{resolved_wt}' remains registered in git worktree list.")

        # 6. Only if target is NOT registered anymore, verify disk state / clean residual untracked files
        if not is_still_registered and resolved_wt.exists():
            shutil.rmtree(resolved_wt, ignore_errors=True)
            if resolved_wt.exists():
                errors.append(f"Worktree directory '{resolved_wt}' still exists on disk.")

        # 7. git branch -D if requested
        if delete_branch and run_id:
            branch_name = f"deep-dev/{run_id}"
            br_cmd = ["git", "-C", str(main_repo), "branch", "-D", branch_name]
            br_proc = subprocess.run(br_cmd, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
            if br_proc.returncode != 0 and "not found" not in br_proc.stderr.lower():
                errors.append(f"git branch -D failed: {br_proc.stderr.strip()}")

            # Verify branch is actually gone
            list_br = ["git", "-C", str(main_repo), "branch", "--list", branch_name]
            list_proc = subprocess.run(list_br, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL, timeout=15)
            if list_proc.returncode == 0 and branch_name in list_proc.stdout:
                errors.append(f"Branch '{branch_name}' still exists in git branch list.")

        # 8. Clean up empty parent project directory if empty
        try:
            parent_dir = resolved_wt.parent
            if parent_dir.exists() and parent_dir != runtime_worktrees_root and not any(parent_dir.iterdir()):
                parent_dir.rmdir()
        except Exception:
            pass

        if errors:
            return False, f"Cleanup incomplete: {'; '.join(errors)}"

        return True, "Cleaned successfully with evidence."
