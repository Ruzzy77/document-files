"""Program-owned table component IDs and collision-free proposed field names.

Apply before a new table mapping is accepted, never to an already compiled result.
Names and header references remain model choices; identities are not source references.
"""

from collections import Counter
from copy import deepcopy

from .compiler import CompileError

VERSION = "document-files.table-component-identity.v1"


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 120:
        raise CompileError("table_component_name_invalid")
    return value


def _keys(proposals):
    counts = Counter(proposals.values())
    occupied = set(proposals.values())
    result = {}
    for coordinate, name in sorted(proposals.items()):
        if counts[name] == 1:
            result[coordinate] = name
            continue
        attempt = 0
        while True:
            suffix = f"__column_{coordinate}" + (f"_{attempt}" if attempt else "")
            key = name[: 120 - len(suffix)] + suffix
            if key not in occupied:
                occupied.add(key)
                result[coordinate] = key
                break
            attempt += 1
    return result


def assign(value):
    """Assign only mechanical metadata; do not fix types, rows, targets or citations."""
    result = deepcopy(value)
    record = result["record"]
    columns = record["columns"]
    coordinates = [column["column"] for column in columns]
    if any(type(c) is not int or c < 0 for c in coordinates) or (
        len(set(coordinates)) != len(columns)
    ):
        raise CompileError("duplicate_or_invalid_table_coordinate")
    # Legacy in-process proposals may still include IDs. Validate their shape,
    # but do not let a shared header ID become shared internal field identity.
    for item in [record, *columns]:
        if "id" in item:
            _name(item["id"])
    proposed = {column["column"]: _name(column["key"]) for column in columns}
    keys = _keys(proposed)
    record["id"] = "record"
    receipt = {
        "version": VERSION,
        "recordId": "record",
        "recordKey": _name(record["key"]),
        "columns": {},
    }
    for column in columns:
        coordinate = column["column"]
        column.update(id=f"column_{coordinate}", key=keys[coordinate])
        receipt["columns"][str(coordinate)] = {
            "id": column["id"],
            "key": column["key"],
            "proposedKey": proposed[coordinate],
        }
    return result, receipt


def validate(receipt, frozen):
    """Replay name allocation before resuming accepted structure or meaning."""
    if not isinstance(receipt, dict) or len(frozen.repeats) != 1:
        raise ValueError("invalid_table_component_identity")
    repeat = frozen.repeats[0]
    proposal = {"record": repeat.model_dump()}
    try:
        for column in proposal["record"]["columns"]:
            column["key"] = receipt["columns"][str(column["column"])]["proposedKey"]
        assigned, expected = assign(proposal)
        if expected != receipt or assigned["record"] != repeat.model_dump():
            raise ValueError("invalid_table_component_identity")
    except (KeyError, TypeError, CompileError) as exc:
        raise ValueError("invalid_table_component_identity") from exc
