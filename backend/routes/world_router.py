"""World-state REST API — direct graph read/write used by the frontend map view."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import world_state as ws
from backend.chronos.commit_engine import ChronosEngine
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── Request models ───────────────────────────────────────────────────────────


class NodePropertiesUpdate(BaseModel):
    """PATCH body for updating a node's mutable properties."""
    properties: dict[str, Any]


class ActiveLocationUpdate(BaseModel):
    """PATCH body for changing the active scene location."""
    location_id: str | None


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/world-state")
async def get_world_state() -> dict[str, Any]:
    """Return the full world_state.json as a JSON object.

    Used by the React Flow map on mount and after every mutating operation.
    """
    return await ws.load(settings.world_state_path)


@router.patch("/nodes/{node_id}")
async def update_node_properties(node_id: str, body: NodePropertiesUpdate) -> dict[str, Any]:
    """Merge *body.properties* into the node identified by *node_id*.

    Existing properties not present in the patch are preserved.
    Returns the updated node dict.
    """
    state = await ws.load(settings.world_state_path)
    node = ws.get_node(state, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

    node.setdefault("properties", {}).update(body.properties)
    ws.upsert_node(state, node)
    await ws.save(settings.world_state_path, state)
    logger.info("world /nodes PATCH → %s (%d props)", node_id, len(body.properties))
    return node


@router.patch("/active-location")
async def set_active_location(body: ActiveLocationUpdate) -> dict[str, Any]:
    """Update the active_location field in world_state metadata.

    Called by the Play page when the player clicks the location chip and
    selects a different scene location from the dropdown.
    """
    state = await ws.load(settings.world_state_path)
    ws.set_active_location(state, body.location_id)
    await ws.save(settings.world_state_path, state)
    logger.info("world /active-location PATCH → %s", body.location_id)
    return {"active_location": body.location_id}


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """Deep health probe used by the frontend nav bar status indicator.

    Returns the liveness of each subsystem without raising errors — callers
    receive a JSON object with boolean/int fields they can render directly.
    All checks are run concurrently to keep latency low.
    """
    async def check_ollama() -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{settings.ollama_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def check_world_state() -> bool:
        try:
            state = await ws.load(settings.world_state_path)
            return isinstance(state, dict)
        except Exception:
            return False

    async def check_lancedb() -> bool:
        try:
            import lancedb
            conn = await asyncio.to_thread(lancedb.connect, settings.lancedb_path)
            await asyncio.to_thread(conn.table_names)
            return True
        except Exception:
            return False

    def count_chronos_commits() -> int:
        try:
            engine = ChronosEngine(Path(settings.chronos_path))
            return len(engine._index)
        except Exception:
            return 0

    ollama_ok, world_ok, lancedb_ok, commit_count = await asyncio.gather(
        check_ollama(),
        check_world_state(),
        check_lancedb(),
        asyncio.to_thread(count_chronos_commits),
    )

    return {
        "ollama": ollama_ok,
        "lancedb": lancedb_ok,
        "world_state_loaded": world_ok,
        "chronos_commits": commit_count,
    }


@router.delete("/edges")
async def delete_edge(source: str, target: str, relation: str) -> dict[str, str]:
    """Remove a specific edge from the world graph by its (source, target, relation) triple.

    Returns 404 if no matching edge is found.
    """
    state = await ws.load(settings.world_state_path)
    before = len(state.get("edges", []))
    state["edges"] = [
        e for e in state.get("edges", [])
        if not (e.get("source") == source and e.get("target") == target and e.get("relation") == relation)
    ]
    if len(state["edges"]) == before:
        raise HTTPException(status_code=404, detail=f"Edge not found: {source} -[{relation}]→ {target}")

    await ws.save(settings.world_state_path, state)
    logger.info("world /edges DELETE → %s -[%s]→ %s", source, relation, target)
    return {"deleted": f"{source} -[{relation}]→ {target}"}
