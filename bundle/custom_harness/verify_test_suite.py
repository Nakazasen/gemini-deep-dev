"""Run every test actually shipped in the portable Deep Dev bundle."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import time

import pytest


HARNESS_ROOT = Path(__file__).parent.resolve()
PAYLOAD_ROOT = HARNESS_ROOT.parent
SKILL_SCRIPTS = PAYLOAD_ROOT / "deep-dev" / "scripts"
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))
if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))


def discover_tests() -> list[Path]:
    harness_tests = sorted((HARNESS_ROOT / "tests").glob("test_*.py"))
    configured_skill = os.environ.get("DEEP_DEV_SKILL_ROOT")
    skill_root = Path(configured_skill) if configured_skill else PAYLOAD_ROOT / "deep-dev"
    deep_dev_tests = [skill_root / "scripts" / "test_deep_dev_security.py"]
    return harness_tests + deep_dev_tests


def run_full_suite() -> int:
    tests = discover_tests()
    print("=" * 80)
    print("DEEP DEV PORTABLE BUNDLE TEST SUITE".center(80))
    print("=" * 80)
    if not tests:
        print("FAILED: bundle contains no executable tests.")
        return 1
    missing = [path for path in tests if not path.is_file()]
    if missing:
        print("FAILED: discovered test paths disappeared before execution:")
        for path in missing:
            print(f"  {path}")
        return 1
    print(f"Discovered {len(tests)} test files; no phantom paths are accepted.")
    started = time.monotonic()
    result = pytest.main(["-q", "--tb=short", *[str(path) for path in tests]])
    duration = time.monotonic() - started
    status = "PASSED" if result == pytest.ExitCode.OK else f"FAILED (exit: {result})"
    print(f"{status}: {len(tests)} files in {duration:.2f}s")
    return 0 if result == pytest.ExitCode.OK else 1


if __name__ == "__main__":
    raise SystemExit(run_full_suite())
