"""Prompt templates and helpers for Actor (interactive narrative) mode.

Narrative pacing is modelled on Freytag's Pyramid — each stage carries a
distinct temperature and injector paragraph that shifts sentence rhythm, sensory
density, and stakes language to match where the story sits on the arc.
"""

import logging
from enum import Enum
from string import Template
from typing import Any

logger = logging.getLogger(__name__)

_COMPRESS_MODEL = "mistral:7b"
_ACTOR_MODEL = "mistral:7b"


# ---------------------------------------------------------------------------
# Freytag pyramid
# ---------------------------------------------------------------------------


class FreytagStage(str, Enum):
    """Narrative arc position that controls temperature and pacing style."""

    EXPOSITION = "EXPOSITION"
    RISING_ACTION = "RISING_ACTION"
    CLIMAX = "CLIMAX"
    FALLING_ACTION = "FALLING_ACTION"
    RESOLUTION = "RESOLUTION"


FREYTAG_TEMPERATURES: dict[FreytagStage, float] = {
    FreytagStage.EXPOSITION: 0.65,
    FreytagStage.RISING_ACTION: 0.75,
    FreytagStage.CLIMAX: 0.88,
    FreytagStage.FALLING_ACTION: 0.70,
    FreytagStage.RESOLUTION: 0.60,
}

FREYTAG_INJECTORS: dict[FreytagStage, str] = {
    FreytagStage.EXPOSITION: (
        "PACING DIRECTIVE — EXPOSITION: You are setting the scene. Slow everything down. "
        "Prioritise world-building over action: describe the architecture, the smell of the air, "
        "the quality of the light, the ambient sounds. Use long, unhurried sentences that draw "
        "the reader into the geography and atmosphere. Introduce characters and factions through "
        "observation rather than confrontation. Plant seeds of mystery without resolving them. "
        "The player should feel they have time to look around and breathe."
    ),
    FreytagStage.RISING_ACTION: (
        "PACING DIRECTIVE — RISING ACTION: The situation is growing more complicated. "
        "Obstacles are accumulating and stakes are clarifying. Use a mix of sentence lengths — "
        "longer passages to establish new complications, shorter ones to signal that events are "
        "speeding up. Introduce at least one element the player did not anticipate. "
        "Every choice should feel like it carries weight and could tip the balance. "
        "Foreshadow the climax without revealing it."
    ),
    FreytagStage.CLIMAX: (
        "PACING DIRECTIVE — CLIMAX: Everything is happening at once. "
        "Use short, punchy sentences. Strike hard. No subordinate clauses, no qualifications. "
        "Assault the senses: the roar of steel, the smell of smoke and blood, the cold weight "
        "of a decision that cannot be undone. Time pressure is absolute — every sentence costs "
        "the player a heartbeat. The narrative voice should feel breathless and urgent. "
        "Stakes are at their highest. Whatever the player does next defines the story permanently."
    ),
    FreytagStage.FALLING_ACTION: (
        "PACING DIRECTIVE — FALLING ACTION: The peak has passed; the world is reassembling itself. "
        "Tension unwinds but does not fully dissolve — consequences are still arriving. "
        "Medium-length sentences carry a sense of exhausted momentum. Describe aftermath: "
        "wounds, silence, the faces of NPCs processing what just happened. "
        "Let the player catch their breath while making clear that choices made at the climax "
        "are already reshaping the landscape. Not everything is resolved."
    ),
    FreytagStage.RESOLUTION: (
        "PACING DIRECTIVE — RESOLUTION: Threads are drawing together toward a close. "
        "Use calm, measured prose with long, reflective sentences that look both backward "
        "and forward. Characters take stock; relationships shift into their settled forms. "
        "The world acknowledges what the player has done. "
        "Endings are earned, not imposed — leave appropriate space for ambiguity and open threads "
        "that hint at what lies beyond this chapter. The tone should feel earned and resonant."
    ),
}


# ---------------------------------------------------------------------------
# Master narrative system prompt
# ---------------------------------------------------------------------------

