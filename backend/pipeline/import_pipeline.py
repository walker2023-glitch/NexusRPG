"""Async import pipeline — vision and text ingestion into the Nexus knowledge graph.

CPU homelab constraints drive several decisions here:
- VISION_SEMAPHORE(1): LLaVA inference is memory-intensive; running two vision jobs
  simultaneously would thrash RAM and slow both to a crawl. Serial execution is faster
  in practice on a single Xeon-class CPU.
- MAX_IMAGE_DIM = 512: LLaVA 7B was trained at this resolution. Sending larger images
  adds JPEG decoding overhead and fills the 2 048-token image budget with redundant
  pixels, increasing latency by 2–4× with no quality benefit.
- _chunk_text splits at paragraph boundaries rather than character boundaries so that
  sentences are never split mid-thought, preserving semantic coherence for embeddings.
"""

import asyncio
import base64
import io
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image

from backend import rag, world_state as ws
from backend.prompts.vision_mode import VISION_IMPORT_PROMPT

logger = logging.getLogger(__name__)

VISION_SEMAPHORE = asyncio.Semaphore(1)
MAX_IMAGE_DIM = 512

_VISION_TIMEOUT_S = 240.0   # CPU homelab LLaVA 7B Q4: typically 60–180s


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resize_and_encode(file_bytes: bytes) -> str:
    """Resize image to fit within MAX_IMAGE_DIM × MAX_IMAGE_DIM and return a base-64 JPEG string.

    512 px is the LLaVA resolution sweet spot: the model's visual encoder was
    trained at this size, so sending larger images provides no additional detail
    while increasing inference time by 2–4× due to the larger token budget.
    LANCZOS resampling is slower than BILINEAR but produces sharper downscales
    for line-art maps and character portraits, which are the primary import types.
    """
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=82)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def _run_vision_inference(image_b64: str, llm_client: Any) -> str:
    """Call generate_vision with the canonical import prompt, bounded to VISION_TIMEOUT_S.

    A 240-second wall-clock timeout is chosen to handle worst-case LLaVA 7B Q4
    inference on a cold-start Intel Xeon E3 (observed: 60–180s). The asyncio
    timeout wraps the entire llm_client call so the semaphore is not held past
    the deadline even if the Ollama HTTP connection hangs.
    """
    coro = llm_client.generate_vision(
        prompt=VISION_IMPORT_PROMPT,
        image_b64=image_b64,
    )
    return await asyncio.wait_for(coro, timeout=_VISION_TIMEOUT_S)


def _chunk_text(text: str, max_chars: int = 4000) -> list[str]:
    """Split *text* into chunks at paragraph boundaries, each at most *max_chars* long.

    Splitting on double-newlines keeps sentences whole, preserving the semantic
    coherence that embedding models rely on. A paragraph that is itself longer
    than *max_chars* is included as a single oversized chunk rather than being
    split mid-sentence.
    """
    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # +2 accounts for the "\n\n" separator between joined paragraphs.
        if current_len + len(para) + 2 > max_chars and current_parts:
            chunks.append("\n\n".join(current_parts))
            current_parts = [para]
            current_len = len(para)
        else:
            current_parts.append(para)
            current_len += len(para) + 2

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks or [text[:max_chars]]


def _slugify(text: str) -> str:
    """Convert *text* to a lowercase snake_case identifier safe for node IDs."""
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40]


# ---------------------------------------------------------------------------
# Public pipeline coroutines
# ---------------------------------------------------------------------------


