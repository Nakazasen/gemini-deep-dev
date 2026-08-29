"""Regenerate the checked integrity registry for a portable release bundle."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundle"
REGISTRY = BUNDLE / "deep-dev" / "integrity.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    for group, root in (("files", BUNDLE / "deep-dev"), ("harness_files", BUNDLE / "custom_harness")):
        data[group] = {relative: digest(root / relative) for relative in data[group]}
    data["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    REGISTRY.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
