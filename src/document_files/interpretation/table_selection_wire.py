"""Shared model reasons; canonical decisions cover every source exactly once."""

from copy import deepcopy

from .compiler import CompileError
from .table_source_decisions import bare_number

VERSION = "document-files.table-selection-wire.v2"
ROLES = {"has_meaning", "no_additional_meaning", "unresolved", "unreviewed"}


def _require(condition, code):
    if not condition:
        raise CompileError(code)


def selection_schema(sources):
    count = len(sources)
    definitions = {}
    for name, roles in (("Choice", ROLES), ("ReviewChoice", ROLES - {"has_meaning"})):
        definitions[name] = {
            "type": "array",
            "prefixItems": [
                {"type": "string", "enum": sorted(roles)},
                {"type": "integer", "minimum": 0, "maximum": max(0, count - 1)},
            ],
            "minItems": 2,
            "maxItems": 2,
            "items": False,
        }
    properties = {
        s["sourceRef"]: {
            "$ref": "#/$defs/Choice"
            if s["text"] and not bare_number(s["text"])
            else "#/$defs/ReviewChoice"
        }
        for s in sources
    }
    return {
        "type": "object",
        "properties": {
            "reasonTable": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 240},
                "minItems": 1 if count else 0,
                "maxItems": count,
                "uniqueItems": True,
            },
            "sourceDecisions": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
        "required": ["reasonTable", "sourceDecisions"],
        "additionalProperties": False,
        "$defs": definitions,
    }


def encode_selection(value):
    """Share identical literal reasons, never infer or group source decisions.

    This internal display helper preserves other response members; the receiving
    decoder and contract, not this helper, validate response shape and coverage.
    """
    result = deepcopy(value)
    reasons, decisions = [], {}
    for ref, choice in result.pop("sourceDecisions").items():
        reason = choice["explanation"]
        if reason not in reasons:
            reasons.append(reason)
        decisions[ref] = [choice["decision"], reasons.index(reason)]
    return {"reasonTable": reasons, **result, "sourceDecisions": decisions}


def decode_selection(value, sources):
    """Restore a closed source-keyed choice map before reference translation."""
    revision_keys = {"action", "baseSelectionSHA256", "reason"}
    required = {"reasonTable", "sourceDecisions"}
    _require(
        isinstance(value, dict)
        and (
            set(value) == required
            or (set(value) == required | revision_keys and value["action"] == "revise_selection")
        ),
        "table_selection_wire_shape",
    )
    refs = {s["sourceRef"]: s["text"] for s in sources}
    _require(len(refs) == len(sources), "table_selection_wire_inventory")
    reasons, choices = value["reasonTable"], value["sourceDecisions"]
    _require(
        isinstance(reasons, list)
        and len(reasons) <= len(refs)
        and all(isinstance(r, str) and 0 < len(r) <= 240 and bool(r.strip()) for r in reasons),
        "table_selection_wire_reasons",
    )
    _require(len(reasons) == len(set(reasons)), "table_selection_wire_duplicate_reason")
    _require(
        isinstance(choices, dict) and set(choices) == set(refs), "table_selection_wire_inventory"
    )
    decisions, used = {}, set()
    for ref, text in refs.items():
        choice = choices[ref]
        _require(
            isinstance(choice, list)
            and len(choice) == 2
            and isinstance(choice[0], str)
            and choice[0] in ROLES
            and type(choice[1]) is int
            and 0 <= choice[1] < len(reasons),
            "table_selection_wire_choice",
        )
        _require(choice[0] != "has_meaning" or text != "", "table_selection_wire_empty_source")
        used.add(choice[1])
        decisions[ref] = {"decision": choice[0], "explanation": reasons[choice[1]]}
    _require(used == set(range(len(reasons))), "table_selection_wire_unused_reason")
    return {k: deepcopy(v) for k, v in value.items() if k not in required} | {
        "sourceDecisions": decisions
    }
