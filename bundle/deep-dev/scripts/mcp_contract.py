"""Load the single canonical MCP naming contract shared by Deep Dev components."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final


CONTRACT_PATH: Final = Path(__file__).resolve().parent.parent / "mcp_contract.json"
_REQUIRED: Final = {"version", "config_alias", "server_name", "transport", "wrapper_tool", "tool_name"}


def load_contract() -> dict[str, str]:
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if set(data) != _REQUIRED or not all(isinstance(data[key], str) and data[key] for key in _REQUIRED):
        raise RuntimeError(f"Invalid Deep Dev MCP contract: {CONTRACT_PATH}")
    if data["config_alias"] != data["server_name"]:
        raise RuntimeError("Deep Dev MCP config alias and runtime server name must be identical")
    if data["transport"] != "direct_mcp":
        raise RuntimeError("Deep Dev requires Antigravity direct MCP transport")
    return data


CONTRACT: Final = load_contract()
CONFIG_ALIAS: Final = CONTRACT["config_alias"]
SERVER_NAME: Final = CONTRACT["server_name"]
TOOL_NAME: Final = CONTRACT["tool_name"]
DIRECT_TOOL: Final = f"{SERVER_NAME}/{TOOL_NAME}"
WRAPPER_TOOL: Final = CONTRACT["wrapper_tool"]
