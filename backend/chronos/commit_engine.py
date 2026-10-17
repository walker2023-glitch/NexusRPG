"""Chronos branching engine — ZSTD-compressed RFC 6902 JSON Patch commit graph.

Each commit stores either a full JSON snapshot of world_state or a compact
RFC 6902 delta (jsonpatch) against its parent.  Snapshots are written every
SNAPSHOT_INTERVAL commits so reconstruction never requires replaying more than
SNAPSHOT_INTERVAL - 1 patch applications regardless of branch depth.

Directory layout under storage_path/
  objects/{commit_id}   — ZSTD-compressed JSON of ChronosCommit (snapshot or delta)
  refs/{branch_name}    — plain-text file containing a single commit_id (branch tip)
  index.json            — lightweight metadata cache (no full states), loaded on init
"""

import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import jsonpatch
import zstandard as zstd

from backend.world_state import validate_state

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL: int = 6

_ZSTD_LEVEL = 3
_SCENE_PREVIEW_LEN = 120
_BRANCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_COMMIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def _validate_commit_id(commit_id: str) -> None:
    """Reject unsafe commit IDs before using them as object filenames."""
    if not isinstance(commit_id, str) or _COMMIT_ID_RE.fullmatch(commit_id) is None:
        raise ValueError("Invalid commit ID.")


def _validate_branch_name(branch_name: str) -> None:
    """Reject unsafe branch names before using them as ref filenames."""
    if not _BRANCH_NAME_RE.fullmatch(branch_name) or branch_name in {".", ".."}:
        raise ValueError(
            "Invalid branch name: use 1-64 characters from letters, numbers, "
            "dot, underscore, or hyphen; it must start with a letter or number."
        )


# ---------------------------------------------------------------------------
# ChronosCommit dataclass
# ---------------------------------------------------------------------------


@dataclass
class ChronosCommit:
    """Immutable record of a single narrative moment in the Chronos DAG.

    Exactly one of ``snapshot`` or ``delta`` must be non-None; the other must
    be None.  Use ``to_compressed_bytes`` / ``from_compressed_bytes`` for disk
    serialisation.
    """

    commit_id: str
    parent_id: str | None
    scene_text: str
    depth: int
    timestamp: float
    branch_label: str | None = None
    choice_taken: str | None = None
    campaign_id: str | None = None
    snapshot: dict | None = None
    delta: list | None = None

    def __post_init__(self) -> None:
        """Enforce the snapshot XOR delta invariant."""
        has_snapshot = self.snapshot is not None
        has_delta = self.delta is not None
        if has_snapshot == has_delta:
            raise ValueError(
                "ChronosCommit must have exactly one of 'snapshot' or 'delta' set, not both or neither."
            )

    @property
    def is_snapshot(self) -> bool:
        """True when this commit stores a full world-state snapshot."""
        return self.snapshot is not None

    def to_compressed_bytes(self) -> bytes:
        """Serialise the commit to a ZSTD-compressed JSON blob (level 3)."""
        raw = json.dumps(asdict(self), ensure_ascii=False).encode("utf-8")
        cctx = zstd.ZstdCompressor(level=_ZSTD_LEVEL)
        return cctx.compress(raw)

    @classmethod
    def from_compressed_bytes(cls, data: bytes) -> "ChronosCommit":
        """Deserialise a ZSTD-compressed blob back into a ChronosCommit instance."""
        dctx = zstd.ZstdDecompressor()
        raw = dctx.decompress(data)
        d = json.loads(raw.decode("utf-8"))
        return cls(**d)


# ---------------------------------------------------------------------------
# ChronosEngine
# ---------------------------------------------------------------------------


