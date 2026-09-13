"""Native-source and scripted compilation regressions, not model quality evidence."""

import copy
import hashlib
import io
import zipfile

import pytest
from openpyxl import Workbook
from test_native_merged_geometry import observe

from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.semantic_types import RegionInterpretation
from document_files.interpretation.table_protocol import (
    meaning_ir,
    meaning_payload,
    meaning_response,
)
from document_files.interpretation.table_revisions import meaning_revision
from document_files.interpretation.table_selection import selection_schema
from document_files.interpretation.table_sources import (
    SOURCE_INVENTORY_VERSION,
    SourceReviewError,
    resolve_quotes,
    review_ranges,
    source_inventory,
)


def workbook(rows):
    book = Workbook()
    for row in rows:
        book.active.append(row)
    book.active["D1"].number_format = "@"  # Explicit blank, not an implicit grid gap.
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def native_sources(doc):
    refs = {
        node["semantic"]["cell"]["coordinate"]: ref
        for ref, node in doc.nodes.items()
        if node.get("semanticRole") == "sheet_cell"
    }
    inv = source_inventory(doc, {"nodeIds": list(refs.values())})
    return refs, inv, {s["sourceRef"]: s for s in inv["sources"]}


def test_actual_xlsx_inventory_reads_native_strings_literals_and_formula_not_display_or_cache():
    text = "  단위\tmm;\r\n mm 🙂가  "
    raw = workbook([[text, "B1=사용자가 쓴 주소"], ["001.2300", ""], [1.23, True], ['="unit mm"']])
    # Preserve a decimal spelling and a stored formula cache in the actual OOXML.
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = content.replace(b"<v>1.23</v>", b"<v>1.2300</v>")
                content = content.replace(
                    b'<c r="A4"><f>"unit mm"</f><v></v></c>',
                    b'<c r="A4" t="str"><f>"unit mm"</f><v>cached mm</v></c>',
                )
            target.writestr(item, content)
    doc = observe(output.getvalue(), "xlsx")
    original = copy.deepcopy(doc)
    refs, inv, sources = native_sources(doc)
    assert inv["version"] == SOURCE_INVENTORY_VERSION
    expected = {
        "A1": ("value", text),
        "B1": ("value", "B1=사용자가 쓴 주소"),
        "D1": ("raw", ""),
        "A2": ("value", "001.2300"),
        "B2": ("value", ""),
        "A3": ("raw", "1.2300"),
        "B3": ("raw", "1"),
        "A4": ("formula", '="unit mm"'),
    }
    assert set(refs) == set(expected)  # No invented implicit cells.
    for coordinate, (key, value) in expected.items():
        source = sources[refs[coordinate]]
        assert source == {
            "sourceRef": refs[coordinate],
            "path": f"/semantic/value/{key}",
            "start": 0,
            "end": len(value),
            "text": value,
            "textSHA256": hashlib.sha256(value.encode()).hexdigest(),
        }
        if value:
            assert resolve_quotes([{"sourceRef": refs[coordinate], "text": value}], inv) == [source]
    assert doc.nodes[refs["A1"]]["text"] != "A1=" + text  # Legacy projection normalizes.
    assert doc.nodes[refs["A4"]]["semantic"]["value"]["cachedValue"]["value"] == "cached mm"
    for ref, quote in ((refs["A1"], "A1="), (refs["A4"], "cached mm")):
        with pytest.raises(SourceReviewError, match="quote_not_in_source"):
            resolve_quotes([{"sourceRef": ref, "text": quote}], inv)
    ranges = resolve_quotes([{"sourceRef": refs["A1"], "text": "mm", "occurrence": 1}], inv)
    assert ranges[0]["start"] == text.index("mm", text.index("mm") + 1)
    assert ranges[0]["path"] == "/semantic/value/value"
    # Empty/number contracts now see real empty/numeric text, not address=value.
    schema = selection_schema(inv["sources"])["properties"]["sourceDecisions"]["properties"]
    assert schema[refs["D1"]]["$ref"].endswith("/EmptySelectionChoice")
    assert schema[refs["A3"]]["$ref"].endswith("/ValueOnlySelectionChoice")
    assert schema[refs["B1"]]["$ref"].endswith("/SelectionChoice")
    assert doc == original


@pytest.mark.parametrize(
    "change",
    [
        {"semanticRole": "paragraph"},
        {"semantic": {}},
        {
            "semantic": {
                "sheet": {"name": "Sheet"},
                "cell": {},
                "value": {"kind": "string", "value": "x"},
            }
        },
        {
            "semantic": {
                "sheet": {"name": "Sheet"},
                "cell": {"coordinate": "A1"},
                "value": {"kind": "integer", "value": 5},
            }
        },
    ],
)
def test_text_that_looks_like_a_coordinate_is_not_stripped_without_native_source(change):
    node = {
        "text": "A1=original text",
        "semanticRole": "sheet_cell",
        "semantic": {
            "sheet": {"name": "Sheet"},
            "cell": {"coordinate": "A1"},
            "value": {"kind": "string", "value": "x"},
        },
    } | change
    inv = source_inventory({"nodes": {"n": node}}, {"nodeIds": ["n"]})
    assert inv["sources"][0]["path"] == "/text"
    assert inv["sources"][0]["text"] == node["text"]


