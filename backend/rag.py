"""LanceDB vector store wrapper for entity description embeddings.

Metadata filter strategy
------------------------
Every row carries a ``location_key`` that identifies the in-world scene the
entity belongs to (e.g. ``"loc_thornwall_citadel"``), or the special sentinel
value ``"global"`` for entities that are relevant everywhere (world laws,
pantheon members, player characters).

All searches apply a pre-filter::

    WHERE location_key = <active_location> OR location_key = 'global'

This shrinks the candidate set from the full universe corpus down to only the
rows that can plausibly appear in the current scene.  Because LanceDB performs
ANN search *within* the filtered subset, both the distance calculations and the
index traversal are proportional to scene size rather than total corpus size.
On a CPU-only homelab with tens of thousands of entities spread across a large
world, this can reduce query latency by 10–100× compared to an unfiltered
full-table scan followed by post-filtering.
"""

import asyncio
import logging
import time
from typing import Any

import lancedb
import pyarrow as pa

logger = logging.getLogger(__name__)

_TABLE_NAME = "entity_descriptions"

_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("text", pa.string()),
        pa.field("source_file", pa.string()),
        pa.field("location_key", pa.string()),
        pa.field("entity_type", pa.string()),
        pa.field("timestamp", pa.float64()),
        pa.field("vector", pa.list_(pa.float32(), 768)),
    ]
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sync_connect(db_path: str) -> lancedb.DBConnection:
    """Open (or create) the LanceDB database at *db_path* synchronously."""
    return lancedb.connect(db_path)


def _sync_get_or_create_table(conn: lancedb.DBConnection) -> Any:
    """Return the entity_descriptions table, creating it with the schema if absent."""
    existing = conn.table_names()
    if _TABLE_NAME in existing:
        return conn.open_table(_TABLE_NAME)
    logger.info("rag → creating table '%s'", _TABLE_NAME)
    return conn.create_table(_TABLE_NAME, schema=_SCHEMA)


def _sync_upsert(
    conn: lancedb.DBConnection,
    record: dict[str, Any],
) -> None:
    """Delete any existing row with the same id, then insert the new record."""
    table = _sync_get_or_create_table(conn)
    entity_id = record["id"]
    try:
        table.delete(f"id = '{entity_id}'")
    except Exception:
        # Table may be empty; delete on empty set raises in some LanceDB versions.
        pass
    table.add([record])


def _sync_search(
    conn: lancedb.DBConnection,
    query_embedding: list[float],
    active_location: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Run a metadata-filtered ANN search and return scored result dicts."""
    table = _sync_get_or_create_table(conn)
    where_clause = (
        f"location_key = '{active_location}' OR location_key = 'global'"
    )
    results = (
        table.search(query_embedding)
        .where(where_clause)
        .limit(limit)
        .select(["id", "text", "_distance"])
        .to_list()
    )
    return [
        {
            "id": row["id"],
            "text": row["text"],
            # LanceDB returns L2 distance; convert to a 0-1 similarity score.
            "score": float(1.0 / (1.0 + row.get("_distance", 0.0))),
        }
        for row in results
    ]


def _sync_delete_entity(conn: lancedb.DBConnection, entity_id: str) -> None:
    """Remove all rows where id equals *entity_id*."""
    table = _sync_get_or_create_table(conn)
    try:
        table.delete(f"id = '{entity_id}'")
        logger.debug("rag.delete_entity → removed rows for id=%s", entity_id)
    except Exception as exc:
        logger.warning("rag.delete_entity → delete failed for id=%s: %s", entity_id, exc)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def init_db(db_path: str) -> lancedb.DBConnection:
    """Connect to (or create) the LanceDB instance at *db_path*.

    The entity_descriptions table is created with the canonical schema if it
    does not already exist.  Returns the open DBConnection for use in
    subsequent calls.
    """
    conn = await asyncio.to_thread(_sync_connect, db_path)
    await asyncio.to_thread(_sync_get_or_create_table, conn)
    logger.info("rag.init_db → connected to LanceDB at %s", db_path)
    return conn


async def upsert(
    db_path: str,
    id: str,
    text: str,
    embedding: list[float],
    location_key: str,
    entity_type: str,
    source_file: str,
) -> None:
    """Insert or replace a single entity embedding record.

    Any existing row with the same *id* is deleted first so callers can
    freely call upsert on re-import without duplicating entries.
    """
    record: dict[str, Any] = {
        "id": id,
        "text": text,
        "source_file": source_file,
        "location_key": location_key,
        "entity_type": entity_type,
        "timestamp": time.time(),
        "vector": embedding,
    }
    conn = await asyncio.to_thread(_sync_connect, db_path)
    await asyncio.to_thread(_sync_upsert, conn, record)
    logger.debug("rag.upsert → id=%s location_key=%s entity_type=%s", id, location_key, entity_type)


async def search(
    db_path: str,
    query_embedding: list[float],
    active_location: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return up to *limit* semantically similar entities for the current scene.

    Results are filtered to rows whose ``location_key`` matches
    *active_location* or is ``"global"``, then ranked by vector similarity.
    Each result dict has keys: ``id``, ``text``, ``score`` (0–1, higher = more similar).

    Raises ValueError when *active_location* is empty to prevent accidental
    full-corpus scans — callers must always supply a concrete location key.
    """
    if not active_location:
        raise ValueError(
            "rag.search requires a non-empty active_location; "
            "use 'global' if intentionally searching across all locations."
        )
    conn = await asyncio.to_thread(_sync_connect, db_path)
    results = await asyncio.to_thread(
        _sync_search, conn, query_embedding, active_location, limit
    )
    logger.debug(
        "rag.search → location=%s limit=%d hits=%d", active_location, limit, len(results)
    )
    return results


async def embed_and_index(
    db_path: str,
    llm_client: Any,
    text: str,
    entity_id: str,
    location_key: str,
    entity_type: str,
    source: str,
) -> None:
    """Embed *text* with the LLM client and upsert the resulting record.

    Convenience wrapper that chains ``llm_client.embed`` → ``upsert`` so
    callers do not have to handle the embedding step manually.
    *llm_client* must expose an async ``embed(text: str) -> list[float]`` method
    (see ``backend.llm_client``).
    """
    embedding = await llm_client.embed(text)
    await upsert(
        db_path=db_path,
        id=entity_id,
        text=text,
        embedding=embedding,
        location_key=location_key,
        entity_type=entity_type,
        source_file=source,
    )
    logger.debug("rag.embed_and_index → id=%s indexed successfully", entity_id)


async def delete_entity(db_path: str, entity_id: str) -> None:
    """Remove all index rows belonging to *entity_id*.

    Silently no-ops if the entity does not exist in the table.
    """
    conn = await asyncio.to_thread(_sync_connect, db_path)
    await asyncio.to_thread(_sync_delete_entity, conn, entity_id)
