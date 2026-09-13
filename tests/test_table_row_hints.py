"""Neutral row geometry in native/model views, not an AI quality approval."""

from copy import deepcopy

import pytest
from test_native_merged_geometry import observe
from test_table_source_wire import native_table

from document_files.document_model.table_headers import declared_header, row_role_order
from document_files.interpretation.regions import (
    _table_payload,
    column_candidates,
    prepare_regions,
    region_payload,
)
from document_files.interpretation.semantic_prompts import SYSTEM
from document_files.interpretation.table_protocol import structure_model_schema, structure_payload
from document_files.interpretation.table_source_wire import expand_table_sources


@pytest.mark.parametrize(
    "basis", ["native_html", "native_structure", "markdown_table_tokens", "recognition", None]
)
@pytest.mark.parametrize("predicted", [False, True])
def test_all_unfixed_rows_keep_exact_coordinates_without_predicting_data(basis, predicted):
    table = {
        "basis": basis,
        "cells": [
            {
                "row": 0,
                "col": 0,
                "rowSpan": 2,
                "colSpan": 2,
                "sourceRef": "heading",
                "isHeader": True,
            },
            # Mixed header/value rows remain decisions; the third column is real.
            {"row": 1, "col": 2, "sourceRef": "mixed", "isHeader": predicted},
            {"row": 3, "col": 0, "rowSpan": 2, "sourceRef": "item", "isHeader": False},
            {"row": 6, "col": 0, "sourceRef": "note", "isHeader": False},
            {"row": 8, "col": 0, "sourceRef": "later-heading", "isHeader": True},
            {"row": 10, "col": 0, "sourceRef": "blank", "isHeader": False},
        ],
    }
    before = deepcopy(table)
    expected = sorted(
        {
            r
            for c in table["cells"]
            if not declared_header(c, table)
            for r in range(c["row"], c["row"] + c.get("rowSpan", 1))
        }
    )
    view = _table_payload(table)
    assert view["rowRoleOrder"] == row_role_order(table) == column_candidates(table)[1] == expected
    assert "dataRows" not in view
    assert table == before and "rowRoleOrder" not in table
    # The old envelope is recoverable, but cannot establish data roles or erase gaps.
    assert expected[-1] == 10 and 2 not in expected and 5 not in expected and 7 not in expected
    assert view["columnCandidates"][-1]["column"] == 2
    if basis in {"native_html", "native_structure", "markdown_table_tokens"}:
        assert expected == ([3, 4, 6, 10] if predicted else [1, 3, 4, 6, 10])
    else:
        assert expected == [0, 1, 3, 4, 6, 8, 10]


@pytest.mark.parametrize(
    "cells", [[], [{"row": 7, "col": 4, "sourceRef": "header", "isHeader": True}]]
)
def test_no_unfixed_rows_remain_empty_not_a_fabricated_data_range(cells):
    table = {"basis": "native_structure", "cells": cells}
    assert _table_payload(table)["rowRoleOrder"] == []
    assert "dataRows" not in _table_payload(table)


@pytest.mark.parametrize("format_id", ["hwpx", "xlsx"])
def test_native_source_and_every_row_survive_neutral_hint_in_all_planned_regions(format_id):
    doc = observe(native_table(format_id, count=8), format_id)
    before = deepcopy(doc)
    for region in prepare_regions(doc, context_chars=16000):
        if not region.get("tableRef"):
            continue
        ordinary = region_payload(doc, region)
        structured = expand_table_sources(structure_payload(ordinary))
        table = doc.tables[region["tableRef"]]
        expected = row_role_order(table)
        for payload in (ordinary, structured):
            view = payload["tables"][region["tableRef"]]
            assert "dataRows" not in view and view["rowRoleOrder"] == expected
        roles = structure_model_schema(doc, region)["$defs"]["StructureRecord"]["properties"][
            "rowRoles"
        ]
        assert roles["minItems"] == roles["maxItems"] == len(expected)
    # Planning may register slice tables but never rewrites original nodes/bindings.
    assert doc.nodes == before.nodes and doc.bindings == before.bindings
    for ref, table in before.tables.items():
        assert doc.tables[ref] == table


def test_general_prompt_does_not_instruct_repetition_over_unclassified_rows():
    assert "dataRows" not in SYSTEM
    assert "not confirmed\ndata" in SYSTEM
    assert "read only the rows chosen as data" in SYSTEM
    assert "Label/value forms use\nscalars instead" in SYSTEM
