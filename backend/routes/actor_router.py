"""Actor mode API — interactive narrative generation using Freytag-aware prompting."""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import llm_client, rag, world_state as ws
from backend.axiom.engine import ValidationResult, load_ruleset, validate_scene_transition
from backend.chronos.commit_engine import ChronosEngine
from backend.config import settings
from backend.prompts.actor_mode import (
    FreytagStage,
    build_narrative_prompt,
    compress_history,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_ACTOR_MODEL = "mistral:7b"
_DEFAULT_BRANCH = "main"

# Matches: CHOICE_A: some text   (case-insensitive, captures letter and content)
_CHOICE_RE = re.compile(r"^CHOICE_([ABC]):\s*(.+)", re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SceneRequest(BaseModel):
    """Payload for generating the next narrative scene."""

    player_action: str = Field(
        ..., min_length=1, description="What the player just did or said."
    )
    freytag_stage: FreytagStage = Field(
        default=FreytagStage.RISING_ACTION,
        description="Current position on the Freytag narrative arc.",
    )
    parent_commit_id: str | None = Field(
        None, description="Chronos commit to branch from; null for a root commit."
    )
    scene_history: list[str] = Field(
        default_factory=list,
        description="Ordered list of prior scene texts (oldest first) for compression.",
    )


class SceneResponse(BaseModel):
    """Narrative scene result returned to the frontend."""

    scene_text: str
    choices: dict[str, str]  # {"A": "...", "B": "...", "C": "..."}
    commit_id: str
    active_location: str | None
    axiom_violations: list[str]


class ChooseRequest(BaseModel):
    """Payload for committing to one of the three generated choices."""

    choice: Literal["A", "B", "C"] = Field(
        ..., description="Which of the three choices the player selected."
    )
    commit_id: str = Field(..., description="The Chronos commit that produced the choices.")
    branch_name: str = Field(
        default=_DEFAULT_BRANCH,
        description="Branch ref to advance to the chosen commit.",
    )


class ChooseResponse(BaseModel):
    """State summary after a player choice is locked in."""

    branch_name: str
    commit_id: str
    choice_taken: str
    active_location: str | None
    node_count: int
    edge_count: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_choices(text: str) -> dict[str, str]:
    """Extract CHOICE_A / CHOICE_B / CHOICE_C lines from generated narrative text.

    Returns whatever choices are found; missing letters map to an empty string
    so the caller can decide how to handle a partially-parsed response.
    """
    choices: dict[str, str] = {"A": "", "B": "", "C": ""}
    for match in _CHOICE_RE.finditer(text):
        letter = match.group(1).upper()
        content = match.group(2).strip()
        choices[letter] = content
    return choices


def _extract_scene_paragraph(text: str) -> str:
    """Return the narrative paragraph (everything before the first CHOICE_ line)."""
    match = _CHOICE_RE.search(text)
    if match:
        return text[: match.start()].strip()
    return text.strip()


def _compress_world_state(state: dict[str, Any]) -> str:
    """Produce a compact human-readable summary of world_state for the prompt context."""
    nodes = state.get("nodes", [])
    edges = state.get("edges", [])
    meta = state.get("metadata", {})

    lines: list[str] = [
        f"Universe: {meta.get('universe') or 'unnamed'}",
        f"Active location: {meta.get('active_location') or 'unknown'}",
        f"Nodes ({len(nodes)}): "
        + ", ".join(
            f"{n.get('id', '?')} ({n.get('label', '?')})"
            for n in nodes[:12]
        ),
        f"Edges ({len(edges)}): "
        + ", ".join(
            f"{e.get('source','?')} —{e.get('relation','?')}→ {e.get('target','?')}"
            for e in edges[:10]
        ),
    ]
    return "\n".join(lines)


def _format_axiom_rules_for_prompt(ruleset: Any) -> str:
    """Render the axiom ruleset as a terse bullet list for inclusion in the system prompt."""
    lines: list[str] = []
    for r in ruleset.numeric_cap_rules:
        lines.append(
            f"• {r.entity_id}.{r.property_path} must stay in [{r.min_val}, {r.max_val}]."
        )
    for r in ruleset.status_immutable_rules:
        lines.append(
            f"• {r.entity_id}: once status = '{r.locked_status}', it can never change."
        )
    for r in ruleset.item_count_rules:
        lines.append(
            f"• {r.entity_id}.{r.item_property} may hold at most {r.max_count} items."
        )
    for r in ruleset.location_bound_rules:
        lines.append(
            f"• {r.item_id} may only be used in: {', '.join(r.allowed_location_ids)}."
        )
    return "\n".join(lines) if lines else "No special constraints defined."


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/scene", response_model=SceneResponse, status_code=201)
async def generate_scene(body: SceneRequest) -> SceneResponse:
    """Generate the next narrative scene in response to a player action.

    Pipeline:
    1. Load world_state → resolve active_location.
    2. Embed player_action → RAG search for location-scoped context (limit=5).
    3. Optionally compress scene history into a ≤150-word digest.
    4. Build the Freytag-stage-aware narrative prompt.
    5. Call Ollama (mistral:7b) with the stage temperature.
    6. Parse CHOICE_A/B/C from the response.
    7. Run the Axiom engine — auto-correct any NumericCap/StatusImmutable violations,
       persist the (possibly corrected) state.
    8. Commit scene + state to Chronos.
    9. Return scene_text, choices, commit_id, active_location.
    """
    # 1. World state + active location
    state = await ws.load(settings.world_state_path)
    active_location = ws.get_active_location(state) or "global"

    # 2. RAG context
    rag_context_str = "No additional context retrieved."
    try:
        query_embedding = await llm_client.embed(body.player_action)
        rag_hits = await rag.search(
            db_path=settings.lancedb_path,
            query_embedding=query_embedding,
            active_location=active_location,
            limit=5,
        )
        if rag_hits:
            rag_context_str = "\n".join(f"- {hit['text']}" for hit in rag_hits)
    except Exception as exc:
        logger.warning("actor RAG search failed (non-fatal): %s", exc)

    # 3. History compression
    compressed_hist = ""
    if body.scene_history:
        try:
            compressed_hist = await compress_history(body.scene_history, llm_client)
        except Exception as exc:
            logger.warning("actor history compression failed (non-fatal): %s", exc)
            compressed_hist = "\n---\n".join(body.scene_history[-3:])[:1200]

    # 4. Axiom ruleset (needed for both prompt and validation)
    ruleset = await load_ruleset(settings.axiom_rules_path)
    axiom_rules_text = _format_axiom_rules_for_prompt(ruleset)

    # 5. Build prompt and temperature
    prompt, temperature = build_narrative_prompt(
        universe=state.get("metadata", {}).get("universe", ""),
        axiom_rules=axiom_rules_text,
        compressed_state=_compress_world_state(state),
        active_location=active_location,
        rag_context=rag_context_str,
        compressed_history=compressed_hist,
        player_action=body.player_action,
        stage=body.freytag_stage,
    )

    # 6. LLM call
    try:
        raw_response = await llm_client.generate(
            model=_ACTOR_MODEL,
            prompt=prompt,
            options={"temperature": temperature, "num_predict": 512},
        )
    except Exception as exc:
        logger.error("actor LLM call failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    # 7. Parse narrative and choices
    scene_text = _extract_scene_paragraph(raw_response)
    choices = _parse_choices(raw_response)

    missing = [k for k, v in choices.items() if not v]
    if missing:
        logger.warning("actor scene: missing choices %s in LLM output", missing)

    # 8. Axiom validation — validate state against itself after narrative generation.
    #    In Actor mode the LLM cannot directly mutate state; validation catches any
    #    pre-existing violations so they are corrected before the commit is recorded.
    validation: ValidationResult = validate_scene_transition(state, state, ruleset)
    final_state = validation.corrected_state if validation.corrected_state is not None else state

    if validation.violations:
        logger.info("actor axiom violations: %s", validation.violations)

    await ws.save(settings.world_state_path, final_state)

    # 9. Chronos commit
    try:
        engine = ChronosEngine(Path(settings.chronos_path))
        commit_id = await asyncio.to_thread(
            engine.commit,
            scene_text,
            final_state,
            body.parent_commit_id,
            body.player_action,
            None,  # branch_label set on /choose
        )
    except Exception as exc:
        logger.error("actor Chronos commit failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Chronos commit error: {exc}") from exc

    return SceneResponse(
        scene_text=scene_text,
        choices=choices,
        commit_id=commit_id,
        active_location=active_location if active_location != "global" else None,
        axiom_violations=validation.violations,
    )


@router.post("/choose", response_model=ChooseResponse)
async def choose(body: ChooseRequest) -> ChooseResponse:
    """Lock in a player choice and advance the branch HEAD to the associated commit.

    Reconstructs the world_state at *commit_id* so the branch always points to a
    consistent snapshot.  The active world_state.json is NOT overwritten here —
    that happens when the frontend calls /chronos/checkout/{commit_id} to navigate
    to the chosen branch.
    """
    try:
        engine = ChronosEngine(Path(settings.chronos_path))

        # Verify the commit exists and get its metadata
        meta = await asyncio.to_thread(engine.get_commit_metadata, body.commit_id)

        # Advance the named branch tip to this commit
        await asyncio.to_thread(engine.update_branch_tip, body.branch_name, body.commit_id)

        # Reconstruct the world state at this commit to return node/edge counts
        state = await asyncio.to_thread(engine.reconstruct_state, body.commit_id)

    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"Commit not found: {body.commit_id}") from exc
    except Exception as exc:
        logger.error("actor /choose failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    active_location = state.get("metadata", {}).get("active_location")

    logger.info(
        "actor /choose → branch '%s' → %s (choice=%s)",
        body.branch_name, body.commit_id, body.choice,
    )

    return ChooseResponse(
        branch_name=body.branch_name,
        commit_id=body.commit_id,
        choice_taken=body.choice,
        active_location=active_location,
        node_count=len(state.get("nodes", [])),
        edge_count=len(state.get("edges", [])),
    )
