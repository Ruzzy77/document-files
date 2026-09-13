"""Stored HWP controls, not text guesses or upstream fixture data."""

import copy

import pytest

from document_files.document_model import hwp_notes
from document_files.document_model.observe import observe_document


def nodes(section=1, stream="Section0", prefix=""):
    location = {"section": section, "section_stream": stream}
    result = {
        "body": {
            "text": "A paragraph with a note reference.",
            "semanticRole": "paragraph",
            "sourceStructure": {**location, "paragraph_record": 7, "record": 8},
        },
        "control": {
            "text": "",
            "semanticRole": "note",
            "sourceStructure": {
                **location,
                "control_type": "fn  ",
                "note": "note-a",
                "owner_paragraph_record": 7,
                "record": 20,
            },
        },
    }
    frame = {"kind": "footnote", "note": "note-a", "owner_paragraph_record": 7}
    result["text"] = {
        "text": "Note content.",
        "semanticRole": "note",
        "sourceStructure": {
            **location,
            "note": "note-a",
            "container_path": [frame],
            "owner_paragraph_record": 7,
            "paragraph_record": 24,
            "record": 25,
        },
    }
    result["number"] = {
        "text": "",
        "semanticRole": "field",
        "sourceStructure": {
            **location,
            "container_path": [frame],
            "note": "note-a",
            "owner_paragraph_record": 24,
            "record": 27,
            "field_type": "auto_number",
            "number_type": "footnote",
            "number_origin": "stored_control_value",
            "stored_number": 3,
        },
        "semantic": {"value": {"kind": "integer", "value": 3, "basis": "stored_control_value"}},
    }
    return {prefix + key: value for key, value in result.items()}


def observe(source, format_id="hwp"):
    original = copy.deepcopy(source)
    doc = observe_document(b"scripted-native-metadata", format_id, source)
    assert doc.nodes == source == original
    return doc, [r for r in doc.relations if r["kind"] == "noteReference"]


def test_control_owns_the_anchor_not_the_number_fields_parent():
    source = nodes()
    doc, refs = observe(source)
    assert not doc.issues
    assert refs == [
        {
            "kind": "noteReference",
            "sourceRef": "body",
            "targetRef": "text",
            "basis": "native_hwp_control",
            "noteKind": "footnote",
            "nativeNoteId": "note-a",
            "controlRef": "control",
            "anchorGranularity": "paragraph",
            "numberSourceRefs": ["number"],
        }
    ]
    contains = {(r["sourceRef"], r["targetRef"]) for r in doc.relations if r["kind"] == "contains"}
    assert contains == {("body", "control"), ("control", "text"), ("control", "number")}
    assert not any("start" in r or "end" in r for r in refs)  # No invented inline anchor.
    assert "text" in next(r for r in doc.regions if r["nodeIds"] == ["body"])["contextNodeIds"]
    before = copy.deepcopy(doc.to_dict())
    hwp_notes.add_hwp_note_relationships(doc, source)
    assert doc.to_dict() == before  # Repeated observation does not duplicate native edges.


@pytest.mark.parametrize("section,stream", [(2, "Section1"), (1, "Section1"), (2, "Section0")])
def test_reused_native_record_and_note_ids_never_cross_section_streams(section, stream):
    source = nodes() | nodes(section, stream, "second-")
    doc, refs = observe(source)
    assert not doc.issues
    assert {(r["sourceRef"], r["targetRef"]) for r in refs} == {
        ("body", "text"),
        ("second-body", "second-text"),
    }


def test_equal_footnote_and_endnote_ids_are_distinct_controls():
    second = nodes(prefix="end-")
    second["end-body"]["sourceStructure"]["paragraph_record"] = 50
    second["end-control"]["sourceStructure"].update(
        control_type="en  ", owner_paragraph_record=50, record=60
    )
    for name in ["end-text", "end-number"]:
        second[name]["sourceStructure"]["container_path"][0]["kind"] = "endnote"
        second[name]["sourceStructure"]["container_path"][0]["owner_paragraph_record"] = 50
    second["end-number"]["sourceStructure"]["number_type"] = "endnote"
    doc, refs = observe(nodes() | second)
    assert not doc.issues and {r["noteKind"] for r in refs} == {"footnote", "endnote"}
    assert {(r["sourceRef"], r["targetRef"]) for r in refs} == {
        ("body", "text"),
        ("end-body", "end-text"),
    }