async def process_image_import(
    file_bytes: bytes,
    filename: str,
    entity_id: str,
    world_state_path: str,
    lancedb_path: str,
    llm_client: Any,
) -> dict[str, Any]:
    """Ingest an image file: vision-describe → embed → LanceDB upsert → world_state update.

    The VISION_SEMAPHORE ensures only one LLaVA inference runs at a time,
    preventing RAM saturation on CPU-only hardware.

    Args:
        file_bytes: Raw bytes of the uploaded image file.
        filename: Original filename (used as source_file in LanceDB).
        entity_id: Graph node ID that this image depicts (e.g. ``char_theron``).
        world_state_path: Path to world_state.json.
        lancedb_path: Root path of the LanceDB instance.
        llm_client: Module exposing async generate_vision and embed functions.

    Returns:
        Dict with keys: entity_id, description, indexed (bool).
    """
    logger.info("process_image_import → %s (entity=%s)", filename, entity_id)

    # Resize/encode outside the semaphore — pure CPU, no GPU/LLM contention.
    image_b64 = await asyncio.to_thread(_resize_and_encode, file_bytes)

    # Serial vision inference.
    async with VISION_SEMAPHORE:
        logger.debug("process_image_import → acquired VISION_SEMAPHORE for %s", entity_id)
        description = await _run_vision_inference(image_b64, llm_client)

    logger.info("process_image_import → vision description %d chars for %s", len(description), entity_id)

    # Embed the description and upsert into LanceDB.
    embedding = await llm_client.embed(description)
    await rag.upsert(
        db_path=lancedb_path,
        id=entity_id,
        text=description,
        embedding=embedding,
        location_key="global",
        entity_type="char",
        source_file=filename,
    )

    # Attach the visual description to the matching world_state node.
    state = await ws.load(world_state_path)
    node = ws.get_node(state, entity_id)
    if node is not None:
        node.setdefault("properties", {})["visual_description"] = description
        ws.upsert_node(state, node)
        await ws.save(world_state_path, state)
        logger.debug("process_image_import → updated node %s with visual_description", entity_id)
    else:
        logger.warning(
            "process_image_import → entity_id %s not found in world_state; skipping node update",
            entity_id,
        )

    return {"entity_id": entity_id, "description": description, "indexed": True}


async def process_text_import(
    file_bytes: bytes,
    filename: str,
    world_state_path: str,
    lancedb_path: str,
    llm_client: Any,
) -> dict[str, Any]:
    """Ingest a plain-text or markdown lore file: chunk → embed → LanceDB upsert.

    Each paragraph-based chunk is embedded and indexed independently so that
    fine-grained RAG retrieval can surface the specific paragraph most relevant
    to a query rather than an entire document.  A ``concept_`` lore node is
    added to world_state to make the document discoverable in the graph.

    Args:
        file_bytes: Raw bytes of the uploaded text/markdown file.
        filename: Original filename (used to derive the entity_id and source_file).
        world_state_path: Path to world_state.json.
        lancedb_path: Root path of the LanceDB instance.
        llm_client: Module exposing an async embed function.

    Returns:
        Dict with keys: entity_id, chunks (int), indexed (bool).
    """
    logger.info("process_text_import → %s", filename)

    # Decode bytes — try UTF-8 with fallback to latin-1.
    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1")

    chunks = _chunk_text(text, max_chars=4000)
    stem = Path(filename).stem
    base_entity_id = f"concept_{_slugify(stem)}"

    indexed_count = 0
    for idx, chunk in enumerate(chunks):
        chunk_entity_id = f"{base_entity_id}_{idx}" if idx > 0 else base_entity_id
        try:
            embedding = await llm_client.embed(chunk)
            await rag.upsert(
                db_path=lancedb_path,
                id=chunk_entity_id,
                text=chunk,
                embedding=embedding,
                location_key="global",
                entity_type="concept",
                source_file=filename,
            )
            indexed_count += 1
        except Exception as exc:
            logger.warning(
                "process_text_import → chunk %d of %s failed to index: %s", idx, filename, exc
            )

    # Add a lightweight lore node to world_state so the document is graph-visible.
    state = await ws.load(world_state_path)
    lore_node: dict[str, Any] = {
        "id": base_entity_id,
        "label": stem.replace("_", " ").title(),
        "entity_type": "concept",
        "properties": {
            "source_file": filename,
            "chunks": len(chunks),
            "indexed": indexed_count,
            "imported_at": time.time(),
        },
    }
    ws.upsert_node(state, lore_node)
    await ws.save(world_state_path, state)
    logger.info("process_text_import → %s: %d chunks indexed", filename, indexed_count)

    return {"entity_id": base_entity_id, "chunks": len(chunks), "indexed": indexed_count > 0}
