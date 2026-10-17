"""Validated canonical-state transitions.

The narrator may propose a transition, but it never receives permission to
mutate the world directly. This module applies a small, explicit JSON Patch
allowlist to a copy of canonical state and raises before returning anything
when the proposal is unsafe or invalid.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping, Sequence

import jsonpatch

_ALLOWED_OPS = frozenset({"add", "replace", "remove"})
_MAX_OPERATIONS = 32
_PROPERTY_PATH = re.compile(
    r"^/(nodes|edges)/(0|[1-9][0-9]*)/properties/"
    r"[A-Za-z0-9_.-]+(?:/(?:[A-Za-z0-9_.-]+|-[^-]))*$"
)
_METADATA_PATH = re.compile(r"^/metadata/[A-Za-z0-9_.-]+$")


class TransitionError(ValueError):
    """Raised when an untrusted transition cannot be safely applied."""


def _is_allowed_path(path: object) -> bool:
    return isinstance(path, str) and (
        _PROPERTY_PATH.fullmatch(path) is not None
        or _METADATA_PATH.fullmatch(path) is not None
    )


def apply_state_transitions(
    state: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply bounded, allowlisted JSON Patch operations to a copied state.

    Allowed paths are canonical node/edge properties and metadata fields.
    The input state is never mutated. A failed operation raises
    TransitionError and returns no partial state.
    """
    if not isinstance(state, Mapping):
        raise TransitionError("Canonical state must be a JSON object.")
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise TransitionError("Transitions must be a list of operation objects.")
    if len(operations) > _MAX_OPERATIONS:
        raise TransitionError(f"Too many transition operations; maximum is {_MAX_OPERATIONS}.")

    patch: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise TransitionError(f"Transition {index} must be an object.")

        op = operation.get("op")
        path = operation.get("path")
        if op not in _ALLOWED_OPS:
            raise TransitionError(f"Transition {index} uses unsupported operation: {op!r}.")
        if not _is_allowed_path(path):
            raise TransitionError(f"Transition {index} uses a forbidden path: {path!r}.")
        if op != "remove" and "value" not in operation:
            raise TransitionError(f"Transition {index} requires a value.")

        item = {"op": op, "path": path}
        if op != "remove":
            item["value"] = copy.deepcopy(operation["value"])
        patch.append(item)

    try:
        result = jsonpatch.apply_patch(copy.deepcopy(dict(state)), patch, in_place=False)
    except (jsonpatch.JsonPatchException, TypeError, ValueError) as exc:
        raise TransitionError(f"Transition could not be applied: {exc}") from exc

    if not isinstance(result, dict):
        raise TransitionError("A transition must produce a JSON object state.")
    return result
