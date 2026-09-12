"""Compact source decisions retain structure and provenance, not model quality proof."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_document_outline import structure_wire
from test_native_records import fixture, structure_fixture

from document_files.interpretation import native_structure as native
from document_files.interpretation import native_structure_wire as wire
from document_files.interpretation.compiler import CompileError, compile_region


def setup():
    doc, region, old = fixture()
    full = structure_fixture(old)
    compact = structure_wire(full, doc.nodes)
    return doc, region, full, compact, {"documentElements": old["documentElements"]}


def test_compact_decisions_preserve_types_definitions_all_states_order_and_exact_anchors():
    doc, region, full, compact, roles = setup()
    before = copy.deepcopy((doc, region, compact))
    schema = wire.contract(region, {})
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(compact)
    assert len(json.dumps(compact)) < 0.65 * len(json.dumps(full))
    rebuilt = wire.decode(compact, doc, region)
    assert rebuilt.fields[0].id == "field:1"
    assert rebuilt.fields[0].definitionRefs == full["fields"][0]["definitionRefs"]
    record = rebuilt.records[0]
    assert [c.valueType for c in record.columns] == [
        c["valueType"] for c in full["records"][0]["columns"]
    ]
    assert all(c.definitionRefs == record.definitionRefs for c in record.columns)
    for original, row in zip(full["records"][0]["rows"], record.rows, strict=True):
        assert [c.status for c in row.cells] == [c["status"] for c in original["cells"]]
        assert [c.sourceRefs for c in row.cells] == [c["sourceRefs"] for c in original["cells"]]
        assert [q.model_dump() for q in row.sourceQuotes] == [
            native.SourceQuote.model_validate(q).model_dump() for q in original["sourceQuotes"]
        ]
    compiled = compile_region(native.interpretation(rebuilt, roles, doc, region), doc, region)
    assert len(compiled.data["items"]) == 2 and not doc.tables
    assert (doc, region, compact) == before


def test_both_source_and_definition_overrides_are_retained():
    doc, region, _, compact, roles = setup()
    compact["fields"][0]["definitionRefs"] = ["note"]
    r = compact["records"][0]
    r["columns"][0]["definitionRefs"] = ["a"]
    r["rows"][0]["states"][0] = {"status": "present", "sourceRefs": ["b"]}
    parsed = wire.decode(compact, doc, region)
    assert parsed.fields[0].definitionRefs == ["note"] and parsed.fields[0].sourceRefs == ["meta"]
    assert parsed.records[0].columns[0].definitionRefs == ["a"]
    assert parsed.records[0].rows[0].cells[0].sourceRefs == ["b"]
    compile_region(native.interpretation(parsed, roles, doc, region), doc, region)


@pytest.mark.parametrize(
    "mutation",
    [
        "old_ids",
        "missing_status",
        "unknown_status",
        "foreign_block",
        "mixed_anchor",
        "extra_value",
        "unknown_meaning",
    ],
)
def test_closed_wire_rejects_invalid_or_retired_choices(mutation):
    doc, region, _, compact, _ = setup()
    row = compact["records"][0]["rows"][0]
    if mutation == "old_ids":
        compact["fields"][0]["id"] = "invented"
    elif mutation == "missing_status":
        del compact["fields"][0]["status"]
    elif mutation == "unknown_status":
        row["states"][0] = "missing"
    elif mutation == "foreign_block":
        row["anchors"] = ["foreign"]
    elif mutation == "mixed_anchor":
        row["anchors"] = [{"sourceRef": "b", "text": "Marigold", "block": "b"}]
    elif mutation == "extra_value":
        row["values"] = ["Marigold"]
    else:
        compact["meanings"][0]["kind"] = "arbitrary"
    assert not Draft202012Validator(wire.contract(region, {})).is_valid(compact)


@pytest.mark.parametrize("mutation", ["short", "long", "foreign_state", "overlapping_blocks"])
def test_compiler_does_not_guess_or_discard_inconsistent_occurrences(mutation):
    doc, region, _, compact, roles = setup()
    row = compact["records"][0]["rows"][0]
    if mutation == "short":
        row["states"].pop()
    elif mutation == "long":
        row["states"].append("present")
    elif mutation == "foreign_state":
        row["states"][0] = {"status": "absent", "sourceRefs": ["meta"]}
    else:
        compact["records"][0]["rows"][1]["anchors"] = list(row["anchors"])
    with pytest.raises((ValueError, CompileError)):
        parsed = wire.decode(compact, doc, region)
        compile_region(native.interpretation(parsed, roles, doc, region), doc, region)


def test_whole_owned_view_and_part_quotes_have_exact_original_unicode_ranges():
    doc, region, _, compact, roles = setup()
    # This view still denotes one exact item, not its hidden prefix/suffix.
    text = doc.nodes["b"]["text"]
    doc.nodes["b"]["text"] = "HIDDEN🙂 " + text + " HIDDEN"
    region["nodeViews"] = {"b": {"start": 8, "end": 8 + len(text)}}
    # Its existing blank binding retains original coordinates too.
    for b in doc.bindings.values():
        if b["sourceRef"] == "b":
            b["start"] += 8
            b["end"] += 8
    parsed = wire.decode(compact, doc, region)
    assert parsed.records[0].rows[0].sourceQuotes[0].text == text
    compiled = compile_region(native.interpretation(parsed, roles, doc, region), doc, region)
    ranges = compiled.logical_coverage["record:1"]["rows"][1]["sourceRanges"]
    assert ranges[0]["start"] == 8 and ranges[0]["end"] == 8 + len(text)


def test_whole_block_empty_record_requires_nonempty_source_and_preserves_empty_array():
    doc, region, _, _, roles = setup()
    doc.nodes["b"]["text"] = "No items were supplied."
    compact = {
        "regionId": region["id"],
        "records": [
            {
                "key": "items",
                "label": "Items",
                "definitionRefs": ["b"],
                "columns": [{"key": "name", "label": "Name", "valueType": "string"}],
                "rows": [],
                "emptyAnchors": ["b"],
            }
        ],
    }
    parsed = wire.decode(compact, doc, region)
    compiled = compile_region(native.interpretation(parsed, roles, doc, region), doc, region)
    assert compiled.data == {"items": []}
    compact["records"][0]["emptyAnchors"] = []
    with pytest.raises(CompileError, match="rows_or_empty_evidence"):
        compile_region(
            native.interpretation(wire.decode(compact, doc, region), roles, doc, region),
            doc,
            region,
        )


def test_value_request_factors_row_context_without_changing_field_source_options():
    doc, region, _, compact, roles = setup()
    parsed = wire.decode(compact, doc, region)
    payload, schema = native.value_request(parsed, roles, doc, region)
    assert len(payload["occurrences"]) == 2
    for item in payload["handles"].values():
        assert "sourceQuotes" not in item
        if "recordId" in item:
            occurrence = payload["occurrences"][item["occurrenceRef"]]
            assert occurrence["rowId"] == item["rowId"] and occurrence["sourceQuotes"]
    Draft202012Validator.check_schema(schema)


def test_decode_limits_expansion_before_constructing_per_cell_objects():
    doc, region, _, compact, _ = setup()
    record = compact["records"][0]
    record["rows"] = record["rows"] * 250
    record["columns"] = record["columns"] * 5
    for row in record["rows"]:
        row["states"] = ["present"] * len(record["columns"])
    with pytest.raises(ValueError, match="expansion_budget_exceeded"):
        wire.decode(compact, doc, region)


@pytest.mark.parametrize("damage", ["hash", "anchor", "definition"])
def test_checkpoint_rebuilds_compact_wire_instead_of_trusting_expanded_structure(damage):
    from test_document_protocol import StagedModel, execute, raw_document

    from document_files.interpretation.document_protocol import digest

    model, states = StagedModel(), []
    raw = raw_document(("Count: 0007",))
    first = execute(model, raw=raw, states=states)
    assert first["extraction"]["status"] == "complete"
    state = copy.deepcopy(states[-1])
    saved = next(iter(state["documentStages"].values()))["structure"]
    assert "id" not in saved["wireResponse"]["fields"][0]
    if damage == "hash":
        saved["wireHash"] = "0" * 64
    else:
        f = saved["wireResponse"]["fields"][0]
        if damage == "anchor":
            f["sourceRefs"] = ["foreign"]
        else:
            f["label"] = "Changed semantic definition"
        saved["wireHash"] = digest(saved["wireResponse"])
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, raw=raw, restore=state)
    assert model.calls == 3


def test_nested_groups_and_distinct_target_handles_keep_their_compiled_destinations():
    doc, region, _, compact, roles = setup()
    compact["groups"] = [
        {"id": "parent", "key": "package", "label": "Package", "sourceRefs": ["meta"]},
        {
            "id": "child",
            "key": "contents",
            "label": "Contents",
            "sourceRefs": ["a", "b"],
            "parentId": "parent",
        },
    ]
    compact["fields"][0]["groupId"] = "parent"
    compact["records"][0]["groupId"] = "child"
    parsed = wire.decode(compact, doc, region)
    result = compile_region(native.interpretation(parsed, roles, doc, region), doc, region)
    assert result.data["package"]["reference"] is None
    assert len(result.data["package"]["contents"]["items"]) == 2
    schema = wire.contract(region, {"targetHandles": {"@target": {}}})
    compact["fields"][0]["targetHandle"] = "@target"
    Draft202012Validator(schema).validate(compact)
    assert wire.decode(compact, doc, region).fields[0].targetHandle == "@target"
    compact["fields"][0]["targetHandle"] = "unknown"
    assert not Draft202012Validator(schema).is_valid(compact)
