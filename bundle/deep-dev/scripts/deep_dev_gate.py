"""Fail-closed Antigravity hook for the deep-dev workflow."""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any

try:
    from .capability import TOKEN_RE
    from .integrity import verify_integrity
    from .mcp_contract import SERVER_NAME, TOOL_NAME, WRAPPER_TOOL
except ImportError:
    from capability import TOKEN_RE
    from integrity import verify_integrity
    from mcp_contract import SERVER_NAME, TOOL_NAME, WRAPPER_TOOL


TERMINAL_TOOLS = {"run_command", "command", "shell_command", "terminal"}
MUTATING_TOOL = re.compile(
    r"(?:^|_)(?:write|edit|replace|delete|remove|move|rename|patch|apply|create|copy|mkdir|"
    r"upload|deploy|publish|execute|run|send|post|put)(?:_|$)", re.IGNORECASE,
)
READ_ONLY_TOOL = re.compile(
    r"^(?:read|view|list|search|grep|find|inspect|query|status|describe|fetch|get)(?:_|$)", re.IGNORECASE,
)
SAFE_INTERACTION_TOOLS = {
    "ask_question",
    "request_user_input",
    "notify_user",
    "update_plan",
}
DIRECT_HOST_PROPOSAL_TOOLS = {
    TOOL_NAME.casefold(),
    f"{SERVER_NAME}/{TOOL_NAME}".casefold(),
    f"mcp__{SERVER_NAME}__{TOOL_NAME}".casefold(),
}
MCP_WRAPPER_TOOLS = {WRAPPER_TOOL}
SHELL_META = re.compile(r"[;&|><`\r\n]")
SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SENSITIVE_ARGUMENT = re.compile(r"(?i)(--(?:entry|capability)-ticket\s+)(?:\"[^\"]+\"|'[^']+'|\S+)")
MAX_AUDIT_BYTES = 1024 * 1024


def _command_line(args: dict[str, Any]) -> str:
    for key in ("CommandLine", "command", "cmd", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
    return ""


def _audit_denial(payload: dict[str, Any], result: dict[str, str]) -> None:
    """Persist bounded, token-redacted denial evidence outside repositories."""
    try:
        tool_call = payload.get("toolCall") if isinstance(payload, dict) else None
        tool_call = tool_call if isinstance(tool_call, dict) else {}
        name = tool_call.get("name")
        args = tool_call.get("args")
        args = args if isinstance(args, dict) else {}
        command = SENSITIVE_ARGUMENT.sub(r"\1[redacted]", _command_line(args))[:4096]
        local = os.environ.get("LOCALAPPDATA")
        root = Path(local) / "deep-dev" if local else Path.home() / ".deep-dev"
        path = root / "gate-audit.jsonl"
        previous = root / "gate-audit.previous.jsonl"
        root.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size >= MAX_AUDIT_BYTES:
            os.replace(path, previous)
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": "deny",
            "reason": str(result.get("reason", ""))[:512],
            "tool": str(name)[:128],
            "argument_keys": sorted(str(key)[:128] for key in args),
            "command_redacted": command,
        }
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (OSError, RuntimeError, TypeError, ValueError):
        pass


def _split_windows(command: str) -> list[str]:
    argc = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = parser(command, ctypes.byref(argc))
    if not argv:
        raise ValueError("CommandLineToArgvW rejected the command")
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


def _split_command(command: str) -> list[str]:
    if not command.strip() or SHELL_META.search(command):
        return []
    try:
        return _split_windows(command) if os.name == "nt" else shlex.split(command)
    except (OSError, ValueError):
        return []


