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


def validate_state(state: dict[str, Any]) -> None:
    """Validate the stable graph envelope before it is read or persisted.

    This intentionally validates structure and identifiers, not arbitrary node
    properties, so imported worlds remain flexible while malformed graph data
    cannot corrupt Chronos reconstruction or frontend rendering.
    """
    if not isinstance(state, dict):
        raise ValueError("World state must be a JSON object.")

    nodes = state.get("nodes", [])
    edges = state.get("edges", [])
    metadata = state.get("metadata", {})
    if not isinstance(nodes, list):
        raise ValueError("World state 'nodes' must be a list.")
    if not isinstance(edges, list):
        raise ValueError("World state 'edges' must be a list.")
    if not isinstance(metadata, dict):
        raise ValueError("World state 'metadata' must be an object.")

    node_ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Every world-state node must be an object.")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
            raise ValueError("Every world-state node must have a non-empty string id of at most 128 characters.")
        if node_id in node_ids:
            raise ValueError(f"Duplicate world-state node id: {node_id}")
        node_ids.add(node_id)

    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Every world-state edge must be an object.")
        for field in ("source", "target", "relation"):
            value = edge.get(field)
            if not isinstance(value, str) or not value or len(value) > 128:
                raise ValueError(f"Every world-state edge must have a non-empty {field} string of at most 128 characters.")

    campaign_id = metadata.get("campaign_id")
    if campaign_id is not None and (
        not isinstance(campaign_id, str) or not campaign_id or len(campaign_id) > 128
    ):
        raise ValueError("metadata.campaign_id must be a non-empty string of at most 128 characters.")


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
        validate_state(state)
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
    validate_state(state)
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
