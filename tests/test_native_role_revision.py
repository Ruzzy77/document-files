"""Actual native parser and controller with scripted reviews; not model quality."""

import copy
import json

import pytest
from test_document_protocol import execute, raw_document
from test_native_structure_revision import saved_region

from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.document_protocol import digest
from document_files.interpretation.native_structure_history import expand

RAW = raw_document(("REF-007",))


class JointModel:
    identity = {"model": "scripted-role-revision", "configurationId": "1"}

    def __init__(self, mode="roles"):
        self.mode = mode
        self.calls = 0
        self.stages = []
        self.requests = []

    def infer(self, request):
        p = json.loads(request.messages[-1]["content"])
        stage = p["documentStage"]
        self.calls += 1
        self.stages.append(stage)
        self.requests.append(p)
        role = dict(sourceRef="n1", role="title", level=0, captionOf=None, status="interpreted")
        wire = {
            "regionId": p["regionId"],
            "fields": [
                {
                    "key": "reference",
                    "label": "Reference",
                    "valueType": "string",
                    "sourceRefs": ["n1"],
                    "status": "present",
                }
            ],
            "dispositions": [{"sourceRef": "n1", "role": "data", "explanation": "Reference"}],
        }
        if stage == "roles":
            value = {"regionId": p["regionId"], "documentElements": [role]}
        elif stage == "structure":
            value = wire
        elif stage == "structureRevision":
            if "retainedReviewHash" in p:
                assert p["roleSourceReview"] == []
                assert p["failureCodes"] == ["document_role_value_conflict"]
            else:
                assert p["roleSourceReview"] == [
                    {"valueHandle": "@value1", "sourceRoles": {"n1": "title"}}
                ]
            assert expand(p["acceptedRoles"]) == [role]
            if self.mode == "transport":
                raise ModelError("ai_test_joint_transport")
            value = {"baseStructureHash": p["baseStructureHash"], "reason": "Review exact source"}
            if self.mode in {"retain", "inner"}:
                value["decision"] = "retain"
            else:
                value.update(
                    decision="replace",
                    replacement=copy.deepcopy(expand(p["previousStructure"])),
                    documentElements=[{**role, "role": "field_group", "level": None}],
                    changes=[
                        {
                            "action": "keep",
                            "before": ["field:1"],
                            "after": ["field:1"],
                            "anchors": ["n1"],
                            "reason": "Same attribute and original source",
                        },
                        {
                            "action": "replace",
                            "before": ["role:1"],
                            "after": ["role:1"],
                            "anchors": ["n1"],
                            "reason": "This source contains a reference attribute",
                        },
                    ],
                )
                if self.mode == "ledger":
                    value["changes"].pop()
                elif self.mode == "missing_role":
                    value["documentElements"] = []
                elif self.mode == "foreign_role":
                    value["documentElements"][0]["sourceRef"] = "n99"
                elif self.mode == "bad_level":
                    value["documentElements"][0]["level"] = 2
                elif self.mode == "bad_anchor":
                    value["changes"][1]["anchors"] = [{"sourceRef": "n1", "text": "invented"}]
                elif self.mode == "structure_only":
                    value.pop("documentElements")
                    value["replacement"] = {
                        "regionId": p["regionId"],
                        "fields": [],
                        "dispositions": [
                            {"sourceRef": "n1", "role": "heading", "explanation": "Pure title"}
                        ],
                    }
                    value["changes"] = [
                        {
                            "action": "remove",
                            "before": ["field:1"],
                            "after": [],
                            "anchors": ["n1"],
                            "reason": "Pure title, not an attribute",
                        }
                    ]
        elif stage == "values":
            value = {
                "regionId": p["regionId"],
                "selections": {
                    h: {
                        "kind": "quote",
                        "quote": {
                            "sourceRef": "n1",
                            "text": "007" if self.mode == "inner" else "REF-007",
                        },
                    }
                    for h in p["handles"]
                },
                "excludedBindings": [],
            }
        else:
            raise AssertionError(stage)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 3, "completion_tokens": 2})


def run(model, **kwargs):
    return execute(model, raw=RAW, **kwargs)


