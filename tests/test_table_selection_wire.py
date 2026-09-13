"""Reason sharing preserves explicit source decisions, not a model quality test."""

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


def test_shared_reasons_restore_distinct_equal_values_source_order_and_literal_text():
    value = decisions()
    value["sourceDecisions"]["n6"]["explanation"] += " "
    original = deepcopy(value)
    wire = encode_selection(value)
    assert len(wire["reasonTable"]) == 2
    wire["sourceDecisions"] = dict(reversed(list(wire["sourceDecisions"].items())))
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
        "extra",
        "canonical_reply",
        "wrong_role",
        "blank_reason",
        "long_reason",
        "bool_index",
        "negative_index",
        "out_of_range",
        "empty_positive",
        "wrong_revision",
        "unused_reason",
        "duplicate_reason",
        "wrong_pair",
        "object_reason",
    ],
)
def test_untrusted_shared_reason_output_is_checked_without_schema_enforcement(mutation):
    value = encode_selection(decisions())
    choices = value["sourceDecisions"]
    if mutation == "missing":
        choices.pop("n6")
    elif mutation == "unknown":
        choices["not-offered"] = choices.pop("n6")
    elif mutation == "extra":
        value["default"] = "no_additional_meaning"
    elif mutation == "canonical_reply":
        value = decisions()
    elif mutation == "wrong_role":
        choices["@s0"]["decision"] = "skip"
    elif mutation == "blank_reason":
        value["reasonTable"][0] = " \t"
    elif mutation == "long_reason":
        value["reasonTable"][0] = "x" * 241
    elif mutation == "bool_index":
        choices["@s0"]["reasonIndex"] = False
    elif mutation == "negative_index":
        choices["@s0"]["reasonIndex"] = -1
    elif mutation == "out_of_range":
        choices["@s0"]["reasonIndex"] = len(value["reasonTable"])
    elif mutation == "empty_positive":
        choices["n2"]["decision"] = "has_meaning"
    elif mutation == "unused_reason":
        value["reasonTable"].append("Unused")
    elif mutation == "duplicate_reason":
        value["reasonTable"].append(value["reasonTable"][0])
    elif mutation == "wrong_pair":
        choices["@s0"] = ["has_meaning", 0]
    elif mutation == "object_reason":
        value["reasonTable"][0] = {}
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


def test_choice_schema_uses_supported_closed_objects_not_runtime_ignored_tuples():
    schema = selection_schema(sources())
    for definition in schema["$defs"].values():
        assert definition["type"] == "object"
        assert definition["required"] == ["decision", "reasonIndex"]
        assert definition["additionalProperties"] is False
    for choice in [
        ["has_meaning", 0],
        {},
        {"decision": "has_meaning"},
        {"decision": "has_meaning", "reasonIndex": 0, "extra": True},
    ]:
        value = encode_selection(decisions())
        value["sourceDecisions"]["@s0"] = choice
        assert not Draft202012Validator(schema).is_valid(value)


def test_empty_inventory_requires_empty_reasons_and_choices_and_roundtrips():
    schema = selection_schema([])
    Draft202012Validator.check_schema(schema)
    value = {"reasonTable": [], "sourceDecisions": {}}
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
        len(json.dumps(wire, ensure_ascii=False))
        < len(json.dumps(value, ensure_ascii=False)) * 0.75
    )
    assert decode_selection(wire, items) == check_selection(value, {"sources": items})
    assert list(wire["sourceDecisions"]) == [s["sourceRef"] for s in items]


def test_reference_alias_decode_happens_after_reason_expansion_without_touching_literals():
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
    assert "reasonTable" in model.requests[-1].output_schema["properties"]
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert "sourceDecisions" in progress["sourceSelections"][0]["response"]
    old = deepcopy(states[-1])
    old["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v25"
    calls = len(model.requests)
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=old)
    assert len(model.requests) == calls


def test_truncated_response_preserves_structure_but_no_selection_is_accepted():
    class Truncated(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if "reasonTable" in request.output_schema.get("properties", {}):
                return InferenceResponse(response.text[:-3], response.usage, "length")
            return response

    model, states = Truncated(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "partial" and len(result["data"]["records"]) == 2
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert not progress.get("sourceSelections")
    assert any(i["code"] == "ai_response_incomplete" for i in result["issues"])


def test_duplicate_source_key_is_not_hidden_by_json_object_parsing():
    class Duplicate(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if "reasonTable" in request.output_schema.get("properties", {}):
                value = json.loads(response.text)
                ref, choice = next(iter(value["sourceDecisions"].items()))
                duplicate = json.dumps(ref) + ":" + json.dumps(choice) + ","
                text = response.text.replace(
                    '"sourceDecisions": {', '"sourceDecisions": {' + duplicate
                )
                assert text != response.text
                return InferenceResponse(text, response.usage)
            return response

    model, states = Duplicate(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "partial" and len(result["data"]["records"]) == 2
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert not progress.get("sourceSelections")
    assert any(i["code"] == "table_stage_invalid" for i in result["issues"])
