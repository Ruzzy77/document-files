"""Finite meaning-repair contracts; scripted replies are not model qualification."""

import copy
import io
import json
from types import SimpleNamespace

import pytest

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import engine
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.table_source_decisions import (
    source_decisions_from_flat,
    source_decisions_to_flat,
)
from document_files.interpretation.table_sources import source_inventory

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
            sources = {s["sourceRef"]: s["text"] for s in payload["meaningSources"]}
            caption = next((ref for ref, text in sources.items() if "Size uses mm." in text), None)
            feedback = payload.get("repairFeedback", {})
            if not isinstance(feedback, dict):
                feedback = {}  # Initial-format repair has no accepted revision.
            value = {
                "regionId": payload["regionId"],
                "meanings": [],
                "sourceReviews": [
                    {
                        "sourceRefs": [ref],
                        "role": "no_additional_meaning",
                        "explanation": "Scripted fixture review of preserved data or definition",
                    }
                    for ref, text in sources.items()
                    if ref != caption and text != "Additional context"
                ],
                "baseRevision": feedback.get("baseRevision"),
                "changes": [],
            }
            if caption:
                value["meanings"] = [
                    {
                        "id": "unit",
                        "kind": "unit",
                        "description": "Size uses millimeters",
                        "sourceQuotes": [{"sourceRef": caption, "text": "Size uses mm."}],
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
                    value["sourceReviews"].append(
                        {
                            "sourceRefs": [caption],
                            "role": "no_additional_meaning",
                            "explanation": "Title remainder; exact unit quote retained",
                        }
                    )
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
    infer = model.infer

    def current_wire(request):
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("meaningPhase") == "selection":
            model.requests.append(payload)
            # Explicit fixture behavior for the new call, counted in requests/usage.
            # These decisions are never provided to the product outside this model.
            decisions = {}
            for source in payload["meaningSources"]:
                text = source["text"]
                role = "no_additional_meaning"
                if "Size uses mm." in text or (
                    getattr(model, "correction_mode", None) == "retract" and text == "001.2300"
                ):
                    role = "has_meaning"
                elif text == "Additional context" or "reinspect" in text:
                    role = "unreviewed"
                decisions[source["sourceRef"]] = {
                    "decision": role,
                    "explanation": "Explicit scripted selection",
                }
            return InferenceResponse(
                json.dumps({"sourceDecisions": decisions}),
                {"prompt_tokens": 10, "completion_tokens": 20},
            )
        if (
            payload.get("meaningPhase") == "details"
            and getattr(model, "correction_mode", None) == "retract"
            and model.meaning_calls
        ):
            data = next(s for s in payload["meaningSources"] if s["text"] == "001.2300")
            decisions = copy.deepcopy(payload["sourceSelection"]["sourceDecisions"])
            if decisions[data["sourceRef"]]["decision"] == "has_meaning":
                model.requests.append(payload)
                decisions[data["sourceRef"]] = {
                    "decision": "no_additional_meaning",
                    "explanation": "Already represented data",
                }
                return InferenceResponse(
                    json.dumps(
                        {
                            "action": "revise_selection",
                            "baseSelectionSHA256": payload["selectionSHA256"],
                            "reason": "Correct the source choice before withdrawing its false unit",
                            "sourceDecisions": decisions,
                        }
                    ),
                    {"prompt_tokens": 10, "completion_tokens": 20},
                )
        response = infer(request)
        if payload.get("tableStage") == "meaning":
            value = source_decisions_from_flat(
                json.loads(response.text), {"sources": payload["meaningSources"]}
            )
            value.pop("sourceDecisions")
            positive = {
                ref
                for ref, choice in payload["sourceSelection"]["sourceDecisions"].items()
                if choice["decision"] == "has_meaning"
            }
            value["remainderReviews"] = [
                dict(review, sourceRefs=[ref for ref in review["sourceRefs"] if ref in positive])
                for review in value.pop("sourceReviews")
                if positive.intersection(review["sourceRefs"])
            ]
            return InferenceResponse(
                json.dumps(value), response.usage, response.finish_reason, response.timings
            )
        return response

    model.infer = current_wire
    try:
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
    finally:
        model.infer = infer


def flat_accepted_feedback(payload):
    """Read current-wire feedback in semantic fixture assertions, not production."""
    refs = [item["sourceRef"] for item in payload["meaningSources"]]
    inventory = source_inventory(
        {
            "nodes": {
                item["sourceRef"]: {"text": item["text"]} for item in payload["meaningSources"]
            }
        },
        {"nodeIds": refs},
    )
    return source_decisions_to_flat(payload["repairFeedback"]["acceptedResponse"], inventory)


def test_detail_schema_pins_the_accepted_meaning_baseline_not_the_selection_revision():
    class Capture(CaptionModel):
        def __init__(self):
            super().__init__()
            self.contracts = []

        def infer(self, request):
            response = super().infer(request)
            if self.requests[-1].get("meaningPhase") == "details":
                self.contracts.append(request.output_schema)
            return response

    model = Capture()
    result = run(model)
    assert result["extraction"]["status"] == "complete"
    initial, repair = [s["anyOf"][0]["properties"] for s in model.contracts]
    assert initial["baseRevision"] == {"type": "null", "const": None}
    assert initial["changes"]["maxItems"] == 0
    payload = model.requests[-1]
    baseline = payload["repairFeedback"]["baseRevision"]
    assert baseline != payload["selectionSHA256"]
    assert repair["baseRevision"] == {"type": "string", "const": baseline}
    assert repair["changes"]["maxItems"] > 0


def test_caption_accounting_repairs_inside_two_meaning_calls_with_stateless_context():
    model, states = CaptionModel(), []
    result = run(model, states=states)
    assert len(model.requests) == 4 and model.meaning_calls == 2
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    feedback = model.requests[-1]["repairFeedback"]
    assert any(i.startswith("table_meaning_source_unreviewed:") for i in feedback["issues"])
    assert (
        flat_accepted_feedback(model.requests[-1])["meanings"][0]["description"]
        == "Size uses millimeters"
    )
    assert flat_accepted_feedback(model.requests[-1])["meanings"][0]["scope"]["columnIds"] == [
        "size"
    ]
    assert "Correct mistaken" in feedback["instruction"]
    assert feedback["baseRevision"]
    assert "sourceQuotes" in flat_accepted_feedback(model.requests[-1])["meanings"][0]
    remaining = feedback["remainingSourceRanges"]
    assert len(remaining) == 1
    assert remaining[0]["text"] == "Measurements; "
    assert (remaining[0]["start"], remaining[0]["end"]) == (0, 14)
    assert remaining[0]["role"] == "unreviewed"
    assert model.requests[1]["frozenStructure"] == model.requests[2]["frozenStructure"]
    rid = next(iter(states[-1]["accepted"]))
    before = next(
        s
        for s in states
        if s["tableStages"].get(rid, {}).get("meaning", {}).get("acceptedResponse")
    )
    assert before["tableStages"][rid]["meaning"]["status"] == "pending"
    assert before["result"]["extraction"]["status"] == "partial"
    assert states[-1]["tableStages"][rid]["meaning"]["usage"]["modelCalls"] == 3
    assert before["accepted"][rid]["repeats"] == states[-1]["accepted"][rid]["repeats"]
    assert before["result"]["data"] == result["data"]
    assert run(model, restore=states[-1])["extraction"]["status"] == "complete"
    assert len(model.requests) == 4


class InvalidThenReview(CaptionModel):
    """Reject a wrong occurrence, then accept meaning before reviewing the title."""

    def __init__(self, *, invalid_calls=0, review_mode="fix"):
        super().__init__()
        self.invalid_calls = invalid_calls
        self.review_mode = review_mode

    def infer(self, request):
        self.mode = "same" if self.meaning_calls <= self.invalid_calls else self.review_mode
        response = super().infer(request)
        if (
            self.requests[-1]["tableStage"] == "meaning"
            and self.meaning_calls <= self.invalid_calls
        ):
            value = json.loads(response.text)
            value["meanings"][0]["sourceQuotes"][0]["occurrence"] = 1
            return InferenceResponse(json.dumps(value), response.usage)
        return response


def test_selection_and_invalid_detail_share_initial_budget_until_explicit_grant():
    model, states = InvalidThenReview(invalid_calls=1), []
    partial = run(model, states=states, maxModelCalls=3, completionSeconds=900)
    assert partial["extraction"]["status"] == "partial" and len(model.requests) == 3
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["attempts"] == 2 and not progress.get("acceptedResponse")
    run(model, restore=states[-1], maxModelCalls=3, completionSeconds=900)
    assert len(model.requests) == 3  # Neither stage nor document budget is reset.
    result = run(
        model,
        states=states,
        restore=states[-1],
        maxModelCalls=3,
        completionSeconds=900,
        additional_budget={"maxModelCalls": 2},
    )
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["extraction"]["budget"] == {"maxModelCalls": 5, "completionSeconds": 900}
    assert len(model.requests) == 5 and model.meaning_calls == 3
    assert model.requests[3]["repairFeedback"] == ["quote_occurrence_required_or_invalid"]
    assert (
        model.requests[4]["repairFeedback"]["remainingSourceRanges"][0]["text"] == "Measurements; "
    )
    assert all(
        r["frozenStructure"] == model.requests[1]["frozenStructure"] for r in model.requests[2:]
    )
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["attempts"] == 2 and progress["reviewAttempts"] == 1
    assert progress["usage"]["modelCalls"] == 4 and len(progress["revisions"]) == 2
    assert progress["phaseUsage"]["selection"]["modelCalls"] == 1
    run(model, restore=states[-1], maxModelCalls=3, completionSeconds=900)
    assert len(model.requests) == 5


def test_selection_plus_invalid_detail_does_not_receive_a_content_review_or_automatic_retry():
    model, states = InvalidThenReview(invalid_calls=2), []
    result = run(model, states=states, maxModelCalls=5)
    assert result["extraction"]["status"] == "partial"
    assert len(model.requests) == 3
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["attempts"] == 2 and progress["reviewAttempts"] == 0
    assert not progress.get("acceptedResponse")
    run(model, restore=states[-1], maxModelCalls=5)
    assert len(model.requests) == 3


@pytest.mark.parametrize("review_mode", ["same", "invalid", "timeout", "cancel"])
def test_one_post_acceptance_review_stops_on_failure_or_no_progress(review_mode):
    model, states = InvalidThenReview(review_mode=review_mode), []
    result = run(model, states=states, maxModelCalls=5)
    assert result["extraction"]["status"] == "partial"
    assert len(model.requests) == 4
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["reviewAttempts"] == 1 and len(progress["revisions"]) == 1
    run(model, restore=states[-1], maxModelCalls=5)
    assert len(model.requests) == 4


def test_content_review_never_exceeds_global_calls_and_grant_is_explicit():
    model, states = InvalidThenReview(), []
    result = run(model, states=states, maxModelCalls=3)
    assert len(model.requests) == 3 and result["extraction"]["status"] == "partial"
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["reviewAttempts"] == 0 and progress["acceptedResponse"]
    run(model, restore=states[-1], maxModelCalls=3)
    assert len(model.requests) == 3
    fixed = run(model, restore=states[-1], maxModelCalls=3, additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete" and len(model.requests) == 4


def test_elapsed_budget_stops_content_review_before_dispatch(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(engine, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class Slow(InvalidThenReview):
        def infer(self, request):
            response = super().infer(request)
            if self.meaning_calls == 1:
                now[0] = 901.0
            return response

    model, states = Slow(), []
    result = run(model, states=states, maxModelCalls=5, completionSeconds=900)
    assert len(model.requests) == 3 and result["extraction"]["status"] == "partial"
    assert any(i["code"] == "completion_budget_exceeded" for i in result["issues"])
    progress = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert progress["attempts"] == 2 and progress["reviewAttempts"] == 0
    assert progress["acceptedResponse"]


@pytest.mark.parametrize("attempts,reviews", [(4, 1), (3, 0), (3, 2), (2, True), (-1, 0), (0, 1)])
def test_invalid_stage_allowance_counters_are_rejected_on_restore(attempts, reviews):
    model, states = InvalidThenReview(), []
    run(model, states=states, maxModelCalls=5)
    forged = copy.deepcopy(states[-1])
    progress = next(iter(forged["tableStages"].values()))["meaning"]
    progress.update(attempts=attempts, reviewAttempts=reviews)
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(model, restore=forged, maxModelCalls=5)
    assert len(model.requests) == 4


@pytest.mark.parametrize("mode", ["drop", "rewrite", "weaken", "scope", "same", "invalid"])
def test_unexplained_change_cannot_replace_the_previous_source_bound_meaning(mode):
    model, states = CaptionModel(mode), []
    result = run(model, states=states)
    assert len(model.requests) == 4
    assert result["extraction"]["status"] == "partial"
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    ir = next(iter(states[-1]["accepted"].values()))
    assert ir["meanings"][0]["description"] == "Size uses millimeters"
    assert ir["meanings"][0]["status"] == "interpreted"
    assert ir["meanings"][0]["fieldIds"] == ["size"]
    assert not ir["dispositions"]
    assert any(i["code"] == "table_stage_invalid" for i in result["issues"])
    run(model, restore=states[-1])
    assert len(model.requests) == 4  # no automatic grant or restart loop
    model.mode = "fix"
    fixed = run(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete", fixed["issues"]
    assert len(model.requests) == 5


def test_global_budget_pause_resumes_only_accounting_repair_with_accepted_statements():
    model, states = CaptionModel(), []
    result = run(model, states=states, maxModelCalls=3)
    assert len(model.requests) == 3 and result["extraction"]["status"] == "partial"
    assert next(iter(states[-1]["tableStages"].values()))["meaning"]["acceptedResponse"] is True
    fixed = run(model, restore=states[-1], maxModelCalls=3, additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete", fixed["issues"]
    assert len(model.requests) == 4
    assert flat_accepted_feedback(model.requests[-1])["meanings"]


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
    assert len(model.requests) == 4
    fixed = run(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert fixed["extraction"]["status"] == "complete"
    assert len(model.requests) == 5


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
    assert len(model.requests) == 2 and model.meaning_calls == 0
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == "value_not_resolved" for i in result["issues"])


def test_structural_change_is_rejected_even_if_accounting_improves(monkeypatch):
    original = engine.meaning_ir

    def malicious(value, frozen, inventory):
        candidate = original(value, frozen, inventory)
        if value.get("baseRevision"):
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
    assert len(model.requests) == 3
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == "region_context_budget_exceeded" for i in result["issues"])
    assert next(iter(states[-1]["accepted"].values()))["meanings"][0]["id"] == "unit"


def test_partial_accounting_improvement_is_saved_without_claiming_completion():
    content = HTML.replace(b"<tr><th>", b"<caption>Additional context</caption><tr><th>")
    model, states = CaptionModel(), []
    result = run(model, states=states, content=content)
    assert len(model.requests) == 4
    assert result["extraction"]["status"] == "partial"
    assert len([i for i in result["issues"] if i["code"] == "table_meaning_source_unreviewed"]) == 1
    reviews = next(iter(states[-1]["accepted"].values()))["tableMeaningState"]["sourceReviews"]
    assert len(reviews) == 4
    assert sum(review["role"] == "unreviewed" for review in reviews) == 1
    assert next(iter(states[-1]["tableStages"].values()))["meaning"]["status"] == "pending"
    run(model, restore=states[-1], content=content)
    assert len(model.requests) == 4


class CorrectingModel(CaptionModel):
    """Wrong first scripted interpretation, not a semantic oracle in production."""

    def __init__(self, mode="split"):
        super().__init__()
        self.correction_mode = mode

    def infer(self, request):
        response = super().infer(request)
        payload = self.requests[-1]
        if payload["tableStage"] != "meaning":
            return response
        value = json.loads(response.text)
        caption = value["meanings"][0]["sourceQuotes"][0]["sourceRef"]
        data = next(s for s in payload["meaningSources"] if s["text"] == "001.2300")
        if self.meaning_calls == 1:
            if self.correction_mode == "retract":
                value["meanings"].append(
                    {
                        "id": "false-unit",
                        "kind": "unit",
                        "description": "Data summary, not a unit",
                        "sourceQuotes": [data],
                        "scope": {"kind": "record"},
                        "status": "interpreted",
                    }
                )
            else:
                value["meanings"][0].update(
                    kind="condition",
                    description="Measurements and unit treated as one condition",
                    sourceQuotes=[{"sourceRef": caption, "text": "Measurements; Size uses mm"}],
                    scope={"kind": "record"},
                )
                # The final punctuation remains unresolved, requiring the bounded second review.
                value["sourceReviews"].append(
                    {
                        "sourceRefs": [caption],
                        "role": "unresolved",
                        "explanation": "Review requested for the combined interpretation",
                    }
                )
        else:
            if self.correction_mode == "retract":
                change = {
                    "previousIds": ["false-unit"],
                    "replacementIds": [],
                    "reviewSourceRefs": [data["sourceRef"]],
                    "reason": "Already preserved data",
                }
            else:
                value["meanings"][0]["id"] = "correct-unit"
                value["meanings"].append(
                    {
                        "id": "title",
                        "kind": "definition",
                        "description": "Table title",
                        "sourceQuotes": [{"sourceRef": caption, "text": "Measurements"}],
                        "scope": {"kind": "record"},
                        "status": "interpreted",
                    }
                )
                change = {
                    "previousIds": ["unit"],
                    "replacementIds": ["correct-unit", "title"],
                    "reviewSourceRefs": [caption],
                    "reason": "Separate title from unit",
                }
            value["changes"] = [change]
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


@pytest.mark.parametrize("mode", ["split", "retract"])
def test_explicit_semantic_correction_is_accepted_without_losing_values(mode):
    model, states = CorrectingModel(mode), []
    result = run(model, states=states)
    if mode == "retract":
        assert result["extraction"]["status"] == "partial"
        assert len(next(iter(states[-1]["accepted"].values()))["meanings"]) == 2
        assert len(model.requests) == 4
        result = run(
            model, states=states, restore=states[-1], additional_budget={"maxModelCalls": 1}
        )
    assert len(model.requests) == (5 if mode == "retract" else 4)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {"rows": [{"size": "001.2300"}]}
    ir = next(iter(states[-1]["accepted"].values()))
    assert all(m["id"] != "false-unit" for m in ir["meanings"])
    if mode == "split":
        assert {m["id"] for m in ir["meanings"]} == {"correct-unit", "title"}
    history = next(iter(states[-1]["tableStages"].values()))["meaning"]["revisions"]
    assert len(history) == 2
    assert (
        history[0]["tableMeaningState"]["inventorySHA256"]
        == ir["tableMeaningState"]["inventorySHA256"]
    )
    assert result["coverage"]["semanticSourceReviews"][0]["unreviewed"] == []


@pytest.mark.parametrize(
    "mutation", ["remove_history", "history_content", "source_hash", "transition_reason"]
)
def test_tampered_meaning_checkpoint_cannot_resume(mutation):
    model, states = CorrectingModel("split"), []
    run(model, states=states)
    state = copy.deepcopy(states[-1])
    rid = next(iter(state["accepted"]))
    history = state["tableStages"][rid]["meaning"]["revisions"]
    if mutation == "remove_history":
        history.clear()
    elif mutation == "history_content":
        history[0]["meanings"][0]["description"] = "Tampered original meaning"
    elif mutation == "source_hash":
        state["accepted"][rid]["tableMeaningState"]["inventorySHA256"] = "0" * 64
    else:
        for part in (history[-1], state["accepted"][rid]):
            part["tableMeaningState"]["changes"][0]["reason"] = "Tampered historical reason"
    calls = len(model.requests)
    with pytest.raises(ValueError):
        run(model, restore=state)
    assert len(model.requests) == calls


def test_embedded_note_is_still_reviewed_after_its_entire_cell_is_read_as_value():
    class Embedded(CaptionModel):
        def infer(self, request):
            response = super().infer(request)
            payload = self.requests[-1]
            value = json.loads(response.text)
            if payload["tableStage"] == "structure":
                value["record"]["columns"][0]["valueType"] = "string"
            else:
                embedded = next(s for s in payload["meaningSources"] if "reinspect" in s["text"])
                value["sourceReviews"] = [
                    r
                    for r in value["sourceReviews"]
                    if embedded["sourceRef"] not in r["sourceRefs"]
                ]
                assert embedded["sourceRef"] in payload["sourceUsage"]["valueRefs"]
            return InferenceResponse(json.dumps(value), {})

    model = Embedded()
    content = HTML.replace(b"001.2300", b"001.2300; reinspect after heating")
    result = run(model, content=content)
    assert result["data"]["rows"][0]["size"] == "001.2300; reinspect after heating"
    assert result["extraction"]["status"] == "partial"
    reviews = result["coverage"]["semanticSourceReviews"][0]
    assert any("reinspect" in r["text"] and r["role"] == "unreviewed" for r in reviews["ranges"])
    assert len(model.requests) == 4


class ScopeAfterSourceReview(CaptionModel):
    def __init__(self, mode="scope", content_status="interpreted"):
        super().__init__()
        self.review_mode = mode
        self.content_status = content_status
        self.scope_calls = 0

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if "tableStage" not in payload:
            self.requests.append(payload)
            self.scope_calls += 1
            candidate = next(c for c in payload["candidates"] if c["label"] == "Size")
            return InferenceResponse(
                json.dumps(
                    {
                        "taskId": payload["taskId"],
                        "decision": "apply",
                        "targetHandles": [candidate["targetHandle"]],
                        "sourceRefs": list(
                            dict.fromkeys(
                                [
                                    *payload["statement"]["sourceRefs"],
                                    *candidate["definitionRefs"],
                                ]
                            )
                        ),
                        "explanation": "Scripted exact Size scope selection",
                    }
                ),
                {},
            )
        response = super().infer(request)
        if payload["tableStage"] != "meaning":
            return response
        value = json.loads(response.text)
        caption = value["meanings"][0]["sourceQuotes"][0]["sourceRef"]
        value["sourceReviews"] = [
            r for r in value["sourceReviews"] if caption not in r["sourceRefs"]
        ]
        value["sourceReviews"].append(
            {
                "sourceRefs": [caption],
                "role": "unresolved" if self.review_mode == "source" else "no_additional_meaning",
                "explanation": "Explicit review of the remainder, separate from applicability",
            }
        )
        if self.review_mode == "scope":
            value["meanings"][0]["scope"] = {"kind": "unresolved"}
            value["meanings"][0]["status"] = self.content_status
        elif self.review_mode == "uncertain":
            value["meanings"][0]["status"] = "uncertain"
        return InferenceResponse(json.dumps(value), {})


@pytest.mark.parametrize("content_status", ["interpreted", "uncertain"])
def test_direct_quote_scope_resolution_preserves_independent_content_status(content_status):
    model, states = ScopeAfterSourceReview(content_status=content_status), []
    result = run(model, states=states)
    assert model.meaning_calls == 1 and model.scope_calls == 1
    assert len(model.requests) == 4
    assert result["extraction"]["status"] == (
        "complete" if content_status == "interpreted" else "partial"
    ), result["issues"]
    assert any(i["code"] == "semantic_interpretation_uncertain" for i in result["issues"]) == (
        content_status == "uncertain"
    )
    assert result["data"]["rows"][0]["size"] == "001.2300"
    review = result["coverage"]["semanticSourceReviews"][0]
    assert review["unreviewed"] == review["unresolved"] == []
    assert not any(i["code"].startswith("table_meaning_source_") for i in result["issues"])
    resumed = run(model, restore=states[-1])
    assert resumed["extraction"]["status"] == result["extraction"]["status"]
    assert resumed["issues"] == result["issues"]
    assert len(model.requests) == 4


@pytest.mark.parametrize(
    "mode,code",
    [
        ("source", "table_meaning_source_unresolved"),
        ("uncertain", "semantic_scope_uncertain"),
    ],
)
def test_explicit_source_or_valid_target_uncertainty_remains_partial(mode, code):
    model = ScopeAfterSourceReview(mode)
    result = run(model)
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == code for i in result["issues"])
    assert result["data"]["rows"][0]["size"] == "001.2300"
    assert model.scope_calls == 0
    review = result["coverage"]["semanticSourceReviews"][0]
    assert bool(review["unresolved"]) is (mode == "source")
