"""Finite source statuses; explanation prose is not part of classification output."""

from copy import deepcopy

from .compiler import CompileError
from .table_source_decisions import bare_number

VERSION = "document-files.table-selection-wire.v4"
ROLES = {"has_meaning", "no_additional_meaning", "unresolved", "unreviewed"}


def _require(condition, code):
    if not condition:
        raise CompileError(code)


def selection_schema(sources):
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
            "sourceDecisions": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
        "required": ["sourceDecisions"],
        "additionalProperties": False,
        "$defs": {
            name: {"type": "string", "enum": sorted(roles)}
            for name, roles in (("Choice", ROLES), ("ReviewChoice", ROLES - {"has_meaning"}))
        },
    }


def encode_selection(value):
    """Display saved statuses; never silently discard a supplied explanation."""
    result = deepcopy(value)
    choices = result["sourceDecisions"]
    _require(
        all(
            isinstance(d, dict)
            and set(d) == {"decision", "explanation"}
            and d["explanation"] is None
            for d in choices.values()
        ),
        "table_selection_wire_explanation_would_be_lost",
    )
    result["sourceDecisions"] = {ref: d["decision"] for ref, d in choices.items()}
    return result


def decode_selection(value, sources):
    """Require every source choice and explicitly retain absent explanation state."""
    revision_keys = {"action", "baseSelectionSHA256", "reason"}
    required = {"sourceDecisions"}
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
    choices = value["sourceDecisions"]
    _require(
        isinstance(choices, dict) and set(choices) == set(refs), "table_selection_wire_inventory"
    )
    decisions = {}
    for ref, text in refs.items():
        choice = choices[ref]
        _require(isinstance(choice, str) and choice in ROLES, "table_selection_wire_choice")
        _require(choice != "has_meaning" or text != "", "table_selection_wire_empty_source")
        decisions[ref] = {"decision": choice, "explanation": None}
    return {k: deepcopy(v) for k, v in value.items() if k not in required} | {
        "sourceDecisions": decisions
    }
