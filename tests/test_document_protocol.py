"""Actual native parsing and scripted stage decisions; not AI quality approval."""

import copy
import io
import json
import zipfile
from xml.sax.saxutils import escape

import pytest
from jsonschema import Draft202012Validator
from test_document_outline import OutlineModel, decision, native_wire, unit

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import document_protocol as protocol
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import region_payload
from document_files.interpretation.semantic_types import RegionInterpretation, region_output_schema


def raw_document(texts=("Staff Register", "Ordinary prose.")):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr(
            zipfile.ZipInfo("Contents/section0.xml"),
            "<sec>"
            + "".join(f"<p><run><t>{escape(text)}</t></run></p>" for text in texts)
            + "</sec>",
        )
    return stream.getvalue()


class StagedModel(OutlineModel):
    def __init__(self, mode=None):
        super().__init__()
        self.mode = mode
        self.requests = []

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        stage = "roles" if payload.get("documentStage") == "roles" else "content"
        self.requests.append((stage, payload, request.output_schema))
        if self.mode == "fail_" + stage:
            self.calls += 1
            raise ModelError("ai_test_transport_failure")
        response = super().infer(request)
        value = json.loads(response.text)
        if self.mode == "reclassify" and stage == "content":
            value["documentElements"] = payload["documentContent"]["roles"]
        if self.mode == "uncertain" and stage == "roles":
            value["documentElements"][0]["status"] = "uncertain"
        if self.mode == "missing" and stage == "roles":
            value["documentElements"] = []
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 9, "completion_tokens": 3})


def execute(model, *, raw=None, states=None, restore=None, budget=12, grant=None, **options):
    raw = raw or raw_document()
    return extract_schema_from_stream(
        AnalysisJob(job_id="staged-native", input=AnalysisInput.from_bytes(raw, format_id="hwpx")),
        io.BytesIO(raw),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=budget, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        additional_budget=grant,
    )


def test_role_only_request_and_immutable_content_have_separate_contracts_and_costs():
    model = StagedModel()
    result = execute(model)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert [s for s, _, _ in model.requests] == ["roles", "content"]
    _, role, role_schema = model.requests[0]
    _, content, content_schema = model.requests[1]
    assert "bindings" not in role and "fields" not in role_schema["properties"]
    assert "documentElements" not in content_schema["properties"]
    assert "documentContext" not in content and content["documentContent"]["roles"]
    assert result["data"] == {} and result["valueEvidence"] == []
    record = next(iter(result["coverage"]["documentInterpretation"].values()))
    assert record["roleStatus"] == record["contentStatus"] == "complete"
    for name in ["roleUsage", "contentUsage"]:
        assert record[name]["modelCalls"] == 1
        assert record[name]["promptTokens"] == 9 and record[name]["completionTokens"] == 3
    assert result["extraction"]["usage"]["modelCalls"] == 2
    assert result["extraction"]["usage"]["promptTokens"] == 18


def test_content_reclassification_rejected_without_losing_already_accepted_roles():
    model = StagedModel("reclassify")
    result = execute(model)
    assert result["extraction"]["status"] == "partial"
    assert [e["role"] for e in result["document"]["outline"]["elements"]] == ["title", "paragraph"]
    assert model.calls == 3 and len(model.outline_requests) == 1
    assert result["coverage"]["unprocessedRegions"]
    assert result["coverage"]["regions"][0]["status"] == "structure_compiled"
    assert result["coverage"]["semanticAccounting"] == []  # Roles do not certify content.
    assert "region_interpretation_invalid" in str(result["issues"])


def test_budget_pause_keeps_roles_and_resume_does_not_replay_role_call():
    model = StagedModel()
    states = []
    paused = execute(model, states=states, budget=1)
    assert paused["extraction"]["status"] == "partial" and model.calls == 1
    assert paused["document"]["outline"]["status"] == "interpreted"
    assert paused["coverage"]["unprocessedRegions"]
    result = execute(model, budget=1, restore=states[-1], grant={"maxModelCalls": 1})
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert model.calls == result["extraction"]["usage"]["modelCalls"] == 2
    assert len(model.outline_requests) == len(model.content_requests) == 1
    assert result["extraction"]["budget"]["maxModelCalls"] == 2


