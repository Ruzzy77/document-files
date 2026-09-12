"""Source/type feasibility is necessary, never independent semantic approval."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_document_outline import decision, unit
from test_document_protocol import StagedModel, execute, raw_document

from document_files.interpretation import native_structure as native
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import compile_region
from document_files.interpretation.native_structure_wire import StructureContractError, validate
from document_files.interpretation.native_value_choices import ValueChoices


def prepared(text, value_type="string", status="present"):
    doc, region = unit("Value: " + text)
    roles = {
        "regionId": region["id"],
        "documentElements": [decision(role="field_group", level=None).model_dump()],
    }
    structure = native.NativeStructure(
        regionId=region["id"],
        fields=[
            {
                "id": "f",
                "key": "value",
                "label": "Value",
                "definitionRefs": ["n1"],
                "sourceRefs": ["n1"],
                "valueType": value_type,
                "status": status,
            }
        ],
    )
    return doc, region, roles, structure


def response(region, choice):
    return {"regionId": region["id"], "selections": {"@value1": choice}, "excludedBindings": []}


@pytest.mark.parametrize(
    "text,kind,status,valid",
    [
        ("8", "integer", "present", True),
        ("requested 8", "integer", "present", False),
        ("0007", "string", "present", True),
        ("0007", "integer", "present", False),
        ("12.5000", "decimal", "present", True),
        ("12.5 mm", "decimal", "present", False),
        ("1,200", "decimal", "present", False),
        ("0.1234567890123456789", "number", "present", False),
        ("true", "boolean", "present", True),
        ("yes", "boolean", "present", False),
        ("8", "integer", "blank", False),
        ("", "integer", "blank", True),
        ("", "integer", "present", False),
        ("", "string", "present", False),
    ],
)
def test_choices_follow_exact_scalar_rules_without_changing_source(text, kind, status, valid):
    doc, region, roles, structure = prepared(text, kind, status)
    before = copy.deepcopy((doc, region, structure))
    payload, contract = native.value_request(structure, roles, doc, region)
    bid = next(b for b, v in payload["bindings"].items() if v.get("candidateRole") == "value")
    value = response(region, {"kind": "binding", "bindingId": bid, "status": status})
    assert Draft202012Validator(contract).is_valid(value) is valid
    if valid:
        ir = native.accept_values(value, structure, roles, doc, region)
        compiled = compile_region(ir, doc, region)
        assert compiled.value_evidence[0]["raw"] == text
        assert compiled.value_evidence[0]["status"] == status
    else:
        with pytest.raises(native.NativeValueError):
            native.accept_values(value, structure, roles, doc, region)
    # Unresolved remains legal; a bad frozen definition cannot force a fake value.
    Draft202012Validator(contract).validate(response(region, {"kind": "unresolved"}))
    assert (doc, region, structure) == before


def test_compound_numeric_source_keeps_exact_quote_alternative_and_all_candidates():
    doc, region, roles, structure = prepared("requested 8", "integer")
    payload, schema = native.value_request(structure, roles, doc, region)
    assert set(payload["bindings"]) == set(region["bindingIds"])
    value = response(region, {"kind": "quote", "quote": {"sourceRef": "n1", "text": "8"}})
    Draft202012Validator(schema).validate(value)
    result = compile_region(native.accept_values(value, structure, roles, doc, region), doc, region)
    assert result.data == {"value": 8}
    assert result.value_evidence[0]["raw"] == "8"
    assert result.value_evidence[0]["binding"]["start"] == doc.nodes["n1"]["text"].index("8")


def test_binding_hints_cannot_override_actual_empty_text_or_conflicting_observation():
    doc, region, roles, structure = prepared("8", "integer", "blank")
    bid = next(b for b, v in doc.bindings.items() if v.get("candidateRole") == "value")
    doc.bindings[bid]["blank"] = True
    shown = copy.deepcopy(doc.bindings)
    shown[bid]["exactText"] = ""
    reader = ValueChoices(doc, region, shown, shown)
    entry = native.entries(structure)[0]
    assert reader.error(entry, bid) == "blank_status_disagrees_with_observation"
    structure.fields[0].status = "present"
    doc.bindings[bid]["candidateStatus"] = "unresolved_conflict"
    _, schema = native.value_request(structure, roles, doc, region)
    assert not Draft202012Validator(schema).is_valid(
        response(region, {"kind": "binding", "bindingId": bid, "status": "present"})
    )


def test_equal_numbers_are_offered_only_within_each_actual_occurrence():
    doc, region, roles, structure = prepared("8; Second: 8", "integer")
    structure.fields = []
    first = "Value: 8"
    second = "Second: 8"
    structure.records = [
        native.NativeRecord(
            id="r",
            key="items",
            label="Items",
            definitionRefs=["n1"],
            columns=[
                {
                    "id": "c",
                    "key": "count",
                    "label": "Count",
                    "valueType": "integer",
                    "definitionRefs": ["n1"],
                }
            ],
            rows=[
                {
                    "id": str(i),
                    "sourceQuotes": [{"sourceRef": "n1", "text": anchor}],
                    "cells": [{"columnId": "c", "sourceRefs": ["n1"], "status": "present"}],
                }
                for i, anchor in enumerate([first, second])
            ],
        )
    ]
    payload, schema = native.value_request(structure, roles, doc, region)
    bids = [b for b, v in payload["bindings"].items() if v.get("candidateRole") == "value"]
    assert len(bids) == 2 and all(payload["bindings"][b]["exactText"] == "8" for b in bids)
    good = {
        "regionId": region["id"],
        "selections": {
            f"@value{i + 1}": {"kind": "binding", "bindingId": b, "status": "present"}
            for i, b in enumerate(bids)
        },
        "excludedBindings": [],
    }
    Draft202012Validator(schema).validate(good)
    bad = copy.deepcopy(good)
    bad["selections"]["@value1"]["bindingId"] = bids[1]
    assert not Draft202012Validator(schema).is_valid(bad)
    with pytest.raises(native.NativeValueError) as error:
        native.accept_values(bad, structure, roles, doc, region)
    assert "logical_value_binding_outside_occurrence" in error.value.diagnostics
    out = compile_region(native.accept_values(good, structure, roles, doc, region), doc, region)
    assert out.data == {"items": [{"count": 8}, {"count": 8}]}
    assert len({e["binding"]["start"] for e in out.value_evidence}) == 2


def test_null_label_feedback_reaches_repair_and_does_not_guess_a_label():
    class NullLabelOnce(StagedModel):
        def infer(self, request):
            result = super().infer(request)
            payload = json.loads(request.messages[-1]["content"])
            if payload.get("documentStage") == "structure" and len(self.structure_requests) == 1:
                value = json.loads(result.text)
                value["fields"][0]["label"] = None
                return InferenceResponse(json.dumps(value), {})
            return result

    model, states = NullLabelOnce(), []
    out = execute(model, raw=raw_document(("Count: 0007",)), states=states)
    assert out["extraction"]["status"] == "complete" and out["data"] == {"count": "0007"}
    assert model.calls == 4
    assert model.structure_requests[1]["repairFeedback"] == [
        "invalid_native_structure_contract",
        "native_structure_contract:/fields/0/label:expected=string",
    ]
    assert any(
        "native_structure_contract:/fields/0/label:expected=string" in str(s) for s in states
    )
    # Only the valid second response is committed; the engine does not fill nulls.
    saved = next(iter(states[-1]["documentStages"].values()))
    assert saved["structure"]["response"]["fields"][0]["label"] == "Count"


def test_structural_diagnostics_never_echo_private_values_or_unknown_member_names():
    doc, region, roles, _ = prepared("PRIVATE SOURCE")
    _, schema = native.request(doc, region, roles, {})
    value = {
        "regionId": "PRIVATE REGION",
        "fields": [
            {
                "key": "value",
                "label": None,
                "valueType": "PRIVATE TYPE",
                "sourceRefs": ["PRIVATE REF"],
                "status": "present",
                "PRIVATE KEY": "PRIVATE VALUE",
            }
        ],
    }
    with pytest.raises(StructureContractError) as error:
        validate(value, schema)
    feedback = error.value.diagnostics
    assert "PRIVATE" not in str(feedback) and len(feedback) <= 12
    assert "native_structure_contract:/fields/0/label:expected=string" in feedback
    assert "native_structure_contract:/fields/0:unexpected_member" in feedback
    assert "native_structure_contract:/regionId:value_not_offered" in feedback
