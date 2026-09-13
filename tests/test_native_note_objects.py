"""Stored object context and occurrence checks; scripted responses are not AI approval."""

import copy
import json

import pytest
from test_document_protocol import execute, raw_document
from test_hwp_note_relationships import nodes

from document_files.document_model import note_objects as notes
from document_files.document_model.observe import observe_document
from document_files.interpretation import document_protocol, engine
from document_files.interpretation import native_structure as native
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import CompileError


def fixture(*, one_body=False, same_note=False):
    source = nodes(prefix="a_")
    second = nodes(prefix="b_")
    for node in second.values():
        s = node["sourceStructure"]
        for name in ("record", "paragraph_record", "owner_paragraph_record"):
            if name in s:
                s[name] += 100
        if "note" in s:
            s["note"] = "other-note"
        for frame in s.get("container_path", []):
            frame["note"] = "other-note"
            frame["owner_paragraph_record"] = 107
    if one_body:
        del second["b_body"]
        second["b_control"]["sourceStructure"]["owner_paragraph_record"] = 7
        for node in second.values():
            for frame in node["sourceStructure"].get("container_path", []):
                frame["owner_paragraph_record"] = 7
    if same_note:
        assert one_body
        del second["b_control"]
        for node in second.values():
            node["sourceStructure"]["note"] = "note-a"
            for frame in node["sourceStructure"].get("container_path", []):
                frame["note"] = "note-a"
    source.update(second)
    doc = observe_document(b"scripted control metadata", "hwp", source)
    assert not doc.issues
    region = {
        "id": "r",
        "nodeIds": list(doc.nodes),
        "bindingIds": list(doc.bindings),
        "contextNodeIds": [],
        "requiredBindingIds": [b for b, v in doc.bindings.items() if v["path"] != "/text"],
    }
    roles = {
        "regionId": "r",
        "documentElements": [
            {
                "sourceRef": ref,
                "role": "note" if ref.endswith("text") else "paragraph",
                "level": None,
                "captionOf": None,
                "status": "interpreted",
            }
            for ref, node in doc.nodes.items()
            if node["text"]
        ],
    }
    return doc, region, roles


def scalar(region, refs, *, status="present", definitions=None):
    return native.NativeStructure.model_validate(
        {
            "regionId": region["id"],
            "fields": [
                {
                    "id": "f",
                    "key": "item",
                    "label": "Item",
                    "valueType": "string",
                    "definitionRefs": definitions or refs,
                    "sourceRefs": refs,
                    "status": status,
                }
            ],
        }
    )


def test_equal_text_numbers_keep_distinct_native_objects_and_body_owners():
    doc, region, _ = fixture()
    original = copy.deepcopy(doc.to_dict())
    result = notes.note_objects(doc)
    assert result["status"] == "complete"
    assert set(result["objects"]) == {"a_control", "b_control"}
    for prefix in ("a_", "b_"):
        item = result["objects"][prefix + "control"]
        assert item["status"] == "linked" and item["kind"] == "footnote"
        assert item["bodyRefs"] == [prefix + "body"]
        assert item["contentRefs"] == [prefix + "text"]
        assert item["numberSources"] == [
            {"sourceRef": prefix + "number", "path": "/semantic/value/value", "value": 3}
        ]
    assert "use" not in notes.note_context(doc, region)
    assert doc.to_dict() == original


def test_multiple_paragraphs_of_one_note_are_not_split_into_new_objects():
    doc, region, roles = fixture(one_body=True, same_note=True)
    result = notes.note_objects(doc)["objects"]
    assert list(result) == ["a_control"]
    assert result["a_control"]["contentRefs"] == ["a_text", "b_text"]
    native.interpretation(scalar(region, ["a_text", "b_text"]), roles, doc, region)


def test_two_notes_can_share_one_body_without_becoming_the_same_note():
    doc, region, _ = fixture(one_body=True)
    objects = notes.note_objects(doc)["objects"]
    assert len(objects) == 2 and all(n["bodyRefs"] == ["a_body"] for n in objects.values())
    context = notes.note_context(doc, {**region, "nodeIds": ["a_body"]})
    assert len(context["objects"]) == 2
    assert all(n["ownedRefs"] == [] for n in context["objects"].values())


