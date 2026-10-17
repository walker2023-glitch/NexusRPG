"""Regression tests for the shared world-state boundary."""

import json
from pathlib import Path

import pytest

from backend import world_state as ws


def test_validate_state_accepts_empty_and_campaign_state() -> None:
    ws.validate_state({"nodes": [], "edges": [], "metadata": {"campaign_id": "home"}})


@pytest.mark.parametrize(
    "state",
    [
        {"nodes": "bad", "edges": [], "metadata": {}},
        {"nodes": [{"id": "same"}, {"id": "same"}], "edges": [], "metadata": {}},
        {"nodes": [{"id": "n1"}], "edges": [{"source": "n1", "target": "", "relation": "knows"}], "metadata": {}},
        {"nodes": [], "edges": [], "metadata": {"campaign_id": 123}},
    ],
)
def test_validate_state_rejects_malformed_graphs(state: dict) -> None:
    with pytest.raises(ValueError):
        ws.validate_state(state)


@pytest.mark.asyncio
async def test_load_rejects_malformed_disk_state(tmp_path: Path) -> None:
    path = tmp_path / "world_state.json"
    path.write_text(json.dumps({"nodes": "bad", "edges": [], "metadata": {}}), encoding="utf-8")

    with pytest.raises(ValueError):
        await ws.load(str(path))


@pytest.mark.asyncio
async def test_save_rejects_malformed_state_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "world_state.json"
    with pytest.raises(ValueError):
        await ws.save(str(path), {"nodes": [{"id": "same"}, {"id": "same"}], "edges": [], "metadata": {}})

    assert not path.exists()
