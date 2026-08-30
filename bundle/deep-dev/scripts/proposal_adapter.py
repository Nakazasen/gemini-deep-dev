"""Normalize host proposal dialects at the Deep Dev boundary.

The IDE host and the bundled harness expose different historical operation
shapes.  This module is deliberately the *only* compatibility boundary: all
downstream Deep Dev components receive the compact canonical shape below.
It is strict about ambiguity and never invents a target or file content.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
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
    expanded_exact_replacements: int = 0


_FIELD_ALIASES = {
    "file_path": ("path", "target_path"),
    "action": ("operation", "op", "type"),
    "content": ("content_or_diff", "new_content", "text"),
}
_KNOWN_FIELDS = frozenset({
    "file_path", "action", "content", "old_text", "new_text", "replacements",
    *[name for names in _FIELD_ALIASES.values() for name in names],
})
_ACTIONS = frozenset({"create", "modify", "delete", "noop", "write", "exact_replace"})


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


def _expand_exact_replace(
    raw: Mapping[str, Any],
    index: int,
    file_path: str,
    snapshot: WorkspaceSnapshot,
    workspace_root: Path | None,
) -> tuple[str, int]:
    """Expand bounded exact replacements against the sealed snapshot baseline."""
    if _nonempty(raw.get("content")) or any(
        _nonempty(raw.get(alias)) for alias in _FIELD_ALIASES["content"]
    ):
        raise ProposalAdapterError(
            f"Operation {index} cannot combine exact_replace with full-file content."
        )
    if workspace_root is None:
        raise ProposalAdapterError(f"Operation {index} cannot exact_replace without a workspace root.")
    entry = snapshot.files.get(file_path)
    if entry is None or not entry.exists:
        raise ProposalAdapterError(f"Operation {index} cannot exact_replace missing file '{file_path}'.")

    target = (workspace_root.resolve() / file_path).resolve()
    try:
        target.relative_to(workspace_root.resolve())
        content = target.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProposalAdapterError(f"Operation {index} cannot read '{file_path}' as UTF-8: {exc}") from exc
    current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if current_hash != entry.base_sha256:
        raise ProposalAdapterError(f"Operation {index} has a stale snapshot for '{file_path}'.")

    raw_replacements = raw.get("replacements")
    has_single = "old_text" in raw or "new_text" in raw
    if raw_replacements is not None and has_single:
        raise ProposalAdapterError(
            f"Operation {index} must use either replacements or old_text/new_text, not both."
        )
    replacements = raw_replacements if raw_replacements is not None else [
        {"old_text": raw.get("old_text"), "new_text": raw.get("new_text")}
    ]
    if not isinstance(replacements, list) or not replacements:
        raise ProposalAdapterError(f"Operation {index} requires at least one exact replacement.")

    for replacement_index, replacement in enumerate(replacements, start=1):
        if not isinstance(replacement, Mapping):
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} must be an object."
            )
        unknown = set(replacement) - {"old_text", "new_text"}
        if unknown:
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} has unsupported fields: "
                f"{', '.join(sorted(str(field) for field in unknown))}."
            )
        old_text = replacement.get("old_text")
        new_text = replacement.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} requires non-empty old_text."
            )
        if not isinstance(new_text, str):
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} requires text new_text."
            )
        old_text = old_text.replace("\r\n", "\n")
        new_text = new_text.replace("\r\n", "\n")
        if old_text == new_text:
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} does not change the file."
            )
        occurrences = content.count(old_text)
        if occurrences != 1:
            raise ProposalAdapterError(
                f"Operation {index} replacement {replacement_index} old_text must match exactly once "
                f"in '{file_path}', found {occurrences}."
            )
        content = content.replace(old_text, new_text, 1)
    return content, len(replacements)


def adapt_operations(
    raw_operations: Any,
    snapshot: WorkspaceSnapshot,
    workspace_root: Path | None = None,
) -> ProposalAdaptation:
    """Convert supported host dialects to canonical full-file operations.

    Supported input is intentionally finite and documented: canonical Deep Dev
    fields, the native harness ``target_path``/``write`` shape, bounded
    ``exact_replace`` edits, and the two former IDE aliases. Ambiguous or
    malformed input is rejected before patch serialization; this preserves the
    snapshot and scope checks downstream.
    """
    if not isinstance(raw_operations, list):
        raise ProposalAdapterError("Host proposal operations must be a list.")

    output: List[Dict[str, Any]] = []
    ignored = normalized_fields = normalized_writes = exact_replacements = 0
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
        if action == "exact_replace":
            expanded, count = _expand_exact_replace(
                raw, index, file_path, snapshot, workspace_root,
            )
            output.append({"file_path": file_path, "action": "modify", "content": expanded})
            exact_replacements += count
            continue
        if action == "write":
            entry = snapshot.files.get(file_path)
            action = "modify" if entry and entry.exists else "create"
            normalized_writes += 1
        if action in {"create", "modify"} and not isinstance(raw_content, str):
            raise ProposalAdapterError(f"Operation {index} requires text content for '{file_path}'.")
        if action == "delete":
            raw_content = ""
        output.append({"file_path": file_path, "action": action, "content": raw_content if isinstance(raw_content, str) else ""})

    return ProposalAdaptation(
        output, ignored, normalized_fields, normalized_writes, exact_replacements,
    )
