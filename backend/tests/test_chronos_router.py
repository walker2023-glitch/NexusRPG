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

from pydantic import ValidationError

from backend.routes.actor_router import SceneRequest


def test_request_sizes_are_bounded() -> None:
    with pytest.raises(ValidationError):
        CommitRequest(scene_text="x" * 100_001)

    with pytest.raises(ValidationError):
        SceneRequest(player_action="x" * 4_001)

    with pytest.raises(ValidationError):
        SceneRequest(player_action="go", scene_history=["scene"] * 51)

    with pytest.raises(ValidationError):
        SceneRequest(
            player_action="go",
            transition_operations=[{"op": "add", "path": "/metadata/active_location", "value": "x"}] * 33,
        )

from backend.routes.architect_router import ExtractRequest, ScrapeRequest
from backend.routes.import_router import _MAX_IMAGE_BYTES, _MAX_TEXT_BYTES


def test_architect_request_sizes_are_bounded() -> None:
    with pytest.raises(ValidationError):
        ExtractRequest(raw_text="x" * 100_001)

    with pytest.raises(ValidationError):
        ScrapeRequest(url="https://example.com", universe_context="x" * 2_001)


def test_import_limits_are_explicit() -> None:
    assert _MAX_IMAGE_BYTES == 10 * 1024 * 1024
    assert _MAX_TEXT_BYTES == 2 * 1024 * 1024

from backend.routes.world_router import ActiveLocationUpdate, NodePropertiesUpdate


def test_world_write_payloads_are_bounded() -> None:
    with pytest.raises(ValidationError):
        NodePropertiesUpdate(properties={str(i): i for i in range(65)})

    with pytest.raises(ValidationError):
        ActiveLocationUpdate(location_id="x" * 129)
