"""Grouped transport preserves explicit source decisions, not a model quality test."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError
from test_table_protocol import TableModel, execute
from test_table_reference_wire import fixture

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.table_reference_wire import prepare_meaning_wire
from document_files.interpretation.table_selection import check_selection
from document_files.interpretation.table_selection_wire import (
    decode_selection,
    encode_selection,
    selection_schema,
)


def sources():
    return [
        {"sourceRef": "@s0", "text": "길이: mm; 조건은 20°C"},
        {"sourceRef": "n2", "text": ""},
        {"sourceRef": "n3", "text": "001.2300"},
        {"sourceRef": "n4", "text": "001.2300"},
        {"sourceRef": "n5", "text": " \t"},
        {"sourceRef": "n6", "text": "동일한 항목"},
    ]


def decisions(items=None):
    return {
        "sourceDecisions": {
            s["sourceRef"]: {
                "decision": "has_meaning" if i == 0 else "no_additional_meaning",
                "explanation": "Exact reason mentions @s0 and n4; 001.2300 그대로",
            }
            for i, s in enumerate(items or sources())
        }
    }


def test_groups_restore_distinct_equal_values_source_order_and_literal_reasons():
    value = decisions()
    value["sourceDecisions"]["n6"]["explanation"] += " "
    original = deepcopy(value)
    wire = encode_selection(value)
    assert len(wire["sourceChoices"]) == 3
    wire["sourceChoices"].reverse()
    for group in wire["sourceChoices"]:
        group["sourceRefs"].reverse()
    Draft202012Validator(selection_schema(sources())).validate(wire)
    assert decode_selection(wire, sources()) == original
    assert list(decode_selection(wire, sources())["sourceDecisions"]) == [
        s["sourceRef"] for s in sources()
    ]
    assert value == original
    restored = decode_selection(wire, sources())
    restored["sourceDecisions"]["n3"]["explanation"] = "changed"
    assert restored["sourceDecisions"]["n4"]["explanation"] != "changed"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "unknown",
        "duplicate_within",
        "duplicate_across",
        "empty_group",
        "extra",
        "canonical_reply",
        "wrong_role",
        "blank_reason",
        "long_reason",
        "bool_ref",
        "object_ref",
        "empty_positive",
        "group_extra",
        "wrong_revision",
    ],
)
def test_untrusted_grouped_output_is_checked_without_schema_enforcement(mutation):
    value = encode_selection(decisions())
    groups = value["sourceChoices"]
    if mutation == "missing":
        groups[1]["sourceRefs"].pop()
    elif mutation == "unknown":
        groups[1]["sourceRefs"][0] = "not-offered"
    elif mutation == "duplicate_within":
        groups[1]["sourceRefs"][-1] = groups[1]["sourceRefs"][0]
    elif mutation == "duplicate_across":
        groups.append(deepcopy(groups[0]))
    elif mutation == "empty_group":
        groups[0]["sourceRefs"] = []
    elif mutation == "extra":
        value["default"] = "no_additional_meaning"
    elif mutation == "canonical_reply":
        value = decisions()
    elif mutation == "wrong_role":
        groups[0]["decision"] = "skip"
    elif mutation == "blank_reason":
        groups[0]["explanation"] = " \t"
    elif mutation == "long_reason":
        groups[0]["explanation"] = "x" * 241
    elif mutation in {"bool_ref", "object_ref"}:
        groups[0]["sourceRefs"][0] = True if mutation == "bool_ref" else {}
    elif mutation == "empty_positive":
        groups[1]["decision"] = "has_meaning"
    elif mutation == "group_extra":
        groups[0]["scope"] = "all"
    else:
        value.update(action="rewrite", baseSelectionSHA256="x", reason="x")
    with pytest.raises(CompileError, match="table_selection_wire_"):
        decode_selection(value, sources())


def test_schema_keeps_existing_empty_and_bare_number_choice_restrictions():
    schema = selection_schema(sources())
    Draft202012Validator.check_schema(schema)
    for ref in ("n2", "n3", "n4"):
        value = decisions()
        value["sourceDecisions"][ref]["decision"] = "has_meaning"
        with pytest.raises(ValidationError):
            Draft202012Validator(schema).validate(encode_selection(value))
    # Whitespace and nonempty textual values are not excluded by category.
    value = decisions()
    for ref in ("n5", "n6"):
        value["sourceDecisions"][ref]["decision"] = "has_meaning"
    Draft202012Validator(schema).validate(encode_selection(value))


def test_empty_inventory_requires_explicit_empty_groups_and_roundtrips():
    schema = selection_schema([])
    Draft202012Validator.check_schema(schema)
    value = {"sourceChoices": []}
    Draft202012Validator(schema).validate(value)
    assert decode_selection(value, []) == {"sourceDecisions": {}}


def test_revision_keeps_hash_reason_and_every_canonical_choice():
    value = decisions() | {
        "action": "revise_selection",
        "baseSelectionSHA256": "unmodified-base",
        "reason": "Literal @s0 must not be translated here",
    }
    assert decode_selection(encode_selection(value), sources()) == value


def test_fifty_sources_share_explanation_without_omitting_or_defaulting_any_choice():
    items = [{"sourceRef": f"n{i}", "text": f"Code {i}"} for i in range(50)]
    value = decisions(items)
    wire = encode_selection(value)
    assert (
        len(json.dumps(wire, ensure_ascii=False)) < len(json.dumps(value, ensure_ascii=False)) / 4
    )
    assert decode_selection(wire, items) == check_selection(value, {"sources": items})
    assert sum(len(g["sourceRefs"]) for g in wire["sourceChoices"]) == 50


def test_reference_alias_decode_happens_after_group_expansion_without_touching_reasons():
    payload, contract, _, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    assert wire.identity is not None
    offered = wire.payload["meaningSources"]
    value = decisions(offered)
    original = wire.decode(decode_selection(encode_selection(value), offered))
    assert list(original["sourceDecisions"]) == [s["sourceRef"] for s in payload["meaningSources"]]
    assert [d["explanation"] for d in original["sourceDecisions"].values()] == [
        d["explanation"] for d in value["sourceDecisions"].values()
    ]


def test_actual_engine_keeps_fixed_output_cap_canonical_history_and_rejects_old_checkpoint():
    model, states = TableModel(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "complete"
    assert model.requests[-1].max_output_tokens == 1536
    assert "sourceChoices" in model.requests[-1].output_schema["properties"]
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert "sourceDecisions" in progress["sourceSelections"][0]["response"]
    old = deepcopy(states[-1])
    old["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v22"
    calls = len(model.requests)
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=old)
    assert len(model.requests) == calls


def test_truncated_grouped_response_preserves_structure_but_no_selection_is_accepted():
    class Truncated(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if "sourceChoices" in request.output_schema.get("properties", {}):
                return InferenceResponse(response.text[:-3], response.usage, "length")
            return response

    model, states = Truncated(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "partial" and len(result["data"]["records"]) == 2
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert not progress.get("sourceSelections")
    assert any(i["code"] == "ai_response_incomplete" for i in result["issues"])
