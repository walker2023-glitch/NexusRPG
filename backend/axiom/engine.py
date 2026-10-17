"""Axiom deterministic constraint engine — zero LLM calls, pure rule evaluation.

Rules are evaluated against a (state_before, state_after) pair produced by a
scene transition.  Two kinds of violation are possible:

* **Auto-correctable** (NumericCap, StatusImmutable) — the engine clamps or
  reverts the offending value and returns a ``corrected_state`` that the caller
  should substitute for ``state_after`` before persisting.
* **Hard invalid** (ItemCount, LocationBound) — the engine cannot safely correct
  these on its own; it marks the result invalid and lists the violation strings
  so the caller can reject or re-prompt.
"""

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import Any

import aiofiles
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule models
# ---------------------------------------------------------------------------


class ItemCountRule(BaseModel):
    """Enforce an upper bound on the length of a list property on a node.

    *item_property* must resolve to a ``list`` on the node; its length is
    compared against *max_count*.  The check uses the state_after node so it
    catches additions made during a transition.
    """

    entity_id: str = Field(..., description="ID of the node that owns the list property.")
    item_property: str = Field(..., description="Dot-notation path to the list property on the node.")
    max_count: int = Field(..., ge=0, description="Maximum allowed number of items in the list.")


class StatusImmutableRule(BaseModel):
    """Prevent a node's status field from changing once it reaches a locked value.

    If the node's ``status`` equals *locked_status* in ``state_before``, the
    engine reverts it to *locked_status* in the corrected state if the
    transition tried to change it.
    """

    entity_id: str = Field(..., description="ID of the node whose status must not change.")
    locked_status: str = Field(
        ..., description="The status value that, once reached, can never be changed."
    )


class NumericCapRule(BaseModel):
    """Clamp a numeric node property within [min_val, max_val].

    *property_path* uses dot-notation relative to the node root, e.g.
    ``"properties.health"`` or ``"stats.mana"``.  Values outside the range
    are silently clamped in the corrected state.
    """

    entity_id: str = Field(..., description="ID of the node that owns the numeric property.")
    property_path: str = Field(
        ..., description="Dot-notation path to the numeric property within the node dict."
    )
    min_val: float = Field(..., description="Minimum allowed value (inclusive).")
    max_val: float = Field(..., description="Maximum allowed value (inclusive).")


class LocationBoundRule(BaseModel):
    """Restrict an item to a set of allowed locations.

    The engine inspects the item node's ``location_key`` property (or the
    first ``located_at`` edge target in state_after) to determine its current
    location.  If that location is not in *allowed_location_ids* the result is
    marked invalid.
    """

    item_id: str = Field(..., description="ID of the item node to constrain.")
    allowed_location_ids: list[str] = Field(
        ..., description="Exhaustive list of location node IDs where this item may exist."
    )


class AxiomRuleset(BaseModel):
    """Complete rule set loaded from axiom_rules.json."""

    item_count_rules: list[ItemCountRule] = Field(default_factory=list)
    status_immutable_rules: list[StatusImmutableRule] = Field(default_factory=list)
    numeric_cap_rules: list[NumericCapRule] = Field(default_factory=list)
    location_bound_rules: list[LocationBoundRule] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """Outcome of a single scene-transition validation pass."""

    valid: bool = Field(..., description="False if any hard-invalid rule was violated.")
    violations: list[str] = Field(
        default_factory=list,
        description="Human-readable descriptions of every rule violation detected.",
    )
    corrected_state: dict | None = Field(
        None,
        description=(
            "A modified copy of state_after with auto-correctable violations applied. "
            "None when no corrections were needed."
        ),
    )


# ---------------------------------------------------------------------------
# Internal navigation helpers
# ---------------------------------------------------------------------------


