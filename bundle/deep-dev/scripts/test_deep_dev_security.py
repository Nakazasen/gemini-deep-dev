from __future__ import annotations

import json
import io
import inspect
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from capability import exchange_ticket, issue_ticket, issue_ticket_once, normalize_scope
from deep_dev_gate import _audit_denial, decide
from mcp_contract import CONFIG_ALIAS, DIRECT_TOOL, SERVER_NAME, TOOL_NAME, WRAPPER_TOOL
from state_store import DeepDevState, RunState
from trajectory import EXPECTED_TRAJECTORY, TrajectoryRecorder
from graph_diff import capture as capture_graph, compare as compare_graph
from memory_hygiene import review as review_memory
from memory_adapter import AgentMemoryRESTBackend
from preflight import PreflightChecker
from isolation_manager import IsolationManager
from graph_freshness import GraphFreshnessChecker
from deep_dev_reminder import (
    _command_prefix,
    _evidence_for_invocation,
    _latest_user_request,
    _latest_user_requested_deep_dev,
    _workspace_root,
    main as reminder_main,
)
from evidence_bootstrap import (
    EvidenceBootstrapError,
    GENERIC_MEMORY_TITLES,
    _graphify_evidence,
    _project_aliases,
    format_evidence_for_injection,
)
from custom_harness.evidence import validate_evidence_pack


def payload(name: str, command: str | None = None) -> dict:
    args = {} if command is None else {"command": command}
    return {"toolCall": {"name": name, "args": args}}


def quote(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts)


def test_known_and_unknown_mutators_are_denied() -> None:
    assert decide(payload("write_to_file"), False)["decision"] == "deny"
    assert decide(payload("filesystem_put"), False)["decision"] == "deny"
    assert decide(payload("unclassified_capability"), False)["decision"] == "deny"


