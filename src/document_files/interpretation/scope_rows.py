"""Validate row ranges over compiler-owned geometry without inventing cells/values."""

from __future__ import annotations

import copy

from ..result_types import Target
from .compiler import CompileError


def row_options(region, repeat_id):
    """A bounded caller owns context delivery; this view never exposes pointers."""
    catalog = region.row_scopes.get(repeat_id)
    if catalog is None:
        return None
    definitions = {d["id"]: d for d in region.semantics}
    if any(
        column["definitionId"] not in definitions
        or definitions[column["definitionId"]].get("kind") != "field_definition"
        or definitions[column["definitionId"]].get("status") != "interpreted"
        for column in catalog["columns"].values()
    ):
        raise CompileError("scope_row_column_definition_unavailable")
    return copy.deepcopy(
        {
            "tableRef": catalog["tableRef"],
            "rowStart": catalog["rowStart"],
            "rowEnd": catalog["rowEnd"],
            "columns": [
                {
                    "columnId": cid,
                    "column": column["column"],
                    "label": definitions[column["definitionId"]]["description"],
                    "definitionRefs": definitions[column["definitionId"]]["sourceRefs"],
                }
                for cid, column in catalog["columns"].items()
            ],
            "rows": [
                {"row": int(row), "role": item["role"], "sourceRefs": item["sourceRefs"]}
                for row, item in catalog["rows"].items()
            ],
        }
    )


def resolve_row_selection(catalog, selection):
    """Select existing data-row targets; gaps/undecided roles cannot prove completion."""
    start, end = selection.rowStart, selection.rowEnd
    if not catalog["rowStart"] <= start <= end <= catalog["rowEnd"]:
        raise CompileError("scope_rows_outside_compiled_range")
    columns = selection.columnIds
    if len(columns) != len(set(columns)) or not set(columns) <= catalog["columns"].keys():
        raise CompileError("scope_rows_unknown_or_duplicate_column")
    selected_columns = columns or list(catalog["columns"])
    targets, refs, complete = [], set(), True
    for row in range(start, end + 1):
        item = catalog["rows"].get(str(row))
        if item is None or item["role"] == "unresolved":
            complete = False
            continue
        if item["role"] != "data":
            continue
        refs.update(item["sourceRefs"])
        for column in selected_columns:
            target = item["targets"].get(column)
            if target is None:
                raise CompileError("scope_rows_incomplete_compiler_mapping")
            targets.append(Target.model_validate(target).model_dump())
    if not targets:
        raise CompileError("scope_rows_have_no_data_targets")
    return targets, refs, complete
