"""Locate a failed table read without changing the model's structural choices."""

import json

from .compiler import CompileError


def structure_feedback(error, observation, region):
    if not isinstance(error, CompileError):
        return ["invalid_table_contract"]
    feedback = [str(error)]
    selection = error.selection
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