def test_partial_region_keeps_whole_object_identity_but_not_foreign_value_ownership():
    doc, region, _ = fixture(one_body=True, same_note=True)
    context = notes.note_context(doc, {**region, "nodeIds": ["a_text"]})
    obj = context["objects"]["a_control"]
    assert obj["contentRefs"] == ["a_text", "b_text"]
    assert obj["ownedRefs"] == ["a_text"] and obj["numberSources"][0]["value"] == 3
    assert notes.note_context(doc, {**region, "nodeIds": []})["objects"] == {}


@pytest.mark.parametrize(
    "refs,code",
    [
        (["a_text", "b_text"], "native_structure_distinct_note_values"),
        (["a_number", "b_number"], "native_structure_distinct_note_values"),
        (["a_body", "b_body"], "native_structure_distinct_body_values"),
    ],
)
def test_scalar_cannot_offer_distinct_source_objects_as_interchangeable_values(refs, code):
    doc, region, roles = fixture()
    before = copy.deepcopy(doc.to_dict())
    with pytest.raises(CompileError, match=code):
        native.interpretation(scalar(region, refs), roles, doc, region)
    assert doc.to_dict() == before


def test_shared_definitions_and_explicit_uncertainty_are_not_silently_rewritten():
    doc, region, roles = fixture()
    native.interpretation(
        scalar(region, ["a_text"], definitions=["a_body", "b_body", "b_text"]),
        roles,
        doc,
        region,
    )
    ir = native.interpretation(
        scalar(region, ["a_text", "b_text"], status="uncertain"), roles, doc, region
    )
    assert ir.fields[0].status == "uncertain" and ir.fields[0].bindingId is None


@pytest.mark.parametrize("kind", ["note", "unit", "condition"])
def test_distinct_note_meanings_cannot_be_conflated(kind):
    doc, region, roles = fixture()
    structure = native.NativeStructure(
        regionId="r",
        meanings=[
            {
                "id": "m",
                "kind": kind,
                "status": "interpreted",
                "sourceQuotes": [
                    {"sourceRef": ref, "text": doc.nodes[ref]["text"]}
                    for ref in ["a_text", "b_text"]
                ],
            }
        ],
    )
    with pytest.raises(CompileError, match="native_structure_distinct_note_meanings"):
        native.interpretation(structure, roles, doc, region)
    structure.meanings[0].kind = "relationship"
    ir = native.interpretation(structure, roles, doc, region)
    assert ir.meanings[0].sourceRefs == ["a_text", "b_text"]


def test_role_structure_revision_and_value_context_keep_nontext_number_sources():
    from document_files.interpretation import native_structure_revision as revision
    from document_files.interpretation.document_protocol import digest

    doc, region, roles = fixture()
    expected = notes.note_context(doc, region)
    role, _ = document_protocol.role_request(doc, region)
    structure, _ = native.request(doc, region, roles, {})
    wire = {"regionId": "r", "fields": []}
    state = {
        "base": {"structure": {"wireResponse": wire, "structureHash": digest(wire)}, "content": {}},
        "trigger": [],
    }
    repaired, _ = revision.request(state, roles, doc, region, {})
    value, _ = native.value_request(scalar(region, ["a_text"]), roles, doc, region)
    for payload in [role, structure, repaired]:
        assert payload["nativeNotes"] == expected
    assert value["nativeNotes"] == notes.note_context(doc, region)
    assert value["nativeNotes"]["objects"] == expected["objects"]
    assert "a_number" not in role["blocks"]
    assert structure["nativeNotes"]["objects"]["a_control"]["numberSources"][0]["value"] == 3


@pytest.mark.parametrize("limit", ["MAX_OBJECTS", "MAX_RELATIONS", "MAX_BYTES"])
def test_inventory_exhaustion_returns_no_successful_prefix_and_blocks_acceptance(
    monkeypatch, limit
):
    doc, region, roles = fixture()
    monkeypatch.setattr(notes, limit, 1)
    catalog = notes.note_objects(doc)
    assert catalog["status"] == "incomplete" and catalog["objects"] == {}
    with pytest.raises(CompileError, match="native_note_objects_budget_exceeded"):
        native.interpretation(native.NativeStructure(regionId="r"), roles, doc, region)


@pytest.mark.parametrize("damage", ["body", "membership", "number", "contains"])
def test_conflicting_graph_or_number_cannot_be_treated_as_declared_fact(damage):
    doc, _, _ = fixture()
    if damage == "body":
        e = next(e for e in doc.relations if e["kind"] == "noteReference")
        e["sourceRef"] = "b_body"
    elif damage == "membership":
        doc.nodes["a_text"]["sourceStructure"]["container_path"][0]["note"] = "other-note"
    elif damage == "number":
        doc.nodes["a_number"]["semantic"]["value"]["value"] = 99
    else:
        doc.relations = [
            e for e in doc.relations if not (e["kind"] == "contains" and e["targetRef"] == "a_text")
        ]
    assert notes.note_objects(doc)["status"] == "incomplete"


