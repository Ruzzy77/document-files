"""Table content cannot bypass separate applicability; scripted, not model quality."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_table_selection import SelectionModel, progress, run

from document_files.interpretation.compiler import CompileError
from document_files.interpretation.table_selection import (
    check_selected_meaning,
    complete_selected_meaning,
    selected_meaning_feedback,
)


class Capture(SelectionModel):
    def infer(self, request):
        response = super().infer(request)
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("meaningPhase") == "details":
            self.content = json.loads(response.text)
            self.contract = request.output_schema
        return response


@pytest.mark.parametrize(
    "extra",
    [
        {"scope": {"kind": "record"}},
        {"scope": {"kind": "columns", "columnIds": ["size"]}},
        {"scope": {"kind": "rows", "rowStart": 1, "rowEnd": 1, "columnIds": []}},
        {"scope": {"kind": "unresolved"}},
        {"fieldIds": []},
        {"repeatIds": ["records"]},
        {"groupIds": []},
        {"rowStart": None},
        {"rowEnd": 2},
    ],
)
def test_content_scope_is_rejected_by_schema_and_grammar_independent_decoder(extra):
    model, states = Capture(), []
    result = run(model, states=states, maxModelCalls=3)
    assert result["extraction"]["status"] == "partial"
    assert not model.scope_requests
    selection = progress(states[-1])["sourceSelections"][-1]
    # Saved selection uses original refs; choose the same explicitly selected source.
    original_ref = next(
        ref
        for ref, item in selection["response"]["sourceDecisions"].items()
        if item["decision"] == "has_meaning"
    )
    value = copy.deepcopy(model.content)
    value["meanings"][0].update(extra)
    assert not Draft202012Validator(model.contract).is_valid(value)
    value["meanings"][0]["sourceQuotes"][0]["sourceRef"] = original_ref
    value["remainderReviews"][0]["sourceRefs"] = [original_ref]
    before = copy.deepcopy(value)
    with pytest.raises(CompileError, match="table_content_scope_must_be_deferred"):
        complete_selected_meaning(value, selection)
    assert value == before


def test_content_exhaustion_does_not_claim_complete_or_implicitly_fund_scope():
    model, states = Capture(), []
    partial = run(model, states=states, maxModelCalls=3)
    ir = next(iter(states[-1]["accepted"].values()))
    assert ir["meanings"][0]["fieldIds"] == ir["meanings"][0]["repeatIds"] == []
    assert partial["semanticDetails"][0]["scope"] == []
    assert partial["extraction"]["modelCalls"] == 3 and not states[-1]["scopeDecisions"]
    assert "scope" not in model.content["meanings"][0]
    assert any(i["code"] == "model_call_budget_exceeded" for i in partial["issues"])
    run(model, restore=states[-1], maxModelCalls=3)
    assert len(model.requests) == 3 and not model.scope_requests
    done = run(
        model,
        states=states,
        restore=states[-1],
        maxModelCalls=3,
        additional_budget={"maxModelCalls": 1},
    )
    assert done["extraction"]["status"] == "complete"
    assert done["extraction"]["modelCalls"] == 4
    assert len(model.requests) == 3 and len(model.scope_requests) == 1
    assert done["data"] == partial["data"] and done["dataSchema"] == partial["dataSchema"]
    unit = done["semanticDetails"][0]
    assert [t["path"] for t in unit["scope"]] == ["/records/0/size", "/records/1/size"]
    for item in done["valueEvidence"]:
        assert (unit["id"] in item["semanticIds"]) == item["target"]["path"].endswith("/size")
    fresh = Capture()
    resumed = run(fresh, restore=states[-1], maxModelCalls=3)
    assert not fresh.requests and not fresh.scope_requests
    for key in ["data", "dataSchema", "semanticDetails", "valueEvidence", "schemaEvidence"]:
        assert resumed[key] == done[key]


def test_old_table_identity_and_forged_bound_content_are_not_accepted():
    model, states = Capture(), []
    run(model, states=states)
    saved = copy.deepcopy(states[-1])
    saved["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v16"
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(Capture(), restore=saved)
    record = progress(states[-1])["sourceSelections"][-1]
    forged = {
        "sourceDecisions": {
            ref: {"decision": v["decision"]}
            for ref, v in record["response"]["sourceDecisions"].items()
        },
        "meanings": [{"scope": {"kind": "record"}, "sourceQuotes": []}],
    }
    with pytest.raises(CompileError, match="table_content_scope_must_be_deferred"):
        check_selected_meaning(forged, record)
    with pytest.raises(CompileError, match="table_content_scope_must_be_deferred"):
        selected_meaning_feedback({"acceptedResponse": forged})


def test_feedback_only_removes_compiler_deferred_scope_and_preserves_literal_content():
    original = {
        "acceptedResponse": {
            "meanings": [{"description": "scope is literal text", "scope": {"kind": "unresolved"}}]
        },
        "baseRevision": "same",
    }
    before = copy.deepcopy(original)
    result = selected_meaning_feedback(original)
    assert result["acceptedResponse"]["meanings"] == [{"description": "scope is literal text"}]
    assert original == before and result["baseRevision"] == "same"
    assert selected_meaning_feedback(["invalid response"]) == ["invalid response"]
