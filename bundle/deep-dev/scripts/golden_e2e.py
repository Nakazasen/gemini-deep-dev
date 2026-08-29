"""Live golden E2E verifier for the canonical Deep Dev MCP path.

This deliberately uses a fixed, human-reviewed proposal rather than a model.
It proves the registry, MCP stdio server, scope ticket, isolation, protected
acceptance test and final apply boundary before a model is allowed to use the
workflow.
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


ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path.home() / ".gemini" / "antigravity" / "custom_harness"
CONFIG = Path.home() / ".gemini" / "config" / "mcp_config.json"


TODO = '''import json
from pathlib import Path
import sys

TODO_FILE = Path("todos.json")

def load_todos():
    if not TODO_FILE.exists():
        return []
    return json.loads(TODO_FILE.read_text(encoding="utf-8"))

def save_todos(todos):
    TODO_FILE.write_text(json.dumps(todos), encoding="utf-8")

def add(description):
    todos = load_todos()
    todos.append({"description": description, "completed": False})
    save_todos(todos)
    print(f"Added: {description}")

def list_tasks():
    for index, todo in enumerate(load_todos(), 1):
        print(f"{index}. {todo['description']}")

def done(raw_index):
    try:
        index = int(raw_index)
    except ValueError:
        print("Error: invalid task number")
        return
    todos = load_todos()
    if not 1 <= index <= len(todos):
        print("Error: invalid task number")
        return
    todos[index - 1]["completed"] = True
    save_todos(todos)
    print(f"Done: {index}")

def main():
    command, *values = sys.argv[1:]
    if command == "add" and values:
        add(" ".join(values))
    elif command == "list":
        list_tasks()
    elif command == "done" and values:
        done(values[0])
    else:
        print("Error: invalid command")

if __name__ == "__main__":
    main()
'''

UNIT = '''import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("todo.py")

class TodoTests(unittest.TestCase):
    def run_cli(self, cwd, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True)

    def test_add(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            result = self.run_cli(cwd, "add", "Milk")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((cwd / "todos.json").read_text())[0], {"description": "Milk", "completed": False})

    def test_list(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            self.run_cli(cwd, "add", "Milk")
            result = self.run_cli(cwd, "list")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1.", result.stdout)
            self.assertIn("Milk", result.stdout)

    def test_done(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            self.run_cli(cwd, "add", "Milk")
            result = self.run_cli(cwd, "done", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads((cwd / "todos.json").read_text())[0]["completed"])

    def test_invalid_number(self):
        with tempfile.TemporaryDirectory() as raw:
            result = self.run_cli(Path(raw), "done", "99")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("error", result.stdout.casefold())
'''

ACCEPTANCE = '''import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "todo.py"

class Acceptance(unittest.TestCase):
    def run_cli(self, cwd, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd, text=True, capture_output=True)

    def test_add(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            add = self.run_cli(cwd, "add", "Buy milk")
            self.assertEqual(add.returncode, 0, add.stderr)
            saved = json.loads((cwd / "todos.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0], {"description": "Buy milk", "completed": False})

    def test_list(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            self.run_cli(cwd, "add", "Buy milk")
            listed = self.run_cli(cwd, "list")
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("1. Buy milk", listed.stdout)

    def test_done(self):
        with tempfile.TemporaryDirectory() as raw:
            cwd = Path(raw)
            self.run_cli(cwd, "add", "Buy milk")
            done = self.run_cli(cwd, "done", "1")
            self.assertEqual(done.returncode, 0, done.stderr)
            saved = json.loads((cwd / "todos.json").read_text(encoding="utf-8"))
            self.assertTrue(saved[0]["completed"])

    def test_invalid_number(self):
        with tempfile.TemporaryDirectory() as raw:
            invalid = self.run_cli(Path(raw), "done", "99")
            self.assertEqual(invalid.returncode, 0, invalid.stderr)
            self.assertIn("error", invalid.stdout.casefold())
'''


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True)


def _prepare_workspace(workspace: Path) -> Path:
    (workspace / ".deep_dev").mkdir()
    (workspace / ".deep_dev" / "acceptance_todo.py").write_text(ACCEPTANCE, encoding="utf-8")
    config = {
        "version": "1.0",
        "allowlisted_test_commands": {
            "unit": {"executable": sys.executable, "args": ["-m", "unittest", "-v"], "cwd": ".", "timeout_seconds": 60, "minimum_test_count": 4},
            "acceptance": {"executable": sys.executable, "args": ["-m", "unittest", "-v", "acceptance_todo"], "cwd": ".deep_dev", "timeout_seconds": 60, "minimum_test_count": 1},
        },
    }
    config_path = workspace / ".deep_dev" / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _run("git", "init", cwd=workspace)
    _run("git", "config", "user.name", "Deep Dev Golden", cwd=workspace)
    _run("git", "config", "user.email", "golden@local", cwd=workspace)
    _run("git", "add", ".", cwd=workspace)
    _run("git", "commit", "-m", "golden baseline", cwd=workspace)
    return config_path


def _operations() -> list[dict[str, str]]:
    return [
        {"file_path": "todo.py", "action": "create", "content": TODO},
        {"file_path": "test_todo.py", "action": "create", "content": UNIT},
        {"file_path": "README.md", "action": "create", "content": "# Golden Todo\n\nRun `python -m unittest -v test_todo`.\n"},
    ]


async def _run_mcp(workspace: Path, config_path: Path) -> dict[str, Any]:
    registry = json.loads(CONFIG.read_text(encoding="utf-8"))
    entry = registry["mcpServers"]["deep_dev_harness"]
    assert entry["args"] == ["-m", "custom_harness.mcp_server"]
    assert Path(entry["cwd"]).resolve() == HARNESS.resolve()
    env = os.environ.copy()
    env.update(entry["env"])
    subprocess.run([sys.executable, "-m", "graphify", "--help"], check=True, capture_output=True, text=True, timeout=15)
    params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=entry["cwd"], env=env)
    ok, ticket = exchange_ticket(issue_ticket(), workspace, ["todo.py", "test_todo.py", "README.md"], config_path)
    assert ok, ticket
    request = {
        "task": "Golden fixed mini-todo proposal",
        "workspace_root": str(workspace),
        "target_paths": ["todo.py", "test_todo.py", "README.md"],
        "config_path": str(config_path),
        "capability_ticket": ticket,
        "proposed_file_operations": _operations(),
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert "execute_host_proposal" in {tool.name for tool in tools.tools}
            result = await session.call_tool("execute_host_proposal", request)
    return json.loads(result.content[0].text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="deep-dev-golden-") as raw:
        workspace = Path(raw)
        config_path = _prepare_workspace(workspace)
        response = asyncio.run(_run_mcp(workspace, config_path))
        assert response["terminal_state"] == "ACCEPT_PATCH", response
        assert (workspace / "todo.py").is_file()
        assert (workspace / "test_todo.py").is_file()
        assert (workspace / ".deep_dev" / "acceptance_todo.py").read_text(encoding="utf-8") == ACCEPTANCE
        print(json.dumps({"golden_e2e": "passed", "run_id": response["run_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