@pytest.mark.parametrize("stage", ["roles", "content"])
def test_unknown_transport_response_is_not_automatically_replayed(stage):
    model = StagedModel("fail_" + stage)
    states = []
    result = execute(model, states=states)
    calls = model.calls
    model.mode = None
    unchanged = execute(model, restore=states[-1])
    assert model.calls == calls and unchanged["extraction"]["status"] == "partial"
    finished = execute(model, restore=states[-1], grant={"maxModelCalls": 2})
    assert finished["extraction"]["status"] == "complete", finished["issues"]
    assert finished["extraction"]["usage"]["modelCalls"] == model.calls
    if stage == "content":
        assert [e["role"] for e in result["document"]["outline"]["elements"]] == [
            "title",
            "paragraph",
        ]
        assert len(model.outline_requests) == 1


def test_saved_inflight_call_requires_explicit_resume_allowance():
    model = StagedModel()
    states = []
    execute(model, states=states)
    state = next(
        s for s in states if any(v["status"] == "running" for v in s["documentStages"].values())
    )
    calls = model.calls
    result = execute(model, restore=state)
    assert result["extraction"]["status"] == "partial" and model.calls == calls
    assert "document_role_response_unavailable" in str(result["issues"])


@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "role",
        "source",
        "content_roles",
        "role_calls",
        "content_calls",
        "total_calls",
        "content_flag",
        "missing_stage",
    ],
)
def test_staged_checkpoint_checks_context_decisions_and_cumulative_usage(damage):
    model = StagedModel()
    states = []
    execute(model, states=states)
    state = copy.deepcopy(states[-1])
    rid, stage = next(iter(state["documentStages"].items()))
    if damage == "request":
        stage["requestHash"] = "0" * 64
    elif damage == "role":
        stage["response"]["documentElements"][0]["role"] = "paragraph"
    elif damage == "source":
        state["result"]["document"]["nodes"]["n1"]["text"] += " changed"
    elif damage == "content_roles":
        state["accepted"][rid]["documentElements"][0]["status"] = "uncertain"
    elif damage == "role_calls":
        stage["usage"]["modelCalls"] = 0
    elif damage == "content_calls":
        stage["content"]["usage"]["modelCalls"] = 0
    elif damage == "total_calls":
        state["usage"]["modelCalls"] = 1
    elif damage == "content_flag":
        stage["content"]["hasAcceptedResponse"] = False
    else:
        state["documentStages"] = {}
    calls = model.calls
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=state, grant={"maxModelCalls": 1})
    assert model.calls == calls


def test_accepted_public_outline_is_recomputed_and_normal_resume_uses_no_calls():
    model = StagedModel()
    states = []
    result = execute(model, states=states)
    state = copy.deepcopy(states[-1])
    state["result"]["document"]["outline"]["elements"][0]["role"] = "invented"
    again = execute(model, restore=state)
    assert again["document"]["outline"] == result["document"]["outline"]
    assert model.calls == 2


def test_uncertain_role_is_not_upgraded_by_a_successful_content_stage():
    model = StagedModel("uncertain")
    result = execute(model)
    assert result["extraction"]["status"] == "partial"
    assert result["document"]["outline"]["elements"][0]["status"] == "uncertain"


def test_inner_structural_value_retained_but_whole_title_copy_rejected():
    doc, region = unit("Count: 0007")
    roles = {"regionId": region["id"], "documentElements": [decision().model_dump()]}
    payload, schema = protocol.content_request(
        region_payload(doc, region),
        region_output_schema(doc, region, compact=False),
        roles,
        doc,
        region,
    )
    value_id = next(b for b, v in doc.bindings.items() if v.get("candidateRole") == "value")
    whole_id = next(b for b, v in doc.bindings.items() if v.get("candidateRole") == "content")
    assert (
        value_id in payload["documentContent"]["valueBindingIds"]
        and whole_id not in payload["documentContent"]["valueBindingIds"]
    )
    field = {
        "id": "count",
        "key": "count",
        "label": "Count",
        "valueType": "string",
        "definitionRefs": ["n1"],
        "bindingId": value_id,
        "status": "present",
    }
    value = {"regionId": region["id"], "fields": [field]}
    assert Draft202012Validator(schema).is_valid(native_wire(value))
    out = compile_region(
        RegionInterpretation.model_validate(protocol.attach_content(native_wire(value), roles)),
        doc,
        region,
    )
    assert out.data == {"count": "0007"} and out.document_elements[0]["role"] == "title"
    assert not out.issues
    bad = {"regionId": region["id"], "fields": [{**field, "bindingId": whole_id}]}
    assert not Draft202012Validator(schema).is_valid(native_wire(bad))
    with pytest.raises(CompileError, match="document_role_value_conflict"):
        compile_region(
            RegionInterpretation.model_validate(protocol.attach_content(native_wire(bad), roles)),
            doc,
            region,
        )
    missing = compile_region(
        RegionInterpretation.model_validate(
            protocol.attach_content({"regionId": region["id"]}, roles)
        ),
        doc,
        region,
    )
    assert missing.issues == [{"code": "value_candidate_unaccounted", "bindingId": value_id}]


