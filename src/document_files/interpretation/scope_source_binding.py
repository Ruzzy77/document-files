"""Resolve compiler-owned provenance after explicit AI applicability selection.

Only the new citation-free wire may call this boundary. A returned decision still
requires apply_scope_decision; source binding is not semantic quality approval.
"""

from __future__ import annotations

import copy

from .compiler import CompileError
from .integration import ScopeDecision, _definition
from .scope_rows import resolve_row_selection, row_options

VERSION = "document-files.scope-source-binding.v1"
MAX_SOURCE_REFS = 100
MAX_TRACE_BINDINGS = 1000
MAX_ROW_WORK = 1000000


def _key(target):
    return target["space"], target["path"]


def bind_scope_sources(choice, task, compiled, *, expected_fingerprint):
    """Use existing selected definitions/bindings, never model prose or old citations.

    Explicit absent/uncertain observations without a value binding retain only
    source-row geometry provenance. They do not acquire a value or an observed
    source binding. The unchanged compiler decides completeness and applicability.
    """
    if task.fingerprint != expected_fingerprint:
        raise CompileError("stale_bound_scope_task")
    if not isinstance(choice, dict) or choice.get("sourceRefs") != []:
        raise CompileError("cannot_replace_model_scope_citations")
    candidate = copy.deepcopy(choice)
    candidate["sourceRefs"] = list(task.source_signature["sourceRefs"])
    try:
        selected = ScopeDecision.model_validate(candidate)
    except ValueError:
        raise CompileError("invalid_bound_scope_choice") from None
    if selected.taskId != task.id:
        raise CompileError("bound_scope_task_mismatch")
    trace = {
        "version": VERSION,
        "taskFingerprint": task.fingerprint,
        "selectionBasis": "ai_interpreted",
        "sourceBindingBasis": "compiler_catalog",
        "modelSuppliedSourceRefs": False,
        "bindings": [],
    }
    if selected.decision == "unresolved":
        return copy.deepcopy(choice), trace
    owners = {region.id: region for region in compiled}
    if len(owners) != len(compiled):
        raise CompileError("ambiguous_bound_scope_region")
    refs, seen_refs = [], set()

    def add(values, basis, **detail):
        if len(trace["bindings"]) >= MAX_TRACE_BINDINGS:
            raise CompileError("scope_source_trace_budget_exceeded")
        for ref in values:
            if not isinstance(ref, str) or not ref:
                raise CompileError("unavailable_bound_scope_source")
            if ref not in seen_refs:
                if len(refs) >= MAX_SOURCE_REFS:
                    raise CompileError("scope_source_binding_budget_exceeded")
                seen_refs.add(ref)
                refs.append(ref)
        trace["bindings"].append({"basis": basis, "sourceRefs": list(values), **detail})

    def current_definition(region, frozen):
        matches = [d for d in region.semantics if d["id"] == frozen["id"]]
        if (
            len(matches) != 1
            or _definition(matches[0]) != frozen
            or matches[0].get("status") != "interpreted"
            or matches[0].get("kind") != "field_definition"
        ):
            raise CompileError("stale_bound_scope_definition")
        return matches[0]

    try:
        add(task.source_signature["sourceRefs"], "given_content_sources")
        for handle in selected.targetHandles:
            target = task.target_map[handle]
            region = owners[target["regionId"]]
            definitions = target.get("definitions", [target.get("definition")])
            for frozen in definitions:
                definition = current_definition(region, frozen)
                add(
                    definition["sourceRefs"],
                    "selected_definition",
                    targetHandle=handle,
                    definitionId=definition["id"],
                )
            if "headerGroup" in target:
                group = target["headerGroup"]
                if region.repeat_paths.get(group["repeatId"]) != group["repeat"]:
                    raise CompileError("stale_bound_scope_group")
                add([group["header"]["sourceRef"]], "selected_group_header", targetHandle=handle)
        work, evidence_indexes = 0, {}
        public = {c["targetHandle"]: c for c in task.payload["candidates"]}
        for selection in selected.rowSelections:
            target = task.target_map[selection.targetHandle]
            region = owners[target["regionId"]]
            scope = target["rowScope"]
            catalog = region.row_scopes[scope["repeatId"]]
            record_definition = current_definition(region, target["definition"])
            if catalog != scope["mapping"] or row_options(region, scope["repeatId"]) != public[
                selection.targetHandle
            ].get("rowOptions"):
                raise CompileError("stale_bound_scope_row_mapping")
            work += (selection.rowEnd - selection.rowStart + 1) * (
                len(selection.columnIds) or len(catalog["columns"])
            )
            if work > MAX_ROW_WORK:
                raise CompileError("scope_source_row_expansion_budget_exceeded")
            chosen, row_refs, complete = resolve_row_selection(catalog, selection)
            definitions = {d["id"]: d for d in region.semantics}
            selected_columns = selection.columnIds or list(catalog["columns"])
            if (
                len(trace["bindings"]) + len(selected_columns) + len(chosen) + 2
                > MAX_TRACE_BINDINGS
            ):
                raise CompileError("scope_source_trace_budget_exceeded")
            for column in selected_columns:
                definition = definitions[catalog["columns"][column]["definitionId"]]
                add(
                    definition["sourceRefs"],
                    "selected_column_definition",
                    targetHandle=selection.targetHandle,
                    columnId=column,
                    definitionId=definition["id"],
                )
            if region.id not in evidence_indexes:
                index = {}
                for evidence in region.value_evidence:
                    index.setdefault(_key(evidence["target"]), []).append(evidence)
                evidence_indexes[region.id] = index
            geometry = {}
            for coordinate in range(selection.rowStart, selection.rowEnd + 1):
                row = catalog["rows"].get(str(coordinate))
                if row and row["role"] == "data":
                    for column in selected_columns:
                        destination = row["targets"][column]
                        key = _key(destination)
                        if key in geometry:
                            raise CompileError("ambiguous_bound_scope_row_target")
                        geometry[key] = row
            for destination in chosen:
                evidence = evidence_indexes[region.id].get(_key(destination), [])
                if len(evidence) != 1:
                    raise CompileError("unavailable_or_ambiguous_scope_value_evidence")
                item = evidence[0]
                binding = item.get("binding")
                if isinstance(binding, dict) and binding.get("sourceRef"):
                    ref = binding["sourceRef"]
                    if ref not in row_refs or ref not in geometry[_key(destination)]["sourceRefs"]:
                        raise CompileError("scope_binding_outside_selected_row")
                    add(
                        [ref],
                        "selected_value_binding",
                        targetHandle=selection.targetHandle,
                        destination=copy.deepcopy(destination),
                        observationStatus=item["status"],
                    )
                elif binding is None and item.get("status") in {"absent", "uncertain"}:
                    # Existing missingness only: this is row context, not an
                    # invented cell source, successful recognition or blank.
                    row_sources = geometry[_key(destination)]["sourceRefs"]
                    if not row_sources:
                        raise CompileError("unavailable_scope_row_geometry_source")
                    add(
                        row_sources,
                        "selected_row_geometry_without_value_binding",
                        targetHandle=selection.targetHandle,
                        destination=copy.deepcopy(destination),
                        observationStatus=item["status"],
                    )
                else:
                    raise CompileError("unavailable_scope_value_binding")
            # The record is context for a row filter, not an additional scope.
            # Reuse every already-bound reference belonging to its definition;
            # do not attach unrelated record cells as new value evidence. This
            # is an explicit anchor rule, not truncation to a source-count cap.
            anchors = [r for r in record_definition["sourceRefs"] if r in seen_refs]
            add(
                anchors or record_definition["sourceRefs"],
                "selected_record_definition",
                targetHandle=selection.targetHandle,
                definitionId=record_definition["id"],
                referenceMode="already_bound_intersection" if anchors else "full_definition",
            )
            add(
                [],
                "row_mapping_completeness",
                complete=complete,
                targetHandle=selection.targetHandle,
            )
        result = copy.deepcopy(choice)
        result["sourceRefs"] = refs
        ScopeDecision.model_validate(result)
        return result, trace
    except (KeyError, TypeError, AttributeError):
        raise CompileError("unavailable_bound_scope_mapping") from None


