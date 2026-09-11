"""One selection list and one column identifier at the applicability boundary.

The axis codec still validates canonical intersections. This layer changes only
typed selection references, never document text, values or semantic decisions.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from .scope_axis_wire import MAX_SELECTIONS, ScopeAxisWire, prepare_scope_axis_wire

VERSION = "document-files.scope-selection-wire.v2"


def _column_schema(schema, aliases):
    """Transform schema properties, not arbitrary strings resembling column IDs."""
    if isinstance(schema, list):
        for item in schema:
            _column_schema(item, aliases)
    elif isinstance(schema, dict):
        props = schema.get("properties", {})
        if "columnIds" in props:
            column = props.pop("columnIds")
            column["items"]["enum"] = [aliases[c] for c in column["items"]["enum"]]
            props["columnHandles"] = column
            schema["required"] = [
                "columnHandles" if k == "columnIds" else k for k in schema["required"]
            ]
        for item in schema.values():
            _column_schema(item, aliases)


@dataclass(frozen=True)
class ScopeSelectionWire:
    payload: dict
    contract: dict
    inverse_columns: dict
    base: ScopeAxisWire
    fingerprint: str

    @property
    def batched(self):
        return self.base.batched

    def _decision(self, value):
        task_id = value.get("taskId") if isinstance(value, dict) else None
        try:
            if (
                not isinstance(task_id, str)
                or task_id not in self.inverse_columns
                or set(value) != {"taskId", "explanation", "decision", "selections"}
            ):
                raise ValueError
            selections = value["selections"]
            if not isinstance(selections, list) or len(selections) > MAX_SELECTIONS:
                raise ValueError
            records, standalone, direct = [], [], {}
            for selection in selections:
                if not isinstance(selection, dict):
                    raise ValueError
                if selection.get("kind") == "standalone":
                    if set(selection) != {"kind", "targetHandle"}:
                        raise ValueError
                    handle = selection["targetHandle"]
                    if not isinstance(handle, str):
                        raise ValueError
                    owner = next(
                        (
                            record
                            for record, aliases in self.inverse_columns[task_id].items()
                            if handle in aliases
                        ),
                        None,
                    )
                    if owner is not None:
                        # A column handle chosen directly means every data row of that
                        # column: the same canonical intersection as a record part, so a
                        # model that names the columns need not build the nested form.
                        direct.setdefault(owner, []).append(
                            self.inverse_columns[task_id][owner][handle]
                        )
                        continue
                    if handle not in self.base.standalone[task_id]:
                        raise ValueError
                    standalone.append(handle)
                elif selection.get("kind") == "record":
                    if set(selection) != {"kind", "recordHandle", "parts"}:
                        raise ValueError
                    handle, parts = selection["recordHandle"], selection["parts"]
                    if (
                        not isinstance(handle, str)
                        or handle not in self.inverse_columns[task_id]
                        or not isinstance(parts, list)
                        or not 1 <= len(parts) <= MAX_SELECTIONS
                    ):
                        raise ValueError
                    parts = copy.deepcopy(parts)
                    for part in parts:
                        if not isinstance(part, dict):
                            raise ValueError
                        column = part.get("columnCoverage")
                        if not isinstance(column, dict) or "columnIds" in column:
                            raise ValueError
                        if "columnHandles" in column:
                            ids = column.pop("columnHandles")
                            aliases = self.inverse_columns[task_id][handle]
                            if not isinstance(ids, list) or any(
                                not isinstance(i, str) or i not in aliases for i in ids
                            ):
                                raise ValueError
                            column["columnIds"] = [aliases[i] for i in ids]
                    records.append({"recordHandle": handle, "parts": parts})
                else:
                    raise ValueError
            for owner, column_ids in direct.items():
                # Several direct columns of one record form one part of one record
                # entry; the axis codec still rejects duplicates and overlaps.
                part = {
                    "rowCoverage": {"kind": "allDataRows"},
                    "columnCoverage": {"kind": "selectedColumns", "columnIds": column_ids},
                }
                existing = next((r for r in records if r["recordHandle"] == owner), None)
                if existing is None:
                    records.append({"recordHandle": owner, "parts": [part]})
                else:
                    existing["parts"].append(part)
            axis = {k: copy.deepcopy(value[k]) for k in ("taskId", "explanation", "decision")}
            if self.base.records[task_id]:
                axis["recordScopes"] = records
            if self.base.standalone[task_id]:
                axis["targetHandles"] = standalone
            return self.base._decision(axis)
        except (ValueError, TypeError, KeyError):
            return {"taskId": copy.deepcopy(task_id), "targetHandles": None}

    def decode(self, response):
        if not self.base.batched:
            return self._decision(response)
        if (
            not isinstance(response, dict)
            or set(response) != {"decisions"}
            or not isinstance(response["decisions"], list)
            or not 1 <= len(response["decisions"]) <= 8
        ):
            raise ValueError("invalid_selection_batch")
        return {"decisions": [self._decision(v) for v in response["decisions"]]}


def prepare_scope_selection_wire(tasks):
    base = prepare_scope_axis_wire(tasks)
    payload = copy.deepcopy(base.payload)
    if base.batched:
        items = base.contract["properties"]["decisions"]["items"]
        contracts = items.get("anyOf", [items])
    else:
        contracts = [base.contract]
    schemas, inverse = [], {}
    for task, display, contract in zip(
        tasks, payload.get("tasks", [payload]), contracts, strict=True
    ):
        display["wireFormat"] = VERSION
        columns = {
            handle: {
                cid: record["columnHandles"].get(cid, f"{handle}.column{i}")
                for i, cid in enumerate(record["columnIds"])
            }
            for handle, record in base.records[task.id].items()
        }
        inverse[task.id] = {
            handle: {alias: cid for cid, alias in mapping.items()}
            for handle, mapping in columns.items()
        }
        for handle, mapping in columns.items():
            if len(inverse[task.id][handle]) != len(mapping):
                raise ValueError("ambiguous_selection_column")
        for candidate in display["candidates"]:
            handle = candidate["targetHandle"]
            if handle not in columns:
                continue
            for column in candidate["rowOptions"]["columns"]:
                column["columnHandle"] = columns[handle][column.pop("columnId")]
        variants = []
        if columns:
            items = contract["properties"]["recordScopes"]["items"]
            for schema in items.get("anyOf", [items]):
                schema = copy.deepcopy(schema)
                handle = schema["properties"]["recordHandle"]["enum"][0]
                _column_schema(schema, columns[handle])
                schema["properties"] = {"kind": {"const": "record"}, **schema["properties"]}
                schema["required"] = ["kind", *schema["required"]]
                variants.append(schema)
        standalone = base.standalone[task.id]
        # Column handles are offered as direct standalone targets as well: the wire
        # showed a model naming a column in its explanation and then being forced onto
        # the only field the standalone branch allowed.
        direct = [alias for handle in columns for alias in columns[handle].values()]
        if standalone or direct:
            variants.append(
                {
                    "type": "object",
                    "properties": {
                        "kind": {"const": "standalone"},
                        "targetHandle": {"type": "string", "enum": [*standalone, *direct]},
                    },
                    "required": ["kind", "targetHandle"],
                    "additionalProperties": False,
                }
            )
        props = {
            k: copy.deepcopy(contract["properties"][k])
            for k in ("explanation", "taskId", "decision")
        }
        props["selections"] = {
            "type": "array",
            "maxItems": MAX_SELECTIONS,
            "items": variants[0] if len(variants) == 1 else {"anyOf": variants},
        }
        schemas.append(
            {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
        )
    contract = schemas[0]
    if base.batched:
        contract = {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {"anyOf": schemas},
                }
            },
            "required": ["decisions"],
            "additionalProperties": False,
        }
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "version": VERSION,
                "baseFingerprint": base.fingerprint,
                "payload": payload,
                "contract": contract,
                "inverseColumns": inverse,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ScopeSelectionWire(payload, contract, inverse, base, fingerprint)
