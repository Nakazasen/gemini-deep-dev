"""Runtime integrity verification for installed Deep Dev control files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verify_integrity() -> tuple[bool, str]:
    skill_root = Path(__file__).resolve().parent.parent
    registry = skill_root / "integrity.json"
    if not registry.is_file():
        return False, "Deep Dev integrity registry is missing. Reinstall Deep Dev."
    try:
        data = json.loads(registry.read_text(encoding="utf-8-sig"))
        files = data.get("files")
        if not isinstance(files, dict) or not files:
            return False, "Deep Dev integrity registry is malformed."
        for relative, expected in files.items():
            candidate = (skill_root / relative).resolve(strict=False)
            try:
                candidate.relative_to(skill_root)
            except ValueError:
                return False, f"Integrity path escapes skill root: {relative}"
            if not candidate.is_file():
                return False, f"Protected Deep Dev file is missing: {relative}"
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual.lower() != str(expected).lower():
                return False, f"Protected Deep Dev file changed: {relative}"
        harness_files = data.get("harness_files", {})
        if not isinstance(harness_files, dict) or not harness_files:
            return False, "Deep Dev harness integrity registry is missing."
        gemini_home = skill_root.parents[2]
        harness_root = (gemini_home / "antigravity" / "custom_harness").resolve(strict=False)
        for relative, expected in harness_files.items():
            candidate = (harness_root / relative).resolve(strict=False)
            try:
                candidate.relative_to(harness_root)
            except ValueError:
                return False, f"Harness integrity path escapes root: {relative}"
            if not candidate.is_file():
                return False, f"Protected Harness file is missing: {relative}"
            actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if actual.lower() != str(expected).lower():
                return False, f"Protected Harness file changed: {relative}"
        return True, "Deep Dev integrity verified."
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, f"Deep Dev integrity verification failed: {type(exc).__name__}."
