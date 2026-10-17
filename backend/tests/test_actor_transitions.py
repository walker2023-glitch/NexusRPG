"""Focused tests for the actor transition preparation seam."""

import copy

import pytest

from backend.axiom.engine import AxiomRuleset, NumericCapRule
from backend.chronos.transitions import TransitionError
from backend.routes.actor_router import _prepare_state_for_commit


@pytest.fixture
def state() -> dict:
    return {
        "nodes": [
            {
                "id": "char_hero",
                "properties": {"health": 100},
            }
        ],
        "edges": [],
        "metadata": {"turn": 1},
    }


def test_actor_preparation_applies_transition_and_axiom_correction(state: dict) -> None:
    original = copy.deepcopy(state)
    ruleset = AxiomRuleset(
        numeric_cap_rules=[
            NumericCapRule(
                entity_id="char_hero",
                property_path="properties.health",
                min_val=0,
                max_val=100,
            )
        ]
    )

    final_state, validation = _prepare_state_for_commit(
        state,
        [
            {
                "op": "replace",
                "path": "/nodes/0/properties/health",
                "value": 500,
            }
        ],
        ruleset,
    )

    assert state == original
    assert final_state["nodes"][0]["properties"]["health"] == 100
    assert validation.valid is True
    assert len(validation.violations) == 1
    assert "clamped to 100" in validation.violations[0]


def test_actor_preparation_rejects_forbidden_transition(state: dict) -> None:
    ruleset = AxiomRuleset()

    with pytest.raises(TransitionError):
        _prepare_state_for_commit(
            state,
            [
                {
                    "op": "replace",
                    "path": "/nodes/0/properties",
                    "value": {"admin": True},
                }
            ],
            ruleset,
        )
