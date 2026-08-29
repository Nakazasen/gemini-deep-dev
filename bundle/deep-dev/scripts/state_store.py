r"""
.deep_dev Engine: State Store Module (state_store.py)
=====================================================
Manages persistent state machine transitions and execution logs in external storage
(%LOCALAPPDATA%\deep-dev\runs\<project_id>\<run_id>\state.json) with fail-closed FSM,
strict identifier validation, and atomic fsync-based persistence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Set
import uuid
from pydantic import BaseModel, Field, field_validator

try:
    from .path_utils import RESERVED_DEVICE_NAMES
    from .trajectory import TrajectoryRecorder
except ImportError:
    from path_utils import RESERVED_DEVICE_NAMES
    from trajectory import TrajectoryRecorder


class StateTransitionError(Exception):
    """Raised when an illegal FSM state transition is attempted."""
    pass


class InvalidIdentifierError(ValueError):
    """Raised when project_id or run_id violates strict identifier safety."""
    pass


class DeepDevState(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    WORKSPACE_SNAPSHOT = "WORKSPACE_SNAPSHOT"
    APPLY_TO_WORKTREE = "APPLY_TO_WORKTREE"
    TEST_EXECUTE = "TEST_EXECUTE"
    ACCEPT_PATCH = "ACCEPT_PATCH"  # Terminal
    ROLLBACK = "ROLLBACK"          # Terminal
    BLOCKED = "BLOCKED"            # Terminal
    STOP = "STOP"                  # Terminal


VALID_TRANSITIONS: Dict[DeepDevState, Set[DeepDevState]] = {
    DeepDevState.PREFLIGHT: {DeepDevState.WORKSPACE_SNAPSHOT, DeepDevState.BLOCKED, DeepDevState.STOP},
    DeepDevState.WORKSPACE_SNAPSHOT: {DeepDevState.APPLY_TO_WORKTREE, DeepDevState.ROLLBACK, DeepDevState.BLOCKED, DeepDevState.STOP},
    DeepDevState.APPLY_TO_WORKTREE: {DeepDevState.TEST_EXECUTE, DeepDevState.ROLLBACK, DeepDevState.BLOCKED, DeepDevState.STOP},
    DeepDevState.TEST_EXECUTE: {DeepDevState.ACCEPT_PATCH, DeepDevState.ROLLBACK, DeepDevState.BLOCKED, DeepDevState.STOP},
    DeepDevState.ACCEPT_PATCH: set(),
    DeepDevState.ROLLBACK: set(),
    DeepDevState.BLOCKED: set(),
    DeepDevState.STOP: set(),
}

ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def validate_identifier(val: str, label: str = "Identifier") -> str:
    """Strictly validate identifier to prevent path traversal, spaces, dots, or special chars."""
    if (
        not isinstance(val, str)
        or not ID_REGEX.match(val)
        or val in {".", ".."}
        or "/" in val
        or "\\" in val
        or val.upper() in RESERVED_DEVICE_NAMES
    ):
        raise InvalidIdentifierError(
            f"Invalid {label}: '{val}'. Must match '^[a-zA-Z0-9_-]+$' without path separators, dots, whitespace, or device names."
        )
    return val


class RunState(BaseModel):
    run_id: str
    project_id: str
    task: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    heartbeat_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    current_state: DeepDevState = DeepDevState.PREFLIGHT
    git_head: Optional[str] = None
    workspace_fingerprint: Optional[str] = None
    config_sha256: Optional[str] = None
    degraded_memory: bool = False
    degraded_graph: bool = False
    allowed_paths: List[str] = Field(default_factory=list)
    applied_patch_path: Optional[str] = None
    test_results_summary: Optional[str] = None
    test_results_artifact: Optional[str] = None
    residual_worktrees: List[str] = Field(default_factory=list)
    error: Optional[str] = None

    @field_validator("project_id")
    @classmethod
    def check_project_id(cls, v: str) -> str:
        return validate_identifier(v, "project_id")

    @field_validator("run_id")
    @classmethod
    def check_run_id(cls, v: str) -> str:
        return validate_identifier(v, "run_id")

    def get_run_dir(self) -> Path:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data) / "deep-dev"
        else:
            base = Path.home() / ".deep-dev"
        return base / "runs" / self.project_id / self.run_id

    def get_state_file(self) -> Path:
        return self.get_run_dir() / "state.json"

    def get_events_file(self) -> Path:
        return self.get_run_dir() / "events.jsonl"

    def get_health_file(self) -> Path:
        return self.get_run_dir().parents[2] / "health" / f"{self.project_id}.json"

    def get_quarantine_file(self) -> Path:
        return self.get_run_dir().parents[2] / "quarantine" / f"{self.project_id}.jsonl"

    @classmethod
    def circuit_status(cls, project_id: str, threshold: int = 3, cooldown_seconds: int = 900) -> tuple[bool, str]:
        validate_identifier(project_id, "project_id")
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "deep-dev" if local else Path.home() / ".deep-dev"
        path = base / "health" / f"{project_id}.json"
        if not path.exists():
            return False, "No prior failures."
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            failures = int(data.get("consecutive_failures", 0))
            last_run = datetime.fromisoformat(str(data.get("last_run_at")))
            age = (datetime.now(timezone.utc) - last_run).total_seconds()
            if failures >= threshold and age < cooldown_seconds:
                return True, f"Circuit open after {failures} consecutive failures; retry after {int(cooldown_seconds - age)}s."
            return False, "Circuit closed."
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return True, "Circuit health data is invalid; failing closed."

    @classmethod
    def reset_circuit(cls, project_id: str, reason: str) -> None:
        """Close an open circuit after explicit dependency and workspace recovery checks."""
        validate_identifier(project_id, "project_id")
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "deep-dev" if local else Path.home() / ".deep-dev"
        path = base / "health" / f"{project_id}.json"
        data: Dict[str, Any] = {"project_id": project_id, "total_runs": 0, "accepted_runs": 0, "failed_runs": 0}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or loaded.get("project_id") != project_id:
                raise ValueError("Circuit health data is invalid; refusing recovery reset.")
            data.update(loaded)
        data["consecutive_failures"] = 0
        data["recovered_at"] = datetime.now(timezone.utc).isoformat()
        data["recovery_reason"] = reason
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    def _quarantine_failure(self, error: Optional[str]) -> None:
        path = self.get_quarantine_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": self.project_id,
            "run_id": self.run_id,
            "terminal_state": self.current_state.value,
            "task_sha256": __import__("hashlib").sha256(self.task.encode("utf-8")).hexdigest(),
            "error_sha256": __import__("hashlib").sha256((error or "unspecified").encode("utf-8")).hexdigest(),
            "promoted_to_memory": False,
        }
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def trajectory(self) -> TrajectoryRecorder:
        return TrajectoryRecorder(self.get_run_dir())

    def record_step(self, phase: str, status: str = "ok", evidence: Any = None) -> Dict[str, Any]:
        evaluation = self.trajectory().record(phase, status=status, evidence=evidence)
        self._append_event("trajectory_step", phase=phase, status=status, score=evaluation["score"])
        return evaluation

    def _append_event(self, event: str, **details: Any) -> None:
        path = self.get_events_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "state": self.current_state.value,
            **details,
        }
        data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _update_health(self) -> None:
        path = self.get_health_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {
            "project_id": self.project_id,
            "total_runs": 0,
            "accepted_runs": 0,
            "failed_runs": 0,
            "consecutive_failures": 0,
        }
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and loaded.get("project_id") == self.project_id:
                    data.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        accepted = self.current_state == DeepDevState.ACCEPT_PATCH
        lowered_error = (self.error or "").lower()
        environmental = self.current_state == DeepDevState.BLOCKED and any(
            marker in lowered_error for marker in ("agentmemory", "graphify", "dependency", "service unavailable", "network unavailable")
        )
        data["total_runs"] = int(data.get("total_runs", 0)) + 1
        key = "accepted_runs" if accepted else ("environment_failures" if environmental else "failed_runs")
        data[key] = int(data.get(key, 0)) + 1
        data["consecutive_failures"] = 0 if accepted else (int(data.get("consecutive_failures", 0)) if environmental else int(data.get("consecutive_failures", 0)) + 1)
        data["success_rate"] = round(int(data.get("accepted_runs", 0)) / data["total_runs"], 4)
        data["last_run_id"] = self.run_id
        data["last_terminal_state"] = self.current_state.value
        data["last_run_at"] = datetime.now(timezone.utc).isoformat()
        tmp_path = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    def heartbeat(self, detail: Optional[str] = None) -> None:
        """Persist a lightweight liveness marker without screenshots or polling."""
        self.heartbeat_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.heartbeat_at
        self.save()
        self._append_event("heartbeat", detail=detail)

    def transition_to(self, new_state: DeepDevState | str, error: Optional[str] = None) -> None:
        """
        Transition to a new state if and only if allowed by the FSM transition table.
        Fails closed with StateTransitionError without modifying disk.
        """
        if isinstance(new_state, str):
            try:
                target_state = DeepDevState(new_state)
            except ValueError:
                raise StateTransitionError(f"Unknown state '{new_state}'. Valid states: {[s.value for s in DeepDevState]}")
        else:
            target_state = new_state

        current = self.current_state
        if not VALID_TRANSITIONS.get(current):
            raise StateTransitionError(f"Cannot transition from terminal state '{current.value}' to '{target_state.value}'.")

        allowed = VALID_TRANSITIONS.get(current, set())
        if target_state not in allowed:
            raise StateTransitionError(
                f"Illegal state transition from '{current.value}' to '{target_state.value}'. "
                f"Allowed transitions from '{current.value}': {[s.value for s in allowed]}"
            )

        previous_state = current
        self.current_state = target_state
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.heartbeat_at = self.updated_at
        if error:
            self.error = error
        self.save()
        self._append_event("state_transition", from_state=previous_state.value, to_state=target_state.value, error=error)
        self.record_step(target_state.value, status="ok" if error is None else "failed", evidence={"error_type": type(error).__name__ if error else None})
        if target_state in {DeepDevState.ACCEPT_PATCH, DeepDevState.ROLLBACK, DeepDevState.BLOCKED, DeepDevState.STOP}:
            self._update_health()
        if target_state in {DeepDevState.ROLLBACK, DeepDevState.BLOCKED, DeepDevState.STOP}:
            self._quarantine_failure(error)

    def save(self) -> None:
        """
        Atomic save: writes to a temporary file in the same directory, flushes, fsyncs,
        and replaces the target state file.
        """
        target_path = self.get_state_file()
        run_dir = target_path.parent
        run_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = run_dir / f"state.json.tmp.{uuid.uuid4().hex[:8]}"
        content = self.model_dump_json(indent=2)

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, target_path)

    @classmethod
    def create(
        cls,
        project_id: str,
        task: str,
        git_head: Optional[str] = None,
        config_sha256: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> RunState:
        """Initialize and atomic-save a new execution state with collision-resistant run_id."""
        validate_identifier(project_id, "project_id")
        if run_id is None:
            now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rand_suffix = uuid.uuid4().hex[:8]
            final_run_id = f"run_{now_str}_{rand_suffix}"
        else:
            final_run_id = validate_identifier(run_id, "run_id")

        instance = cls(
            run_id=final_run_id,
            project_id=project_id,
            task=task,
            git_head=git_head,
            config_sha256=config_sha256,
        )
        instance.save()
        instance._append_event("run_created", task=task)
        instance.record_step("RUN_CREATED", evidence={"task_sha256": __import__("hashlib").sha256(task.encode("utf-8")).hexdigest()})
        return instance

    @classmethod
    def load(cls, project_id: str, run_id: str) -> RunState:
        """Load state from disk with identifier safety."""
        validate_identifier(project_id, "project_id")
        validate_identifier(run_id, "run_id")

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            base = Path(local_app_data) / "deep-dev"
        else:
            base = Path.home() / ".deep-dev"
        path = base / "runs" / project_id / run_id / "state.json"
        if not path.exists():
            raise FileNotFoundError(f"State file not found: '{path}'")
        content = path.read_text(encoding="utf-8")
        return cls.model_validate(json.loads(content))
