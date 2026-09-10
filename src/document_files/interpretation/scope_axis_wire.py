"""Internal row/column applicability wire over every offered compiler candidate.

Not yet the engine's default wire. The codec translates selections, never values,
literal document text or meaning. Canonical source binding is a separate step.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

from .scope_reference_wire import prepare_scope_wire

VERSION = "document-files.scope-axis-wire.v1"
MAX_SELECTIONS = 100


def _object(properties):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _variants(items):
    return items[0] if len(items) == 1 else {"anyOf": items}


def _enum(values):
    return {"type": "string", "enum": list(values)}


def _column_schema(record, *, whole_rows):
    columns = record["columnHandles"] if whole_rows else record["columnIds"]
    choices = [_object({"kind": {"const": "allMappedColumns"}})]
    if columns:
        choices.append(
            _object(
                {
                    "kind": {"const": "selectedColumns"},
                    "columnIds": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": len(columns),
                        "items": _enum(columns),
                    },
                }
            )
        )
    if record["groups"]:
        choices.append(
            _object(
                {
                    "kind": {"const": "headerGroup"},
                    "groupHandle": _enum(record["groups"]),
                }
            )
        )
    return _variants(choices)


def _record_schema(handle, record):
    all_rows = _object({"kind": {"const": "allDataRows"}})
    ranges = []
    if record["rows"]:
        ranges.append(
            _object(
                {
                    "kind": {"const": "rowRange"},
                    "rowStartRef": _enum(record["rows"]),
                    "rowEndRef": _enum(record["rows"]),
                }
            )
        )
    if record["hasGaps"]:
        # A missing coordinate is not a new observation. Retain the old ability
        # to select a range ending in a gap without enumerating millions of rows.
        coordinate = {
            "type": "integer",
            "minimum": record["rowStart"],
            "maximum": record["rowEnd"],
        }
        ranges.append(
            _object(
                {
                    "kind": {"const": "sourceRowRange"},
                    "rowStart": coordinate,
                    "rowEnd": copy.deepcopy(coordinate),
                }
            )
        )
    column_all = _column_schema(record, whole_rows=True)
    column_range = _column_schema(record, whole_rows=False)
    if column_all == column_range:
        part = _object(
            {"rowCoverage": _variants([all_rows, *ranges]), "columnCoverage": column_all}
        )
    else:
        # Bounded discovery may include a record and its row options but omit a
        # standalone column definition. Never invent its target handle or drop
        # the remaining row/column capabilities to fit the new wire.
        choices = [_object({"rowCoverage": all_rows, "columnCoverage": column_all})]
        if ranges:
            choices.append(
                _object({"rowCoverage": _variants(ranges), "columnCoverage": column_range})
            )
        part = _variants(choices)
    return _object(
        {
            "recordHandle": _enum([handle]),
            "parts": {"type": "array", "minItems": 1, "maxItems": 100, "items": part},
        }
    )


def _decision_schema(task_id, records, standalone, base_properties):
    properties = {
        name: copy.deepcopy(base_properties[name]) for name in ("explanation", "taskId", "decision")
    }
    properties["taskId"]["enum"] = [task_id]
    if records:
        properties["recordScopes"] = {
            "type": "array",
            "maxItems": min(len(records), MAX_SELECTIONS),
            "items": _variants([_record_schema(h, r) for h, r in records.items()]),
        }
    if standalone:
        properties["targetHandles"] = {
            "type": "array",
            "maxItems": min(len(standalone), MAX_SELECTIONS),
            "items": _enum(standalone),
        }
    return _object(properties)


def _decode_part(part, handle, record):
    if not isinstance(part, dict) or set(part) != {"rowCoverage", "columnCoverage"}:
        raise ValueError("invalid_axis_part")
    rows, columns = part["rowCoverage"], part["columnCoverage"]
    if not isinstance(rows, dict) or not isinstance(columns, dict):
        raise ValueError("invalid_axis_coverage")
    row_kind, column_kind = rows.get("kind"), columns.get("kind")
    whole_rows = row_kind == "allDataRows"
    if column_kind == "allMappedColumns" and set(columns) == {"kind"}:
        handles, column_ids = [handle], []
    elif column_kind == "selectedColumns" and set(columns) == {"kind", "columnIds"}:
        column_ids = columns["columnIds"]
        offered = record["columnHandles"] if whole_rows else record["columnIds"]
        if (
            not isinstance(column_ids, list)
            or not 1 <= len(column_ids) <= len(offered)
            or any(not isinstance(c, str) or c not in offered for c in column_ids)
            or len(column_ids) != len(set(column_ids))
        ):
            raise ValueError("unknown_or_duplicate_axis_column")
        handles = [record["columnHandles"][c] for c in column_ids] if whole_rows else []
    elif column_kind == "headerGroup" and set(columns) == {"kind", "groupHandle"}:
        group = columns["groupHandle"]
        if not isinstance(group, str) or group not in record["groups"]:
            raise ValueError("foreign_axis_header_group")
        handles, column_ids = [group], record["groups"][group]
    else:
        raise ValueError("invalid_axis_columns")
    if whole_rows and set(rows) == {"kind"}:
        return handles, []
    if row_kind == "rowRange" and set(rows) == {"kind", "rowStartRef", "rowEndRef"}:
        refs = [rows["rowStartRef"], rows["rowEndRef"]]
        if any(not isinstance(r, str) or r not in record["rows"] for r in refs):
            raise ValueError("foreign_axis_row_reference")
        start, end = [record["rows"][r] for r in refs]
    elif (
        row_kind == "sourceRowRange"
        and record["hasGaps"]
        and set(rows) == {"kind", "rowStart", "rowEnd"}
    ):
        start, end = rows["rowStart"], rows["rowEnd"]
    else:
        raise ValueError("invalid_axis_rows")
    if (
        type(start) is not int
        or type(end) is not int
        or not record["rowStart"] <= start <= end <= record["rowEnd"]
    ):
        raise ValueError("invalid_axis_row_range")
    return [], [
        {
            "targetHandle": handle,
            "rowStart": start,
            "rowEnd": end,
            "columnIds": copy.deepcopy(column_ids),
        }
    ]


@dataclass(frozen=True)
class ScopeAxisWire:
    payload: dict
    contract: dict
    targets: dict
    records: dict
    standalone: dict
    fingerprint: str
    batched: bool

    def _decision(self, value):
        task_id = value.get("taskId") if isinstance(value, dict) else None
        try:
            if not isinstance(task_id, str) or task_id not in self.records:
                raise ValueError("unknown_axis_task")
            records, standalone = self.records[task_id], self.standalone[task_id]
            keys = {"explanation", "taskId", "decision"}
            if records:
                keys.add("recordScopes")
            if standalone:
                keys.add("targetHandles")
            # No old sourceRefs field is accepted, even if empty: this contract
            # cannot be used to repair or replace a model's failed citations.
            if set(value) != keys:
                raise ValueError("invalid_axis_response")
            handles = value.get("targetHandles", [])
            if (
                not isinstance(handles, list)
                or len(handles) > MAX_SELECTIONS
                or any(not isinstance(h, str) or h not in standalone for h in handles)
            ):
                raise ValueError("foreign_axis_target")
            handles, selections = list(handles), []
            items = value.get("recordScopes", [])
            if not isinstance(items, list) or len(items) > len(records):
                raise ValueError("invalid_axis_records")
            seen, part_count = set(), 0
            for item in items:
                if not isinstance(item, dict) or set(item) != {"recordHandle", "parts"}:
                    raise ValueError("invalid_axis_record")
                handle = item["recordHandle"]
                if not isinstance(handle, str) or handle not in records or handle in seen:
                    raise ValueError("foreign_or_duplicate_axis_record")
                seen.add(handle)
                parts = item["parts"]
                if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_SELECTIONS:
                    raise ValueError("invalid_axis_parts")
                part_count += len(parts)
                if part_count > MAX_SELECTIONS:
                    raise ValueError("axis_selection_budget_exceeded")
                for part in parts:
                    hs, rs = _decode_part(part, handle, records[handle])
                    handles.extend(hs)
                    selections.extend(rs)
                    if len(handles) + len(selections) > MAX_SELECTIONS:
                        raise ValueError("axis_selection_budget_exceeded")
            result = {k: copy.deepcopy(value[k]) for k in ("explanation", "taskId", "decision")}
            result.update(
                targetHandles=[self.targets[h] for h in handles],
                rowSelections=[
                    {**r, "targetHandle": self.targets[r["targetHandle"]]} for r in selections
                ],
                sourceRefs=[],
            )
            return result
        except (ValueError, TypeError, KeyError):
            # Preserve the task ID so later duplicate detection invalidates all
            # copies, but keep independent siblings available for validation.
            return {"taskId": copy.deepcopy(task_id), "targetHandles": None}

    def decode(self, response):
        if not self.batched:
            return self._decision(response)
        if (
            not isinstance(response, dict)
            or set(response) != {"decisions"}
            or not isinstance(response["decisions"], list)
            or not 1 <= len(response["decisions"]) <= 8
        ):
            raise ValueError("invalid_axis_batch")
        return {"decisions": [self._decision(v) for v in response["decisions"]]}


def prepare_scope_axis_wire(tasks):
    """Represent all discovered candidates, including bounded and mixed tasks."""
    base = prepare_scope_wire(tasks)
    base_properties = (base.contract["$defs"]["ScopeDecision"] if base.batched else base.contract)[
        "properties"
    ]
    payload = copy.deepcopy(base.payload)
    records_by_task, standalone_by_task, schemas = {}, {}, []
    record_numbers = {}
    for task, display in zip(tasks, payload.get("tasks", [payload]), strict=True):
        candidates = display["candidates"]
        records, represented, boundaries = {}, set(), []
        for public in candidates:
            if "rowOptions" not in public:
                continue
            handle = public["targetHandle"]
            private = task.target_map[base.targets[handle]]
            scope = private["rowScope"]
            mapping, options = scope["mapping"], public["rowOptions"]
            if (
                options["tableRef"] != mapping["tableRef"]
                or options["rowStart"] != mapping["rowStart"]
                or options["rowEnd"] != mapping["rowEnd"]
                or {c["columnId"] for c in options["columns"]} != set(mapping["columns"])
            ):
                raise ValueError("axis_record_mapping_mismatch")
            record_numbers.setdefault(handle, len(record_numbers) + 1)
            number = record_numbers[handle]
            columns, groups = {}, {}
            for cid, column in mapping["columns"].items():
                matches = [
                    c["targetHandle"]
                    for c in candidates
                    if task.target_map[base.targets[c["targetHandle"]]].get("regionId")
                    == private["regionId"]
                    and task.target_map[base.targets[c["targetHandle"]]]
                    .get("definition", {})
                    .get("id")
                    == column["definitionId"]
                ]
                if len(matches) > 1:
                    raise ValueError("ambiguous_axis_column")
                if matches:
                    columns[cid] = matches[0]
            for candidate in candidates:
                group_handle = candidate["targetHandle"]
                target = task.target_map[base.targets[group_handle]]
                if (
                    target.get("regionId") != private["regionId"]
                    or target.get("headerGroup", {}).get("repeatId") != scope["repeatId"]
                ):
                    continue
                members = {d["id"] for d in target["definitions"]}
                member_columns = [
                    cid for cid, c in mapping["columns"].items() if c["definitionId"] in members
                ]
                if len(member_columns) != len(members) or not members:
                    raise ValueError("axis_group_mapping_mismatch")
                groups[group_handle] = member_columns
            row_map, data_number, seen = {}, 0, set()
            original_rows = options.pop("rows")
            expected_rows = [
                {"row": int(r), "role": value["role"], "sourceRefs": value["sourceRefs"]}
                for r, value in mapping["rows"].items()
            ]
            if original_rows != expected_rows:
                raise ValueError("axis_row_options_mismatch")
            if [r["row"] for r in original_rows] != sorted(r["row"] for r in original_rows):
                raise ValueError("axis_row_order_mismatch")
            for row in original_rows:
                coordinate = row["row"]
                if (
                    type(coordinate) is not int
                    or not mapping["rowStart"] <= coordinate <= mapping["rowEnd"]
                    or coordinate in seen
                ):
                    raise ValueError("invalid_axis_observed_row")
                seen.add(coordinate)
                ref = f"@record{number}.sourceRow{coordinate}"
                extra = {}
                if row["role"] == "data":
                    data_number += 1
                    ref = f"@record{number}.dataRow{data_number}"
                    extra["dataRowNumberInFragment"] = data_number
                boundaries.append(
                    {
                        "rowRef": ref,
                        "targetHandle": handle,
                        "tableRef": options["tableRef"],
                        **copy.deepcopy(row),
                        **extra,
                    }
                )
                row_map[ref] = coordinate
            options["rowBoundaryRefs"] = list(row_map)
            records[handle] = {
                "columnIds": list(mapping["columns"]),
                "columnHandles": columns,
                "groups": groups,
                "rows": row_map,
                "rowStart": mapping["rowStart"],
                "rowEnd": mapping["rowEnd"],
                "hasGaps": len(seen) != mapping["rowEnd"] - mapping["rowStart"] + 1,
            }
            represented.update([handle, *columns.values(), *groups])
        standalone = [c["targetHandle"] for c in candidates if c["targetHandle"] not in represented]
        if boundaries or records:
            display["rowBoundaryCandidates"] = boundaries
            display["rowNumbering"] = {
                "row": "Zero-based source table coordinate, including non-data rows.",
                "dataRowNumberInFragment": (
                    "One-based order of compiler-classified data rows in this source fragment "
                    "only; not a global document record number."
                ),
                "rowRef": (
                    "Opaque selection reference. Choose rowStartRef and rowEndRef from the same "
                    "targetHandle; use the same reference for one row."
                ),
            }
        records_by_task[task.id], standalone_by_task[task.id] = records, standalone
        schemas.append(_decision_schema(task.id, records, standalone, base_properties))
    contract = schemas[0]
    if len(tasks) > 1:
        contract = _object(
            {
                "decisions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": _variants(schemas),
                }
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "version": VERSION,
                "tasks": [(t.id, t.fingerprint) for t in tasks],
                "payload": payload,
                "contract": contract,
                "targets": base.targets,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ScopeAxisWire(
        payload,
        contract,
        base.targets,
        records_by_task,
        standalone_by_task,
        fingerprint,
        base.batched,
    )
