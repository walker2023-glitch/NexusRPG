"""Prompt templates and Pydantic response models for Architect (graph extraction) mode."""

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# ID / type constants
# ---------------------------------------------------------------------------

_ID_PATTERN = r"^(char|loc|item|org|event|concept)_[a-z0-9_]+$"

EntityType = Literal["char", "loc", "item", "org", "event", "concept"]

_MAX_NODES = 12
_MAX_EDGES = 16
_MAX_EXISTING_IDS_HINT = 8
_LABEL_MAX = 80
_DESC_MAX = 300
_RELATION_MAX = 60


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GraphNode(BaseModel):
    """A single entity node in the Nexus knowledge graph."""

    id: str = Field(
        ...,
        pattern=_ID_PATTERN,
        description=(
            "Unique node ID. snake_case with type prefix: "
            "char_ | loc_ | item_ | org_ | event_ | concept_"
        ),
    )
    label: str = Field(..., max_length=_LABEL_MAX, description="Human-readable display name.")
    entity_type: EntityType = Field(..., description="Entity category — must match the ID prefix.")
    description: str = Field(
        default="",
        max_length=_DESC_MAX,
        description="Brief factual description. Omit entirely if uncertain.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Flat key-value pairs of confirmed facts. No nested objects.",
    )

    @model_validator(mode="after")
    def _type_matches_prefix(self) -> "GraphNode":
        """Ensure entity_type is consistent with the ID prefix segment."""
        prefix = self.id.split("_")[0]
        if prefix != self.entity_type:
            raise ValueError(
                f"entity_type '{self.entity_type}' does not match ID prefix '{prefix}_'"
            )
        return self


class GraphEdge(BaseModel):
    """A directed relationship between two graph nodes."""

    source: str = Field(..., pattern=_ID_PATTERN, description="Source node ID.")
    target: str = Field(..., pattern=_ID_PATTERN, description="Target node ID.")
    relation: str = Field(
        ...,
        max_length=_RELATION_MAX,
        description="Snake_case verb phrase describing the relationship, e.g. 'located_in', 'wields'.",
    )


class GraphExtractionResult(BaseModel):
    """Structured output returned by the LLM extraction call."""

    nodes: list[GraphNode] = Field(
        default_factory=list,
        max_length=_MAX_NODES,
        description=f"Extracted entity nodes (max {_MAX_NODES}).",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        max_length=_MAX_EDGES,
        description=f"Directed relationships between nodes (max {_MAX_EDGES}).",
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

NODE_EXTRACTION_SYSTEM: str = f"""\
You are a knowledge-graph extraction engine for a role-playing game world.
Your ONLY output is a single valid JSON object — no markdown fences, no commentary, no extra keys.

JSON SCHEMA (follow exactly):
{{
  "nodes": [          // list of entities, max {_MAX_NODES}
    {{
      "id":          "<prefix>_<snake_case>",   // prefix: char|loc|item|org|event|concept
      "label":       "Display Name",             // max {_LABEL_MAX} chars
      "entity_type": "<prefix>",                 // MUST match ID prefix
      "description": "One factual sentence.",    // max {_DESC_MAX} chars
      "properties":  {{"key": "value"}}           // flat confirmed facts only
    }}
  ],
  "edges": [          // directed relationships, max {_MAX_EDGES}
    {{"source": "char_x", "target": "loc_y", "relation": "located_in"}}
  ]
}}

RULES:
1. IDs: lowercase letters, digits, underscores ONLY — no spaces, no hyphens.
   Good: char_iron_hand  Bad: char_IronHand, char_iron-hand
2. entity_type must equal the prefix segment of id (char_thor → "char").
3. relation: snake_case verb phrase — e.g. owns, allied_with, forged_in, rules.
4. UNCERTAINTY → OMIT. If you are not confident about a fact, leave the field out.
   Never hallucinate names, dates, or relationships.
5. properties: flat object only — no nested dicts, no arrays.
6. Return {{"nodes": [], "edges": []}} if nothing can be extracted.
"""

# ---------------------------------------------------------------------------
# One-shot example payload (Thor/Mjolnir/Asgard, ~180 tokens)
# ---------------------------------------------------------------------------

_EXAMPLE_INPUT = (
    "Thor wields Mjolnir, a magical war hammer forged in Asgard by dwarven smiths. "
    "He serves as protector of the Nine Realms and is allied with the Warriors Three."
)

_EXAMPLE_OUTPUT = json.dumps(
    {
        "nodes": [
            {
                "id": "char_thor",
                "label": "Thor",
                "entity_type": "char",
                "description": "Asgardian god of thunder and protector of the Nine Realms.",
                "properties": {"weapon": "mjolnir", "role": "protector"},
            },
            {
                "id": "item_mjolnir",
                "label": "Mjolnir",
                "entity_type": "item",
                "description": "Magical war hammer with enchanted properties.",
                "properties": {"crafted_by": "dwarven_smiths", "material": "enchanted_metal"},
            },
            {
                "id": "loc_asgard",
                "label": "Asgard",
                "entity_type": "loc",
                "description": "Realm of the Asgardian gods.",
                "properties": {},
            },
            {
                "id": "org_warriors_three",
                "label": "Warriors Three",
                "entity_type": "org",
                "description": "Elite band of Asgardian warriors allied with Thor.",
                "properties": {},
            },
        ],
        "edges": [
            {"source": "char_thor", "target": "item_mjolnir", "relation": "wields"},
            {"source": "item_mjolnir", "target": "loc_asgard", "relation": "forged_in"},
            {"source": "char_thor", "target": "loc_asgard", "relation": "protects"},
            {"source": "char_thor", "target": "org_warriors_three", "relation": "allied_with"},
        ],
    },
    separators=(",", ":"),
)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_node_extraction_prompt(
    input_text: str,
    existing_node_ids: list[str] | None = None,
    universe_context: str = "",
) -> str:
    """Construct the user-turn prompt for graph extraction.

    Args:
        input_text: Raw narrative text to analyse.
        existing_node_ids: Up to 8 node IDs already in the graph; passed as a
            hint so the model reuses existing IDs instead of creating duplicates.
        universe_context: Optional one-liner describing the world setting
            (e.g. "Low-magic medieval Europe, circa 1200 AD").

    Returns:
        Fully assembled user-turn string ready to send to the LLM.
    """
    parts: list[str] = []

    # One-shot example
    parts.append("EXAMPLE INPUT:")
    parts.append(_EXAMPLE_INPUT)
    parts.append("")
    parts.append("EXAMPLE OUTPUT:")
    parts.append(_EXAMPLE_OUTPUT)
    parts.append("")

    # Optional universe context
    if universe_context:
        parts.append(f"UNIVERSE CONTEXT: {universe_context.strip()}")
        parts.append("")

    # Existing node ID hint (prevents duplicate node creation)
    if existing_node_ids:
        hint_ids = existing_node_ids[:_MAX_EXISTING_IDS_HINT]
        parts.append(
            "EXISTING NODE IDs (reuse these exact IDs if the same entity appears — "
            "do NOT create a new node for an entity already listed here):"
        )
        parts.append("  " + ", ".join(hint_ids))
        parts.append("")

    # Actual extraction target
    parts.append("NOW EXTRACT FROM:")
    parts.append(input_text.strip())

    return "\n".join(parts)
