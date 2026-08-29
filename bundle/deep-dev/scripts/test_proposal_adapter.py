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
