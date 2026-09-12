"""Lossless model-facing view of already validated native structure history.

Canonical checkpoints keep the original objects. Only repeated property names in
arrays of equal-shaped objects are shared; values, order and missing keys remain.
"""

import json
from copy import deepcopy

VERSION = "column-rows.v1"
SYSTEM = """
History tables {columns,rows} encode object arrays: each row's values follow column
order. Decode nested tables likewise. Every property, value and row is preserved.
"""


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def compact(value):
    if isinstance(value, list):
        nested = [compact(v) for v in value]
        if len(value) > 1 and all(
            isinstance(row, dict) and row.keys() == value[0].keys() for row in value
        ):
            columns = list(value[0])
            table = {"columns": columns, "rows": [[row[k] for k in columns] for row in nested]}
            if _size(table) < _size(nested):
                return table
        return nested
    if isinstance(value, dict):
        # This shape cannot be a validated native record, field, quote or role.
        # Fail closed if a new input contract ever gives it another meaning.
        if value.keys() == {"columns", "rows"}:
            raise ValueError("ambiguous_native_history_object")
        return {k: compact(v) for k, v in value.items()}
    return deepcopy(value)


def expand(value):
    """Reconstruct the native objects for request verification and test clients."""
    if isinstance(value, dict):
        if value.keys() == {"columns", "rows"}:
            columns, rows = value["columns"], value["rows"]
            if (
                not isinstance(columns, list)
                or not all(isinstance(k, str) for k in columns)
                or len(set(columns)) != len(columns)
                or not isinstance(rows, list)
                or not all(isinstance(row, list) and len(row) == len(columns) for row in rows)
            ):
                raise ValueError("invalid_native_history_table")
            return [
                {key: expand(item) for key, item in zip(columns, row, strict=True)} for row in rows
            ]
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return deepcopy(value)
