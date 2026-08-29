"""Lightweight dependency-diff gate for Deep Dev worktree changes."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import Any


_JS_IMPORT = re.compile(r"(?:from\s+|require\s*\(|import\s*\()\s*['\"]([^'\"]+)['\"]")


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


def capture(root: Path) -> dict[str, Any]:
    root = root.resolve()
    suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes and ".git" not in path.parts)
    edges: set[tuple[str, str]] = set()
    for path in files:
        edges.update(_edges(path, root))
    serial = sorted([source, target] for source, target in edges)
    return {"root": str(root), "edges": serial, "sha256": hashlib.sha256(json.dumps(serial, separators=(",", ":")).encode()).hexdigest()}


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
