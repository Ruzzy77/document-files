"""Source selection, finite detail calls and durable identity; no model quality claim."""

import copy
import io
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from test_table_protocol import HTML, record_response

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import engine
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.table_selection import (
    check_selection,
    complete_selected_meaning,
    selection_schema,
)

CAPTION = HTML.replace(b"<table>", b"<table><caption>Size uses mm.</caption>")


def scripted_size_scope(payload):
    """Explicit fixture answer to a separately charged applicability request."""
    if "tasks" in payload:
        return {"decisions": [scripted_size_scope(task) for task in payload["tasks"]]}
    record = next(c for c in payload["candidates"] if "rowOptions" in c)
    size = next(c for c in record["rowOptions"]["columns"] if c["label"] == "Size")
    return {
        "taskId": payload["taskId"],
        "decision": "apply",
        "selections": [
            {
                "kind": "record",
                "recordHandle": record["targetHandle"],
                "parts": [
                    {
                        "rowCoverage": {"kind": "allDataRows"},
                        "columnCoverage": {
                            "kind": "selectedColumns",
                            "columnHandles": [size["columnHandle"]],
                        },
                    }
                ],
            }
        ],
        "explanation": "Scripted Size-only applicability, not a model quality result",
    }


class SelectionModel:
    identity = {"adapter": "selection-scripted-fixture", "model": "not-quality-qualified"}

    def __init__(self, *, wrong_choice=False, fail_details=False, malicious_quote=False):
        self.requests = []  # Table-phase requests; scope requests are counted separately.
        self.scope_requests = []
        self.wrong_choice = wrong_choice
        self.fail_details = fail_details
        self.malicious_quote = malicious_quote

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if "tableStage" not in payload:
            self.scope_requests.append(payload)
            value = scripted_size_scope(payload)
            Draft202012Validator(request.output_schema).validate(value)
            return InferenceResponse(
                json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20}
            )
        self.requests.append(payload)
        sources = payload.get("meaningSources", [])
        if payload["tableStage"] == "structure":
            value = record_response(payload)
        elif payload["meaningPhase"] == "selection":
            value = {
                "sourceDecisions": {
                    s["sourceRef"]: {
                        "decision": "has_meaning"
                        if s["text"] == "Size uses mm."
                        or (self.wrong_choice and s["text"] == "1.2300")
                        else "no_additional_meaning",
                        "explanation": "Scripted source choice; not a heuristic in the product",
                    }
                    for s in sources
                }
            }
        else:
            if self.fail_details:
                raise ModelError("ai_cancelled")
            selection = payload["sourceSelection"]["sourceDecisions"]
            if self.wrong_choice:
                self.wrong_choice = False
                value = {
                    "action": "revise_selection",
                    "baseSelectionSHA256": payload["selectionSHA256"],
                    "reason": "The extra selected source is an ordinary value, not a unit",
                    "sourceDecisions": {
                        s["sourceRef"]: {
                            "decision": "has_meaning"
                            if s["text"] == "Size uses mm."
                            else "no_additional_meaning",
                            "explanation": "Explicit corrected source choice",
                        }
                        for s in sources
                    },
                }
            else:
                value = {
                    "regionId": payload["regionId"],
                    "meanings": [
                        {
                            "id": "unit",
                            "kind": "unit",
                            "description": "Size uses millimeters",
                            "sourceQuotes": [{"sourceRef": s["sourceRef"], "text": s["text"]}],
                            "status": "interpreted",
                        }
                        for s in sources
                        if s["text"] == "Size uses mm."
                    ],
                    "remainderReviews": [
                        {
                            "sourceRefs": [ref],
                            "role": "no_additional_meaning"
                            if d["decision"] == "has_meaning"
                            else d["decision"],
                            "explanation": "Every source and remainder reviewed",
                        }
                        for ref, d in selection.items()
                        if d["decision"] == "has_meaning"
                    ],
                    "baseRevision": payload.get("repairFeedback", {}).get("baseRevision")
                    if isinstance(payload.get("repairFeedback", {}), dict)
                    else None,
                    "changes": [],
                }
                if self.malicious_quote:
                    value["meanings"][0]["sourceQuotes"] = [
                        {
                            "sourceRef": next(
                                s["sourceRef"] for s in sources if s["text"] == "1.2300"
                            ),
                            "text": "1.2300",
                        }
                    ]
        if not self.malicious_quote:
            Draft202012Validator.check_schema(request.output_schema)
            Draft202012Validator(request.output_schema).validate(value)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def run(
    model,
    *,
    content=CAPTION,
    states=None,
    restore=None,
    additional_budget=None,
    cancelled=None,
    **options,
):
    return extract_schema_from_stream(
        AnalysisJob(
            job_id="selection-test", input=AnalysisInput.from_bytes(content, format_id="html")
        ),
        io.BytesIO(content),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        additional_budget=additional_budget,
        cancelled=cancelled,
    )


