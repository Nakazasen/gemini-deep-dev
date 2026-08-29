"""
.deep_dev Engine: Memory Adapter Module (memory_adapter.py)
===========================================================
Provides dynamic capability discovery and injection for agentmemory MCP/backends,
fail-closed verification evidence gating for lesson persistence, and graceful degradation.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import request as urllib_request
from pydantic import BaseModel, Field

logger = logging.getLogger("deep_dev.memory")


@dataclass
class RecallResult:
    items: List[Dict[str, Any]]
    degraded_memory: bool
    source_tool: Optional[str] = None


class VerificationEvidence(BaseModel):
    """Evidence gate model: required before saving lessons to memory."""
    terminal_state: str  # Must be "ACCEPT_PATCH" or "APPROVED"
    all_tests_passed: bool
    failed_test_count: int = 0
    test_summary: str
    run_id: str
    project_id: str

    def is_verified(self) -> bool:
        return (
            self.terminal_state.upper() in {"ACCEPT_PATCH", "APPROVED"}
            and self.all_tests_passed is True
            and self.failed_test_count == 0
        )


@runtime_checkable
class MemoryBackend(Protocol):
    """Protocol for pluggable memory backends (MCP stdio, HTTP REST, or in-memory mock)."""

    def is_healthy(self) -> bool:
        """Check if memory backend is online and responding."""
        ...

    def recall(self, project_id: str, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Query observations/lessons with dynamic tool discovery."""
        ...

    def save_lesson(
        self,
        project_id: str,
        lesson_text: str,
        tags: List[str],
        evidence_data: Dict[str, Any],
    ) -> bool:
        """Persist verified lesson with evidence metadata."""
        ...


class DefaultMemoryBackend:
    """Default fallback memory backend when no external MCP is attached."""

    def __init__(self, is_online: bool = False):
        self._online = is_online
        self._storage: List[Dict[str, Any]] = []

    def is_healthy(self) -> bool:
        return self._online

    def recall(self, project_id: str, query: str, limit: int = 5) -> List[Dict[str, str]]:
        if not self._online:
            raise RuntimeError("Memory backend offline")
        # In-memory keyword match for test/offline simulations
        matches: List[Dict[str, str]] = []
        q_lower = query.lower()
        for item in self._storage:
            if item.get("project_id") == project_id and any(w in item.get("text", "").lower() for w in q_lower.split()):
                matches.append({"source": "agentmemory", "content": item.get("text", "")})
                if len(matches) >= limit:
                    break
        return matches

    def save_lesson(
        self,
        project_id: str,
        lesson_text: str,
        tags: List[str],
        evidence_data: Dict[str, Any],
    ) -> bool:
        if not self._online:
            return False
        self._storage.append({
            "project_id": project_id,
            "text": lesson_text,
            "tags": tags,
            "evidence": evidence_data,
        })
        return True


class AgentMemoryRESTBackend:
    """Required AgentMemory backend using its primary localhost REST surface."""

    def __init__(self, base_url: Optional[str] = None, secret: Optional[str] = None, timeout: float = 5.0):
        self.base_url = (base_url or os.environ.get("AGENTMEMORY_URL") or "http://127.0.0.1:3111").rstrip("/")
        self.secret = secret if secret is not None else self._load_secret()
        self.timeout = timeout
        self.last_error: Optional[str] = None

    @staticmethod
    def _load_secret() -> Optional[str]:
        direct = os.environ.get("AGENTMEMORY_SECRET")
        if direct:
            return direct
        env_file = Path.home() / ".agentmemory" / ".env"
        if not env_file.is_file():
            return None
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("AGENTMEMORY_SECRET="):
                    return line.split("=", 1)[1].strip() or None
        except OSError:
            return None
        return None

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode("utf-8")
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        req = urllib_request.Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
                if not 200 <= response.status < 300:
                    raise RuntimeError(f"AgentMemory returned HTTP {response.status}")
                return json.loads(raw.decode("utf-8")) if raw else {}
        except (urllib_error.URLError, urllib_error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"AgentMemory REST request failed: {method} {path}: {exc}") from exc

    def is_healthy(self) -> bool:
        try:
            self._request("GET", "/agentmemory/livez")
            return True
        except RuntimeError as exc:
            self.last_error = str(exc)
            return False

    def ensure_available(self, startup_timeout: float = 30.0) -> bool:
        """Auto-start an installed AgentMemory service, then wait for REST health."""
        if self.is_healthy():
            return True
        executable = shutil.which("agentmemory.cmd") or shutil.which("agentmemory")
        if not executable:
            self.last_error = "AgentMemory executable was not found on PATH."
            return False
        try:
            kwargs: Dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "cwd": str(Path.home()),
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
                command = ["cmd.exe", "/d", "/s", "/c", executable]
            else:
                command = [executable]
            subprocess.Popen(command, **kwargs)
        except Exception as exc:
            self.last_error = f"AgentMemory auto-start failed: {exc}"
            return False

        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self.is_healthy():
                self.last_error = None
                return True
            time.sleep(0.5)
        self.last_error = f"AgentMemory did not become healthy within {startup_timeout:.0f}s after auto-start."
        return False

    def recall(self, project_id: str, query: str, limit: int = 5) -> List[Dict[str, str]]:
        result = self._request(
            "POST",
            "/agentmemory/smart-search",
            {"query": f"project:{project_id} {query}", "limit": limit},
        )
        candidates: Any = result
        if isinstance(result, dict):
            candidates = next(
                (result[key] for key in ("results", "memories", "observations", "data") if isinstance(result.get(key), list)),
                [],
            )
        if not isinstance(candidates, list):
            raise RuntimeError("AgentMemory smart-search returned an unsupported response schema")
        items: List[Dict[str, Any]] = []
        for item in candidates:
            if isinstance(item, dict):
                # AgentMemory compact smart-search returns ``title`` plus an
                # observation id instead of a full ``content`` field.  Treat
                # that title as valid recall evidence instead of silently
                # discarding every compact result.
                content = (
                    item.get("content")
                    or item.get("text")
                    or item.get("memory")
                    or item.get("observation")
                    or item.get("title")
                )
            else:
                content = item
            if content:
                provenance = "agentmemory:smart-search"
                if isinstance(item, dict) and item.get("obsId"):
                    provenance = f"agentmemory:{item['obsId']}"
                normalized: Dict[str, Any] = {"source": provenance, "content": str(content)}
                # Preserve only the metadata consumed by memory hygiene.  The old
                # adapter discarded these fields, making expiry, confidence,
                # contradiction, and file-reference checks ineffective at runtime.
                if isinstance(item, dict):
                    for key in ("confidence", "expires_at", "key", "value", "files", "file"):
                        if key in item:
                            normalized[key] = item[key]
                items.append(normalized)
        return items

    def save_lesson(
        self,
        project_id: str,
        lesson_text: str,
        tags: List[str],
        evidence_data: Dict[str, Any],
    ) -> bool:
        concepts = list(dict.fromkeys([project_id, "deep_dev", *tags]))
        self._request(
            "POST",
            "/agentmemory/remember",
            {
                "content": f"[project:{project_id}] {lesson_text}\nEvidence: {json.dumps(evidence_data, sort_keys=True)}",
                "concepts": concepts,
            },
        )
        return True