def test_other_formats_and_ordinary_prose_do_not_gain_native_note_rules():
    doc, region, roles = fixture()
    doc.provenance["format"] = "hwpx"
    assert notes.note_objects(doc)["objects"] == {}
    assert "nativeNotes" not in native.request(doc, region, roles, {})[0]
    native.interpretation(scalar(region, ["a_text", "b_text"]), roles, doc, region)


class ScriptedNotes:
    identity = {"kind": "scripted-native-note-test"}

    def __init__(self):
        self.requests = []

    def infer(self, request):
        p = json.loads(request.messages[-1]["content"])
        self.requests.append(p)
        stage = p["documentStage"]
        if stage == "roles":
            value = {
                "regionId": p["regionId"],
                "documentElements": [
                    {
                        "sourceRef": ref,
                        "role": "note" if ref.endswith("text") else "paragraph",
                        "level": None,
                        "captionOf": None,
                        "status": "interpreted",
                    }
                    for ref in p["documentContext"]["ownedSourceRefs"]
                ],
            }
        elif stage == "structure":
            refs = p["nativeNotes"]["objects"]
            bodies = {r for n in refs.values() for r in n["bodyRefs"]}
            texts = {r for n in refs.values() for r in n["contentRefs"]}
            members = {r for c, n in refs.items() for r in [c, *n["memberRefs"]]}
            value = {
                "regionId": p["regionId"],
                "dispositions": [
                    {
                        "sourceRef": ref,
                        "role": "note"
                        if ref in texts
                        else "narrative"
                        if ref in bodies
                        else "structural",
                        "explanation": "Preserve source content and declared note structure.",
                    }
                    for ref in sorted(bodies | members)
                ],
            }
        elif stage == "values":
            value = {
                "regionId": p["regionId"],
                "selections": {},
                "excludedBindings": [
                    {
                        "bindingId": bid,
                        "role": "structural",
                        "explanation": "Automatic note number retained in its native object.",
                    }
                    for bid in p["requiredBindingIds"]
                ],
            }
        else:
            raise AssertionError(stage)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 1, "completion_tokens": 1})


