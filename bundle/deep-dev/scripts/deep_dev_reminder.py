"""Inject Deep Dev guidance and issue a ticket only for explicit /deep-dev invocations."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid
from typing import Any
from urllib.parse import unquote, urlparse

try:
    from .capability import issue_ticket_once
    from .evidence_bootstrap import EvidenceBootstrapError, build_evidence_pack, format_evidence_for_injection
    from .integrity import verify_integrity
    from .mcp_contract import SERVER_NAME, TOOL_NAME
except ImportError:
    from capability import issue_ticket_once
    from evidence_bootstrap import EvidenceBootstrapError, build_evidence_pack, format_evidence_for_injection
    from integrity import verify_integrity
    from mcp_contract import SERVER_NAME, TOOL_NAME


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _latest_user_context(payload: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """Read only the latest explicit user input from Antigravity's trusted transcript path."""
    transcript_value = payload.get("transcriptPath")
    if not isinstance(transcript_value, str) or not transcript_value:
        return False, None, None
    try:
        transcript = Path(transcript_value).expanduser().resolve(strict=True)
        gemini_root = (Path.home() / ".gemini").resolve(strict=True)
        transcript.relative_to(gemini_root)
        if not transcript.is_file() or transcript.suffix.lower() != ".jsonl":
            return False, None, None
        latest_event: dict[str, Any] | None = None
        latest_deep_event: dict[str, Any] | None = None
        with open(transcript, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("source") == "USER_EXPLICIT" and event.get("type") == "USER_INPUT":
                    latest_event = event
                    if "/deep-dev" in str(event.get("content", "")).lower():
                        latest_deep_event = event
        if latest_event is None:
            return False, None, None
        latest_content = str(latest_event.get("content", ""))
        active_event = latest_event if "/deep-dev" in latest_content.lower() else None
        if active_event is None and latest_deep_event is not None:
            try:
                latest_time = datetime.fromisoformat(str(latest_event["created_at"]).replace("Z", "+00:00"))
                deep_time = datetime.fromisoformat(str(latest_deep_event["created_at"]).replace("Z", "+00:00"))
                if 0 <= (latest_time - deep_time).total_seconds() <= 600:
                    active_event = latest_deep_event
            except (KeyError, TypeError, ValueError):
                active_event = None
        if active_event is None:
            return False, None, None
        active_content = str(active_event.get("content", ""))
        identity = json.dumps({
            "transcript": str(transcript),
            "step_index": active_event.get("step_index"),
            "created_at": active_event.get("created_at"),
            "content_sha256": hashlib.sha256(active_content.encode("utf-8")).hexdigest(),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return True, identity, latest_content
    except (OSError, RuntimeError, ValueError):
        return False, None, None


def _latest_user_request(payload: dict[str, Any]) -> tuple[bool, str | None]:
    active, identity, _ = _latest_user_context(payload)
    return active, identity


def _latest_user_requested_deep_dev(payload: dict[str, Any]) -> bool:
    return _latest_user_request(payload)[0]


def _resolve_existing_path(raw: str) -> Path | None:
    normalized = raw.strip()
    if not normalized:
        return None
    if normalized.casefold().startswith("file://"):
        parsed = urlparse(normalized)
        normalized = unquote(parsed.path)
        if parsed.netloc:
            normalized = f"//{parsed.netloc}{normalized}"
        elif os.name == "nt" and re.match(r"^/[A-Za-z]:", normalized):
            normalized = normalized[1:]
    try:
        return Path(normalized).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _workspace_from_active_document(payload: dict[str, Any]) -> Path | None:
    """Recover the project root from Antigravity's trusted transcript metadata."""
    transcript_value = payload.get("transcriptPath")
    if not isinstance(transcript_value, str) or not transcript_value:
        return None
    try:
        transcript = Path(transcript_value).expanduser().resolve(strict=True)
        gemini_root = (Path.home() / ".gemini").resolve(strict=True)
        transcript.relative_to(gemini_root)
        if not transcript.is_file() or transcript.suffix.casefold() != ".jsonl":
            return None
        latest_content = ""
        with open(transcript, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("source") == "USER_EXPLICIT" and event.get("type") == "USER_INPUT":
                    latest_content = str(event.get("content", ""))
        metadata_match = re.search(
            r"<ADDITIONAL_METADATA>(.*?)</ADDITIONAL_METADATA>",
            latest_content,
            flags=re.DOTALL,
        )
        if not metadata_match:
            return None
        active_match = re.search(
            r"(?m)^Active Document:\s*(.+?)\s+\(LANGUAGE_[^)]+\)\s*$",
            metadata_match.group(1),
        )
        if not active_match:
            return None
        active_path = _resolve_existing_path(active_match.group(1))
        if active_path is None:
            return None
        start = active_path.parent if active_path.is_file() else active_path
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists() or (candidate / ".deep_dev" / "config.json").is_file():
                return candidate
        return start
    except (OSError, RuntimeError, ValueError):
        return None


def _workspace_root(payload: dict[str, Any]) -> Path:
    raw_paths = payload.get("workspacePaths")
    candidates: list[Any] = list(raw_paths) if isinstance(raw_paths, list) else []
    candidates.extend(payload.get(key) for key in ("workspacePath", "workspaceRoot", "cwd"))
    for item in candidates:
        if isinstance(item, dict):
            raw = next(
                (item.get(key) for key in ("path", "fsPath", "rootPath", "uri") if item.get(key)),
                None,
            )
        else:
            raw = item
        if not isinstance(raw, str) or not raw.strip():
            continue
        candidate = _resolve_existing_path(raw)
        if candidate is not None and candidate.is_dir():
            return candidate
    transcript_workspace = _workspace_from_active_document(payload)
    if transcript_workspace is not None:
        return transcript_workspace
    raise EvidenceBootstrapError("Antigravity did not provide an existing workspace root.")


def _command_prefix(script_name: str) -> str:
    parts = [str(Path(sys.executable).resolve()), str(Path(__file__).with_name(script_name).resolve())]
    return subprocess.list2cmdline(parts) if os.name == "nt" else shlex.join(parts)


ENTRY_CACHE_VERSION = "1.0"
ENTRY_LOCK_STALE_SECONDS = 120
ENTRY_LOCK_WAIT_SECONDS = 30


def _runtime_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "deep-dev" if local else Path.home() / ".deep-dev"


def _entry_cache_path(invocation_key: str) -> Path:
    identity = hashlib.sha256(invocation_key.encode("utf-8")).hexdigest()
    return _runtime_root() / "entry-cache" / f"{identity}.json"


def _load_cached_evidence(invocation_key: str, workspace: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(_entry_cache_path(invocation_key).read_text(encoding="utf-8"))
        invocation_sha256 = hashlib.sha256(invocation_key.encode("utf-8")).hexdigest()
        if record.get("cache_version") != ENTRY_CACHE_VERSION:
            return None
        if record.get("invocation_sha256") != invocation_sha256:
            return None
        artifact = Path(str(record["entry_artifact"])).expanduser().resolve(strict=True)
        entry_root = (_runtime_root() / "entry-runs").resolve(strict=True)
        artifact.relative_to(entry_root)
        pack = json.loads(artifact.read_text(encoding="utf-8"))
        if Path(str(pack.get("workspace_root", ""))).resolve(strict=True) != workspace.resolve(strict=True):
            return None
        if pack.get("evidence_sha256") != record.get("evidence_sha256"):
            return None
        if pack.get("harness_status") != "preflight_passed":
            return None
        if pack.get("entry_trajectory", [])[-1:] != ["READY"]:
            return None
        return pack
    except (KeyError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _cache_evidence(invocation_key: str, pack: dict[str, Any]) -> None:
    path = _entry_cache_path(invocation_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex[:8]}")
    record = {
        "cache_version": ENTRY_CACHE_VERSION,
        "invocation_sha256": hashlib.sha256(invocation_key.encode("utf-8")).hexdigest(),
        "entry_artifact": pack["entry_artifact"],
        "evidence_sha256": pack["evidence_sha256"],
    }
    temporary.write_text(json.dumps(record, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def _remove_abandoned_entry_lock(lock_path: Path) -> bool:
    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            owner_pid = int(owner["pid"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            owner_pid = 0
        if owner_pid > 0 and _process_is_alive(owner_pid):
            return False
        if owner_pid <= 0 and age_seconds <= ENTRY_LOCK_STALE_SECONDS:
            return False
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _evidence_for_invocation(invocation_key: str, workspace: Path, request_text: str) -> dict[str, Any]:
    cached = _load_cached_evidence(invocation_key, workspace)
    if cached is not None:
        return cached
    cache_path = _entry_cache_path(invocation_key)
    lock_path = cache_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd: int | None = None
    owner_token = uuid.uuid4().hex
    deadline = time.monotonic() + ENTRY_LOCK_WAIT_SECONDS
    while time.monotonic() < deadline:
        try:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            owner = json.dumps(
                {"pid": os.getpid(), "owner_token": owner_token, "created_at": datetime.now().astimezone().isoformat()},
                separators=(",", ":"),
            ).encode("utf-8")
            os.write(lock_fd, owner)
            os.fsync(lock_fd)
            break
        except FileExistsError:
            if _remove_abandoned_entry_lock(lock_path):
                continue
            time.sleep(0.05)
            cached = _load_cached_evidence(invocation_key, workspace)
            if cached is not None:
                return cached
    if lock_fd is None:
        raise EvidenceBootstrapError("timed out waiting for the entry-run cache lock")
    os.close(lock_fd)
    try:
        cached = _load_cached_evidence(invocation_key, workspace)
        if cached is not None:
            return cached
        pack = build_evidence_pack(workspace, request_text)
        _cache_evidence(invocation_key, pack)
        return pack
    finally:
        try:
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            if owner.get("owner_token") == owner_token:
                lock_path.unlink(missing_ok=True)
        except (OSError, AttributeError, json.JSONDecodeError):
            pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    integrity_ok, integrity_reason = verify_integrity()
    if not integrity_ok:
        print(json.dumps({"injectSteps": [{"ephemeralMessage": f"DEEP DEV LOCKED: {integrity_reason}"}]}, ensure_ascii=False))
        return 0
    transcript_explicit, invocation_key, request_text = _latest_user_context(payload)
    payload_explicit = any("/deep-dev" in text.lower() for text in _strings(payload))
    explicit = payload_explicit or transcript_explicit
    if explicit:
        if invocation_key is None:
            invocation_key = "payload:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        if not request_text:
            request_text = next((text for text in _strings(payload) if "/deep-dev" in text.lower()), "")
        try:
            evidence_pack = _evidence_for_invocation(invocation_key, _workspace_root(payload), request_text)
            evidence_message = format_evidence_for_injection(evidence_pack)
        except EvidenceBootstrapError as exc:
            print(json.dumps({"injectSteps": [{"ephemeralMessage": f"DEEP DEV STARTING: evidence bootstrap is not ready yet ({exc}). No ticket was issued for this turn; continue normally, or invoke /deep-dev again after the service is ready."}]}, ensure_ascii=False))
            return 0
        token = issue_ticket_once(invocation_key)
        if token:
            scope_prefix = _command_prefix("deep_dev_scope.py")
            message = evidence_message + "\n\n" + (
                "DEEP DEV ENFORCEMENT IS ACTIVE. Entry ticket: " + token + ". Your FIRST assistant action MUST be one or more "
                "read-only discovery tool calls. Do not answer in prose, ask for confirmation, estimate project-specific values, "
                "or state project facts before fresh evidence from this invocation. Analysis-only requests still require discovery. "
                "For a mutation request, finish complete structured file operations before exchanging the ticket. Scope only the "
                "smallest paths actually mutated; files read for context are not targets. Use action=exact_replace with exact unique "
                "old_text/new_text replacements for large existing files instead of emitting their full contents. Never stop at a plan, "
                "split the task into another user invocation, or ask for /deep-dev again because of output-token limits. "
                "Immediately before execution run the exact "
                "scope command prefix shown here, followed by this --entry-ticket, --workspace-root, every --target-path, and --config-path .deep_dev/config.json: "
                + scope_prefix + ". Submit it as one direct command line: do not prepend &, and do not use backticks, newlines, "
                "pipes, shell variables, or chaining. Immediately then call the declared direct MCP tool " + SERVER_NAME + "/" + TOOL_NAME
                + " with matching task/workspace/targets/config, scope-bound capability, and structured operations as direct "
                "arguments. Do not inspect MCP configuration, search transport code, or look for call_mcp_tool; it is not this skill's "
                "execution route. There is no deep_dev_harness.py command; never invent or call one. Do not call "
                "a second Gemini API or use direct mutation tools. Perform fresh file/graph discovery before "
                "any final answer and never claim exhaustive review or test success without evidence from this invocation. If the "
                "canonical tool returns a repair object after ROLLBACK, use only its returned repair ticket with the same task and "
                "scope: for incomplete_proposal include every missing target; for test_failure read its test_results_artifact first. "
                "At most two repair revisions are allowed; never bypass a repair with direct mutation. "
                "Tickets expire in 10 minutes and are single-use."
            )
        else:
            message = evidence_message + "\n\n" + (
                "DEEP DEV ENFORCEMENT IS ACTIVE. The single entry ticket for this user invocation was already exchanged, "
                "consumed, or expired. Do not answer with unverified project facts. Continue only with fresh read-only evidence and "
                "its previously returned scope-bound ticket. If that ticket is still live, call execute_host_proposal next; do not "
                "rerun the scope command or ask for another invocation merely to continue planned implementation."
            )
    else:
        message = (
            "Deep Dev is available on demand. Invoke /deep-dev only when you want isolated worktree verification; "
            "ordinary requests remain in normal mode."
        )
    print(json.dumps({"injectSteps": [{"ephemeralMessage": message}]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
