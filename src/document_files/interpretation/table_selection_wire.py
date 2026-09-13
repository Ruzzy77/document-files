"""Grouped model choices; canonical decisions still cover every source exactly once."""

from copy import deepcopy

from .compiler import CompileError
from .table_source_decisions import bare_number

VERSION = "document-files.table-selection-wire.v1"
ROLES = {"has_meaning", "no_additional_meaning", "unresolved", "unreviewed"}


def _require(condition, code):
    if not condition:
        raise CompileError(code)


def selection_schema(sources):
    refs = [s["sourceRef"] for s in sources]
    positive = [s["sourceRef"] for s in sources if s["text"] and not bare_number(s["text"])]

    def group(choices, roles):
        return {
            "type": "object",
            "properties": {
                "sourceRefs": {
                    "type": "array",
                    "items": {"type": "string", "enum": choices},
                    "minItems": 1,
                    "maxItems": len(refs),
                    "uniqueItems": True,
                },
                "decision": {"type": "string", "enum": sorted(roles)},
                "explanation": {"type": "string", "minLength": 1, "maxLength": 240},
            },
            "required": ["sourceRefs", "decision", "explanation"],
            "additionalProperties": False,
        }

    variants = ([group(positive, {"has_meaning"})] if positive else []) + (
        [group(refs, ROLES - {"has_meaning"})] if refs else []
    )
    return {
        "type": "object",
        "properties": {
            "sourceChoices": {
                "type": "array",
                "items": {"anyOf": variants} if variants else False,
                "minItems": 1 if refs else 0,
                "maxItems": len(refs),
            }
        },
        "required": ["sourceChoices"],
        "additionalProperties": False,
        "$defs": {},
    }


def encode_selection(value):
    """Share only identical decisions and literal reasons, never infer a choice.

    This internal display helper preserves other response members; the receiving
    decoder and contract, not this helper, validate response shape and coverage.
    """
    result = deepcopy(value)
    groups = {}
    for ref, choice in result.pop("sourceDecisions").items():
        key = choice["decision"], choice["explanation"]
        groups.setdefault(key, {"sourceRefs": [], **choice})["sourceRefs"].append(ref)
    result["sourceChoices"] = list(groups.values())
    return result


def decode_selection(value, sources):
    """Require an explicit, disjoint, complete inventory; restore original order."""
    revision_keys = {"action", "baseSelectionSHA256", "reason"}
    _require(
        isinstance(value, dict)
        and (
            set(value) == {"sourceChoices"}
            or (
                set(value) == {"sourceChoices"} | revision_keys
                and value["action"] == "revise_selection"
            )
        ),
        "table_selection_wire_shape",
    )
    refs = {s["sourceRef"]: s["text"] for s in sources}
    _require(len(refs) == len(sources), "table_selection_wire_inventory")
    groups = value["sourceChoices"]
    _require(isinstance(groups, list) and len(groups) <= len(refs), "table_selection_wire_shape")
    decisions = {}
    for item in groups:
        _require(
            isinstance(item, dict)
            and set(item) == {"sourceRefs", "decision", "explanation"}
            and isinstance(item["sourceRefs"], list)
            and 0 < len(item["sourceRefs"]) <= len(refs)
            and isinstance(item["decision"], str)
            and item["decision"] in ROLES
            and isinstance(item["explanation"], str)
            and 0 < len(item["explanation"]) <= 240
            and bool(item["explanation"].strip()),
            "table_selection_wire_choice",
        )
        for ref in item["sourceRefs"]:
            _require(isinstance(ref, str) and ref in refs, "table_selection_wire_unknown_source")
            _require(ref not in decisions, "table_selection_wire_duplicate_source")
            _require(
                item["decision"] != "has_meaning" or refs[ref] != "",
                "table_selection_wire_empty_source",
            )
            decisions[ref] = {k: item[k] for k in ("decision", "explanation")}
    _require(set(decisions) == set(refs), "table_selection_wire_inventory")
    return {k: deepcopy(v) for k, v in value.items() if k != "sourceChoices"} | {
        "sourceDecisions": {ref: decisions[ref] for ref in refs}
    }