@pytest.mark.parametrize("missing", ["body", "text", "control"])
def test_missing_native_ownership_is_explicit_not_guessed_from_nearby_text(missing):
    source = nodes()
    del source[missing]
    doc, refs = observe(source)
    assert not refs and doc.coverage["status"] == "partial"
    expected = {
        "body": "owner_unavailable",
        "text": "content_unavailable",
        "control": "control_missing",
    }
    assert any(i["code"] == "native_hwp_note_" + expected[missing] for i in doc.issues)


def test_duplicate_control_identity_does_not_choose_one_arbitrarily():
    source = nodes()
    source["other-control"] = copy.deepcopy(source["control"])
    doc, refs = observe(source)
    assert not refs and {i["code"] for i in doc.issues} == {"native_hwp_note_control_ambiguous"}


def test_disagreeing_container_and_control_ownership_is_not_silently_resolved():
    source = nodes()
    source["text"]["sourceStructure"]["container_path"][0]["owner_paragraph_record"] = 99
    doc, refs = observe(source)
    assert not refs and any(i["code"] == "native_hwp_note_membership_conflict" for i in doc.issues)


@pytest.mark.parametrize("target", ["control", "text", "body"])
def test_incomplete_section_metadata_cannot_link_another_stream(target):
    source = nodes() | nodes(2, "Section1", "second-")
    del source[target]["sourceStructure"]["section_stream"]
    doc, refs = observe(source)
    assert doc.issues and {(r["sourceRef"], r["targetRef"]) for r in refs} == {
        ("second-body", "second-text")
    }


def test_split_paragraph_and_blank_note_text_are_not_discarded():
    source = nodes()
    source["body-segment"] = copy.deepcopy(source["body"])
    source["body-segment"]["sourceStructure"]["record"] = 9
    source["body-segment"]["text"] = "Another exact segment of the same native paragraph."
    source["text"]["text"] = ""
    _, refs = observe(source)
    assert {(r["sourceRef"], r["targetRef"]) for r in refs} == {
        ("body", "text"),
        ("body-segment", "text"),
    }


def test_nested_note_uses_its_nearest_container_without_cross_linking_numbers():
    source = nodes()
    nested = nodes(prefix="inner-")
    del nested["inner-body"]
    nested["inner-control"]["sourceStructure"].update(
        note="inner", record=30, owner_paragraph_record=24
    )
    outer = copy.deepcopy(source["text"]["sourceStructure"]["container_path"][0])
    nested["inner-control"]["sourceStructure"]["container_path"] = [outer]
    for name in ["inner-text", "inner-number"]:
        s = nested[name]["sourceStructure"]
        s["note"] = "inner"
        s["container_path"] = [
            outer,
            {"kind": "footnote", "note": "inner", "owner_paragraph_record": 24},
        ]
    nested["inner-text"]["sourceStructure"].update(paragraph_record=34, record=35)
    nested["inner-number"]["sourceStructure"].update(owner_paragraph_record=34, record=37)
    doc, refs = observe(source | nested)
    assert not doc.issues
    assert {(r["sourceRef"], r["targetRef"]) for r in refs} == {
        ("body", "text"),
        ("text", "inner-text"),
    }
    assert {r["targetRef"]: r["numberSourceRefs"] for r in refs} == {
        "text": ["number"],
        "inner-text": ["inner-number"],
    }


@pytest.mark.parametrize(
    "key,value",
    [("number_origin", "inferred"), ("stored_number", True), ("number_type", "endnote")],
)
def test_only_matching_stored_number_controls_are_linked(key, value):
    source = nodes()
    source["number"]["sourceStructure"][key] = value
    _, refs = observe(source)
    assert refs[0]["numberSourceRefs"] == []


def test_note_relation_budget_reports_partial_without_rewriting_original_nodes(monkeypatch):
    monkeypatch.setattr(hwp_notes, "MAX_NOTE_RELATIONS", 1)
    doc, refs = observe(nodes())
    assert not refs and doc.coverage["status"] == "partial"
    assert {i["code"] for i in doc.issues} == {
        "native_hwp_note_relation_budget_exceeded",
        "observation_budget_exceeded",
    }


def test_hwp_record_metadata_is_not_applied_to_other_format_names():
    doc, refs = observe(nodes(), "hwpx")
    assert not refs and not any(r["basis"] == "native_hwp_control" for r in doc.relations)
