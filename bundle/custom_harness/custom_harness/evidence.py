"""Strict, lightweight validation for pre-answer Deep Dev evidence packs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class EvidenceItem(BaseModel):
    source: Literal["agentmemory", "graphify"]
    content: str = Field(min_length=1, max_length=2400)
    provenance: str = Field(min_length=1, max_length=1000)

    @field_validator("content", "provenance")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32).strip()
        if not cleaned:
            raise ValueError("Evidence text cannot be empty after sanitization.")
        return cleaned


class ProviderExecutionReceipt(BaseModel):
    provider: Literal["agentmemory", "graphify"]
    operation: str = Field(min_length=3, max_length=200)
    status: Literal["succeeded"]
    started_at: str = Field(min_length=20, max_length=40)
    completed_at: str = Field(min_length=20, max_length=40)
    duration_ms: int = Field(ge=0, le=300_000)
    evidence_count: int = Field(ge=0, le=20)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_real_provider_operation(self) -> "ProviderExecutionReceipt":
        operation = self.operation.casefold()
        valid_operation = (
            all(marker in operation for marker in ("health", "recall"))
            if self.provider == "agentmemory"
            else ("query" in operation or "extract" in operation)
        )
        if not valid_operation:
            raise ValueError(f"{self.provider} receipt does not describe the required execution.")
        # A successful AgentMemory search may legitimately have no prior
        # project-specific observations on a first run. Graphify evidence is
        # still required by EvidencePack and may never be empty.
        if self.provider == "graphify" and self.evidence_count < 1:
            raise ValueError("Graphify execution returned no evidence.")
        try:
            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00"))
            completed = datetime.fromisoformat(self.completed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Receipt timestamps must be ISO-8601 values.") from exc
        if completed < started:
            raise ValueError("Receipt completion precedes its start.")
        return self


class EvidencePack(BaseModel):
    schema_version: Literal["1.1"] = "1.1"
    entry_run_id: str = Field(pattern=r"^entry_[0-9]{8}T[0-9]{6}Z_[0-9a-f]{8}$")
    workspace_root: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=8000)
    agentmemory_status: Literal["ready"]
    graphify_status: Literal["queried_fresh_graph", "extracted_fresh_ast"]
    provider_receipts: list[ProviderExecutionReceipt] = Field(min_length=2, max_length=2)
    items: list[EvidenceItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_all_evidence_providers(self) -> "EvidencePack":
        providers = [receipt.provider for receipt in self.provider_receipts]
        if sorted(providers) != ["agentmemory", "graphify"]:
            raise ValueError("Exactly one real AgentMemory and one real Graphify receipt are required.")
        for provider in providers:
            receipt_count = next(
                receipt.evidence_count for receipt in self.provider_receipts if receipt.provider == provider
            )
            item_count = sum(item.source == provider for item in self.items)
            if receipt_count != item_count:
                raise ValueError(f"{provider} receipt count does not match its evidence items.")
        if not any(item.source == "graphify" for item in self.items):
            raise ValueError("At least one Graphify evidence item is required.")
        return self


def validate_evidence_pack(data: dict[str, Any]) -> dict[str, Any]:
    """Run the entry Harness FSM and attach a canonical execution receipt."""
    trajectory = ["INIT"]
    pack = EvidencePack.model_validate(data)
    trajectory.append("VERIFY_AGENTMEMORY")
    if not any(receipt.provider == "agentmemory" for receipt in pack.provider_receipts):
        raise ValueError("AgentMemory execution is absent.")
    trajectory.append("VERIFY_GRAPHIFY")
    if not any(receipt.provider == "graphify" for receipt in pack.provider_receipts):
        raise ValueError("Graphify execution is absent.")
    trajectory.append("SEAL_EVIDENCE")
    canonical = json.dumps(pack.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result = pack.model_dump()
    evidence_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    trajectory.append("READY")
    completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    receipt_material = f"{pack.entry_run_id}:{evidence_sha256}:{'|'.join(trajectory)}"
    result["harness_status"] = "preflight_passed"
    result["harness_receipt"] = {
        "receipt_id": f"harness_{uuid4().hex[:12]}",
        "operation": "entry_evidence_fsm",
        "status": "succeeded",
        "completed_at": completed_at,
        "input_sha256": evidence_sha256,
        "receipt_sha256": hashlib.sha256(receipt_material.encode("utf-8")).hexdigest(),
    }
    result["entry_trajectory"] = trajectory
    result["evidence_sha256"] = evidence_sha256
    return result
