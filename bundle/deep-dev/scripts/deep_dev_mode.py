"""User-operated Quick/Deep mode switch; never exposed to the model tool gate."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"quick", "deep", "status"}:
        print("Usage: deep_dev_mode.cmd quick|deep|status")
        return 2
    config = Path.home() / ".gemini" / "config" / "hooks.json"
    data = json.loads(config.read_text(encoding="utf-8"))
    enforcement = data["deep-dev-enforcement"]
    if sys.argv[1] == "status":
        mode = "deep" if enforcement.get("PreToolUse") else "quick"
        print(f"Deep Dev mode: {mode}")
        return 0
    backup = config.with_name(f"hooks.json.mode-{datetime.now():%Y%m%d-%H%M%S}.bak")
    backup.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    if sys.argv[1] == "quick":
        enforcement["PreToolUse"] = []
    else:
        gate = str(Path.home() / ".gemini" / "config" / "skills" / "deep-dev" / "scripts" / "deep_dev_gate.cmd")
        enforcement["PreToolUse"] = [{"matcher": "*", "hooks": [{"type": "command", "command": gate, "timeout": 45}]}]
    config.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Deep Dev mode: {sys.argv[1]}; backup: {backup}")
    print("Restart Antigravity completely for the change to take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
