"""Deterministic memory acceptance test for the first NexusRPG milestone.

This test deliberately bypasses Ollama and HTTP routes. It verifies the
canonical Chronos contract first: exact state survives 24 turns, a fresh
engine instance, and a divergent branch.
"""

import copy
from pathlib import Path

import pytest

from backend.chronos.commit_engine import ChronosEngine


def _initial_state() -> dict:
    return {
        "schema_version": 1,
        "campaign_id": "campaign_memory_acceptance",
        "nodes": [
            {
                "id": "char_hero",
                "label": "Mara",
                "entity_type": "char",
                "properties": {"known_facts": []},
            },
            {
                "id": "char_keeper",
                "label": "Keeper Elian",
                "entity_type": "char",
                "properties": {"known_facts": []},
            },
            {
                "id": "item_lantern",
                "label": "Star Lantern",
                "entity_type": "item",
                "properties": {"holder_id": "char_keeper"},
            },
            {
                "id": "quest_gate",
                "label": "Open the North Gate",
                "entity_type": "quest",
                "properties": {"status": "active"},
            },
        ],
        "edges": [
            {
                "source": "char_hero",
                "target": "char_keeper",
                "relation": "trusts",
                "properties": {"value": 0},
            }
        ],
        "metadata": {
            "active_location": "loc_village",
            "turn": 0,
            "scene_id": "scene_0",
        },
    }


def _node(state: dict, node_id: str) -> dict:
    return next(node for node in state["nodes"] if node["id"] == node_id)


def _edge(state: dict, source: str, target: str, relation: str) -> dict:
    return next(
        edge
        for edge in state["edges"]
        if edge["source"] == source
        and edge["target"] == target
        and edge["relation"] == relation
    )


def _main_turn(state: dict, turn: int) -> dict:
    """Apply a deterministic main-branch transition for one turn."""
    next_state = copy.deepcopy(state)
    next_state["metadata"]["turn"] = turn
    next_state["metadata"]["scene_id"] = f"scene_{turn}"

    if turn == 8:
        _node(next_state, "char_hero")["properties"]["known_facts"].append(
            "the_north_gate_will_open_at_dawn"
        )
    if turn == 12:
        _node(next_state, "char_keeper")["properties"]["known_facts"].append(
            "mara_is_trusted_with_the_lantern"
        )
        _edge(next_state, "char_hero", "char_keeper", "trusts")["properties"]["value"] = 2
    if turn == 16:
        _node(next_state, "item_lantern")["properties"]["holder_id"] = "char_hero"
    if turn == 20:
        _node(next_state, "quest_gate")["properties"]["status"] = "completed"

    return next_state


def _branch_turn(state: dict, turn: int) -> dict:
    """Apply a deliberately different outcome after the turn-12 fork."""
    next_state = copy.deepcopy(state)
    next_state["metadata"]["turn"] = turn
    next_state["metadata"]["scene_id"] = f"branch_scene_{turn}"

    if turn == 13:
        _node(next_state, "item_lantern")["properties"]["holder_id"] = "char_keeper"
    if turn == 14:
        _node(next_state, "char_keeper")["properties"]["known_facts"].append(
            "mara_has_not_earned_the_lantern"
        )
        _edge(next_state, "char_hero", "char_keeper", "trusts")["properties"]["value"] = -1
    if turn == 16:
        _node(next_state, "quest_gate")["properties"]["status"] = "failed"

    return next_state


def test_memory_survives_24_turn_restart_and_branch(tmp_path: Path) -> None:
    """Canonical facts survive a long run, restart, and divergent branch."""
    storage = tmp_path / "chronos"
    engine = ChronosEngine(storage)

    state = _initial_state()
    root_id = engine.commit("The adventure begins.", state, branch_label="main")
    engine.fork(root_id, "main")
    expected_main = {root_id: copy.deepcopy(state)}
    main_ids = [root_id]

    for turn in range(1, 25):
        state = _main_turn(state, turn)
        commit_id = engine.commit(
            f"Main scene {turn}.",
            state,
            parent_id=main_ids[-1],
            branch_label="main",
            choice_taken=f"main_choice_{turn}",
        )
        engine.update_branch_tip("main", commit_id)
        main_ids.append(commit_id)
        expected_main[commit_id] = copy.deepcopy(state)

    # Simulate closing and reopening the application.
    restarted = ChronosEngine(storage)
    main_tip = restarted.get_branch_tip("main")
    assert main_tip == main_ids[-1]
    assert restarted.reconstruct_state(main_tip) == expected_main[main_tip]

    # Every main-branch commit must reconstruct to its exact committed state.
    for commit_id, expected_state in expected_main.items():
        assert restarted.reconstruct_state(commit_id) == expected_state

    # Fork precisely at turn 12 and continue with different consequences.
    fork_id = main_ids[12]
    restarted.fork(fork_id, "alternate")
    branch_state = restarted.reconstruct_state(fork_id)
    branch_ids = [fork_id]

    for turn in range(13, 17):
        branch_state = _branch_turn(branch_state, turn)
        commit_id = restarted.commit(
            f"Alternate scene {turn}.",
            branch_state,
            parent_id=branch_ids[-1],
            branch_label="alternate",
            choice_taken=f"alternate_choice_{turn}",
        )
        restarted.update_branch_tip("alternate", commit_id)
        branch_ids.append(commit_id)

    alternate_tip = restarted.get_branch_tip("alternate")
    alternate_state = restarted.reconstruct_state(alternate_tip)
    main_state = restarted.reconstruct_state(restarted.get_branch_tip("main"))

    assert _node(main_state, "item_lantern")["properties"]["holder_id"] == "char_hero"
    assert _node(main_state, "quest_gate")["properties"]["status"] == "completed"
    assert _edge(main_state, "char_hero", "char_keeper", "trusts")["properties"]["value"] == 2

    assert _node(alternate_state, "item_lantern")["properties"]["holder_id"] == "char_keeper"
    assert _node(alternate_state, "quest_gate")["properties"]["status"] == "failed"
    assert _edge(alternate_state, "char_hero", "char_keeper", "trusts")["properties"]["value"] == -1

    # The alternate branch must not alter the main branch's facts or tip.
    assert restarted.get_branch_tip("main") == main_ids[-1]
    assert restarted.reconstruct_state(main_ids[-1]) == expected_main[main_ids[-1]]

    # The branch shares history through the fork and has no main-only commits.
    assert restarted.get_commit_metadata(branch_ids[0])["commit_id"] == fork_id
    assert set(branch_ids[1:]).isdisjoint(set(main_ids[13:]))

