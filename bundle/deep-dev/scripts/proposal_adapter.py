"""Normalize host proposal dialects at the Deep Dev boundary.

The IDE host and the bundled harness expose different historical operation
shapes.  This module is deliberately the *only* compatibility boundary: all
downstream Deep Dev components receive the compact canonical shape below.
It is strict about ambiguity and never invents a target or file content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping

try:
    from .path_utils import PathSecurityError, canonicalize_safe_relative_path
    from .snapshot import WorkspaceSnapshot
except ImportError:
    from path_utils import PathSecurityError, canonicalize_safe_relative_path
    from snapshot import WorkspaceSnapshot


class ProposalAdapterError(ValueError):
    """A host operation cannot be safely converted to the canonical contract."""


@dataclass(frozen=True)
class ProposalAdaptation:
    operations: List[Dict[str, Any]]
    ignored_empty_noops: int = 0
    normalized_fields: int = 0
    normalized_write_actions: int = 0


_FIELD_ALIASES = {
    "file_path": ("path", "target_path"),
    "action": ("operation", "op", "type"),
    "content": ("content_or_diff", "new_content", "text"),
}
_KNOWN_FIELDS = frozenset({"file_path", "action", "content", *[name for names in _FIELD_ALIASES.values() for name in names]})
_ACTIONS = frozenset({"create", "modify", "delete", "noop", "write"})


def _nonempty(value: Any) -> bool:
    return value not in (None, "")


def _field(raw: Mapping[str, Any], canonical: str, index: int) -> tuple[Any, int]:
    candidates = [(canonical, raw.get(canonical))]
    candidates.extend((alias, raw.get(alias)) for alias in _FIELD_ALIASES[canonical])
    present = [(name, value) for name, value in candidates if _nonempty(value)]
    if not present:
        return None, 0
    values = {str(value) for _, value in present}
    if len(values) != 1:
        names = ", ".join(name for name, _ in present)
        raise ProposalAdapterError(f"Operation {index} has conflicting {canonical} fields: {names}.")
    name, value = present[0]
    return value, 0 if name == canonical else 1


def _is_empty_noop(raw: Mapping[str, Any]) -> bool:
    if not raw:
        return True
    if not set(raw).issubset(_KNOWN_FIELDS):
        return False
    action = raw.get("action", raw.get("operation", raw.get("op", raw.get("type", ""))))
    path = raw.get("file_path", raw.get("path", raw.get("target_path", "")))
    content = raw.get("content", raw.get("content_or_diff", raw.get("new_content", raw.get("text", ""))))
    return not _nonempty(path) and str(action or "").casefold() in {"", "noop"} and not _nonempty(content)


def adapt_operations(raw_operations: Any, snapshot: WorkspaceSnapshot) -> ProposalAdaptation:
    """Convert supported host dialects to canonical full-file operations.

    Supported input is intentionally finite and documented: canonical Deep Dev
    fields, the native harness ``target_path``/``write`` shape, and the two
    former IDE aliases.  Ambiguous or malformed input is rejected before patch
    serialization; this preserves the snapshot and scope checks downstream.
    """
    if not isinstance(raw_operations, list):
        raise ProposalAdapterError("Host proposal operations must be a list.")

    output: List[Dict[str, Any]] = []
    ignored = normalized_fields = normalized_writes = 0
    for index, raw in enumerate(raw_operations, start=1):
        if not isinstance(raw, Mapping):
            raise ProposalAdapterError(f"Operation {index} must be an object.")
        if _is_empty_noop(raw):
            ignored += 1
            continue

        raw_path, path_aliases = _field(raw, "file_path", index)
        raw_action, action_aliases = _field(raw, "action", index)
        raw_content, content_aliases = _field(raw, "content", index)
        normalized_fields += path_aliases + action_aliases + content_aliases
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProposalAdapterError(f"Operation {index} is missing file_path.")
        if not isinstance(raw_action, str) or not raw_action.strip():
            raise ProposalAdapterError(f"Operation {index} is missing action.")
        try:
            file_path = canonicalize_safe_relative_path(raw_path, allow_root_dot=False)
        except PathSecurityError as exc:
            raise ProposalAdapterError(f"Operation {index} has unsafe file_path: {exc}") from exc
        action = raw_action.casefold().strip()
        if action not in _ACTIONS:
            raise ProposalAdapterError(f"Operation {index} has unsupported action '{raw_action}'.")
        if action == "write":
            entry = snapshot.files.get(file_path)
            action = "modify" if entry and entry.exists else "create"
            normalized_writes += 1
        if action in {"create", "modify"} and not isinstance(raw_content, str):
            raise ProposalAdapterError(f"Operation {index} requires text content for '{file_path}'.")
        if action == "delete":
            raw_content = ""
        output.append({"file_path": file_path, "action": action, "content": raw_content if isinstance(raw_content, str) else ""})

    return ProposalAdaptation(output, ignored, normalized_fields, normalized_writes)
