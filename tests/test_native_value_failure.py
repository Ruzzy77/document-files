"""Real-value-first review through a native parser; scripted decisions, not AI approval."""

import copy
import json

import pytest
from test_document_protocol import execute, raw_document
from test_native_role_revision import JointModel, run
from test_native_structure_revision import saved_region

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.document_protocol import digest


class FailureModel(JointModel):
    def __init__(self, outcome="replace"):
        super().__init__()
        self.outcome = outcome
        self.review_calls = 0

    def infer(self, request):
        p = json.loads(request.messages[-1]["content"])
        if p["documentStage"] == "structureRevision":
            self.review_calls += 1
            self.mode = {
                "replace": "roles",
                "invalid": "ledger",
                "transport": "transport",
                "retain": "retain",
                "inner": "retain",
            }[self.outcome]
        elif p["documentStage"] == "values":
            self.mode = "inner" if self.outcome == "inner" and self.review_calls else "retain"
        response = super().infer(request)
        if "batchId" in p:
            value = json.loads(response.text)
            value["batchId"] = p["batchId"]
            response = InferenceResponse(json.dumps(value), response.usage)
        return response


@pytest.mark.parametrize("batched", [False, True])
def test_first_concrete_conflict_precedes_the_only_review(monkeypatch, batched):
    if batched:
        from document_files.interpretation import native_value_batches

        monkeypatch.setattr(native_value_batches, "is_needed", lambda *args: True)
    model, states = FailureModel(), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"reference": "REF-007"}
    assert model.stages == ["roles", "structure", "values", "structureRevision", "values"]
    review = saved_region(states[-1])["revision"]
    assert review["attempts"] == review["usage"]["modelCalls"] == 1
    assert "priorReview" not in review
    assert review["base"]["content"]["attempts"] == 1
    assert review["base"]["content"]["roleValueFailure"]["code"] == "document_role_value_conflict"
    assert "roleValueFailure" not in saved_region(states[-1])["content"]
    assert run(model, restore=states[-1])["document"] == out["document"] and model.calls == 5


@pytest.mark.parametrize("outcome", ["retain", "invalid"])
def test_no_new_cycle_after_retention_or_invalid_replacement(outcome):
    model, states = FailureModel(outcome), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial" and out["data"] == {"reference": None}
    assert model.review_calls == (1 if outcome == "retain" else 2)
    assert model.calls == 5
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 5


def test_retention_can_be_followed_by_genuine_inner_value_not_forced_role_edit():
    model, states = FailureModel("inner"), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"reference": "007"}
    assert out["document"]["outline"]["elements"][0]["role"] == "title"
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 5


@pytest.mark.parametrize("budget", [2, 3, 4])
@pytest.mark.parametrize("batched", [False, True])
def test_pause_grant_and_failed_read_cost_survive(monkeypatch, budget, batched):
    if batched:
        from document_files.interpretation import native_value_batches

        monkeypatch.setattr(native_value_batches, "is_needed", lambda *args: True)
    model, states = FailureModel(), []
    out = run(model, states=states, budget=budget)
    assert out["extraction"]["status"] == "partial" and model.calls == budget
    assert (
        run(model, restore=states[-1], budget=budget)["data"] == out["data"]
        and model.calls == budget
    )
    out = run(model, restore=states[-1], budget=budget, grant={"maxModelCalls": 5 - budget})
    assert out["extraction"]["status"] == "complete" and model.calls == 5
    assert out["extraction"]["modelCalls"] == 5


def test_unknown_review_needs_explicit_grant_without_erasing_failed_read():
    model, states = FailureModel("transport"), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial" and model.calls == 4
    model.outcome = "replace"
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 4
    final = run(model, restore=states[-1], grant={"maxModelCalls": 2})
    assert final["extraction"]["status"] == "complete" and model.calls == 6
    assert final["extraction"]["modelCalls"] == 6


@pytest.mark.parametrize(
    "damage",
    [
        "request",
        "no_conflict",
        "none_failure",
        "no_read",
        "prior_review",
        "early_marker",
        "false_usage",
    ],
)
def test_replay_requires_actual_failed_values_not_codes_or_prior_role_suspicion(damage):
    model, states = FailureModel(), []
    run(model, states=states)
    saved = copy.deepcopy(states[-1])
    review = saved_region(saved)["revision"]
    content = review["base"]["content"]
    if damage == "none_failure":
        content["roleValueFailure"] = None
    elif damage == "no_read":
        content["attempts"] = 0
    elif damage == "prior_review":
        review["priorReview"] = {}
    elif damage == "early_marker":
        content["roleSourceReview"] = [{}]
    elif damage == "false_usage":
        content["usage"]["modelCalls"] = 0
    elif damage == "request":
        content["roleValueFailure"]["requestBaseHash"] = "foreign"
    else:
        failure = content["roleValueFailure"]
        failure["response"]["selections"]["@value1"]["quote"]["text"] = "007"
        failure["responseHash"] = digest(failure["response"])
    with pytest.raises(ValueError, match="checkpoint"):
        run(model, restore=saved)
    assert model.calls == 5


