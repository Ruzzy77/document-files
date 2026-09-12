"""Compact native structure decisions, expanded into the existing checked IR.

Only repetition is removed: keys, labels, types, source choices and every cell's
presence remain model decisions. Whole-block anchors denote an exact owned view,
not a guessed item boundary. Overlapping item anchors are rejected by the compiler.
"""

from __future__ import annotations

from copy import deepcopy

from pydantic import Field

from ..result_types import Contract
from .semantic_types import Disposition, Group, Presence, SourceQuote, ValueType, _compact_contract
from .table_sources import source_inventory

VERSION = "document-files.native-structure-wire.v1"


class Definition(Contract):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    valueType: ValueType
    definitionRefs: list[str] | None = Field(default=None, min_length=1, max_length=50)
    targetHandle: str | None = None


class FieldChoice(Definition):
    sourceRefs: list[str] = Field(min_length=1, max_length=50)
    status: Presence
    groupId: str | None = None


class CellState(Contract):
    status: Presence
    sourceRefs: list[str] = Field(min_length=1, max_length=50)


class Row(Contract):
    anchors: list[str | SourceQuote] = Field(min_length=1, max_length=50)
    states: list[Presence | CellState] = Field(min_length=1, max_length=200)


class Record(Contract):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    definitionRefs: list[str] = Field(min_length=1, max_length=50)
    columns: list[Definition] = Field(min_length=1, max_length=200)
    rows: list[Row] = Field(max_length=500)
    emptyAnchors: list[str | SourceQuote] = Field(default_factory=list, max_length=10)
    groupId: str | None = None
    targetHandle: str | None = None


class MeaningChoice(Contract):
    kind: str  # Constrained to the canonical meaning kinds below, not arbitrary model code.
    anchors: list[str | SourceQuote] = Field(min_length=1, max_length=100)
    status: str


class Structure(Contract):
    regionId: str
    fields: list[FieldChoice] = Field(default_factory=list, max_length=500)
    groups: list[Group] = Field(default_factory=list, max_length=200)
    records: list[Record] = Field(default_factory=list, max_length=100)
    meanings: list[MeaningChoice] = Field(default_factory=list, max_length=500)
    dispositions: list[Disposition] = Field(default_factory=list, max_length=5000)
    unresolved: list[str] = Field(default_factory=list, max_length=100)


def contract(region, metadata):
    schema = Structure.model_json_schema()
    owned = region["nodeIds"]
    refs = list(dict.fromkeys([*owned, *region.get("contextNodeIds", [])]))
    schema["properties"]["regionId"] = {"type": "string", "const": region["id"]}
    for definition in schema["$defs"].values():
        for key, prop in definition.get("properties", {}).items():
            if key in {"sourceRefs", "definitionRefs"}:
                target = prop
                if "anyOf" in prop:
                    target = next(p for p in prop["anyOf"] if p.get("type") == "array")
                target["items"] = {
                    "type": "string",
                    "enum": refs if key == "definitionRefs" else owned,
                }
            elif key == "sourceRef":
                prop.update(type="string", enum=owned)
            elif key in {"anchors", "emptyAnchors"}:
                prop["items"]["anyOf"][0] = {"type": "string", "enum": owned}
            elif key == "targetHandle":
                offered = list(metadata.get("targetHandles", {}))
                prop["anyOf"] = ([{"type": "string", "enum": offered}] if offered else []) + [
                    {"type": "null"}
                ]
    # Avoid a second independent enum when the compiler's canonical type changes.
    from .native_structure import NativeMeaning

    meaning = NativeMeaning.model_json_schema()["properties"]
    schema["$defs"]["MeaningChoice"]["properties"].update(
        kind=deepcopy(meaning["kind"]), status=deepcopy(meaning["status"])
    )
    schema["$defs"]["SourceQuote"]["properties"]["occurrence"].pop("default", None)
    return _compact_contract(schema)


def decode(value, observation, region):
    """Program IDs and source inheritance are deterministic, never value inference."""
    from .native_structure import NativeStructure

    raw = Structure.model_validate(value)
    if raw.regionId != region["id"]:
        raise ValueError("native_structure_region_mismatch")
    if (
        sum(len(r.rows) for r in raw.records) > 1000
        or sum(len(row.states) for r in raw.records for row in r.rows) > 10000
        or sum(len(row.anchors) for r in raw.records for row in r.rows) > 10000
        or sum(len(m.anchors) for m in raw.meanings) > 1000
    ):
        raise ValueError("native_structure_expansion_budget_exceeded")
    inventory = {s["sourceRef"]: s for s in source_inventory(observation, region)["sources"]}

    def quotes(anchors):
        result = []
        for anchor in anchors:
            if isinstance(anchor, str):
                if anchor not in inventory or not inventory[anchor]["text"]:
                    raise ValueError("native_structure_block_anchor_invalid")
                result.append({"sourceRef": anchor, "text": inventory[anchor]["text"]})
            else:
                result.append(anchor.model_dump(exclude_unset=True))
        return result

    def definition(item, ident, inherited):
        result = item.model_dump(exclude_unset=True)
        result.update(id=ident, definitionRefs=item.definitionRefs or inherited)
        return result

    fields = [definition(f, f"field:{i}", f.sourceRefs) for i, f in enumerate(raw.fields, 1)]
    records = []
    for i, record in enumerate(raw.records, 1):
        rid = f"record:{i}"
        columns = [
            definition(c, f"{rid}:column:{j}", record.definitionRefs)
            for j, c in enumerate(record.columns, 1)
        ]
        rows = []
        for j, row in enumerate(record.rows, 1):
            if len(row.states) != len(columns):
                raise ValueError("native_structure_row_state_count_mismatch")
            anchors = quotes(row.anchors)
            refs = list(dict.fromkeys(q["sourceRef"] for q in anchors))
            cells = [
                {
                    "columnId": column["id"],
                    **(
                        {"status": state, "sourceRefs": refs}
                        if isinstance(state, str)
                        else state.model_dump()
                    ),
                }
                for column, state in zip(columns, row.states, strict=True)
            ]
            rows.append({"id": f"{rid}:row:{j}", "sourceQuotes": anchors, "cells": cells})
        records.append(
            {
                **record.model_dump(
                    exclude={"columns", "rows", "emptyAnchors"}, exclude_unset=True
                ),
                "id": rid,
                "columns": columns,
                "rows": rows,
                "emptySourceQuotes": quotes(record.emptyAnchors),
            }
        )
    meanings = [
        {
            "id": f"meaning:{i}",
            "kind": m.kind,
            "status": m.status,
            "sourceQuotes": quotes(m.anchors),
        }
        for i, m in enumerate(raw.meanings, 1)
    ]
    return NativeStructure(
        regionId=raw.regionId,
        fields=fields,
        groups=raw.groups,
        records=records,
        meanings=meanings,
        dispositions=raw.dispositions,
        unresolved=raw.unresolved,
    )
