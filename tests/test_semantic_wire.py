"""Small model-facing contracts preserve the full typed IR and source choices."""

from copy import deepcopy

from jsonschema import Draft202012Validator

from document_files.document_model.observe import observe_document
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.semantic_types import (
    RegionInterpretation,
    _compact_contract,
    region_output_schema,
)


def test_text_wire_omits_table_definitions_and_requires_an_explicit_type():
    observation = observe_document(b"Length: 0\n", "txt", {})
    region = prepare_regions(observation, context_chars=120000)[0]
    original = RegionInterpretation.model_json_schema()
    wire = region_output_schema(observation, region)
    Draft202012Validator.check_schema(wire)
    assert {"RepeatLink", "ColumnLink", "RowRole"}.isdisjoint(wire["$defs"])
    assert wire["properties"]["repeats"]["maxItems"] == 0
    assert "valueType" in wire["$defs"]["FieldLink"]["required"]
    assert "valueType" not in original["$defs"]["FieldLink"]["required"]
    assert original == RegionInterpretation.model_json_schema()


def test_compaction_preserves_accepted_values_and_property_names():
    schema = {
        "type": "object",
        "title": "Container",
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "empty": {"type": "array", "maxItems": 0, "items": {"$ref": "#/$defs/Unused"}},
            "data": {"$ref": "#/$defs/Used"},
        },
        "$defs": {
            "Used": {"type": "array", "items": {"$ref": "#/$defs/Leaf"}},
            "Leaf": {"type": "integer", "minimum": 0},
            "Unused": {"type": "string"},
        },
        "additionalProperties": False,
    }
    compact = _compact_contract(deepcopy(schema))
    assert set(compact["$defs"]) == {"Used", "Leaf"}
    assert "title" in compact["properties"]
    before, after = Draft202012Validator(schema), Draft202012Validator(compact)
    for value in (
        {},
        {"empty": []},
        {"empty": [None]},
        {"empty": ["x"]},
        {"title": "x", "data": [0]},
        {"data": [-1]},
        {"extra": 1},
    ):
        assert before.is_valid(value) == after.is_valid(value)


def test_table_wire_retains_repeat_definition_and_explicit_column_types():
    observation = observe_document(b"| A | B |\n|---|---|\n| 0 | false |\n", "md", {})
    region = next(
        r for r in prepare_regions(observation, context_chars=120000) if r.get("tableRef")
    )
    wire = region_output_schema(observation, region)
    Draft202012Validator.check_schema(wire)
    assert {"RepeatLink", "ColumnLink", "RowRole"} <= set(wire["$defs"])
    assert "valueType" in wire["$defs"]["ColumnLink"]["required"]


def test_node_read_coverage_requires_every_owning_view_even_if_seen_as_context():
    from document_files.interpretation.engine import _node_read_coverage

    regions = [
        {"id": "a", "nodeIds": ["n1"], "contextNodeIds": ["n2", "heading"]},
        {"id": "b", "nodeIds": ["n1"]},
        {"id": "c", "nodeIds": ["n2"]},
    ]
    assert _node_read_coverage(regions, {"a"}) == {"readNodes": 1, "partiallyReadNodes": 2}
    assert _node_read_coverage(regions, {"a", "b"}) == {"readNodes": 2, "partiallyReadNodes": 1}
    assert _node_read_coverage(regions, {"a", "b", "c"}) == {
        "readNodes": 3,
        "partiallyReadNodes": 0,
    }


def test_shared_reference_enums_preserve_validation_and_fit_small_cpu_table():
    import json

    choices = {"type": "string", "enum": ["source:" + "a" * 80, "source:" + "b" * 80]}
    original = {"type": "object", "properties": {"a": deepcopy(choices), "b": deepcopy(choices)}}
    compact = _compact_contract(deepcopy(original))
    assert "$ref" in compact["properties"]["a"]
    for value in [{"a": choices["enum"][0], "b": choices["enum"][1]}, {"a": "invented"}, {}]:
        assert Draft202012Validator(original).is_valid(value) == Draft202012Validator(
            compact
        ).is_valid(value)
    html = (
        b'<table><tr><th rowspan="2">ID</th><th colspan="2">Measurements</th></tr>'
        b"<tr><th>Length</th><th>Width</th></tr>"
        b"<tr><td>00012345678901234567890</td><td>0</td>"
        b"<td>1.234567890123456789</td></tr></table>"
    )
    doc = observe_document(html, "html", {})
    regions = prepare_regions(doc, context_chars=16000)
    tables = [r for r in regions if r.get("tableRef")]
    assert tables and all(r["withinContextBudget"] for r in tables)
    assert all(r["requestChars"] <= 16000 for r in tables)
    for region in tables:
        wire = region_output_schema(doc, region)
        Draft202012Validator.check_schema(wire)
        assert "invented" not in json.dumps(wire)


