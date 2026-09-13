"""Bounded early-retain recovery; scripted controller evidence, not AI approval."""

import copy
import json

import pytest
from test_document_protocol import execute, raw_document
from test_native_role_revision import JointModel, run
from test_native_structure_revision import saved_region

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.document_protocol import digest


class FollowupModel(JointModel):
    def __init__(self, outcome="replace", *, early_attempts=1):
        super().__init__()
        self.outcome = outcome
        self.early_attempts = early_attempts
        self.early_calls = self.late_calls = 0

    def infer(self, request):
        p = json.loads(request.messages[-1]["content"])
        if p["documentStage"] == "structureRevision":
            if "retainedReviewHash" not in p:
                self.early_calls += 1
                self.mode = "ledger" if self.early_calls < self.early_attempts else "retain"
            else:
                self.late_calls += 1
                self.mode = {
                    "replace": "roles",
                    "invalid": "ledger",
                    "transport": "transport",
                    "retain": "retain",
                    "inner": "retain",
                }[self.outcome]
        elif p["documentStage"] == "values":
            self.mode = "inner" if self.outcome == "inner" and self.late_calls else "retain"
        response = super().infer(request)
        if "batchId" in p:
            value = json.loads(response.text)
            value["batchId"] = p["batchId"]
            response = InferenceResponse(json.dumps(value), response.usage)
        return response


@pytest.mark.parametrize("batched", [False, True])
def test_actual_conflict_uses_remaining_review_before_identical_value_retry(monkeypatch, batched):
    if batched:
        from document_files.interpretation import native_value_batches

        monkeypatch.setattr(native_value_batches, "is_needed", lambda *args: True)
    model, states = FollowupModel(), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"reference": "REF-007"}
    assert model.stages == [
        "roles",
        "structure",
        "structureRevision",
        "values",
        "structureRevision",
        "values",
    ]
    state = saved_region(states[-1])
    review = state["revision"]
    assert review["attempts"] == review["usage"]["modelCalls"] == 2
    assert review["priorReview"]["attempts"] == 1
    assert review["priorReview"]["decision"] == "retain"
    assert review["base"]["content"]["attempts"] == 1
    assert review["base"]["content"]["roleValueFailure"]["code"] == "document_role_value_conflict"
    assert review["priorReview"]["base"]["content"]["attempts"] == 0
    assert review["priorReview"]["requestHash"] != review["requestHash"]
    assert "roleValueFailure" not in state["content"]
    assert "roleSourceReview" not in review["base"]["content"]
    if batched:
        failure = review["base"]["content"]["roleValueFailure"]
        assert failure["batch"]["phase"] == "values"
    assert any(
        saved_region(s).get("revision", {}).get("decision") == "replace"
        and s["result"]["data"] == {"reference": None}
        for s in states
        if s["documentStages"]
    )
    again = run(model, restore=states[-1])
    assert again["document"] == out["document"] and model.calls == 6


@pytest.mark.parametrize("outcome", ["retain", "invalid"])
def test_no_third_review_and_no_success_from_retention_or_invalid_changes(outcome):
    model, states = FollowupModel(outcome), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial" and out["data"] == {"reference": None}
    assert model.early_calls == model.late_calls == 1
    assert model.calls == (6 if outcome == "retain" else 5)
    before = model.calls
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == before


def test_followup_may_retain_a_real_title_and_read_an_inner_attribute():
    model, states = FollowupModel("inner"), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"reference": "007"} and model.calls == 6
    assert out["document"]["outline"]["elements"][0]["role"] == "title"
    assert saved_region(states[-1])["revision"]["decision"] == "retain"
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 6


def test_exhausted_early_attempts_do_not_get_a_new_cycle():
    model, states = FollowupModel(early_attempts=2), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial"
    assert model.early_calls == 2 and model.late_calls == 0 and model.calls == 6
    assert "priorReview" not in saved_region(states[-1])["revision"]
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 6


@pytest.mark.parametrize("budget", [3, 4, 5])
@pytest.mark.parametrize("batched", [False, True])
def test_budget_pause_and_explicit_grant_keep_review_and_failed_read_history(
    monkeypatch, budget, batched
):
    if batched:
        from document_files.interpretation import native_value_batches

        monkeypatch.setattr(native_value_batches, "is_needed", lambda *args: True)
    model, states = FollowupModel(), []
    out = run(model, states=states, budget=budget)
    assert out["extraction"]["status"] == "partial" and model.calls == budget
    assert run(model, restore=states[-1], budget=budget)["data"] == out["data"]
    assert model.calls == budget
    out = run(model, restore=states[-1], budget=budget, grant={"maxModelCalls": 6 - budget})
    assert out["extraction"]["status"] == "complete" and model.calls == 6


def test_unknown_followup_needs_a_grant_without_erasing_early_usage():
    model, states = FollowupModel("transport"), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial" and model.calls == 5
    model.outcome = "replace"
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 5
    final = run(model, restore=states[-1], grant={"maxModelCalls": 2})
    assert final["extraction"]["status"] == "complete" and model.calls == 7
    assert final["extraction"]["modelCalls"] == 7


@pytest.mark.parametrize(
    "damage",
    ["prior_decision", "prior_overlap", "prior_usage", "request", "no_conflict", "nested", "usage"],
)
def test_replay_reproduces_the_failure_and_rechecks_the_early_review(damage):
    model, states = FollowupModel(), []
    run(model, states=states)
    state = copy.deepcopy(states[-1])
    review = saved_region(state)["revision"]
    if damage == "prior_decision":
        review["priorReview"]["decision"] = "replace"
    elif damage == "prior_overlap":
        review["priorReview"]["base"]["content"]["roleSourceReview"] = []
    elif damage == "prior_usage":
        review["priorReview"]["usage"]["modelCalls"] = review["usage"]["modelCalls"]
    elif damage == "nested":
        review["priorReview"]["priorReview"] = copy.deepcopy(review["priorReview"])
    elif damage == "usage":
        review["attempts"] = 1
    else:
        failure = review["base"]["content"]["roleValueFailure"]
        if damage == "request":
            failure["requestBaseHash"] = "foreign"
        else:
            failure["response"]["selections"]["@value1"]["quote"]["text"] = "007"
            failure["responseHash"] = digest(failure["response"])
    with pytest.raises(ValueError, match="checkpoint"):
        run(model, restore=state)
    assert model.calls == 6


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
            if "retainedReviewHash" not in p:
                value["decision"] = "retain"
            else:
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
        "structureRevision",
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
    assert execute(model, raw=raw, restore=states[-1])["data"] == out["data"] and model.calls == 8
    for field, bad in [("index", 99), ("phase", "foreign"), ("requestHash", "bad")]:
        damaged = copy.deepcopy(states[-1])
        failure = saved_region(damaged)["revision"]["base"]["content"]["roleValueFailure"]
        failure["batch"][field] = bad
        with pytest.raises(ValueError, match="checkpoint"):
            execute(model, raw=raw, restore=damaged)
    assert model.calls == 8