class MultiBatchModel:
    identity = {"model": "scripted-retain-followup-multiple-values", "configurationId": "1"}

    def __init__(self):
        self.calls = 0
        self.stages = []

    def infer(self, request):
        from document_files.interpretation.native_structure_history import expand

        p = json.loads(request.messages[-1]["content"])
        stage = p["documentStage"]
        self.calls += 1
        self.stages.append(stage)
        roles = [
            {
                "sourceRef": ref,
                "role": role,
                "level": level,
                "captionOf": None,
                "status": "interpreted",
            }
            for ref, role, level in [("n1", "title", 0), ("n2", "field_group", None)]
        ]
        if stage == "roles":
            value = {"regionId": p["regionId"], "documentElements": roles}
        elif stage == "structure":
            value = {
                "regionId": p["regionId"],
                "fields": [
                    {
                        "key": key,
                        "label": key,
                        "valueType": "string",
                        "sourceRefs": [ref],
                        "status": "present",
                    }
                    for key, ref in [("other", "n2"), ("reference", "n1")]
                ],
                "dispositions": [
                    {"sourceRef": ref, "role": "data", "explanation": "Reference attribute"}
                    for ref in ["n1", "n2"]
                ],
            }
        elif stage == "structureRevision":
            value = {"baseStructureHash": p["baseStructureHash"], "reason": "Review actual source"}
            roles[0].update(role="field_group", level=None)
            value.update(
                decision="replace",
                replacement=expand(p["previousStructure"]),
                documentElements=roles,
                changes=[
                    {
                        "action": action,
                        "before": [entity],
                        "after": [entity],
                        "anchors": [ref],
                        "reason": "Check original source",
                    }
                    for entity, ref, action in [
                        ("field:1", "n2", "keep"),
                        ("field:2", "n1", "keep"),
                        ("role:1", "n1", "replace"),
                        ("role:2", "n2", "keep"),
                    ]
                ],
            )
        elif stage == "values":
            value = {
                "regionId": p["regionId"],
                "batchId": p["batchId"],
                "selections": {
                    h: {
                        "kind": "quote",
                        "quote": {
                            "sourceRef": v["sourceRefs"][0],
                            "text": {"n1": "REF-007", "n2": "ALPHA"}[v["sourceRefs"][0]],
                        },
                    }
                    for h, v in p["handles"].items()
                },
                "excludedBindings": [],
            }
        else:
            raise AssertionError(stage)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 3, "completion_tokens": 2})


def test_later_batch_conflict_keeps_prior_accepted_values_only_in_checked_history(monkeypatch):
    from document_files.interpretation import native_value_batches

    monkeypatch.setattr(native_value_batches, "is_needed", lambda *args: True)
    monkeypatch.setattr(
        native_value_batches, "partition", lambda p, s, kind, keys, *args: [[k] for k in keys]
    )
    raw = raw_document(("REF-007", "ALPHA"))
    model, states = MultiBatchModel(), []
    out = execute(model, raw=raw, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"other": "ALPHA", "reference": "REF-007"}
    assert model.stages == [
        "roles",
        "structure",
        "values",
        "values",
        "structureRevision",
        "values",
        "values",
    ]
    record = saved_region(states[-1])["revision"]
    assert record["base"]["accepted"] is not None
    assert record["base"]["content"]["usage"]["modelCalls"] == 2
    assert record["base"]["content"]["roleValueFailure"]["batch"]["index"] == 1
    assert record["base"]["content"]["batches"]["values"][0]["response"]
    assert "response" not in record["base"]["content"]["batches"]["values"][1]
    assert any(
        saved_region(s).get("revision", {}).get("decision") == "replace"
        and s["result"]["data"] == {"other": None, "reference": None}
        for s in states
        if s["documentStages"]
    )
    assert execute(model, raw=raw, restore=states[-1])["data"] == out["data"] and model.calls == 7
    for field, bad in [("index", 99), ("phase", "foreign"), ("requestHash", "bad")]:
        damaged = copy.deepcopy(states[-1])
        failure = saved_region(damaged)["revision"]["base"]["content"]["roleValueFailure"]
        failure["batch"][field] = bad
        with pytest.raises(ValueError, match="checkpoint"):
            execute(model, raw=raw, restore=damaged)
    assert model.calls == 7