def progress(state):
    return next(iter(state["tableStages"].values()))["meaning"]


def test_positive_selection_and_details_are_separate_charged_calls():
    model, states = SelectionModel(), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert [p.get("meaningPhase", p["tableStage"]) for p in model.requests] == [
        "structure",
        "selection",
        "details",
    ]
    assert result["data"]["records"][0] == {"code": "0007", "size": "1.2300"}
    state = progress(states[-1])
    assert state["usage"]["modelCalls"] == 2 and state["attempts"] == 2
    assert (
        state["phaseUsage"]["selection"]["modelCalls"]
        == state["phaseUsage"]["details"]["modelCalls"]
        == 1
    )
    assert (
        state["phaseUsage"]["selection"]["promptTokens"]
        == state["phaseUsage"]["details"]["promptTokens"]
        == 10
    )
    assert state["meaningSelections"] == [state["sourceSelections"][0]["sha256"]]
    assert any(
        progress(s).get("sourceSelections") and not progress(s).get("acceptedResponse")
        for s in states
        if s.get("tableStages")
    )
    assert run(model, restore=states[-1])["data"] == result["data"]
    assert len(model.requests) == 3


def test_detail_response_reuses_exact_saved_choices_and_only_reviews_positive_remainders():
    class Capture(SelectionModel):
        def infer(self, request):
            response = super().infer(request)
            if json.loads(request.messages[-1]["content"]).get("meaningPhase") == "details":
                self.details = json.loads(response.text)
                self.detail_schema = request.output_schema
            return response

    model, states = Capture(), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "complete"
    assert set(model.details) == {
        "regionId",
        "meanings",
        "remainderReviews",
        "baseRevision",
        "changes",
    }
    assert len(model.details["remainderReviews"]) == 1
    empty = dict(model.details, meanings=[])
    assert not Draft202012Validator(model.detail_schema).is_valid(empty)
    selection = progress(states[-1])["sourceSelections"][-1]
    assert not Draft202012Validator(model.detail_schema).is_valid(
        dict(model.details, baseRevision=selection["sha256"])
    )
    assert model.detail_schema["anyOf"][0]["properties"]["baseRevision"]["const"] is None
    assert model.detail_schema["anyOf"][0]["properties"]["changes"]["maxItems"] == 0
    choices = progress(states[-1])["sourceSelections"][-1]["response"]["sourceDecisions"]
    reviews = next(iter(states[-1]["accepted"].values()))["tableMeaningState"]["sourceReviews"]
    for ref, choice in choices.items():
        if choice["decision"] != "has_meaning":
            assert {
                "sourceRefs": [ref],
                "role": choice["decision"],
                "explanation": choice["explanation"],
            } in reviews


@pytest.mark.parametrize(
    "mutation",
    ["repeated_choices", "old_reviews", "negative_review", "duplicate", "missing", "malformed"],
)
def test_detail_cannot_rewrite_or_omit_saved_source_reviews(mutation):
    record = {
        "response": {
            "sourceDecisions": {
                "positive": {"decision": "has_meaning", "explanation": "Unit needs interpretation"},
                "negative": {
                    "decision": "unresolved",
                    "explanation": "Unclear text remains unresolved",
                },
            }
        }
    }
    value = {
        "regionId": "r",
        "meanings": [],
        "baseRevision": None,
        "changes": [],
        "remainderReviews": [
            {"sourceRefs": ["positive"], "role": "unreviewed", "explanation": "Remainder deferred"}
        ],
    }
    if mutation == "repeated_choices":
        value["sourceDecisions"] = record["response"]["sourceDecisions"]
    elif mutation == "old_reviews":
        value["sourceReviews"] = value.pop("remainderReviews")
    elif mutation == "negative_review":
        value["remainderReviews"][0]["sourceRefs"].append("negative")
    elif mutation == "duplicate":
        value["remainderReviews"] *= 2
    elif mutation == "missing":
        value["remainderReviews"] = []
    else:
        value["remainderReviews"] = [None]
    with pytest.raises(CompileError, match="table_selection_"):
        complete_selected_meaning(value, record)


