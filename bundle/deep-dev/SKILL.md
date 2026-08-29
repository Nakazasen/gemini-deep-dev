---
name: deep-dev
description: Run the opt-in fail-closed Deep Dev workflow with required AgentMemory recall/save and self-healing Graphify analysis. Activate only when the user explicitly invokes /deep-dev.
---

# Deep Dev

Use the deterministic graph-feedback workflow only for an explicit `/deep-dev` request. Ordinary requests remain normal assistant work; Deep Dev must not create extra user commands or block them.

For every explicit `/deep-dev` invocation, the first assistant action must be one or more read-only discovery tool calls. Do not answer in prose, ask for confirmation, estimate project-specific values, or state project facts before collecting fresh evidence in that invocation. This applies even when the requested output is only analysis, advice, or a plan. Never describe a review as exhaustive, verified, tested, or safe unless the current run contains corresponding file/graph/test evidence. Clearly label any conclusion that current evidence cannot verify. Mutation requests must additionally complete the ticket, scope, and orchestrator workflow below.

Before the model receives an entry ticket, the entry hook must produce a persisted entry-run artifact containing real execution receipts: AgentMemory health plus smart-search recall, a fresh Graphify query or AST extraction, and a Harness evidence FSM ending in `READY`. Boolean `*_invoked` claims, cached graph reads without a Graphify engine operation, and schema-only Harness validation are not sufficient. If any receipt or the terminal Harness preflight state is missing, entry fails closed.

The PreInvocation hook reuses that exact persisted entry artifact for subsequent tools in the same explicit `/deep-dev` invocation. It must not rerun AgentMemory, Graphify, or the Harness entry FSM before every tool. A new explicit `/deep-dev` user event receives a new entry run.

## Mandatory entry contract

1. Start with read-only discovery. Inspect the codebase and Graphify graph to identify exact target files.
2. If the target boundary is ambiguous, stop and ask the user to confirm it.
3. Produce complete structured `proposed_file_operations` from the signed-in host model, with full replacement content and no placeholders. Use the short host shape for every file: `{ "file_path": "relative/path", "action": "write", "content": "full file text" }`. Deep Dev deterministically converts `write` to `create` or `modify` against the sealed snapshot; never make that decision yourself.
4. Immediately before execution, exchange the injected bootstrap ticket for a scope-bound ticket using `deep_dev_scope.py`, with the workspace, every explicit target path, and `--config-path .deep_dev/config.json`.
5. Immediately after the scope exchange, call the declared direct MCP tool `deep_dev_harness/execute_host_proposal`; do not inspect MCP configuration, search transport code, or look for `call_mcp_tool`. Pass the exact task, workspace, target set, config path, returned capability ticket, and structured operations as direct arguments. Omit `run_id`: Deep Dev creates it safely for every proposal and revision. `call_mcp_tool` is retained only as gate-level compatibility for old sessions, not as an execution route for this skill. Never invent another route or a `deep_dev_harness.py` terminal script. Do not use direct write, edit, patch, delete, copy, move, or arbitrary terminal mutation tools.
6. Treat `BLOCKED`, `STOP`, and `ROLLBACK` as failures. Never bypass a failure by switching tools.
7. Accept output only when the terminal state is `ACCEPT_PATCH`, graph diff is safe, configured allowlist tests pass, and the verified patch has been applied to the main workspace.
8. Require the trajectory graph to report `acceptance_ready=true`; never infer success from the final text alone.

## Required integrations

- **AgentMemory:** Check REST health, auto-start an installed service when needed, require smart-search recall before generation, and require verified lesson persistence before `ACCEPT_PATCH`. Stop only if auto-start or a required operation actually fails.
- **Graphify:** Install the `graphifyy` package when missing, refresh the Antigravity integration, update a missing or stale graph, and verify freshness. Use the bounded AST scanner only after a real install/update/verification error, and report degraded graph status.

The entry hook injects the exact, machine-local command prefix for `deep_dev_scope.py`. Copy that prefix exactly and append the required arguments on one command line, including `--config-path .deep_dev/config.json`. Never infer a username or Python location. Do not prepend PowerShell `&`; do not use backticks, newlines, pipes, shell variables, command substitution, or chaining. Read the scope helper's JSON output and pass its returned ticket directly to `execute_host_proposal` with the identical workspace, target set, and config path. Never reveal the ticket in prose.

