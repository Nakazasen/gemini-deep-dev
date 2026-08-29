"""
Custom Harness Command Line Interface
=====================================
Unified CLI for running the deterministic agent feedback loop, testing the MCP server,
validating guardrails, and querying grounded documentation.
"""

import argparse
import json
import os
from pathlib import Path
import sys
from typing import List, Optional

from custom_harness.agents.coder import CoderSubAgent
from custom_harness.agents.critic import CriticSubAgent
from custom_harness.agents.feedback_loop import DeterministicFeedbackLoop
from custom_harness.config import HarnessSettings, load_settings
from custom_harness.guardrails.engine import GuardrailEngine
from custom_harness.guardrails.exceptions import (
    GuardrailError,
    PolicyViolationError,
)
from custom_harness.guardrails.models import (
    ActionType,
    FileOperation,
    GuardrailPolicy,
)
from custom_harness.harness.client import LLMClientFactory
from custom_harness.harness.models import HarnessConfig


def print_banner() -> None:
    """Print standard CLI banner."""
    print("=" * 70)
    print("  Antigravity Deterministic Gemini Flash Agent Harness v1.0.0")
    print("  MCP Grounding | Pydantic V2 Strict | Dual Sub-Agents | Fail-Closed")
    print("=" * 70)


def cmd_run(args: argparse.Namespace) -> int:
    """Run deterministic dual-agent feedback loop on a task."""
    # 1. Determine task description
    task_desc = ""
    if args.task_file:
        p = Path(args.task_file)
        if not p.is_file():
            print(f"Error: Task file '{args.task_file}' does not exist.", file=sys.stderr)
            return 1
        task_desc = p.read_text(encoding="utf-8")
    elif args.task:
        task_desc = args.task
    else:
        print("Error: Either --task or --task-file must be provided.", file=sys.stderr)
        return 1

    # 2. Load settings
    settings = load_settings(
        config_file=args.config,
        workspace_root=args.workspace
    )
    if args.mock:
        settings.use_mock = True
    if args.turns:
        settings.max_turns = args.turns
    if args.no_guardrails:
        settings.enable_guardrails = False

    harness_config = settings.to_harness_config()

    # 3. Retrieve grounding docs if enabled
    grounded_docs = None
    if settings.enable_grounding and not args.no_grounding:
        try:
            from custom_harness.mcp_grounding.retriever import DocRetriever
            retriever = DocRetriever(workspace_root=settings.workspace_root)
            search_res = retriever.search_docs(query=task_desc[:100], limit=3)
            if search_res.results:
                doc_lines = []
                for r in search_res.results:
                    doc_lines.append(f"### Reference: {r.title} ({r.uri}) [Hash: {r.sha256[:8]}]")
                    doc_lines.append(r.snippet)
                    doc_lines.append("")
                grounded_docs = "\n".join(doc_lines)
        except Exception as exc:
            if not args.json:
                print(f"[Notice] Grounding retriever fallback: {exc}", file=sys.stderr)

    # 4. Initialize client and sub-agents
    client = LLMClientFactory.create_client(
        model_name=settings.model_name,
        api_key=settings.api_key,
        use_mock=settings.use_mock,
    )

    coder = CoderSubAgent(client=client, max_retries=settings.max_retries)
    critic = CriticSubAgent(client=client, max_retries=settings.max_retries)

    guardrail_engine = None
    if settings.enable_guardrails:
        policy = GuardrailPolicy(workspace_root=settings.workspace_root, fail_closed=True)
        guardrail_engine = GuardrailEngine(policy=policy)

    loop = DeterministicFeedbackLoop(
        coder=coder,
        critic=critic,
        config=harness_config,
        guardrail_engine=guardrail_engine,
    )

    if not args.json:
        print_banner()
        print(f"[*] Task: {task_desc[:80]}...")
        print(f"[*] Workspace: {settings.workspace_root}")
        print(f"[*] Model: {settings.model_name} (Mock: {settings.use_mock})")
        print(f"[*] Guardrails: {'Enabled' if settings.enable_guardrails else 'Disabled'}")
        print(f"[*] Starting feedback loop (max {settings.max_turns} turns)...")
        print("-" * 70)

    result = loop.run(
        task_description=task_desc,
        grounded_docs=grounded_docs,
        apply_changes_to_disk=args.apply,
    )

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print("\n" + "=" * 70)
        print(f"  Execution Result: {'SUCCESS' if result.success else 'FAILED'}")
        print(f"  Final Verdict:    {result.final_verdict.value}")
        print(f"  Iterations:       {result.iterations_count}")
        print(f"  Terminal State:   {result.terminal_state.value}")
        print("=" * 70)

        if result.final_coder_output:
            print("\n[Approved Plan Steps]:")
            for step in result.final_coder_output.plan_steps:
                print(f"  - {step}")

            print(f"\n[File Operations ({len(result.final_coder_output.file_operations)})]:")
            for op in result.final_coder_output.file_operations:
                applied_str = " (Applied to disk)" if args.apply else ""
                print(f"  [{op.action.value.upper()}] {op.file_path}{applied_str}: {op.description}")

        if result.error:
            print(f"\n[Error Details]: {result.error}", file=sys.stderr)

    return 0 if result.success else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run FastMCP documentation grounding server."""
    from custom_harness.mcp_server import main as run_mcp
    return run_mcp()


def cmd_guardrail(args: argparse.Namespace) -> int:
    """Validate a path or content through the Declarative Guardrail Engine."""
    ws = Path(args.workspace).resolve() if args.workspace else Path.cwd().resolve()
    policy = GuardrailPolicy(workspace_root=ws, fail_closed=True)
    engine = GuardrailEngine(policy=policy)

    content = None
    if args.content_file:
        cf = Path(args.content_file)
        if cf.is_file():
            content = cf.read_text(encoding="utf-8")

    action = ActionType(args.action.lower()) if args.action else ActionType.WRITE
    target = args.path or "test.py"

    op = FileOperation(
        target_path=target,
        action=action,
        content=content
    )

    try:
        result = engine.validate_operation(op)
    except (GuardrailError, PolicyViolationError) as ge:
        if args.json:
            print(json.dumps({
                "is_valid": False,
                "error": str(ge),
                "violation_type": getattr(ge, "violation_type", ge.__class__.__name__),
                "details": getattr(ge, "details", {})
            }, indent=2))
        else:
            print(f"Error: Guardrail policy violation: {ge}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print_banner()
        print(f"Target Path: {target}")
        print(f"Action:      {action.value}")
        print(f"Permitted:   {'YES' if result.is_valid else 'NO (BLOCKED)'}")
        if result.violations:
            print("\nViolations:")
            for v in result.violations:
                print(f"  - [{v.violation_type}] {v.message}")
        if result.warnings:
            print("\nWarnings:")
            for w in result.warnings:
                print(f"  - {w}")

    return 0 if result.is_valid else 1


def cmd_grounding(args: argparse.Namespace) -> int:
    """Query documentation grounding provider."""
    ws = Path(args.workspace).resolve() if args.workspace else Path.cwd().resolve()
    from custom_harness.mcp_grounding.retriever import DocRetriever
    retriever = DocRetriever(workspace_root=ws)

    if args.search:
        search_res = retriever.search_docs(query=args.search, limit=args.limit)
        results = search_res.results
        if args.json:
            print(json.dumps([r.model_dump() for r in results], indent=2))
        else:
            print_banner()
            print(f"Query: '{args.search}' (Found {len(results)} matches)\n")
            for idx, r in enumerate(results, 1):
                print(f"[{idx}] {r.title} (Score: {r.score:.2f})")
                print(f"    Path: {r.uri}")
                print(f"    Doc-Hash: {r.sha256[:16]}...")
                print(f"    Snippet: {r.snippet[:120]}...\n")
        return 0

    print("Please specify --search <query>", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="antigravity-harness",
        description="Antigravity Deterministic Gemini Flash Agent Harness CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Run subcommand
    run_parser = subparsers.add_parser("run", help="Run dual-agent deterministic feedback loop")
    run_parser.add_argument("--task", "-t", type=str, help="Task description string")
    run_parser.add_argument("--task-file", "-f", type=str, help="Path to task description file")
    run_parser.add_argument("--workspace", "-w", type=str, default=".", help="Workspace root directory")
    run_parser.add_argument("--turns", type=int, default=5, help="Maximum review turns")
    run_parser.add_argument("--mock", action="store_true", help="Force deterministic mock mode")
    run_parser.add_argument("--apply", action="store_true", help="Apply approved changes to disk")
    run_parser.add_argument("--no-guardrails", action="store_true", help="Disable safety guardrails")
    run_parser.add_argument("--no-grounding", action="store_true", help="Disable doc grounding injection")
    run_parser.add_argument("--config", "-c", type=str, help="Path to harness configuration file")
    run_parser.add_argument("--json", action="store_true", help="Output JSON result")

    # MCP server subcommand
    mcp_parser = subparsers.add_parser("mcp", help="Launch FastMCP Documentation Grounding Server")

    # Guardrail subcommand
    guard_parser = subparsers.add_parser("guardrail", help="Evaluate file operations against guardrails")
    guard_parser.add_argument("--path", "-p", type=str, required=True, help="Target file path to check")
    guard_parser.add_argument("--action", "-a", type=str, default="write", help="Action (read, write, delete)")
    guard_parser.add_argument("--content-file", type=str, help="Path to content file to validate")
    guard_parser.add_argument("--workspace", "-w", type=str, default=".", help="Workspace root")
    guard_parser.add_argument("--json", action="store_true", help="Output JSON result")

    # Grounding subcommand
    ground_parser = subparsers.add_parser("grounding", help="Search or retrieve grounded documentation")
    ground_parser.add_argument("--search", "-s", type=str, help="Search query")
    ground_parser.add_argument("--limit", "-n", type=int, default=5, help="Max search results")
    ground_parser.add_argument("--workspace", "-w", type=str, default=".", help="Workspace root")
    ground_parser.add_argument("--json", action="store_true", help="Output JSON result")

    # Version subcommand
    subparsers.add_parser("version", help="Display package version")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command or args.command == "version":
        print_banner()
        print("Package: custom_harness")
        print("Version: 1.0.0")
        print(f"Python:  {sys.version.split()[0]}")
        return 0

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "mcp":
        return cmd_mcp(args)
    elif args.command == "guardrail":
        return cmd_guardrail(args)
    elif args.command == "grounding":
        return cmd_grounding(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
