"""Text anchor eligibility never removes native typed value sources."""

import copy

import pytest
from jsonschema import Draft202012Validator
from test_native_note_objects import fixture
from test_native_occurrence_contract import field

from document_files.document_model.model import ObservationDocument
from document_files.interpretation import native_structure as native
from document_files.interpretation import native_structure_revision as revision
from document_files.interpretation import native_structure_wire as wire
from document_files.interpretation.document_protocol import digest
from document_files.interpretation.table_sources import source_inventory


@pytest.mark.parametrize("ref", ["a_control", "a_number"])
@pytest.mark.parametrize("kind", ["note", "unit", "condition", "reference", "relationship"])
def test_empty_controls_and_numbers_are_not_text_anchors_but_keep_other_roles(ref, kind):
    doc, region, roles = fixture()
    before = copy.deepcopy(doc.to_dict())
    _, schema = native.request(doc, region, roles, {})
    validator = Draft202012Validator(schema)
    Draft202012Validator.check_schema(schema)
    for anchor in [ref, {"sourceRef": ref, "text": "Invented control description"}]:
        assert not validator.is_valid(
            {
                "regionId": "r",
                "meanings": [{"kind": kind, "anchors": [anchor], "status": "interpreted"}],
            }
        )
    assert validator.is_valid(
        {
            "regionId": "r",
            "fields": [field([ref])],
            "dispositions": [
                {
                    "sourceRef": ref,
                    "role": "structural",
                    "explanation": "Retain declared structure.",
                }
            ],
        }
    )
    assert doc.to_dict() == before


def test_row_and_empty_record_anchors_need_text_not_typed_number_metadata():
    doc, region, roles = fixture()
    _, schema = native.request(doc, region, roles, {})
    record = {
        "key": "items",
        "label": "Items",
        "definitionRefs": ["a_control"],
        "columns": [
            {
                "key": "number",
                "label": "Number",
                "valueType": "integer",
                "definitionRefs": ["a_control"],
            }
        ],
        "rows": [
            {"anchors": ["a_number"], "states": [{"status": "present", "sourceRefs": ["a_number"]}]}
        ],
    }
    value = {"regionId": "r", "records": [record]}
    validator = Draft202012Validator(schema)
    assert not validator.is_valid(value)
    record["rows"][0]["anchors"] = ["a_text"]
    assert validator.is_valid(value)  # Anchor eligibility, not row-value containment approval.
    record.update(rows=[], emptyAnchors=["a_number"])
    assert not validator.is_valid(value)
    record["emptyAnchors"] = ["a_text"]
    assert validator.is_valid(value)


def test_anchor_candidates_use_owned_views_not_full_node_text_or_archive_nodes():
    doc = ObservationDocument()
    doc.node("visible", "ABC")
    doc.node("empty_view", "hidden text")
    doc.node("archive", "whole original text", role="source_text")
    doc.node("context", "context only")
    region = {
        "id": "r",
        "nodeIds": ["visible", "empty_view", "archive"],
        "contextNodeIds": ["context"],
        "nodeViews": {"visible": {"start": 1, "end": 2}, "empty_view": {"start": 0, "end": 0}},
    }
    before = copy.deepcopy((doc.to_dict(), region))
    validator = Draft202012Validator(wire.contract(region, {}, observation=doc))
    for ref in ["visible", "empty_view", "archive", "context"]:
        value = {
            "regionId": "r",
            "meanings": [{"kind": "reference", "anchors": [ref], "status": "interpreted"}],
        }
        assert validator.is_valid(value) == (ref == "visible")
    structure = wire.decode(
        {
            "regionId": "r",
            "meanings": [{"kind": "reference", "anchors": ["visible"], "status": "interpreted"}],
        },
        doc,
        region,
    )
    assert structure.meanings[0].sourceQuotes[0].text == "B"
    assert (doc.to_dict(), region) == before


def test_native_spreadsheet_source_text_path_controls_quote_eligibility():
    doc = ObservationDocument()
    for ref, text, value in [
        ("blank", "A1=", {"kind": "blank", "raw": ""}),
        ("number", "A2=1", {"kind": "number", "raw": "1.00", "value": 1}),
    ]:
        doc.node(
            ref,
            text,
            role="sheet_cell",
            semantic={
                "sheet": {"name": "Sheet1"},
                "cell": {"coordinate": "A1" if ref == "blank" else "A2"},
                "value": value,
            },
        )
    region = {"id": "r", "nodeIds": list(doc.nodes)}
    validator = Draft202012Validator(wire.contract(region, {}, observation=doc))
    assert not validator.is_valid(
        {
            "regionId": "r",
            "meanings": [{"kind": "note", "status": "interpreted", "anchors": ["blank"]}],
        }
    )
    value = {
        "regionId": "r",
        "meanings": [{"kind": "reference", "status": "interpreted", "anchors": ["number"]}],
    }
    assert validator.is_valid(value)
    assert wire.decode(value, doc, region).meanings[0].sourceQuotes[0].text == "1.00"
    assert source_inventory(doc, region)["sources"][1]["path"] == "/semantic/value/raw"


def test_no_text_still_allows_typed_scalar_values_and_structural_accounting():
    doc, region, roles = fixture()
    region = {**region, "nodeIds": ["a_number"], "contextNodeIds": ["a_control"]}
    schema = wire.contract(region, {}, observation=doc)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    value = {
        "regionId": "r",
        "fields": [
            {**field(["a_number"]), "valueType": "integer", "definitionRefs": ["a_control"]}
        ],
        "dispositions": [
            {"sourceRef": "a_number", "role": "structural", "explanation": "Declared number."}
        ],
    }
    assert validator.is_valid(value)
    assert not validator.is_valid(
        {
            "regionId": "r",
            "meanings": [{"kind": "reference", "status": "interpreted", "anchors": ["a_number"]}],
        }
    )


def test_revision_change_anchors_use_same_eligible_text_sources():
    doc, region, roles = fixture()
    value = {"regionId": "r", "fields": [field(["a_text"])]}
    state = {
        "base": {
            "structure": {"wireResponse": value, "structureHash": digest(value)},
            "content": {},
        },
        "trigger": [],
    }
    payload, schema = revision.request(state, roles, doc, region, {})
    candidate = {
        "baseStructureHash": payload["baseStructureHash"],
        "decision": "replace",
        "reason": "Retain field.",
        "replacement": value,
        "changes": [
            {
                "action": "keep",
                "before": ["field:1"],
                "after": ["field:1"],
                "anchors": [{"sourceRef": "a_control", "text": "Invented control text"}],
                "reason": "Exact source.",
            }
        ],
    }
    validator = Draft202012Validator(schema)
    assert not validator.is_valid(candidate)
    candidate["changes"][0]["anchors"] = ["a_text", "a_control"]
    assert validator.is_valid(candidate)
