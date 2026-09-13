"""Offer only compatible native value sources, without choosing document fields."""

import json
from copy import deepcopy
from itertools import product

from .backends import ModelError
from .compiler import CompileError
from .native_note_checks import ownership

MAX_SOURCE_SETS = 1024
MAX_SOURCE_CHECKS = 2000000
MAX_CONTRACT_BYTES = 2 * 1024 * 1024


class OccurrenceContractError(ModelError):
    """Complete native constraints cannot be offered; no permissive fallback."""


def _limit(condition):
    if not condition:
        raise OccurrenceContractError("native_occurrence_contract_budget_exceeded")


def _sets(owned, axes):
    """Maximal sets preserve exactly the existing at-most-one-owner rules per axis.

    Unmarked sources remain possible in every set. Body and note axes are independent:
    a body and a note can coexist, but two distinct bodies or notes cannot. No nearest
    owner, text-based grouping, or first-source value selection is performed.
    """
    choices = [
        sorted(set().union(*(axis.get(ref, set()) for ref in owned))) or [None] for axis in axes
    ]
    count = 1
    for values in choices:
        count *= len(values)
    _limit(count <= MAX_SOURCE_SETS and count * len(owned) <= MAX_SOURCE_CHECKS)
    result, seen = [], set()
    for selected in product(*choices):
        allowed = tuple(
            ref
            for ref in owned
            if all(
                not axis.get(ref) or axis[ref] <= {owner}
                for axis, owner in zip(axes, selected, strict=True)
            )
        )
        if allowed and allowed not in seen:
            result.append(list(allowed))
            seen.add(allowed)
    return result


def constrain(schema, observation, region):
    """Specialize standard JSON Schema; keep the existing wire data and checked IR."""
    try:
        notes, bodies = ownership(observation, region)
    except CompileError as exc:
        raise OccurrenceContractError(str(exc)) from exc
    owned = region["nodeIds"]
    if not owned or (not notes and not bodies):
        return schema
    axes = [notes, {ref: {owner} for ref, owner in bodies.items()}]
    sources = _sets(owned, axes)
    definitions = schema["$defs"]

    def arrays(original, sets):
        choices = []
        for refs in sets:
            option = deepcopy(original)
            option["items"] = {"type": "string", "enum": refs}
            choices.append(option)
        return {"anyOf": choices} if choices else {"not": {}}

    for name in ["FieldChoice", "CellState"]:
        original = definitions[name]
        constrained, missing = deepcopy(original), deepcopy(original)
        constrained["properties"]["status"] = {"type": "string", "enum": ["present", "blank"]}
        constrained["properties"]["sourceRefs"] = arrays(
            original["properties"]["sourceRefs"], sources
        )
        missing["properties"]["status"] = {
            "type": "string",
            "enum": ["absent", "unreadable", "uncertain"],
        }
        definitions[name] = {"anyOf": [constrained, missing]}

    # An inherited bare present/blank state could bypass the explicit source choices.
    # Its fully equivalent CellState is still available; missing states may inherit.
    definitions["Row"]["properties"]["states"]["items"]["anyOf"][0] = {
        "type": "string",
        "enum": ["absent", "unreadable", "uncertain"],
    }

    original = definitions["MeaningChoice"]
    constrained, other, uncertain = (deepcopy(original) for _ in range(3))
    constrained["properties"]["kind"] = {"type": "string", "enum": ["note", "unit", "condition"]}
    constrained["properties"]["status"] = {"type": "string", "const": "interpreted"}
    anchors = []
    for refs in _sets(owned, [notes]):
        quote = deepcopy(definitions["SourceQuote"])
        quote["properties"]["sourceRef"] = {"type": "string", "enum": refs}
        option = deepcopy(original["properties"]["anchors"])
        option["items"] = {"anyOf": [{"type": "string", "enum": refs}, quote]}
        anchors.append(option)
    constrained["properties"]["anchors"] = {"anyOf": anchors} if anchors else {"not": {}}
    other["properties"]["kind"] = {
        "type": "string",
        "enum": ["definition", "reference", "relationship"],
    }
    other["properties"]["status"] = {"type": "string", "const": "interpreted"}
    uncertain["properties"]["status"] = {"type": "string", "const": "uncertain"}
    definitions["MeaningChoice"] = {"anyOf": [constrained, other, uncertain]}
    _limit(len(json.dumps(schema, ensure_ascii=False).encode()) <= MAX_CONTRACT_BYTES)
    return schema
