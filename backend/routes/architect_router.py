"""Architect mode API — extract knowledge-graph entities from text or scraped URLs."""

import json
import logging
from typing import Any

import instructor
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, HttpUrl

from backend import llm_client, rag, world_state as ws
from backend.config import settings
from backend.prompts.architect_mode import (
    NODE_EXTRACTION_SYSTEM,
    GraphExtractionResult,
    build_node_extraction_prompt,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_EXTRACT_MODEL = "mistral:7b"


# ---------------------------------------------------------------------------
# Instructor client factory
# ---------------------------------------------------------------------------


def _instructor_client() -> instructor.AsyncInstructor:
    """Return an async instructor client pointed at the local Ollama /v1 endpoint."""
    openai_client = AsyncOpenAI(
        base_url=f"{settings.ollama_url}/v1",
        api_key="ollama",  # Ollama ignores the key; any non-empty string works.
    )
    return instructor.from_openai(openai_client, mode=instructor.Mode.JSON)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    """Payload for the /extract endpoint."""

    raw_text: str = Field(..., min_length=1, description="Narrative or descriptive text to analyse.")
    universe_context: str = Field(
        default="",
        description="Optional one-liner describing the world setting for disambiguation.",
    )


class ExtractResponse(BaseModel):
    """Graph extraction result returned to the caller."""

    new_nodes: int
    new_edges: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class ScrapeRequest(BaseModel):
    """Payload for the /scrape endpoint."""

    url: HttpUrl = Field(..., description="Publicly accessible URL to crawl and extract from.")
    universe_context: str = Field(default="", description="Optional world-setting context hint.")


class ScrapeResponse(BaseModel):
    """Combined scrape + extraction result."""

    url: str
    scraped_chars: int
    new_nodes: int
    new_edges: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _node_embedding_text(node: dict[str, Any]) -> str:
    """Build a compact plain-text summary of a node for embedding.

    Combines label, description, and flattened properties into a single string
    that preserves the most semantically relevant content for vector search.
    """
    parts = [node.get("label", ""), node.get("description", "")]
    props = node.get("properties", {})
    if props:
        parts.append(json.dumps(props, ensure_ascii=False))
    return " ".join(p for p in parts if p).strip()


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    """Return a hashable identity key for an edge to support deduplication."""
    return (edge.get("source", ""), edge.get("target", ""), edge.get("relation", ""))


async def _run_extraction(raw_text: str, universe_context: str) -> GraphExtractionResult:
    """Call the instructor-wrapped Ollama endpoint and return a validated extraction result."""
    state = await ws.load(settings.world_state_path)
    existing_ids = [n["id"] for n in state.get("nodes", [])]

    prompt = build_node_extraction_prompt(
        input_text=raw_text,
        existing_node_ids=existing_ids,
        universe_context=universe_context,
    )

    client = _instructor_client()
    try:
        result: GraphExtractionResult = await client.chat.completions.create(
            model=_EXTRACT_MODEL,
            response_model=GraphExtractionResult,
            temperature=0.1,
            max_retries=2,
            messages=[
                {"role": "system", "content": NODE_EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:
        logger.error("architect extraction failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM extraction error: {exc}") from exc

    logger.info(
        "architect extraction → %d nodes, %d edges", len(result.nodes), len(result.edges)
    )
    return result


async def _merge_and_index(result: GraphExtractionResult) -> None:
    """Merge extracted nodes/edges into world_state.json and index embeddings in LanceDB."""
    state = await ws.load(settings.world_state_path)

    # Upsert nodes
    for node in result.nodes:
        ws.upsert_node(state, node.model_dump())

    # Deduplicate and append edges
    existing_edge_keys = {_edge_key(e) for e in state.get("edges", [])}
    for edge in result.edges:
        key = _edge_key(edge.model_dump())
        if key not in existing_edge_keys:
            state.setdefault("edges", []).append(edge.model_dump())
            existing_edge_keys.add(key)

    await ws.save(settings.world_state_path, state)

    # Embed and index each node in LanceDB (location_key="global" for architect entities)
    for node in result.nodes:
        node_dict = node.model_dump()
        embed_text = _node_embedding_text(node_dict)
        try:
            embedding = await llm_client.embed(embed_text)
            await rag.upsert(
                db_path=settings.lancedb_path,
                id=node.id,
                text=embed_text,
                embedding=embedding,
                location_key="global",
                entity_type=node.entity_type,
                source_file="architect_extract",
            )
        except Exception as exc:
            # Embedding failure is non-fatal; log and continue.
            logger.warning("architect embed failed for %s: %s", node.id, exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/extract", response_model=ExtractResponse, status_code=201)
async def extract(body: ExtractRequest) -> ExtractResponse:
    """Extract a knowledge-graph from raw narrative text.

    Calls the local Ollama LLM via instructor for structured JSON output,
    merges the result into world_state.json, and indexes node embeddings in
    LanceDB with location_key="global".
    """
    result = await _run_extraction(body.raw_text, body.universe_context)
    await _merge_and_index(result)

    return ExtractResponse(
        new_nodes=len(result.nodes),
        new_edges=len(result.edges),
        nodes=[n.model_dump() for n in result.nodes],
        edges=[e.model_dump() for e in result.edges],
    )


@router.post("/scrape", response_model=ScrapeResponse, status_code=201)
async def scrape(body: ScrapeRequest) -> ScrapeResponse:
    """Crawl *url*, extract its markdown text, then run graph extraction.

    crawl4ai fetches the page with word_count_threshold=10 to skip boilerplate
    and exclude_external_links=True to stay focused on the page content.
    The markdown output is truncated to 6 000 characters before extraction to
    stay within the model's context window on a CPU-only homelab.
    """
    url_str = str(body.url)

    try:
        from crawl4ai import AsyncWebCrawler  # noqa: PLC0415 — optional heavy dep

        async with AsyncWebCrawler(verbose=False) as crawler:
            crawl_result = await crawler.arun(
                url=url_str,
                word_count_threshold=10,
                exclude_external_links=True,
            )
    except ImportError as exc:
        raise HTTPException(
            status_code=500, detail="crawl4ai is not installed or failed to import."
        ) from exc
    except Exception as exc:
        logger.error("scrape failed for %s: %s", url_str, exc)
        raise HTTPException(status_code=502, detail=f"Crawl error: {exc}") from exc

    markdown: str = getattr(crawl_result, "markdown", "") or ""
    if not markdown.strip():
        raise HTTPException(
            status_code=422, detail=f"No extractable content found at {url_str}."
        )

    truncated = markdown[:6000]
    logger.info("scrape → %s — %d raw chars, %d after truncation", url_str, len(markdown), len(truncated))

    result = await _run_extraction(truncated, body.universe_context)
    await _merge_and_index(result)

    return ScrapeResponse(
        url=url_str,
        scraped_chars=len(truncated),
        new_nodes=len(result.nodes),
        new_edges=len(result.edges),
        nodes=[n.model_dump() for n in result.nodes],
        edges=[e.model_dump() for e in result.edges],
    )
