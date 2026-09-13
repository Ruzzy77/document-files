"""Exact numeral aids retain the existing compiler and cannot certify semantics."""

import copy

import pytest
from jsonschema import Draft202012Validator
from test_native_source_choices import prepared, response

from document_files.interpretation import native_literal_choices as literals
from document_files.interpretation import native_structure as native
from document_files.interpretation import native_value_batches as batches
from document_files.interpretation.compiler import compile_region
from document_files.interpretation.source_dictionary import source_nodes


def offered(doc, region, structure):
    return literals.LiteralChoices(doc, region, native.entries(structure))


@pytest.mark.parametrize(
    "text,kind,expected",
    [
        ("8.", "integer", ["8"]),
        ("-8", "integer", ["-8"]),
        ("+8", "integer", []),
        ("+8", "decimal", ["+8"]),
        ("001.2300", "decimal", ["001.2300"]),
        ("001.2300", "integer", []),
        ("0007", "integer", []),
        ("0007", "string", []),
        ("1.20e-3", "decimal", ["1.20e-3"]),
        (".2500", "decimal", [".2500"]),
        ("1,234.50", "decimal", []),
        ("1.234.56", "decimal", []),
        ("0.1234567890123456789", "number", []),
        ("0.1234567890123456789", "decimal", ["0.1234567890123456789"]),
        ("12:30", "string", []),
        ("4-6", "integer", ["4", "6"]),
    ],
)
def test_literal_spelling_and_canonical_type_feasibility(text, kind, expected):
    doc, region, roles, structure = prepared(text, kind)
    before = copy.deepcopy((doc, region, structure))
    payload, schema = native.value_request(structure, roles, doc, region)
    assert [v["text"] for v in payload["literals"].values()] == expected
    for lid, literal in payload["literals"].items():
        value = response(region, {"kind": "literal", "literalId": lid})
        Draft202012Validator(schema).validate(value)
        out = compile_region(
            native.accept_values(value, structure, roles, doc, region), doc, region
        )
        assert out.value_evidence[0]["raw"] == literal["text"]
    assert (doc, region, structure) == before


def test_equal_values_have_original_context_and_correct_substring_occurrences():
    doc, region, roles, structure = prepared("18; requested 8; received 8.", "integer")
    payload, schema = native.value_request(structure, roles, doc, region)
    eights = [(lid, v) for lid, v in payload["literals"].items() if v["text"] == "8"]
    assert [v["occurrence"] for _, v in eights] == [1, 2]  # '8' inside '18' counts too.
    assert eights[0][1]["before"].endswith("requested ")
    assert eights[1][1]["before"].endswith("received ")
    starts = []
    for lid, _ in eights:
        value = response(region, {"kind": "literal", "literalId": lid})
        Draft202012Validator(schema).validate(value)
        result = compile_region(
            native.accept_values(value, structure, roles, doc, region), doc, region
        )
        assert result.data == {"value": 8}
        starts.append(result.value_evidence[0]["binding"]["start"])
    text = doc.nodes["n1"]["text"]
    assert starts == [text.index("requested 8") + 10, text.index("received 8") + 9]


def test_literal_source_is_regenerated_not_read_from_display_metadata():
    doc, region, roles, structure = prepared("8", "integer")
    payload, _ = native.value_request(structure, roles, doc, region)
    lid = next(iter(payload["literals"]))
    payload["literals"][lid]["text"] = "999"
    value = response(region, {"kind": "literal", "literalId": lid})
    out = compile_region(native.accept_values(value, structure, roles, doc, region), doc, region)
    assert out.data == {"value": 8} and out.value_evidence[0]["raw"] == "8"
    for bad in ["unknown", "@literal999"]:
        with pytest.raises(native.NativeValueError):
            native.accept_values(
                response(region, {"kind": "literal", "literalId": bad}),
                structure,
                roles,
                doc,
                region,
            )


def test_numeric_candidates_never_replace_blank_binding_or_other_types():
    doc, region, roles, structure = prepared("8; Empty:", "integer", "blank")
    payload, schema = native.value_request(structure, roles, doc, region)
    assert payload["literals"] == {} and "literalIds" not in payload["handles"]["@value1"]
    assert not Draft202012Validator(schema).is_valid(
        response(region, {"kind": "literal", "literalId": "@literal1"})
    )


def test_cut_numeral_views_and_conflicting_sources_do_not_offer_interior_choices():
    doc, region, roles, structure = prepared("001.2300", "decimal")
    text = doc.nodes["n1"]["text"]
    start = text.index("1.23")
    view = {**region, "nodeViews": {"n1": {"start": start, "end": start + 4}}}
    assert offered(doc, view, structure).catalog == {}
    doc.bindings[next(iter(doc.bindings))]["candidateStatus"] = "unresolved_conflict"
    assert offered(doc, region, structure).catalog == {}


def test_occurrence_and_source_ownership_constrain_each_handle():
    doc, region, roles, structure = prepared("8; Second: 8", "integer")
    structure.fields = []
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
                for i, anchor in enumerate(["Value: 8", "Second: 8"])
            ],
        )
    ]
    payload, schema = native.value_request(structure, roles, doc, region)
    ids = [v["literalIds"] for v in payload["handles"].values()]
    assert len(ids) == 2 and all(len(v) == 1 for v in ids) and ids[0] != ids[1]
    value = {
        "regionId": region["id"],
        "selections": {
            h: {"kind": "literal", "literalId": v["literalIds"][0]}
            for h, v in payload["handles"].items()
        },
        "excludedBindings": [],
    }
    good = compile_region(native.accept_values(value, structure, roles, doc, region), doc, region)
    assert good.data == {"items": [{"count": 8}, {"count": 8}]}
    value["selections"]["@value1"]["literalId"] = ids[1][0]
    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(native.NativeValueError):
        native.accept_values(value, structure, roles, doc, region)


