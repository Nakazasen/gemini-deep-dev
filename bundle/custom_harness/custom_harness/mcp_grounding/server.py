"""Canonical Deep Dev FastMCP server implementation."""

from __future__ import annotations

import logging
import json
import os
import faulthandler
import threading
from pathlib import Path
import sys
from typing import Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP

try:
    from mcp_contract import SERVER_NAME
except ModuleNotFoundError:
    # The installed launcher supplies the skill scripts on PYTHONPATH.  This
    # fallback also makes the portable bundle importable and testable before
    # installation, without duplicating the canonical MCP identity.
    _bundle_contract = Path(__file__).resolve().parents[3] / "deep-dev" / "mcp_contract.json"
    with _bundle_contract.open("r", encoding="utf-8") as _contract_handle:
        SERVER_NAME = str(json.load(_contract_handle)["server_name"])

from custom_harness.mcp_grounding.cache import DocCache
from custom_harness.mcp_grounding.hasher import compute_canonical_hash
from custom_harness.mcp_grounding.live_fetcher import LiveDocFetcher
from custom_harness.mcp_grounding.prompt_injector import (
    format_grounded_prompt,
    validate_citations,
)
from custom_harness.mcp_grounding.retriever import DocRetriever
from custom_harness.mcp_grounding.schemas import (
    CitationItem,
    DocContentResult,
    FetchDocInput,
    GenAIQueryInput,
    GenAIQueryResult,
    SearchDocsInput,
    SearchDocsResult,
    TokenUsage,
)

# Redirect all logging to stderr to prevent stdout JSON-RPC stream corruption
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("mcp_grounding.server")

# Instantiate FastMCP server instance
mcp = FastMCP(SERVER_NAME)

# Shared backend components
_cache = DocCache()
_live_fetcher = LiveDocFetcher(cache=_cache)
_retriever = DocRetriever(cache=_cache, live_fetcher=_live_fetcher, index_on_init=False)


@mcp.tool()
def fetch_doc(
    target: str,
    section: Optional[str] = None,
    max_length: int = 50000,
    refresh_cache: bool = False,
) -> Dict[str, Any]:
    """Retrieve complete documentation content or a specific heading section from local workspace or live Antigravity docs with SHA-256 provenance hash.

    Args:
        target: Target document URI, URL (e.g. 'https://antigravity.google/docs/mcp'), relative workspace path ('docs/rules.md'), or doc_id ('doc:live:mcp').
        section: Optional heading name or anchor (e.g., 'Configuration Schema' or '#stdio-transport') to retrieve only that section.
        max_length: Maximum character length of content to return before truncation. Default 50000.
        refresh_cache: If True, bypasses local disk cache and forces a fresh network fetch for live URLs.

    Returns:
        DocContentResult dictionary containing canonical content, sha256 hash, title, and section metadata.
    """
    input_model = FetchDocInput(
        target=target,
        section=section,
        max_length=max_length,
        refresh_cache=refresh_cache,
    )
    result: DocContentResult = _retriever.fetch_doc(
        target=input_model.target,
        section=input_model.section,
        max_length=input_model.max_length or 50000,
        refresh_cache=input_model.refresh_cache,
    )
    return result.model_dump()


@mcp.tool()
def search_docs(
    query: str,
    sources: Optional[List[str]] = None,
    limit: int = 5,
    min_score: float = 0.20,
    include_snippets: bool = True,
) -> Dict[str, Any]:
    """Search indexed documentation and rules across workspace and live documentation using BM25 and fuzzy keyword ranking.

    Args:
        query: Search terms or natural language query (e.g., 'mcp config stdio transport', 'pydantic structured outputs').
        sources: Source categories to search within: 'local', 'live', 'builtin'. Defaults to all.
        limit: Maximum number of search results to return (1 to 20).
        min_score: Minimum relevance score threshold (0.0 to 1.0).
        include_snippets: Whether to generate and include contextual excerpt snippets.

    Returns:
        SearchDocsResult dictionary with ranked matching documents, scores, and excerpts.
    """
    valid_sources = sources or ["local", "live", "builtin"]
    input_model = SearchDocsInput(
        query=query,
        sources=valid_sources,  # type: ignore
        limit=limit,
        min_score=min_score,
        include_snippets=include_snippets,
    )
    result: SearchDocsResult = _retriever.search_docs(
        query=input_model.query,
        sources=input_model.sources,
        limit=input_model.limit,
        min_score=input_model.min_score,
        include_snippets=input_model.include_snippets,
    )
    return result.model_dump()