def test_normal_requests_are_not_forced_through_deep_dev(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gemini_root = tmp_path / ".gemini"
    gemini_root.mkdir()
    transcript = gemini_root / "normal.jsonl"
    transcript.write_text(json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "Update the README title."}) + "\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    request = payload("write_to_file")
    request["transcriptPath"] = str(transcript)
    assert decide(request, False)["decision"] == "allow"
    transcript.write_text(json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/deep-dev\nUpdate the README title."}) + "\n", encoding="utf-8")
    assert decide(request, False)["decision"] == "deny"


def test_model_run_id_is_not_part_of_capability_scope(tmp_path: Path) -> None:
    scope = normalize_scope(tmp_path, ["target.py"], None, "model-invented-run")
    assert scope["run_id"] is None


def test_explicit_read_only_tools_are_allowed() -> None:
    assert decide(payload("read_file"), False)["decision"] == "allow"
    assert decide(payload("list_directory"), False)["decision"] == "allow"
    assert decide(payload("grep_search"), False)["decision"] == "allow"


def test_non_mutating_interaction_tools_are_allowed_exactly() -> None:
    for name in ("ask_question", "request_user_input", "notify_user", "update_plan"):
        assert decide(payload(name), False)["decision"] == "allow"
    assert decide(payload("ask_question_and_write"), False)["decision"] == "deny"
    assert decide(payload("browser_subagent"), False)["decision"] == "deny"
    assert decide(payload("mcp_tool"), False)["decision"] == "deny"
    assert decide(payload("generate_image"), False)["decision"] == "deny"


def test_synthetic_direct_host_proposal_name_is_denied() -> None:
    synthetic = {
        "toolCall": {
            "name": "mcp_deep-dev-harness_execute_host_proposal",
            "args": {
                "task": "repair settings",
                "workspace_root": r"D:\workspace",
                "target_paths": ["services/settings_service.py"],
                "capability_ticket": "A" * 43,
                "proposed_file_operations": [
                    {"file_path": "services/settings_service.py", "action": "modify", "content_or_diff": "x = 1\n"}
                ],
            },
        }
    }
    assert decide(synthetic, False)["decision"] == "deny"
    synthetic["toolCall"]["name"] = "execute_host_proposal"
    assert decide(synthetic, False)["decision"] == "allow"


def test_antigravity_direct_mcp_allows_only_verified_host_proposal() -> None:
    proposal = {
        "task": "repair settings",
        "workspace_root": r"D:\workspace",
        "target_paths": ["services/settings_service.py"],
        "capability_ticket": "A" * 43,
        "proposed_file_operations": [
            {"file_path": "services/settings_service.py", "action": "modify", "content_or_diff": "x = 1\n"}
        ],
    }
    direct = {
        "toolCall": {
            "name": DIRECT_TOOL,
            "args": proposal,
        }
    }
    assert decide(direct, False)["decision"] == "allow"
    assert CONFIG_ALIAS == SERVER_NAME == "deep_dev_harness"
    wrapped = {
        "toolCall": {
            "name": WRAPPER_TOOL,
            "args": {"ServerName": SERVER_NAME, "ToolName": TOOL_NAME, "Arguments": proposal},
        }
    }
    assert decide(wrapped, False)["decision"] == "allow"
    wrapped["toolCall"]["args"]["ToolName"] = "other_tool"
    assert decide(wrapped, False)["decision"] == "deny"
    direct["toolCall"]["args"].pop("proposed_file_operations")
    assert decide(direct, False)["decision"] == "deny"


def test_terminal_read_only_is_narrow() -> None:
    assert decide(payload("run_command", "git status"), False)["decision"] == "allow"
    assert decide(payload("run_command", "rg needle ."), False)["decision"] == "allow"
    assert decide(payload("run_command", "rg --pre evil.exe needle ."), False)["decision"] == "deny"
    assert decide(payload("run_command", "graphify query x --save-result out.json"), False)["decision"] == "deny"


def test_masquerading_orchestrator_is_denied(tmp_path: Path) -> None:
    fake = quote([
        sys.executable,
        "-c",
        "__import__('pathlib').Path('probe').write_text('x')",
        str(Path(__file__).with_name("deep_orchestrator.py")),
        "task",
        "--workspace-root",
        str(tmp_path),
        "--target-path",
        "safe.txt",
    ])
    assert decide(payload("run_command", fake), False)["decision"] == "deny"


def test_direct_orchestrator_is_denied_even_with_scoped_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    ok, ticket = exchange_ticket(issue_ticket(), tmp_path, ["safe.txt"])
    assert ok is True
    command = quote([
        sys.executable,
        str(Path(__file__).with_name("deep_orchestrator.py")),
        "task",
        "--workspace-root",
        str(tmp_path),
        "--target-path",
        "safe.txt",
        "--capability-ticket",
        ticket,
        "--json",
    ])
    assert decide(payload("run_command", command), False)["decision"] == "deny"
    ok, escaped_ticket = exchange_ticket(issue_ticket(), tmp_path, ["safe.txt"])
    assert ok is True
    escaped = command.replace("safe.txt", "..\\escaped.txt").replace(ticket, escaped_ticket)
    assert decide(payload("run_command", escaped), False)["decision"] == "deny"


def test_scope_ticket_cannot_change_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    ok, ticket = exchange_ticket(issue_ticket(), tmp_path, ["a.py"])
    assert ok is True
    command = quote([
        sys.executable, str(Path(__file__).with_name("deep_orchestrator.py")), "task",
        "--workspace-root", str(tmp_path), "--target-path", "b.py",
        "--capability-ticket", ticket, "--json",
    ])
    assert decide(payload("run_command", command), False)["decision"] == "deny"


def test_graph_diff_denies_new_dependency_outside_scope(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("VALUE = 2\n", encoding="utf-8")
    baseline = capture_graph(tmp_path)
    (tmp_path / "a.py").write_text("import b\nVALUE = b.VALUE\n", encoding="utf-8")
    report = compare_graph(baseline, capture_graph(tmp_path), [tmp_path / "a.py"], tmp_path)
    assert report["safe"] is False
    assert report["violations"] == [["a.py", "b.py"]]


def test_memory_hygiene_quarantines_conflicts_and_stale_items(tmp_path: Path) -> None:
    quarantine = tmp_path / "quarantine.jsonl"
    items = [
        {"key": "formatter", "value": "black", "confidence": 0.9},
        {"key": "formatter", "value": "ruff", "confidence": 0.9},
        {"content": "old", "expires_at": "2000-01-01T00:00:00+00:00"},
        {"content": "valid", "confidence": 0.9},
    ]
    accepted, report = review_memory(items, tmp_path, quarantine)
    assert accepted == [{"content": "valid", "confidence": 0.9}]
    assert report["quarantined"] == 3
    assert quarantine.is_file()


def test_circuit_can_reset_after_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    project_id = "recovery_project"
    health = tmp_path / "local" / "deep-dev" / "health" / f"{project_id}.json"
    health.parent.mkdir(parents=True)
    health.write_text(json.dumps({
        "project_id": project_id,
        "consecutive_failures": 3,
        "last_run_at": "2099-01-01T00:00:00+00:00",
    }), encoding="utf-8")
    assert RunState.circuit_status(project_id)[0] is True
    RunState.reset_circuit(project_id, "fault injection recovery passed")
    assert RunState.circuit_status(project_id)[0] is False


def test_atomic_state_failure_preserves_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    state = RunState.create("atomic_project", "probe", run_id="atomic_run")
    original = state.get_state_file().read_bytes()
    monkeypatch.setattr("state_store.os.replace", lambda *_: (_ for _ in ()).throw(OSError("injected")))
    state.task = "changed"
    with pytest.raises(OSError, match="injected"):
        state.save()
    assert state.get_state_file().read_bytes() == original


def test_reminder_reads_latest_explicit_request_from_trusted_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    transcript = fake_home / ".gemini" / "antigravity-ide" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/deep-dev fix it"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deep_dev_reminder.Path.home", lambda: fake_home)
    assert _latest_user_requested_deep_dev({"transcriptPath": str(transcript)}) is True
    transcript.write_text(
        json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "/deep-dev fix it"}) + "\n" +
        json.dumps({"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "status?"}) + "\n",
        encoding="utf-8",
    )
    assert _latest_user_requested_deep_dev({"transcriptPath": str(transcript)}) is False


def test_entry_evidence_is_built_once_per_explicit_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    calls = 0

    def fake_build(root: Path, request_text: str) -> dict:
        nonlocal calls
        calls += 1
        run_id = f"entry_{calls}"
        artifact = local / "deep-dev" / "entry-runs" / "project" / f"{run_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        pack = {
            "workspace_root": str(root.resolve()),
            "request_text": request_text,
            "evidence_sha256": f"{calls:064x}",
            "entry_run_id": run_id,
            "entry_artifact": str(artifact),
            "entry_trajectory": ["INIT", "VERIFY_AGENTMEMORY", "VERIFY_GRAPHIFY", "SEAL_EVIDENCE", "READY"],
            "harness_status": "preflight_passed",
        }
        artifact.write_text(json.dumps(pack), encoding="utf-8")
        return pack

    monkeypatch.setattr("deep_dev_reminder.build_evidence_pack", fake_build)
    first = _evidence_for_invocation("transcript:step-7", workspace, "/deep-dev fix")
    second = _evidence_for_invocation("transcript:step-7", workspace, "/deep-dev fix")
    third = _evidence_for_invocation("transcript:step-8", workspace, "/deep-dev fix another")

    assert calls == 2
    assert first["entry_run_id"] == second["entry_run_id"]
    assert third["entry_run_id"] != first["entry_run_id"]