The `/deep-dev` entry hook injects a short-lived bootstrap ticket. Internally exchange it once through the exact installed `deep_dev_scope.py`; users still run only `/deep-dev`. The returned ticket is bound to the normalized workspace, targets, and config, and is consumed once inside the exact MCP host-proposal tool. Deep Dev generates run IDs itself. During an explicit Deep Dev invocation, the global `PreToolUse` hook permits allowlisted read-only tools, the exact scope command, and a structurally valid host-proposal call. Unknown tools fail closed. It denies missing/reused/expired or scope-mismatched tickets, direct mutation, unsafe read-command options, traversal, masquerading, non-allowlisted terminal commands, and chaining.

When `execute_host_proposal` returns a `repair` object after `ROLLBACK`, its `repair.next_tool` is authoritative: your next tool call must be direct MCP `deep_dev_harness/execute_host_proposal`, copying `repair.next_tool.arguments` exactly and adding only corrected `proposed_file_operations`. Do not call `call_mcp_tool`. For `reason=incomplete_proposal`, include a non-noop operation for every path in `evidence.missing_targets`; for `reason=test_failure`, read `evidence.test_results_artifact` first; for `reason=proposal_schema`, use exactly the short `file_path`/`action: write`/`content` shape above. There are at most two revisions total after the initial proposal, shared by both failure types; never use direct mutation tools or change task/scope during revision.

## Adaptive Teamwork repair

Use `/teamwork-preview` only when `repair.teamwork_preview` is present, or when the task has a security, persistence, schema/migration, public API, or multi-file contract change. Do not use it for a one-file copy change: Flash should remain fast there.

For a repair, invoke the small advisory squad specified by `repair.teamwork_preview` **before** spending the repair ticket. The team must work read-only and return one handoff containing: root cause tied to the evidence, smallest safe fix, scope check, and one owner for the final complete operations. Explorer/debugger and reviewer/challenger may disagree; the proposal integrator resolves that disagreement into exactly one proposal. The parent is the only executor: no teammate may call `execute_host_proposal`, issue a scope command, mutate the workspace, or claim tests passed. Deep Dev remains the independent test/apply authority. If Teamwork is unavailable, state `teamwork_degraded` and continue with the same bounded repair contract; never fabricate a team verdict.

## Verified workflow

1. **Preflight:** Auto-start and verify AgentMemory, self-heal Graphify, fingerprint the exact Git working state (clean or dirty), lock `.deep_dev/config.json`, and resolve impact paths.
2. **Snapshot:** Capture SHA-256 hashes for every allowed path.
3. **Host coder + deterministic critic:** Gemini Flash generates structured operations in memory; Deep Dev validates paths, hashes, schema, dependency boundaries, and tests without requiring a second Gemini API key.
4. **Isolation and verification:** Mirror the user's exact current baseline into an external Git worktree, apply the proposal there, require a safe dependency `graph_diff.json`, run allowlisted tests, verify cleanup and an unchanged baseline, persist evidence, then apply only the verified delta to the main workspace before `ACCEPT_PATCH`.

Progress is recorded without screenshots: each run writes append-only `events.jsonl`, liveness timestamps, and a compact per-project health summary under `%LOCALAPPDATA%\deep-dev`.
The run also writes `trajectory.json`, evaluates workflow-phase precision/recall/order (not semantic code quality), filters stale or contradictory recalled memory while preserving required AgentMemory metadata, permits one bounded retry only for transient/schema harness failures, opens a 15-minute circuit after three project failures, and attempts recovery only after AgentMemory, Graphify, and stable-Git-baseline probes pass. After applying a verified patch to the main workspace, require a Graphify refresh attempt and record its exact success or degraded result before `ACCEPT_PATCH`.

## Handover

Report:

- Run ID and terminal state.
- Target paths and hash changes.
- Test count, pass/fail result, and duration.
- Generated patch path.
- Any degraded Graphify or memory status.
- AgentMemory health, recall, and save evidence. A successful run must never report degraded memory.
- Trajectory precision, recall, order, score, and `acceptance_ready`.
