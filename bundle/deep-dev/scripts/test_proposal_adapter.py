import hashlib
from pathlib import Path

from snapshot import SnapshotFileEntry, WorkspaceSnapshot
from proposal_adapter import ProposalAdapterError, adapt_operations

import pytest


def snapshot() -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        run_id="adapter-test",
        allowed_paths=["existing.py", "new.py"],
        files={
            "existing.py": SnapshotFileEntry(exists=True, base_sha256="x"),
            "new.py": SnapshotFileEntry(exists=False, base_sha256=None),
        },
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([{"file_path": "existing.py", "action": "modify", "content": "x\n"}], "modify"),
        ([{"target_path": "existing.py", "action": "write", "content": "x\n"}], "modify"),
        ([{"path": "new.py", "operation": "write", "new_content": "x\n"}], "create"),
        ([{"file_path": "new.py", "action": "create", "content_or_diff": "x\n"}], "create"),
    ],
)
def test_supported_host_dialects_become_one_canonical_contract(raw, expected) -> None:
    result = adapt_operations(raw, snapshot())
    assert result.operations[0]["action"] == expected
    assert set(result.operations[0]) == {"file_path", "action", "content"}


def test_only_inert_empty_placeholder_is_ignored() -> None:
    result = adapt_operations([{}, {"file_path": "new.py", "action": "write", "content": "x"}], snapshot())
    assert result.ignored_empty_noops == 1
    assert len(result.operations) == 1


@pytest.mark.parametrize(
    "raw",
    [
        [{"file_path": "a.py", "path": "b.py", "action": "write", "content": "x"}],
        [{"file_path": "new.py", "action": "write"}],
        [{"file_path": "../new.py", "action": "write", "content": "x"}],
        [{"file_path": "new.py", "action": "replace", "content": "x"}],
    ],
)
def test_ambiguous_or_unsafe_proposals_fail_closed(raw) -> None:
    with pytest.raises(ProposalAdapterError):
        adapt_operations(raw, snapshot())


def exact_replace_snapshot(workspace: Path) -> WorkspaceSnapshot:
    content = "def first():\n    return 1\n\ndef second():\n    return 2\n"
    (workspace / "large.py").write_text(content, encoding="utf-8")
    return WorkspaceSnapshot(
        run_id="exact-replace-test",
        allowed_paths=["large.py"],
        files={
            "large.py": SnapshotFileEntry(
                exists=True,
                base_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            ),
        },
    )


def test_exact_replace_reconstructs_full_file_from_compact_edits(tmp_path: Path) -> None:
    sealed = exact_replace_snapshot(tmp_path)
    result = adapt_operations(
        [{
            "file_path": "large.py",
            "action": "exact_replace",
            "replacements": [
                {"old_text": "return 1", "new_text": "return 10"},
                {"old_text": "return 2", "new_text": "return 20"},
            ],
        }],
        sealed,
        tmp_path,
    )
    assert result.operations == [{
        "file_path": "large.py",
        "action": "modify",
        "content": "def first():\n    return 10\n\ndef second():\n    return 20\n",
    }]
    assert result.expanded_exact_replacements == 2


@pytest.mark.parametrize(
    "old_text",
    ["return", "not present"],
)
def test_exact_replace_requires_one_unique_snapshot_match(tmp_path: Path, old_text: str) -> None:
    sealed = exact_replace_snapshot(tmp_path)
    with pytest.raises(ProposalAdapterError, match="match exactly once"):
        adapt_operations(
            [{
                "file_path": "large.py",
                "action": "exact_replace",
                "old_text": old_text,
                "new_text": "changed",
            }],
            sealed,
            tmp_path,
        )


def test_exact_replace_rejects_stale_snapshot(tmp_path: Path) -> None:
    sealed = exact_replace_snapshot(tmp_path)
    (tmp_path / "large.py").write_text("changed after snapshot\n", encoding="utf-8")
    with pytest.raises(ProposalAdapterError, match="stale snapshot"):
        adapt_operations(
            [{
                "file_path": "large.py",
                "action": "exact_replace",
                "old_text": "return 1",
                "new_text": "return 10",
            }],
            sealed,
            tmp_path,
        )


def test_exact_replace_rejects_ambiguous_full_content_shape(tmp_path: Path) -> None:
    sealed = exact_replace_snapshot(tmp_path)
    with pytest.raises(ProposalAdapterError, match="cannot combine"):
        adapt_operations(
            [{
                "file_path": "large.py",
                "action": "exact_replace",
                "content": "full replacement",
                "old_text": "return 1",
                "new_text": "return 10",
            }],
            sealed,
            tmp_path,
        )
