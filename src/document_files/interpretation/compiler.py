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

from ..document_model.table_headers import declared_header, fixed_header_rows, observed_rows
from ..result_types import Assertion, Evidence, SourceBinding, Target
from .accounting import bound_node_dispositions, observed_heading
from .bindings import resolve
from .document_outline import compile_elements
from .field_identity import exact_field_identity
from .semantic_types import RegionInterpretation
from .table_revisions import meaning_revision
from .table_sources import SourceReviewError, review_ranges, source_inventory
from .validation import check_schema, escape, leaves, pointer, schema_definitions


class CompileError(ValueError):
    """Only product-written diagnostics; never raw model values or document text."""

    def __init__(self, code, *, selection=None):
        super().__init__(code)
        self.selection = selection


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
    document_elements: list[dict] = field(default_factory=list)
    grounded_bindings: dict[str, dict] = field(default_factory=dict)
    logical_coverage: dict[str, dict] = field(default_factory=dict)
    repeat_paths: dict[str, dict] = field(default_factory=dict)
    row_scopes: dict[str, dict] = field(default_factory=dict)
    meaning_statuses: dict[str, str] = field(default_factory=dict)
    header_value_bindings: set[str] = field(default_factory=set)
    dropped_fields: dict[str, str] = field(default_factory=dict)
    meaning_review: dict | None = None
    # Deterministic program corrections of a model label, resolved and auditable;
    # they are not unresolved processing issues.
    corrections: list[dict] = field(default_factory=list)


