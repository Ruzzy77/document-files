"""Compiler-derived column read choices, not inferred field types or meanings."""

import hashlib
import json
from copy import deepcopy

from .compiler import CompileError, _read, table_cell_grid, table_value_selection
from .semantic_types import _compact_contract

VERSION = "document-files.table-read-domains.v1"
MODES = ("source", "text", "cached", "formula")
TYPES = ("string", "decimal", "integer", "number", "boolean", "null", "native")


def permitted_reads(observation, region, roles=None):
    """Remove only hard read failures over all data cells in the supplied layout.

    Absence, source conflicts, blank cells and decimal uncertainty do not prove a
    type impossible. They remain compiler-visible states, not successful reads.
    Selected regional bindings retain their exact windows; no first-row sample,
    model answer, expected value or native kind is used to infer an output type.
    """
    table, bindings = observation.tables[region["tableRef"]], observation.bindings
    cells = table["cells"]
    high = max((c["col"] + c.get("colSpan", 1) for c in cells), default=0)
    columns = list(range(high))
    if not cells:
        return {}
    conflicts = set()
    grid = table_cell_grid(
        cells,
        columns,
        min(c["row"] for c in cells),
        max(c["row"] + c.get("rowSpan", 1) - 1 for c in cells),
        conflicts=conflicts,
    )
    by_source = {}
    for bid in region["bindingIds"]:
        candidate = bindings[bid]
        by_source.setdefault(candidate["sourceRef"], {})[bid] = candidate
    rows = (
        sorted(r for r, role in roles.items() if role == "data")
        if roles is not None
        else sorted({row for row, _ in grid})
    )
    result = {}
    for column in columns:
        result[str(column)] = {}
        for mode in MODES:
            universe = [t for t in TYPES if mode != "formula" or t in {"string", "native"}]
            allowed = set(universe) if roles is not None else set()
            # Spans may repeat one source on several rows. Resolve its same
            # binding once, but never fold different cells with equal values.
            checked = {}
            for row in rows:
                if (row, column) in conflicts:
                    # Overlapping geometry is not proof of a representation
                    # failure. Keep choices; the canonical compiler still rejects
                    # a selected ambiguous cell rather than picking one source.
                    allowed = allowed & set(universe) if roles is not None else set(universe)
                    continue
                cell = grid.get((row, column))
                source = cell["sourceRef"] if cell else None
                if source in checked:
                    readable = checked[source]
                    allowed = allowed & readable if roles is not None else allowed | readable
                    continue
                readable = set(universe)
                try:
                    bid, status = table_value_selection(
                        cell, mode, by_source, observation.nodes, table
                    )
                except CompileError:
                    readable.clear()
                else:
                    if status == "present":
                        for kind in universe:
                            try:
                                _read(bindings[bid], kind, observation.nodes)
                            except CompileError:
                                readable.remove(kind)
                checked[source] = readable
                allowed = allowed & readable if roles is not None else allowed | readable
            if allowed:
                result[str(column)][mode] = [kind for kind in universe if kind in allowed]
    return result


def identity(observation, region, roles, layout_hash):
    """Bind choices to the saved layout and exact regional data-cell candidates."""
    sources = {
        cell["sourceRef"]
        for cell in observation.tables[region["tableRef"]]["cells"]
        if any(
            roles.get(row) == "data"
            for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1))
        )
    }
    value = {
        "version": VERSION,
        "layoutSHA256": layout_hash,
        "domains": permitted_reads(observation, region, roles),
        "bindings": {
            bid: observation.bindings[bid]
            for bid in sorted(region["bindingIds"])
            if observation.bindings[bid]["sourceRef"] in sources
        },
    }
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def column_schema(canonical, domains, *, reserve=False):
    """Share definitions, while each coordinate offers exact mode:type choices.

    The reserve keeps one full column body per coordinate and does not factor
    constraints: subset enums and identical-body sharing can only shorten it.
    """
    result = deepcopy(canonical)
    definition = deepcopy(result["$defs"]["ColumnLink"]["anyOf"][0])
    definition["properties"].pop("valueType")
    definition["properties"].pop("bindingMode")
    definition["required"].remove("valueType")
    result["$defs"]["ColumnDefinition"] = definition
    columns = result["$defs"]["StructureRecord"]["properties"]["columns"]["properties"]
    shared = {}
    for column, modes in domains.items():
        choices = tuple(mode + ":" + kind for mode, kinds in modes.items() for kind in kinds)
        if not choices:
            # A column with no readable combination can be omitted, not invented.
            columns[column] = {"not": {}}
            continue
        key = column if reserve else choices
        if key not in shared:
            name = "ColumnRead" + str(len(shared))
            shared[key] = name
            result["$defs"][name] = {
                "type": "object",
                "properties": {
                    "definition": {"$ref": "#/$defs/ColumnDefinition"},
                    "read": {"type": "string", "enum": list(choices)},
                },
                "required": ["definition", "read"],
                "additionalProperties": False,
            }
        columns[column] = {"$ref": "#/$defs/" + shared[key]}
    return _compact_contract(result, share=not reserve)


def decode_columns(value):
    """Expand model-only paired reads; legacy explicit pairs still face compilation."""
    result = deepcopy(value)
    columns = result.get("record", {}).get("columns", {}) if isinstance(result, dict) else {}
    if not isinstance(columns, dict):
        return result  # The ordinary coordinate decoder reports the malformed shape.
    for coordinate, column in columns.items():
        if not isinstance(column, dict) or not ({"definition", "read"} & column.keys()):
            continue
        if set(column) != {"definition", "read"} or not isinstance(column["definition"], dict):
            raise CompileError("table_column_read_wire_invalid")
        read = column["read"]
        if not isinstance(read, str) or read.count(":") != 1:
            raise CompileError("table_column_read_wire_invalid")
        mode, kind = read.split(":")
        definition = column["definition"]
        if (
            mode not in MODES
            or kind not in TYPES
            or {"column", "bindingMode", "valueType", "definition", "read"} & definition.keys()
        ):
            raise CompileError("table_column_read_wire_invalid")
        columns[coordinate] = {**definition, "bindingMode": mode, "valueType": kind}
    return result


def encode_columns(value):
    """Lossless inspection/fixture inverse; source mode is the canonical default."""
    result = deepcopy(value)
    for coordinate, column in result["record"]["columns"].items():
        item = deepcopy(column)
        read = item.pop("bindingMode", "source") + ":" + item.pop("valueType")
        result["record"]["columns"][coordinate] = {"definition": item, "read": read}
    return result