def _find_node(state: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
    """Return the first node in *state* whose ``id`` matches *entity_id*, or None."""
    for node in state.get("nodes", []):
        if node.get("id") == entity_id:
            return node
    return None


def _find_node_in_corrected(
    corrected: dict[str, Any], entity_id: str
) -> dict[str, Any] | None:
    """Same as _find_node but operates on the mutable corrected state dict."""
    return _find_node(corrected, entity_id)


def _get_dotpath(obj: dict[str, Any], path: str) -> Any:
    """Navigate a dot-notation *path* inside *obj*, returning None on missing keys."""
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_dotpath(obj: dict[str, Any], path: str, value: Any) -> None:
    """Set *value* at the dot-notation *path* inside *obj*, creating dicts as needed."""
    parts = path.split(".")
    current: Any = obj
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    if isinstance(current, dict):
        current[parts[-1]] = value


def _item_location(state: dict[str, Any], item_id: str) -> str | None:
    """Resolve an item's current location from its node property or a ``located_at`` edge."""
    node = _find_node(state, item_id)
    if node is None:
        return None
    # Prefer an explicit location_key property on the node.
    loc = node.get("location_key") or _get_dotpath(node, "properties.location_key")
    if loc:
        return str(loc)
    # Fall back to the first outgoing ``located_at`` edge.
    for edge in state.get("edges", []):
        if edge.get("source") == item_id and edge.get("relation") == "located_at":
            return str(edge["target"])
    return None


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------


def validate_scene_transition(
    state_before: dict[str, Any],
    state_after: dict[str, Any],
    ruleset: AxiomRuleset,
) -> ValidationResult:
    """Evaluate all Axiom rules against a scene transition and return a ValidationResult.

    Corrections (clamp / revert) are collected into a deep copy of *state_after*
    and returned as ``corrected_state`` when at least one auto-fix was applied.
    Hard-invalid violations (ItemCount, LocationBound) set ``valid=False`` but
    do not prevent corrections from also being applied.
    """
    violations: list[str] = []
    hard_invalid = False
    corrected: dict[str, Any] = copy.deepcopy(state_after)
    any_correction = False

    # -- StatusImmutableRule -------------------------------------------------
    for rule in ruleset.status_immutable_rules:
        node_before = _find_node(state_before, rule.entity_id)
        if node_before is None:
            continue
        before_status = node_before.get("status") or _get_dotpath(node_before, "properties.status")
        if before_status != rule.locked_status:
            continue  # Lock not yet reached in state_before; no constraint.

        node_after_c = _find_node_in_corrected(corrected, rule.entity_id)
        if node_after_c is None:
            continue
        after_status = node_after_c.get("status") or _get_dotpath(node_after_c, "properties.status")
        if after_status == rule.locked_status:
            continue  # Status unchanged — OK.

        # Revert to locked status.
        if "status" in node_after_c:
            node_after_c["status"] = rule.locked_status
        else:
            _set_dotpath(node_after_c, "properties.status", rule.locked_status)
        msg = (
            f"StatusImmutable [{rule.entity_id}]: status '{after_status}' reverted to "
            f"locked value '{rule.locked_status}'."
        )
        violations.append(msg)
        any_correction = True
        logger.debug("axiom → %s", msg)

    # -- NumericCapRule ------------------------------------------------------
    for rule in ruleset.numeric_cap_rules:
        node_after_c = _find_node_in_corrected(corrected, rule.entity_id)
        if node_after_c is None:
            continue
        raw = _get_dotpath(node_after_c, rule.property_path)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            violations.append(
                f"NumericCap [{rule.entity_id}.{rule.property_path}]: "
                f"value '{raw}' is not numeric; cannot evaluate cap."
            )
            hard_invalid = True
            continue

        clamped = max(rule.min_val, min(rule.max_val, value))
        if clamped != value:
            _set_dotpath(node_after_c, rule.property_path, clamped)
            msg = (
                f"NumericCap [{rule.entity_id}.{rule.property_path}]: "
                f"value {value} clamped to {clamped} "
                f"(range [{rule.min_val}, {rule.max_val}])."
            )
            violations.append(msg)
            any_correction = True
            logger.debug("axiom → %s", msg)

    # -- ItemCountRule -------------------------------------------------------
    for rule in ruleset.item_count_rules:
        node_after = _find_node(state_after, rule.entity_id)
        if node_after is None:
            continue
        items = _get_dotpath(node_after, rule.item_property)
        if items is None:
            continue
        if not isinstance(items, list):
            violations.append(
                f"ItemCount [{rule.entity_id}.{rule.item_property}]: "
                f"property is not a list (type={type(items).__name__})."
            )
            hard_invalid = True
            continue
        count = len(items)
        if count > rule.max_count:
            msg = (
                f"ItemCount [{rule.entity_id}.{rule.item_property}]: "
                f"count {count} exceeds max {rule.max_count}."
            )
            violations.append(msg)
            hard_invalid = True
            logger.debug("axiom → %s", msg)

    # -- LocationBoundRule ---------------------------------------------------
    for rule in ruleset.location_bound_rules:
        current_loc = _item_location(state_after, rule.item_id)
        if current_loc is None:
            continue  # Item absent from state; no constraint applicable.
        if current_loc not in rule.allowed_location_ids:
            msg = (
                f"LocationBound [{rule.item_id}]: current location '{current_loc}' "
                f"is not in allowed set {rule.allowed_location_ids}."
            )
            violations.append(msg)
            hard_invalid = True
            logger.debug("axiom → %s", msg)

    return ValidationResult(
        valid=not hard_invalid,
        violations=violations,
        corrected_state=corrected if any_correction else None,
    )


# ---------------------------------------------------------------------------
# Ruleset loader
# ---------------------------------------------------------------------------


async def load_ruleset(path: str | Path) -> AxiomRuleset:
    """Asynchronously load and parse *path* as an AxiomRuleset.

    Returns an empty AxiomRuleset (no rules) if the file does not exist so
    callers never need to handle a missing rules file specially.
    """
    try:
        async with aiofiles.open(str(path), mode="r", encoding="utf-8") as fh:
            raw = await fh.read()
        ruleset = AxiomRuleset.model_validate_json(raw)
        logger.info(
            "axiom.load_ruleset → loaded %d item_count, %d status_immutable, "
            "%d numeric_cap, %d location_bound rules from %s",
            len(ruleset.item_count_rules),
            len(ruleset.status_immutable_rules),
            len(ruleset.numeric_cap_rules),
            len(ruleset.location_bound_rules),
            path,
        )
        return ruleset
    except FileNotFoundError:
        logger.warning("axiom.load_ruleset → %s not found, returning empty ruleset", path)
        return AxiomRuleset()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("axiom.load_ruleset → failed to parse %s: %s", path, exc)
        raise