def compile_region(ir: RegionInterpretation, observation, region: dict, *, target_schema=None):
    if ir.regionId != region["id"]:
        raise CompileError("region_id_mismatch")
    from .native_records import prepare as prepare_native_records

    ir, observation, region, grounded, logical_anchors = prepare_native_records(
        ir, observation, region
    )
    nodes, bindings, tables = observation.nodes, observation.bindings, observation.tables
    allowed = set(region["nodeIds"]) | set(region.get("contextNodeIds", []))
    candidates = set(region["bindingIds"])
    by_source = {}
    for bid in candidates:
        candidate = bindings[bid]
        by_source.setdefault(candidate["sourceRef"], {})[bid] = candidate
    catalog = target_catalog(target_schema)
    out = CompiledRegion(ir.regionId)
    out.grounded_bindings = copy.deepcopy(grounded)
    try:
        out.document_elements = compile_elements(
            ir.documentElements,
            observation,
            region,
            [*ir.fields, *(v for r in ir.logicalRecords for row in r.rows for v in row.values)],
        )
    except ValueError as exc:
        raise CompileError(str(exc)) from None
    out.issues.extend(
        {"code": "document_element_uncertain", "sourceRef": element["sourceRef"]}
        for element in out.document_elements
        if element["status"] == "uncertain" or element["role"] == "unresolved"
    )
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
    ids += [r.id for r in ir.logicalRecords] + [c.id for r in ir.logicalRecords for c in r.columns]
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
            # Name the colliding key so a repair can rename it rather than guess.
            raise CompileError(f"duplicate_data_property:{key}")
        data[key], shape["properties"][key] = value, schema
        shape["required"].append(key)
        out.has_data = True
        return "/" + "/".join(escape(t) for t in tokens)

    def definition(entity_id, label, source_refs, schema_path, data_targets, *, uncertain=False):
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
                status="uncertain" if uncertain else "interpreted",
            ).model_dump()
        )
        out.schema_evidence.append(
            Evidence(
                target=schema_target,
                sourceRefs=source_refs,
                semanticIds=[sid],
                raw="",
                status="uncertain" if uncertain else "present",
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
        if binding_id in grounded:
            transformation = "exact_source_quote"
        binding = None
        raw = ""
        if status in {"present", "blank"}:
            if binding_id not in candidates:
                raise CompileError("binding_not_in_region")
            candidate = bindings[binding_id]
            if candidate.get("candidateStatus") == "unresolved_conflict":
                raise CompileError("binding_has_unresolved_observation_conflict")
            source_refs = list(dict.fromkeys([candidate["sourceRef"], *source_refs]))
            # Blank is an observed empty source, not a failed numeric conversion.
            # Use the same text representation as blank repeat cells; nonempty
            # candidates must still fail the status/observation comparison below.
            try:
                value, raw, binding = _read(
                    candidate, "string" if status == "blank" else value_type, nodes
                )
            except CompileError as exc:
                raise CompileError(
                    str(exc), selection={"bindingId": binding_id, "requestedType": value_type}
                ) from None
            if (status == "blank") != (raw == ""):
                raise CompileError("blank_status_disagrees_with_observation")
            if status == "present" and value_type == "decimal" and not _decimal_literal(raw):
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
        shape = _schema("string" if status == "blank" else value_type, status)
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

    # A delimiter label/value line is one scalar: its value span, labelled by its
    # label span. When a region binds that value span, further fields that bind
    # only the label span or the whole line restate the same scalar and are
    # dropped with a correction. Region 6 of the fifteenth continued-table run
    # emitted three fields per repeated condition line, which left a whole-line
    # field and a label field beside the value the earlier page had folded.
    value_lines = {}
    for item in ir.fields:
        binding = bindings.get(item.bindingId) if item.bindingId in candidates else None
        if (
            binding is None
            or item.status not in {"present", "blank"}
            or binding.get("candidateRole") != "value"
            or binding.get("candidateStatus") == "unresolved_conflict"
        ):
            continue
        labels = list(binding.get("labelRefs") or [])
        if not labels or any(
            bindings.get(label) is None
            or bindings[label].get("candidateRole") != "label"
            or bindings[label]["sourceRef"] != binding["sourceRef"]
            for label in labels
        ):
            continue
        value_lines.setdefault(binding["sourceRef"], []).append((item.id, labels))
    collapsed = set()
    bound_fields, field_aliases = {}, {}

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
        binding = bindings.get(field_link.bindingId) if field_link.bindingId in candidates else None
        owners = value_lines.get(binding["sourceRef"]) if binding is not None else None
        if owners and field_link.status in {"present", "blank"}:
            basis = None
            if any(field_link.bindingId in labels for _, labels in owners):
                basis = "label_span_of_bound_value"
            elif binding.get("candidateRole") == "content":
                basis = "whole_line_of_bound_value"
            if basis is not None:
                out.dropped_fields[field_link.id] = binding["sourceRef"]
                collapsed.add(field_link.id)
                out.corrections.append(
                    {
                        "code": "label_value_line_field_dropped",
                        "regionId": out.id,
                        "fieldId": field_link.id,
                        "bindingId": field_link.bindingId,
                        "sourceRef": binding["sourceRef"],
                        "valueFieldIds": [owner for owner, _ in owners],
                        "basis": basis,
                    }
                )
                continue
        # The source alone does not identify a field. Keep different definitions
        # and destinations; a wrong first field must not erase a later correct one.
        tokens, sp = location(field_link.key, field_link.groupId, field_link.targetHandle)
        if field_link.bindingId in candidates and field_link.status in {"present", "blank"}:
            identity = exact_field_identity(
                field_link, bindings[field_link.bindingId], nodes, tokens
            )
            duplicate = bound_fields.get(identity)
            if duplicate is not None:
                kept, kept_binding = duplicate
                if kept_binding in out.consumed_bindings:
                    out.consumed_bindings.add(field_link.bindingId)
                out.dropped_fields[field_link.id] = bindings[field_link.bindingId]["sourceRef"]
                field_aliases[field_link.id] = kept
                out.corrections.append(
                    {
                        "code": "duplicate_binding_field_dropped",
                        "regionId": out.id,
                        "fieldId": field_link.id,
                        "bindingId": field_link.bindingId,
                        "keptFieldId": kept,
                        "basis": "same_source_definition_and_destination",
                    }
                )
                continue
            bound_fields[identity] = (field_link.id, field_link.bindingId)
        resolved_fields.append(field_link)
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
        # Only source-declared header-only rows have a program-owned role. Column
        # citations cannot override content roles: text records, subtotals and notes
        # are not headers merely because the model used them to define a column.
        # Use the same declaration/geometry rule as the table structure protocol;
        # recognizer predictions and mixed header/value rows stay model decisions.
        for row in sorted(fixed_header_rows(table) & set(roles)):
            role = roles[row]
            if role.role in {"header", "blank", "unresolved"}:
                continue
            roles[row] = role.model_copy(update={"role": "header"})
            out.corrections.append(
                {
                    "code": "native_header_row_role_corrected",
                    "regionId": out.id,
                    "tableRef": repeat.tableRef,
                    "row": row,
                    "declaredRole": role.role,
                    "basis": "all_observed_cells_declared_headers",
                }
            )
        # A cell of a content row (data, subtotal, note) is a value, not a column
        # definition: such citations are dropped from the column and recorded. A column
        # that would keep no citation retains its proposal as uncertain for repair;
        # it cannot silently turn its cited content rows into headers.
        content_refs = {
            cell["sourceRef"]
            for row, row_cells in observed.items()
            if row in roles and roles[row].role in {"data", "subtotal", "note"}
            for cell in row_cells
        }
        columns = []
        conflicting_column_definitions = set()
        for col in repeat.columns:
            dropped = [ref for ref in col.definitionRefs if ref in content_refs]
            kept = [ref for ref in col.definitionRefs if ref not in content_refs]
            if dropped and kept:
                out.corrections.append(
                    {
                        "code": "column_definition_content_cells_dropped",
                        "regionId": out.id,
                        "tableRef": repeat.tableRef,
                        "columnId": col.id,
                        "column": col.column,
                        "sourceRefs": dropped,
                        "basis": "cells_of_rows_labeled_data_subtotal_or_note",
                    }
                )
                col = col.model_copy(update={"definitionRefs": kept})
            elif dropped:
                conflicting_column_definitions.add(col.id)
                out.issues.append(
                    {
                        "code": "column_definition_conflicts_with_content",
                        "tableRef": repeat.tableRef,
                        "columnId": col.id,
                        "column": col.column,
                        "sourceRefs": dropped,
                    }
                )
            columns.append(col)
        repeat = repeat.model_copy(update={"columns": columns})
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
        row_scope = {
            "tableRef": repeat.tableRef,
            "rowStart": repeat.rowStart,
            "rowEnd": repeat.rowEnd,
            "dataOwnerRegionId": out.id,
            "columns": {
                col.id: {"column": col.column, "definitionId": prefix + col.id}
                for col in repeat.columns
            },
            "rows": {
                str(row): {
                    "role": roles[row].role if row in roles else "unresolved",
                    "sourceRefs": list(dict.fromkeys(c["sourceRef"] for c in row_cells)),
                    "targets": {},
                }
                for row, row_cells in sorted(observed.items())
            },
        }
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
                row_scope["rows"][str(row)]["targets"][col.id] = target.model_dump()
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
                uncertain=col.id in conflicting_column_definitions,
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
            "columnKeys": {str(c.column): c.key for c in repeat.columns},
            "headerSourceRefs": sorted(declared),
        }
        out.row_scopes[repeat.id] = row_scope
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

    # Logical record rows are interpreted occurrences, never synthetic native cells.
    for record in ir.logicalRecords:
        tokens, sp = location(record.key, record.groupId, record.targetHandle)
        path = "/" + "/".join(map(escape, tokens)) if tokens else ""
        rows, row_shape, columns = [], _object(), {}
        if len({c.id for c in record.columns}) != len(record.columns):
            raise CompileError("duplicate_logical_column_id")
        for col in record.columns:
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
        record_target = Target(space="data", path=path)
        definition(record.id, record.label, record.definitionRefs, sp, [record_target])
        field_targets = {col.id: [] for col in record.columns}
        row_targets[record.id] = {}
        row_scope = {
            "tableRef": None,
            "recordRef": prefix + record.id,
            "basis": "source_grounded_logical_occurrences",
            "rowStart": 0,
            "rowEnd": len(record.rows) - 1,
            "dataOwnerRegionId": out.id,
            "columns": {
                col.id: {"column": index, "definitionId": prefix + col.id}
                for index, col in enumerate(record.columns)
            },
            "rows": {},
        }
        for index, row in enumerate(record.rows):
            selected_values = {v.columnId: v for v in row.values}
            if len(selected_values) != len(row.values) or set(selected_values) != set(
                field_targets
            ):
                raise CompileError("logical_record_values_do_not_match_columns")
            row_targets[record.id][index] = []
            row_scope["rows"][str(index)] = {
                "role": "data",
                "occurrenceId": row.id,
                "sourceRefs": list(
                    dict.fromkeys(s["sourceRef"] for s in logical_anchors[(record.id, row.id)])
                ),
                "sourceRanges": logical_anchors[(record.id, row.id)],
                "targets": {},
            }
            item = {}
            for key, col in columns.items():
                value = selected_values[col.id]
                target = Target(space="data", path=f"{path}/{index}/{escape(key)}")
                field_targets[col.id].append(target)
                row_targets[record.id][index].append(target)
                row_scope["rows"][str(index)]["targets"][col.id] = target.model_dump()
                item[key], shape = scalar(
                    value.bindingId,
                    col.valueType,
                    value.status,
                    value.sourceRefs,
                    target,
                    prefix + col.id,
                )
                old = row_shape["properties"][key]["type"]
                new = shape["type"]
                types = set(old if isinstance(old, list) else [old]) | set(
                    new if isinstance(new, list) else [new]
                )
                row_shape["properties"][key]["type"] = (
                    sorted(types) if len(types) > 1 else next(iter(types))
                )
            rows.append(item)
        for key, col in columns.items():
            definition(
                col.id,
                col.label,
                col.definitionRefs,
                f"{sp}/items/properties/{escape(key)}",
                field_targets[col.id],
            )
        assign(tokens, rows, {"type": "array", "items": row_shape})
        out.repeat_paths[record.id] = {
            "path": path,
            "schemaPath": sp,
            "tableRef": None,
            "recordRef": prefix + record.id,
            "basis": "source_grounded_logical_occurrences",
            "rowStart": 0,
            "rowEnd": len(rows) - 1,
            "columns": list(range(len(record.columns))),
            "columnDefinitions": {str(i): prefix + c.id for i, c in enumerate(record.columns)},
            "columnKeys": {str(i): c.key for i, c in enumerate(record.columns)},
            "headerSourceRefs": [],
        }
        out.logical_coverage[record.id] = {
            "recordRef": prefix + record.id,
            "basis": "source_grounded_logical_occurrences",
            "columnIds": list(field_targets),
            "rows": copy.deepcopy(list(row_scope["rows"].values())),
            "emptySourceRanges": copy.deepcopy(logical_anchors.get((record.id, None), [])),
        }
        if rows:
            out.row_scopes[record.id] = row_scope
        else:
            spans = logical_anchors[(record.id, None)]
            out.value_evidence.append(
                Evidence(
                    target=record_target,
                    sourceRefs=list(dict.fromkeys(s["sourceRef"] for s in spans)),
                    semanticIds=[prefix + record.id],
                    raw="",
                    status="blank",
                    transformation="source_grounded_empty_logical_record",
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
        | {c.id for r in [*ir.repeats, *ir.logicalRecords] for c in r.columns},
        "groupIds": set(groups),
        "repeatIds": {r.id for r in [*ir.repeats, *ir.logicalRecords]},
    }

    def statement_fields(source_refs):
        """String fields that merely carry the statement's own text (label/value)."""
        statement = set(source_refs)
        owned = set()
        for item in ir.fields:
            if item.id in out.dropped_fields or item.valueType != "string":
                continue
            binding = bindings.get(item.bindingId) if item.bindingId else None
            if binding is not None:
                if binding.get("sourceRef") in statement:
                    owned.add(item.id)
            elif set(item.definitionRefs) <= statement:
                owned.add(item.id)
        return owned

    owned_nodes = set(region["nodeIds"])
    disposition_roles = {d.sourceRef: d.role for d in ir.dispositions}
    consumed_refs = {bindings[b]["sourceRef"] for b in out.consumed_bindings}

    def heading(ref):
        """A heading by the model's disposition or by the recognized role of an unread node."""
        if disposition_roles.get(ref) == "heading":
            return True
        return observed_heading(nodes.get(ref, {})) and ref not in consumed_refs

    for meaning in ir.meanings:
        if field_aliases:
            meaning = meaning.model_copy(
                update={
                    "fieldIds": list(
                        dict.fromkeys(field_aliases.get(fid, fid) for fid in meaning.fieldIds)
                    )
                }
            )
        if meaning.id in meaning_ids or meaning.id in ids:
            raise CompileError("duplicate_meaning_id")
        meaning_ids.add(meaning.id)
        source_refs = refs(meaning.sourceRefs)
        if not set(source_refs) & owned_nodes:
            # Context clarifies the owned nodes; the region that owns a statement
            # interprets it. The delivery-form scalar region restated its context
            # statements as meanings and applied them to every subtotal scalar.
            out.corrections.append(
                {
                    "code": "context_only_meaning_dropped",
                    "regionId": out.id,
                    "semanticId": prefix + meaning.id,
                    "kind": meaning.kind,
                    "sourceRefs": source_refs,
                    "basis": "statement_owned_by_another_region",
                }
            )
            continue
        if (
            meaning.kind == "definition"
            and meaning.rowStart is None
            and not meaning.groupIds
            and not meaning.repeatIds
            and not [entity for entity in meaning.fieldIds if entity not in collapsed]
            and all(heading(ref) for ref in source_refs)
        ):
            # A document or section title defines no offered value: a definition over
            # headings alone names nothing to apply to, and an applicability request
            # for it can only come back unresolved (delivery-form run 4).
            out.corrections.append(
                {
                    "code": "heading_definition_dropped",
                    "regionId": out.id,
                    "semanticId": prefix + meaning.id,
                    "sourceRefs": source_refs,
                    "basis": "definition_without_scope_over_headings",
                }
            )
            continue
        targets = []
        invalid_ids = []
        scope_errors = []
        field_ids = [entity for entity in meaning.fieldIds if entity not in collapsed]
        for kind, known in scope_ids.items():
            for entity in getattr(meaning, kind):
                if kind == "fieldIds" and entity in collapsed:
                    # The dropped label or whole-line field is read by its value field.
                    continue
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
                bounds = out.repeat_paths.get(rid)
                if bounds and (
                    meaning.rowStart < bounds["rowStart"] or meaning.rowEnd > bounds["rowEnd"]
                ):
                    scope_errors.append("meaning_row_scope_outside_repeat")
        if scope_errors:
            # A malformed applicability link must not discard valid field/value
            # bindings in the same response or silently apply only its valid half.
            targets = []
        elif (
            meaning.kind in {"unit", "condition"}
            and not meaning.groupIds
            and not meaning.repeatIds
            and field_ids
            and set(field_ids) <= statement_fields(source_refs)
        ):
            # A unit or condition that only qualifies the string fields carrying its
            # own statement has no applicability yet; the separate scope protocol
            # chooses the values it governs, as it does for table meanings.
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
        out.meaning_statuses[sid] = meaning.status
        if meaning.status == "uncertain":
            out.issues.append({"code": "semantic_interpretation_uncertain", "semanticId": sid})
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
    for element in out.document_elements:
        # Source use follows a separately validated role decision, not vice versa.
        # This does not exempt any of the required value candidates below.
        derived.setdefault(
            element["sourceRef"],
            {
                "sourceRef": element["sourceRef"],
                "role": "unresolved"
                if element["status"] == "uncertain"
                else "heading"
                if element["role"] in {"title", "section_heading"}
                else "structural",
                "explanation": "Source preserved by an explicit document element decision",
                "basis": "ai_interpreted",
                "documentElementId": element["id"],
            },
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
            if header:
                explanation = "Declares a mapped repeat column"
            elif field_id in collapsed:
                explanation = "Label/value line read as one scalar by its value field"
            else:
                explanation = "Read as a record value by a repeat column"
            entry = {
                "sourceRef": ref,
                "role": "structural" if header else "data",
                "explanation": explanation,
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
    corrections = [copy.deepcopy(c) for region in compiled for c in region.corrections]
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
        document_only = (
            not assigned
            and data == {}
            and schema == _object()
            and any(r.document_elements for r in compiled)
        )
        if not document_only and leaves(data) - seen_values:
            errors.append("data_leaves_missing_evidence")
        if not document_only and schema_definitions(schema) - seen_schema:
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
        "corrections": corrections,
        "errors": list(dict.fromkeys(errors))[:30],
    }


def _schema_pointer(data_path):
    """dataSchema pointer of an object-nested data leaf; repeat rows are not scalar fields."""
    segments = data_path.split("/")[1:] if data_path else []
    if not segments or any(segment.isdigit() for segment in segments):
        return None
    return "".join("/properties/" + segment for segment in segments)


def _rewrite(target, mapping):
    """Rewrite one target in place by exact or nested pointer within its space."""
    space, path = target.get("space"), target.get("path")
    for (old_space, old), new in mapping:
        if old_space != space:
            continue
        if path == old:
            target["path"] = new
            return
        if path.startswith(old + "/"):
            target["path"] = new + path[len(old) :]
            return


def _targets(region):
    for item in region.semantics:
        yield from item["targets"]
        yield from item["scope"]
    for item in [*region.schema_evidence, *region.value_evidence, *region.value_observations]:
        yield item["target"]
    for item in region.semantic_details:
        yield from item["scope"]
    for row_scope in region.row_scopes.values():
        for row in row_scope["rows"].values():
            yield from row["targets"].values()


def _delete_leaf(region, data_path, schema_path):
    parent_path, _, key = data_path.rpartition("/")
    key = key.replace("~1", "/").replace("~0", "~")
    del pointer(region.data, parent_path)[key]
    parent = pointer(region.schema, schema_path.rsplit("/properties/", 1)[0])
    del parent["properties"][key]
    if key in parent.get("required", []):
        parent["required"].remove(key)


def _unresolved(region, semantic_id):
    return any(
        i.get("code") == "semantic_scope_unresolved" and i.get("semanticId") == semantic_id
        for i in region.issues
    )


def _drop_semantic(region, semantic_id):
    region.semantics[:] = [i for i in region.semantics if i["id"] != semantic_id]
    region.semantic_details[:] = [d for d in region.semantic_details if d["id"] != semantic_id]
    region.issues[:] = [i for i in region.issues if i.get("semanticId") != semantic_id]
    region.meaning_statuses.pop(semantic_id, None)


def _union(items, extra):
    result = list(items)
    for item in extra:
        if item not in result:
            result.append(item)
    return result


def _signature(item):
    return (
        item["kind"],
        frozenset((t["space"], t["path"]) for t in item["targets"]),
        frozenset((t["space"], t["path"]) for t in item["scope"]),
    )


def _leaves(compiled):
    for region in compiled:
        for record in region.value_evidence:
            target = record["target"]
            schema_path = _schema_pointer(target["path"]) if target.get("space") == "data" else None
            if schema_path is None:
                continue
            try:
                value = pointer(region.data, target["path"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if isinstance(value, dict | list):
                continue
            yield region, record, schema_path, value


def _merge_repeated_statements(compiled, counterparts, candidate_id, aliases):
    """Fold fields and meanings repeated on a joined page into the earlier ones.

    A leaf repeats an earlier leaf when every node it cites is a later-page node
    whose text appears once on each page, the earlier leaf cites those counterparts
    and both carry the same observed value. Statement meanings follow the same node
    identity in the same resolution state. Text identity applies the model's page
    relation to repeated wording; it never relates pages by itself. The later page's
    nodes join the schema and value evidence and the correction record; the
    assertion keeps the earlier definition's own references, so applicability
    candidates present each definition as it was interpreted.
    """
    leaves = list(_leaves(compiled))
    for right, record, schema_path, value in leaves:
        refs = record.get("sourceRefs") or []
        if record not in right.value_evidence or not refs:
            continue
        if any(ref not in counterparts for ref in refs):
            continue
        mapped = {counterparts[ref] for ref in refs}
        matches = [
            (left, other, other_schema)
            for left, other, other_schema, other_value in leaves
            if other is not record
            and other in left.value_evidence
            and mapped <= set(other.get("sourceRefs") or [])
            and other.get("status") == record.get("status")
            and type(other_value) is type(value)
            and other_value == value
        ]
        if len(matches) != 1:
            continue
        left, other, other_schema = matches[0]
        data_old, data_new = record["target"]["path"], other["target"]["path"]
        for item in list(right.semantics):
            if item["kind"] != "field_definition" or not any(
                t["space"] == "dataSchema" and t["path"] == schema_path for t in item["targets"]
            ):
                continue
            for earlier in left.semantics:
                if earlier["kind"] == "field_definition" and any(
                    t["space"] == "dataSchema" and t["path"] == other_schema
                    for t in earlier["targets"]
                ):
                    aliases[item["id"]] = earlier["id"]
                    _drop_semantic(right, item["id"])
                    break
        other["sourceRefs"] = _union(other["sourceRefs"], record["sourceRefs"])
        other["semanticIds"] = _union(other["semanticIds"], record["semanticIds"])
        right.value_evidence.remove(record)
        _delete_leaf(right, data_old, schema_path)
        mapping = [(("data", data_old), data_new), (("dataSchema", schema_path), other_schema)]
        for target in _targets(right):
            _rewrite(target, mapping)
        right.corrections.append(
            {
                "code": "repeated_statement_merged",
                "regionId": right.id,
                "candidateId": candidate_id,
                "path": data_old,
                "into": data_new,
                "sourceRefs": list(record["sourceRefs"]),
                "basis": "same_text_and_value_on_joined_page",
            }
        )
    details = [(region, detail) for region in compiled for detail in region.semantic_details]
    for right, detail in details:
        refs = detail.get("sourceRefs") or []
        if detail["id"] in aliases or not refs or any(ref not in counterparts for ref in refs):
            continue
        mapped = {counterparts[ref] for ref in refs}
        matches = [
            (left, other)
            for left, other in details
            if other is not detail
            and other["id"] not in aliases
            and other["kind"] == detail["kind"]
            and mapped <= set(other.get("sourceRefs") or [])
            and _unresolved(left, other["id"]) == _unresolved(right, detail["id"])
        ]
        if not matches:
            # One later statement may restate several earlier statements of the same
            # kind at once (a raster page's two condition lines read as one meaning
            # while the native page interpreted them separately). When earlier
            # meanings cover every mapped source, the later meaning repeats them.
            covering = [
                (left, other)
                for left, other in details
                if other is not detail
                and other["id"] not in aliases
                and other["kind"] == detail["kind"]
                and set(other.get("sourceRefs") or [])
                and set(other.get("sourceRefs") or []) <= mapped
            ]
            covered = {ref for _, other in covering for ref in other["sourceRefs"]}
            if covering and covered == mapped:
                item = next(i for i in right.semantics if i["id"] == detail["id"])
                if not _unresolved(right, detail["id"]):
                    for left, other in covering:
                        earlier = next(i for i in left.semantics if i["id"] == other["id"])
                        earlier["targets"] = _union(earlier["targets"], item["targets"])
                        earlier["scope"] = _union(earlier["scope"], item["scope"])
                        other["scope"] = _union(other["scope"], detail["scope"])
                aliases[detail["id"]] = covering[0][1]["id"]
                right.corrections.append(
                    {
                        "code": "repeated_meaning_merged",
                        "regionId": right.id,
                        "candidateId": candidate_id,
                        "semanticId": detail["id"],
                        "into": covering[0][1]["id"],
                        "alsoInto": [other["id"] for _, other in covering[1:]],
                        "sourceRefs": list(item["sourceRefs"]),
                        "basis": "same_statement_texts_on_joined_page",
                    }
                )
                _drop_semantic(right, detail["id"])
            continue
        if len(matches) != 1:
            continue
        left, other = matches[0]
        item = next(i for i in right.semantics if i["id"] == detail["id"])
        earlier = next(i for i in left.semantics if i["id"] == other["id"])
        if not _unresolved(right, detail["id"]):
            earlier["targets"] = _union(earlier["targets"], item["targets"])
            earlier["scope"] = _union(earlier["scope"], item["scope"])
            other["scope"] = _union(other["scope"], detail["scope"])
        aliases[detail["id"]] = other["id"]
        right.corrections.append(
            {
                "code": "repeated_meaning_merged",
                "regionId": right.id,
                "candidateId": candidate_id,
                "semanticId": detail["id"],
                "into": other["id"],
                "sourceRefs": list(item["sourceRefs"]),
                "basis": "same_statement_text_on_joined_page",
            }
        )
        _drop_semantic(right, detail["id"])


def _extend_continuation_definitions(left, right, candidate_id):
    """Let the earlier fragment's table definitions also govern the appended rows.

    Column i of the right table is column i of the left under the decided relation,
    so a column handle of the earlier fragment means every data row including the
    appended ones. The later fragment keeps its own definitions and row geometry for
    fragment-bounded selections.
    """
    by_targets = {
        frozenset((t["space"], t["path"]) for t in item["targets"]): item
        for item in left.semantics
        if item["kind"] == "field_definition"
    }
    for item in right.semantics:
        if item["kind"] != "field_definition":
            continue
        earlier = by_targets.get(frozenset((t["space"], t["path"]) for t in item["targets"]))
        if earlier is None:
            continue
        added = [t for t in item["scope"] if t not in earlier["scope"]]
        if not added:
            continue
        earlier["scope"] = _union(earlier["scope"], item["scope"])
        right.corrections.append(
            {
                "code": "continuation_definition_extended",
                "regionId": right.id,
                "candidateId": candidate_id,
                "semanticId": earlier["id"],
                "addedFrom": item["id"],
                "addedTargets": [t["path"] for t in added],
                "basis": "same_column_position_under_the_decided_relation",
            }
        )


def _merge_duplicate_table(left, right, a, candidate_id, aliases):
    """A duplicate presentation adds provenance to the earlier rows, never rows."""
    by_path = {(e["target"]["space"], e["target"]["path"]): e for e in left.value_evidence}
    kept = []
    for record in right.value_evidence:
        key = (record["target"]["space"], record["target"]["path"])
        earlier = by_path.get(key)
        if earlier is None or not (key[1] == a["path"] or key[1].startswith(a["path"] + "/")):
            kept.append(record)
            continue
        earlier["sourceRefs"] = _union(earlier["sourceRefs"], record["sourceRefs"])
        earlier["semanticIds"] = _union(earlier["semanticIds"], record["semanticIds"])
    right.value_evidence[:] = kept
    signatures = {_signature(item): item for item in left.semantics}
    for item in list(right.semantics):
        earlier = signatures.get(_signature(item))
        if earlier is None:
            continue
        aliases[item["id"]] = earlier["id"]
        right.corrections.append(
            {
                "code": "duplicate_table_definition_merged",
                "regionId": right.id,
                "candidateId": candidate_id,
                "semanticId": item["id"],
                "into": earlier["id"],
                "sourceRefs": list(item["sourceRefs"]),
                "basis": "same_definition_over_the_same_rows",
            }
        )
        _drop_semantic(right, item["id"])


def unescape(segment):
    return segment.replace("~1", "/").replace("~0", "~")


def _column_renames(a, b):
    """Right keys that differ from the left key of the same column position."""
    keys_a, keys_b = a.get("columnKeys"), b.get("columnKeys")
    if not keys_a or not keys_b or set(keys_a) != set(keys_b):
        return {}
    return {keys_b[c]: keys_a[c] for c in keys_a if keys_b[c] != keys_a[c]}


def _rename_keys(row, renamed):
    return {renamed.get(k, k): v for k, v in row.items()}


def _rename_row_schema(schema, renamed):
    items = schema.get("items", {})
    properties = {renamed.get(k, k): v for k, v in items.get("properties", {}).items()}
    required = [renamed.get(k, k) for k in items.get("required", [])]
    return {**schema, "items": {**items, "properties": properties, "required": required}}


def join_continuations(compiled, candidates, decisions):
    """Apply decided table relations and rewrite dependent generated pointers only.

    continue appends the right rows; duplicate binds the right presentation to the
    same rows and adds only provenance; both fold statements repeated on the joined
    page into the earlier fields and meanings.
    """
    result = copy.deepcopy(compiled)
    regions = {r.id: r for r in result}
    issues, relations = [], []
    roots = {}
    for candidate in candidates:
        decision = decisions.get(candidate["id"])
        duplicate = not candidate["confirmed"] and decision == "duplicate"
        if not candidate["confirmed"] and decision not in {"continue", "duplicate"}:
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
            if a["columns"] != b["columns"]:
                raise ValueError
            # Regions are interpreted independently, so the later fragment names the
            # same columns with its own keys. Under the decided relation column i of
            # the right table is column i of the left, so the right keys follow the
            # left keys; value types must still agree.
            renamed = _column_renames(a, b)
            if renamed:
                rows_b = [_rename_keys(row, renamed) for row in rows_b]
                schema_b = _rename_row_schema(schema_b, renamed)
            if schema_a != schema_b:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            issues.append(
                {"code": "table_continuation_column_conflict", "candidateId": candidate["id"]}
            )
            continue
        if renamed:
            pointer(right.data, b["path"])[:] = rows_b
            row_schema = pointer(right.schema, b["schemaPath"])
            row_schema.clear()
            row_schema.update(schema_b)
            right.corrections.append(
                {
                    "code": "continuation_columns_renamed",
                    "regionId": right.id,
                    "candidateId": candidate["id"],
                    "tableRef": candidate["rightTable"],
                    "renamed": dict(renamed),
                    "basis": "same_column_positions_under_the_decided_relation",
                }
            )
        if duplicate:
            if rows_a != rows_b:
                issues.append(
                    {"code": "table_duplicate_rows_differ", "candidateId": candidate["id"]}
                )
                continue
            offset = 0
        else:
            offset = len(rows_a)
            rows_a.extend(rows_b)
        if candidate["confirmed"]:
            a["rowEnd"] = b["rowEnd"]

        def remap(target, a=a, b=b, offset=offset, renamed=renamed):
            key = "path"
            if target.get("space") == "dataSchema":
                old, new = b["schemaPath"], a["schemaPath"]
                if target[key] == old or target[key].startswith(old + "/"):
                    rest = target[key][len(old) :]
                    prefix = "/items/properties/"
                    if renamed and rest.startswith(prefix):
                        column, slash, tail = rest[len(prefix) :].partition("/")
                        column = escape(renamed.get(unescape(column), unescape(column)))
                        rest = prefix + column + slash + tail
                    target[key] = new + rest
            elif target.get("space") == "data":
                if target[key] == b["path"]:
                    target[key] = a["path"]
                elif target[key].startswith(b["path"] + "/"):
                    suffix = target[key][len(b["path"]) + 1 :]
                    row, slash, rest = suffix.partition("/")
                    if renamed and rest:
                        column, slash2, tail = rest.partition("/")
                        column = escape(renamed.get(unescape(column), unescape(column)))
                        rest = column + slash2 + tail
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
        repeat_id = next(k for k, v in right.repeat_paths.items() if v is b)
        for row_scope in right.row_scopes.values():
            if row_scope["tableRef"] == b["tableRef"]:
                for row in row_scope["rows"].values():
                    for target in row["targets"].values():
                        remap(target)
                row_scope["dataOwnerRegionId"] = left_id
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
        aliases = {}
        if duplicate:
            right.row_scopes.pop(repeat_id, None)
            right.repeat_paths.pop(repeat_id, None)
            _merge_duplicate_table(left, right, a, candidate["id"], aliases)
        else:
            _extend_continuation_definitions(left, right, candidate["id"])
        _merge_repeated_statements(
            result, candidate.get("nodeCounterparts") or {}, candidate["id"], aliases
        )
        if aliases:
            for region in result:
                for record in [*region.schema_evidence, *region.value_evidence]:
                    record["semanticIds"] = list(
                        dict.fromkeys(aliases.get(s, s) for s in record["semanticIds"])
                    )
        roots[candidate["rightRegion"]] = left_id
        relations.append(
            {
                "id": candidate["id"],
                "kind": "tableDuplicate" if duplicate else "tableContinuation",
                "sourceTable": candidate["leftTable"],
                "targetTable": candidate["rightTable"],
                "sourceRefs": candidate["sourceRefs"],
                "basis": candidate["basis"] if candidate["confirmed"] else "ai_interpreted",
                "target": {"space": "data", "path": a["path"]},
            }
        )
    return result, issues, relations
