"""Lightweight dependency-diff gate for Deep Dev worktree changes."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable


_JS_IMPORT = re.compile(r"(?:from\s+|require\s*\(|import\s*\()\s*['\"]([^'\"]+)['\"]")
_CODE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_SKIP_DIRECTORIES = frozenset({
    ".git", "__pycache__", "node_modules", "dist", "build", "local_cases",
    "local_runs", "graphify-out", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})


class DependencyGraphCaptureError(RuntimeError):
    """Dependency capture exceeded its signed scope or bounded resource budget."""


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _resolve_python(source: Path, module: str, level: int, root: Path) -> Path | None:
    base = source.parent
    for _ in range(max(level - 1, 0)):
        base = base.parent
    parts = module.split(".") if module else []
    candidate = base.joinpath(*parts) if level else root.joinpath(*parts)
    for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
        if path.exists() and _inside(path, root):
            return path.resolve()
    return None


def _edges(path: Path, root: Path) -> set[tuple[str, str]]:
    source = path.resolve().relative_to(root.resolve()).as_posix()
    found: set[tuple[str, str]] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return found
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return found
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [(alias.name, 0) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or "", node.level)]
            else:
                continue
            for module, level in modules:
                target = _resolve_python(path, module, level, root)
                if target:
                    found.add((source, target.relative_to(root.resolve()).as_posix()))
    elif path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        for specifier in _JS_IMPORT.findall(text):
            if not specifier.startswith("."):
                continue
            candidate = (path.parent / specifier).resolve(strict=False)
            variants = [candidate, *(candidate.with_suffix(ext) for ext in (".js", ".jsx", ".ts", ".tsx")), candidate / "index.ts", candidate / "index.js"]
            target = next((item for item in variants if item.is_file() and _inside(item, root)), None)
            if target:
                found.add((source, target.relative_to(root.resolve()).as_posix()))
    return found


def _walk_code_files(root: Path, source_paths: Iterable[str | Path] | None) -> Iterable[Path]:
    """Yield code files, preferring an explicit signed source boundary."""
    if source_paths is not None:
        seen: set[Path] = set()
        for raw in source_paths:
            candidate = (root / Path(raw)).resolve(strict=False) if not Path(raw).is_absolute() else Path(raw).resolve(strict=False)
            if not _inside(candidate, root):
                raise DependencyGraphCaptureError(f"Dependency source escaped workspace: {raw}")
            if candidate.is_file() and candidate.suffix.lower() in _CODE_SUFFIXES and candidate not in seen:
                seen.add(candidate)
                yield candidate
        return

    for directory, names, files in os.walk(root):
        names[:] = [
            name for name in names
            if name not in _SKIP_DIRECTORIES
            and not name.startswith(".venv")
            and not name.startswith(".tmp_")
        ]
        base = Path(directory)
        for name in files:
            candidate = base / name
            if candidate.suffix.lower() in _CODE_SUFFIXES:
                yield candidate


def capture(
    root: Path,
    source_paths: Iterable[str | Path] | None = None,
    *,
    max_files: int = 2000,
    max_seconds: float = 5.0,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> dict[str, Any]:
    root = root.resolve()
    started = time.monotonic()
    files: list[Path] = []
    for path in _walk_code_files(root, source_paths):
        if time.monotonic() - started > max_seconds:
            raise DependencyGraphCaptureError(
                f"Dependency capture exceeded {max_seconds:.1f}s before parsing."
            )
        if len(files) >= max_files:
            raise DependencyGraphCaptureError(
                f"Dependency capture exceeded the {max_files}-file limit."
            )
        try:
            if path.stat().st_size > max_file_bytes:
                raise DependencyGraphCaptureError(
                    f"Dependency source exceeds the {max_file_bytes}-byte limit: {path.name}"
                )
        except OSError as exc:
            raise DependencyGraphCaptureError(f"Dependency source cannot be inspected: {path.name}") from exc
        files.append(path)

    edges: set[tuple[str, str]] = set()
    for path in sorted(files):
        if time.monotonic() - started > max_seconds:
            raise DependencyGraphCaptureError(
                f"Dependency capture exceeded {max_seconds:.1f}s while parsing {len(files)} files."
            )
        edges.update(_edges(path, root))
    serial = sorted([source, target] for source, target in edges)
    return {
        "root": str(root),
        "edges": serial,
        "sha256": hashlib.sha256(json.dumps(serial, separators=(",", ":")).encode()).hexdigest(),
        "scanned_files": len(files),
        "scope": "signed_targets" if source_paths is not None else "bounded_workspace",
    }


def compare(baseline: dict[str, Any], candidate: dict[str, Any], allowed_paths: list[Path], root: Path) -> dict[str, Any]:
    root = root.resolve()
    allowed = [path.resolve(strict=False) for path in allowed_paths]
    prior = {tuple(edge) for edge in baseline.get("edges", [])}
    current = {tuple(edge) for edge in candidate.get("edges", [])}
    added = sorted(current - prior)
    violations: list[list[str]] = []
    for source, target in added:
        source_path, target_path = root / source, root / target
        source_allowed = any(source_path == item or item.is_dir() and _inside(source_path, item) for item in allowed)
        target_allowed = any(target_path == item or item.is_dir() and _inside(target_path, item) for item in allowed)
        if source_allowed and not target_allowed:
            violations.append([source, target])
    return {"safe": not violations, "added_edges": [list(edge) for edge in added], "violations": violations, "baseline_sha256": baseline.get("sha256"), "candidate_sha256": candidate.get("sha256")}
