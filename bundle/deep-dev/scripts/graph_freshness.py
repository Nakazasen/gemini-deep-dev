"""
.deep_dev Engine: Graph Freshness & Impact Radius Module (graph_freshness.py)
=============================================================================
Verifies freshness of graphify knowledge graph (supporting actual nodes+links schema
and direct manifest mappings) and provides a fast, bounded AST import scanner fallback
with directory pruning and O(1) module index lookups.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from .path_utils import canonicalize_safe_relative_path, PathSecurityError
except ImportError:
    from path_utils import canonicalize_safe_relative_path, PathSecurityError

logger = logging.getLogger("deep_dev.graph")

PRUNED_DIRS = {
    ".git", ".venv", "venv", "env", ".env", "node_modules",
    "graphify-out", "__pycache__", ".deep_dev", "dist", "build",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".idea", ".vscode", "eggs", ".eggs",
}


class ASTImportScanner:
    """Fast, bounded AST scanner that extracts module dependency graphs in Python workspaces."""

    @classmethod
    def collect_python_files(
        cls,
        workspace_root: Path,
        max_files: int = 2000,
        max_seconds: float = 5.0,
    ) -> Tuple[List[Path], bool]:
        """Collect Python files with pruning of virtual environments, cache, and non-source directories."""
        py_files: List[Path] = []
        ws_resolved = workspace_root.resolve()
        start_time = time.monotonic()
        exceeded_budget = False

        for root, dirs, files in os.walk(workspace_root, followlinks=False):
            # Prune directories in place to prevent descending into venv/caches
            dirs[:] = [
                d for d in dirs
                if d not in PRUNED_DIRS and not d.endswith(".egg-info") and not d.startswith(".venv")
            ]

            for f in files:
                if f.endswith(".py"):
                    full_path = Path(root) / f
                    try:
                        resolved = full_path.resolve()
                        resolved.relative_to(ws_resolved)
                        py_files.append(full_path)
                    except (ValueError, OSError):
                        continue

                    if len(py_files) >= max_files:
                        exceeded_budget = True
                        break

            if exceeded_budget or (time.monotonic() - start_time) > max_seconds:
                exceeded_budget = True
                break

        return py_files, exceeded_budget

    @classmethod
    def scan_file_imports(cls, file_path: Path) -> Set[str]:
        """Extract imported module names from a Python source file."""
        imports: Set[str] = set()
        if not file_path.exists() or not file_path.is_file() or file_path.suffix != ".py":
            return imports

        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8", errors="replace"), filename=str(file_path))
        except SyntaxError:
            return imports

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    @classmethod
    def build_dependency_map(
        cls,
        workspace_root: Path,
        max_files: int = 2000,
        max_seconds: float = 5.0,
    ) -> Tuple[Dict[str, Set[str]], bool]:
        """
        Build caller map using fast single-pass indexing in O(files * imports).
        Returns (caller_map, exceeded_budget).
        """
        start_time = time.monotonic()
        py_files, exceeded_budget = cls.collect_python_files(workspace_root, max_files, max_seconds)
        ws_resolved = workspace_root.resolve()

        # Build module_name -> relative_path dictionary index
        module_to_file: Dict[str, str] = {}
        for pf in py_files:
            try:
                rel = pf.resolve().relative_to(ws_resolved).as_posix()
                mod_full = rel[:-3].replace("/", ".")
                mod_base = Path(rel).stem
                module_to_file[mod_full] = rel
                if mod_base not in module_to_file:
                    module_to_file[mod_base] = rel
            except Exception:
                continue

        caller_map: Dict[str, Set[str]] = {rel: set() for rel in module_to_file.values()}
        file_imports: Dict[str, Set[str]] = {}

        for pf in py_files:
            if (time.monotonic() - start_time) > max_seconds:
                exceeded_budget = True
                break
            try:
                rel = pf.resolve().relative_to(ws_resolved).as_posix()
                file_imports[rel] = cls.scan_file_imports(pf)
            except Exception:
                continue

        # Fast O(1) dictionary resolution for imports
        for caller_file, imports in file_imports.items():
            for imp in imports:
                if imp in module_to_file:
                    callee = module_to_file[imp]
                    caller_map[callee].add(caller_file)
                else:
                    # Check parent package prefix (e.g. 'pkg.math.add' -> 'pkg.math')
                    parts = imp.split(".")
                    while len(parts) > 1:
                        parts.pop()
                        parent_mod = ".".join(parts)
                        if parent_mod in module_to_file:
                            callee = module_to_file[parent_mod]
                            caller_map[callee].add(caller_file)
                            break

        return caller_map, exceeded_budget

    @classmethod
    def get_impact_radius(
        cls,
        workspace_root: Path,
        target_paths: List[str],
        depth: int = 1,
        max_files: int = 2000,
        max_seconds: float = 5.0,
    ) -> Tuple[List[str], bool]:
        """Compute blast radius of target files using bounded AST dependency graph."""
        dep_map, exceeded_budget = cls.build_dependency_map(workspace_root, max_files, max_seconds)
        impacted: Set[str] = set()

        for t in target_paths:
            try:
                norm = canonicalize_safe_relative_path(t, allow_root_dot=False)
            except PathSecurityError:
                continue

            impacted.add(norm)
            current_layer = {norm}

            for _ in range(depth):
                next_layer: Set[str] = set()
                for item in current_layer:
                    callers = dep_map.get(item, set())
                    for c in callers:
                        if c not in impacted:
                            impacted.add(c)
                            next_layer.add(c)
                current_layer = next_layer
                if not current_layer:
                    break

        return sorted(list(impacted)), exceeded_budget


class GraphFreshnessChecker:
    """Checks freshness of graphify knowledge graph artifacts."""

    @staticmethod
    def _external_output_root(workspace_root: Path) -> Path:
        local = os.environ.get("LOCALAPPDATA")
        runtime = Path(local) / "deep-dev" if local else Path.home() / ".deep-dev"
        identity = hashlib.sha256(str(workspace_root.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
        return runtime / "graphify" / identity

    @classmethod
    def _graph_output_dir(cls, workspace_root: Path) -> Path:
        repository_output = workspace_root / "graphify-out"
        if (repository_output / "graph.json").is_file() and (repository_output / "manifest.json").is_file():
            return repository_output
        return cls._external_output_root(workspace_root) / "graphify-out"

    @classmethod
    def ensure_graphify_ready(cls, workspace_root: Path, timeout_seconds: int = 180) -> Tuple[bool, str]:
        """Install Graphify if missing, refresh its Antigravity skill, and rebuild stale graphs."""
        # A genuinely new project can contain only Git metadata and Deep Dev's
        # protected configuration.  There is no source graph to create yet;
        # record an empty baseline and let the post-apply refresh index the
        # verified source instead of spending minutes rebuilding an empty tree.
        meaningful_entries = [
            path for path in workspace_root.iterdir()
            if path.name not in {".git", ".deep_dev", "graphify-out"}
        ]
        if not meaningful_entries:
            return True, "empty_workspace_graph_baseline"
        base_command = [sys.executable, "-m", "graphify"]

        def run_checked(args: List[str], timeout: int) -> Tuple[Optional[subprocess.CompletedProcess[str]], Optional[str]]:
            try:
                # Never inherit MCP stdio. Some Graphify maintenance commands
                # probe stdin; inheriting the JSON-RPC channel can deadlock a
                # host-proposal run before preflight completes.
                return subprocess.run(args, capture_output=True, text=True, input="", encoding="utf-8", errors="replace", check=False, timeout=timeout), None
            except Exception as exc:
                return None, f"{type(exc).__name__}: {exc}"

        probe, probe_error = run_checked(base_command + ["--help"], 30)
        if probe is None or probe.returncode != 0:
            install, install_error = run_checked(
                [sys.executable, "-m", "pip", "install", "graphifyy"],
                timeout_seconds,
            )
            if install is None or install.returncode != 0:
                detail = install_error or (install.stderr or install.stdout).strip()[-500:]
                return False, f"Graphify installation failed: {detail}"

        skill_install, skill_error = run_checked(
            base_command + ["install", "--platform", "antigravity"],
            60,
        )
        if skill_install is None or skill_install.returncode != 0:
            detail = skill_error or (skill_install.stderr or skill_install.stdout).strip()[-500:]
            return False, f"Graphify Antigravity integration update failed: {detail}"

        is_fresh, reason = cls.check_freshness(workspace_root)
        if is_fresh:
            return True, reason

        repository_output = workspace_root / "graphify-out"
        if (repository_output / "graph.json").is_file():
            update_args = base_command + ["update", str(workspace_root)]
        else:
            external_root = cls._external_output_root(workspace_root)
            external_root.mkdir(parents=True, exist_ok=True)
            update_args = base_command + [
                str(workspace_root),
                "--out", str(external_root),
                "--no-viz",
                "--code-only",
            ]
        update, update_error = run_checked(update_args, timeout_seconds)
        if update is None or update.returncode != 0:
            detail = update_error or (update.stderr or update.stdout).strip()[-500:]
            return False, f"Graphify update failed after stale/missing graph ({reason}): {detail}"

        is_fresh, post_reason = cls.check_freshness(workspace_root)
        if not is_fresh:
            return False, f"Graphify update completed but freshness verification failed: {post_reason}"
        return True, "Graphify installed/updated and freshness verified."

    @classmethod
    def check_freshness(cls, workspace_root: Path) -> Tuple[bool, str]:
        """
        Verify whether graphify-out/graph.json and manifest.json exist, are valid schemas,
        and all indexed files match on disk.
        """
        graph_output = cls._graph_output_dir(workspace_root)
        graph_file = graph_output / "graph.json"
        manifest_file = graph_output / "manifest.json"

        if not graph_file.exists():
            return False, "Graphify output 'graphify-out/graph.json' does not exist."

        if not manifest_file.exists():
            return False, "Graphify manifest 'graphify-out/manifest.json' does not exist."

        try:
            raw_manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(raw_manifest, dict):
                return False, "Manifest is invalid: root element must be a dictionary mapping."

            # Support both direct mapping and legacy 'files' wrapper
            if "files" in raw_manifest and isinstance(raw_manifest["files"], dict):
                files_dict = raw_manifest["files"]
            else:
                files_dict = raw_manifest

            if not files_dict:
                return False, "Manifest is empty."

            ws_resolved = workspace_root.resolve()

            for raw_rel_path, meta in files_dict.items():
                if not isinstance(meta, dict):
                    return False, f"Manifest metadata for '{raw_rel_path}' is invalid: must be a dict."

                try:
                    norm_path = canonicalize_safe_relative_path(raw_rel_path, allow_root_dot=False)
                except PathSecurityError as pse:
                    return False, f"Manifest contains dangerous or invalid path '{raw_rel_path}': {pse}"

                target = (workspace_root / norm_path).resolve()
                try:
                    target.relative_to(ws_resolved)
                except ValueError:
                    return False, f"Manifest file '{raw_rel_path}' resolves outside workspace boundary."

                if not target.exists():
                    return False, f"Graph is stale: indexed file '{norm_path}' has been deleted."

                recorded_mtime = meta.get("mtime")
                if recorded_mtime is not None:
                    current_mtime = target.stat().st_mtime
                    if current_mtime > float(recorded_mtime) + 1.0:
                        return False, f"Graph is stale: indexed file '{norm_path}' modified on disk."

            return True, "Graphify graph is fresh."
        except Exception as exc:
            return False, f"Error inspecting graphify manifest: {exc}"

    @classmethod
    def query_impact(
        cls,
        workspace_root: Path,
        target_paths: List[str],
        max_files: int = 2000,
        max_seconds: float = 5.0,
    ) -> Tuple[List[str], bool]:
        """
        Query impact radius using Graphify nodes+links schema if fresh, or ASTImportScanner fallback.
        Returns (impacted_paths, is_degraded_fallback).
        """
        is_fresh, reason = cls.check_freshness(workspace_root)
        if is_fresh:
            try:
                graph_file = cls._graph_output_dir(workspace_root) / "graph.json"
                graph_data = json.loads(graph_file.read_text(encoding="utf-8"))

                if not isinstance(graph_data, dict):
                    raise ValueError("Graph data must be a JSON object.")

                nodes = graph_data.get("nodes")
                links = graph_data.get("links")

                if not isinstance(nodes, list) or not isinstance(links, list):
                    raise ValueError("Graph schema mismatch: 'nodes' and 'links' must both be lists.")

                # Build mapping: node_id -> safe_relative_source_file
                node_to_file: Dict[str, str] = {}
                file_to_nodes: Dict[str, Set[str]] = {}

                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    node_id = str(node.get("id", ""))
                    if not node_id:
                        continue

                    raw_src = node.get("source_file") or node.get("file_path") or node.get("path") or ""
                    if not raw_src and (node.get("type") == "file" or node.get("kind") == "file"):
                        raw_src = node_id

                    if raw_src:
                        try:
                            norm_file = canonicalize_safe_relative_path(str(raw_src), allow_root_dot=False)
                            node_to_file[node_id] = norm_file
                            file_to_nodes.setdefault(norm_file, set()).add(node_id)
                        except PathSecurityError:
                            continue

                # Collect target node IDs
                normalized_targets: Set[str] = set()
                target_node_ids: Set[str] = set()

                for t in target_paths:
                    try:
                        norm_t = canonicalize_safe_relative_path(t, allow_root_dot=False)
                        normalized_targets.add(norm_t)
                        if norm_t in file_to_nodes:
                            target_node_ids.update(file_to_nodes[norm_t])
                        if norm_t in node_to_file:
                            target_node_ids.add(norm_t)
                    except PathSecurityError:
                        continue

                impacted_files: Set[str] = set(normalized_targets)

                for link in links:
                    if not isinstance(link, dict):
                        continue
                    src_id = str(link.get("source", ""))
                    tgt_id = str(link.get("target", ""))

                    if src_id in target_node_ids and tgt_id in node_to_file:
                        impacted_files.add(node_to_file[tgt_id])
                    elif tgt_id in target_node_ids and src_id in node_to_file:
                        impacted_files.add(node_to_file[src_id])

                if impacted_files:
                    return sorted(list(impacted_files)), False
            except Exception:
                pass

        # Fallback to bounded AST scanner
        ast_impacted, exceeded = ASTImportScanner.get_impact_radius(
            workspace_root, target_paths, depth=1, max_files=max_files, max_seconds=max_seconds
        )
        return ast_impacted, True
