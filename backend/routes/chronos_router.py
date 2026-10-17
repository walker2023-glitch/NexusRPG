"""Chronos REST API — branch management, commit history, and timeline navigation."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from backend.chronos.commit_engine import ChronosEngine
from backend.config import settings
from backend import world_state as ws

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _engine() -> ChronosEngine:
    """Return a ChronosEngine rooted at the configured chronos storage path."""
    return ChronosEngine(Path(settings.chronos_path))


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CommitRequest(BaseModel):
    """Payload for recording a new narrative commit."""

    scene_text: str = Field(..., description="Prose description of the current scene moment.")
    parent_id: str | None = Field(None, description="Parent commit ID, or null for a root commit.")
    choice_taken: str | None = Field(None, description="Player choice that led to this state.")
    branch_label: str | None = Field(None, description="Optional human-readable branch name.")
    update_branch: str | None = Field(
        None,
        description="If provided, the named branch ref tip is advanced to the new commit.",
    )


class CommitResponse(BaseModel):
    """Response after successfully recording a commit."""

    commit_id: str
    depth: int
    is_snapshot: bool


class ForkRequest(BaseModel):
    """Payload for creating a named branch from an existing commit."""

    from_commit_id: str = Field(..., description="The commit the new branch will start from.")
    branch_name: str = Field(..., description="Name of the new branch ref.")


class ForkResponse(BaseModel):
    """Response after a successful fork operation."""

    branch_name: str
    commit_id: str


class CheckoutResponse(BaseModel):
    """Response after checking out a commit — includes the reconstructed world state."""

    commit_id: str
    depth: int
    world_state: dict[str, Any]


class PruneResponse(BaseModel):
    """Response after a prune operation."""

    deleted_objects: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/commit", response_model=CommitResponse, status_code=201)
async def create_commit(body: CommitRequest) -> CommitResponse:
    """Record a new narrative moment in the Chronos DAG.

    Loads the current world_state from disk, computes a snapshot or delta
    commit depending on the depth/SNAPSHOT_INTERVAL policy, and optionally
    advances a named branch ref.
    """
    state = await ws.load(settings.world_state_path)

    try:
        engine = _engine()
        commit_id = await asyncio.to_thread(
            engine.commit,
            body.scene_text,
            state,
            body.parent_id,
            body.choice_taken,
            body.branch_label,
        )

        if body.update_branch:
            await asyncio.to_thread(engine.update_branch_tip, body.update_branch, commit_id)

        meta = await asyncio.to_thread(engine.get_commit_metadata, commit_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("chronos /commit → %s depth=%d", commit_id, meta["depth"])
    return CommitResponse(
        commit_id=commit_id,
        depth=meta["depth"],
        is_snapshot=meta["is_snapshot"],
    )


@router.get("/reconstruct/{commit_id}", response_model=dict[str, Any])
async def reconstruct(commit_id: str) -> dict[str, Any]:
    """Reconstruct and return the full world_state at *commit_id*.

    Replays at most SNAPSHOT_INTERVAL - 1 deltas; result is never persisted
    unless the caller explicitly issues a subsequent /checkout.
    """
    try:
        engine = _engine()
        state = await asyncio.to_thread(engine.reconstruct_state, commit_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Commit not found: {commit_id}")
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return state


@router.post("/fork", response_model=ForkResponse, status_code=201)
async def fork_branch(body: ForkRequest) -> ForkResponse:
    """Create a named branch ref pointing to an existing commit.

    Zero-copy: no commit objects are duplicated.  The branch ref file is
    written to refs/{branch_name} containing the commit_id string.
    """
    try:
        engine = _engine()
        commit_id = await asyncio.to_thread(engine.fork, body.from_commit_id, body.branch_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    logger.info("chronos /fork → branch '%s' at %s", body.branch_name, commit_id)
    return ForkResponse(branch_name=body.branch_name, commit_id=commit_id)


@router.get("/timeline", response_model=list[dict[str, Any]])
async def get_timeline() -> list[dict[str, Any]]:
    """Return lightweight metadata for every commit in the DAG.

    Results are sorted by depth then timestamp.  No full world states are
    loaded; the response is served entirely from index.json.
    """
    engine = _engine()
    return await asyncio.to_thread(engine.list_all_metadata)


@router.get("/branches", response_model=dict[str, str])
async def get_branches() -> dict[str, str]:
    """Return a mapping of branch_name → commit_id for all existing refs."""
    engine = _engine()
    return await asyncio.to_thread(engine.list_branches)


@router.post("/checkout/{commit_id}", response_model=CheckoutResponse)
async def checkout(commit_id: str, background_tasks: BackgroundTasks) -> CheckoutResponse:
    """Reconstruct the world_state at *commit_id* and persist it as the active state.

    The reconstructed state is written to world_state.json in the background
    so the HTTP response is not blocked on disk I/O.  The caller receives the
    full reconstructed state in the response body for immediate UI update.
    """
    try:
        engine = _engine()
        state = await asyncio.to_thread(engine.reconstruct_state, commit_id)
        meta = await asyncio.to_thread(engine.get_commit_metadata, commit_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Commit not found: {commit_id}")
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    background_tasks.add_task(ws.save, settings.world_state_path, state)
    logger.info("chronos /checkout → %s depth=%d", commit_id, meta["depth"])

    return CheckoutResponse(
        commit_id=commit_id,
        depth=meta["depth"],
        world_state=state,
    )


@router.delete("/prune", response_model=PruneResponse)
async def prune(background_tasks: BackgroundTasks) -> PruneResponse:
    """Delete commit objects unreachable from any branch ref.

    DFS is performed from every ref tip; orphaned object files are removed and
    the index is cleaned up.  The prune runs synchronously (wrapped in a
    thread) and returns the count of deleted objects.
    """
    engine = _engine()
    deleted = await asyncio.to_thread(engine.prune_orphaned_branches)
    logger.info("chronos /prune → %d object(s) deleted", deleted)
    return PruneResponse(deleted_objects=deleted)
