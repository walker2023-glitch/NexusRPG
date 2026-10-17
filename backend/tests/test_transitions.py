"""Tests for the bounded canonical-state transition boundary."""

import copy

import pytest

from backend.chronos.transitions import TransitionError, apply_state_transitions


@pytest.fixture
def state() -> dict:
    return {
        "nodes": [
            {
                "id": "char_hero",
                "properties": {"health": 100, "known_facts": []},
            }
        ],
        "edges": [],
        "metadata": {"turn": 1},
    }


def test_valid_transition_returns_new_state_without_mutating_input(state: dict) -> None:
    original = copy.deepcopy(state)

    result = apply_state_transitions(
        state,
        [
            {"op": "replace", "path": "/nodes/0/properties/health", "value": 80},
            {
                "op": "add",
                "path": "/nodes/0/properties/known_facts/-",
                "value": "gate_opens_at_dawn",
            },
            {"op": "replace", "path": "/metadata/turn", "value": 2},
        ],
    )

    assert state == original
    assert result["nodes"][0]["properties"]["health"] == 80
    assert result["nodes"][0]["properties"]["known_facts"] == ["gate_opens_at_dawn"]
    assert result["metadata"]["turn"] == 2


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "/nodes/0", "value": {}},
        {"op": "replace", "path": "/world_state", "value": {}},
        {"op": "replace", "path": "/nodes/0/properties/health", "value": 80, "from": "../../secret"},
        {"op": "copy", "path": "/nodes/0/properties/health", "from": "/metadata/turn"},
        {"op": "replace", "path": "/nodes/0/properties/health"},
    ],
)
def test_invalid_transition_is_rejected_without_partial_mutation(
    state: dict, operation: dict
) -> None:
    original = copy.deepcopy(state)

    with pytest.raises(TransitionError):
        apply_state_transitions(state, [operation])

    assert state == original


def test_transition_count_is_bounded(state: dict) -> None:
    operations = [
        {"op": "replace", "path": "/metadata/turn", "value": i}
        for i in range(33)
    ]

    with pytest.raises(TransitionError, match="maximum"):
        apply_state_transitions(state, operations)