def test_display_view_offsets_are_not_reinterpreted_as_native_value_offsets():
    doc = observe(workbook([["  A1=mm\r\n mm  "]]), "xlsx")
    refs, _, _ = native_sources(doc)
    ref = refs["A1"]
    original = doc.nodes[ref]["text"]
    start = original.index("mm")
    inv = source_inventory(
        doc, {"nodeIds": [ref], "nodeViews": {ref: {"start": start, "end": start + 2}}}
    )
    span = resolve_quotes([{"sourceRef": ref, "text": "mm"}], inv)[0]
    assert (span["path"], span["start"], span["end"]) == ("/text", start, start + 2)


def test_native_meaning_ranges_compile_and_repair_with_original_path_and_offsets():
    doc = observe(workbook([["ID", "Length (mm; mm)"], ["Q-0001", "001.2300"]]), "xlsx")
    original = copy.deepcopy(doc)
    refs, _, _ = native_sources(doc)
    region = next(r for r in prepare_regions(doc, context_chars=100000) if r.get("tableRef"))
    table = doc.tables[region["tableRef"]]
    frozen = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "repeats": [
                {
                    "id": "records",
                    "key": "records",
                    "label": "Records",
                    "tableRef": region["tableRef"],
                    "rowStart": 0,
                    "rowEnd": 1,
                    "definitionRefs": [refs["A1"], refs["B1"]],
                    "rowRoles": [
                        {
                            "row": row,
                            "role": "header" if row == 0 else "data",
                            "sourceRefs": [
                                c["sourceRef"] for c in table["cells"] if c["row"] == row
                            ],
                        }
                        for row in (0, 1)
                    ],
                    "columns": [
                        {
                            "id": key,
                            "key": key,
                            "label": key,
                            "column": col,
                            "valueType": "string",
                            "definitionRefs": [refs[coord]],
                        }
                        for col, key, coord in ((0, "id", "A1"), (1, "length", "B1"))
                    ],
                }
            ],
        }
    )
    before = compile_region(frozen, doc, region)
    inv = source_inventory(doc, region)
    payload = meaning_payload(region_payload(doc, region), frozen, before, inv)
    assert (
        next(s["text"] for s in payload["meaningSources"] if s["sourceRef"] == refs["B1"])
        == "Length (mm; mm)"
    )
    raw = {
        "regionId": region["id"],
        "meanings": [
            {
                "id": "unit",
                "kind": "unit",
                "description": "mm",
                "sourceQuotes": [{"sourceRef": refs["B1"], "text": "mm", "occurrence": 1}],
                "scope": {"kind": "columns", "columnIds": ["length"]},
                "status": "interpreted",
            }
        ],
        "sourceReviews": [
            {
                "sourceRefs": [s["sourceRef"] for s in inv["sources"]],
                "role": "no_additional_meaning",
                "explanation": "Scripted source review, not quality approval",
            }
        ],
        "baseRevision": None,
        "changes": [],
    }
    ir = meaning_ir(raw, frozen, inv)
    after = compile_region(ir, doc, region)
    assert before.data == after.data == {"records": [{"id": "Q-0001", "length": "001.2300"}]}
    assert [
        {key: value for key, value in item.items() if key != "semanticIds"}
        for item in before.value_evidence
    ] == [
        {key: value for key, value in item.items() if key != "semanticIds"}
        for item in after.value_evidence
    ]
    assert after.value_evidence[1]["semanticIds"] == [
        *before.value_evidence[1]["semanticIds"],
        f"{region['id']}:unit",
    ]
    assert after.semantic_details[0]["sourceText"] == ["mm"]
    assert after.semantic_details[0]["sourceRanges"][0]["path"] == "/semantic/value/value"
    assert after.semantic_details[0]["sourceRanges"][0]["start"] == 12
    assert (
        meaning_response(ir, inv)["meanings"][0]["sourceQuotes"]
        == raw["meanings"][0]["sourceQuotes"]
    )
    assert all(
        r["path"] in {"/semantic/value/value", "/semantic/value/raw"}
        for r in after.meaning_review["ranges"]
    )
    for operation in (
        meaning_response,
        lambda value, inventory: compile_region(value, doc, region),
    ):
        forged = copy.deepcopy(ir)
        forged.meanings[0].sourceRanges[0].path = "/text"
        forged.tableMeaningState.revisionSHA256 = meaning_revision(forged)
        with pytest.raises(CompileError, match="range"):
            operation(forged, inv)
    stale = copy.deepcopy(ir)
    stale.tableMeaningState.inventorySHA256 = "0" * 64
    with pytest.raises(CompileError, match="source_inventory_changed"):
        compile_region(stale, doc, region)
    # Planning adds views; original observation nodes and all source bindings stay intact.
    assert doc.nodes == original.nodes and doc.bindings == original.bindings


def test_older_inventory_version_cannot_resolve_new_quotes():
    doc = observe(workbook([["mm"]]), "xlsx")
    _, inv, _ = native_sources(doc)
    inv["version"] = "document-files.table-source-inventory.v1"
    with pytest.raises(SourceReviewError, match="source_inventory_version"):
        review_ranges([], [], inv)