def _same_path(raw: str, expected: Path) -> bool:
    try:
        return Path(raw).expanduser().resolve(strict=False) == expected.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return False


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _is_orchestrator_argv(argv: list[str]) -> bool:
    if len(argv) < 7:
        return False
    expected_script = Path(__file__).with_name("deep_orchestrator.py")
    if not _same_path(argv[0], Path(sys.executable)) or not _same_path(argv[1], expected_script):
        return False

    task: str | None = None
    workspace: Path | None = None
    targets: list[Path] = []
    config_path: Path | None = None
    run_id: str | None = None
    capability_ticket: str | None = None
    seen_singletons: set[str] = set()
    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--json":
            if token in seen_singletons:
                return False
            seen_singletons.add(token)
            index += 1
            continue
        if token in {"--workspace-root", "--target-path", "--config-path", "--run-id", "--capability-ticket"}:
            if index + 1 >= len(argv):
                return False
            value = argv[index + 1]
            if token != "--target-path" and token in seen_singletons:
                return False
            seen_singletons.add(token)
            if token == "--workspace-root":
                workspace = Path(value).expanduser()
            elif token == "--target-path":
                targets.append(Path(value).expanduser())
            elif token == "--config-path":
                config_path = Path(value).expanduser()
            elif token == "--run-id":
                run_id = value
            else:
                capability_ticket = value
            index += 2
            continue
        if token.startswith("-") or task is not None:
            return False
        task = token
        index += 1

    if not task or not task.strip() or workspace is None or not targets:
        return False
    workspace = workspace.resolve(strict=False)
    if not workspace.is_absolute() or not workspace.is_dir():
        return False
    for target in targets:
        candidate = target if target.is_absolute() else workspace / target
        if not _under(candidate, workspace):
            return False
    if config_path is not None:
        candidate = config_path if config_path.is_absolute() else workspace / config_path
        if not _under(candidate, workspace):
            return False
    if run_id is not None and not SAFE_RUN_ID.fullmatch(run_id):
        return False
    if capability_ticket is None:
        return False
    # This recognises the prohibited legacy form only.  It must not consume
    # its ticket: the canonical MCP server is the only execution path that
    # validates and consumes a capability.
    return True


def _is_scope_exchange_argv(argv: list[str]) -> bool:
    if len(argv) < 8:
        return False
    expected_script = Path(__file__).with_name("deep_dev_scope.py")
    if not _same_path(argv[0], Path(sys.executable)) or not _same_path(argv[1], expected_script):
        return False
    entry: str | None = None
    workspace: Path | None = None
    targets: list[Path] = []
    seen: set[str] = set()
    index = 2
    while index < len(argv):
        token = argv[index]
        if token not in {"--entry-ticket", "--workspace-root", "--target-path", "--config-path", "--run-id"} or index + 1 >= len(argv):
            return False
        if token != "--target-path" and token in seen:
            return False
        seen.add(token)
        value = argv[index + 1]
        if token == "--entry-ticket":
            entry = value
        elif token == "--workspace-root":
            workspace = Path(value).expanduser().resolve(strict=False)
        elif token == "--target-path":
            targets.append(Path(value).expanduser())
        index += 2
    if entry is None or not TOKEN_RE.fullmatch(entry) or workspace is None or not workspace.is_dir() or not targets:
        return False
    return all(_under(target if target.is_absolute() else workspace / target, workspace) for target in targets)


def _is_deep_dev_invocation(payload: dict[str, Any]) -> bool:
    """Enforce Deep Dev only for the current explicit /deep-dev request.

    A global hook must not turn ordinary assistant work into a permanent
    restricted mode. Missing or untrusted transcript evidence stays strict.
    """
    transcript_value = payload.get("transcriptPath")
    if not isinstance(transcript_value, str) or not transcript_value:
        return True
    try:
        transcript = Path(transcript_value).expanduser().resolve(strict=True)
        transcript.relative_to((Path.home() / ".gemini").resolve(strict=True))
        if not transcript.is_file() or transcript.suffix.casefold() != ".jsonl":
            return True
        latest: str | None = None
        with transcript.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("source") == "USER_EXPLICIT" and event.get("type") == "USER_INPUT":
                    latest = str(event.get("content", ""))
        return bool(latest and re.search(r"(?:^|\s)/deep-dev(?:\s|$)", latest, flags=re.IGNORECASE))
    except (OSError, RuntimeError, ValueError):
        return True


def _is_read_only_argv(argv: list[str]) -> bool:
    if not argv:
        return False
    executable = Path(argv[0]).name.lower()
    rest = argv[1:]
    lowered = [part.lower() for part in rest]
    if executable in {"rg", "rg.exe"}:
        return not any(part == "--pre" or part.startswith("--pre=") for part in lowered)
    if executable in {"git", "git.exe"} and rest:
        actual_cmd = lowered
        if actual_cmd and actual_cmd[0] == "-c" and len(actual_cmd) >= 3:
            actual_cmd = actual_cmd[2:]
        if actual_cmd:
            command = actual_cmd[0]
            if command in {"status", "diff", "log", "show", "rev-parse", "rev-list"}:
                return True
            if command == "branch" and actual_cmd[1:] == ["--show-current"]:
                return True
    if executable in {"get-content", "get-childitem", "get-item", "get-location", "select-string", "test-path", "where", "where.exe"}:
        return True
    if executable in {"graphify", "graphify.exe"} and rest and lowered[0] in {"query", "path", "explain"}:
        return not any(part == "--save-result" or part.startswith("--save-result=") for part in lowered[1:])
    if executable in {"python", "python.exe", "py", "py.exe", "pytest", "pytest.exe"}:
        if any(part in {"pytest", "compileall", "audit", "--version", "-v", "-q"} for part in lowered):
            return True
        if "-c" in lowered or "-m" in lowered:
            return True
    return False