def test_entry_evidence_recovers_a_stale_crash_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    invocation = "transcript:stale-lock"
    from deep_dev_reminder import _entry_cache_path

    lock_path = _entry_cache_path(invocation).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("crashed", encoding="utf-8")
    stale = time.time() - 601
    os.utime(lock_path, (stale, stale))

    def fake_build(root: Path, _request_text: str) -> dict:
        artifact = local / "deep-dev" / "entry-runs" / "project" / "entry_recovered.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        pack = {
            "workspace_root": str(root.resolve()),
            "evidence_sha256": "c" * 64,
            "entry_run_id": "entry_recovered",
            "entry_artifact": str(artifact),
            "entry_trajectory": ["INIT", "READY"],
            "harness_status": "preflight_passed",
        }
        artifact.write_text(json.dumps(pack), encoding="utf-8")
        return pack

    monkeypatch.setattr("deep_dev_reminder.build_evidence_pack", fake_build)
    recovered = _evidence_for_invocation(invocation, workspace, "/deep-dev recover")
    assert recovered["entry_run_id"] == "entry_recovered"
    assert not lock_path.exists()


def test_entry_evidence_recovers_a_fresh_lock_owned_by_dead_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "local"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    invocation = "transcript:dead-owner-lock"
    from deep_dev_reminder import _entry_cache_path

    lock_path = _entry_cache_path(invocation).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"pid": 2147483647, "owner_token": "dead"}), encoding="utf-8")

    def fake_build(root: Path, _request_text: str) -> dict:
        artifact = local / "deep-dev" / "entry-runs" / "project" / "entry_dead_owner_recovered.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        pack = {
            "workspace_root": str(root.resolve()),
            "evidence_sha256": "d" * 64,
            "entry_run_id": "entry_dead_owner_recovered",
            "entry_artifact": str(artifact),
            "entry_trajectory": ["INIT", "READY"],
            "harness_status": "preflight_passed",
        }
        artifact.write_text(json.dumps(pack), encoding="utf-8")
        return pack

    monkeypatch.setattr("deep_dev_reminder.build_evidence_pack", fake_build)
    recovered = _evidence_for_invocation(invocation, workspace, "/deep-dev recover dead owner")
    assert recovered["entry_run_id"] == "entry_dead_owner_recovered"
    assert not lock_path.exists()


