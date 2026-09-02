from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from openpyxl import Workbook

from document_files.engine import extract_structure


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_xlsx_structure_and_typed_values_are_source_addressed(tmp_path: Path) -> None:
    path = tmp_path / "values.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "데이터"
    sheet.merge_cells("A1:B1")
    sheet["A1"] = "항목"
    sheet["A2"] = "수량"
    sheet["B2"] = 3
    sheet["C2"] = True
    sheet["D2"] = date(2026, 9, 2)
    sheet["E2"] = "=B2*2"
    sheet.row_dimensions[3].hidden = True
    sheet.column_dimensions["F"].hidden = True
    hidden = workbook.create_sheet("비공개")
    hidden.sheet_state = "hidden"
    hidden["A1"] = "숨김값"
    workbook.save(path)
    before = _digest(path)

    result = extract_structure(path, max_units=50)

    assert _digest(path) == before
    assert result["schemaVersion"] == "document-files.structured-extraction.v1"
    assert result["semanticPolicy"] == {
        "sourceDeclaredOnly": True,
        "adjacentCellInference": False,
        "formulaEvaluation": False,
        "indexIdentityAssigned": False,
    }
    assert result["summary"]["unitTypes"] == {"sheet": 2, "sheet_cell": 7}
    units = result["units"]
    data_sheet = next(
        unit
        for unit in units
        if unit["sourceUnitType"] == "sheet" and unit["semantic"]["sheet"]["name"] == "데이터"
    )
    assert data_sheet["semantic"]["sheet"]["mergedRanges"] == [
        {
            "range": "A1:B1",
            "origin": {"row": 1, "column": 1},
            "rowSpan": 1,
            "columnSpan": 2,
        }
    ]
    assert data_sheet["semantic"]["sheet"]["hiddenRows"] == [3]
    assert data_sheet["semantic"]["sheet"]["hiddenColumnRanges"] == [
        {"minColumn": 6, "maxColumn": 6}
    ]

    cells = {
        unit["semantic"]["cell"]["coordinate"]: unit["semantic"]
        for unit in units
        if unit["sourceUnitType"] == "sheet_cell" and unit["semantic"]["sheet"]["name"] == "데이터"
    }
    assert cells["B2"]["value"] == {"kind": "integer", "value": 3}
    assert cells["C2"]["value"] == {"kind": "boolean", "value": True}
    assert cells["D2"]["value"] == {"kind": "date", "value": "2026-09-02"}
    assert cells["E2"]["value"] == {
        "kind": "formula",
        "formula": "=B2*2",
        "evaluation": "stored_cached_value_only",
        "cachedAvailable": False,
        "cachedValue": None,
    }
    assert result["summary"]["issueCounts"] == {"xlsx_formula_without_cached_value": 1}


def test_structured_extraction_is_paged_and_can_omit_text(tmp_path: Path) -> None:
    path = tmp_path / "source.md"
    path.write_text("# 제목\n\n첫 문단\n\n둘째 문단", encoding="utf-8")

    first = extract_structure(path, max_units=2, include_text=False)
    second = extract_structure(
        path,
        unit_offset=first["unitPage"]["nextOffset"],
        max_units=2,
    )

    assert first["unitPage"] == {
        "offset": 0,
        "limit": 2,
        "returned": 2,
        "total": 3,
        "hasMore": True,
        "nextOffset": 2,
        "textIncluded": False,
    }
    assert all("text" not in unit for unit in first["units"])
    assert second["unitPage"]["hasMore"] is False
    assert [unit["text"] for unit in second["units"]] == ["둘째 문단"]
    assert first["manifestHash"] == second["manifestHash"]
