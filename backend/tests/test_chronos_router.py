"""Endpoint-level regression tests for Chronos input validation."""

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.routes.chronos_router import (
    CheckoutResponse,
    CommitRequest,
    ForkRequest,
    checkout,
    create_commit,
    fork_branch,
    reconstruct,
)
from fastapi import BackgroundTasks


@pytest.fixture
def chronos_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "world_state_path", str(tmp_path / "world_state.json"))
    monkeypatch.setattr(settings, "chronos_path", str(tmp_path / "chronos"))


@pytest.mark.asyncio
async def test_commit_reconstruct_and_fork_round_trip(chronos_paths: None) -> None:
    created = await create_commit(
        CommitRequest(
            scene_text="The party enters the archive.",
            update_branch="main",
        )
    )

    state = await reconstruct(created.commit_id)
    assert state["metadata"]["universe"] == ""

    forked = await fork_branch(
        ForkRequest(from_commit_id=created.commit_id, branch_name="alternate-1")
    )
    assert forked.commit_id == created.commit_id


@pytest.mark.asyncio
async def test_reconstruct_rejects_unsafe_commit_id(chronos_paths: None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await reconstruct("../outside")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_checkout_rejects_unsafe_commit_id(chronos_paths: None) -> None:
    with pytest.raises(HTTPException) as exc_info:
        await checkout("../outside", BackgroundTasks())

    assert exc_info.value.status_code == 400