def test_reminder_inherits_recent_deep_dev_for_followup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    transcript = fake_home / ".gemini" / "antigravity-ide" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    deep_event = {"step_index": 1, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "2026-08-27T07:00:00Z", "content": "/deep-dev fix it"}
    followup = {"step_index": 2, "source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": "2026-08-27T07:05:00Z", "content": "also limit DUKC to Form"}
    transcript.write_text(json.dumps(deep_event) + "\n" + json.dumps(followup) + "\n", encoding="utf-8")
    monkeypatch.setattr("deep_dev_reminder.Path.home", lambda: fake_home)
    active, followup_key = _latest_user_request({"transcriptPath": str(transcript)})
    assert active is True
    transcript.write_text(json.dumps(deep_event) + "\n", encoding="utf-8")
    explicit, explicit_key = _latest_user_request({"transcriptPath": str(transcript)})
    assert explicit is True
    assert followup_key == explicit_key
    followup["created_at"] = "2026-08-27T07:10:01Z"
    transcript.write_text(json.dumps(deep_event) + "\n" + json.dumps(followup) + "\n", encoding="utf-8")
    assert _latest_user_requested_deep_dev({"transcriptPath": str(transcript)}) is False


def test_entry_ticket_is_issued_once_per_user_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    first = issue_ticket_once("conversation:step:1")
    second = issue_ticket_once("conversation:step:1")
    assert first is not None
    assert second == first
    ok, _ = exchange_ticket(first, tmp_path, ["safe.txt"])
    assert ok is True
    assert issue_ticket_once("conversation:step:1") is None
    assert issue_ticket_once("conversation:step:2") not in {None, first}


def test_injected_command_prefixes_are_machine_local_and_shell_safe() -> None:
    for script in ("deep_dev_scope.py", "deep_orchestrator.py"):
        prefix = _command_prefix(script)
        expected_parts = [
            str(Path(sys.executable).resolve()),
            str(Path(__file__).with_name(script).resolve()),
        ]
        expected_prefix = subprocess.list2cmdline(expected_parts) if os.name == "nt" else shlex.join(expected_parts)
        assert prefix == expected_prefix
        # A local account may legitimately be named "Admin". Check the helper
        # implementation rather than rejecting a valid machine-local path.
        assert "C:\\Users\\Admin" not in inspect.getsource(_command_prefix)
        assert "&" not in prefix
        assert "`" not in prefix
        assert "\n" not in prefix


