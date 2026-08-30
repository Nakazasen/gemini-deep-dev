"""
.deep_dev Engine: Unified CLI & Workflow Orchestrator (deep_orchestrator.py)
============================================================================
Coordinates the 4-Phase deterministic developer workflow:
1. Preflight baseline, capability discovery, graph impact analysis
2. Fail-closed workspace snapshot boundary
3. Pure structured-output dual-agent feedback loop (Coder + Critic)
4. Unified patch serialization, external Git worktree isolation, allowlist test execution,
   fail-closed commit boundary, anti-race checks, and evidence-based cleanup.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

try:
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
    from .config_lock import load_and_lock_config, verify_config_hash, ConfigLockResult, ConfigLockError, DeepDevTestConfig
    from .snapshot import WorkspaceSnapshot
    from .patch_serializer import (
        PatchSerializer,
        UnauthorizedTargetPathError,
        CreateOnExistingFileError,
        BaseHashConflictError,
    )
    from .test_executor import TestExecutor, TestSuiteResult
    from .isolation_manager import IsolationManager, IsolationManagerError
    from .state_store import RunState, DeepDevState, StateTransitionError
    from .graph_freshness import GraphFreshnessChecker
    from .memory_adapter import (
        MemoryAdapter,
        MemoryBackend,
        AgentMemoryRESTBackend,
        VerificationEvidence,
        RecallResult,
    )
    from .preflight import PreflightChecker, PreflightCheckResult
    from .graph_diff import capture as capture_dependency_graph, compare as compare_dependency_graph
    from .memory_hygiene import review as review_memory
    from .proposal_adapter import ProposalAdapterError, adapt_operations
except ImportError:
    from path_utils import canonicalize_safe_relative_path, PathSecurityError
    from config_lock import load_and_lock_config, verify_config_hash, ConfigLockResult, ConfigLockError, DeepDevTestConfig
    from snapshot import WorkspaceSnapshot
    from patch_serializer import (
        PatchSerializer,
        UnauthorizedTargetPathError,
        CreateOnExistingFileError,
        BaseHashConflictError,
    )
    from test_executor import TestExecutor, TestSuiteResult
    from isolation_manager import IsolationManager, IsolationManagerError
    from state_store import RunState, DeepDevState, StateTransitionError
    from graph_freshness import GraphFreshnessChecker
    from memory_adapter import (
        MemoryAdapter,
        MemoryBackend,
        AgentMemoryRESTBackend,
        VerificationEvidence,
        RecallResult,
    )
    from preflight import PreflightChecker, PreflightCheckResult
    from graph_diff import capture as capture_dependency_graph, compare as compare_dependency_graph
    from memory_hygiene import review as review_memory
    from proposal_adapter import ProposalAdapterError, adapt_operations

logger = logging.getLogger("deep_dev.orchestrator")


def validate_declared_target_coverage(
    file_operations: Any,
    declared_targets: List[str],
) -> Tuple[bool, str]:
    """Require a non-noop operation for every explicitly scope-bound target."""
    try:
        required = {
            canonicalize_safe_relative_path(path, allow_root_dot=False)
            for path in declared_targets
        }
    except (PathSecurityError, TypeError) as exc:
        return False, f"Declared target path is invalid: {exc}"

    if not required:
        return True, ""
    if not isinstance(file_operations, list):
        return False, "Host proposal operations must be a list."

    covered: set[str] = set()
    for operation in file_operations:
        if not isinstance(operation, dict):
            return False, "Host proposal contains a malformed file operation."
        try:
            path = canonicalize_safe_relative_path(
                str(operation.get("file_path", "")),
                allow_root_dot=False,
            )
        except PathSecurityError as exc:
            return False, f"Host proposal contains an invalid target path: {exc}"
        action = str(operation.get("action", "")).casefold()
        if path in required and action != "noop":
            covered.add(path)

    missing = sorted(required - covered)
    if missing:
        return False, (
            "Host proposal is incomplete; every explicitly declared target needs "
            f"a non-noop operation. Missing: {', '.join(missing)}"
        )
    return True, ""


def separate_impact_from_mutation_scope(
    declared_targets: List[str],
    impacted_paths: List[str],
) -> Tuple[List[str], List[str]]:
    """Keep Graphify impact advisory; only signed targets may enter mutation scope."""
    mutation_scope = sorted(set(declared_targets))
    advisory_impact = sorted(set(impacted_paths) - set(mutation_scope))
    return mutation_scope, advisory_impact


def persist_test_results(run_dir: Path, suite_result: TestSuiteResult) -> Path:
    """Persist command-level test evidence before the disposable worktree is removed."""
    artifact = run_dir / "test_results.json"
    artifact.write_text(
        json.dumps(suite_result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return artifact


def resolve_custom_harness_path() -> Optional[Path]:
    """Dynamically discover custom_harness package path across environments."""
    # 1. Direct environment variable override
    env_path = os.environ.get("CUSTOM_HARNESS_PATH") or os.environ.get("ANTIGRAVITY_CUSTOM_HARNESS_PATH")
    if env_path:
        p = Path(env_path).resolve()
        if p.exists() and (p / "custom_harness").is_dir():
            return p

    # 2. Check canonical global user directory (~/.gemini/antigravity/custom_harness)
    canonical = (Path.home() / ".gemini" / "antigravity" / "custom_harness").resolve()
    if canonical.exists() and (canonical / "custom_harness").is_dir():
        return canonical

    # 3. Check USERPROFILE / APPDATA on Windows
    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        win_path = (Path(user_profile) / ".gemini" / "antigravity" / "custom_harness").resolve()
        if win_path.exists() and (win_path / "custom_harness").is_dir():
            return win_path

    # 4. Check relative traversal from this script file
    try:
        rel_path = (Path(__file__).resolve().parent.parent.parent.parent / "antigravity" / "custom_harness").resolve()
        if rel_path.exists() and (rel_path / "custom_harness").is_dir():
            return rel_path
    except Exception:
        pass

    return None


def ensure_custom_harness_importable() -> None:
    """Ensure custom_harness package is importable on sys.path."""
    try:
        import custom_harness  # type: ignore
        return
    except ImportError:
        pass

    harness_root = resolve_custom_harness_path()
    if harness_root and str(harness_root) not in sys.path:
        sys.path.insert(0, str(harness_root))


class DeepDevOrchestratorResult(BaseModel):
    success: bool
    run_id: str
    project_id: str
    terminal_state: str
    final_verdict: str
    applied_patch_path: Optional[str] = None
    patch_summary: Optional[Dict[str, Any]] = None
    test_results_summary: Optional[str] = None
    test_results_artifact: Optional[str] = None
    all_tests_passed: bool = False
    degraded_memory: bool = False
    degraded_graph: bool = False
    error: Optional[str] = None


class AgentMemoryMCPBackend:
    """
    AgentMemory MCP Client adapter conforming to exact tool schemas.
    Integration seam for connecting to AgentMemory MCP bridge when available.
    """

    def __init__(self, mcp_client_callable: Optional[Callable[..., Any]] = None):
        self._caller = mcp_client_callable

    def is_healthy(self) -> bool:
        return self._caller is not None

    def recall(self, project_id: str, query: str, limit: int = 5) -> List[Dict[str, str]]:
        if not self._caller:
            raise RuntimeError("AgentMemory MCP client is not configured")
        # Try memory_smart_search strictly with allowed schema: {"query": query, "limit": limit}
        try:
            res = self._caller("agentmemory", "memory_smart_search", {"query": query, "limit": limit})
            if isinstance(res, dict) and (res.get("isError") or res.get("error")):
                raise RuntimeError(f"MCP error: {res.get('error')}")
            if isinstance(res, list):
                return [{"source": "agentmemory", "content": str(item)} for item in res]
            elif isinstance(res, dict) and "results" in res and isinstance(res["results"], list):
                return [{"source": "agentmemory", "content": str(r)} for r in res["results"]]
            return []
        except Exception:
            # Fallback to memory_recall with schema: {"query": query, "limit": limit, "format": "compact"}
            try:
                res = self._caller("agentmemory", "memory_recall", {"query": query, "limit": limit, "format": "compact"})
                if isinstance(res, dict) and (res.get("isError") or res.get("error")):
                    raise RuntimeError(f"MCP error: {res.get('error')}")
                if isinstance(res, list):
                    return [{"source": "agentmemory", "content": str(item)} for item in res]
                return []
            except Exception as exc:
                logger.warning("AgentMemory MCP recall failed: %s", exc)
                return []

    def save_lesson(
        self,
        project_id: str,
        lesson_text: str,
        tags: List[str],
        evidence_data: Dict[str, Any],
    ) -> bool:
        if not self._caller:
            return False
        try:
            # Schema for memory_save: content, project, concepts, type
            concepts_str = ", ".join(tags) if tags else "deep_dev"
            payload = {
                "content": lesson_text,
                "project": project_id,
                "concepts": concepts_str,
                "type": "workflow",
            }
            res = self._caller("agentmemory", "memory_save", payload)
            if isinstance(res, dict) and (res.get("isError") or res.get("error")):
                logger.warning("AgentMemory MCP save returned error response: %s", res.get("error"))
                return False
            return True
        except Exception as exc:
            logger.warning("AgentMemory MCP save failed: %s", exc)
            return False


class DeepDevOrchestrator:
    """Executes the complete /deep-dev workflow loop with fail-closed FSM."""

    @classmethod
    def _cleanup_and_resolve_state(
        cls,
        ws: Path,
        wt_path: Optional[Path],
        project_id: str,
        run_id: str,
        initial_error: Optional[str],
        intended_state: DeepDevState = DeepDevState.ROLLBACK,
    ) -> Tuple[DeepDevState, str]:
        """
        Cleanup worktree fail-closed across all rollback/exit paths.
        If cleanup fails, terminal state MUST be STOP to signal uncleaned residual artifacts.
        """
        if wt_path is None:
            return intended_state, initial_error or "Unknown failure before worktree creation."

        clean_ok, clean_msg = IsolationManager.cleanup_worktree(
            main_repo=ws,
            worktree_path=wt_path,
            project_id=project_id,
            run_id=run_id,
            delete_branch=True,
        )
        if not clean_ok:
            combined_err = f"{initial_error or ''}; WORKTREE CLEANUP FAILED (residual on disk or git registry): {clean_msg}".strip("; ")
            return DeepDevState.STOP, combined_err

        return intended_state, initial_error or ""

    @classmethod
    def run(
        cls,
        task: str,
        workspace_root: Optional[Path] = None,
        target_paths: Optional[List[str]] = None,
        config_path: Optional[Path] = None,
        harness_runner: Optional[Callable[..., Dict[str, Any]]] = None,
        memory_backend: Optional[MemoryBackend] = None,
        run_id: Optional[str] = None,
    ) -> DeepDevOrchestratorResult:
        """
        Run the complete deterministic development workflow.
        """
        ws = (workspace_root or Path.cwd()).resolve()
        project_id = PreflightChecker.resolve_project_id(ws)

        circuit_open, circuit_reason = RunState.circuit_status(project_id)
        if circuit_open:
            recovery_memory = memory_backend or AgentMemoryRESTBackend()
            memory_recovered = recovery_memory.ensure_available() if isinstance(recovery_memory, AgentMemoryRESTBackend) else recovery_memory.is_healthy()
            graph_recovered, _ = GraphFreshnessChecker.ensure_graphify_ready(ws)
            baseline_recovered, _, _, _, _ = PreflightChecker.capture_git_baseline(ws)
            if memory_recovered and graph_recovered and baseline_recovered:
                RunState.reset_circuit(project_id, "AgentMemory, Graphify, and stable Git baseline passed automatic recovery probes.")
            else:
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id="circuit_open",
                    project_id=project_id,
                    terminal_state=DeepDevState.BLOCKED.value,
                    final_verdict="REJECTED_UNSAFE",
                    error=f"{circuit_reason} Automatic recovery probes failed (memory={memory_recovered}, graph={graph_recovered}, git_baseline={baseline_recovered}).",
                )

        # Wire memory backend if provided
        if memory_backend:
            MemoryAdapter.set_backend(memory_backend)

        # 1. State Store Initialization (PREFLIGHT)
        state = RunState.create(
            project_id=project_id,
            task=task,
            run_id=run_id,
        )

        # AgentMemory is mandatory. The CLI process connects through the primary REST surface.
        required_memory = memory_backend or AgentMemoryRESTBackend()
        MemoryAdapter.set_backend(required_memory)
        memory_ready = (
            required_memory.ensure_available()
            if isinstance(required_memory, AgentMemoryRESTBackend)
            else required_memory.is_healthy()
        )
        if not memory_ready:
            detail = getattr(required_memory, "last_error", None) or "health check failed"
            err_msg = f"Required AgentMemory could not be started or reached: {detail}"
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=True,
                degraded_graph=False,
                error=err_msg,
            )
        state.record_step("MEMORY_HEALTH", evidence={"healthy": True})

        # Graphify self-heals first. AST fallback is allowed only after a real install/update failure.
        graph_ready, graph_status = GraphFreshnessChecker.ensure_graphify_ready(ws)
        state.degraded_graph = not graph_ready
        if not graph_ready:
            logger.warning("Graphify unavailable after self-heal; AST fallback enabled: %s", graph_status)
        state.record_step("GRAPH_READY", status="ok" if graph_ready else "degraded", evidence={"ready": graph_ready})

        # 2. Run Preflight
        preflight_res = PreflightChecker.run_preflight(ws, config_path)
        state.git_head = preflight_res.git_head
        state.workspace_fingerprint = preflight_res.workspace_fingerprint
        state.config_sha256 = preflight_res.config_sha256
        state.degraded_memory = preflight_res.degraded_memory
        state.degraded_graph = preflight_res.degraded_graph

        if not preflight_res.passed:
            err_msg = f"Preflight checks failed: {'; '.join(preflight_res.errors)}"
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        state.record_step("PREFLIGHT", evidence={
            "git_head": state.git_head,
            "config_sha256": state.config_sha256,
            "workspace_dirty": not preflight_res.git_clean,
            "workspace_fingerprint": state.workspace_fingerprint,
        })

        # 3. Context Recall
        recall_res = MemoryAdapter.recall_context(project_id, task)
        if recall_res.degraded_memory:
            err_msg = "Required AgentMemory recall failed; refusing to continue in degraded mode."
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=True,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        context_items = recall_res.items
        context_items, hygiene_report = review_memory(
            context_items,
            ws,
            state.get_run_dir().parents[2] / "quarantine" / f"{project_id}-memory.jsonl",
        )
        state.degraded_memory = recall_res.degraded_memory
        state.save()
        state.record_step("MEMORY_RECALL", evidence={"item_count": len(context_items)})
        state.record_step("MEMORY_HYGIENE", status="ok" if hygiene_report["quarantined"] == 0 else "degraded", evidence=hygiene_report)

        # 4. Impact Analysis & Snapshot Boundary Locking
        declared = target_paths or []
        impacted, degraded_graph = GraphFreshnessChecker.query_impact(ws, declared)
        state.degraded_graph = degraded_graph
        allowed_paths, advisory_impact = separate_impact_from_mutation_scope(declared, impacted)
        state.allowed_paths = allowed_paths
        state.save()

        if not allowed_paths:
            err_msg = "No safe target paths resolved. Provide --target-path or use /deep-dev target discovery."
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        state.record_step("IMPACT_ANALYSIS", evidence={
            "target_count": len(allowed_paths),
            "advisory_impact_count": len(advisory_impact),
            "degraded": degraded_graph,
        })
        state.heartbeat("Capturing dependency baseline for signed mutation targets.")
        try:
            baseline_dependency_graph = capture_dependency_graph(ws, allowed_paths)
        except Exception as exc:
            err_msg = f"Scoped dependency baseline failed: {exc}"
            state.transition_to(DeepDevState.STOP, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.STOP.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        state.heartbeat(
            f"Dependency baseline complete for {baseline_dependency_graph.get('scanned_files', 0)} code files."
        )

        # 5. Snapshot Capture
        state.transition_to(DeepDevState.WORKSPACE_SNAPSHOT)
        try:
            snapshot = WorkspaceSnapshot.capture(
                workspace_root=ws,
                allowed_paths=allowed_paths,
                run_id=state.run_id,
                git_head=state.git_head,
            )
            snapshot_path = state.get_run_dir() / "snapshot.json"
            snapshot.save(snapshot_path)
        except Exception as exc:
            err_msg = f"Failed to capture workspace snapshot: {exc}"
            state.transition_to(DeepDevState.STOP, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.STOP.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        # 6. Dual-Agent Deterministic Harness (Pure Structured Output)
        harness_output: Dict[str, Any] = {}
        harness_exception: Optional[Exception] = None
        retryable_states = {"SCHEMA_FAILURE", "ERROR", "TRANSIENT_ERROR"}
        for attempt in range(1, 3):
            try:
                if harness_runner:
                    harness_output = harness_runner(task=task, workspace_root=str(ws), context_items=context_items)
                else:
                    ensure_custom_harness_importable()
                    from custom_harness.mcp_grounding.server import execute_deterministic_harness
                    harness_output = execute_deterministic_harness(
                        task=task, workspace_root=str(ws), context_items=context_items, execution_mode="stage_only",
                    )
                terminal = str(harness_output.get("terminal_state", ""))
                category = str(harness_output.get("error_category", ""))
                if harness_output.get("success") or (terminal not in retryable_states and category not in retryable_states):
                    break
                state.record_step("HARNESS_RETRY", status="retry", evidence={"attempt": attempt, "category": category or terminal})
            except Exception as exc:
                harness_exception = exc
                if attempt < 2:
                    state.record_step("HARNESS_RETRY", status="retry", evidence={"attempt": attempt, "exception": type(exc).__name__})
                    continue
                break
        if harness_exception is not None and not harness_output:
            exc = harness_exception
            err_msg = f"Harness runner invocation raised exception: {exc}"
            state.record_step("HARNESS_REVIEW", status="failed", evidence={"exception": type(exc).__name__})
            state.transition_to(DeepDevState.ROLLBACK, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.ROLLBACK.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        final_verdict = harness_output.get("final_verdict", "REJECTED_UNSAFE")
        try:
            adaptation = adapt_operations(
                harness_output.get("proposed_file_operations", []), snapshot, ws,
            )
        except ProposalAdapterError as exc:
            error = f"Host proposal schema is invalid: {exc}"
            state.record_step("HARNESS_REVIEW", status="failed", evidence={"schema_error": str(exc)})
            state.transition_to(DeepDevState.ROLLBACK, error=error)
            return DeepDevOrchestratorResult(
                success=False, run_id=state.run_id, project_id=project_id,
                terminal_state=DeepDevState.ROLLBACK.value, final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory, degraded_graph=state.degraded_graph, error=error,
            )
        proposed_ops = adaptation.operations
        harness_success = harness_output.get("success", False)

        if not harness_success or final_verdict != "APPROVED":
            err_msg = f"Dual-agent review rejected proposed changes. Verdict: {final_verdict}. Error: {harness_output.get('error')}"
            state.record_step("HARNESS_REVIEW", status="failed", evidence={"verdict": final_verdict})
            state.transition_to(DeepDevState.ROLLBACK, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.ROLLBACK.value,
                final_verdict=final_verdict,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        coverage_ok, coverage_error = validate_declared_target_coverage(proposed_ops, declared)
        if not coverage_ok:
            state.record_step("HARNESS_REVIEW", status="failed", evidence={"coverage_error": coverage_error})
            state.transition_to(DeepDevState.ROLLBACK, error=coverage_error)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.ROLLBACK.value,
                final_verdict="REJECTED_UNSAFE",
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=coverage_error,
            )
        state.record_step(
            "HARNESS_REVIEW",
            evidence={
                "verdict": final_verdict,
                "operation_count": len(proposed_ops),
                "ignored_empty_noops": adaptation.ignored_empty_noops,
                "normalized_write_actions": adaptation.normalized_write_actions,
                "normalized_operation_aliases": adaptation.normalized_fields,
                "expanded_exact_replacements": adaptation.expanded_exact_replacements,
            },
        )

        # 7. Serialize Unified Patch
        try:
            patch_text, patch_summary = PatchSerializer.serialize_operations(
                snapshot=snapshot,
                file_operations=proposed_ops,
                workspace_root=ws,
            )
            patch_file = state.get_run_dir() / "run.patch"
            patch_file.write_text(patch_text, encoding="utf-8")
            state.applied_patch_path = str(patch_file)
            state.save()
            state.record_step("PATCH_SERIALIZE", evidence={"patch_sha256": __import__("hashlib").sha256(patch_text.encode("utf-8")).hexdigest()})
        except Exception as exc:
            err_msg = f"Patch serialization failed: {exc}"
            state.transition_to(DeepDevState.ROLLBACK, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.ROLLBACK.value,
                final_verdict=final_verdict,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        # 8. Anti-Race Checks before Worktree Creation
        cfg_file = config_path or (ws / ".deep_dev" / "config.json")
        baseline_valid, current_head, current_fingerprint, _, baseline_err = PreflightChecker.capture_git_baseline(ws)
        if (
            not baseline_valid
            or current_head != state.git_head
            or current_fingerprint != state.workspace_fingerprint
        ):
            err_msg = f"Race condition detected: Git baseline changed before worktree creation ({baseline_err or 'fingerprint/HEAD mismatch'})."
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict=final_verdict,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        if not verify_config_hash(cfg_file, state.config_sha256):
            err_msg = "Race condition detected: Test config was tampered with before worktree creation."
            state.transition_to(DeepDevState.BLOCKED, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.BLOCKED.value,
                final_verdict=final_verdict,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        # 9. Apply to Isolated Worktree
        wt_path: Optional[Path] = None
        state.transition_to(DeepDevState.APPLY_TO_WORKTREE)
        try:
            wt_path = IsolationManager.create_worktree(ws, project_id, state.run_id)
            state.residual_worktrees.append(str(wt_path))
            state.save()
            mirror_ok, mirror_msg = IsolationManager.mirror_workspace_baseline(
                ws,
                wt_path,
                normalize_text_paths=set(snapshot.allowed_paths),
            )
            if not mirror_ok:
                raise IsolationManagerError(f"Failed to mirror current workspace baseline: {mirror_msg}")
            state.record_step("BASELINE_MIRROR", evidence={"workspace_dirty": not preflight_res.git_clean})
            # The verified proposal carries complete file content. Apply those
            # bytes against the mirrored snapshot instead of asking `git apply`
            # to reconcile a textual diff with Windows CRLF/untracked baseline
            # state. The patch remains an audit artifact; this path still
            # verifies scope and snapshot hashes before every write.
            apply_ok, apply_msg = IsolationManager.apply_verified_operations_to_main(
                main_repo=wt_path,
                snapshot=snapshot,
                file_operations=proposed_ops,
            )
            if not apply_ok:
                raise IsolationManagerError(f"Failed to apply verified proposal in worktree: {apply_msg}")
        except Exception as exc:
            term_state, err_msg = cls._cleanup_and_resolve_state(
                ws=ws,
                wt_path=wt_path,
                project_id=project_id,
                run_id=state.run_id,
                initial_error=f"Worktree patch application failed: {exc}",
                intended_state=DeepDevState.ROLLBACK,
            )
            state.transition_to(term_state, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=term_state.value,
                final_verdict=final_verdict,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )

        resolved_worktree_targets = [
            (wt_path / Path(item)).resolve(strict=False) if not Path(item).is_absolute()
            else (wt_path / Path(item).resolve(strict=False).relative_to(ws)).resolve(strict=False)
            for item in allowed_paths
        ]
        try:
            candidate_dependency_graph = capture_dependency_graph(wt_path, allowed_paths)
        except Exception as exc:
            term_state, err_msg = cls._cleanup_and_resolve_state(
                ws, wt_path, project_id, state.run_id,
                f"Scoped candidate dependency capture failed: {exc}",
                DeepDevState.ROLLBACK,
            )
            state.record_step("GRAPH_DIFF", status="failed", evidence={"capture_error": str(exc)})
            state.transition_to(term_state, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False, run_id=state.run_id, project_id=project_id,
                terminal_state=term_state.value, final_verdict=final_verdict,
                degraded_memory=state.degraded_memory, degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        graph_diff_report = compare_dependency_graph(
            baseline_dependency_graph,
            candidate_dependency_graph,
            resolved_worktree_targets,
            wt_path,
        )
        graph_diff_path = state.get_run_dir() / "graph_diff.json"
        graph_diff_path.write_text(json.dumps(graph_diff_report, ensure_ascii=False, indent=2), encoding="utf-8")
        if not graph_diff_report["safe"]:
            term_state, err_msg = cls._cleanup_and_resolve_state(
                ws, wt_path, project_id, state.run_id,
                "Dependency graph diff introduced an edge outside the capability scope.",
                DeepDevState.ROLLBACK,
            )
            state.record_step("GRAPH_DIFF", status="failed", evidence=graph_diff_report)
            state.transition_to(term_state, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False, run_id=state.run_id, project_id=project_id,
                terminal_state=term_state.value, final_verdict=final_verdict,
                degraded_memory=state.degraded_memory, degraded_graph=state.degraded_graph,
                error=err_msg,
            )
        state.record_step("GRAPH_DIFF", evidence=graph_diff_report)

        # 10. Execute Allowlist Test Suite in Worktree
        state.transition_to(DeepDevState.TEST_EXECUTE)
        try:
            lock_res = load_and_lock_config(cfg_file)
            suite_res = TestExecutor.execute_suite(lock_res.config, wt_path)
            state.test_results_summary = suite_res.summary
            state.save()
        except Exception as exc:
            suite_res = TestSuiteResult(
                all_passed=False,
                total_commands=0,
                passed_count=0,
                failed_count=1,
                results=[],
                summary=f"Test suite execution exception: {exc}",
            )
        test_results_artifact = persist_test_results(state.get_run_dir(), suite_res)
        state.test_results_artifact = str(test_results_artifact)
        state.save()
        failed_commands = [
            {"id": result.command_id, "exit_code": result.exit_code}
            for result in suite_res.results
            if not result.passed
        ]
        state.record_step(
            "TEST_EXECUTE",
            status="ok" if suite_res.all_passed else "failed",
            evidence={
                "summary": suite_res.summary,
                "artifact": str(test_results_artifact),
                "failed_commands": failed_commands,
            },
        )

        # 11. Safety Gates: Commit & Cleanup before ACCEPT_PATCH
        if suite_res.all_passed:
            # 11a. Commit safety gate
            commit_ok, commit_msg = IsolationManager.optional_commit(
                worktree_path=wt_path,
                snapshot=snapshot,
                commit_message=f"deep-dev: {task} (run {state.run_id})",
            )
            if not commit_ok:
                term_state, err_msg = cls._cleanup_and_resolve_state(
                    ws=ws,
                    wt_path=wt_path,
                    project_id=project_id,
                    run_id=state.run_id,
                    initial_error=f"Worktree commit boundary check failed: {commit_msg}",
                    intended_state=DeepDevState.ROLLBACK,
                )
                state.transition_to(term_state, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=term_state.value,
                    final_verdict=final_verdict,
                    test_results_summary=suite_res.summary,
                    all_tests_passed=True,
                    degraded_memory=state.degraded_memory,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )

            # 11b. Cleanup safety gate
            clean_ok, clean_msg = IsolationManager.cleanup_worktree(
                main_repo=ws,
                worktree_path=wt_path,
                project_id=project_id,
                run_id=state.run_id,
                delete_branch=True,
            )
            if not clean_ok:
                err_msg = f"Worktree cleanup failed (residual on disk or git list): {clean_msg}"
                state.transition_to(DeepDevState.STOP, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=DeepDevState.STOP.value,
                    final_verdict=final_verdict,
                    test_results_summary=suite_res.summary,
                    all_tests_passed=True,
                    degraded_memory=state.degraded_memory,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )
            cleaned_worktree = wt_path.resolve(strict=False)
            state.residual_worktrees = [
                item for item in state.residual_worktrees
                if Path(item).resolve(strict=False) != cleaned_worktree
            ]
            state.save()

            # 11c. Post-cleanup baseline verification. The original workspace may
            # intentionally be dirty; it must be byte-identical to the captured baseline.
            baseline_valid_post, head_post, fingerprint_post, _, err_post = PreflightChecker.capture_git_baseline(ws)
            if (
                not baseline_valid_post
                or head_post != state.git_head
                or fingerprint_post != state.workspace_fingerprint
            ):
                err_msg = f"Main workspace changed concurrently during verification: {err_post or 'fingerprint/HEAD mismatch'}."
                state.transition_to(DeepDevState.STOP, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=DeepDevState.STOP.value,
                    final_verdict=final_verdict,
                    degraded_memory=state.degraded_memory,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )

            # 11d. Required AgentMemory evidence persistence before ACCEPT_PATCH.
            evidence = VerificationEvidence(
                terminal_state="APPROVED",
                all_tests_passed=True,
                failed_test_count=0,
                test_summary=suite_res.summary,
                run_id=state.run_id,
                project_id=project_id,
            )
            memory_saved = MemoryAdapter.save_lesson(
                evidence=evidence,
                lesson_text=(
                    f"Verified task '{task}'. Allowed paths: {allowed_paths}. "
                    f"Patch summary: {patch_summary}. Tests: {suite_res.summary}. "
                    f"Dependency graph diff: {json.dumps(graph_diff_report, sort_keys=True, default=str)}"
                ),
                tags=["deep_dev", "verified_patch"],
            )
            if not memory_saved:
                err_msg = "Required AgentMemory save failed after verification; ACCEPT_PATCH denied."
                state.transition_to(DeepDevState.STOP, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=DeepDevState.STOP.value,
                    final_verdict=final_verdict,
                    applied_patch_path=state.applied_patch_path,
                    patch_summary=patch_summary,
                    test_results_summary=suite_res.summary,
                    all_tests_passed=True,
                    degraded_memory=True,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )

            state.record_step("MEMORY_SAVE", evidence={"saved": True})

            # 11e. Apply only the verified delta to the unchanged main baseline.
            apply_main_ok, apply_main_msg = IsolationManager.apply_verified_operations_to_main(
                main_repo=ws,
                snapshot=snapshot,
                file_operations=proposed_ops,
            )
            if not apply_main_ok:
                err_msg = f"Verified patch could not be applied to the main workspace: {apply_main_msg}"
                state.transition_to(DeepDevState.STOP, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=DeepDevState.STOP.value,
                    final_verdict="REJECTED_UNSAFE",
                    applied_patch_path=state.applied_patch_path,
                    patch_summary=patch_summary,
                    test_results_summary=suite_res.summary,
                    all_tests_passed=True,
                    degraded_memory=state.degraded_memory,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )
            state.record_step("MAIN_APPLY", evidence={"applied": True})

            # Refresh the persistent Graphify index against the exact accepted
            # workspace state.  A refresh failure is explicit degraded evidence;
            # it must never be reported as a successful graph update.
            # Post-apply indexing is observability, not a second acceptance
            # gate. Bound it tightly so a slow Graphify update cannot hold a
            # fully verified patch hostage; the exact degraded receipt remains
            # in the run evidence.
            graph_refreshed, graph_refresh_status = GraphFreshnessChecker.ensure_graphify_ready(
                ws, timeout_seconds=20
            )
            state.degraded_graph = not graph_refreshed
            state.save()
            trajectory_eval = state.record_step(
                "GRAPH_REFRESH",
                status="ok" if graph_refreshed else "degraded",
                evidence={"refreshed": graph_refreshed, "status": graph_refresh_status},
            )
            if not trajectory_eval.get("ready_for_accept"):
                err_msg = f"Trajectory evidence incomplete; ACCEPT_PATCH denied. Missing: {trajectory_eval.get('missing_phases')}"
                state.transition_to(DeepDevState.STOP, error=err_msg)
                return DeepDevOrchestratorResult(
                    success=False,
                    run_id=state.run_id,
                    project_id=project_id,
                    terminal_state=DeepDevState.STOP.value,
                    final_verdict="REJECTED_UNSAFE",
                    test_results_summary=suite_res.summary,
                    all_tests_passed=True,
                    degraded_memory=state.degraded_memory,
                    degraded_graph=state.degraded_graph,
                    error=err_msg,
                )

            # 11f. ACCEPT_PATCH exists only after verified main-workspace application.
            state.transition_to(DeepDevState.ACCEPT_PATCH)

            return DeepDevOrchestratorResult(
                success=True,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=DeepDevState.ACCEPT_PATCH.value,
                final_verdict="APPROVED",
                applied_patch_path=state.applied_patch_path,
                patch_summary=patch_summary,
                test_results_summary=suite_res.summary,
                all_tests_passed=True,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
            )
        else:
            # Tests failed -> Rollback with fail-closed cleanup check
            term_state, err_msg = cls._cleanup_and_resolve_state(
                ws=ws,
                wt_path=wt_path,
                project_id=project_id,
                run_id=state.run_id,
                initial_error=f"Allowlist test suite failed: {suite_res.summary}",
                intended_state=DeepDevState.ROLLBACK,
            )
            state.transition_to(term_state, error=err_msg)
            return DeepDevOrchestratorResult(
                success=False,
                run_id=state.run_id,
                project_id=project_id,
                terminal_state=term_state.value,
                final_verdict=final_verdict,
                applied_patch_path=state.applied_patch_path,
                patch_summary=patch_summary,
                test_results_summary=suite_res.summary,
                test_results_artifact=state.test_results_artifact,
                all_tests_passed=False,
                degraded_memory=state.degraded_memory,
                degraded_graph=state.degraded_graph,
                error=err_msg,
            )


def main() -> None:
    """CLI entrypoint for deep-dev orchestrator."""
    parser = argparse.ArgumentParser(
        prog="deep_orchestrator",
        description="Deterministic developer workflow orchestrator (pure structured output, git isolation, test allowlists).",
    )
    parser.add_argument("task", type=str, help="Task description or instruction for deep-dev.")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd(), help="Path to workspace root repository (default: cwd).")
    parser.add_argument("--target-path", action="append", dest="target_paths", default=[], help="Target file path to allow (repeatable).")
    parser.add_argument("--config-path", type=Path, default=None, help="Path to custom .deep_dev/config.json.")
    parser.add_argument("--run-id", type=str, default=None, help="Custom identifier for this run.")
    parser.add_argument("--capability-ticket", type=str, required=True, help="Single-use ticket issued by the /deep-dev entry hook.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON result to stdout.")

    args = parser.parse_args()

    message = (
        "Direct deep_orchestrator CLI execution is disabled. Submit a complete "
        "proposal only through deep_dev_harness.execute_host_proposal so ticket, "
        "scope, evidence, and bounded revisions remain enforced."
    )
    blocked = {
        "success": False,
        "terminal_state": DeepDevState.BLOCKED.value,
        "final_verdict": "REJECTED_UNSAFE",
        "error": message,
    }
    if args.json:
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
    else:
        print(f"[BLOCKED] {message}")
    sys.exit(2)


if __name__ == "__main__":
    main()
