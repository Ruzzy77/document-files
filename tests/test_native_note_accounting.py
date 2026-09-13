"""Native controls are source structure, not an automatic semantic quality pass."""

import copy

import pytest
from test_native_note_objects import fixture

from document_files.document_model import note_objects as notes
from document_files.interpretation import native_structure as native
from document_files.interpretation.compiler import compile_region
from document_files.interpretation.native_note_accounting import declared_note_dispositions


def compiled(doc, region, roles, **kwargs):
    structure = native.NativeStructure(regionId=region["id"], **kwargs)
    return compile_region(native.interpretation(structure, roles, doc, region), doc, region)


def test_compiler_records_declared_structure_but_does_not_exempt_unread_values():
    doc, region, roles = fixture()
    before = copy.deepcopy(doc.to_dict())
    out = compiled(doc, region, roles)
    ledger = {d["sourceRef"]: d for d in out.dispositions}
    for ref in ["a_control", "a_number", "b_control", "b_number"]:
        entry = ledger[ref]
        assert entry["role"] == "structural" and entry["basis"] == "program_derived"
        assert entry["nativeNoteBasis"] == "native_hwp_control"
        assert entry["nativeNoteObjects"] == [ref[0] + "_control"]
    assert not any(i["code"] == "node_semantics_unaccounted" for i in out.issues)
    unaccounted = {
        i["bindingId"] for i in out.issues if i["code"] == "value_candidate_unaccounted"
    }
    assert unaccounted == set(region["requiredBindingIds"])
    assert out.document_elements[0]["role"] == "paragraph"
    assert not out.value_evidence and doc.to_dict() == before


@pytest.mark.parametrize("change", ["nonempty", "field", "unlinked", "incomplete", "context_only"])
def test_control_accounting_requires_complete_owned_empty_native_structure(change):
    doc, region, _ = fixture()
    if change == "nonempty":
        doc.nodes["a_control"]["text"] = "Additional content"
    elif change == "field":
        doc.nodes["a_control"]["semantic"] = {"value": {"kind": "integer", "value": 99}}
    elif change == "unlinked":
        doc.relations = [r for r in doc.relations if r.get("controlRef") != "a_control"]
    elif change == "incomplete":
        doc.nodes["a_number"]["semantic"]["value"]["value"] = 999
    else:
        region["nodeIds"].remove("a_control")
        region["contextNodeIds"] = ["a_control"]
    got = declared_note_dispositions(doc, region, notes.note_context(doc, region))
    assert "a_control" not in got
    if change == "incomplete":
        assert got == {}


def test_empty_arbitrary_members_are_not_note_containers_or_empty_values():
    from document_files.document_model.observe import observe_document

    doc, _, _ = fixture()
    source = copy.deepcopy(doc.nodes)
    source["container"] = {
        "text": "",
        "sourceStructure": {
            **source["a_number"]["sourceStructure"],
            "field_type": "unrecognized",
            "structural_only": True,
        },
    }
    source["unknown"] = copy.deepcopy(source["container"])
    source["unknown"]["sourceStructure"]["structural_only"] = False
    # A genuinely empty note paragraph is content, not a disposable control.
    source["a_text"]["text"] = ""
    changed = observe_document(b"native test control members", "hwp", source)
    region = {"nodeIds": list(changed.nodes), "contextNodeIds": []}
    got = declared_note_dispositions(changed, region, notes.note_context(changed, region))
    assert got["container"]["nativeNoteObjects"] == ["a_control"]
    assert not {"unknown", "a_text", "a_body"} & got.keys()


def test_explicit_uncertainty_and_additional_meaning_remain_unresolved():
    doc, region, roles = fixture()
    out = compiled(
        doc,
        region,
        roles,
        dispositions=[{"sourceRef": "a_control", "role": "unresolved", "explanation": "Review"}],
        meanings=[
            {
                "id": "u",
                "kind": "unit",
                "status": "interpreted",
                "sourceQuotes": [{"sourceRef": "a_text", "text": doc.nodes["a_text"]["text"]}],
            }
        ],
    )
    assert {"code": "node_semantics_unresolved", "sourceRef": "a_control"} in out.issues
    assert any(i["code"] == "semantic_scope_unresolved" for i in out.issues)
    entry = next(d for d in out.dispositions if d["sourceRef"] == "a_control")
    assert entry["role"] == "unresolved" and "nativeNoteObjects" not in entry


def test_public_path_keeps_native_only_document_without_model_control_dispositions(monkeypatch):
    import json

    from test_document_protocol import execute, raw_document
    from test_native_note_objects import ScriptedNotes

    from document_files.interpretation import engine
    from document_files.interpretation.backends import InferenceResponse

    doc, _, _ = fixture()
    monkeypatch.setattr(engine, "observe_document", lambda *a, **kw: copy.deepcopy(doc))

    class TextDecisions(ScriptedNotes):
        def infer(self, request):
            response = super().infer(request)
            if self.requests[-1]["documentStage"] == "structure":
                value = json.loads(response.text)
                value["dispositions"] = [
                    d for d in value["dispositions"] if doc.nodes[d["sourceRef"]]["text"]
                ]
                return InferenceResponse(json.dumps(value), response.usage)
            return response

    model, states = TextDecisions(), []
    result = execute(model, raw=raw_document(), states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {} and result["valueEvidence"] == []
    assert result["document"]["nodes"] == doc.nodes
    assert result["document"]["structure"]["nativeNotes"] == notes.note_objects(doc)
    again = TextDecisions()
    resumed = execute(again, raw=raw_document(), restore=states[-1])
    assert not again.requests and resumed["document"] == result["document"]