def test_role_schema_requires_owned_sources_and_declares_partial_table_preview():
    doc, region = unit(count=2)
    doc.tables["t"] = {
        "rowCount": 20,
        "colCount": 1,
        "cells": [{"sourceRef": "n1", "row": i, "col": 0} for i in range(20)],
    }
    before = copy.deepcopy(doc.tables)
    payload, schema = protocol.role_request(doc, region)
    Draft202012Validator.check_schema(schema)
    preview = protocol.table_preview(doc, "t")
    assert preview["shownCells"] == 12 and preview["totalCells"] == 20 and not preview["complete"]
    assert all(not c["declaredHeader"] for c in preview["cells"])
    assert doc.tables == before
    with pytest.raises(ValueError, match="sources"):
        protocol.accept_roles(
            {"regionId": region["id"], "documentElements": [decision().model_dump()]}, doc, region
        )
    with pytest.raises(ValueError):
        protocol.accept_roles(
            {
                "regionId": region["id"],
                "documentElements": [decision().model_dump(), decision().model_dump()],
            },
            doc,
            region,
        )


def test_structural_text_keeps_unit_meaning_without_inventing_a_scalar():
    doc, region = unit("Units: mm")
    roles = {"regionId": region["id"], "documentElements": [decision().model_dump()]}
    bid = next(b for b, v in doc.bindings.items() if v.get("candidateRole") == "value")
    value = {
        "regionId": region["id"],
        "meanings": [
            {
                "id": "unit",
                "kind": "unit",
                "description": "mm",
                "sourceRefs": ["n1"],
                "fieldIds": [],
                "groupIds": [],
                "repeatIds": [],
                "status": "interpreted",
            }
        ],
        "excludedBindings": [
            {
                "bindingId": bid,
                "role": "structural",
                "explanation": "Unit context, not a separate business value",
            }
        ],
    }
    out = compile_region(
        RegionInterpretation.model_validate(protocol.attach_content(native_wire(value), roles)),
        doc,
        region,
    )
    assert out.data == {} and out.document_elements[0]["role"] == "title"
    assert any(
        s["kind"] == "unresolved_unit" and s["description"] == "mm" and s["sourceRefs"] == ["n1"]
        for s in out.semantics
    )
    assert out.issues == [
        {"code": "semantic_scope_unresolved", "semanticId": region["id"] + ":unit"}
    ]
    assert not any(i["code"] == "value_candidate_unaccounted" for i in out.issues)


def test_too_large_role_request_preserves_source_and_does_not_dispatch():
    model = StagedModel()
    model.input_budget_chars = 2000
    result = execute(
        model, raw=raw_document(("Staff Register", "Large ordinary prose " * 50)), contextChars=8000
    )
    assert result["extraction"]["status"] == "partial" and model.calls == 0
    assert any("budget_exceeded" in i["code"] for i in result["issues"])
    assert result["document"]["nodes"]["n1"]["text"] == "Staff Register"


def test_managed_native_stages_use_bounded_reasoning_without_changing_external_clients(monkeypatch):
    import document_files.interpretation.engine as engine

    class Recorded(StagedModel):
        last_diagnostics = {}

        def __init__(self):
            super().__init__()
            self.budgets = []

        def infer(self, request):
            self.budgets.append(request.reasoning_budget_tokens)
            return super().infer(request)

    external = Recorded()
    assert execute(external)["extraction"]["status"] == "complete"
    assert external.budgets == [None, None]
    monkeypatch.setattr(engine, "ManagedPackClient", Recorded)
    managed = Recorded()
    assert execute(managed)["extraction"]["status"] == "complete"
    assert managed.budgets == [protocol.REASONING_BUDGET, protocol.REASONING_BUDGET]