class ChronosEngine:
    """Manages the Chronos DAG: commits, branching, reconstruction, and pruning.

    All public methods are synchronous; callers in async route handlers must
    wrap them with ``asyncio.to_thread``.

    Parameters
    ----------
    storage_path:
        Root directory for all Chronos data.  Created on first use.
    """

    def __init__(self, storage_path: Path) -> None:
        self._root = Path(storage_path)
        self._objects_dir = self._root / "objects"
        self._refs_dir = self._root / "refs"
        self._index_path = self._root / "index.json"

        self._objects_dir.mkdir(parents=True, exist_ok=True)
        self._refs_dir.mkdir(parents=True, exist_ok=True)

        self._index: dict[str, dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------

    def _load_index(self) -> dict[str, dict[str, Any]]:
        """Read index.json from disk, returning an empty dict if absent."""
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.error("chronos index.json corrupted: %s", exc)
                raise RuntimeError("Chronos index is corrupted; refusing to start with an empty index.") from exc
        return {}

    def _save_index(self) -> None:
        """Atomically persist the in-memory index to disk."""
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._index_path)

    def _index_entry(self, commit: ChronosCommit) -> dict[str, Any]:
        """Build a lightweight metadata dict suitable for storage in the index."""
        return {
            "commit_id": commit.commit_id,
            "parent_id": commit.parent_id,
            "depth": commit.depth,
            "branch_label": commit.branch_label,
            "choice_taken": commit.choice_taken,
            "campaign_id": commit.campaign_id,
            "is_snapshot": commit.is_snapshot,
            "timestamp": commit.timestamp,
            "scene_preview": commit.scene_text[:_SCENE_PREVIEW_LEN],
        }

    # ------------------------------------------------------------------
    # Object I/O
    # ------------------------------------------------------------------

    def _object_path(self, commit_id: str) -> Path:
        _validate_commit_id(commit_id)
        return self._objects_dir / commit_id

    def _write_object(self, commit: ChronosCommit) -> None:
        """Compress and write a commit object to disk."""
        self._object_path(commit.commit_id).write_bytes(commit.to_compressed_bytes())

    def _read_object(self, commit_id: str) -> ChronosCommit:
        """Read and decompress a commit object from disk."""
        path = self._object_path(commit_id)
        if not path.exists():
            raise FileNotFoundError(f"Chronos object not found: {commit_id}")
        return ChronosCommit.from_compressed_bytes(path.read_bytes())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def commit(
        self,
        scene_text: str,
        current_world_state: dict[str, Any],
        parent_id: str | None = None,
        choice_taken: str | None = None,
        branch_label: str | None = None,
    ) -> str:
        """Record a new moment in the narrative graph and return its commit_id.

        Snapshot policy: a full snapshot is written when there is no parent
        (root commit) or when ``depth % SNAPSHOT_INTERVAL == 0``.  All other
        commits store a compact RFC 6902 JSON Patch delta against their parent's
        reconstructed state, which bounds reconstruction cost to at most
        ``SNAPSHOT_INTERVAL - 1`` patch applications.
        """
        commit_id = str(uuid.uuid4())
        campaign_id = current_world_state.get("metadata", {}).get("campaign_id")
        if campaign_id is not None and not isinstance(campaign_id, str):
            raise ValueError("campaign_id must be a string when present in state metadata")

        # Resolve depth from the parent's indexed depth and enforce campaign isolation.
        parent_depth = -1
        if parent_id is not None:
            parent_meta = self._index.get(parent_id)
            if parent_meta is None:
                raise ValueError(f"Unknown parent commit: {parent_id}")
            parent_depth = parent_meta["depth"]
            parent_campaign_id = parent_meta.get("campaign_id")
            if parent_campaign_id is not None and campaign_id != parent_campaign_id:
                raise ValueError(
                    "Campaign mismatch: child state cannot attach to a parent from another campaign."
                )
        depth = parent_depth + 1

        use_snapshot = (parent_id is None) or (depth % SNAPSHOT_INTERVAL == 0)

        if use_snapshot:
            new_commit = ChronosCommit(
                commit_id=commit_id,
                parent_id=parent_id,
                scene_text=scene_text,
                depth=depth,
                timestamp=time.time(),
                branch_label=branch_label,
                choice_taken=choice_taken,
                campaign_id=campaign_id,
                snapshot=copy.deepcopy(current_world_state),
                delta=None,
            )
            logger.debug("chronos.commit → snapshot depth=%d id=%s", depth, commit_id)
        else:
            parent_state = self.reconstruct_state(parent_id)
            patch = jsonpatch.make_patch(parent_state, current_world_state)
            new_commit = ChronosCommit(
                commit_id=commit_id,
                parent_id=parent_id,
                scene_text=scene_text,
                depth=depth,
                timestamp=time.time(),
                branch_label=branch_label,
                choice_taken=choice_taken,
                campaign_id=campaign_id,
                snapshot=None,
                delta=patch.patch,
            )
            logger.debug(
                "chronos.commit → delta depth=%d ops=%d id=%s",
                depth, len(patch.patch), commit_id,
            )

        self._write_object(new_commit)
        self._index[commit_id] = self._index_entry(new_commit)
        self._save_index()
        return commit_id

    def reconstruct_state(self, commit_id: str) -> dict[str, Any]:
        """Rebuild the world_state dict at *commit_id* by replaying deltas.

        Walks the ancestor chain backwards until a snapshot node is found, then
        applies the collected deltas in chronological order.  The maximum number
        of patch applications is ``SNAPSHOT_INTERVAL - 1``.
        """
        # Collect the chain back to the nearest snapshot.
        chain: list[ChronosCommit] = []
        current_id: str | None = commit_id
        while current_id is not None:
            c = self._read_object(current_id)
            if c.commit_id != current_id:
                raise RuntimeError(
                    f"Chronos object identity mismatch for commit {current_id}."
                )
            if c.parent_id is not None:
                _validate_commit_id(c.parent_id)
            chain.append(c)
            if c.is_snapshot:
                break
            current_id = c.parent_id

        if not chain or not chain[-1].is_snapshot:
            raise RuntimeError(
                f"Could not find a snapshot ancestor for commit {commit_id}; "
                "the object store may be corrupt."
            )

        # Validate links before applying any deltas.
        for child, parent in zip(chain, chain[1:]):
            if child.parent_id != parent.commit_id:
                raise RuntimeError("Chronos parent link is inconsistent.")
            if child.depth != parent.depth + 1:
                raise RuntimeError("Chronos commit depth is inconsistent.")
            if parent.campaign_id is not None and child.campaign_id != parent.campaign_id:
                raise RuntimeError("Chronos campaign lineage is inconsistent.")

        # chain[-1] is the snapshot; chain[0] is the requested commit.
        state: dict[str, Any] = copy.deepcopy(chain[-1].snapshot)  # type: ignore[arg-type]

        # Replay deltas from oldest to newest (skip the snapshot itself).
        for c in reversed(chain[:-1]):
            if c.delta:
                state = jsonpatch.apply_patch(state, c.delta, in_place=False)

        try:
            validate_state(state)
        except ValueError as exc:
            raise RuntimeError("Reconstructed Chronos state failed validation.") from exc

        return state

    def fork(self, from_commit_id: str, branch_name: str) -> str:
        """Create a named branch ref pointing to *from_commit_id*.

        This is a zero-copy operation — no commit objects are duplicated.
        Returns *from_commit_id* unchanged so callers can chain the result.
        """
        _validate_branch_name(branch_name)
        if from_commit_id not in self._index:
            raise ValueError(f"Cannot fork from unknown commit: {from_commit_id}")
        self.update_branch_tip(branch_name, from_commit_id)
        logger.info("chronos.fork → branch '%s' → %s", branch_name, from_commit_id)
        return from_commit_id

    def get_branch_tip(self, branch_name: str) -> str | None:
        """Return the commit_id at the tip of *branch_name*, or None if the ref does not exist."""
        _validate_branch_name(branch_name)
        ref_path = self._refs_dir / branch_name
        if ref_path.exists():
            commit_id = ref_path.read_text(encoding="utf-8").strip()
            _validate_commit_id(commit_id)
            if commit_id not in self._index:
                raise ValueError(f"Branch ref points to unknown commit: {commit_id}")
            return commit_id
        return None

    def update_branch_tip(self, branch_name: str, commit_id: str) -> None:
        """Point *branch_name* at an existing commit, creating the ref if needed."""
        _validate_branch_name(branch_name)
        _validate_commit_id(commit_id)
        if commit_id not in self._index:
            raise ValueError(f"Cannot point branch at unknown commit: {commit_id}")
        ref_path = self._refs_dir / branch_name
        ref_path.write_text(commit_id, encoding="utf-8")
        logger.debug("chronos.update_branch_tip → %s → %s", branch_name, commit_id)

    def get_commit_metadata(self, commit_id: str) -> dict[str, Any]:
        """Return a lightweight metadata dict for *commit_id* without loading the full state.

        Keys: commit_id, parent_id, depth, branch_label, choice_taken,
              campaign_id, is_snapshot, timestamp, scene_preview.
        """
        meta = self._index.get(commit_id)
        if meta is None:
            raise KeyError(f"Commit not found in index: {commit_id}")
        return dict(meta)

    def list_all_metadata(self) -> list[dict[str, Any]]:
        """Return metadata for every commit in the index, sorted by depth then timestamp."""
        entries = list(self._index.values())
        entries.sort(key=lambda m: (m["depth"], m["timestamp"]))
        return entries

    def list_branches(self) -> dict[str, str]:
        """Return a mapping of branch_name → commit_id for every existing ref."""
        branches: dict[str, str] = {}
        for ref_file in self._refs_dir.iterdir():
            if ref_file.is_file():
                _validate_branch_name(ref_file.name)
                tip = self.get_branch_tip(ref_file.name)
                if tip is not None:
                    branches[ref_file.name] = tip
        return branches

    def prune_orphaned_branches(self) -> int:
        """Delete object files that are not reachable from any branch ref.

        Performs a DFS from every ref tip through the parent chain.  Object
        files for commits absent from the reachable set are deleted.  Index
        entries are cleaned up accordingly.

        Returns the number of object files deleted.
        """
        reachable: set[str] = set()

        def _dfs(cid: str) -> None:
            if cid in reachable:
                return
            reachable.add(cid)
            meta = self._index.get(cid)
            if meta and meta.get("parent_id"):
                _dfs(meta["parent_id"])

        for commit_id in self.list_branches().values():
            _dfs(commit_id)

        deleted = 0
        for obj_path in list(self._objects_dir.iterdir()):
            if obj_path.name not in reachable:
                obj_path.unlink(missing_ok=True)
                self._index.pop(obj_path.name, None)
                deleted += 1
                logger.info("chronos.prune → deleted orphan %s", obj_path.name)

        if deleted:
            self._save_index()

        logger.info("chronos.prune → %d orphaned object(s) deleted", deleted)
        return deleted
