"""Security regression tests for Chronos refs."""

from pathlib import Path

import pytest

from backend.chronos.commit_engine import ChronosCommit, ChronosEngine


def test_commit_ids_cannot_escape_objects_directory(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")

    for unsafe_id in ("../outside", "..", "/absolute", "nested/id", ""):
        with pytest.raises(ValueError, match="Invalid commit ID"):
            engine.reconstruct_state(unsafe_id)


def test_branch_names_cannot_escape_refs_directory(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    root_id = engine.commit("Root.", {"metadata": {}})

    for unsafe_name in ("../outside", "..", ".", "/absolute", "branch/name", ""):
        with pytest.raises(ValueError, match="Invalid branch name"):
            engine.fork(root_id, unsafe_name)

        with pytest.raises(ValueError, match="Invalid branch name"):
            engine.get_branch_tip(unsafe_name)

        with pytest.raises(ValueError, match="Invalid branch name"):
            engine.update_branch_tip(unsafe_name, root_id)


def test_branch_names_accept_bounded_safe_names(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    root_id = engine.commit("Root.", {"metadata": {}})

    engine.fork(root_id, "main")
    engine.fork(root_id, "alternate-1")
    assert engine.get_branch_tip("main") == root_id
    assert engine.get_branch_tip("alternate-1") == root_id

def test_branch_tip_cannot_point_to_unknown_commit(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    with pytest.raises(ValueError, match="unknown commit"):
        engine.update_branch_tip("main", "missing-commit")


def test_malformed_branch_ref_is_rejected_on_read(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    ref_path = engine._refs_dir / "main"
    ref_path.write_text("../escape", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid commit ID"):
        engine.get_branch_tip("main")


def test_reconstruction_rejects_object_identity_mismatch(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    commit_id = engine.commit("Root.", {"metadata": {}, "nodes": [], "edges": []})
    original = engine._read_object(commit_id)
    tampered = ChronosCommit(
        commit_id="different-object",
        parent_id=original.parent_id,
        scene_text=original.scene_text,
        depth=original.depth,
        timestamp=original.timestamp,
        campaign_id=original.campaign_id,
        snapshot=original.snapshot,
        delta=original.delta,
    )
    (engine._objects_dir / commit_id).write_bytes(tampered.to_compressed_bytes())

    with pytest.raises(RuntimeError, match="identity mismatch"):
        engine.reconstruct_state(commit_id)


def test_reconstruction_rejects_campaign_lineage_tampering(tmp_path: Path) -> None:
    engine = ChronosEngine(tmp_path / "chronos")
    root_state = {"metadata": {"campaign_id": "alpha"}, "nodes": [], "edges": []}
    root_id = engine.commit("Root.", root_state)
    child_state = {"metadata": {"campaign_id": "alpha"}, "nodes": [], "edges": []}
    child_id = engine.commit("Child.", child_state, parent_id=root_id)
    original = engine._read_object(child_id)
    tampered = ChronosCommit(
        commit_id=original.commit_id,
        parent_id=original.parent_id,
        scene_text=original.scene_text,
        depth=original.depth,
        timestamp=original.timestamp,
        campaign_id="beta",
        snapshot=original.snapshot,
        delta=original.delta,
    )
    (engine._objects_dir / child_id).write_bytes(tampered.to_compressed_bytes())

    with pytest.raises(RuntimeError, match="campaign lineage"):
        engine.reconstruct_state(child_id)


def test_corrupted_index_fails_closed(tmp_path: Path) -> None:
    storage = tmp_path / "chronos"
    storage.mkdir()
    (storage / "index.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="index is corrupted"):
        ChronosEngine(storage)
