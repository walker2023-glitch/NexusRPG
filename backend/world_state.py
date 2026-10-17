"""Async helpers for reading and writing world_state.json — the single graph truth."""

import asyncio
import json
import logging
import os
from typing import Any

import aiofiles

logger = logging.getLogger(__name__)

_EMPTY_STATE: dict[str, Any] = {
    "nodes": [],
    "edges": [],
    "metadata": {
        "universe": "",
        "active_location": None,
        "session_id": None,
    },
}


def _empty_state() -> dict[str, Any]:
    """Return a fresh copy of the empty state template."""
    return json.loads(json.dumps(_EMPTY_STATE))


async def load(path: str) -> dict[str, Any]:
    """Read and parse *path* as JSON, returning an empty state dict if the file is missing.

    All I/O is performed asynchronously via aiofiles.
    """
    try:
        async with aiofiles.open(path, mode="r", encoding="utf-8") as fh:
            raw = await fh.read()
        state: dict[str, Any] = json.loads(raw)
        logger.debug("world_state.load → %d nodes, %d edges from %s",
                     len(state.get("nodes", [])), len(state.get("edges", [])), path)
        return state
    except FileNotFoundError:
        logger.warning("world_state.load → %s not found, returning empty state", path)
        return _empty_state()
    except json.JSONDecodeError as exc:
        logger.error("world_state.load → JSON decode error in %s: %s", path, exc)
        raise


async def save(path: str, state: dict[str, Any]) -> None:
    """Atomically write *state* to *path* via a sibling .tmp file and os.replace.

    Writing to a temp file first prevents corruption if the process is killed mid-write.
    The os.replace call is performed in a thread pool to avoid blocking the event loop.
    """
    tmp_path = path + ".tmp"
    serialised = json.dumps(state, ensure_ascii=False, indent=2)
    async with aiofiles.open(tmp_path, mode="w", encoding="utf-8") as fh:
        await fh.write(serialised)
    await asyncio.to_thread(os.replace, tmp_path, path)
    logger.debug("world_state.save → wrote %d nodes, %d edges to %s",
                 len(state.get("nodes", [])), len(state.get("edges", [])), path)


def get_node(state: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    """Return the node with *node_id* from *state*, or None if not found."""
    for node in state.get("nodes", []):
        if node.get("id") == node_id:
            return node
    return None


def upsert_node(state: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a node in *state* by its ``id`` field.

    If a node with the same id already exists it is updated in-place (its list
    position is preserved). Otherwise *node* is appended. Returns *state*.
    """
    node_id = node.get("id")
    if node_id is None:
        raise ValueError("upsert_node: node dict must contain an 'id' field")

    nodes: list[dict[str, Any]] = state.setdefault("nodes", [])
    for idx, existing in enumerate(nodes):
        if existing.get("id") == node_id:
            nodes[idx] = node
            logger.debug("upsert_node → updated node %s", node_id)
            return state

    nodes.append(node)
    logger.debug("upsert_node → inserted node %s", node_id)
    return state


def delete_node(state: dict[str, Any], node_id: str) -> None:
    """Remove the node with *node_id* from *state* and purge all edges that reference it.

    Modifies *state* in-place; no-ops silently if the node does not exist.
    """
    before_nodes = len(state.get("nodes", []))
    state["nodes"] = [n for n in state.get("nodes", []) if n.get("id") != node_id]

    before_edges = len(state.get("edges", []))
    state["edges"] = [
        e for e in state.get("edges", [])
        if e.get("source") != node_id and e.get("target") != node_id
    ]

    removed_nodes = before_nodes - len(state["nodes"])
    removed_edges = before_edges - len(state["edges"])
    logger.debug(
        "delete_node → %s: removed %d node(s), %d edge(s)",
        node_id, removed_nodes, removed_edges,
    )


def get_active_location(state: dict[str, Any]) -> str | None:
    """Return the current active_location id from state metadata, or None."""
    return state.get("metadata", {}).get("active_location")


def set_active_location(state: dict[str, Any], location_id: str | None) -> None:
    """Set the active_location field in state metadata.

    Passing None clears the active location. Modifies *state* in-place.
    """
    state.setdefault("metadata", {})["active_location"] = location_id
    logger.debug("set_active_location → %s", location_id)
