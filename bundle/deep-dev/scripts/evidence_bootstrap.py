"""Build a bounded AgentMemory + Graphify evidence pack before Gemini answers."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any
from datetime import datetime, timezone
from uuid import uuid4

try:
    from .memory_adapter import AgentMemoryRESTBackend
    from .preflight import PreflightChecker
    from .graph_freshness import GraphFreshnessChecker
except ImportError:
    from memory_adapter import AgentMemoryRESTBackend
    from preflight import PreflightChecker
    from graph_freshness import GraphFreshnessChecker


MAX_GRAPH_BYTES = 25 * 1024 * 1024
MAX_CODE_FILES = 250
MAX_ITEMS_PER_SOURCE = 5
GENERIC_MEMORY_TITLES = {
    "apply_patch", "bash", "computer", "powershell", "run_command", "update_plan",
}
STOP_WORDS = {
    "about", "after", "before", "deep", "dev", "from", "giup", "help", "into",
    "nhung", "please", "suy", "the", "this", "that", "them", "tren", "with",
}


class EvidenceBootstrapError(RuntimeError):
    """Fail-closed bootstrap failure."""


def _terms(query: str) -> set[str]:
    return {
        token for token in re.findall(r"[\wÀ-ỹ]+", query.casefold())
        if len(token) >= 3 and token not in STOP_WORDS
    }


def _node_text(node: dict[str, Any]) -> str:
    values = [
        node.get("label"), node.get("name"), node.get("id"), node.get("type"),
        node.get("source_file"), node.get("source_location"), node.get("file_path"),
    ]
    return " | ".join(str(value) for value in values if value)


def _rank_graph_nodes(nodes: list[dict[str, Any]], query: str) -> list[dict[str, str]]:
    wanted = _terms(query)
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = _node_text(node)
        folded = text.casefold()
        score = sum(1 for term in wanted if term in folded)
        if score or len(ranked) < MAX_ITEMS_PER_SOURCE:
            ranked.append((score, text, node))
    ranked.sort(key=lambda item: (-item[0], item[1].casefold()))
    results: list[dict[str, str]] = []
    for score, text, node in ranked[:MAX_ITEMS_PER_SOURCE]:
        provenance = str(
            node.get("source_location") or node.get("source_file") or node.get("file_path") or "graphify:node"
        )
        results.append({
            "source": "graphify",
            "content": f"score={score}; {text}"[:2400],
            "provenance": provenance[:1000],
        })
    return results


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_receipt(
    provider: str,
    operation: str,
    started_at: str,
    started_monotonic: float,
    items: list[dict[str, str]],
    output_material: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "operation": operation,
        "status": "succeeded",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "duration_ms": max(0, int((time.monotonic() - started_monotonic) * 1000)),
        "evidence_count": len(items),
        "output_sha256": hashlib.sha256(output_material.encode("utf-8")).hexdigest(),
    }


def _graphify_command() -> list[str]:
    executable = shutil.which("graphify")
    return [executable] if executable else [sys.executable, "-m", "graphify"]


def _graphify_evidence(
    workspace_root: Path,
    query: str,
) -> tuple[str, list[dict[str, str]], dict[str, Any]]:
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    graph_file = GraphFreshnessChecker._graph_output_dir(workspace_root) / "graph.json"
    is_fresh, _freshness_reason = GraphFreshnessChecker.check_freshness(workspace_root)
    if graph_file.is_file() and is_fresh:
        if graph_file.stat().st_size > MAX_GRAPH_BYTES:
            raise EvidenceBootstrapError("Graphify graph exceeds the 25 MB bootstrap limit.")
        try:
            graph = json.loads(graph_file.read_text(encoding="utf-8"))
            nodes = graph.get("nodes", [])
            if not isinstance(nodes, list) or not nodes:
                raise ValueError("graph has no nodes")
            evidence = _rank_graph_nodes(nodes, query)
            if not evidence:
                raise ValueError("graph query returned no evidence")
            command = _graphify_command() + [
                "query", query[:1000], "--budget", "1200", "--graph", str(graph_file),
            ]
            completed = subprocess.run(
                command,
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=45,
            )
            if completed.returncode != 0 or not completed.stdout.strip():
                detail = (completed.stderr or completed.stdout).strip()[-500:]
                raise EvidenceBootstrapError(f"Graphify query execution failed: {detail}")
            receipt = _execution_receipt(
                "graphify",
                "freshness_check+query",
                started_at,
                started_monotonic,
                evidence,
                completed.stdout,
            )
            return "queried_fresh_graph", evidence, receipt
        except EvidenceBootstrapError:
            raise
        except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            raise EvidenceBootstrapError(f"Existing Graphify graph is unusable: {exc}") from exc

    try:
        from graphify.detect import detect
        from graphify.extract import extract

        detection = detect(workspace_root)
        raw_files = detection.get("files", {}).get("code", [])
        code_files = [Path(item) for item in raw_files[:MAX_CODE_FILES]]
        if not code_files:
            raise EvidenceBootstrapError("Graphify found no supported code files in the workspace.")
        local_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".deep-dev"))
        workspace_hash = hashlib.sha256(str(workspace_root).casefold().encode("utf-8")).hexdigest()[:16]
        cache_root = local_root / "deep-dev" / "graph-cache" / workspace_hash
        cache_root.mkdir(parents=True, exist_ok=True)
        extraction = extract(code_files, cache_root=cache_root)
        nodes = extraction.get("nodes", [])
        if not isinstance(nodes, list) or not nodes:
            raise EvidenceBootstrapError("Graphify in-memory AST extraction returned no nodes.")
        evidence = _rank_graph_nodes(nodes, query)
        if not evidence:
            raise EvidenceBootstrapError("Graphify in-memory query returned no evidence.")
        receipt = _execution_receipt(
            "graphify",
            "freshness_check+extract+query",
            started_at,
            started_monotonic,
            evidence,
            json.dumps(extraction, ensure_ascii=False, sort_keys=True),
        )
        return "extracted_fresh_ast", evidence, receipt
    except EvidenceBootstrapError:
        raise
    except Exception as exc:
        raise EvidenceBootstrapError(f"Graphify bootstrap failed: {type(exc).__name__}: {exc}") from exc


def _load_harness_validator():
    try:
        from custom_harness.evidence import validate_evidence_pack
        return validate_evidence_pack
    except ImportError:
        harness_root = Path.home() / ".gemini" / "antigravity" / "custom_harness"
        if harness_root.is_dir() and str(harness_root) not in sys.path:
            sys.path.insert(0, str(harness_root))
        try:
            from custom_harness.evidence import validate_evidence_pack
            return validate_evidence_pack
        except ImportError as exc:
            raise EvidenceBootstrapError("custom_harness evidence validator is unavailable.") from exc


def _project_aliases(workspace: Path, canonical_project_id: str) -> list[str]:
    folder = workspace.name.strip()
    versionless = re.sub(r"[-_.]?v?\d+(?:[._-]\d+){1,3}$", "", folder, flags=re.IGNORECASE).strip("-_. ")
    aliases = [canonical_project_id, folder, versionless]
    return list(dict.fromkeys(alias for alias in aliases if alias))


def build_evidence_pack(workspace_root: Path, query: str) -> dict[str, Any]:
    workspace = workspace_root.expanduser().resolve(strict=True)
    if not workspace.is_dir():
        raise EvidenceBootstrapError("Workspace root is not a directory.")
    task = query.strip()
    if not task:
        raise EvidenceBootstrapError("Deep Dev request text is empty.")
    project_id = PreflightChecker.resolve_project_id(workspace)

    entry_run_id = datetime.now(timezone.utc).strftime("entry_%Y%m%dT%H%M%SZ_") + uuid4().hex[:8]
    memory_started_at = _utc_now()
    memory_started_monotonic = time.monotonic()
    memory = AgentMemoryRESTBackend(timeout=1.5)
    # A cold AgentMemory start on Windows regularly exceeds five seconds.  The
    # PreInvocation hook is the only caller that can auto-start the service, so
    # use a bounded grace window instead of rejecting a valid first request.
    if not memory.ensure_available(startup_timeout=30.0):
        raise EvidenceBootstrapError(memory.last_error or "AgentMemory is unavailable.")
    recalled: list[dict[str, str]] = []
    recall_errors: list[str] = []
    seen_memory: set[str] = set()
    for alias in _project_aliases(workspace, project_id):
        try:
            for item in memory.recall(alias, task, limit=MAX_ITEMS_PER_SOURCE):
                content = str(item.get("content", "")).strip()
                if content and content not in seen_memory:
                    seen_memory.add(content)
                    recalled.append(item)
                    if len(recalled) >= MAX_ITEMS_PER_SOURCE:
                        break
        except Exception as exc:
            recall_errors.append(f"{alias}: {exc}")
        if recalled:
            break
    if not recalled and recall_errors:
        raise EvidenceBootstrapError("AgentMemory recall failed for every project alias: " + "; ".join(recall_errors))
    memory_items = [
        {
            "source": "agentmemory",
            "content": str(item.get("content", ""))[:2400],
            "provenance": str(item.get("source", "agentmemory:smart-search"))[:1000],
        }
        for item in recalled
        if str(item.get("content", "")).strip()
        and str(item.get("content", "")).strip().casefold() not in GENERIC_MEMORY_TITLES
    ]
    # A healthy smart-search that finds no project-specific observations is a
    # valid first-run outcome. The receipt records that zero-result recall;
    # Graphify remains mandatory evidence for every accepted entry.
    memory_receipt = _execution_receipt(
        "agentmemory",
        "health_check+smart_search_recall",
        memory_started_at,
        memory_started_monotonic,
        memory_items,
        json.dumps(memory_items, ensure_ascii=False, sort_keys=True),
    )

    graph_status, graph_items, graph_receipt = _graphify_evidence(workspace, task)
    raw_pack = {
        "schema_version": "1.1",
        "entry_run_id": entry_run_id,
        "workspace_root": str(workspace),
        "project_id": project_id,
        "query": task[:8000],
        "agentmemory_status": "ready",
        "graphify_status": graph_status,
        "provider_receipts": [memory_receipt, graph_receipt],
        "items": [*memory_items, *graph_items],
    }
    validator = _load_harness_validator()
    try:
        result = validator(raw_pack)
    except Exception as exc:
        raise EvidenceBootstrapError(f"custom_harness rejected the evidence pack: {exc}") from exc
    local_root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".deep-dev"))
    artifact_dir = local_root / "deep-dev" / "entry-runs" / project_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"{entry_run_id}.json"
    temporary_path = artifact_path.with_suffix(".tmp")
    result["entry_artifact"] = str(artifact_path)
    temporary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_path.replace(artifact_path)
    return result


def format_evidence_for_injection(pack: dict[str, Any]) -> str:
    lines = [
        "DEEP DEV EVIDENCE BOOTSTRAP: READY",
        f"Evidence SHA-256: {pack['evidence_sha256']}",
        f"Entry run: {pack['entry_run_id']}",
        f"Project: {pack['project_id']}",
        f"AgentMemory: {pack['agentmemory_status']} ({sum(1 for item in pack['items'] if item['source'] == 'agentmemory')} recalled)",
        f"Graphify: {pack['graphify_status']} ({sum(1 for item in pack['items'] if item['source'] == 'graphify')} evidence nodes)",
        f"Harness: {pack['harness_status']} ({' -> '.join(pack['entry_trajectory'])})",
        f"Entry artifact: {pack['entry_artifact']}",
        "Use the evidence below before any answer. Distinguish evidence from inference:",
    ]
    for item in pack["items"]:
        lines.append(f"- [{item['source']}] {item['content']} (provenance: {item['provenance']})")
    return "\n".join(lines)