def bind_scope_choices(decoded, tasks, compiled):
    """Bind independent unique decisions without hiding duplicate or invalid siblings."""
    batched = len(tasks) != 1
    if batched:
        if not isinstance(decoded, dict) or set(decoded) != {"decisions"}:
            raise ValueError("invalid_bound_scope_batch")
        values = decoded["decisions"]
        if not isinstance(values, list) or not 1 <= len(values) <= 8:
            raise ValueError("invalid_bound_scope_batch")
    else:
        values = [decoded]
    expected = {task.id: task for task in tasks}
    counts = {}
    for value in values:
        identifier = value.get("taskId") if isinstance(value, dict) else None
        if isinstance(identifier, str):
            counts[identifier] = counts.get(identifier, 0) + 1
    bound, traces = [], {}
    for value in values:
        identifier = value.get("taskId") if isinstance(value, dict) else None
        try:
            if (
                not isinstance(identifier, str)
                or counts[identifier] != 1
                or identifier not in expected
            ):
                raise ValueError("unexpected_or_duplicate_bound_scope_task")
            task = expected[identifier]
            decision, trace = bind_scope_sources(
                value, task, compiled, expected_fingerprint=task.fingerprint
            )
            bound.append(decision)
            traces[identifier] = trace
        except (ValueError, TypeError):
            bound.append({"taskId": copy.deepcopy(identifier), "targetHandles": None})
    return ({"decisions": bound} if batched else bound[0]), traces
