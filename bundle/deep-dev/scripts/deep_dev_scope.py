"""Exchange a /deep-dev entry ticket for a scope-bound orchestrator capability."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .capability import exchange_ticket
except ImportError:
    from capability import exchange_ticket


def main() -> int:
    parser = argparse.ArgumentParser(prog="deep_dev_scope")
    parser.add_argument("--entry-ticket", required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--target-path", action="append", dest="target_paths", required=True)
    parser.add_argument("--config-path", type=Path, default=None)
    parser.add_argument("--run-id", default=None, help="Deprecated and ignored; run IDs are host-generated.")
    args = parser.parse_args()
    ok, value = exchange_ticket(
        args.entry_ticket, args.workspace_root, args.target_paths, args.config_path, None,
    )
    print(json.dumps({"success": ok, "capability_ticket": value if ok else None, "error": None if ok else value}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