# $-prefixed identifiers are filled by build_narrative_prompt via string.Template.
NARRATIVE_BASE_SYSTEM: str = """\
You are the narrative engine for an interactive role-playing game set in: $universe

━━━━ WORLD RULES — AXIOM CONSTRAINTS (never violate these) ━━━━
$axiom_rules

━━━━ CURRENT WORLD STATE (compressed) ━━━━
$compressed_state

━━━━ ACTIVE LOCATION ━━━━
$active_location

━━━━ RELEVANT CONTEXT (retrieved world memories) ━━━━
$rag_context

━━━━ RECENT SESSION HISTORY ━━━━
$compressed_history

━━━━ STAGE DIRECTIVE ━━━━
$stage_injector

━━━━ PLAYER ACTION ━━━━
$player_action

━━━━ OUTPUT FORMAT — follow exactly, no deviation ━━━━
Write ONE narrative paragraph (3–6 sentences) that continues the story in direct
response to the player action above.  Then, on the very next lines, present exactly
three forward-looking choices using this format (each on its own line, no blank lines
between them):

CHOICE_A: [a concrete action the player can take]
CHOICE_B: [an alternative approach with different risk/reward]
CHOICE_C: [a cautious or investigative option]

Do not add any text after CHOICE_C.\
"""

_NARRATIVE_TEMPLATE = Template(NARRATIVE_BASE_SYSTEM)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_narrative_prompt(
    universe: str,
    axiom_rules: str,
    compressed_state: str,
    active_location: str,
    rag_context: str,
    compressed_history: str,
    player_action: str,
    stage: FreytagStage,
) -> tuple[str, float]:
    """Fill the narrative system prompt template and return (prompt, temperature).

    All placeholder values are pre-validated by the caller.  Empty strings are
    replaced with sensible fallbacks so the LLM is never passed a bare token.
    """
    filled = _NARRATIVE_TEMPLATE.substitute(
        universe=universe or "an unnamed world",
        axiom_rules=axiom_rules or "No special constraints apply.",
        compressed_state=compressed_state or "World state not yet initialised.",
        active_location=active_location or "an unspecified location",
        rag_context=rag_context or "No additional context retrieved.",
        compressed_history=compressed_history or "No prior session history.",
        stage_injector=FREYTAG_INJECTORS[stage],
        player_action=player_action,
    )
    temperature = FREYTAG_TEMPERATURES[stage]
    logger.debug(
        "build_narrative_prompt → stage=%s temperature=%.2f prompt_len=%d",
        stage.value, temperature, len(filled),
    )
    return filled, temperature


# ---------------------------------------------------------------------------
# History compressor
# ---------------------------------------------------------------------------


async def compress_history(scenes: list[str], llm_client: Any) -> str:
    """Summarise a list of scene texts into a single ≤150-word digest.

    Preserves narrative continuity by emphasising events, state changes, and
    location transitions — the details most likely to matter for future choices.
    Returns the raw scene list joined with separators if the LLM call fails, so
    the caller always receives *something* usable.

    Args:
        scenes: Ordered list of scene narrative strings (oldest first).
        llm_client: Module or object exposing an async ``generate(model, prompt)``
                    coroutine (matches the signature of ``backend.llm_client.generate``).
    """
    if not scenes:
        return ""

    joined = "\n---\n".join(scenes)
    compression_prompt = (
        "Summarise the following RPG scene log in 150 words or fewer. "
        "Preserve: all named characters encountered, key items gained or lost, "
        "location transitions, and any decisions with lasting consequences. "
        "Write in past tense, third-person. Output the summary only — no preamble.\n\n"
        f"SCENE LOG:\n{joined}"
    )

    try:
        summary = await llm_client.generate(
            model=_COMPRESS_MODEL,
            prompt=compression_prompt,
            system=(
                "You are a precise narrative archivist. "
                "Output only the requested summary; no commentary."
            ),
            options={"num_predict": 220, "temperature": 0.3},
        )
        logger.debug("compress_history → %d scenes → %d chars", len(scenes), len(summary))
        return summary.strip()
    except Exception as exc:
        logger.warning("compress_history failed, returning raw join: %s", exc)
        # Graceful fallback: return last 3 scenes trimmed to 1 200 chars
        fallback = "\n---\n".join(scenes[-3:])
        return fallback[:1200]
