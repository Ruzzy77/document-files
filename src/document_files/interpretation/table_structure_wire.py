"""Coordinate-keyed model decisions, with unchanged canonical table records.

Column geometry moves into keys; row roles follow the supplied row order.
Names, types, selected columns, source
definitions and non-fixed row roles remain model decisions, not inferred defaults.
"""

from copy import deepcopy

from ..document_model.table_headers import fixed_header_rows, observed_rows
from .compiler import CompileError


def _domain(observation, region):
    table = observation.tables[region["tableRef"]]
    cells = table["cells"]
    # Preserve the old bounded column range, including unoccupied grid positions.
    # A column can be omitted; known geometry does not require mapping every slot.
    high = max((c["col"] + c.get("colSpan", 1) for c in cells), default=0)
    rows = sorted(set(observed_rows(cells)) - fixed_header_rows(table))
    return [str(c) for c in range(high)], rows


def schema(canonical, observation, region):
    """Transform the un-factored schema before ordinary schema compaction."""
    result = deepcopy(canonical)
    columns, rows = _domain(observation, region)
    for branch in result["$defs"]["ColumnLink"]["anyOf"]:
        del branch["properties"]["column"]
        branch["required"].remove("column")
    props = result["$defs"]["StructureRecord"]["properties"]
    props["columns"] = {
        "type": "object",
        "properties": {c: {"$ref": "#/$defs/ColumnLink"} for c in columns},
        "additionalProperties": False,
        "minProperties": 1,
        "maxProperties": 200,
    }
    role = result["$defs"]["RowDecision"]["properties"]["role"]
    props["rowRoles"] = {
        "type": "array",
        "items": deepcopy(role),
        "minItems": len(rows),
        "maxItems": len(rows),
    }
    return result


def decode(value, observation, region):
    """Reject malformed/legacy shapes; do not merge or drop conflicting decisions.

    The transport JSON parser also rejects duplicate keys before this function.
    The ordinary canonical validator/compiler still checks all semantic choices.
    """
    result = deepcopy(value)
    if not isinstance(result, dict) or result.get("record") is None:
        return result
    record = result["record"]
    if not isinstance(record, dict):
        raise CompileError("table_structure_coordinate_wire_invalid")
    columns, rows = record.get("columns"), record.get("rowRoles")
    allowed_columns, required_rows = _domain(observation, region)
    if (
        not isinstance(columns, dict)
        or not 1 <= len(columns) <= 200
        or set(columns) - set(allowed_columns)
        or not isinstance(rows, list)
        or len(rows) > 1000
        or len(rows) != len(required_rows)
        or any(not isinstance(c, dict) or "column" in c for c in columns.values())
        or any(not isinstance(role, str) for role in rows)
    ):
        raise CompileError("table_structure_coordinate_wire_invalid")
    record["columns"] = [{**column, "column": int(key)} for key, column in columns.items()]
    record["rowRoles"] = [
        {"row": row, "role": role} for row, role in zip(required_rows, rows, strict=True)
    ]
    return result


def encode(value, *, row_order=None):
    """Inspection/fixture inverse; duplicate canonical positions are never folded."""
    result = deepcopy(value)
    if not isinstance(result, dict) or result.get("record") is None:
        return result
    record = result["record"]
    for name, coordinate in (("columns", "column"), ("rowRoles", "row")):
        mapped = {}
        for item in record[name]:
            key = str(item[coordinate])
            if type(item[coordinate]) is not int or key in mapped:
                raise ValueError("duplicate_or_invalid_table_coordinate")
            mapped[key] = (
                {k: deepcopy(v) for k, v in item.items() if k != coordinate}
                if name == "columns"
                else item["role"]
            )
            if name == "rowRoles" and set(item) != {"row", "role"}:
                raise ValueError("table_row_decision_has_extra_members")
        if name == "rowRoles":
            order = sorted(int(key) for key in mapped)
            if row_order is not None and order != row_order:
                raise ValueError("table_row_decisions_do_not_match_offered_order")
            record[name] = [mapped[str(row)] for row in order]
        else:
            record[name] = mapped
    return result