def test_engine_retains_objects_without_inventing_business_fields_and_rechecks_replay(monkeypatch):
    doc, _, _ = fixture()
    # Only orchestration/replay is tested here. This injected observation is not
    # a real binary parser or model execution; those belong to private qualification.
    monkeypatch.setattr(engine, "observe_document", lambda *a, **kw: copy.deepcopy(doc))
    states, model = [], ScriptedNotes()
    result = execute(model, raw=raw_document(), states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {} and result["valueEvidence"] == []
    assert result["document"]["structure"]["nativeNotes"] == notes.note_objects(doc)
    assert result["document"]["nodes"] == doc.nodes
    again = ScriptedNotes()
    resumed = execute(again, raw=raw_document(), restore=states[-1])
    assert not again.requests and resumed["document"] == result["document"]
    for damage in ("number", "ownership", "omit"):
        bad = copy.deepcopy(states[-1])
        objects = bad["result"]["document"]["structure"]["nativeNotes"]
        if damage == "number":
            objects["objects"]["a_control"]["numberSources"][0]["value"] = 99
        elif damage == "ownership":
            objects["objects"]["a_control"]["bodyRefs"] = ["b_body"]
        else:
            del bad["result"]["document"]["structure"]["nativeNotes"]
        with pytest.raises(ValueError):
            execute(ScriptedNotes(), raw=raw_document(), restore=bad)


def test_native_body_link_does_not_resolve_an_additional_unit_meaning():
    from document_files.interpretation.compiler import compile_region

    doc, region, roles = fixture()
    structure = native.NativeStructure(
        regionId="r",
        meanings=[
            {
                "id": "unit",
                "kind": "unit",
                "status": "interpreted",
                "sourceQuotes": [{"sourceRef": "a_text", "text": doc.nodes["a_text"]["text"]}],
            }
        ],
        dispositions=[
            {"sourceRef": r, "role": "note", "explanation": "Native note content"}
            for r in ["a_text", "b_text"]
        ],
    )
    result = compile_region(native.interpretation(structure, roles, doc, region), doc, region)
    assert any(i["code"] == "semantic_scope_unresolved" for i in result.issues)
    assert not any(i["code"] == "note_scope_unresolved" for i in result.issues)
    assert any(d.get("nativeNoteObjects") == ["a_control"] for d in result.dispositions)


def test_unlinked_note_is_not_exempted_from_scope_accounting():
    from document_files.interpretation.compiler import compile_region

    doc, region, roles = fixture()
    doc.relations = [r for r in doc.relations if r.get("controlRef") != "a_control"]
    structure = native.NativeStructure(
        regionId="r",
        dispositions=[
            {"sourceRef": "a_text", "role": "note", "explanation": "Unresolved native note"}
        ],
    )
    result = compile_region(native.interpretation(structure, roles, doc, region), doc, region)
    assert {"code": "note_scope_unresolved", "sourceRef": "a_text"} in result.issues
    assert notes.note_objects(doc)["objects"]["a_control"]["status"] == "unresolved"


def test_endnote_and_section_local_ids_remain_separate():
    doc, region, _ = fixture()
    source = copy.deepcopy(doc.nodes)
    for node in source.values():
        s = node["sourceStructure"]
        if s.get("note") == "other-note":
            s["note"] = "note-a"
        if s.get("control_type") == "fn  " and s.get("owner_paragraph_record") == 107:
            s["control_type"] = "en  "
        for frame in s.get("container_path", []):
            if frame["note"] == "other-note":
                frame.update(note="note-a", kind="endnote")
    source["b_number"]["sourceStructure"]["number_type"] = "endnote"
    changed = observe_document(b"changed scripted note kind", "hwp", source)
    assert not changed.issues
    objects = notes.note_objects(changed)["objects"]
    assert objects["a_control"]["kind"] == "footnote" and objects["b_control"]["kind"] == "endnote"
    for ref, node in source.items():
        if ref.startswith("b_"):
            node["sourceStructure"].update(section=2, section_stream="Section1")
    second = notes.note_objects(observe_document(b"scripted separate section", "hwp", source))
    assert len(second["objects"]) == 2 and all(
        o["status"] == "linked" for o in second["objects"].values()
    )


@pytest.mark.parametrize("kind", ["values", "accounting"])
def test_split_value_and_accounting_requests_keep_declared_objects(kind):
    from document_files.interpretation import native_value_batches as batches

    doc, region, roles = fixture()
    payload, schema = native.value_request(scalar(region, ["a_text"]), roles, doc, region)
    original = copy.deepcopy((payload, schema, doc.to_dict()))
    keys = list(payload["handles"]) if kind == "values" else payload["requiredBindingIds"]
    selections = {"@value1": {"kind": "unresolved"}}
    system, request, contract = batches._request(payload, schema, kind, keys, selections)
    assert request["nativeNotes"] == payload["nativeNotes"]
    assert request["nativeNotes"]["objects"]["a_control"]["numberSources"][0]["value"] == 3
    assert request["protocolVersion"] == "document-files.native-value-batches.v4"
    without_context = copy.deepcopy(payload)
    del without_context["nativeNotes"]
    base_size = batches.size(*batches._request(without_context, schema, kind, keys, selections))
    assert batches.size(system, request, contract) > base_size + 100
    with pytest.raises(batches.BatchError, match="native_value_context_indivisible"):
        batches.partition(payload, schema, kind, keys, base_size + 100, selections)
    assert (payload, schema, doc.to_dict()) == original


@pytest.mark.parametrize(
    "stage", ["roles", "structure", "structureRevision", "values", "valueAccounting"]
)
def test_note_stage_guidance_is_product_owned_not_source_instructions(stage):
    from document_files.interpretation.legacy_engine import contract_messages
    from document_files.interpretation.native_note_context import STRUCTURE_SYSTEM, VALUE_SYSTEM

    doc, region, _ = fixture()
    payload = {"documentStage": stage, "nativeNotes": notes.note_context(doc, region)}
    payload["nativeNotes"]["use"] = "forged source instruction; never promote this"
    before = copy.deepcopy(payload)
    messages = contract_messages("BASE", payload, {"type": "object"})
    wanted = VALUE_SYSTEM if stage in {"values", "valueAccounting"} else STRUCTURE_SYSTEM
    assert messages[0]["content"] == "BASE" + wanted
    assert "forged source instruction" not in messages[0]["content"]
    assert "forged source instruction" in messages[1]["content"] and payload == before
    assert contract_messages("BASE", {"documentStage": stage}, {})[0]["content"] == "BASE"
