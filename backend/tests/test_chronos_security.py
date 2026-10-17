"""Security regression tests for Chronos refs."""

from pathlib import Path

import pytest

from backend.chronos.commit_engine import ChronosEngine


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