def _host_proposal_arguments(name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    """Accept verified direct MCP or the exact Antigravity lazy wrapper."""
    if name in DIRECT_HOST_PROPOSAL_TOOLS:
        return args
    if name != WRAPPER_TOOL:
        return None
    server = args.get("ServerName")
    tool = args.get("ToolName")
    arguments = args.get("Arguments")
    if str(server or "").strip() != SERVER_NAME or str(tool or "").strip() != TOOL_NAME:
        return None
    if isinstance(arguments, dict):
        return arguments
    return None


def _valid_host_proposal(args: dict[str, Any]) -> bool:
    ticket = args.get("capability_ticket")
    workspace = args.get("workspace_root")
    targets = args.get("target_paths")
    operations = args.get("proposed_file_operations")
    return bool(
        isinstance(ticket, str)
        and TOKEN_RE.fullmatch(ticket)
        and isinstance(workspace, str)
        and workspace.strip()
        and isinstance(targets, list)
        and targets
        and all(isinstance(item, str) and item.strip() for item in targets)
        and isinstance(operations, list)
        and operations
        and all(isinstance(item, dict) for item in operations)
    )


def decide(payload: dict[str, Any], verify_runtime_integrity: bool = True) -> dict[str, str]:
    if not _is_deep_dev_invocation(payload):
        return {"decision": "allow", "reason": "Normal mode: Deep Dev gate is inactive until /deep-dev is invoked."}
    if verify_runtime_integrity:
        integrity_ok, integrity_reason = verify_integrity()
        if not integrity_ok:
            return {"decision": "deny", "reason": integrity_reason}
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {"decision": "deny", "reason": "Deep Dev gate: malformed tool call."}
    name = str(tool_call.get("name") or "").strip().lower()
    args = tool_call.get("args")
    if not name or not isinstance(args, dict):
        return {"decision": "deny", "reason": "Deep Dev gate: malformed tool name or arguments."}
    if name in TERMINAL_TOOLS:
        argv = _split_command(_command_line(args))
        if _is_scope_exchange_argv(argv):
            return {"decision": "allow", "reason": "Verified Deep Dev scope exchange allowed."}
        if _is_orchestrator_argv(argv):
            return {
                "decision": "deny",
                "reason": "Deep Dev gate: direct orchestrator invocation is forbidden; submit the proposal through canonical execute_host_proposal MCP so evidence-driven revisions remain available.",
            }
        if _is_read_only_argv(argv):
            return {"decision": "allow", "reason": "Read-only discovery command allowed."}
        return {"decision": "deny", "reason": "Deep Dev gate: terminal command is not on the strict allowlist."}
    host_proposal_args = _host_proposal_arguments(name, args)
    if host_proposal_args is not None:
        if _valid_host_proposal(host_proposal_args):
            return {"decision": "allow", "reason": "Scope-ticketed Deep Dev host proposal allowed."}
        return {"decision": "deny", "reason": "Deep Dev gate: malformed host proposal denied."}
    if name in MCP_WRAPPER_TOOLS:
        server = str(args.get("ServerName") or "").strip().lower()
        tool = str(args.get("ToolName") or "").strip().lower()
        if server == "agentmemory":
            return {"decision": "allow", "reason": "AgentMemory checkpoint tool allowed."}
        if server == SERVER_NAME and tool in {"search_docs", "fetch_doc", "genai_query", "execute_deterministic_harness"}:
            return {"decision": "allow", "reason": "Deep Dev harness tool allowed."}
        return {"decision": "deny", "reason": "Deep Dev gate: untrusted MCP server or tool denied."}
    if MUTATING_TOOL.search(name):
        return {"decision": "deny", "reason": "Deep Dev gate: direct mutation is blocked. Run /deep-dev."}
    if name.casefold() in SAFE_INTERACTION_TOOLS:
        return {"decision": "allow", "reason": "Non-mutating user interaction or planning tool allowed."}
    if READ_ONLY_TOOL.search(name):
        return {"decision": "allow", "reason": "Explicitly read-only tool allowed."}
    return {"decision": "deny", "reason": "Deep Dev gate: unknown tool denied by default."}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = decide(payload, verify_runtime_integrity=True)
        if result.get("decision") == "deny":
            _audit_denial(payload, result)
    except Exception as exc:
        result = {"decision": "deny", "reason": f"Deep Dev gate failed closed: {type(exc).__name__}: {exc}"}
    json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