def test_sliced_native_headers_stay_context_instead_of_becoming_data_regions():
    html = (
        b'<table><tr><th rowspan="2">Sample ID</th><th colspan="2">Measurements</th></tr>'
        b"<tr><th>Length</th><th>Width</th></tr>"
        + b"".join(
            b"<tr><td>" + str(row).encode() + b"</td><td>" + b"1" * 120 + b"</td><td>3.00</td></tr>"
            for row in range(16)
        )
        + b"</table>"
    )
    doc = observe_document(html, "html", {})
    source_table = deepcopy(next(iter(doc.tables.values())))
    headers = {c["sourceRef"] for c in source_table["cells"] if c.get("isHeader")}
    regions = prepare_regions(doc, context_chars=16000)
    table_regions = [r for r in regions if r.get("tableRef")]
    assert len(table_regions) > 1
    owned = set()
    for region in table_regions:
        assert region["withinContextBudget"]
        table = doc.tables[region["tableRef"]]
        assert table["viewRowStart"] >= 2
        assert not headers.intersection(region["nodeIds"])
        assert headers <= set(region["contextNodeIds"])
        assert table["headerCells"] == [c for c in source_table["cells"] if c.get("isHeader")]
        assert not headers.intersection(doc.bindings[b]["sourceRef"] for b in region["bindingIds"])
        owned.update(region["nodeIds"])
    assert owned == {c["sourceRef"] for c in source_table["cells"]} - headers
    assert doc.tables[source_table["id"]] == source_table


def test_shared_constraints_preserve_required_bounds_and_nullable_choices():
    import json

    scalar = {"type": "string", "minLength": 1, "maxLength": 120}
    nullable = {"anyOf": [scalar, {"type": "null"}], "default": None}
    schema = {
        "type": "object",
        "properties": {f"field{i}": deepcopy(nullable) for i in range(12)},
        "required": ["field0"],
        "additionalProperties": False,
    }
    original = deepcopy(schema)
    compact = _compact_contract(deepcopy(schema))
    assert schema == original
    Draft202012Validator.check_schema(compact)
    before, after = Draft202012Validator(original), Draft202012Validator(compact)
    for value in (
        {},
        {"field0": None},
        {"field0": "a"},
        {"field0": ""},
        {"field0": False},
        {"field0": "x" * 121},
        {"field0": "a", "extra": 0},
    ):
        assert before.is_valid(value) == after.is_valid(value)
    assert len(json.dumps(compact)) < len(json.dumps(original))
    # Stable output prevents new request identities from incidental traversal order.
    assert compact == _compact_contract(deepcopy(original))


def test_columnar_cell_prompt_is_lossless_and_does_not_mutate_observation():
    from document_files.interpretation.regions import _table_payload

    table = {
        "cells": [
            {
                "sourceRef": f"n{i}",
                "row": i,
                "col": 0,
                "rowSpan": 1,
                "colSpan": 2,
                "isHeader": False,
                "headerScope": None,
                "text": text,
            }
            for i, text in enumerate(
                ["", "0", "false", "00000000000000000001", "1.234567890123456789", "한국어"]
            )
        ],
        "headerCells": [{"sourceRef": "h", "row": 0, "col": 0}],
    }
    before = deepcopy(table)
    projected = _table_payload(table)
    matrix = projected["cells"]
    assert matrix["encoding"] == "columns-rows.v1"
    assert [dict(zip(matrix["columns"], row, strict=True)) for row in matrix["rows"]] == table[
        "cells"
    ]
    assert table == before
    assert projected["headerCells"] == table["headerCells"]  # no inflation of small lists
    # Missing is not null: heterogeneous properties must not be padded.
    del table["cells"][0]["headerScope"]
    assert _table_payload(table)["cells"] == table["cells"]


