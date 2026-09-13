"""Source-specific property collisions; no automatic field removal or renaming."""

import copy
import json

from test_document_protocol import execute, raw_document

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.native_structure_feedback import property_collisions


def field(ref, key="private label", **extra):
    return {"key": key, "sourceRefs": [ref], **extra}


def test_collision_reports_owned_positions_not_key_or_group_names_and_keeps_input():
    wire = {"fields": [field("n1"), field("n2"), field("n3", "other"), field("n4", "other")]}
    before = copy.deepcopy(wire)
    assert property_collisions(wire, {}) == [
        "duplicate_data_property",
        "native_structure_property_collision:1:field:1:sources=n1",
        "native_structure_property_collision:1:field:2:sources=n2",
        "native_structure_property_collision:2:field:3:sources=n3",
        "native_structure_property_collision:2:field:4:sources=n4",
    ]
    assert wire == before


def test_same_names_in_different_objects_do_not_collide():
    wire = {
        "groups": [{"id": "private-group", "key": "g", "label": "g", "sourceRefs": ["n1"]}],
        "fields": [field("n1"), field("n2", groupId="private-group")],
    }
    assert property_collisions(wire, {}) == ["duplicate_data_property"]


def test_record_field_group_and_explicit_target_collisions_follow_compiler_destinations():
    wire = {
        "groups": [{"id": "g", "key": "same", "sourceRefs": ["n1"]}],
        "fields": [field("n2", "same"), field("n3", "else", targetHandle="t1")],
        "records": [{"key": "same", "definitionRefs": ["n4"]}],
    }
    out = property_collisions(wire, {"t1": {"tokens": ["same"]}})
    assert len(out) == 5 and all(":1:" in v for v in out[1:])
    assert [v.split(":sources=")[0].split(":", 2)[2] for v in out[1:]] == [
        "group:1",
        "field:1",
        "field:2",
        "record:1",
    ]


def test_feedback_is_bounded_without_erasing_source_occurrences():
    wire = {"fields": [field(f"n{i}") for i in range(25)]}
    before = copy.deepcopy(wire)
    assert len(property_collisions(wire, {})) == 11
    assert wire == before


class CollisionModel:
    identity = {"model": "scripted-collision-repair", "configurationId": "1"}

    def __init__(self):
        self.calls = 0
        self.structures = 0
        self.feedback = None

    def infer(self, request):
        p = json.loads(request.messages[-1]["content"])
        self.calls += 1
        if p["documentStage"] == "roles":
            value = {
                "regionId": p["regionId"],
                "documentElements": [
                    {
                        "sourceRef": ref,
                        "role": "field_group",
                        "level": None,
                        "captionOf": None,
                        "status": "interpreted",
                    }
                    for ref in ["n1", "n2"]
                ],
            }
        elif p["documentStage"] == "structure":
            self.structures += 1
            if self.structures == 2:
                self.feedback = p["repairFeedback"]
            value = {
                "regionId": p["regionId"],
                "fields": [
                    {
                        "key": key if self.structures == 2 else "same",
                        "label": "same label",
                        "valueType": "string",
                        "status": "present",
                        "sourceRefs": [ref],
                    }
                    for key, ref in [("left", "n1"), ("right", "n2")]
                ],
            }
        else:
            assert p["documentStage"] == "values"
            value = {
                "regionId": p["regionId"],
                "selections": {
                    h: {
                        "kind": "quote",
                        "quote": {
                            "sourceRef": v["sourceRefs"][0],
                            "text": {"n1": "ALPHA", "n2": "BRAVO"}[v["sourceRefs"][0]],
                        },
                    }
                    for h, v in p["handles"].items()
                },
                "excludedBindings": [],
            }
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 3, "completion_tokens": 2})


def test_actual_native_parser_repair_sees_both_source_positions_and_preserves_values():
    model = CollisionModel()
    states = []
    result = execute(model, raw=raw_document(("ALPHA", "BRAVO")), states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {"left": "ALPHA", "right": "BRAVO"} and model.calls == 4
    assert model.feedback == [
        "duplicate_data_property",
        "native_structure_property_collision:1:field:1:sources=n1",
        "native_structure_property_collision:1:field:2:sources=n2",
    ]
    assert not any(i["code"] == "native_structure_invalid" for i in result["issues"])
    again = execute(model, raw=raw_document(("ALPHA", "BRAVO")), restore=states[-1])
    assert again["data"] == result["data"] and model.calls == 4