def test_reminder_requires_discovery_as_first_action_for_analysis_only_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_home = tmp_path / "home"
    transcript = fake_home / ".gemini" / "antigravity-ide" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({
            "step_index": 7,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-08-27T07:55:43Z",
            "content": "/deep-dev will fitting 120 rows look bad?",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deep_dev_reminder.Path.home", lambda: fake_home)
    monkeypatch.setattr("deep_dev_reminder.verify_integrity", lambda: (True, "OK"))
    monkeypatch.setattr("deep_dev_reminder.issue_ticket_once", lambda _key: "A" * 48)
    monkeypatch.setattr("deep_dev_reminder.build_evidence_pack", lambda *_: {
        "evidence_sha256": "b" * 64,
        "entry_run_id": "entry_20260827T102627Z_1234abcd",
        "entry_artifact": str(tmp_path / "entry.json"),
        "entry_trajectory": ["INIT", "VERIFY_AGENTMEMORY", "VERIFY_GRAPHIFY", "SEAL_EVIDENCE", "READY"],
        "project_id": "sample_12345678",
        "agentmemory_status": "ready",
        "graphify_status": "extracted_fresh_ast",
        "harness_status": "preflight_passed",
        "items": [
            {"source": "agentmemory", "content": "past decision", "provenance": "smart-search"},
            {"source": "graphify", "content": "current symbol", "provenance": "app.py:L1"},
        ],
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "transcriptPath": str(transcript),
        "workspacePaths": [str(tmp_path)],
    })))
    assert reminder_main() == 0
    message = json.loads(capsys.readouterr().out)["injectSteps"][0]["ephemeralMessage"]
    assert "DEEP DEV EVIDENCE BOOTSTRAP: READY" in message
    assert "AgentMemory: ready (1 recalled)" in message
    assert "Graphify: extracted_fresh_ast (1 evidence nodes)" in message
    assert "Harness: preflight_passed (INIT -> VERIFY_AGENTMEMORY -> VERIFY_GRAPHIFY -> SEAL_EVIDENCE -> READY)" in message
    assert "FIRST assistant action MUST be one or more read-only discovery tool calls" in message
    assert "Analysis-only requests still require discovery" in message
    assert "Do not answer in prose" in message
    assert "Scope only the smallest paths actually mutated" in message
    assert "action=exact_replace" in message
    assert "Never stop at a plan" in message
    assert "ask for /deep-dev again because of output-token limits" in message


def test_reminder_fails_closed_before_ticket_when_evidence_bootstrap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("deep_dev_reminder.verify_integrity", lambda: (True, "OK"))
    monkeypatch.setattr(
        "deep_dev_reminder.build_evidence_pack",
        lambda *_: (_ for _ in ()).throw(EvidenceBootstrapError("graph unavailable")),
    )
    ticket_called = False

    def unexpected_ticket(_key: str) -> str:
        nonlocal ticket_called
        ticket_called = True
        return "A" * 48

    monkeypatch.setattr("deep_dev_reminder.issue_ticket_once", unexpected_ticket)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "prompt": "/deep-dev inspect",
        "workspacePaths": [str(tmp_path)],
    })))
    assert reminder_main() == 0
    message = json.loads(capsys.readouterr().out)["injectSteps"][0]["ephemeralMessage"]
    assert message == "DEEP DEV STARTING: evidence bootstrap is not ready yet (graph unavailable). No ticket was issued for this turn; continue normally, or invoke /deep-dev again after the service is ready."
    assert ticket_called is False


def test_workspace_root_skips_stale_antigravity_paths(tmp_path: Path) -> None:
    valid = tmp_path / "current-workspace"
    valid.mkdir()
    payload = {
        "workspacePaths": [
            str(tmp_path / "stale-workspace"),
            {"path": str(valid)},
        ]
    }
    assert _workspace_root(payload) == valid.resolve()


def test_workspace_root_accepts_file_uri_and_top_level_fallback(tmp_path: Path) -> None:
    valid = tmp_path / "uri workspace"
    valid.mkdir()
    assert _workspace_root({"workspacePaths": [{"uri": valid.as_uri()}]}) == valid.resolve()
    assert _workspace_root({"workspacePaths": ["missing"], "cwd": str(valid)}) == valid.resolve()


def test_workspace_root_fails_closed_without_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(EvidenceBootstrapError, match="existing workspace root"):
        _workspace_root({"workspacePaths": [str(tmp_path / "missing")]})


