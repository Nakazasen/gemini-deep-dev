"""
.deep_dev Engine: Test Executor Module (test_executor.py)
=========================================================
Executes hash-locked allowlisted test commands in isolated external worktree with
strict cwd boundary validation, shell=False, per-command timeouts, Windows process-tree
cleanup (taskkill /T /F), and complete stdout/stderr log capture.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

try:
    from .config_lock import AllowlistCommand, DeepDevTestConfig
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from config_lock import AllowlistCommand, DeepDevTestConfig
    from path_utils import canonicalize_safe_relative_path, PathSecurityError


class SingleTestResult(BaseModel):
    command_id: str
    executable: str
    args: List[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    discovered_test_count: Optional[int] = None
    timed_out: bool = False
    passed: bool


class TestSuiteResult(BaseModel):
    all_passed: bool
    total_commands: int
    passed_count: int
    failed_count: int
    total_test_cases: int = 0
    results: List[SingleTestResult]
    summary: str


def kill_process_tree_windows(pid: int) -> None:
    """Force kill a process and all its children on Windows."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass


class TestExecutor:
    """Executes allowlisted test commands safely."""

    @classmethod
    def execute_command(
        cls,
        command_id: str,
        cmd_def: AllowlistCommand,
        worktree_dir: Path,
        timeout_override: Optional[int] = None,
    ) -> SingleTestResult:
        """Run a single test command inside the worktree directory."""
        worktree_root = worktree_dir.resolve()

        # Defense-in-depth: Validate cwd path security
        try:
            norm_cwd = canonicalize_safe_relative_path(cmd_def.cwd, allow_root_dot=True)
            resolved_cwd = (worktree_dir / norm_cwd).resolve()
            resolved_cwd.relative_to(worktree_root)
        except (PathSecurityError, ValueError) as pse:
            return SingleTestResult(
                command_id=command_id,
                executable=cmd_def.executable,
                args=cmd_def.args,
                exit_code=-1,
                stdout="",
                stderr=f"BLOCKED: Security violation in cwd: {pse}",
                duration_seconds=0.0,
                timed_out=False,
                passed=False,
            )

        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            return SingleTestResult(
                command_id=command_id,
                executable=cmd_def.executable,
                args=cmd_def.args,
                exit_code=-1,
                stdout="",
                stderr=f"BLOCKED: cwd directory does not exist or is not a directory: {resolved_cwd}",
                duration_seconds=0.0,
                timed_out=False,
                passed=False,
            )

        timeout = timeout_override or cmd_def.timeout_seconds
        cmd_list = [cmd_def.executable] + list(cmd_def.args)

        start_time = time.time()
        stdout_text = ""
        stderr_text = ""
        exit_code = -1
        timed_out = False
        discovered: Optional[int] = None

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(resolved_cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                encoding="utf-8",
                errors="replace",
                env=env,
                stdin=subprocess.DEVNULL,
            )
            try:
                stdout_text, stderr_text = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                if sys.platform.startswith("win"):
                    kill_process_tree_windows(proc.pid)
                else:
                    proc.kill()
                try:
                    stdout_text, stderr_text = proc.communicate(timeout=2)
                except Exception:
                    pass
                exit_code = -99
        except Exception as exc:
            stderr_text = f"Execution failed to launch: {exc}"
            exit_code = -1

        duration = time.time() - start_time
        passed = (exit_code == 0 and not timed_out)
        combined_output = f"{stdout_text}\n{stderr_text}"
        # unittest's TextTestRunner writes its summary to stderr by default.
        matches = re.findall(r"\bRan\s+(\d+)\s+tests?\b", combined_output, re.IGNORECASE)
        discovered = int(matches[-1]) if matches else None
        if cmd_def.minimum_test_count is not None:
            if discovered is None or discovered < cmd_def.minimum_test_count:
                passed = False
                requirement = cmd_def.minimum_test_count
                observed = "unreported" if discovered is None else str(discovered)
                stderr_text = (
                    f"{stderr_text}\nBLOCKED: expected at least {requirement} test(s), "
                    f"but observed {observed}."
                ).strip()

        return SingleTestResult(
            command_id=command_id,
            executable=cmd_def.executable,
            args=cmd_def.args,
            exit_code=exit_code,
            stdout=stdout_text or "",
            stderr=stderr_text or "",
            duration_seconds=round(duration, 3),
            discovered_test_count=discovered,
            timed_out=timed_out,
            passed=passed,
        )

    @classmethod
    def execute_suite(
        cls,
        config: DeepDevTestConfig,
        worktree_dir: Path,
        timeout_override: Optional[int] = None,
    ) -> TestSuiteResult:
        """Run all allowlisted test commands."""
        results: List[SingleTestResult] = []

        for cid, cmd_def in config.allowlisted_test_commands.items():
            res = cls.execute_command(
                command_id=cid,
                cmd_def=cmd_def,
                worktree_dir=worktree_dir,
                timeout_override=timeout_override,
            )
            results.append(res)

        all_passed = len(results) > 0 and all(r.passed for r in results)
        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count
        total_test_cases = sum(result.discovered_test_count or 0 for result in results)

        summary = (
            f"Ran {total_test_cases} reported test case(s) across {len(results)} command(s): "
            f"{passed_count} command(s) passed, {failed_count} failed."
        )
        return TestSuiteResult(
            all_passed=all_passed,
            total_commands=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            total_test_cases=total_test_cases,
            results=results,
            summary=summary,
        )
