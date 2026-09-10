"""Exclusive table applicability decisions, converted to the unchanged Meaning IR.

The model chooses columns, the whole record, bounded rows (optionally intersected
with columns), or unresolved. Record membership is never a second scope qualifier.
No unit, condition, field name or value is inferred by these conversions.
"""

from __future__ import annotations

import copy
from typing import Literal

from pydantic import Field, TypeAdapter

from ..result_types import Contract
from .compiler import CompileError
from .semantic_types import Meaning

LEGACY_SCOPE_FIELDS = {"fieldIds", "groupIds", "repeatIds", "rowStart", "rowEnd"}


class ColumnScope(Contract):
    kind: Literal["columns"]
    columnIds: list[str] = Field(min_length=1, max_length=200)


class RecordScope(Contract):
    kind: Literal["record"]


class RowScope(Contract):
    kind: Literal["rows"]
    rowStart: int = Field(ge=0)
    rowEnd: int = Field(ge=0)
    columnIds: list[str] = Field(
        max_length=200, description="Empty means all columns in these rows"
    )


class UnresolvedScope(Contract):
    kind: Literal["unresolved"]


_SCOPE = TypeAdapter(ColumnScope | RecordScope | RowScope | UnresolvedScope)


def _record(frozen):
    if len(frozen.repeats) != 1:
        raise CompileError("table_scope_requires_one_frozen_record")
    return frozen.repeats[0]


def _columns(ids, record):
    if len(ids) != len(set(ids)) or not set(ids) <= {column.id for column in record.columns}:
        raise CompileError("table_scope_unknown_or_duplicate_column")
    return list(ids)


def meaning_wire_schema(base, frozen):
    """Reuse bounded Meaning metadata/source choices; replace only applicability.

    Returns a schema with its own $defs. The enclosing response must lift those
    definitions to its root before inserting this object as its Meaning definition.
    """
    record = _record(frozen)
    scope = _SCOPE.json_schema()
    definitions = scope.pop("$defs")
    ids = [column.id for column in record.columns]
    for name in ("ColumnScope", "RowScope"):
        choice = definitions[name]["properties"]["columnIds"]
        choice["items"] = {"type": "string", "enum": ids}
        choice["maxItems"] = len(ids)
        choice["uniqueItems"] = True
    for name in ("rowStart", "rowEnd"):
        definitions["RowScope"]["properties"][name].update(
            minimum=record.rowStart, maximum=record.rowEnd
        )
    result = copy.deepcopy(base)
    for name in LEGACY_SCOPE_FIELDS:
        result["properties"].pop(name, None)
    result["required"] = [
        name for name in result.get("required", []) if name not in LEGACY_SCOPE_FIELDS
    ]
    result["required"].append("scope")
    result["properties"]["scope"] = scope
    result["$defs"] = result.get("$defs", {}) | definitions
    return result


def meaning_from_wire(value, frozen):
    record = _record(frozen)
    if not isinstance(value, dict) or "scope" not in value or LEGACY_SCOPE_FIELDS & value.keys():
        raise CompileError("table_meaning_requires_exclusive_scope")
    scope = _SCOPE.validate_python(value["scope"])
    metadata = {key: item for key, item in value.items() if key != "scope"}
    applicability = {}
    if isinstance(scope, ColumnScope):
        applicability["fieldIds"] = _columns(scope.columnIds, record)
    elif isinstance(scope, RecordScope):
        applicability["repeatIds"] = [record.id]
    elif isinstance(scope, RowScope):
        if not record.rowStart <= scope.rowStart <= scope.rowEnd <= record.rowEnd:
            raise CompileError("table_scope_rows_outside_frozen_record")
        applicability = {
            "repeatIds": [record.id],
            "fieldIds": _columns(scope.columnIds, record),
            "rowStart": scope.rowStart,
            "rowEnd": scope.rowEnd,
        }
    # An unresolved scope leaves applicability empty. Preserve the independent
    # content status; the compiler marks the combined result uncertain until
    # applicability is resolved, without erasing the original distinction.
    return Meaning.model_validate(metadata | applicability)


def meaning_to_wire(meaning, frozen):
    """Stateless repair sees the same exclusive language as its output contract."""
    record = _record(frozen)
    if meaning.groupIds or (meaning.repeatIds and meaning.repeatIds != [record.id]):
        raise CompileError("table_meaning_ir_outside_frozen_record")
    columns = _columns(meaning.fieldIds, record)
    if meaning.rowStart is not None or meaning.rowEnd is not None:
        if meaning.rowStart is None or meaning.rowEnd is None or meaning.repeatIds != [record.id]:
            raise CompileError("table_meaning_ir_incomplete_row_scope")
        scope = {
            "kind": "rows",
            "rowStart": meaning.rowStart,
            "rowEnd": meaning.rowEnd,
            "columnIds": columns,
        }
    elif meaning.repeatIds:
        if columns:
            raise CompileError("table_meaning_ir_parent_child_union_forbidden")
        scope = {"kind": "record"}
    elif columns:
        scope = {"kind": "columns", "columnIds": columns}
    else:
        scope = {"kind": "unresolved"}
    result = {
        key: item for key, item in meaning.model_dump().items() if key not in LEGACY_SCOPE_FIELDS
    }
    if not result.get("sourceRanges"):
        result.pop("sourceRanges", None)
    result["scope"] = scope
    # Revalidate bounds and metadata without guessing a replacement scope.
    result["status"] = meaning_from_wire(result, frozen).status
    return result