def test_negative_selection_compiles_explicit_reviews_without_detail_call():
    model, states = SelectionModel(), []
    result = run(model, content=HTML, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(model.requests) == 2
    state = progress(states[-1])
    assert state["phaseUsage"]["details"]["modelCalls"] == 0
    accepted = next(iter(states[-1]["accepted"].values()))
    assert accepted["meanings"] == [] and len(accepted["tableMeaningState"]["sourceReviews"]) == 6
    assert run(model, content=HTML, restore=states[-1])["data"] == result["data"]
    assert len(model.requests) == 2


def test_global_pause_after_selection_resumes_details_without_reselecting():
    model, states = SelectionModel(), []
    partial = run(model, states=states, maxModelCalls=2)
    assert partial["extraction"]["status"] == "partial" and len(model.requests) == 2
    saved = copy.deepcopy(progress(states[-1])["sourceSelections"])
    run(model, restore=states[-1], maxModelCalls=2)
    assert len(model.requests) == 2
    result = run(
        model,
        restore=states[-1],
        maxModelCalls=2,
        additional_budget={"maxModelCalls": 2},
        states=states,
    )
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert progress(states[-1])["sourceSelections"] == saved
    assert len(model.requests) == 3 and model.requests[-1]["meaningPhase"] == "details"


def test_pre_dispatch_cancel_keeps_selection_and_does_not_charge_detail():
    model, states = SelectionModel(), []
    partial = run(model, states=states, cancelled=lambda: len(model.requests) == 2)
    assert any(i["code"] == "ai_cancelled" for i in partial["issues"])
    assert progress(states[-1])["phaseUsage"]["details"]["modelCalls"] == 0
    assert run(model, restore=states[-1])["extraction"]["status"] == "complete"
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "counter",
    ["modelCalls", "promptTokens", "completionTokens", "elapsedSeconds", "unreportedUsageCalls"],
)
@pytest.mark.parametrize("grant", [None, {"maxModelCalls": 1}])
def test_document_usage_cannot_be_lowered_below_cumulative_table_stages(
    counter, grant, monkeypatch
):
    model = SelectionModel(fail_details=counter == "unreportedUsageCalls")
    # Instant scripted replies can have zero measured duration on Windows. Give
    # these calls explicit elapsed time instead of depending on host clock ticks.
    clock = [100.0]
    monkeypatch.setattr(engine, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    infer = model.infer

    def timed_infer(request):
        try:
            return infer(request)
        finally:
            clock[0] += 0.125

    monkeypatch.setattr(model, "infer", timed_infer)
    states = []
    max_calls = 3 if model.fail_details else 2
    run(model, states=states, maxModelCalls=max_calls)
    checkpoint = copy.deepcopy(states[-1])
    total = sum(
        state[stage]["usage"][counter]
        for state in checkpoint["tableStages"].values()
        for stage in ("structure", "meaning")
    )
    assert total > 0
    checkpoint["usage"][counter] = 0 if counter == "elapsedSeconds" else total - 1
    before = len(model.requests)
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(model, restore=checkpoint, maxModelCalls=max_calls, additional_budget=grant)
    assert len(model.requests) == before


def test_zero_duration_scripted_calls_can_resume_without_inventing_elapsed_usage(monkeypatch):
    monkeypatch.setattr(engine, "time", SimpleNamespace(monotonic=lambda: 100.0))
    model, states = SelectionModel(), []
    partial = run(model, states=states, maxModelCalls=2)
    assert partial["extraction"]["status"] == "partial"
    assert states[-1]["usage"]["elapsedSeconds"] == 0
    assert all(
        state[stage]["usage"]["elapsedSeconds"] == 0
        for state in states[-1]["tableStages"].values()
        for stage in ("structure", "meaning")
    )
    result = run(model, restore=states[-1], maxModelCalls=2, additional_budget={"maxModelCalls": 2})
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["extraction"]["usage"]["elapsedSeconds"] == 0
    assert len(model.requests) == 3


def test_failed_detail_consumes_shared_initial_allowance_and_needs_explicit_grant():
    model, states = SelectionModel(fail_details=True), []
    partial = run(model, states=states)
    assert partial["data"]["records"] and partial["extraction"]["status"] == "partial"
    assert progress(states[-1])["attempts"] == 2
    model.fail_details = False
    run(model, restore=states[-1])
    assert len(model.requests) == 3
    done = run(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert done["extraction"]["status"] == "complete", done["issues"]
    assert len(model.requests) == 4 and model.requests[-1]["meaningPhase"] == "details"


def test_explicit_selection_revision_is_durable_and_shares_the_same_allowance():
    model, states = SelectionModel(wrong_choice=True), []
    partial = run(model, states=states, maxModelCalls=3)
    state = progress(states[-1])
    assert partial["extraction"]["status"] == "partial"
    assert len(state["sourceSelections"]) == 2 and state["attempts"] == 2
    assert (
        state["sourceSelections"][1]["baseSelectionSHA256"]
        == state["sourceSelections"][0]["sha256"]
    )
    assert not state.get("acceptedResponse") and len(model.requests) == 3
    run(model, restore=states[-1], maxModelCalls=3)
    assert len(model.requests) == 3
    done = run(
        model,
        states=states,
        restore=states[-1],
        maxModelCalls=3,
        additional_budget={"maxModelCalls": 2},
    )
    assert done["extraction"]["status"] == "complete", done["issues"]
    assert progress(states[-1])["meaningSelections"] == [state["sourceSelections"][-1]["sha256"]]
    assert len(model.requests) == 4


def test_unselected_quotes_are_rejected_even_when_model_ignores_grammar():
    model, states = SelectionModel(malicious_quote=True), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "partial"
    assert any("table_selection_unselected_quote" in i.get("errors", []) for i in result["issues"])
    assert next(iter(states[-1]["accepted"].values()))["meanings"] == []
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "choice",
        "inventory",
        "structure",
        "model",
        "wire",
        "phase_usage",
        "association",
        "history_missing",
    ],
)
def test_tampered_selection_or_usage_is_rejected_before_resume_dispatch(mutation):
    model, states = SelectionModel(), []
    run(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    state = progress(checkpoint)
    record = state["sourceSelections"][0]
    if mutation == "choice":
        next(iter(record["response"]["sourceDecisions"].values()))["explanation"] = "Forged"
    elif mutation in {"inventory", "structure"}:
        record["inputIdentity"][mutation + "SHA256"] = "0" * 64
    elif mutation in {"model", "wire"}:
        record["inputIdentity"]["referenceWire" if mutation == "wire" else "model"] = None
    elif mutation == "phase_usage":
        state["phaseUsage"]["selection"]["modelCalls"] += 1
    elif mutation == "association":
        state["meaningSelections"] = ["0" * 64]
    else:
        state["sourceSelections"] = []
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(model, restore=checkpoint)
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "extra",
        "missing",
        "wrong_role",
        "blank_reason",
        "long_reason",
        "empty_positive",
        "malformed",
    ],
)
def test_selection_validation_does_not_rely_on_model_schema(mutation):
    inventory = {"sources": [{"sourceRef": "a", "text": ""}, {"sourceRef": "b", "text": " "}]}
    value = {
        "sourceDecisions": {
            ref: {"decision": "no_additional_meaning", "explanation": "Scripted choice"}
            for ref in ["a", "b"]
        }
    }
    if mutation == "extra":
        value["sourceDecisions"]["x"] = value["sourceDecisions"]["b"]
    elif mutation == "missing":
        del value["sourceDecisions"]["b"]
    elif mutation == "wrong_role":
        value["sourceDecisions"]["a"]["decision"] = "ignore"
    elif mutation == "blank_reason":
        value["sourceDecisions"]["a"]["explanation"] = " "
    elif mutation == "long_reason":
        value["sourceDecisions"]["a"]["explanation"] = "x" * 241
    elif mutation == "empty_positive":
        value["sourceDecisions"]["a"]["decision"] = "has_meaning"
    else:
        value["sourceDecisions"]["a"] = []
    with pytest.raises(CompileError, match="table_selection_"):
        check_selection(value, inventory)


