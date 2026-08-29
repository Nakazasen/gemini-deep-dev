"""Fail-closed filtering for recalled AgentMemory observations."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _expired(value: Any) -> bool:
    if not value:
        return False
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.tzinfo is not None and moment <= datetime.now(timezone.utc)
    except ValueError:
        return True


def review(items: list[Any], workspace: Path, quarantine_path: Path) -> tuple[list[Any], dict[str, Any]]:
    accepted: list[Any] = []
    quarantined: list[dict[str, Any]] = []
    keyed: dict[str, set[str]] = {}
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("key") is not None:
            keyed.setdefault(str(item["key"]), set()).add(json.dumps(item.get("value"), sort_keys=True, default=str))
    conflicts = {key for key, values in keyed.items() if len(values) > 1}
    for index, item in enumerate(items):
        reasons: list[str] = []
        if isinstance(item, dict):
            try:
                if item.get("confidence") is not None and float(item["confidence"]) < 0.5:
                    reasons.append("low-confidence")
            except (TypeError, ValueError):
                reasons.append("invalid-confidence")
            if _expired(item.get("expires_at")):
                reasons.append("expired")
            if str(item.get("key")) in conflicts:
                reasons.append("contradiction")
            references = item.get("files", [])
            if item.get("file"):
                references = [*references, item["file"]] if isinstance(references, list) else [item["file"]]
            for reference in references if isinstance(references, list) else []:
                path = Path(str(reference))
                resolved = path if path.is_absolute() else workspace / path
                try:
                    resolved.resolve(strict=False).relative_to(workspace.resolve())
                except ValueError:
                    reasons.append("out-of-workspace-reference")
                    continue
                if not resolved.exists():
                    reasons.append("stale-file-reference")
        if reasons:
            digest = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
            quarantined.append({"index": index, "sha256": digest, "reasons": sorted(set(reasons))})
        else:
            accepted.append(item)
    if quarantined:
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(quarantine_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            record = {"timestamp": datetime.now(timezone.utc).isoformat(), "items": quarantined}
            os.write(descriptor, (json.dumps(record, separators=(",", ":")) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return accepted, {"accepted": len(accepted), "quarantined": len(quarantined), "details": quarantined}