@pytest.mark.parametrize(
    "limit,reason", [("MAX_LITERALS", "candidate_limit"), ("MAX_SOURCE_CHARS", "source_limit")]
)
def test_optional_aid_limit_preserves_full_source_and_original_choices(monkeypatch, limit, reason):
    doc, region, roles, structure = prepared("1; 2; 3", "integer")
    before = copy.deepcopy(doc.to_dict())
    monkeypatch.setattr(literals, limit, 1)
    payload, schema = native.value_request(structure, roles, doc, region)
    assert payload["literalChoicesStatus"] == reason and payload["literals"] == {}
    assert source_nodes(payload, "nodes")["n1"]["text"] == doc.nodes["n1"]["text"]
    assert set(payload["bindings"]) == set(region["bindingIds"])
    quote = response(region, {"kind": "quote", "quote": {"sourceRef": "n1", "text": "3"}})
    Draft202012Validator(schema).validate(quote)
    result = compile_region(native.accept_values(quote, structure, roles, doc, region), doc, region)
    assert result.data == {"value": 3} and doc.to_dict() == before


def test_batches_keep_literal_context_and_accounting_reads():
    doc, region, roles, structure = prepared("8", "integer")
    payload, schema = native.value_request(structure, roles, doc, region)
    lid = next(iter(payload["literals"]))
    _, value_payload, value_schema = batches._request(payload, schema, "values", ["@value1"])
    assert value_payload["literals"] == payload["literals"]
    value = {
        "regionId": region["id"],
        "batchId": value_payload["batchId"],
        "selections": {"@value1": {"kind": "literal", "literalId": lid}},
        "excludedBindings": [],
    }
    Draft202012Validator(value_schema).validate(value)
    keys = payload["requiredBindingIds"]
    assert keys
    _, account, _ = batches._request(payload, schema, "accounting", keys, value["selections"])
    assert account["literals"] == payload["literals"]
    assert account["nodes"] == value_payload["nodes"] == payload["nodes"]
    assert batches.identity(payload, schema, "structure", 16000) != batches.identity(
        {**payload, "literals": {}}, schema, "structure", 16000
    )


def test_context_fallback_removes_only_optional_literal_language():
    doc, region, roles, structure = prepared("18; requested 8; received 8.", "integer")
    payload, schema = native.value_request(structure, roles, doc, region)
    full = batches._request(payload, schema, "values", ["@value1"])
    p, s = batches._without_literal_aid(full[1], full[2])
    limit = batches.size(full[0], p, s) + batches.REPAIR_RESERVE
    limited = batches._request(payload, schema, "values", ["@value1"], limit=limit)
    assert limited[1]["literalChoicesStatus"] == "context_limit"
    assert limited[1]["literals"] == {}
    assert "literalIds" not in limited[1]["handles"]["@value1"]
    for key in ["nodes", "bindings", "occurrences", "requiredBindingIds"]:
        assert limited[1][key] == full[1][key]
    assert batches.size(*limited) + batches.REPAIR_RESERVE <= limit
    selected = {
        "regionId": region["id"],
        "batchId": limited[1]["batchId"],
        "selections": {
            "@value1": {"kind": "literal", "literalId": next(iter(payload["literals"]))}
        },
        "excludedBindings": [],
    }
    with pytest.raises(batches.BatchError):
        batches.accept(selected, limited, "values", ["@value1"])
    selected["selections"]["@value1"] = {
        "kind": "quote",
        "quote": {"sourceRef": "n1", "text": "8", "occurrence": 2},
    }
    batches.accept(selected, limited, "values", ["@value1"])


def test_literal_end_to_end_scripted_read_and_checkpoint_resume():
    import json

    from test_document_protocol import StagedModel, execute, raw_document

    from document_files.interpretation.backends import InferenceResponse

    class LiteralModel(StagedModel):
        def infer(self, request):
            result = super().infer(request)
            p = json.loads(request.messages[-1]["content"])
            value = json.loads(result.text)
            if p.get("documentStage") == "structure":
                value["fields"][0]["valueType"] = "integer"
            elif p.get("documentStage") == "values":
                value["selections"] = {
                    h: {"kind": "literal", "literalId": entry["literalIds"][0]}
                    for h, entry in p["handles"].items()
                }
                value["excludedBindings"] = [
                    {
                        "bindingId": b,
                        "role": "structural",
                        "explanation": "Exact numeral read through original quote.",
                    }
                    for b in p["requiredBindingIds"]
                ]
            return InferenceResponse(json.dumps(value), result.usage)

    model, states = LiteralModel(), []
    raw = raw_document(("Count: 23",))
    out = execute(model, raw=raw, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"count": 23}
    assert out["valueEvidence"][0]["raw"] == "23"
    before = model.calls
    resumed = execute(model, raw=raw, restore=copy.deepcopy(states[-1]))
    assert resumed["data"] == out["data"] and resumed["valueEvidence"] == out["valueEvidence"]
    assert model.calls == before


@pytest.mark.parametrize("damage", ["changed", "wrong_type"])
def test_batch_literal_delivery_limit_is_part_of_checked_resume(damage):
    from test_native_value_batches import ManyValues, run

    model, states = ManyValues(), []
    run(model, states=states, budget=3)
    saved = copy.deepcopy(states[-1])
    state = next(iter(saved["documentStages"].values()))["content"]["batches"]
    state["contextLimit"] = (
        state["contextLimit"] + 1 if damage == "changed" else float(state["contextLimit"])
    )
    before = len(model.calls)
    with pytest.raises(ValueError, match="incompatible"):
        run(model, restore=saved, budget=3)
    assert len(model.calls) == before