def test_joint_role_only_change_precedes_values_and_rebuilds_outline_atomically():
    model, states = JointModel(), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert model.stages == ["roles", "structure", "structureRevision", "values"]
    assert result["data"] == {"reference": "REF-007"}
    assert result["document"]["outline"]["elements"][0]["role"] == "field_group"
    state = saved_region(states[-1])
    assert state["response"]["documentElements"][0]["role"] == "title"
    assert state["revision"]["base"]["content"]["usage"]["modelCalls"] == 0
    assert (
        state["revision"]["response"]["replacement"]
        == state["revision"]["base"]["structure"]["wireResponse"]
    )
    assert any(
        s["result"]["data"] == {"reference": None}
        and saved_region(s).get("revision", {}).get("decision") == "replace"
        for s in states
    )
    again = run(model, restore=states[-1])
    assert again["data"] == result["data"] and model.calls == 4
    assert again["document"]["outline"] == result["document"]["outline"]


@pytest.mark.parametrize(
    "mode", ["ledger", "missing_role", "foreign_role", "bad_level", "bad_anchor"]
)
def test_invalid_joint_replacement_keeps_original_roles_structure_and_no_values(mode):
    model, states = JointModel(mode), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial" and model.calls == 4
    assert "values" not in model.stages
    assert out["data"] == {"reference": None}
    assert out["document"]["outline"]["elements"][0]["role"] == "title"
    state = saved_region(states[-1])
    assert state["structure"] == state["revision"]["base"]["structure"]
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 4


@pytest.mark.parametrize("mode,expected", [("inner", {"reference": "007"}), ("structure_only", {})])
def test_review_can_preserve_genuine_title_or_remove_an_invented_field(mode, expected):
    model, states = JointModel(mode), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "complete", out["issues"]
    assert out["data"] == expected
    assert out["document"]["outline"]["elements"][0]["role"] == "title"
    assert run(model, restore=states[-1])["data"] == expected


@pytest.mark.parametrize("budget", [2, 3])
def test_pause_and_explicit_resume_preserve_original_and_revised_roles(budget):
    model, states = JointModel(), []
    out = run(model, states=states, budget=budget)
    assert out["extraction"]["status"] == "partial" and model.calls == budget
    assert run(model, restore=states[-1], budget=budget)["data"] == out["data"]
    assert model.calls == budget
    final = run(model, restore=states[-1], budget=budget, grant={"maxModelCalls": 4 - budget})
    assert final["extraction"]["status"] == "complete" and model.calls == 4
    assert final["data"] == {"reference": "REF-007"}


