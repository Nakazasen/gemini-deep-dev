"""Deterministic, content-minimized trajectory graph and evaluator."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
import uuid


EXPECTED_TRAJECTORY = [
    "RUN_CREATED", "MEMORY_HEALTH", "GRAPH_READY", "PREFLIGHT", "MEMORY_RECALL", "MEMORY_HYGIENE",
    "IMPACT_ANALYSIS", "WORKSPACE_SNAPSHOT", "HARNESS_REVIEW", "PATCH_SERIALIZE",
    "APPLY_TO_WORKTREE", "GRAPH_DIFF", "TEST_EXECUTE", "MEMORY_SAVE", "MAIN_APPLY",
    "GRAPH_REFRESH", "ACCEPT_PATCH",
]


class TrajectoryRecorder:
    def __init__(self, run_dir: Path):
        self.path = run_dir / "trajectory.json"

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": "1.0", "nodes": [], "edges": [], "evaluation": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f"{self.path.name}.tmp.{uuid.uuid4().hex[:8]}")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, self.path)

    @staticmethod
    def _evaluate(nodes: list[dict[str, Any]]) -> dict[str, Any]:
        actual = [str(node["phase"]) for node in nodes if node.get("status") in {"ok", "degraded"}]
        recognized = [phase for phase in actual if phase in EXPECTED_TRAJECTORY]
        unique = set(recognized)
        recall = len(unique) / len(EXPECTED_TRAJECTORY)
        precision = len(recognized) / len(actual) if actual else 0.0
        positions = [EXPECTED_TRAJECTORY.index(phase) for phase in recognized]
        in_order = positions == sorted(positions)
        score = round((0.5 * recall) + (0.25 * precision) + (0.25 if in_order else 0.0), 4)
        missing = [phase for phase in EXPECTED_TRAJECTORY if phase not in unique]
        acceptance_ready = not missing and in_order
        # Before the terminal node is recorded this means the run may accept;
        # afterwards it means that acceptance was completed.  Keeping one
        # truthful boolean avoids a completed ACCEPT_PATCH run being reported
        # as "not ready" in host handover text.
        ready_for_accept = acceptance_ready or (missing == ["ACCEPT_PATCH"] and in_order)
        return {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "in_order": in_order,
            "score": score,
            "missing_phases": missing,
            "acceptance_ready": acceptance_ready,
            "ready_for_accept": ready_for_accept,
        }

    def record(self, phase: str, status: str = "ok", evidence: Any = None) -> dict[str, Any]:
        data = self._load()
        nodes = data["nodes"]
        node_id = f"n{len(nodes) + 1:03d}"
        evidence_hash = None
        if evidence is not None:
            normalized = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
            evidence_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        node = {
            "id": node_id,
            "phase": phase,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence_sha256": evidence_hash,
        }
        if nodes:
            data["edges"].append({"source": nodes[-1]["id"], "target": node_id, "type": "NEXT"})
        nodes.append(node)
        data["evaluation"] = self._evaluate(nodes)
        self._save(data)
        return data["evaluation"]

    def evaluation(self) -> dict[str, Any]:
        return self._evaluate(self._load()["nodes"])
