"""Locate a failed table read without changing the model's structural choices."""

import json

from .compiler import CompileError
from .table_row_checks import BLANK_ROW_ERROR, blank_row_conflicts

_COLUMN_ERRORS = {
    "column_definition_not_above_column",
    "column_definition_conflicts_with_content",
    "column_leaf_header_missing",
}
_ROW_ERRORS = {"table_rows_outside_repeat", "repeat_row_roles_incomplete"}
_STRUCTURAL_ERRORS = _COLUMN_ERRORS | _ROW_ERRORS | {"header_cell_bound_as_value"}


class _StructureIssues(CompileError):
    def __init__(self, codes, feedback):
        super().__init__(",".join(codes))
        self.feedback = feedback


def check_structure(issues, observation, region):
    """Reject the same structural issues, retaining their safe source locations.

    Group duplicate findings without dropping distinct sources or row/cell pairs.
    Do not truncate diagnostics to fit a request: the ordinary preflight preserves
    the failed state and reports an oversized repair without making a model call.
    """
    selected = [i for i in issues if i.get("code") in _STRUCTURAL_ERRORS]
    if not selected:
        return
    codes = sorted({i["code"] for i in selected})
    table = observation.tables.get(region.get("tableRef"), {})
    cells = table.get("cells", [])
    allowed = set(region["nodeIds"]) | set(region.get("contextNodeIds", []))
    sources = {
        c["sourceRef"]
        for c in [*cells, *table.get("headerCells", [])]
        if c["sourceRef"] in allowed and c["sourceRef"] in observation.nodes
    }
    high_column = max((c["col"] + c.get("colSpan", 1) for c in cells), default=0)

    def valid_column(value):
        return type(value) is int and 0 <= value < high_column

    def valid_row(value):
        return type(value) is int and any(
            c["row"] <= value < c["row"] + c.get("rowSpan", 1) for c in cells
        )

    feedback = []
    for code in codes:
        columns, rows, locations = {}, set(), set()
        for issue in selected:
            if issue["code"] != code or issue.get("tableRef", region.get("tableRef")) != region.get(
                "tableRef"
            ):
                continue
            refs = {
                ref
                for ref in [issue.get("sourceRef"), *issue.get("sourceRefs", [])]
                if isinstance(ref, str) and ref in sources
            }
            column, row = issue.get("column"), issue.get("row")
            if code in _COLUMN_ERRORS and valid_column(column) and refs:
                columns.setdefault(column, set()).update(refs)
            elif code in _ROW_ERRORS:
                rows.update(r for r in issue.get("rows", []) if valid_row(r))
            elif code == "header_cell_bound_as_value" and refs:
                location = {"sourceRefs": sorted(refs)}
                if valid_row(row):
                    location["row"] = row
                if valid_column(column):
                    location["column"] = column
                locations.add(json.dumps(location, ensure_ascii=False, separators=(",", ":")))
        if columns:
            detail = {str(col): sorted(refs) for col, refs in sorted(columns.items())}
        elif rows:
            detail = {"rows": sorted(rows)}
        elif locations:
            detail = {"cells": [json.loads(x) for x in sorted(locations)]}
        else:
            detail = None
        feedback.append(
            code
            if detail is None
            else code + ":" + json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
        )
    raise _StructureIssues(codes, feedback)


def structure_feedback(error, observation, region):
    if isinstance(error, _StructureIssues):
        return list(error.feedback)
    if not isinstance(error, CompileError):
        return ["invalid_table_contract"]
    feedback = [str(error)]
    selection = error.selection
    if str(error) == BLANK_ROW_ERROR and isinstance(selection, dict):
        rows = selection.get("rows")
        if isinstance(rows, list):
            roles = {
                item["row"]: "blank"
                for item in rows
                if isinstance(item, dict) and type(item.get("row")) is int
            }
            table = observation.tables.get(region.get("tableRef"))
            details = blank_row_conflicts(observation, table, roles) if table else []
            if details:
                feedback.append(
                    "invalid_table_blank_rows:"
                    + json.dumps({"rows": details}, ensure_ascii=False, separators=(",", ":"))
                )
        return feedback
    if str(error) == "table_structure_formula_requires_text" and selection:
        # A column-level contract error precedes row expansion. Do not invent a
        # particular failing cell or copy the model's column label/identifier.
        feedback.append(
            "invalid_table_column_read:"
            + json.dumps(selection, ensure_ascii=False, separators=(",", ":"))
        )
        return feedback
    if str(error) != "binding_cannot_represent_requested_type" or not selection:
        return feedback
    binding_id = selection.get("bindingId")
    if binding_id not in region["bindingIds"]:
        return feedback
    binding = observation.bindings.get(binding_id)
    if binding is None or binding["sourceRef"] not in region["nodeIds"]:
        return feedback
    table = observation.tables.get(region.get("tableRef"), {})
    cells = [c for c in table.get("cells", []) if c["sourceRef"] == binding["sourceRef"]]
    if not cells:
        return feedback
    # Binding IDs are not in the structural request. Use its already offered
    # source reference instead, never a model-generated field ID/key or cell text.
    detail = {
        "sourceRef": binding["sourceRef"],
        "path": binding["path"],
        "requestedType": selection["requestedType"],
    }
    if len(cells) == 1:
        cell = cells[0]
        detail["sourceCell"] = {
            "row": cell["row"],
            "column": cell["col"],
            "rowSpan": cell.get("rowSpan", 1),
            "columnSpan": cell.get("colSpan", 1),
        }
    feedback.append(
        "invalid_table_value_selection:"
        + json.dumps(detail, ensure_ascii=False, separators=(",", ":"))
    )
    return feedback


def needs_layout_review(error, observation, region):
    """Only a source-local row/content conflict can spend a layout review.

    IDs, keys, wire shape, range and formula-mode errors belong to mapping. A
    diagnostic without verified source detail cannot justify changing row roles.
    """
    if not isinstance(error, CompileError):
        return False
    feedback = structure_feedback(error, observation, region)
    if str(error) in {"binding_cannot_represent_requested_type", BLANK_ROW_ERROR}:
        return len(feedback) > 1
    return isinstance(error, _StructureIssues) and any(
        item.startswith(
            (
                "column_definition_conflicts_with_content:",
                "header_cell_bound_as_value:",
            )
        )
        for item in feedback
    )