class MemoryAdapter:
    """Interface to agentmemory with evidence gating and graceful degradation."""

    _backend: Optional[MemoryBackend] = None

    @classmethod
    def set_backend(cls, backend: Optional[MemoryBackend]) -> None:
        """Inject or override memory backend (used by orchestrator, MCP bridge, or tests)."""
        cls._backend = backend

    @classmethod
    def get_backend(cls) -> MemoryBackend:
        if cls._backend is None:
            cls._backend = DefaultMemoryBackend(is_online=False)
        return cls._backend

    @classmethod
    def is_available(cls) -> bool:
        """Check if memory subsystem is active and healthy."""
        try:
            return cls.get_backend().is_healthy()
        except Exception:
            return False

    @classmethod
    def recall_context(
        cls,
        project_id: str,
        task: str,
        limit: int = 5,
    ) -> RecallResult:
        """
        Recall past lessons and gotchas with dynamic tool discovery.
        Gracefully returns degraded_memory=True on failure or offline status.
        """
        try:
            backend = cls.get_backend()
            if not backend.is_healthy():
                return RecallResult(items=[], degraded_memory=True)

            items = backend.recall(project_id, task, limit=limit)
            return RecallResult(items=items, degraded_memory=False)
        except Exception as exc:
            logger.warning("Memory recall encountered error (degrading gracefully): %s", exc)
            return RecallResult(items=[], degraded_memory=True)

    @classmethod
    def save_lesson(
        cls,
        evidence: VerificationEvidence,
        lesson_text: str,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """
        Persist a verified lesson into memory.
        Enforces evidence gate: must be ACCEPT_PATCH/APPROVED and all allowlisted tests pass exit 0.
        """
        if not isinstance(evidence, VerificationEvidence) or not evidence.is_verified():
            logger.warning("Rejected lesson persistence: evidence gate not satisfied.")
            return False

        if not lesson_text or not lesson_text.strip():
            logger.warning("Rejected lesson persistence: empty lesson text.")
            return False

        backend = cls.get_backend()
        tag_list = tags or ["verified_fix", "lesson"]
        # A local AgentMemory service can briefly time out while flushing its
        # own index. Retry one health-checked time only; failure remains
        # fail-closed and is never converted into an accepted patch.
        for attempt in range(2):
            try:
                if not backend.is_healthy():
                    raise RuntimeError("Memory backend offline")
                if backend.save_lesson(
                    project_id=evidence.project_id,
                    lesson_text=lesson_text.strip(),
                    tags=tag_list,
                    evidence_data=evidence.model_dump(),
                ):
                    return True
                raise RuntimeError("Memory backend rejected lesson persistence")
            except Exception as exc:
                if attempt == 0:
                    logger.warning("Lesson persistence failed transiently; retrying once: %s", exc)
                    time.sleep(0.5)
                    continue
                logger.warning("Failed to save lesson to memory backend after retry: %s", exc)
        return False