def test_only_literal_empty_source_changes_choice_schema_not_whitespace():
    sources = [{"sourceRef": "a", "text": ""}, {"sourceRef": "b", "text": " "}]
    schema = selection_schema(sources)
    Draft202012Validator.check_schema(schema)
    choices = schema["properties"]["sourceDecisions"]["properties"]
    assert choices["a"] == {"$ref": "#/$defs/EmptySelectionChoice"}
    assert choices["b"] == {"$ref": "#/$defs/SelectionChoice"}
    assert (
        "has_meaning"
        not in schema["$defs"]["EmptySelectionChoice"]["properties"]["decision"]["enum"]
    )
    assert "has_meaning" in schema["$defs"]["SelectionChoice"]["properties"]["decision"]["enum"]


def test_interruption_after_negative_selection_resumes_local_compile_without_any_budget():
    model, states = SelectionModel(), []

    class Stopped(RuntimeError):
        pass

    def checkpoint(state):
        states.append(copy.deepcopy(state))
        if (
            state.get("tableStages")
            and progress(state).get("sourceSelections")
            and not progress(state).get("acceptedResponse")
        ):
            raise Stopped

    with pytest.raises(Stopped):
        extract_schema_from_stream(
            AnalysisJob(
                job_id="selection-test", input=AnalysisInput.from_bytes(HTML, format_id="html")
            ),
            io.BytesIO(HTML),
            model_client=model,
            options=ExtractionOptions(reconstructionContext=False, maxModelCalls=2),
            checkpoint=checkpoint,
        )
    assert len(model.requests) == 2
    result = run(model, content=HTML, restore=states[-1], maxModelCalls=2)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(model.requests) == 2