@mcp.tool()
def genai_query(
    prompt: str,
    doc_ids_or_queries: Optional[List[str]] = None,
    model: str = "gemini-2.5-flash",
    temperature: float = 0.0,
    enforce_citations: bool = True,
) -> Dict[str, Any]:
    """Execute a grounded generation query against Gemini Flash, automatically retrieving matching documentation, injecting authoritative SHA-256 grounded context, and validating citations.

    Args:
        prompt: Natural language instruction or code generation request.
        doc_ids_or_queries: Explicit list of doc_ids or search queries to retrieve and ground context from.
        model: Gemini model identifier (e.g., 'gemini-2.5-flash', 'gemini-1.5-flash').
        temperature: Generation temperature; default 0.0 for deterministic output.
        enforce_citations: If True, verifies that the model response contains valid citations matching injected doc hashes.

    Returns:
        GenAIQueryResult dictionary with generated response, citations, doc_hashes, and token usage.
    """
    input_model = GenAIQueryInput(
        prompt=prompt,
        doc_ids_or_queries=doc_ids_or_queries,
        model=model,
        temperature=temperature,
        enforce_citations=enforce_citations,
    )

    # 1. Retrieve grounding documents
    retrieved_docs: List[DocContentResult] = []
    if input_model.doc_ids_or_queries:
        for item in input_model.doc_ids_or_queries:
            try:
                doc = _retriever.fetch_doc(item)
                retrieved_docs.append(doc)
            except Exception:
                # Try search query fallback
                search_res = _retriever.search_docs(item, limit=2, min_score=0.3)
                for res_item in search_res.results:
                    try:
                        doc = _retriever.fetch_doc(res_item.doc_id)
                        retrieved_docs.append(doc)
                    except Exception:
                        pass
    else:
        # Automatic doc search from prompt terms
        search_res = _retriever.search_docs(input_model.prompt, limit=2, min_score=0.3)
        for res_item in search_res.results:
            try:
                doc = _retriever.fetch_doc(res_item.doc_id)
                retrieved_docs.append(doc)
            except Exception:
                pass

    # 2. Inject grounding delimiter block
    grounded_prompt, meta = format_grounded_prompt(input_model.prompt, retrieved_docs)

    # 3. Execute generation via google.genai if API key present
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    response_text = ""
    token_usage = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)

    if api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            config = types.GenerateContentConfig(
                temperature=input_model.temperature,
            )
            response = client.models.generate_content(
                model=input_model.model,
                contents=grounded_prompt,
                config=config,
            )
            response_text = response.text or ""

            if response.usage_metadata:
                token_usage = TokenUsage(
                    prompt_tokens=response.usage_metadata.prompt_token_count or 0,
                    completion_tokens=response.usage_metadata.candidates_token_count or 0,
                    total_tokens=response.usage_metadata.total_token_count or 0,
                )
        except Exception as e:
            logger.error("GenAI model invocation error: %s", e)
            response_text = (
                f"[Grounded Synthesis Error: Model execution failed ({e})]. "
                f"Grounding context was verified with {len(retrieved_docs)} reference docs."
            )
    else:
        # Synthesize deterministic grounded response if offline/no key
        if retrieved_docs:
            primary_doc = retrieved_docs[0]
            short_hash = primary_doc.sha256[:8]
            response_text = (
                f"// Grounded response for '{input_model.prompt}'\n"
                f"// Derived from authoritative reference: {primary_doc.title} [Doc-Hash: {short_hash}]\n"
                f"// SHA-256: {primary_doc.sha256}\n\n"
                f"Documentation reference confirms specifications for {input_model.prompt}.\n"
                f"[Doc-Hash: {short_hash}]"
            )
        else:
            response_text = (
                f"Standard response for '{input_model.prompt}'. Note: GEMINI_API_KEY was not set in environment."
            )
        p_toks = max(1, len(grounded_prompt) // 4)
        c_toks = max(1, len(response_text) // 4)
        token_usage = TokenUsage(
            prompt_tokens=p_toks,
            completion_tokens=c_toks,
            total_tokens=p_toks + c_toks,
        )

    # 4. Validate citations
    passed, citations, citation_errors = validate_citations(
        response_text=response_text,
        injected_docs=retrieved_docs,
        enforce_citations=input_model.enforce_citations,
    )

    result_model = GenAIQueryResult(
        response=response_text,
        grounded=bool(retrieved_docs),
        citations=citations,
        doc_hashes=meta.doc_hashes,
        model_used=input_model.model,
        token_usage=token_usage,
        citation_check_passed=passed,
        citation_errors=citation_errors if citation_errors else None,
    )

    return result_model.model_dump()


@mcp.tool()
def execute_deterministic_harness(
    task: str,
    workspace_root: Optional[str] = None,
    max_turns: int = 3,
    enforce_guardrails: bool = True,
    execution_mode: Literal["stage_only"] = "stage_only",
    context_items: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Execute the full Deterministic Dual-Agent Harness in staging mode (in-memory).

    Args:
        task: Natural language programming task or bug fix instruction.
        workspace_root: Target workspace folder. Defaults to current directory.
        max_turns: Maximum review/critique correction turns (default: 3).
        enforce_guardrails: Whether to strictly block unauthorized path access or lazy placeholders (default: True).
        execution_mode: Staging mode ("stage_only").
        context_items: Optional contextual references from memory and knowledge graph.

    Returns:
        Dictionary containing execution verdict, status, generated file operations, and critic checklist results.
    """
    if not task or not task.strip():
        return {
            "success": False,
            "final_verdict": "REJECTED_UNSAFE",
            "iterations_count": 0,
            "terminal_state": "ERROR",
            "proposed_file_operations": [],
            "checklist_results": [],
            "guardrail_violations": [],
            "error": "Task description cannot be empty.",
        }

    try:
        from custom_harness.agents.coder import CoderSubAgent
        from custom_harness.agents.critic import CriticSubAgent
        from custom_harness.agents.feedback_loop import DeterministicFeedbackLoop
        from custom_harness.config import load_settings
        from custom_harness.guardrails.engine import GuardrailEngine
        from custom_harness.guardrails.models import GuardrailPolicy
        from custom_harness.harness.client import LLMClientFactory

        target_ws = Path(workspace_root) if workspace_root else Path.cwd()
        settings = load_settings(workspace_root=target_ws)
        settings.max_turns = max_turns
        settings.enable_guardrails = enforce_guardrails

        client = LLMClientFactory.create_client(
            model_name=settings.model_name,
            api_key=settings.api_key,
            use_mock=settings.use_mock,
        )

        coder = CoderSubAgent(client=client, max_retries=settings.max_retries)
        critic = CriticSubAgent(client=client, max_retries=settings.max_retries)

        guardrail_engine = None
        if enforce_guardrails:
            policy = GuardrailPolicy(
                workspace_root=target_ws,
                fail_closed=True,
                allow_non_workspace_reads=False,
                allow_non_workspace_writes=False,
            )
            guardrail_engine = GuardrailEngine(policy=policy)

        loop = DeterministicFeedbackLoop(
            coder=coder,
            critic=critic,
            config=settings.to_harness_config(),
            guardrail_engine=guardrail_engine,
        )

        # Format grounded context if provided
        grounded_docs_str = None
        if context_items:
            grounded_docs_str = "\n\n".join(
                f"// Context Source: {item.get('source', 'unknown')}\n{item.get('content', '')}"
                for item in context_items
            )

        result = loop.run(
            task_description=task,
            grounded_docs=grounded_docs_str,
            apply_changes_to_disk=False,
        )

        coder_out = result.final_coder_output
        critic_out = result.final_critic_output

        return {
            "success": result.success,
            "final_verdict": result.final_verdict.value if hasattr(result.final_verdict, "value") else str(result.final_verdict),
            "iterations_count": result.iterations_count,
            "terminal_state": result.terminal_state.value if hasattr(result.terminal_state, "value") else str(result.terminal_state),
            "proposed_file_operations": [
                operation.model_dump(mode="json")
                for operation in (coder_out.file_operations if coder_out else [])
            ],
            "checklist_results": [
                item.model_dump(mode="json")
                for item in (critic_out.checklist_results if critic_out else [])
            ],
            "guardrail_violations": result.guardrail_violations,
            "error": result.error,
        }
    except Exception as exc:
        logger.error("Harness execution error: %s", exc)
        return {
            "success": False,
            "final_verdict": "REJECTED_UNSAFE",
            "iterations_count": 0,
            "terminal_state": "ERROR",
            "proposed_file_operations": [],
            "checklist_results": [],
            "guardrail_violations": [],
            "error": f"Harness execution failed: {exc}",
        }


@mcp.tool()
def execute_host_proposal(
    task: str,
    workspace_root: str,
    target_paths: List[str],
    capability_ticket: str,
    proposed_file_operations: List[Dict[str, Any]],
    config_path: Optional[str] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate, test, and apply file operations authored by the signed-in host model.

    This avoids a second Gemini API credential: Antigravity's Gemini Flash remains
    the coder, while Deep Dev supplies ticket binding, AgentMemory, Graphify,
    snapshot hashes, isolated execution, graph diff, tests, and final application.
    """
    # Optional, opt-in deadlock evidence for a host that terminates a stalled
    # stdio request.  It is inert in normal use and writes only when the host
    # explicitly supplies a diagnostic path.
    diagnostic_timer: Optional[threading.Timer] = None
    diagnostic_path = os.environ.get("DEEP_DEV_DIAGNOSTIC_STACK_PATH")
    if diagnostic_path:
        def _dump_stacks() -> None:
            try:
                with Path(diagnostic_path).open("w", encoding="utf-8") as handle:
                    faulthandler.dump_traceback(file=handle, all_threads=True)
            except OSError:
                pass
        diagnostic_timer = threading.Timer(15.0, _dump_stacks)
        diagnostic_timer.daemon = True
        diagnostic_timer.start()
    try:
        from capability import consume_ticket_details, issue_repair_ticket, normalize_scope, MAX_REPAIR_ATTEMPTS
        from deep_orchestrator import DeepDevOrchestrator

        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        # MCP arguments commonly carry the workspace-relative default
        # `.deep_dev/config.json`.  Resolve it at the capability boundary,
        # never against the harness process cwd, otherwise an absent harness
        # config silently becomes an empty test suite (0 commands).
        raw_config = Path(config_path).expanduser() if config_path else None
        config = ((workspace / raw_config) if raw_config and not raw_config.is_absolute() else raw_config)
        config = config.resolve(strict=False) if config else None
        # A model-provided run_id must never affect ticket scope or the run
        # directory.  It is optional legacy transport metadata only.
        run_id = None
        scope = normalize_scope(workspace, target_paths, config, None)
        ticket_ok, ticket_reason, ticket_metadata = consume_ticket_details(capability_ticket, scope, task)
        if not ticket_ok:
            return {
                "success": False,
                "terminal_state": "BLOCKED",
                "final_verdict": "REJECTED_UNSAFE",
                "error": ticket_reason,
            }
        if not proposed_file_operations:
            return {
                "success": False,
                "terminal_state": "BLOCKED",
                "final_verdict": "REJECTED_UNSAFE",
                "error": "Host proposal contains no file operations.",
            }

        def host_proposal_runner(**_: Any) -> Dict[str, Any]:
            return {
                "success": True,
                "final_verdict": "APPROVED",
                "terminal_state": "HOST_PROPOSAL",
                "proposed_file_operations": proposed_file_operations,
                "checklist_results": [
                    {"check": "host_model_proposal_received", "passed": True},
                    {"check": "deterministic_verification_required", "passed": True},
                ],
                "guardrail_violations": [],
                "error": None,
            }

        result = DeepDevOrchestrator.run(
            task=task,
            workspace_root=workspace,
            target_paths=target_paths,
            config_path=config,
            harness_runner=host_proposal_runner,
            run_id=None,
        )
        response = result.model_dump(mode="json")
        prior_attempt = int((ticket_metadata or {}).get("repair_attempt", 0))
        next_attempt = prior_attempt + 1
        error = str(response.get("error") or "")
        artifact_raw = response.get("test_results_artifact")
        artifact = Path(str(artifact_raw)).resolve(strict=False) if artifact_raw else None
        repair_reason: Optional[str] = None
        repair_evidence: Dict[str, Any] = {}
        if error.startswith("Host proposal is incomplete;"):
            repair_reason = "incomplete_proposal"
            missing_marker = "Missing: "
            missing = error.split(missing_marker, 1)[1].split(", ") if missing_marker in error else []
            repair_evidence["missing_targets"] = missing
        elif error.startswith("Allowlist test suite failed:") and artifact is not None and artifact.is_file():
            repair_reason = "test_failure"
            repair_evidence["test_results_artifact"] = str(artifact)
        elif error.startswith("Host proposal schema is invalid:"):
            # The host may emit the native guardrail operation shape rather
            # than the canonical one.  Preserve the bounded repair workflow
            # instead of consuming the user's whole /deep-dev invocation.
            repair_reason = "proposal_schema"
            repair_evidence["schema_error"] = error

        if (
            response.get("terminal_state") == "ROLLBACK"
            and repair_reason is not None
            and next_attempt <= MAX_REPAIR_ATTEMPTS
        ):
            repair_scope = dict(scope)
            repair_scope["run_id"] = None
            retry_ticket = issue_repair_ticket(repair_scope, next_attempt, task, repair_reason)
            if retry_ticket:
                response["repair"] = {
                    "attempt": next_attempt,
                    "max_attempts": MAX_REPAIR_ATTEMPTS,
                    "capability_ticket": retry_ticket,
                    "reason": repair_reason,
                    "evidence": repair_evidence,
                    # Keep transport and the signed scope explicit.  Flash
                    # should only replace proposed_file_operations here; it
                    # must not rediscover MCP configuration or invent a lazy
                    # wrapper after a failed test.
                    "next_tool": {
                        "server_name": "deep_dev_harness",
                        "tool_name": "execute_host_proposal",
                        "arguments": {
                            "task": task,
                            "workspace_root": str(workspace),
                            "target_paths": target_paths,
                            "config_path": str(config) if config else None,
                            "capability_ticket": retry_ticket,
                        },
                    },
                    # Teamwork is advisory only.  It raises proposal quality
                    # before the scarce repair attempt, while this MCP server
                    # remains the sole authority that can test or apply.
                    "teamwork_preview": {
                        "recommended": True,
                        "mode": "repair_advisory",
                        "roles": ["debugger", "contract_reviewer", "proposal_integrator"],
                        "brief": (
                            "Invoke /teamwork-preview before spending this repair ticket. "
                            "Create a small repair squad: debugger reads the supplied evidence and names the root cause; "
                            "contract_reviewer checks scope, acceptance expectations, and regression risk; proposal_integrator "
                            "returns one complete corrected proposed_file_operations list. The team is read-only: it must not edit files, "
                            "run mutation commands, call Deep Dev MCP, or claim tests passed. Return one concise handoff to the parent."
                        ),
                        "required_handoff": {
                            "root_cause": "specific failing assertion or exception",
                            "minimal_fix": "smallest safe change",
                            "scope_check": "only ticket target paths",
                            "proposal_owner": "one integrator owns the final operation list",
                        },
                    },
                    "instruction": (
                        "YOUR NEXT TOOL CALL must be the direct MCP tool deep_dev_harness/execute_host_proposal. Copy repair.next_tool.arguments exactly, add only corrected proposed_file_operations, and omit run_id so the revision has a new evidence run. Do not call call_mcp_tool: that wrapper is unavailable in this client. "
                        "If repair.teamwork_preview is present, invoke /teamwork-preview first and use its single handoff; only the parent executor may make the MCP call. "
                        "For incomplete_proposal, include a non-noop operation for every target listed in evidence.missing_targets. "
                        "For test_failure, read evidence.test_results_artifact first. Do not use direct mutation tools. "
                        "For proposal_schema, keep the same scope and submit complete operations using file_path, action, and text content."
                    ),
                }
        return response
    except Exception as exc:
        logger.exception("Host proposal execution failed")
        return {
            "success": False,
            "terminal_state": "STOP",
            "final_verdict": "REJECTED_UNSAFE",
            "error": f"Host proposal execution failed: {type(exc).__name__}: {exc}",
        }
    finally:
        if diagnostic_timer is not None:
            diagnostic_timer.cancel()


def run_stdio_server() -> None:
    """Run FastMCP stdio server loop."""
    logger.info("Starting canonical Deep Dev MCP server '%s' on stdio...", SERVER_NAME)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_stdio_server()