def test_workspace_root_uses_trusted_active_document_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    project = tmp_path / "project with spaces"
    project.mkdir()
    (project / ".git").mkdir()
    active_document = project / "main.py"
    active_document.write_text("print('ready')\n", encoding="utf-8")
    transcript = fake_home / ".gemini" / "antigravity-ide" / "brain" / "transcript.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": (
                "<USER_REQUEST>\n/deep-dev fix\n</USER_REQUEST>\n"
                "<ADDITIONAL_METADATA>\n"
                f"Active Document: {active_document} (LANGUAGE_PYTHON)\n"
                "</ADDITIONAL_METADATA>"
            ),
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deep_dev_reminder.Path.home", lambda: fake_home)
    assert _workspace_root({"workspacePaths": [], "transcriptPath": str(transcript)}) == project.resolve()


def test_graphify_bootstrap_extracts_code_in_memory_without_writing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.py"
    source.write_text("def compare_form():\n    return 'Form'\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path.parent / "local-cache"))
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    status, items, receipt = _graphify_evidence(tmp_path, "compare Form")
    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert status == "extracted_fresh_ast"
    assert items and all(item["source"] == "graphify" for item in items)
    assert receipt["provider"] == "graphify"
    assert "extract" in receipt["operation"] and "query" in receipt["operation"]
    assert receipt["evidence_count"] == len(items)
    assert before == after


def test_graphify_runtime_output_defaults_outside_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "Dự án Unicode"
    workspace.mkdir()
    local = tmp_path / "local"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    output = GraphFreshnessChecker._graph_output_dir(workspace)
    assert output.is_relative_to(local / "deep-dev" / "graphify")
    assert not output.is_relative_to(workspace)


def test_evidence_injection_is_bounded_and_identifies_all_three_components() -> None:
    pack = {
        "evidence_sha256": "c" * 64,
        "entry_run_id": "entry_20260827T102627Z_1234abcd",
        "entry_artifact": r"C:\entry.json",
        "entry_trajectory": ["INIT", "VERIFY_AGENTMEMORY", "VERIFY_GRAPHIFY", "SEAL_EVIDENCE", "READY"],
        "project_id": "project_12345678",
        "agentmemory_status": "ready",
        "graphify_status": "queried_fresh_graph",
        "harness_status": "preflight_passed",
        "items": [
            {"source": "agentmemory", "content": "memory", "provenance": "recall"},
            {"source": "graphify", "content": "node", "provenance": "a.py:L1"},
        ],
    }
    message = format_evidence_for_injection(pack)
    assert "AgentMemory: ready" in message
    assert "Graphify: queried_fresh_graph" in message
    assert "Harness: preflight_passed" in message
    assert len(message) < 10_000


def _provider_receipt(provider: str, operation: str, count: int) -> dict:
    return {
        "provider": provider,
        "operation": operation,
        "status": "succeeded",
        "started_at": "2026-08-27T10:26:27Z",
        "completed_at": "2026-08-27T10:26:28Z",
        "duration_ms": 1000,
        "evidence_count": count,
        "output_sha256": "d" * 64,
    }


def test_harness_entry_fsm_requires_execution_receipts_not_invoked_booleans(tmp_path: Path) -> None:
    fake = {
        "schema_version": "1.1",
        "entry_run_id": "entry_20260827T102627Z_1234abcd",
        "workspace_root": str(tmp_path),
        "project_id": "project_12345678",
        "query": "inspect failure",
        "agentmemory_status": "ready",
        "graphify_status": "extracted_fresh_ast",
        "agentmemory_invoked": True,
        "graphify_invoked": True,
        "items": [
            {"source": "agentmemory", "content": "memory", "provenance": "recall"},
            {"source": "graphify", "content": "node", "provenance": "a.py:L1"},
        ],
    }
    with pytest.raises(Exception):
        validate_evidence_pack(fake)


def test_harness_entry_fsm_emits_terminal_receipt(tmp_path: Path) -> None:
    pack = {
        "schema_version": "1.1",
        "entry_run_id": "entry_20260827T102627Z_1234abcd",
        "workspace_root": str(tmp_path),
        "project_id": "project_12345678",
        "query": "inspect failure",
        "agentmemory_status": "ready",
        "graphify_status": "queried_fresh_graph",
        "provider_receipts": [
            _provider_receipt("agentmemory", "health_check+smart_search_recall", 1),
            _provider_receipt("graphify", "freshness_check+query", 1),
        ],
        "items": [
            {"source": "agentmemory", "content": "memory", "provenance": "recall"},
            {"source": "graphify", "content": "node", "provenance": "a.py:L1"},
        ],
    }
    result = validate_evidence_pack(pack)
    assert result["harness_status"] == "preflight_passed"
    assert result["entry_trajectory"][-1] == "READY"
    assert result["harness_receipt"]["operation"] == "entry_evidence_fsm"
    assert len(result["harness_receipt"]["receipt_sha256"]) == 64


def test_harness_accepts_healthy_empty_agentmemory_recall_for_a_new_project(tmp_path: Path) -> None:
    pack = {
        "schema_version": "1.1",
        "entry_run_id": "entry_20260828T053000Z_1234abcd",
        "workspace_root": str(tmp_path),
        "project_id": "new_project_12345678",
        "query": "inspect a new project",
        "agentmemory_status": "ready",
        "graphify_status": "extracted_fresh_ast",
        "provider_receipts": [
            _provider_receipt("agentmemory", "health_check+smart_search_recall", 0),
            _provider_receipt("graphify", "freshness_check+extract", 1),
        ],
        "items": [
            {"source": "graphify", "content": "probe.py:evidence_probe", "provenance": "probe.py:L1"},
        ],
    }
    result = validate_evidence_pack(pack)
    assert result["harness_status"] == "preflight_passed"
    assert result["entry_trajectory"][-1] == "READY"
    assert result["provider_receipts"][0]["evidence_count"] == 0


def test_project_aliases_bridge_versioned_workspace_and_memory_project(tmp_path: Path) -> None:
    workspace = tmp_path / "deep-dev-v1.6.0"
    workspace.mkdir()
    aliases = _project_aliases(workspace, "deep-dev-v1_6_0_deadbeef")
    assert aliases == ["deep-dev-v1_6_0_deadbeef", "deep-dev-v1.6.0", "deep-dev"]


def test_agentmemory_rest_accepts_compact_smart_search_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = AgentMemoryRESTBackend()
    monkeypatch.setattr(
        backend,
        "_request",
        lambda *_args, **_kwargs: {
            "mode": "compact",
            "results": [{"obsId": "obs-123", "title": "A prior architecture decision"}],
        },
    )
    assert backend.recall("project-id", "architecture", limit=1) == [
        {"source": "agentmemory:obs-123", "content": "A prior architecture decision"}
    ]


def test_agentmemory_rest_preserves_memory_hygiene_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = AgentMemoryRESTBackend()
    monkeypatch.setattr(
        backend,
        "_request",
        lambda *_args, **_kwargs: {
            "results": [{
                "obsId": "obs-456",
                "title": "Old decision",
                "confidence": 0.2,
                "expires_at": "2020-01-01T00:00:00Z",
                "key": "architecture",
                "value": "legacy",
                "files": ["removed.py"],
            }],
        },
    )
    recalled = backend.recall("project-id", "architecture", limit=1)
    assert recalled[0]["content"] == "Old decision"
    assert recalled[0]["confidence"] == 0.2
    assert recalled[0]["expires_at"] == "2020-01-01T00:00:00Z"
    assert recalled[0]["key"] == "architecture"
    assert recalled[0]["files"] == ["removed.py"]


def test_generic_tool_observations_are_marked_as_noise() -> None:
    assert {"bash", "apply_patch", "update_plan"} <= GENERIC_MEMORY_TITLES


def test_denial_audit_redacts_capability_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    secret = "A" * 48
    blocked = payload("run_command", f"python bad.py --entry-ticket {secret}")
    result = {"decision": "deny", "reason": "test denial"}
    _audit_denial(blocked, result)
    audit = (tmp_path / "deep-dev" / "gate-audit.jsonl").read_text(encoding="utf-8")
    assert secret not in audit
    assert "--entry-ticket [redacted]" in audit
    assert '"tool":"run_command"' in audit


def test_orchestrator_without_entry_ticket_is_denied(tmp_path: Path) -> None:
    command = quote([
        sys.executable,
        str(Path(__file__).with_name("deep_orchestrator.py")),
        "task",
        "--workspace-root",
        str(tmp_path),
        "--target-path",
        "safe.txt",
        "--json",
    ])
    assert decide(payload("run_command", command), False)["decision"] == "deny"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Deep Dev Test"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "deep-dev-test@localhost"], check=True)
    (path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "baseline"], check=True, capture_output=True)