@pytest.mark.parametrize("mutation", ["stale", "unchanged", "unreviewed", "missing", "no_reason"])
def test_invalid_selection_revisions_are_rejected_with_structure_and_history_preserved(mutation):
    class BrokenRevision(SelectionModel):
        def infer(self, request):
            response = super().infer(request)
            value = json.loads(response.text)
            if value.get("action") != "revise_selection":
                return response
            payload = self.requests[-1]
            if mutation == "stale":
                value["baseSelectionSHA256"] = "0" * 64
            elif mutation == "unchanged":
                value["sourceDecisions"] = payload["sourceSelection"]["sourceDecisions"]
            elif mutation == "missing":
                value["sourceDecisions"].pop(next(iter(value["sourceDecisions"])))
            elif mutation == "no_reason":
                value["reason"] = " "
            else:
                key = next(
                    ref
                    for ref, d in value["sourceDecisions"].items()
                    if d["decision"] == "no_additional_meaning"
                )
                value["sourceDecisions"][key]["decision"] = "unreviewed"
            return InferenceResponse(json.dumps(value), response.usage)

    model, states = BrokenRevision(wrong_choice=True), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "partial" and result["data"]["records"]
    assert len(progress(states[-1])["sourceSelections"]) == 1
    assert len(model.requests) == 3
    assert any(i["code"] == "table_stage_invalid" for i in result["issues"])


@pytest.mark.parametrize(
    "role,code",
    [
        ("unresolved", "table_meaning_source_unresolved"),
        ("unreviewed", "table_meaning_source_unreviewed"),
    ],
)
def test_negative_selection_does_not_turn_unknown_or_deferred_sources_into_completion(role, code):
    class Uncertain(SelectionModel):
        def infer(self, request):
            response = super().infer(request)
            if self.requests[-1].get("meaningPhase") == "selection":
                value = json.loads(response.text)
                next(iter(value["sourceDecisions"].values()))["decision"] = role
                return InferenceResponse(json.dumps(value), response.usage)
            return response

    model, states = Uncertain(), []
    result = run(model, content=HTML, states=states)
    assert result["extraction"]["status"] == "partial" and result["data"]["records"]
    assert any(i["code"] == code for i in result["issues"])
    assert len(model.requests) == 3
    run(model, content=HTML, restore=states[-1])
    assert len(model.requests) == 3


def test_deterministic_negative_compile_failure_does_not_spin_or_reselect(monkeypatch):
    from document_files.interpretation import engine

    def failed(*args, **kwargs):
        raise CompileError("scripted_compile_failure")

    monkeypatch.setattr(engine, "meaning_ir", failed)
    model, states = SelectionModel(), []
    result = run(model, content=HTML, states=states)
    assert result["extraction"]["status"] == "partial" and len(model.requests) == 2
    assert progress(states[-1])["negativeCompileFailed"] is True
    run(model, content=HTML, restore=states[-1], states=states)
    assert len(model.requests) == 3 and model.requests[-1]["meaningPhase"] == "details"
    run(model, content=HTML, restore=states[-1])
    assert len(model.requests) == 3
