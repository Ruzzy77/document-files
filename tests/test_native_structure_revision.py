"""Atomic source-grounded structure replacement; scripted decisions, not AI approval."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_document_protocol import StagedModel, execute, raw_document
from test_native_structure import prepared

from document_files.interpretation import native_structure_revision as revision
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.document_protocol import digest
from document_files.interpretation.native_structure_history import expand as expand_history

RAW = raw_document(("Measure: 12.5000",))


class RevisionModel(StagedModel):
    def __init__(self, review="replace"):
        super().__init__()
        self.review = review
        self.revision_requests = []
        self.revised = False

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        stage = payload["documentStage"]
        if stage == "structureRevision":
            self.calls += 1
            self.revision_requests.append(payload)
            if self.review == "transport":
                raise ModelError("ai_test_revision_transport_failure")
            if self.review == "truncated":
                return InferenceResponse('{"decision":"replace"', {}, finish_reason="length")
            value = {
                "baseStructureHash": payload["baseStructureHash"],
                "reason": "Source says a precise measure, not a separate empty box.",
            }
            if self.review == "retain":
                value["decision"] = "retain"
            else:
                field = copy.deepcopy(expand_history(payload["previousStructure"])["fields"][0])
                field["valueType"] = "decimal"
                value.update(
                    decision="replace",
                    replacement={"regionId": payload["regionId"], "fields": [field]},
                    changes=[
                        {
                            "action": "replace",
                            "before": ["field:1"],
                            "after": ["field:1"],
                            "anchors": ["n1"],
                            "reason": "Retain precise spelling, not a floating point number.",
                        },
                        {
                            "action": "remove",
                            "before": ["field:2"],
                            "after": [],
                            "anchors": ["n1"],
                            "reason": "The empty box has no source; retain the original measure.",
                        },
                    ],
                )
                if self.review == "invalid":
                    value["changes"].pop()
                else:
                    self.revised = True
            return InferenceResponse(
                json.dumps(value), {"prompt_tokens": 13, "completion_tokens": 7}
            )
        if stage == "values":
            self.calls += 1
            self.content_requests.append(payload)
            bid = next(
                b for b, v in payload["bindings"].items() if v.get("candidateRole") == "value"
            )
            value = {
                "regionId": payload["regionId"],
                "selections": {
                    h: {"kind": "binding", "bindingId": bid, "status": "present"}
                    if e["status"] == "present"
                    else {"kind": "unresolved"}
                    for h, e in payload["handles"].items()
                },
                "excludedBindings": [],
            }
            return InferenceResponse(
                json.dumps(value), {"prompt_tokens": 9, "completion_tokens": 3}
            )
        response = super().infer(request)
        if stage == "structure":
            value = json.loads(response.text)
            value["fields"] = [
                {
                    "key": "measure",
                    "label": "Measure",
                    "valueType": "number",
                    "definitionRefs": ["n1"],
                    "sourceRefs": ["n1"],
                    "status": "present",
                }
            ]
            ghost = copy.deepcopy(value["fields"][0])
            ghost.update(
                key="unfounded_box", label="Unfounded box", status="blank", valueType="string"
            )
            value["fields"].append(ghost)
            return InferenceResponse(json.dumps(value), response.usage)
        return response


def run(model, **kwargs):
    return execute(model, raw=RAW, **kwargs)


def saved_region(checkpoint):
    return next(iter(checkpoint["documentStages"].values()))


def test_valid_revision_commits_atomically_and_rereads_even_equal_keys_and_handles():
    model, states = RevisionModel(), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {"measure": "12.5000"}
    assert model.calls == 6 and len(model.structure_requests) == 1
    state = saved_region(states[-1])
    record = state["revision"]
    assert record["decision"] == "replace" and record["usage"]["modelCalls"] == 1
    assert record["base"]["content"]["usage"]["modelCalls"] == 2
    assert state["content"]["usage"]["modelCalls"] == 1
    assert record["base"]["accepted"]["fields"][0]["valueType"] == "number"
    assert out["valueEvidence"][0]["raw"] == "12.5000"
    assert state["structure"]["revisionHash"] == digest(record["response"])
    assert any(s["result"]["data"] == {"measure": 12.5, "unfounded_box": None} for s in states)
    # After committing the new structure, the old number is not relabeled as a decimal.
    assert any(
        s["result"]["data"] == {"measure": None}
        and saved_region(s).get("revision", {}).get("decision") == "replace"
        for s in states
    )
    assert len(model.content_requests) == 3
    restored = run(model, restore=states[-1])
    assert restored["data"] == out["data"] and model.calls == 6


@pytest.mark.parametrize("mode,calls", [("invalid", 6), ("retain", 5)])
def test_rejected_or_retained_revision_preserves_the_previous_partial(mode, calls):
    model, states = RevisionModel(mode), []
    out = run(model, states=states)
    assert out["data"] == {"measure": 12.5, "unfounded_box": None}
    assert out["extraction"]["status"] == "partial" and model.calls == calls
    state = saved_region(states[-1])
    before = state["revision"]["base"]
    assert state["structure"] == before["structure"] and state["content"] == before["content"]
    assert "revisionHash" not in state["structure"]
    again = run(model, restore=states[-1])
    assert again["data"] == out["data"] and model.calls == calls


@pytest.mark.parametrize("budget", [4, 5])
def test_budget_pause_resumes_revision_or_new_values_without_replaying_old_reads(budget):
    model, states = RevisionModel(), []
    out = run(model, states=states, budget=budget)
    assert model.calls == budget and out["extraction"]["status"] == "partial"
    assert out["data"] == (
        {"measure": 12.5, "unfounded_box": None} if budget == 4 else {"measure": None}
    )
    again = run(model, restore=states[-1], budget=budget)
    assert again["data"] == out["data"] and model.calls == budget
    final = run(model, restore=states[-1], budget=budget, grant={"maxModelCalls": 6 - budget})
    assert (
        final["data"] == {"measure": "12.5000"} and final["extraction"]["status"] == "complete"
    ), final["issues"]
    assert model.calls == final["extraction"]["usage"]["modelCalls"] == 6


@pytest.mark.parametrize("failure", ["transport", "truncated"])
def test_unknown_revision_exchange_needs_explicit_grant_and_keeps_old_data(failure):
    model, states = RevisionModel(failure), []
    out = run(model, states=states)
    assert model.calls == 5 and out["data"] == {"measure": 12.5, "unfounded_box": None}
    record = saved_region(states[-1])["revision"]
    assert record["halted"] and record["usage"]["unreportedUsageCalls"] == 1
    model.review = "replace"
    again = run(model, restore=states[-1])
    assert again["data"] == out["data"] and model.calls == 5
    final = run(model, restore=states[-1], grant={"maxModelCalls": 2})
    assert final["extraction"]["status"] == "complete" and final["data"] == {
        "measure": "12.5000"
    }, final["issues"]
    assert model.calls == final["extraction"]["usage"]["modelCalls"] == 7


def test_saved_inflight_revision_cannot_be_replayed_or_claimed_as_accepted():
    model, states = RevisionModel(), []
    run(model, states=states)
    checkpoint = next(
        s
        for s in states
        if s["documentStages"] and saved_region(s).get("revision", {}).get("status") == "running"
    )
    calls = model.calls
    result = run(model, restore=checkpoint)
    assert model.calls == calls and result["data"] == {"measure": 12.5, "unfounded_box": None}
    assert result["extraction"]["status"] == "partial"


@pytest.mark.parametrize(
    "damage",
    [
        "base_wire",
        "base_read",
        "transition",
        "ledger",
        "usage",
        "request",
        "revision_flag",
        "lost_history",
    ],
)
def test_revision_checkpoint_rebuilds_base_changes_and_current_values(damage):
    model, states = RevisionModel(), []
    run(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    state = saved_region(checkpoint)
    record = state["revision"]
    if damage == "base_wire":
        record["base"]["structure"]["wireResponse"]["fields"][0]["label"] = "Changed"
    elif damage == "base_read":
        record["base"]["content"]["response"]["selections"]["@value1"] = {"kind": "unresolved"}
    elif damage == "transition":
        state["structure"]["revisionHash"] = "f" * 64
    elif damage == "ledger":
        record["response"]["changes"].pop()
        record["responseHash"] = digest(record["response"])
        state["structure"]["revisionHash"] = record["responseHash"]
    elif damage == "usage":
        record["usage"]["modelCalls"] = 0
    elif damage == "request":
        record["requestHash"] = "0" * 64
    elif damage == "revision_flag":
        record["decision"] = "retain"
    else:
        del state["revision"]
    with pytest.raises(ValueError, match="checkpoint"):
        run(model, restore=checkpoint)
    assert model.calls == 6


def test_revision_request_preserves_every_source_property_with_lossless_factoring():
    model, states = RevisionModel(), []
    run(model, states=states)
    from document_files.interpretation.source_dictionary import source_nodes

    restored = source_nodes(model.revision_requests[0], "blocks")
    assert restored == source_nodes(model.structure_requests[0], "blocks")
    assert (
        "bindings" not in model.revision_requests[0]
        and "expected" not in model.revision_requests[0]
    )


def test_change_inventory_covers_record_columns_every_row_and_meaning_anchors():
    doc, region, roles, structure, _ = prepared()
    from test_document_outline import structure_wire

    wire = structure_wire(structure.model_dump(exclude_unset=True))
    old = revision.inventory(wire)
    assert {
        "record:1",
        "record:1:column:1",
        "record:1:row:1",
        "record:1:row:2",
        "meaning:1",
    } <= old.keys()
    state = {
        "base": {
            "structure": {"wireResponse": wire, "structureHash": digest(structure.model_dump())},
            "content": {},
        },
        "trigger": ["test_failed_read"],
    }
    replacement = copy.deepcopy(wire)
    replacement["records"][0]["label"] = "Items as recorded"
    value = {
        "decision": "replace",
        "baseStructureHash": state["base"]["structure"]["structureHash"],
        "reason": "Review each source occurrence.",
        "replacement": replacement,
        "changes": [
            {
                "action": "replace",
                "before": list(old),
                "after": list(revision.inventory(replacement)),
                "anchors": list(region["nodeIds"]),
                "reason": "Keep all fields, rows and their meaning sources.",
            }
        ],
    }
    schema = revision.request(state, roles, doc, region, {})[1]
    Draft202012Validator.check_schema(schema)
    updated, fragment = revision.accept(value, state, roles, doc, region, {})
    assert len(updated.records[0].rows) == len(fragment.data["items"]) == 2
    for mutation, code in [
        ("omit_old", "incomplete_changes"),
        ("omit_new", "incomplete_changes"),
        ("duplicate", "duplicate_change"),
        ("source", "source_uncovered"),
        ("keep", "changed_retained_entity"),
        ("invented", "quote_not_in_source"),
    ]:
        bad = copy.deepcopy(value)
        if mutation == "omit_old":
            bad["changes"][0]["before"].pop()
        elif mutation == "omit_new":
            bad["changes"][0]["after"].pop()
        elif mutation == "duplicate":
            bad["changes"].append(copy.deepcopy(bad["changes"][0]))
        elif mutation == "source":
            bad["changes"][0]["anchors"] = [region["nodeIds"][0]]
        elif mutation == "keep":
            bad["changes"][0]["action"] = "keep"
        else:
            bad["changes"][0]["anchors"] = [
                {"sourceRef": region["nodeIds"][0], "text": "INVENTED SOURCE"}
            ]
        with pytest.raises(ValueError, match=code):
            revision.accept(bad, state, roles, doc, region, {})


def test_accepted_revision_replans_batches_and_preserves_retired_read_cost():
    from test_native_value_batches import ManyValues
    from test_native_value_batches import run as run_many

    class ReviseBatch(ManyValues):
        def __init__(self):
            super().__init__()
            self.revised = False

        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if payload["documentStage"] == "structureRevision":
                self.calls.append(payload)
                self.revised = True
                wire = expand_history(payload["previousStructure"])
                wire["fields"][0]["label"] = "Reviewed reading 00"
                return InferenceResponse(
                    json.dumps(
                        {
                            "decision": "replace",
                            "baseStructureHash": payload["baseStructureHash"],
                            "reason": "Retain all observations with the reviewed first definition.",
                            "replacement": wire,
                            "changes": [
                                {
                                    "action": "replace",
                                    "before": list(
                                        revision.inventory(
                                            expand_history(payload["previousStructure"])
                                        )
                                    ),
                                    "after": list(revision.inventory(wire)),
                                    "anchors": ["n1"],
                                    "reason": "The source supports all thirty-two definitions.",
                                }
                            ],
                        }
                    ),
                    {"prompt_tokens": 17, "completion_tokens": 8},
                )
            response = super().infer(request)
            if payload["documentStage"] == "values" and not self.revised:
                value = json.loads(response.text)
                if "@value1" in value["selections"]:
                    value["selections"]["@value1"] = {"kind": "unresolved"}
                return InferenceResponse(json.dumps(value), response.usage)
            return response

    model, states = ReviseBatch(), []
    out = run_many(model, states=states, budget=12)
    assert out["extraction"]["status"] == "partial" and len(model.calls) == 12
    assert out["data"] == {f"reading{i:02d}": 1000 + i for i in range(32)}
    # The original limit is respected: final source accounting needs an explicit grant.
    out = run_many(model, states=states, restore=states[-1], budget=12, grant={"maxModelCalls": 2})
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == {f"reading{i:02d}": 1000 + i for i in range(32)}
    current = saved_region(states[-1])
    old = current["revision"]["base"]["content"]
    assert old["usage"]["modelCalls"] == 5
    assert current["content"]["usage"]["modelCalls"] >= 6
    assert old["batches"]["identity"] != current["content"]["batches"]["identity"]
    assert out["extraction"]["usage"]["modelCalls"] == len(model.calls)
    revision_index = next(
        i for i, p in enumerate(model.calls) if p["documentStage"] == "structureRevision"
    )
    reads_after_revision = [
        p for p in model.calls[revision_index + 1 :] if p["documentStage"] == "values"
    ]
    assert set().union(*(set(p["handles"]) for p in reads_after_revision)) == {
        f"@value{i + 1}" for i in range(32)
    }
    calls = len(model.calls)
    restored = run_many(model, restore=states[-1], budget=12)
    assert restored["data"] == out["data"] and len(model.calls) == calls


def test_revision_preflight_keeps_old_values_and_does_not_dispatch_oversized_context():
    class SmallBudget(RevisionModel):
        def infer(self, request):
            response = super().infer(request)
            if len(self.content_requests) == 2:
                self.input_budget_chars = 1000
            return response

    model, states = SmallBudget(), []
    out = run(model, states=states)
    assert model.calls == 4 and not model.revision_requests
    assert out["data"] == {"measure": 12.5, "unfounded_box": None}
    record = saved_region(states[-1])["revision"]
    assert record["attempts"] == record["usage"]["modelCalls"] == 0
    assert not record["inputPreflight"]["withinBudget"]
    assert record["inputPreflight"]["stage"] == "structureRevision"
    assert out["extraction"]["status"] == "partial"


def test_change_can_cite_a_genuinely_empty_block_without_fabricating_quote_text():
    from test_document_outline import decision

    from document_files.document_model.model import ObservationDocument

    doc = ObservationDocument(provenance={"format": "hwpx"})
    doc.node("n1", "Item label")
    doc.node("n2", "")
    doc.bind("n2", start=0, end=0, candidateRole="value", blank=True)
    region = {"id": "r", "nodeIds": ["n1", "n2"], "bindingIds": list(doc.bindings)}
    roles = {
        "regionId": region["id"],
        "documentElements": [decision(role="paragraph", level=None).model_dump()],
    }
    wire = {
        "regionId": region["id"],
        "fields": [
            {
                "key": "item",
                "label": "Item",
                "valueType": "integer",
                "status": "blank",
                "sourceRefs": ["n2"],
                "definitionRefs": ["n1"],
            }
        ],
    }
    state = {
        "base": {"structure": {"wireResponse": wire, "structureHash": digest(wire)}, "content": {}},
        "trigger": ["test_failed_read"],
    }
    changed = copy.deepcopy(wire)
    changed["fields"][0]["valueType"] = "string"
    value = {
        "decision": "replace",
        "baseStructureHash": digest(wire),
        "reason": "Review an empty source.",
        "replacement": changed,
        "changes": [
            {
                "action": "replace",
                "before": ["field:1"],
                "after": ["field:1"],
                "anchors": ["n1", "n2"],
                "reason": "The source block is present and empty.",
            }
        ],
    }
    structure, fragment = revision.accept(value, state, roles, doc, region, {})
    assert structure.fields[0].status == "blank" and fragment.data == {"item": None}
    assert doc.nodes["n2"]["text"] == ""
