"""Deterministically compile semantic links into v1 schema, values and evidence.

The compiler, not a language model, owns scalar reads, repeated-row traversal,
JSON Pointers and assertion IDs. It never executes document conditions.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator
from referencing import Registry

from ..document_model.table_headers import declared_header, observed_rows
from ..result_types import Assertion, Evidence, SourceBinding, Target
from .accounting import bound_node_dispositions
from .bindings import resolve
from .semantic_types import RegionInterpretation
from .table_revisions import meaning_revision
from .table_sources import SourceReviewError, review_ranges, source_inventory
from .validation import check_schema, escape, leaves, pointer, schema_definitions


class CompileError(ValueError):
    """Only product-written diagnostics; never raw model values or document text."""


def target_catalog(schema: dict | None) -> dict[str, dict]:
    """Assign handles to schema positions; models never calculate destination pointers."""
    if schema is None:
        return {}
    check_schema(schema)
    catalog = {}

    def visit(item, tokens, schema_path, depth=0):
        if depth > 40:
            raise CompileError("target_schema_depth_exceeded")
        if not isinstance(item, dict):
            return
        if "$ref" in item:
            item = pointer(schema, item["$ref"][1:])
        handle = f"t{len(catalog)}"
        catalog[handle] = {"tokens": tokens, "schemaPath": schema_path, "schema": item}
        for key, child in item.get("properties", {}).items():
            visit(child, [*tokens, key], f"{schema_path}/properties/{escape(key)}", depth + 1)
        if isinstance(item.get("items"), dict):
            visit(item["items"], [*tokens, "*"], f"{schema_path}/items", depth + 1)

    visit(schema, [], "")
    return catalog


def _view_text(nodes, region, source_ref):
    """Read precisely the source window shown for this regional interpretation."""
    text = nodes[source_ref].get("text", "")
    window = region.get("nodeViews", {}).get(source_ref)
    return text[window["start"] : window["end"]] if window is not None else text


def _object():
    return {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _schema(value_type, status):
    kind = {"decimal": "string", "native": "string"}.get(value_type, value_type)
    if status in {"absent", "unreadable", "uncertain"}:
        return {"type": [kind, "null"]}
    return {"type": kind}


def _source_binding(candidate, representation):
    return SourceBinding(
        **{k: candidate.get(k) for k in ("sourceRef", "path", "start", "end")},
        representation=representation,
    )


def _read(candidate, value_type, nodes):
    representation = {"string": "text", "decimal": "text"}.get(value_type, value_type)
    binding = _source_binding(candidate, representation)
    try:
        value, raw = resolve(binding, nodes)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError):
        raise CompileError("binding_cannot_represent_requested_type") from None
    return value, raw, binding


def _decimal_literal(raw):
    return (
        re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", raw.strip())
        is not None
    )


def preferred_binding(bindings, source_ref, mode="source"):
    items = [(key, value) for key, value in bindings.items() if value["sourceRef"] == source_ref]
    # Equivalent candidate containers may arrive in set/dictionary order. Prefer
    # the whole original text over a later annotation span, then stable IDs.
    items.sort(
        key=lambda item: (
            item[1].get("start") is not None,
            item[1].get("start") or 0,
            -(item[1].get("end") or 0),
            item[0],
        )
    )
    if mode == "formula":
        paths = ["/semantic/value/formula"]
    elif mode == "cached":
        paths = ["/semantic/value/cachedValue/raw", "/semantic/value/cachedValue/value"]
    elif mode == "text":
        paths = ["/text"]
    else:
        paths = ["/semantic/value/raw", "/semantic/value/value", "/text"]
    for path in paths:
        for key, candidate in items:
            if candidate["path"] == path and (
                path != "/text" or candidate.get("candidateRole") in {"content", "cell"}
            ):
                return key
    return None


def _column_header(cell, table, roles):
    if cell.get("headerScope") in {"row", "rowgroup"}:
        return False
    role = roles.get(cell["row"])
    if role == "header":
        return True
    # Native labels in subtotal/note/mixed rows are not automatically column
    # headings. Header context outside a bounded data view keeps its declaration.
    return declared_header(cell, table) and role is None


@dataclass
class CompiledRegion:
    id: str
    data: object = field(default_factory=dict)
    has_data: bool = False
    schema: dict = field(default_factory=_object)
    semantics: list[dict] = field(default_factory=list)
    schema_evidence: list[dict] = field(default_factory=list)
    value_evidence: list[dict] = field(default_factory=list)
    semantic_details: list[dict] = field(default_factory=list)
    value_observations: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    consumed_bindings: set[str] = field(default_factory=set)
    dispositions: list[dict] = field(default_factory=list)
    repeat_paths: dict[str, dict] = field(default_factory=dict)
    header_value_bindings: set[str] = field(default_factory=set)
    dropped_fields: dict[str, str] = field(default_factory=dict)
    meaning_review: dict | None = None


def compile_region(ir: RegionInterpretation, observation, region: dict, *, target_schema=None):
    if ir.regionId != region["id"]:
        raise CompileError("region_id_mismatch")
    nodes, bindings, tables = observation.nodes, observation.bindings, observation.tables
    allowed = set(region["nodeIds"]) | set(region.get("contextNodeIds", []))
    candidates = set(region["bindingIds"])
    by_source = {}
    for bid in candidates:
        candidate = bindings[bid]
        by_source.setdefault(candidate["sourceRef"], {})[bid] = candidate
    catalog = target_catalog(target_schema)
    out = CompiledRegion(ir.regionId)
    if ir.tableMeaningState is not None:
        state = ir.tableMeaningState
        if len(ir.repeats) != 1 or not region.get("tableRef"):
            raise CompileError("table_meaning_review_requires_frozen_table")
        try:
            inventory = source_inventory(observation, region)
            if state.inventorySHA256 != inventory["sha256"]:
                raise CompileError("table_meaning_source_inventory_changed")
            if state.revisionSHA256 != meaning_revision(ir):
                raise CompileError("table_meaning_revision_mismatch")
            for meaning in ir.meanings:
                if meaning.sourceRefs != list(
                    dict.fromkeys(s.sourceRef for s in meaning.sourceRanges)
                ):
                    raise CompileError("table_meaning_source_references_mismatch")
            out.meaning_review = review_ranges(
                # Literal-span coverage is independent of applicability status.
                # Scope/content uncertainty remains in semantic details and its
                # own issues; a later scope decision must not leave a stale
                # source-review failure for an already quoted phrase.
                [m.model_dump() | {"status": "interpreted"} for m in ir.meanings],
                [r.model_dump() for r in state.sourceReviews],
                inventory,
            ) | {
                "version": state.version,
                "regionId": ir.regionId,
                "revisionSHA256": state.revisionSHA256,
            }
        except SourceReviewError as exc:
            raise CompileError(str(exc)) from None
        for key, code in (
            ("unreviewed", "table_meaning_source_unreviewed"),
            ("unresolved", "table_meaning_source_unresolved"),
        ):
            out.issues.extend({"code": code, "sourceRef": ref} for ref in out.meaning_review[key])
    elif any(meaning.sourceRanges for meaning in ir.meanings):
        raise CompileError("table_meaning_review_required_for_source_ranges")
    prefix = ir.regionId + ":"
    groups = {g.id: g for g in ir.groups}
    if len(groups) != len(ir.groups):
        raise CompileError("duplicate_group_id")
    ids = [f.id for f in ir.fields] + [r.id for r in ir.repeats] + list(groups)
    ids += [f.id for r in ir.repeats for f in r.columns]
    if len(ids) != len(set(ids)):
        raise CompileError("duplicate_component_id")
    entity_targets: dict[str, list[Target]] = {}
    row_targets: dict[str, dict[int, list[Target]]] = {}
    header_definition_sources = set()
    group_paths = {None: ([], "")}

    def refs(source_refs):
        if not source_refs or not set(source_refs) <= allowed:
            raise CompileError("source_reference_not_in_region_context")
        return list(dict.fromkeys(source_refs))

    def location(key, group_id, handle=None):
        if handle is not None:
            if handle not in catalog or "*" in catalog[handle]["tokens"]:
                raise CompileError("target_handle_not_assignable")
            return list(catalog[handle]["tokens"]), catalog[handle]["schemaPath"]
        if target_schema is not None:
            raise CompileError("target_handle_required")
        if group_id not in group_paths:
            raise CompileError("unknown_or_cyclic_group")
        tokens, schema_path = group_paths[group_id]
        return [*tokens, key], f"{schema_path}/properties/{escape(key)}"

    def assign(tokens, value, schema):
        if any(not isinstance(t, str) for t in tokens):
            raise CompileError("invalid_generated_target")
        if not tokens:
            if out.has_data:
                raise CompileError("duplicate_data_property")
            out.data, out.schema, out.has_data = value, schema, True
            return ""
        if not isinstance(out.data, dict) or out.schema.get("type") != "object":
            raise CompileError("target_shape_conflict")
        data, shape = out.data, out.schema
        for token in tokens[:-1]:
            if token not in data:
                data[token], shape["properties"][token] = {}, _object()
                shape["required"].append(token)
            if (
                not isinstance(data[token], dict)
                or shape["properties"][token].get("type") != "object"
            ):
                raise CompileError("target_shape_conflict")
            data, shape = data[token], shape["properties"][token]
        key = tokens[-1]
        if key in data:
            raise CompileError("duplicate_data_property")
        data[key], shape["properties"][key] = value, schema
        shape["required"].append(key)
        out.has_data = True
        return "/" + "/".join(escape(t) for t in tokens)

    def definition(entity_id, label, source_refs, schema_path, data_targets):
        source_refs = refs(source_refs)
        sid = prefix + entity_id
        schema_target = Target(space="dataSchema", path=schema_path)
        out.semantics.append(
            Assertion(
                id=sid,
                kind="field_definition",
                description=label,
                targets=[schema_target],
                scope=data_targets or [schema_target],
                sourceRefs=source_refs,
                basis="ai_interpreted",
                status="interpreted",
            ).model_dump()
        )
        out.schema_evidence.append(
            Evidence(
                target=schema_target,
                sourceRefs=source_refs,
                semanticIds=[sid],
                raw="",
                status="present",
                transformation="field_definition_from_observed_context",
            ).model_dump()
        )
        entity_targets[entity_id] = data_targets
        return sid

    pending = list(ir.groups)
    for _ in range(len(pending) + 1):
        remaining = []
        for group in pending:
            if group.parentId not in group_paths:
                remaining.append(group)
                continue
            tokens, sp = location(group.key, group.parentId, group.targetHandle)
            path = assign(tokens, {}, _object())
            group_paths[group.id] = (tokens, sp)
            definition(
                group.id, group.label, group.sourceRefs, sp, [Target(space="data", path=path)]
            )
        if not remaining:
            break
        if len(remaining) == len(pending):
            raise CompileError("unknown_or_cyclic_group")
        pending = remaining

    def scalar(
        binding_id,
        value_type,
        status,
        source_refs,
        target,
        semantic_id,
        transformation="source_binding",
    ):
        source_refs = refs(source_refs)
        binding = None
        raw = ""
        if status in {"present", "blank"}:
            if binding_id not in candidates:
                raise CompileError("binding_not_in_region")
            candidate = bindings[binding_id]
            if candidate.get("candidateStatus") == "unresolved_conflict":
                raise CompileError("binding_has_unresolved_observation_conflict")
            source_refs = list(dict.fromkeys([candidate["sourceRef"], *source_refs]))
            value, raw, binding = _read(candidate, value_type, nodes)
            if (status == "blank") != (raw == ""):
                raise CompileError("blank_status_disagrees_with_observation")
            if value_type == "decimal" and not _decimal_literal(raw):
                # Keep source text/evidence without accepting a header, localised
                # separator or unit-bearing string as a verified decimal value.
                value, status, binding = None, "uncertain", None
                out.issues.append(
                    {
                        "code": "decimal_format_unresolved",
                        "bindingId": binding_id,
                        "sourceRef": candidate["sourceRef"],
                        "target": target.model_dump(),
                    }
                )
            else:
                out.consumed_bindings.add(binding_id)
            native = nodes[candidate["sourceRef"]].get("semantic", {}).get("value")
            if native:
                out.value_observations.append(
                    {
                        "target": target.model_dump(),
                        "sourceRef": candidate["sourceRef"],
                        "native": copy.deepcopy(native),
                    }
                )
        else:
            if binding_id is not None:
                raise CompileError("nonpresent_value_cannot_bind_scalar")
            value = None
            if status != "absent":
                out.issues.append(
                    {"code": "value_not_resolved", "target": target.model_dump(), "status": status}
                )
        out.value_evidence.append(
            Evidence(
                target=target,
                sourceRefs=source_refs,
                semanticIds=[semantic_id],
                raw=raw,
                status=status,
                transformation=transformation if binding else "explicit_missingness",
                binding=binding,
            ).model_dump()
        )
        shape = _schema(value_type, status)
        if value_type == "native" and value is None and status == "present":
            shape["type"] = "null"
        if value_type == "native" and value is not None:
            shape["type"] = (
                "boolean"
                if isinstance(value, bool)
                else "integer"
                if isinstance(value, int)
                else "number"
                if isinstance(value, float)
                else "string"
            )
        return value, shape

    # Cells that a repeat reads as record values are enumerated by the program. A
    # scalar field on such a cell duplicates that reading, possibly under another
    # label; the record reading is kept and the drop is recorded in the ledger.
    record_bindings = {}
    # Declared header cells above a mapped column already define that column. A
    # scalar field defined only by such headers and reading no cell is a duplicate
    # of the column definition, not a form value.
    mapped_headers = set()
    for repeat in ir.repeats:
        table = tables.get(repeat.tableRef)
        if not table:
            continue
        roles = {r.row: r.role for r in repeat.rowRoles}
        mapped = {c.column for c in repeat.columns}
        for cell in [*table["cells"], *table.get("headerCells", [])]:
            if _column_header(cell, table, roles) and any(
                cell["col"] <= column < cell["col"] + cell.get("colSpan", 1) for column in mapped
            ):
                mapped_headers.add(cell["sourceRef"])
        for cell in table["cells"]:
            rows = range(
                max(cell["row"], repeat.rowStart),
                min(cell["row"] + cell.get("rowSpan", 1), repeat.rowEnd + 1),
            )
            if any(roles.get(row) == "data" for row in rows):
                for column in repeat.columns:
                    if cell["col"] <= column.column < cell["col"] + cell.get("colSpan", 1):
                        bid = preferred_binding(
                            by_source.get(cell["sourceRef"], {}),
                            cell["sourceRef"],
                            column.bindingMode,
                        )
                        if bid and bindings[bid].get("candidateStatus") != "unresolved_conflict":
                            try:
                                _, raw, _ = _read(bindings[bid], "string", nodes)
                                value_type = column.valueType if raw else "string"
                                _read(bindings[bid], value_type, nodes)
                            except CompileError:
                                continue
                            if value_type != "decimal" or _decimal_literal(raw):
                                record_bindings.setdefault(bid, set()).add(value_type)

    resolved_fields = []
    for field_link in ir.fields:
        transformation = "source_binding"
        if field_link.bindingId is None and field_link.status in {"present", "blank"}:
            # A field's cited source can itself declare one unambiguous delimiter
            # pair. Resolve that relationship, never guess from a matching value,
            # a nearby node, a label name, or an arbitrary model-written offset.
            eligible = []
            for source_ref in field_link.definitionRefs:
                for bid, candidate in by_source.get(source_ref, {}).items():
                    labels = [bindings.get(b) for b in candidate.get("labelRefs", [])]
                    if (
                        candidate.get("candidateRole") == "value"
                        and candidate.get("candidateStatus") != "unresolved_conflict"
                        and labels
                        and all(
                            label is not None
                            and label.get("candidateRole") == "label"
                            and label["sourceRef"] == source_ref
                            for label in labels
                        )
                    ):
                        eligible.append(bid)
            if len(set(eligible)) == 1:
                field_link = field_link.model_copy(update={"bindingId": eligible[0]})
                transformation = "source_binding_from_unique_label_reference"
        if (
            field_link.bindingId is None
            and mapped_headers
            and set(field_link.definitionRefs) <= mapped_headers
        ):
            out.dropped_fields[field_link.id] = field_link.definitionRefs[0]
            continue
        if field_link.bindingId is None and field_link.status in {"present", "blank"}:
            # A present value without any source is a contradiction in one field.
            # Keep the definition, read nothing, and ask for repair rather than
            # discarding every other decision of the regional response.
            out.issues.append(
                {
                    "code": "field_binding_missing",
                    "fieldId": field_link.id,
                    "sourceRef": field_link.definitionRefs[0],
                }
            )
            field_link = field_link.model_copy(update={"status": "uncertain"})
        if (
            field_link.bindingId in candidates
            and field_link.status in {"present", "blank"}
            and field_link.valueType in record_bindings.get(field_link.bindingId, set())
        ):
            out.dropped_fields[field_link.id] = bindings[field_link.bindingId]["sourceRef"]
            continue
        resolved_fields.append(field_link)
        tokens, sp = location(field_link.key, field_link.groupId, field_link.targetHandle)
        path = "/" + "/".join(map(escape, tokens)) if tokens else ""
        target = Target(space="data", path=path)
        sid = definition(field_link.id, field_link.label, field_link.definitionRefs, sp, [target])
        value, shape = scalar(
            field_link.bindingId,
            field_link.valueType,
            field_link.status,
            field_link.definitionRefs,
            target,
            sid,
            transformation,
        )
        assign(tokens, value, shape)

    region_table = tables.get(region.get("tableRef") or region.get("tableContextRef"), {})
    declared_headers = {
        cell["sourceRef"]
        for key in ("cells", "headerCells")
        for cell in region_table.get(key, [])
        if declared_header(cell, region_table)
    }
    for field_link in resolved_fields:
        binding_id = field_link.bindingId
        if binding_id not in out.consumed_bindings:
            continue
        if bindings[binding_id]["sourceRef"] in declared_headers:
            # A declared header cell defines columns or fields. Reading its text as a
            # field value stays visible and repairable; it is never accepted silently.
            out.header_value_bindings.add(binding_id)
            out.issues.append(
                {
                    "code": "header_cell_bound_as_value",
                    "sourceRef": bindings[binding_id]["sourceRef"],
                    "bindingId": binding_id,
                }
            )

    for repeat in ir.repeats:
        if repeat.tableRef not in tables:
            raise CompileError("unknown_repeat_table")
        table = tables[repeat.tableRef]
        cells = table["cells"]
        if not cells or not set(c["sourceRef"] for c in cells) <= allowed:
            raise CompileError("repeat_table_not_fully_observed_in_region")
        if repeat.rowEnd < repeat.rowStart or repeat.rowEnd > max(
            c["row"] + c.get("rowSpan", 1) - 1 for c in cells
        ):
            raise CompileError("repeat_range_outside_observed_table")
        if repeat.rowStart < table.get("viewRowStart", 0) or repeat.rowEnd > table.get(
            "viewRowEnd", repeat.rowEnd
        ):
            raise CompileError("repeat_range_outside_region_view")
        if (repeat.rowEnd - repeat.rowStart + 1) * len(repeat.columns) > 1000000:
            raise CompileError("repeat_expansion_budget_exceeded")
        roles = {r.row: r for r in repeat.rowRoles}
        if len(roles) != len(repeat.rowRoles) or not set(roles) <= set(
            range(repeat.rowStart, repeat.rowEnd + 1)
        ):
            raise CompileError("invalid_repeat_row_roles")
        observed = {
            row: row_cells
            for row, row_cells in observed_rows(cells).items()
            if repeat.rowStart <= row <= repeat.rowEnd
        }
        if not set(roles) <= set(observed):
            raise CompileError("repeat_role_has_no_observed_cells")
        missing_roles = sorted(set(observed) - set(roles))
        if missing_roles:
            out.issues.append(
                {
                    "code": "repeat_row_roles_incomplete",
                    "tableRef": repeat.tableRef,
                    "rows": missing_roles,
                }
            )
        for role in roles.values():
            refs(role.sourceRefs)
            actual_refs = {cell["sourceRef"] for cell in observed[role.row]}
            if set(role.sourceRefs) != actual_refs:
                raise CompileError("repeat_row_role_sources_disagree_with_geometry")
        # Header geometry is program knowledge: every column's definition carries the
        # declared header cells above it, and a cited header that does not sit above
        # the column, or a missing lowest header, is reported for repair.
        declared = {}
        for cell in sorted(
            [*cells, *table.get("headerCells", [])], key=lambda c: (c["row"], c["col"])
        ):
            if _column_header(cell, table, {row: role.role for row, role in roles.items()}):
                declared.setdefault(cell["sourceRef"], cell)
        headers_above = {}
        for ref, cell in declared.items():
            for column in range(cell["col"], cell["col"] + cell.get("colSpan", 1)):
                headers_above.setdefault(column, []).append(ref)
        column_sources = {}
        for col in repeat.columns:
            above = headers_above.get(col.column, [])
            for ref in col.definitionRefs:
                if ref in declared and ref not in above:
                    out.issues.append(
                        {
                            "code": "column_definition_not_above_column",
                            "sourceRef": ref,
                            "column": col.column,
                        }
                    )
            if above and above[-1] not in col.definitionRefs:
                out.issues.append(
                    {
                        "code": "column_leaf_header_missing",
                        "sourceRef": above[-1],
                        "column": col.column,
                    }
                )
            column_sources[col.id] = list(dict.fromkeys([*col.definitionRefs, *above]))
        column_definitions = {ref for sources in column_sources.values() for ref in sources}
        header_definition_sources.update(
            cell["sourceRef"]
            for cell in cells
            if cell["sourceRef"] in column_definitions
            and (
                declared_header(cell, table)
                or (cell["row"] in roles and roles[cell["row"]].role == "header")
            )
        )
        tokens, sp = location(repeat.key, repeat.groupId, repeat.targetHandle)
        path = "/" + "/".join(map(escape, tokens)) if tokens else ""
        rows, row_shape = [], _object()
        columns = {}
        column_indices = set()
        for col in repeat.columns:
            if col.column in column_indices:
                raise CompileError("duplicate_repeat_column_index")
            column_indices.add(col.column)
            key = col.key
            if target_schema is not None:
                target = catalog.get(col.targetHandle)
                if not target or target["tokens"][:-1] != [*tokens, "*"]:
                    raise CompileError("repeat_column_target_handle_invalid")
                key = target["tokens"][-1]
            if key in columns:
                raise CompileError("duplicate_repeat_column_property")
            columns[key] = col
            row_shape["properties"][key] = _schema(col.valueType, "present")
            row_shape["required"].append(key)
        repeat_target = Target(space="data", path=path)
        definition(repeat.id, repeat.label, repeat.definitionRefs, sp, [repeat_target])
        field_targets = {col.id: [] for col in repeat.columns}
        row_targets[repeat.id] = {}
        grid = {}
        for cell in cells:
            for row in range(
                max(cell["row"], repeat.rowStart),
                min(cell["row"] + cell.get("rowSpan", 1), repeat.rowEnd + 1),
            ):
                for col in repeat.columns:
                    if cell["col"] <= col.column < cell["col"] + cell.get("colSpan", 1):
                        key = (row, col.column)
                        if key in grid and grid[key] != cell:
                            raise CompileError("overlapping_observed_table_cells")
                        grid[key] = cell
        for row in sorted(observed):
            if row not in roles:
                continue  # Missing decisions never create records, including synthetic gaps.
            if roles[row].role != "data":
                if roles[row].role == "unresolved":
                    out.issues.append(
                        {"code": "repeat_row_unresolved", "tableRef": repeat.tableRef, "row": row}
                    )
                continue
            record = {}
            row_targets[repeat.id][row] = []
            for key, col in columns.items():
                cell = grid.get((row, col.column))
                if cell and declared_header(cell, table):
                    out.issues.append(
                        {
                            "code": "header_cell_bound_as_value",
                            "sourceRef": cell["sourceRef"],
                            "tableRef": repeat.tableRef,
                            "row": row,
                            "column": col.column,
                        }
                    )
                target = Target(space="data", path=f"{path}/{len(rows)}/{escape(key)}")
                field_targets[col.id].append(target)
                row_targets[repeat.id][row].append(target)
                binding_id = (
                    preferred_binding(
                        by_source.get(cell["sourceRef"], {}), cell["sourceRef"], col.bindingMode
                    )
                    if cell
                    else None
                )
                if (
                    binding_id
                    and bindings[binding_id].get("candidateStatus") == "unresolved_conflict"
                ):
                    binding_id, status = None, "uncertain"
                elif binding_id:
                    _, raw, _ = _read(bindings[binding_id], "string", nodes)
                    status = "blank" if raw == "" else "present"
                else:
                    status = "uncertain" if table.get("basis") != "native_structure" else "absent"
                record[key], shape = scalar(
                    binding_id,
                    col.valueType if status != "blank" else "string",
                    status,
                    col.definitionRefs,
                    target,
                    prefix + col.id,
                )
                types = set(
                    row_shape["properties"][key]["type"]
                    if isinstance(row_shape["properties"][key]["type"], list)
                    else [row_shape["properties"][key]["type"]]
                )
                types.update(shape["type"] if isinstance(shape["type"], list) else [shape["type"]])
                row_shape["properties"][key]["type"] = (
                    sorted(types) if len(types) > 1 else next(iter(types))
                )
            rows.append(record)
        for key, col in columns.items():
            definition(
                col.id,
                col.label,
                column_sources[col.id],
                f"{sp}/items/properties/{escape(key)}",
                field_targets[col.id],
            )
        assign(tokens, rows, {"type": "array", "items": row_shape})
        out.repeat_paths[repeat.id] = {
            "path": path,
            "schemaPath": sp,
            "tableRef": repeat.tableRef,
            "rowStart": repeat.rowStart,
            "rowEnd": repeat.rowEnd,
            "columns": [c.column for c in repeat.columns],
            "columnDefinitions": {str(c.column): prefix + c.id for c in repeat.columns},
            "headerSourceRefs": sorted(declared),
        }
        if not rows:
            unresolved_rows = bool(missing_roles) or any(
                r.role == "unresolved" for r in roles.values()
            )
            out.value_evidence.append(
                Evidence(
                    target=repeat_target,
                    sourceRefs=repeat.definitionRefs,
                    semanticIds=[prefix + repeat.id],
                    raw="",
                    status="uncertain" if unresolved_rows else "blank",
                    transformation="unresolved_repeat_rows"
                    if unresolved_rows
                    else "observed_empty_repeat",
                ).model_dump()
            )

    if ir.repeats and region.get("tableRef") in tables:
        region_cells = tables[region["tableRef"]]["cells"]
        data_rows = {
            row
            for cell in region_cells
            if not declared_header(cell, tables[region["tableRef"]])
            for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1))
        }
        covered = set()
        for repeat in ir.repeats:
            if repeat.tableRef == region["tableRef"]:
                covered.update(range(repeat.rowStart, repeat.rowEnd + 1))
        outside = sorted(data_rows - covered)
        if outside:
            # A repeat that stops short leaves observed rows to per-cell guesses.
            out.issues.append(
                {
                    "code": "table_rows_outside_repeat",
                    "sourceRef": region["tableRef"],
                    "tableRef": region["tableRef"],
                    "rows": outside[:50],
                }
            )
    meaning_ids = set()
    scope_ids = {
        "fieldIds": {f.id for f in ir.fields if f.id not in out.dropped_fields}
        | {c.id for r in ir.repeats for c in r.columns},
        "groupIds": set(groups),
        "repeatIds": {r.id for r in ir.repeats},
    }
    repeats_by_id = {r.id: r for r in ir.repeats}
    for meaning in ir.meanings:
        if meaning.id in meaning_ids or meaning.id in ids:
            raise CompileError("duplicate_meaning_id")
        meaning_ids.add(meaning.id)
        source_refs = refs(meaning.sourceRefs)
        targets = []
        invalid_ids = []
        scope_errors = []
        for kind, known in scope_ids.items():
            for entity in getattr(meaning, kind):
                if entity not in known:
                    invalid_ids.append(entity)
                else:
                    targets.extend(entity_targets[entity])
        if invalid_ids:
            scope_errors.append("meaning_has_unknown_scope")
        # Unbounded entity IDs form a union, not a record/column qualifier pair.
        # Keep invalid applicability unresolved for the existing scope-only repair;
        # never silently broaden a column assertion to its parent record/group.
        if (
            meaning.rowStart is None
            and meaning.rowEnd is None
            and any(
                left.space == right.space
                and left.path != right.path
                and left.path.startswith(right.path.rstrip("/") + "/")
                for left in targets
                for right in targets
            )
        ):
            scope_errors.append("meaning_has_overlapping_scope")
        if (meaning.rowStart is None) != (meaning.rowEnd is None):
            scope_errors.append("meaning_row_scope_requires_both_bounds")
        if meaning.rowStart is not None and meaning.rowEnd is not None:
            if meaning.rowStart > meaning.rowEnd or not meaning.repeatIds:
                scope_errors.append("meaning_row_scope_invalid")
            for rid in meaning.repeatIds:
                repeat = repeats_by_id.get(rid)
                if repeat and (
                    meaning.rowStart < repeat.rowStart or meaning.rowEnd > repeat.rowEnd
                ):
                    scope_errors.append("meaning_row_scope_outside_repeat")
        if scope_errors:
            # A malformed applicability link must not discard valid field/value
            # bindings in the same response or silently apply only its valid half.
            targets = []
        elif meaning.rowStart is not None:
            bounded = []
            for rid in meaning.repeatIds:
                for row, row_items in row_targets[rid].items():
                    if meaning.rowStart <= row <= meaning.rowEnd:
                        bounded.extend(row_items)
            fields = {t.path for fid in meaning.fieldIds for t in entity_targets[fid]}
            targets = [t for t in bounded if not fields or t.path in fields]
        sid = prefix + meaning.id
        status = meaning.status if targets else "uncertain"
        assertion_targets = targets or [
            Target(space="document", path="/" + escape(ref)) for ref in source_refs
        ]
        if not targets:
            out.issues.append({"code": "semantic_scope_unresolved", "semanticId": sid})
        out.semantics.append(
            Assertion(
                id=sid,
                kind=meaning.kind if targets else "unresolved_" + meaning.kind,
                description=meaning.description,
                targets=assertion_targets,
                scope=assertion_targets,
                sourceRefs=source_refs,
                basis="ai_interpreted",
                status=status,
            ).model_dump()
        )
        out.semantic_details.append(
            {
                "id": sid,
                "kind": meaning.kind,
                "scope": [t.model_dump() for t in targets],
                "sourceRefs": source_refs,
                "sourceText": [s.text for s in meaning.sourceRanges]
                if meaning.sourceRanges
                else [_view_text(nodes, region, ref) for ref in source_refs],
                **(
                    {
                        "sourceRanges": [s.model_dump() for s in meaning.sourceRanges],
                        "surroundingContext": [
                            {"sourceRef": ref, "text": _view_text(nodes, region, ref)}
                            for ref in source_refs
                        ],
                        "sourceInventorySHA256": ir.tableMeaningState.inventorySHA256,
                    }
                    if meaning.sourceRanges
                    else {
                        "sourceRanges": [
                            {"sourceRef": r, "path": "/text", **region["nodeViews"][r]}
                            for r in source_refs
                            if r in region["nodeViews"]
                        ]
                    }
                    if region.get("nodeViews")
                    else {}
                ),
                "interpretationStatus": status,
                "executable": False,
                **(
                    {
                        "scopeErrors": list(dict.fromkeys(scope_errors)),
                        "unresolvedScopeIds": list(dict.fromkeys(invalid_ids)),
                    }
                    if scope_errors
                    else {}
                ),
            }
        )
        if targets and status == "uncertain":
            out.issues.append({"code": "semantic_scope_uncertain", "semanticId": sid})
        target_paths = {t.path for t in targets}
        for item in out.value_evidence:
            if any(
                item["target"]["path"] == p or item["target"]["path"].startswith(p + "/")
                for p in target_paths
            ):
                item["semanticIds"].append(sid)

    dispositions = {d.sourceRef: d for d in ir.dispositions}
    if len(dispositions) != len(ir.dispositions) or not set(dispositions) <= allowed:
        raise CompileError("invalid_node_dispositions")
    derived = bound_node_dispositions(
        observation, region, resolved_fields, out.consumed_bindings, header_definition_sources
    )
    for ref in region["nodeIds"]:
        if (
            ref not in dispositions
            and out.meaning_review is not None
            and any(s["sourceRef"] == ref for s in inventory["sources"])
        ):
            # Table meaning is reviewed by exact text range, independently from
            # the mechanical value/header dispositions preserved below.
            continue
        disposition = dispositions.get(ref)
        if disposition is None:
            if ref not in derived:
                out.issues.append({"code": "node_semantics_unaccounted", "sourceRef": ref})
        elif disposition.role in {"unresolved", "unsupported"}:
            out.issues.append({"code": "node_semantics_" + disposition.role, "sourceRef": ref})
        elif disposition.role == "note" and not any(
            ref in item["sourceRefs"] for item in out.semantic_details
        ):
            out.issues.append({"code": "note_scope_unresolved", "sourceRef": ref})
    out.dispositions = [
        *[d.model_dump() for d in ir.dispositions],
        *[value for ref, value in derived.items() if ref not in dispositions],
    ]
    for disposition in out.dispositions:
        window = region.get("nodeViews", {}).get(disposition["sourceRef"])
        if window is not None:
            disposition.update(regionId=region["id"], textRange={"path": "/text", **window})
    for field_id, ref in out.dropped_fields.items():
        entry = next((d for d in out.dispositions if d["sourceRef"] == ref), None)
        if entry is None:
            header = ref in mapped_headers
            entry = {
                "sourceRef": ref,
                "role": "structural" if header else "data",
                "explanation": "Declares a mapped repeat column"
                if header
                else "Read as a record value by a repeat column",
                "basis": "program_derived",
                "bindingIds": [],
            }
            out.dispositions.append(entry)
        entry.setdefault("redundantFieldIds", []).append(field_id)
    excluded = {b.bindingId: b for b in ir.excludedBindings}
    if (
        len(excluded) != len(ir.excludedBindings)
        or not set(excluded) <= candidates
        or set(excluded) & out.consumed_bindings
    ):
        raise CompileError("invalid_excluded_binding")
    used_sources = {bindings[b]["sourceRef"] for b in out.consumed_bindings}
    for bid in region.get("requiredBindingIds", []):
        if bid in out.consumed_bindings:
            continue
        # A selected native raw scalar and its text representation refer to the same cell.
        source_ref = bindings[bid]["sourceRef"]
        if source_ref in header_definition_sources:
            # Field definitions plus an observed/declared header role account for labels.
            # Ordinary data cells never become labels just because a model cites them.
            continue
        native_equivalent = (
            bindings[bid].get("candidateRole") == "native_value" and source_ref in used_sources
        )
        if native_equivalent:
            continue
        if bid not in excluded or excluded[bid].role == "unresolved":
            out.issues.append({"code": "value_candidate_unaccounted", "bindingId": bid})
    out.issues.extend(
        {"code": "semantic_relation_unresolved", "description": text} for text in ir.unresolved
    )
    return out


def combine_regions(compiled: list[CompiledRegion], *, target_schema=None):
    """Merge without silently overwriting equal-looking fields from different regions."""
    data, schema = {}, _object()
    errors, conflicts = [], set()

    def merge(left, left_schema, right, right_schema, path=""):
        for key, value in right.items():
            prop = right_schema["properties"][key]
            if key in left:
                if isinstance(left[key], dict) and isinstance(value, dict):
                    merge(
                        left[key],
                        left_schema["properties"][key],
                        value,
                        prop,
                        path + "/" + escape(key),
                    )
                else:
                    conflicts.add(path + "/" + escape(key))
            else:
                left[key], left_schema["properties"][key] = (
                    copy.deepcopy(value),
                    copy.deepcopy(prop),
                )
                left_schema["required"].append(key)

    assigned = False
    for region in compiled:
        if not region.has_data:
            continue
        if not assigned:
            data, schema = copy.deepcopy(region.data), copy.deepcopy(region.schema)
            assigned = True
        elif isinstance(data, dict) and isinstance(region.data, dict):
            merge(data, schema, region.data, region.schema)
        else:
            conflicts.add("")
    if conflicts:
        errors.append("cross_region_data_conflict")
    if target_schema is not None:
        schema = copy.deepcopy(target_schema)
    semantic, schema_ev, value_ev, details, values, issues = [], [], [], [], [], []
    seen_schema, seen_values, seen_semantics = set(), set(), set()
    for region in compiled:
        for item in region.semantics:
            if item["id"] not in seen_semantics:
                semantic.append(item)
                seen_semantics.add(item["id"])
        for item in region.schema_evidence:
            # Shared groups may have several observations, combined into one evidence record.
            path = item["target"]["path"]
            if path in seen_schema:
                prior = next(x for x in schema_ev if x["target"]["path"] == path)
                prior["sourceRefs"] = list(
                    dict.fromkeys([*prior["sourceRefs"], *item["sourceRefs"]])
                )
                prior["semanticIds"] = list(
                    dict.fromkeys([*prior["semanticIds"], *item["semanticIds"]])
                )
            else:
                schema_ev.append(copy.deepcopy(item))
                seen_schema.add(path)
        for item in region.value_evidence:
            path = item["target"]["path"]
            if path not in seen_values:
                value_ev.append(item)
                seen_values.add(path)
        details.extend(region.semantic_details)
        values.extend(region.value_observations)
        issues.extend(region.issues)
    if compiled:
        try:
            check_schema(schema)
            for error in Draft202012Validator(schema, registry=Registry()).iter_errors(data):
                errors.append("data_schema_instance_mismatch:" + "/".join(map(str, error.path)))
                if len(errors) >= 30:
                    break
        except Exception:
            errors.append("data_schema_invalid")
        if leaves(data) - seen_values:
            errors.append("data_leaves_missing_evidence")
        if schema_definitions(schema) - seen_schema:
            errors.append("schema_definitions_missing_evidence")
    return {
        "data": data if compiled else None,
        "dataSchema": schema if compiled else None,
        "semantics": semantic,
        "schemaEvidence": schema_ev,
        "valueEvidence": value_ev,
        "semanticDetails": details,
        "valueObservations": values,
        "issues": issues,
        "errors": list(dict.fromkeys(errors))[:30],
    }


def join_continuations(compiled, candidates, decisions):
    """Apply confirmed table continuations and rewrite dependent generated pointers only."""
    result = copy.deepcopy(compiled)
    regions = {r.id: r for r in result}
    issues, relations = [], []
    roots = {}
    for candidate in candidates:
        decision = decisions.get(candidate["id"])
        if not candidate["confirmed"] and decision != "continue":
            if decision != "separate":
                issues.append(
                    {"code": "table_continuation_unresolved", "candidateId": candidate["id"]}
                )
            continue
        left_id = roots.get(candidate["leftRegion"], candidate["leftRegion"])
        left, right = regions.get(left_id), regions.get(candidate["rightRegion"])
        if not left or not right:
            issues.append({"code": "table_continuation_pending", "candidateId": candidate["id"]})
            continue
        a = list(left.repeat_paths.values())
        b = [v for v in right.repeat_paths.values() if v["tableRef"] == candidate["rightTable"]]
        if len(a) != 1 or len(b) != 1:
            issues.append(
                {"code": "table_continuation_mapping_ambiguous", "candidateId": candidate["id"]}
            )
            continue
        a, b = a[0], b[0]
        if candidate["confirmed"] and b["rowStart"] <= a["rowEnd"]:
            issues.append(
                {"code": "table_continuation_rows_overlap", "candidateId": candidate["id"]}
            )
            continue
        try:
            rows_a, rows_b = pointer(left.data, a["path"]), pointer(right.data, b["path"])
            schema_a, schema_b = (
                pointer(left.schema, a["schemaPath"]),
                pointer(right.schema, b["schemaPath"]),
            )
            if schema_a != schema_b or a["columns"] != b["columns"]:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            issues.append(
                {"code": "table_continuation_column_conflict", "candidateId": candidate["id"]}
            )
            continue
        offset = len(rows_a)
        rows_a.extend(rows_b)
        if candidate["confirmed"]:
            a["rowEnd"] = b["rowEnd"]

        def remap(target, a=a, b=b, offset=offset):
            key = "path"
            if target.get("space") == "dataSchema":
                old, new = b["schemaPath"], a["schemaPath"]
                if target[key] == old or target[key].startswith(old + "/"):
                    target[key] = new + target[key][len(old) :]
            elif target.get("space") == "data":
                if target[key] == b["path"]:
                    target[key] = a["path"]
                elif target[key].startswith(b["path"] + "/"):
                    suffix = target[key][len(b["path"]) + 1 :]
                    row, slash, rest = suffix.partition("/")
                    target[key] = f"{a['path']}/{int(row) + offset}" + (
                        slash + rest if slash else ""
                    )

        for item in right.semantics:
            for target in [*item["targets"], *item["scope"]]:
                remap(target)
        for item in [*right.schema_evidence, *right.value_evidence, *right.value_observations]:
            remap(item["target"])
        for item in right.semantic_details:
            for target in item["scope"]:
                remap(target)
        # The merged data lives in the first region. Evidence from later regions stays distinct.
        if b["path"] == "":
            right.data, right.schema, right.has_data = {}, _object(), False
        else:
            parent_path, _, key = b["path"].rpartition("/")
            key = key.replace("~1", "/").replace("~0", "~")
            del pointer(right.data, parent_path)[key]
            schema_parent = b["schemaPath"].rsplit("/properties/", 1)[0]
            parent = pointer(right.schema, schema_parent)
            del parent["properties"][key]
            parent["required"].remove(key)
        roots[candidate["rightRegion"]] = left_id
        relations.append(
            {
                "id": candidate["id"],
                "kind": "tableContinuation",
                "sourceTable": candidate["leftTable"],
                "targetTable": candidate["rightTable"],
                "sourceRefs": candidate["sourceRefs"],
                "basis": candidate["basis"] if candidate["confirmed"] else "ai_interpreted",
                "target": {"space": "data", "path": a["path"]},
            }
        )
    return result, issues, relations
