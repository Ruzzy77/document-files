"""Finite meaning-repair contracts; scripted replies are not model qualification."""

import copy
import io
import json

import pytest

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import engine
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.contracts import ExtractionOptions

HTML = (
    b"<table><caption>Measurements; Size uses mm.</caption>"
    b"<tr><th>Size</th></tr><tr><td>001.2300</td></tr></table>"
)


class CaptionModel:
    identity = {"adapter": "caption-repair-fixture", "model": "scripted-not-qualified"}

    def __init__(self, mode="fix"):
        self.requests = []
        self.meaning_calls = 0
        self.mode = mode

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        self.requests.append(payload)
        if payload["tableStage"] == "structure":
            table_ref, table = next(iter(payload["tables"].items()))
            header = table["columnCandidates"][0]["headerRefs"][-1]
            value = {
                "regionId": payload["regionId"],
                "tableKind": "record_table",
                "record": {
                    "id": "rows",
                    "key": "rows",
                    "label": "Measurements",
                    "tableRef": table_ref,
                    "rowStart": 0,
                    "rowEnd": 1,
                    "definitionRefs": [header],
                    "rowRoles": [{"row": 1, "role": "data"}],
                    "columns": [
                        {
                            "id": "size",
                            "key": "size",
                            "label": "Size",
                            "column": 0,
                            "valueType": "decimal",
                            "definitionRefs": [header],
                        }
                    ],
                },
            }
        else:
            self.meaning_calls += 1
            caption = next(
                (
                    ref
                    for ref, node in payload["nodes"].items()
                    if node.get("semanticRole") == "caption"
                ),
                None,
            )
            value = {"regionId": payload["regionId"], "meanings": []}
            if caption:
                value["meanings"] = [
                    {
                        "id": "unit",
                        "kind": "unit",
                        "description": "Size uses millimeters",
                        "sourceRefs": [caption],
                        "scope": {"kind": "columns", "columnIds": ["size"]},
                        "status": "interpreted",
                    }
                ]
            if self.meaning_calls >= 2:
                if self.mode in {"timeout", "cancel"}:
                    raise ModelError("ai_timeout" if self.mode == "timeout" else "ai_cancelled")
                if self.mode == "invalid":
                    value["fields"] = []
                if self.mode != "same" and caption:
                    value["dispositions"] = [
                        {
                            "sourceRef": caption,
                            "role": "heading",
                            "explanation": "Table caption and unit statement",
                        }
                    ]
                if self.mode == "drop":
                    value["meanings"] = []
                elif self.mode == "rewrite":
                    value["meanings"][0]["description"] = "Replacement assertion"
                elif self.mode == "weaken":
                    value["meanings"][0]["status"] = "uncertain"
                elif self.mode == "scope":
                    value["meanings"][0]["scope"] = {"kind": "record"}
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def run(model, *, states=None, restore=None, additional_budget=None, content=HTML, **options):
    return engine.extract_schema_from_stream(
        AnalysisJob(
            job_id="meaning-repair", input=AnalysisInput.from_bytes(content, format_id="html")
        ),
        io.BytesIO(content),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        additional_budget=additional_budget,
    )


def test_caption_accounting_repairs_inside_two_meaning_calls_with_stateless_context():
    model, states = CaptionModel(), []
    result = run(model, states=states)
    assert len(model.requests) == 3 and model.meaning_calls == 2
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    feedback = model.requests[-1]["repairFeedback"]
    assert feedback["issues"][0].startswith("node_semantics_unaccounted:")
    assert feedback["acceptedMeanings"][0]["description"] == "Size uses millimeters"
    assert feedback["acceptedMeanings"][0]["scope"]["columnIds"] == ["size"]
    assert "do not remove" in feedback["instruction"]
    assert model.requests[1]["frozenStructure"] == model.requests[2]["frozenStructure"]
    rid = next(iter(states[-1]["accepted"]))
    before = next(
        s
        for s in states
        if s["tableStages"].get(rid, {}).get("meaning", {}).get("acceptedResponse")
    )
    assert before["tableStages"][rid]["meaning"]["status"] == "pending"
    assert before["result"]["extraction"]["status"] == "partial"
    assert states[-1]["tableStages"][rid]["meaning"]["usage"]["modelCalls"] == 2
    assert before["accepted"][rid]["repeats"] == states[-1]["accepted"][rid]["repeats"]
    assert before["result"]["data"] == result["data"]
    assert run(model, restore=states[-1])["extraction"]["status"] == "complete"
    assert len(model.requests) == 3


