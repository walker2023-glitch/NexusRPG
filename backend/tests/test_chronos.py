"""pytest-asyncio tests for backend/chronos/commit_engine.py.

All tests use a fresh temporary directory per test so no state leaks between runs.
"""

import copy
import os
from pathlib import Path

import pytest

from backend.chronos.commit_engine import (
    SNAPSHOT_INTERVAL,
    ChronosCommit,
    ChronosEngine,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_engine(tmp_path: Path) -> ChronosEngine:
    """Return a ChronosEngine backed by a fresh temp directory."""
    return ChronosEngine(tmp_path / "chronos")


@pytest.fixture
def base_state() -> dict:
    """Minimal world_state dict used as the snapshot/delta source."""
    return {
        "nodes": [
            {"id": "char_hero", "label": "Hero", "entity_type": "char", "properties": {"health": 100}},
            {"id": "loc_village", "label": "Village", "entity_type": "loc", "properties": {}},
        ],
        "edges": [{"source": "char_hero", "target": "loc_village", "relation": "located_in"}],
        "metadata": {"universe": "Test World", "active_location": "loc_village", "session_id": None},
    }


# ─── ChronosCommit serialisation ──────────────────────────────────────────────


def test_commit_roundtrip_snapshot(base_state: dict) -> None:
    """A snapshot commit survives a compress → decompress round-trip unchanged."""
    commit = ChronosCommit(
        commit_id="aaa",
        parent_id=None,
        scene_text="The hero stands at the village gate.",
        depth=0,
        timestamp=1_000_000.0,
        snapshot=copy.deepcopy(base_state),
        delta=None,
    )
    restored = ChronosCommit.from_compressed_bytes(commit.to_compressed_bytes())
    assert restored.snapshot == base_state
    assert restored.delta is None
    assert restored.is_snapshot is True


def test_commit_roundtrip_delta() -> None:
    """A delta commit survives a compress → decompress round-trip unchanged."""
    patch = [{"op": "replace", "path": "/nodes/0/properties/health", "value": 80}]
    commit = ChronosCommit(
        commit_id="bbb",
        parent_id="aaa",
        scene_text="The hero is wounded.",
        depth=1,
        timestamp=1_000_001.0,
        snapshot=None,
        delta=patch,
    )
    restored = ChronosCommit.from_compressed_bytes(commit.to_compressed_bytes())
    assert restored.delta == patch
    assert restored.snapshot is None
    assert restored.is_snapshot is False


def test_commit_xor_invariant(base_state: dict) -> None:
    """ChronosCommit raises if both snapshot and delta are provided."""
    with pytest.raises(ValueError, match="exactly one"):
        ChronosCommit(
            commit_id="bad",
            parent_id=None,
            scene_text="Broken commit.",
            depth=0,
            timestamp=0.0,
            snapshot=base_state,
            delta=[],
        )


def test_commit_xor_invariant_neither() -> None:
    """ChronosCommit raises if neither snapshot nor delta is provided."""
    with pytest.raises(ValueError, match="exactly one"):
        ChronosCommit(
            commit_id="bad",
            parent_id=None,
            scene_text="Broken commit.",
            depth=0,
            timestamp=0.0,
            snapshot=None,
            delta=None,
        )


# ─── Snapshot policy ──────────────────────────────────────────────────────────


def test_depth_zero_is_snapshot(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """The root commit (depth 0, no parent) must always be a snapshot."""
    cid = tmp_engine.commit("Root scene.", base_state)
    meta = tmp_engine.get_commit_metadata(cid)
    assert meta["depth"] == 0
    assert meta["is_snapshot"] is True


def test_intermediate_commits_are_deltas(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """Commits at depths 1 through SNAPSHOT_INTERVAL-1 must be deltas, not snapshots."""
    state = copy.deepcopy(base_state)
    parent = tmp_engine.commit("Scene 0 (root).", state)

    for i in range(1, SNAPSHOT_INTERVAL):
        state = copy.deepcopy(base_state)
        state["nodes"][0]["properties"]["health"] = 100 - i * 5
        cid = tmp_engine.commit(f"Scene {i}.", state, parent_id=parent)
        meta = tmp_engine.get_commit_metadata(cid)
        assert meta["depth"] == i, f"Expected depth {i}, got {meta['depth']}"
        assert meta["is_snapshot"] is False, f"Depth {i} should be a delta"
        parent = cid


def test_snapshot_at_snapshot_interval(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """A commit at exactly depth=SNAPSHOT_INTERVAL must be a snapshot."""
    state = copy.deepcopy(base_state)
    parent = tmp_engine.commit("Root.", state)

    for i in range(1, SNAPSHOT_INTERVAL + 1):
        state = copy.deepcopy(base_state)
        state["nodes"][0]["properties"]["health"] = 100 - i * 3
        cid = tmp_engine.commit(f"Scene {i}.", state, parent_id=parent)
        meta = tmp_engine.get_commit_metadata(cid)
        if i == SNAPSHOT_INTERVAL:
            assert meta["is_snapshot"] is True, f"Depth {SNAPSHOT_INTERVAL} must be a snapshot"
        parent = cid


def test_campaign_id_is_preserved_in_metadata(tmp_engine: ChronosEngine) -> None:
    state = {"metadata": {"campaign_id": "campaign-a"}}
    commit_id = tmp_engine.commit("Campaign A.", state)
    assert tmp_engine.get_commit_metadata(commit_id)["campaign_id"] == "campaign-a"


def test_campaign_cannot_attach_to_parent_from_another_campaign(
    tmp_engine: ChronosEngine,
) -> None:
    parent_id = tmp_engine.commit(
        "Campaign A root.",
        {"metadata": {"campaign_id": "campaign-a"}},
    )

    with pytest.raises(ValueError, match="Campaign mismatch"):
        tmp_engine.commit(
            "Campaign B child.",
            {"metadata": {"campaign_id": "campaign-b"}},
            parent_id=parent_id,
        )


# ─── reconstruct_state ────────────────────────────────────────────────────────


def test_reconstruct_after_deltas(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """reconstruct_state must return the exact state passed to commit after 5 delta commits."""
    state = copy.deepcopy(base_state)
    parent = tmp_engine.commit("Root.", state)

    # Apply 5 successive mutations — all will be stored as deltas (depths 1–5).
    target_state = copy.deepcopy(base_state)
    for i in range(1, SNAPSHOT_INTERVAL):  # 1..5 (SNAPSHOT_INTERVAL=6)
        target_state["nodes"][0]["properties"]["health"] = 100 - i * 10
        target_state["metadata"]["session_id"] = f"session_{i}"
        cid = tmp_engine.commit(
            f"Scene {i} — health dropped.",
            copy.deepcopy(target_state),
            parent_id=parent,
        )
        parent = cid

    reconstructed = tmp_engine.reconstruct_state(cid)
    assert reconstructed == target_state, (
        "Reconstructed state must match the state passed to the final commit exactly."
    )


def test_reconstruct_root_snapshot(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """reconstruct_state of the root commit returns the original snapshot verbatim."""
    cid = tmp_engine.commit("Root scene.", copy.deepcopy(base_state))
    assert tmp_engine.reconstruct_state(cid) == base_state


def test_reconstruct_across_snapshot_boundary(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """reconstruct_state works correctly for a delta that follows a periodic snapshot."""
    state = copy.deepcopy(base_state)
    parent = tmp_engine.commit("Root.", state)

    # Drive to depth SNAPSHOT_INTERVAL (which will be a snapshot)…
    for i in range(1, SNAPSHOT_INTERVAL + 1):
        state = copy.deepcopy(base_state)
        state["nodes"][0]["properties"]["health"] = 100 - i
        cid = tmp_engine.commit(f"S{i}.", copy.deepcopy(state), parent_id=parent)
        parent = cid

    # …then one more delta on top of the periodic snapshot.
    final_state = copy.deepcopy(state)
    final_state["nodes"][0]["properties"]["health"] = 42
    last_cid = tmp_engine.commit("Final delta.", copy.deepcopy(final_state), parent_id=parent)

    reconstructed = tmp_engine.reconstruct_state(last_cid)
    assert reconstructed["nodes"][0]["properties"]["health"] == 42


# ─── fork ─────────────────────────────────────────────────────────────────────


def test_fork_creates_ref_file(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """fork() writes a ref file in refs/ pointing to the correct commit_id."""
    cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    tmp_engine.fork(cid, "alternate-timeline")

    ref_path = tmp_engine._refs_dir / "alternate-timeline"
    assert ref_path.exists(), "refs/alternate-timeline file must exist after fork"
    assert ref_path.read_text().strip() == cid


def test_fork_creates_zero_new_objects(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """fork() must not create any new object files — it is a zero-copy operation."""
    cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    objects_before = set(tmp_engine._objects_dir.iterdir())

    tmp_engine.fork(cid, "branch-a")
    tmp_engine.fork(cid, "branch-b")

    objects_after = set(tmp_engine._objects_dir.iterdir())
    assert objects_before == objects_after, "fork must not create any new object files"


def test_fork_returns_from_commit_id(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """fork() must return the from_commit_id unchanged."""
    cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    returned = tmp_engine.fork(cid, "test-branch")
    assert returned == cid


def test_fork_nonexistent_commit_raises(tmp_engine: ChronosEngine) -> None:
    """fork() must raise ValueError when from_commit_id is not in the index."""
    with pytest.raises(ValueError, match="unknown commit"):
        tmp_engine.fork("does-not-exist", "orphan-branch")


# ─── prune_orphaned_branches ──────────────────────────────────────────────────


def test_prune_removes_unreachable_object(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """prune_orphaned_branches deletes an object file that is not reachable from any ref."""
    # Create two commits: root and child.
    root_cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    child_state = copy.deepcopy(base_state)
    child_state["nodes"][0]["properties"]["health"] = 50
    child_cid = tmp_engine.commit("Child.", child_state, parent_id=root_cid)

    # Point a branch ref only at the child — the root is reachable (ancestor).
    tmp_engine.fork(child_cid, "main")

    # Create an orphan commit that is NOT reachable from any ref.
    orphan_state = copy.deepcopy(base_state)
    orphan_state["metadata"]["universe"] = "Orphan Universe"
    orphan_cid = tmp_engine.commit("Orphan.", orphan_state, parent_id=None)

    # Manually break the ref so the orphan has no branch pointing at it.
    # (The commit was added to the index by commit(), but no ref was created.)
    assert (tmp_engine._objects_dir / orphan_cid).exists()

    deleted = tmp_engine.prune_orphaned_branches()

    assert deleted == 1, f"Expected 1 object deleted, got {deleted}"
    assert not (tmp_engine._objects_dir / orphan_cid).exists(), "Orphan object file must be gone"
    assert (tmp_engine._objects_dir / root_cid).exists(), "Root (ancestor) must survive"
    assert (tmp_engine._objects_dir / child_cid).exists(), "Child (branch tip) must survive"


def test_prune_with_no_refs_deletes_all(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """If there are no refs at all, every object is unreachable and should be pruned."""
    tmp_engine.commit("Scene A.", copy.deepcopy(base_state))
    tmp_engine.commit("Scene B.", copy.deepcopy(base_state))

    # No fork() calls → no refs exist.
    deleted = tmp_engine.prune_orphaned_branches()

    assert deleted == 2
    assert list(tmp_engine._objects_dir.iterdir()) == [], "All objects must be deleted"


def test_prune_returns_zero_when_nothing_to_delete(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """prune_orphaned_branches returns 0 when every object is reachable."""
    cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    tmp_engine.fork(cid, "main")

    deleted = tmp_engine.prune_orphaned_branches()
    assert deleted == 0


# ─── Index persistence ────────────────────────────────────────────────────────


def test_index_survives_engine_reload(tmp_path: Path, base_state: dict) -> None:
    """The index.json must be loadable by a fresh ChronosEngine instance."""
    storage = tmp_path / "chronos"
    engine1 = ChronosEngine(storage)
    cid = engine1.commit("Root.", copy.deepcopy(base_state))

    # Instantiate a second engine pointing at the same storage directory.
    engine2 = ChronosEngine(storage)
    meta = engine2.get_commit_metadata(cid)
    assert meta["commit_id"] == cid
    assert meta["is_snapshot"] is True


def test_get_branch_tip_returns_none_for_unknown(tmp_engine: ChronosEngine) -> None:
    """get_branch_tip returns None for a branch that has never been created."""
    assert tmp_engine.get_branch_tip("nonexistent") is None


def test_update_branch_tip_round_trip(tmp_engine: ChronosEngine, base_state: dict) -> None:
    """update_branch_tip then get_branch_tip returns the same commit_id."""
    cid = tmp_engine.commit("Root.", copy.deepcopy(base_state))
    tmp_engine.update_branch_tip("main", cid)
    assert tmp_engine.get_branch_tip("main") == cid