def test_dirty_git_baseline_is_fingerprinted_and_detects_races(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "new.txt").write_text("first\n", encoding="utf-8")
    valid, head, first, clean, error = PreflightChecker.capture_git_baseline(repo)
    assert valid is True and head and first and error is None
    assert clean is False
    (repo / "new.txt").write_text("second\n", encoding="utf-8")
    valid, same_head, second, clean, error = PreflightChecker.capture_git_baseline(repo)
    assert valid is True and same_head == head and error is None
    assert clean is False and second != first


def test_dirty_baseline_is_mirrored_and_verified_delta_applies_to_main(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("preserve me\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    worktree = IsolationManager.create_worktree(repo, "sample", "mirror_run")
    mirrored, message = IsolationManager.mirror_workspace_baseline(repo, worktree)
    assert mirrored is True, message
    assert (worktree / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (worktree / "unrelated.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain=v1", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""
    from snapshot import WorkspaceSnapshot
    snapshot = WorkspaceSnapshot.capture(repo, ["app.py"], "mirror_run")
    applied, apply_message = IsolationManager.apply_verified_operations_to_main(
        repo,
        snapshot,
        [{"file_path": "app.py", "action": "modify", "content_or_diff": "VALUE = 3\n"}],
    )
    assert applied is True, apply_message
    assert (repo / "app.py").read_text(encoding="utf-8") == "VALUE = 3\n"
    assert (repo / "unrelated.txt").read_text(encoding="utf-8") == "preserve me\n"
    cleaned, cleanup_message = IsolationManager.cleanup_worktree(
        repo, worktree, "sample", "mirror_run", delete_branch=True
    )
    assert cleaned is True, cleanup_message


def test_progress_events_and_longitudinal_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    run = RunState.create(project_id="sample", task="verify telemetry", run_id="run_one")
    run.heartbeat("preflight alive")
    run.transition_to(DeepDevState.BLOCKED, error="synthetic test")

    events = [json.loads(line) for line in run.get_events_file().read_text(encoding="utf-8").splitlines()]
    event_names = [event["event"] for event in events]
    assert event_names.count("trajectory_step") == 2
    assert "run_created" in event_names
    assert "heartbeat" in event_names
    assert "state_transition" in event_names
    health = json.loads(run.get_health_file().read_text(encoding="utf-8"))
    assert health["total_runs"] == 1
    assert health["failed_runs"] == 1
    assert health["consecutive_failures"] == 1
    assert health["last_terminal_state"] == "BLOCKED"
    quarantine = run.get_quarantine_file().read_text(encoding="utf-8")
    assert '"promoted_to_memory":false' in quarantine
    trajectory = json.loads((run.get_run_dir() / "trajectory.json").read_text(encoding="utf-8"))
    assert trajectory["nodes"][0]["phase"] == "RUN_CREATED"
    assert trajectory["nodes"][-1]["phase"] == "BLOCKED"


def test_complete_trajectory_is_ordered_and_ready(tmp_path: Path) -> None:
    recorder = TrajectoryRecorder(tmp_path)
    evaluation = {}
    for phase in EXPECTED_TRAJECTORY[:-1]:
        evaluation = recorder.record(phase, evidence={"phase": phase})
    assert evaluation["ready_for_accept"] is True
    evaluation = recorder.record("ACCEPT_PATCH")
    assert evaluation["acceptance_ready"] is True
    assert evaluation["ready_for_accept"] is True
    assert evaluation["score"] == 1.0


def test_circuit_breaker_opens_after_three_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    for index in range(3):
        run = RunState.create(project_id="unstable", task="failure", run_id=f"run_{index}")
        run.transition_to(DeepDevState.BLOCKED, error="synthetic")
    opened, reason = RunState.circuit_status("unstable")
    assert opened is True
    assert "3 consecutive failures" in reason