def test_planned_table_size_matches_actual_compact_payload():
    import json

    from document_files.interpretation.regions import region_payload
    from document_files.interpretation.semantic_prompts import SYSTEM

    doc = observe_document(
        b"<table><tr><th>ID</th><th>Amount</th></tr>"
        + b"<tr><td>00001</td><td>0.001</td></tr>" * 8
        + b"</table>",
        "html",
        {},
    )
    metadata = {"intent": "discover", "targetHandles": {}}
    for region in prepare_regions(doc, context_chars=16000, request_metadata=metadata):
        if not region.get("tableRef"):
            continue
        request = {
            **region_payload(doc, region),
            **metadata,
            "outputContract": region_output_schema(doc, region, {}),
        }
        actual = len(SYSTEM) + len(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
        assert region["requestChars"] == actual
        assert region["withinContextBudget"] == (actual <= 16000)


MERGED_HEADER_FORM = (
    b"<table><caption>Dimension inspection. Unit mm.</caption>"
    b'<tr><th rowspan="2">Sample ID</th><th colspan="2">Measurements</th></tr>'
    b"<tr><th>Length</th><th>Width</th></tr>"
    b"<tr><td>00012345678901234567890</td><td>0</td><td>1.234567890123456789</td></tr>"
    b"<tr><td>00000000000000000002</td><td>12.50</td><td>3.00</td></tr></table>"
)


def test_table_payload_lists_program_derived_column_candidates_and_data_rows():
    from document_files.interpretation.regions import region_payload

    doc = observe_document(MERGED_HEADER_FORM, "html", {})
    regions = prepare_regions(doc, context_chars=120000)
    assert len(regions) == 1  # the table's own caption is read with the table
    region = regions[0]
    table = doc.tables[region["tableRef"]]
    text = {c["sourceRef"]: doc.nodes[c["sourceRef"]]["text"] for c in table["cells"]}
    payload = region_payload(doc, region)["tables"][region["tableRef"]]
    candidates = [[text[r] for r in c["headerRefs"]] for c in payload["columnCandidates"]]
    assert candidates == [["Sample ID"], ["Measurements", "Length"], ["Measurements", "Width"]]
    assert [c["headerText"] for c in payload["columnCandidates"]] == [
        "Sample ID",
        "Measurements > Length",
        "Measurements > Width",
    ]
    assert payload["dataRows"] == {"rowStart": 2, "rowEnd": 3}
    caption = next(n for n in doc.nodes if doc.nodes[n].get("semanticRole") == "caption")
    assert caption in region["nodeIds"] and caption not in region["contextNodeIds"]
    assert not any(doc.bindings[b].get("candidateRole") == "lexeme" for b in region["bindingIds"])
    headers = {c["sourceRef"] for c in table["cells"] if c.get("isHeader")}
    offered = {doc.bindings[b]["sourceRef"] for b in region["bindingIds"]}
    assert not offered & (headers | {caption})
    assert any(doc.bindings[b].get("candidateRole") == "lexeme" for b in doc.bindings)
    assert "columnCandidates" not in table and "dataRows" not in table


def test_sliced_table_reads_its_caption_with_the_first_slice_only():
    html = (
        b"<table><caption>Long inspection table</caption>"
        b'<tr><th rowspan="2">Sample ID</th><th colspan="2">Measurements</th></tr>'
        b"<tr><th>Length</th><th>Width</th></tr>"
        + b"".join(
            b"<tr><td>" + str(row).encode() + b"</td><td>" + b"1" * 120 + b"</td><td>3.00</td></tr>"
            for row in range(8)
        )
        + b"</table>"
    )
    doc = observe_document(html, "html", {})
    caption = next(n for n in doc.nodes if doc.nodes[n].get("semanticRole") == "caption")
    regions = prepare_regions(doc, context_chars=16000)
    views = [r for r in regions if r.get("tableRef")]
    assert len(views) > 1 and len(views) == len(regions)
    assert [caption in r["nodeIds"] for r in views] == [True] + [False] * (len(views) - 1)
    assert all(r["withinContextBudget"] for r in views)
