"""Canonical MCP simulations for common Gemini Flash Deep Dev mistakes.

These tests use no model and no direct workspace mutations.  They submit the
same structured proposals a host model would submit and assert the harness's
real terminal states, repair tickets, isolation, tests, and final application.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from capability import exchange_ticket, issue_ticket
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import golden_e2e


TASK = "Create the required mini-todo CLI, unittest suite, and README."
TARGETS = ["todo.py", "test_todo.py", "README.md"]


def _registry_params() -> StdioServerParameters:
    registry = json.loads(golden_e2e.CONFIG.read_text(encoding="utf-8"))
    entry = registry["mcpServers"]["deep_dev_harness"]
    assert entry["args"] == ["-m", "custom_harness.mcp_server"]
    assert Path(entry["cwd"]).resolve() == golden_e2e.HARNESS.resolve()
    env = os.environ.copy()
    env.update(entry["env"])
    return StdioServerParameters(command=entry["command"], args=entry["args"], cwd=entry["cwd"], env=env)


async def _call(request: dict[str, Any]) -> dict[str, Any]:
    async with stdio_client(_registry_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "execute_host_proposal" in {tool.name for tool in tools.tools}
            response = await session.call_tool("execute_host_proposal", request)
    return json.loads(response.content[0].text)


def _request(workspace: Path, config_path: Path, ticket: str, operations: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "task": TASK,
        "workspace_root": str(workspace),
        "target_paths": TARGETS,
        "config_path": str(config_path),
        "capability_ticket": ticket,
        "proposed_file_operations": operations,
    }


def _scope_ticket_via_cli(workspace: Path) -> str:
    """Use the installed scope command exactly as Flash is instructed to use it."""
    scope_script = Path(__file__).with_name("deep_dev_scope.py")
    command = [
        sys.executable, str(scope_script), "--entry-ticket", issue_ticket(),
        "--workspace-root", str(workspace),
    ]
    for target in TARGETS:
        command.extend(["--target-path", target])
    # Deliberately omit --config-path: this is the Flash failure we repaired.
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    payload = json.loads(result.stdout)
    assert payload["success"] is True, payload
    return str(payload["capability_ticket"])


def _trajectory(run_id: str) -> dict[str, Any]:
    runtime = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".deep-dev")) / "deep-dev" / "runs"
    matches = list(runtime.glob(f"*/{run_id}/trajectory.json"))
    assert len(matches) == 1, f"Missing trajectory for {run_id}: {matches}"
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _test_results(run_id: str) -> dict[str, Any]:
    runtime = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".deep-dev")) / "deep-dev" / "runs"
    matches = list(runtime.glob(f"*/{run_id}/test_results.json"))
    assert len(matches) == 1, f"Missing test results for {run_id}: {matches}"
    return json.loads(matches[0].read_text(encoding="utf-8"))


def _assert_accepted(response: dict[str, Any], workspace: Path) -> None:
    assert response["success"] is True, response
    assert response["terminal_state"] == "ACCEPT_PATCH", response
    assert response["all_tests_passed"] is True, response
    assert response["degraded_memory"] is False, response
    assert response["degraded_graph"] is False, response
    assert all((workspace / target).is_file() for target in TARGETS)
    # Prove the main-workspace bytes equal the proposal that passed in the
    # worktree; existence alone would miss an empty or stale final apply.
    for operation in golden_e2e._operations():
        expected = operation["content"].replace("\r\n", "\n")
        if expected and not expected.endswith("\n"):
            expected += "\n"
        actual = (workspace / operation["file_path"]).read_text(encoding="utf-8")
        assert actual == expected, f"Main apply content mismatch: {operation['file_path']}"
    assert not (workspace / "todos.json").exists()
    results = _test_results(response["run_id"])
    assert results["all_passed"] is True and results["total_commands"] == 2, results
    assert results["total_test_cases"] == 8, results
    unit = next(result for result in results["results"] if result["command_id"] == "unit")
    assert "Ran 4 tests" in f"{unit['stdout']}\n{unit['stderr']}", unit
    trajectory = _trajectory(response["run_id"])
    phases = {node["phase"] for node in trajectory["nodes"]}
    assert {"MEMORY_RECALL", "GRAPH_DIFF", "TEST_EXECUTE", "MEMORY_SAVE", "MAIN_APPLY", "GRAPH_REFRESH", "ACCEPT_PATCH"} <= phases, phases
    evaluation = trajectory["evaluation"]
    assert evaluation["acceptance_ready"] is True, evaluation
    assert evaluation["in_order"] is True, evaluation


def _scenario_omitted_scope_config() -> str:
    with tempfile.TemporaryDirectory(prefix="flash-sim-omitted-config-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        # Simulates Flash omitting --config-path in deep_dev_scope.py, while
        # the canonical MCP call supplies the normal config path.
        ticket = _scope_ticket_via_cli(workspace)
        response = asyncio.run(_call(_request(workspace, config, ticket, golden_e2e._operations())))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_workspace_relative_config_path() -> str:
    """Relative MCP config paths must bind to the proposal workspace, not server cwd."""
    with tempfile.TemporaryDirectory(prefix="flash-sim-relative-config-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, None)
        assert ok, ticket
        request = _request(workspace, config, ticket, golden_e2e._operations())
        request["config_path"] = ".deep_dev/config.json"
        response = asyncio.run(_call(request))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_incomplete_then_repair() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="flash-sim-incomplete-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, None)
        assert ok, ticket
        incomplete = [golden_e2e._operations()[0]]
        failed = asyncio.run(_call(_request(workspace, config, ticket, incomplete)))
        assert failed["terminal_state"] == "ROLLBACK", failed
        assert not any((workspace / target).exists() for target in TARGETS)
        repair = failed.get("repair")
        assert repair and repair["reason"] == "incomplete_proposal", failed
        assert sorted(repair["evidence"]["missing_targets"]) == ["README.md", "test_todo.py"], repair
        next_tool = repair["next_tool"]
        assert next_tool["server_name"] == "deep_dev_harness" and next_tool["tool_name"] == "execute_host_proposal"
        teamwork = repair["teamwork_preview"]
        assert teamwork["recommended"] is True and teamwork["mode"] == "repair_advisory", teamwork
        assert teamwork["roles"] == ["debugger", "contract_reviewer", "proposal_integrator"], teamwork
        repaired_request = dict(next_tool["arguments"])
        repaired_request["proposed_file_operations"] = golden_e2e._operations()
        repaired = asyncio.run(_call(repaired_request))
        _assert_accepted(repaired, workspace)
        return failed["run_id"], repaired["run_id"]


def _scenario_test_failure_then_repair() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="flash-sim-test-failure-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, None)
        assert ok, ticket
        faulty = golden_e2e._operations()
        faulty[0] = dict(faulty[0])
        faulty[0]["content"] = faulty[0]["content"].replace('"description": description', '"task": description')
        failed = asyncio.run(_call(_request(workspace, config, ticket, faulty)))
        assert failed["terminal_state"] == "ROLLBACK", failed
        assert not any((workspace / target).exists() for target in TARGETS)
        repair = failed.get("repair")
        assert repair and repair["reason"] == "test_failure", failed
        artifact = Path(repair["evidence"]["test_results_artifact"])
        assert artifact.is_file(), repair
        next_tool = repair["next_tool"]
        assert next_tool["server_name"] == "deep_dev_harness" and next_tool["tool_name"] == "execute_host_proposal"
        teamwork = repair["teamwork_preview"]
        assert teamwork["recommended"] is True and "root_cause" in teamwork["required_handoff"], teamwork
        repaired_request = dict(next_tool["arguments"])
        repaired_request["proposed_file_operations"] = golden_e2e._operations()
        repaired = asyncio.run(_call(repaired_request))
        _assert_accepted(repaired, workspace)
        return failed["run_id"], repaired["run_id"]


def _scenario_scope_mismatch_preserves_ticket() -> str:
    with tempfile.TemporaryDirectory(prefix="flash-sim-scope-mismatch-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        other_config = workspace / ".deep_dev" / "other.json"
        other_config.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, None)
        assert ok, ticket
        mismatched = _request(workspace, other_config, ticket, golden_e2e._operations())
        blocked = asyncio.run(_call(mismatched))
        assert blocked["terminal_state"] == "BLOCKED", blocked
        # A rejected scope must not consume the ticket; the same ticket remains
        # usable for the originally bound scope.
        accepted = asyncio.run(_call(_request(workspace, config, ticket, golden_e2e._operations())))
        _assert_accepted(accepted, workspace)
        return accepted["run_id"]


def _scenario_legacy_content_field() -> str:
    with tempfile.TemporaryDirectory(prefix="flash-sim-legacy-content-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, None)
        assert ok, ticket
        legacy = []
        for operation in golden_e2e._operations():
            clone = dict(operation)
            clone["content_or_diff"] = clone.pop("content")
            legacy.append(clone)
        response = asyncio.run(_call(_request(workspace, config, ticket, legacy)))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_existing_targets_main_apply() -> str:
    """Normalize Flash's write wording against existing snapshot-bound files."""
    with tempfile.TemporaryDirectory(prefix="flash-sim-existing-targets-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        for target in TARGETS:
            (workspace / target).write_text("stale fixture\n", encoding="utf-8")
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, config)
        assert ok, ticket
        # This is the exact action wording emitted by Flash in the real run.
        operations = [dict(operation, action="write") for operation in golden_e2e._operations()]
        response = asyncio.run(_call(_request(workspace, config, ticket, operations)))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_empty_placeholder() -> str:
    """Ignore Flash's harmless empty placeholder forms."""
    with tempfile.TemporaryDirectory(prefix="flash-sim-empty-noop-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, config)
        assert ok, ticket
        operations = [
            *golden_e2e._operations(),
            {"file_path": "", "action": "noop", "content": ""},
            {},
        ]
        response = asyncio.run(_call(_request(workspace, config, ticket, operations)))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_operation_aliases() -> str:
    """Accept Flash's common path/operation/new_content aliases safely."""
    with tempfile.TemporaryDirectory(prefix="flash-sim-operation-aliases-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, config)
        assert ok, ticket
        operations = [
            {"path": operation["file_path"], "operation": "write", "new_content": operation["content"]}
            for operation in golden_e2e._operations()
        ]
        response = asyncio.run(_call(_request(workspace, config, ticket, operations)))
        _assert_accepted(response, workspace)
        return response["run_id"]


def _scenario_native_harness_shape() -> str:
    """The host's native target_path/write objects need no model-side rewrite."""
    with tempfile.TemporaryDirectory(prefix="flash-sim-native-shape-") as raw:
        workspace = Path(raw)
        config = golden_e2e._prepare_workspace(workspace)
        ok, ticket = exchange_ticket(issue_ticket(), workspace, TARGETS, config)
        assert ok, ticket
        operations = [
            {"target_path": operation["file_path"], "action": "write", "content": operation["content"]}
            for operation in golden_e2e._operations()
        ]
        response = asyncio.run(_call(_request(workspace, config, ticket, operations)))
        _assert_accepted(response, workspace)
        return response["run_id"]


def main() -> int:
    omitted = _scenario_omitted_scope_config()
    relative_config = _scenario_workspace_relative_config_path()
    incomplete, incomplete_repaired = _scenario_incomplete_then_repair()
    failed, failure_repaired = _scenario_test_failure_then_repair()
    mismatch = _scenario_scope_mismatch_preserves_ticket()
    legacy = _scenario_legacy_content_field()
    existing_targets = _scenario_existing_targets_main_apply()
    empty_noop = _scenario_empty_placeholder()
    operation_aliases = _scenario_operation_aliases()
    native_shape = _scenario_native_harness_shape()
    print(json.dumps({
        "flash_e2e_simulator": "passed",
        "omitted_scope_config": omitted,
        "workspace_relative_config_path": relative_config,
        "incomplete_then_repair": [incomplete, incomplete_repaired],
        "test_failure_then_repair": [failed, failure_repaired],
        "scope_mismatch_preserves_ticket": mismatch,
        "legacy_content_field": legacy,
        "existing_targets_main_apply": existing_targets,
        "empty_noop_placeholder": empty_noop,
        "operation_aliases": operation_aliases,
        "native_harness_shape": native_shape,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
