"""Actual XLSX source text in structural requests, not a model-quality fixture."""

import copy
import io
import zipfile

from openpyxl import Workbook
from test_native_merged_geometry import observe

from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.table_protocol import structure_payload
from document_files.interpretation.table_source_wire import expand_table_sources


def source_fixture():
    book = Workbook()
    sheet = book.active
    sheet.append(["  항목\tName  ", "Amount", "Formula"])
    sheet.append(["A2=실제 값", 1.23, "=B2*2"])
    sheet["D2"].number_format = "@"  # Stored blank, unlike absent E2.
    sheet["A3"] = "   "
    sheet["C2"].number_format = "0.0000"
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(stream.getvalue())) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for entry in source.infolist():
            data = source.read(entry)
            if entry.filename == "xl/worksheets/sheet1.xml":
                assert b"<v>1.23</v>" in data and b"<f>B2*2</f><v></v>" in data
                data = data.replace(b"<v>1.23</v>", b"<v>1.234567890123456789000</v>")
                data = data.replace(b"<f>B2*2</f><v></v>", b"<f>B2*2</f><v>9.0000</v>")
            target.writestr(entry, data)
    doc = observe(output.getvalue(), "xlsx")
    refs = {
        n["semantic"]["cell"]["coordinate"]: r
        for r, n in doc.nodes.items()
        if n.get("semanticRole") == "sheet_cell"
    }
    region = prepare_regions(doc, context_chars=16000)[0]
    return doc, region, refs


def test_structure_uses_exact_native_cells_with_original_locations_and_no_address_stripping():
    doc, region, refs = source_fixture()
    payload = region_payload(doc, region)
    table = payload["tables"][region["tableRef"]]
    # Exercise rebuilding a computed header caption from the same source refs.
    table["columnCandidates"] = [
        {"column": 0, "headerRefs": [refs["A1"]], "headerText": "old display"}
    ]
    before = copy.deepcopy((doc, region, payload))
    view = expand_table_sources(structure_payload(payload))
    expected = {
        "A1": ("value", "  항목\tName  "),
        "B1": ("value", "Amount"),
        "C1": ("value", "Formula"),
        "A2": ("value", "A2=실제 값"),
        "B2": ("raw", "1.234567890123456789000"),
        "C2": ("formula", "=B2*2"),
        "D2": ("raw", ""),
        "A3": ("value", "   "),
    }
    assert set(refs) == set(expected)  # No invented gap cells.
    for coordinate, (key, text) in expected.items():
        ref = refs[coordinate]
        assert view["nodes"][ref]["text"] == text
        assert view["nodes"][ref]["textRange"] == {
            "path": f"/semantic/value/{key}",
            "start": 0,
            "end": len(text),
        }
        assert view["nodes"][ref]["semantic"] == payload["nodes"][ref]["semantic"]
    assert view["nodes"][refs["C2"]]["semantic"]["value"]["cachedValue"]["raw"] == "9.0000"
    presented = view["tables"][region["tableRef"]]
    assert presented["columnCandidates"][0]["headerText"] == "  항목\tName  "
    assert {
        cell[2]: cell[3] for row in presented["rowCandidates"]["rows"] for cell in row["cells"]
    } == {refs[coordinate]: text for coordinate, (_, text) in expected.items()}
    assert (doc, region, payload) == before


def test_existing_display_window_is_not_reinterpreted_as_a_native_value_range():
    doc, region, refs = source_fixture()
    source = refs["A2"]
    region["nodeViews"] = {source: {"start": 1, "end": 4}}
    payload = region_payload(doc, region)
    view = expand_table_sources(structure_payload(payload))
    assert view["nodes"][source]["text"] == doc.nodes[source]["text"][1:4]
    assert view["nodes"][source]["textRange"] == {"path": "/text", "start": 1, "end": 4}


def test_missing_native_literal_falls_back_without_guessing_a_coordinate_prefix():
    doc, region, refs = source_fixture()
    payload = region_payload(doc, region)
    source = refs["A2"]
    payload["nodes"][source]["semantic"]["value"].pop("value")
    before = copy.deepcopy(payload)
    view = expand_table_sources(structure_payload(payload))
    assert view["nodes"][source]["text"] == before["nodes"][source]["text"]
    assert "textRange" not in view["nodes"][source]
    assert payload == before
