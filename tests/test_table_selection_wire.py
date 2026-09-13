"""Finite source classification and honest explanation state, not model quality."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError
from test_table_protocol import TableModel, execute
from test_table_reference_wire import fixture

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.table_reference_wire import prepare_meaning_wire
from document_files.interpretation.table_selection import (
    check_selection,
    selection_review_explanation,
)
from document_files.interpretation.table_selection_wire import (
    ROLES,
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
                "explanation": None,
            }
            for i, s in enumerate(items or sources())
        }
    }


def is_selection(request):
    return "sourceDecisions" in request.output_schema.get("properties", {})


def test_every_source_order_and_distinct_equal_value_survives_without_fabricating_reasons():
    value = decisions()
    original = deepcopy(value)
    wire = encode_selection(value)
    assert set(wire) == {"sourceDecisions"}
    assert all(v in ROLES for v in wire["sourceDecisions"].values())
    wire["sourceDecisions"] = dict(reversed(list(wire["sourceDecisions"].items())))
    Draft202012Validator(selection_schema(sources())).validate(wire)
    assert decode_selection(wire, sources()) == original
    restored = decode_selection(wire, sources())
    assert list(restored["sourceDecisions"]) == [s["sourceRef"] for s in sources()]
    assert value == original and all(
        d["explanation"] is None for d in restored["sourceDecisions"].values()
    )
    restored["sourceDecisions"]["n3"]["decision"] = "unresolved"
    assert restored["sourceDecisions"]["n4"]["decision"] == "no_additional_meaning"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "unknown",
        "extra",
        "canonical_reply",
        "wrong_role",
        "bool",
        "null",
        "integer",
        "array",
        "object",
        "empty_positive",
        "wrong_revision",
        "reason_table",
    ],
)
def test_untrusted_status_output_is_checked_without_schema_enforcement(mutation):
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
        choices["@s0"] = "skip"
    elif mutation in {"bool", "null", "integer", "array", "object"}:
        choices["@s0"] = {"bool": False, "null": None, "integer": 0, "array": [], "object": {}}[
            mutation
        ]
    elif mutation == "empty_positive":
        choices["n2"] = "has_meaning"
    elif mutation == "reason_table":
        value["reasonTable"] = ["Unrequested prose"]
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
    value = decisions()
    for ref in ("n5", "n6"):
        value["sourceDecisions"][ref]["decision"] = "has_meaning"
    Draft202012Validator(schema).validate(encode_selection(value))


def test_classification_schema_has_only_closed_keys_and_finite_status_literals():
    schema = selection_schema(sources())
    assert set(schema["properties"]) == {"sourceDecisions"}
    choices = schema["properties"]["sourceDecisions"]
    assert choices["required"] == [s["sourceRef"] for s in sources()]
    assert choices["additionalProperties"] is False
    for definition in schema["$defs"].values():
        assert definition["type"] == "string" and set(definition["enum"]) <= ROLES


def test_empty_inventory_still_requires_an_explicit_empty_status_map():
    schema = selection_schema([])
    Draft202012Validator.check_schema(schema)
    value = {"sourceDecisions": {}}
    Draft202012Validator(schema).validate(value)
    assert decode_selection(value, []) == value


def test_revision_keeps_its_overall_reason_hash_and_every_choice():
    value = decisions() | {
        "action": "revise_selection",
        "baseSelectionSHA256": "unmodified-base",
        "reason": "Literal @s0 must not be translated or dropped",
    }
    assert decode_selection(encode_selection(value), sources()) == value


def test_fifty_sources_require_no_free_prose_and_keep_complete_status_coverage():
    items = [{"sourceRef": f"n{i}", "text": f"Code {i}"} for i in range(50)]
    value = decisions(items)
    wire = encode_selection(value)
    assert len(json.dumps(wire, ensure_ascii=False, separators=(",", ":"))) < 1800
    assert decode_selection(wire, items) == check_selection(value, {"sources": items})
    assert list(wire["sourceDecisions"]) == [s["sourceRef"] for s in items]
    # This fixture-size check is not a tokenizer guarantee for arbitrary inventories.


def test_alias_decode_preserves_sources_and_absent_explanations():
    payload, contract, _, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    assert wire.identity is not None
    offered = wire.payload["meaningSources"]
    value = decisions(offered)
    original = wire.decode(decode_selection(encode_selection(value), offered))
    assert list(original["sourceDecisions"]) == [s["sourceRef"] for s in payload["meaningSources"]]
    assert all(d["explanation"] is None for d in original["sourceDecisions"].values())


@pytest.mark.parametrize("literal", ["Real supplied explanation", "n4 is mentioned literally", " "])
def test_display_never_silently_discards_a_supplied_explanation(literal):
    value = decisions()
    value["sourceDecisions"]["n4"]["explanation"] = literal
    with pytest.raises(CompileError, match="explanation_would_be_lost"):
        encode_selection(value)
    assert selection_review_explanation(value["sourceDecisions"]["n4"]) == literal


@pytest.mark.parametrize("status", sorted(ROLES))
def test_rendered_review_identifies_status_not_a_model_authored_reason(status):
    choice = {"decision": status, "explanation": None}
    assert selection_review_explanation(choice) == (
        f"Model source choice: {status}. No per-source explanation was requested."
    )


def test_engine_records_no_explanation_policy_keeps_cap_and_rejects_old_checkpoint():
    model, states = TableModel(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "complete"
    assert model.requests[-1].max_output_tokens == 1536 and is_selection(model.requests[-1])
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    selection = progress["sourceSelections"][0]
    assert selection["version"] == "document-files.table-source-selection.v3"
    assert selection["origin"] == "model"
    assert selection["explanationState"] == "not_requested"
    assert all(d["explanation"] is None for d in selection["response"]["sourceDecisions"].values())
    old = deepcopy(states[-1])
    old["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v26"
    calls = len(model.requests)
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=old)
    assert len(model.requests) == calls


def test_truncated_response_keeps_structure_but_no_partial_selection_is_accepted():
    class Truncated(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if is_selection(request):
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
            if is_selection(request):
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