@pytest.mark.parametrize("damage", ["original_role", "revised_role", "role_ledger", "overlap"])
def test_checkpoint_rechecks_original_roles_role_change_coverage_and_source_review(damage):
    model, states = JointModel(), []
    run(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    state = saved_region(checkpoint)
    record = state["revision"]
    if damage == "original_role":
        state["response"]["documentElements"][0].update(role="paragraph", level=None)
    elif damage == "revised_role":
        record["response"]["documentElements"][0]["role"] = "paragraph"
        record["responseHash"] = digest(record["response"])
    elif damage == "role_ledger":
        record["response"]["changes"].pop()
        record["responseHash"] = digest(record["response"])
    else:
        record["base"]["content"]["roleSourceReview"][0]["sourceRoles"] = {}
    with pytest.raises(ValueError, match="checkpoint"):
        run(model, restore=checkpoint)
    assert model.calls == 4


def test_unknown_joint_exchange_requires_explicit_grant_and_keeps_no_false_values():
    model, states = JointModel("transport"), []
    out = run(model, states=states)
    assert model.calls == 3 and out["data"] == {"reference": None}
    model.mode = "roles"
    assert run(model, restore=states[-1])["data"] == out["data"] and model.calls == 3
    final = run(model, restore=states[-1], grant={"maxModelCalls": 2})
    assert final["data"] == {"reference": "REF-007"} and model.calls == 5


def test_overlap_review_is_bounded_and_does_not_infer_fields_from_native_note_ownership():
    from test_native_structure import prepared

    from document_files.interpretation.native_role_review import overlaps

    doc, region, roles, structure, _ = prepared()
    original = copy.deepcopy((doc, region, structure))
    roles["documentElements"][0].update(role="title", level=0)
    review = overlaps(structure, roles, doc, region)
    assert review == [{"valueHandle": "@value1", "sourceRoles": {"meta": "title"}}]
    assert "Reference" not in json.dumps(review) and "0007" not in json.dumps(review)
    # An owned actual inner binding can coexist with the heading without this review.
    bid = doc.bind("meta", start=13, end=17, candidateRole="value")
    region["bindingIds"].append(bid)
    assert overlaps(structure, roles, doc, region) == []
    assert structure == original[2]


@pytest.mark.parametrize("state", ["absent", "uncertain", "unreadable"])
def test_missing_state_does_not_trigger_early_review_as_if_a_value_were_read(state):
    from test_native_structure import prepared

    from document_files.interpretation.native_role_review import overlaps

    doc, region, roles, structure, _ = prepared()
    roles["documentElements"][0].update(role="title", level=0)
    structure.fields[0].status = state
    assert overlaps(structure, roles, doc, region) == []


def test_large_overlap_set_is_not_silently_truncated():
    from test_native_structure import prepared

    from document_files.interpretation.native_role_review import overlaps

    doc, region, roles, structure, _ = prepared()
    roles["documentElements"][0].update(role="title", level=0)
    structure.fields = [structure.fields[0].model_copy(update={"id": f"f{i}"}) for i in range(65)]
    with pytest.raises(ValueError, match="native_role_review_budget_exceeded"):
        overlaps(structure, roles, doc, region)


def test_retaining_a_role_does_not_approve_conflicting_whole_text_value():
    model, states = JointModel("retain"), []
    out = run(model, states=states)
    assert out["extraction"]["status"] == "partial"
    assert out["data"] == {"reference": None}
    assert model.stages.count("structureRevision") == 2
    assert any("document_role_value_conflict" in i.get("errors", []) for i in out["issues"])
    assert run(model, restore=states[-1])["data"] == out["data"]


def test_role_change_cannot_silently_stale_a_later_saved_heading_context():
    from test_native_structure import prepared

    from document_files.interpretation.document_protocol import (
        ROLE_SYSTEM,
        accept_roles,
        role_request,
    )
    from document_files.interpretation.native_role_review import preserves_role_contexts

    doc, _, _, _, _ = prepared()
    first = {"id": "first", "nodeIds": ["meta"], "bindingIds": []}
    later = {"id": "later", "nodeIds": ["a", "b", "note"], "bindingIds": list(doc.bindings)}
    decision = {
        "regionId": "first",
        "documentElements": [
            dict(sourceRef="meta", role="title", level=0, captionOf=None, status="interpreted")
        ],
    }
    _, before = accept_roles(decision, doc, first)
    states = {"later": {"requestHash": digest([ROLE_SYSTEM, *role_request(doc, later, [before])])}}
    assert preserves_role_contexts(doc, [first, later], "first", states, {"first": before})
    decision["documentElements"][0].update(role="field_group", level=None)
    _, after = accept_roles(decision, doc, first)
    assert not preserves_role_contexts(doc, [first, later], "first", states, {"first": after})
    assert preserves_role_contexts(doc, [first, later], "first", {}, {"first": after})


def test_note_review_instructions_are_stage_specific_without_changing_native_source_context():
    from document_files.document_model.note_objects import VERSION
    from document_files.interpretation.native_note_context import (
        REVISION_SYSTEM,
        ROLE_SYSTEM,
        STRUCTURE_SYSTEM,
        VALUE_SYSTEM,
        system_for,
    )

    notes = {"version": VERSION, "status": "complete", "objects": {}}
    for stage, expected in [
        ("roles", ROLE_SYSTEM),
        ("structureRevision", REVISION_SYSTEM),
        ("structure", STRUCTURE_SYSTEM),
        ("values", VALUE_SYSTEM),
    ]:
        payload = {"nativeNotes": notes, "documentStage": stage}
        before = copy.deepcopy(payload)
        assert system_for(payload) == expected and payload == before