@pytest.mark.parametrize("mode", ["drop", "rewrite", "weaken", "scope", "same", "invalid"])
def test_issue_reduction_cannot_delete_or_weaken_committed_source_statement(mode):
    model, states = CaptionModel(mode), []
    result = run(model, states=states)
    assert len(model.requests) == 3
    assert result["extraction"]["status"] == "partial"
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    ir = next(iter(states[-1]["accepted"].values()))
    assert ir["meanings"][0]["description"] == "Size uses millimeters"
    assert ir["meanings"][0]["status"] == "interpreted"
    assert ir["meanings"][0]["fieldIds"] == ["size"]
    assert not ir["dispositions"]
    assert any(i["code"] == "table_stage_invalid" for i in result["issues"])
    run(model, restore=states[-1])
    assert len(model.requests) == 3  # no automatic grant or restart loop
    model.mode = "fix"
    fixed = run(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete", fixed["issues"]
    assert len(model.requests) == 4


def test_global_budget_pause_resumes_only_accounting_repair_with_accepted_statements():
    model, states = CaptionModel(), []
    result = run(model, states=states, maxModelCalls=2)
    assert len(model.requests) == 2 and result["extraction"]["status"] == "partial"
    assert next(iter(states[-1]["tableStages"].values()))["meaning"]["acceptedResponse"] is True
    fixed = run(model, restore=states[-1], maxModelCalls=2, additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete", fixed["issues"]
    assert len(model.requests) == 3
    assert model.requests[-1]["repairFeedback"]["acceptedMeanings"]


@pytest.mark.parametrize("mode,code", [("timeout", "ai_timeout"), ("cancel", "ai_cancelled")])
def test_failed_second_call_preserves_meanings_and_retry_is_only_explicit_after_stage_limit(
    mode, code
):
    model, states = CaptionModel(mode), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "partial" and result["data"]["rows"]
    assert any(i["code"] == code for i in result["issues"])
    assert next(iter(states[-1]["accepted"].values()))["meanings"]
    model.mode = "fix"
    run(model, restore=states[-1])
    assert len(model.requests) == 3
    fixed = run(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete"
    assert len(model.requests) == 4


def test_native_value_failure_does_not_cause_meaning_regeneration(monkeypatch):
    original = engine.compile_region

    def unresolved(*args, **kwargs):
        result = original(*args, **kwargs)
        result.issues.append({"code": "value_not_resolved", "status": "uncertain"})
        return result

    monkeypatch.setattr(engine, "compile_region", unresolved)
    model = CaptionModel()
    content = HTML.replace(b"<caption>Measurements; Size uses mm.</caption>", b"")
    result = run(model, content=content)
    assert len(model.requests) == 2 and model.meaning_calls == 1
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == "value_not_resolved" for i in result["issues"])


def test_structural_change_is_rejected_even_if_accounting_improves(monkeypatch):
    original = engine.meaning_ir

    def malicious(value, frozen):
        candidate = original(value, frozen)
        if candidate.dispositions:
            candidate.repeats = copy.deepcopy(candidate.repeats)
            candidate.repeats[0].columns[0].key = "changed"
        return candidate

    monkeypatch.setattr(engine, "meaning_ir", malicious)
    model, states = CaptionModel(), []
    result = run(model, states=states)
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    assert result["extraction"]["status"] == "partial"
    assert any("table_meaning_changed_structure" in i.get("errors", []) for i in result["issues"])


def test_repair_context_limit_preserves_the_accepted_meaning_without_dispatch():
    class Limited(CaptionModel):
        def infer(self, request):
            response = super().infer(request)
            if self.meaning_calls == 1:
                self.input_budget_chars = sum(len(m["content"]) for m in request.messages)
            return response

    model, states = Limited(), []
    result = run(model, states=states)
    assert len(model.requests) == 2
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == "region_context_budget_exceeded" for i in result["issues"])
    assert next(iter(states[-1]["accepted"].values()))["meanings"][0]["id"] == "unit"


def test_partial_accounting_improvement_is_saved_without_claiming_completion():
    content = HTML.replace(b"<tr><th>", b"<caption>Additional context</caption><tr><th>")
    model, states = CaptionModel(), []
    result = run(model, states=states, content=content)
    assert len(model.requests) == 3
    assert result["extraction"]["status"] == "partial"
    assert len([i for i in result["issues"] if i["code"] == "node_semantics_unaccounted"]) == 1
    assert len(next(iter(states[-1]["accepted"].values()))["dispositions"]) == 1
    assert next(iter(states[-1]["tableStages"].values()))["meaning"]["status"] == "pending"
    run(model, restore=states[-1], content=content)
    assert len(model.requests) == 3
