"""Install one verified Gemini Deep Dev bundle into an Antigravity profile."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


REQUIRED = (
    "deep-dev/SKILL.md",
    "deep-dev/mcp_contract.json",
    "deep-dev/scripts/deep_dev_gate.py",
    "deep-dev/scripts/deep_dev_reminder.py",
    "custom_harness/custom_harness/mcp_grounding/server.py",
    "mcp/deep_dev_harness/execute_host_proposal.json",
)


def validate_bundle(bundle_root: Path) -> None:
    missing = [item for item in REQUIRED if not (bundle_root / item).is_file()]
    if missing:
        raise ValueError(f"Bundle is incomplete: {', '.join(missing)}")
    registry = json.loads((bundle_root / "deep-dev" / "integrity.json").read_text(encoding="utf-8-sig"))
    for group, root in (("files", bundle_root / "deep-dev"), ("harness_files", bundle_root / "custom_harness")):
        for relative, expected in registry.get(group, {}).items():
            candidate = root / relative
            if not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest().lower() != str(expected).lower():
                raise ValueError(f"Bundle integrity failed: {group}/{relative}")


def _atomic_copy_tree(source: Path, destination: Path) -> None:
    staging = destination.with_name(f"{destination.name}.staging")
    backup = destination.with_name(f"{destination.name}.backup")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "backups"))
    if destination.exists():
        destination.replace(backup)
    staging.replace(destination)
    shutil.rmtree(backup, ignore_errors=True)


def _update_hooks(profile: Path, skill_dir: Path) -> Path:
    config_dir = profile / ".gemini" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = config_dir / "hooks.json"
    if hooks_path.exists():
        backup = hooks_path.with_name(f"hooks.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json.bak")
        shutil.copy2(hooks_path, backup)
        data = json.loads(hooks_path.read_text(encoding="utf-8-sig"))
    else:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("hooks.json must contain a JSON object")
    data["deep-dev-enforcement"] = {
        "enabled": True,
        "PreInvocation": [{"type": "command", "command": str(skill_dir / "scripts" / "deep_dev_reminder.cmd"), "timeout": 45}],
        "PreToolUse": [],
    }
    hooks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return hooks_path


def install(bundle_root: Path, user_profile: Path, version: str, manifest_url: str, python_executable: str, auto_update: bool = True) -> dict[str, str]:
    validate_bundle(bundle_root)
    skill_dir = user_profile / ".gemini" / "config" / "skills" / "deep-dev"
    harness_dir = user_profile / ".gemini" / "antigravity" / "custom_harness" / "custom_harness"
    mcp_dir = user_profile / ".gemini" / "antigravity-ide" / "mcp" / "deep_dev_harness"
    _atomic_copy_tree(bundle_root / "deep-dev", skill_dir)
    _atomic_copy_tree(bundle_root / "custom_harness" / "custom_harness", harness_dir)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle_root / "mcp" / "deep_dev_harness" / "execute_host_proposal.json", mcp_dir / "execute_host_proposal.json")
    updater_dir = skill_dir / "updater"
    updater_dir.mkdir(parents=True, exist_ok=True)
    for name in ("deep_dev_installer.py", "deep_dev_update.py", "AutoUpdate-DeepDev.ps1"):
        shutil.copy2(Path(__file__).with_name(name), updater_dir / name)
    hooks_path = _update_hooks(user_profile, skill_dir)
    update_config = user_profile / ".gemini" / "config" / "deep-dev-update.json"
    update_config.write_text(json.dumps({"version": version, "manifest_url": manifest_url, "auto_update": auto_update}, indent=2) + "\n", encoding="utf-8")
    return {"skill_dir": str(skill_dir), "harness_dir": str(harness_dir), "mcp_dir": str(mcp_dir), "hooks": str(hooks_path), "update_config": str(update_config)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--user-profile", type=Path, default=Path.home())
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-url", default="")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--disable-auto-update", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(args.bundle_root.resolve(), args.user_profile.resolve(), args.version, args.manifest_url, args.python_executable, not args.disable_auto_update)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
