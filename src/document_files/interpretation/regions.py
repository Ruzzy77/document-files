"""Bounded regional views with original observations and table identities intact."""

from __future__ import annotations

import copy
import json

from ..document_model.table_headers import declared_header
from .compiler import preferred_binding
from .table_protocol import STRUCTURE_SYSTEM, structure_payload, structure_schema
from .text_views import split_text_region

REGION_PLAN_VERSION = "document-files.region-plan.v14"


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _model_locator(locator):
    """Keep document geometry, not duplicate recognizer audit coordinates."""
    result = {
        key: value
        for key, value in locator.items()
        if key
        not in {
            "characters",
            "provenance",
            "rectangle",
            "tableBBox",
            "doclingRef",
            "backendCellIndex",
            "cellOrdinal",
            "recognitionBatch",
            "pdfium",
        }
    }
    if isinstance(result.get("bbox"), dict):
        result["bbox"] = {k: v for k, v in result["bbox"].items() if k != "sourceBox"}
    return result


def model_node(node):
    """Private meaning-bearing view; the original observation stays untouched.

    Character alignment traces establish observation equivalence in program code;
    sending them again for every cell does not add semantic context. Keep their
    status, conflicts and original source identities, along with exact text/types.
    """
    result = {
        key: value
        for key, value in node.items()
        if key
        in {
            "text",
            "semantic",
            "semanticRole",
            "semanticInput",
            "sourceStructure",
            "observationBasis",
            "parentRef",
            "normalization",
            "sourceSegments",
            "recognizedText",
        }
    }
    if isinstance(result.get("sourceStructure"), dict):
        result["sourceStructure"] = _model_locator(result["sourceStructure"])
    if isinstance(result.get("semanticInput"), dict):
        result["semanticInput"] = {
            k: v for k, v in result["semanticInput"].items() if k != "sourceSegments"
        }
    return result


def _table_payload(table):
    """Lossless, model-only columnar cells; never change the stored observation.

    Heterogeneous keys keep their original objects so absent properties cannot
    become explicit nulls. Small lists also stay objects if packing costs more.
    """
    result = {key: value for key, value in table.items() if key != "contextNodeIds"}
    if isinstance(result.get("locator"), dict):
        result["locator"] = _model_locator(result["locator"])
    for key in ("cells", "headerCells", "leadingCells"):
        cells = table.get(key)
        if not isinstance(cells, list) or not cells or not all(isinstance(c, dict) for c in cells):
            continue
        columns = list(cells[0])
        if not all(set(cell) == set(columns) for cell in cells):
            continue
        packed = {
            "encoding": "columns-rows.v1",
            "columns": columns,
            "rows": [[cell[column] for column in columns] for cell in cells],
        }
        if len(_encoded(packed)) < len(_encoded(cells)):
            result[key] = packed
    result["columnCandidates"], result["dataRows"] = column_candidates(table)
    return result


def column_candidates(table):
    """Derive column indices with their declared header cells and the observed data rows.

    Geometry is program work: the interpreter names, types and scopes columns but
    does not invent column indices. Header references come only from cells declared
    as headers; tables without declared headers get empty header lists, not guesses.
    """
    cells = table.get("cells", [])
    headers = {}
    for cell in sorted(
        [*cells, *table.get("headerCells", [])], key=lambda c: (c.get("row", 0), c.get("col", 0))
    ):
        if declared_header(cell, table):
            headers.setdefault(cell["sourceRef"], cell)
    col_count = max(
        table.get("colCount", 0) or 0,
        max((c["col"] + c.get("colSpan", 1) for c in [*cells, *headers.values()]), default=0),
    )
    candidates = [
        {
            "column": column,
            "headerRefs": [
                ref
                for ref, cell in headers.items()
                if cell["col"] <= column < cell["col"] + cell.get("colSpan", 1)
            ],
        }
        for column in range(col_count)
    ]
    data_rows = sorted(
        {
            row
            for cell in cells
            if not declared_header(cell, table)
            for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1))
        }
    )
    data_range = {"rowStart": data_rows[0], "rowEnd": data_rows[-1]} if data_rows else None
    return candidates, data_range


def region_payload(observation, region):
    node_ids = [
        ref
        for ref in dict.fromkeys([*region["nodeIds"], *region.get("contextNodeIds", [])])
        if observation.nodes.get(ref, {}).get("semanticRole") != "source_text"
    ]
    node_set = set(node_ids)
    binding_set = set(region["bindingIds"])
    # Resources are kept in the result, not copied into inference requests.
    nodes = {
        ref: model_node(observation.nodes[ref]) for ref in node_ids if ref in observation.nodes
    }
    if region.get("tableRef"):
        table = observation.tables[region["tableRef"]]
        for cell in [
            *table.get("cells", []),
            *table.get("headerCells", []),
            *table.get("leadingCells", []),
        ]:
            node = nodes.get(cell["sourceRef"])
            if node is not None and node.get("semanticRole") == "table_cell":
                # Membership, row/column/spans, page and recognition basis are
                # already explicit in the table. Preserve cell-specific status.
                for key in (
                    "sourceStructure",
                    "semanticRole",
                    "observationBasis",
                    "recognizedText",
                ):
                    node.pop(key, None)
                if node.get("semanticInput", {}).get("basis") == "native_character_geometry":
                    node["semanticInput"] = {
                        k: v for k, v in node["semanticInput"].items() if k != "basis"
                    }
    for ref, window in region.get("nodeViews", {}).items():
        if ref in nodes:
            nodes[ref] = {
                **nodes[ref],
                "text": observation.nodes[ref].get("text", "")[window["start"] : window["end"]],
                "textRange": {"path": "/text", **window},
            }
    boundary = [
        {
            **item,
            "text": observation.nodes[item["sourceRef"]].get("text", "")[
                item["textRange"]["start"] : item["textRange"]["end"]
            ],
            "role": "context_only",
        }
        for item in region.get("boundaryContext", [])
    ]
    table_ref = region.get("tableRef") or region.get("tableContextRef")
    tables = {table_ref: _table_payload(observation.tables[table_ref])} if table_ref else {}
    for table in tables.values():
        for candidate in table.get("columnCandidates", []):
            # Header levels are joined with " > " so bilingual "A / B" labels stay intact.
            candidate["headerText"] = " > ".join(
                observation.nodes.get(ref, {}).get("text", "") for ref in candidate["headerRefs"]
            )
    return {
        "regionId": region["id"],
        "nodeIds": region["nodeIds"],
        "contextNodeIds": region.get("contextNodeIds", []),
        "nodes": nodes,
        "bindings": {b: observation.bindings[b] for b in region["bindingIds"]},
        "requiredBindingIds": region.get("requiredBindingIds", []),
        "tables": tables,
        **(
            {
                "tableKind": "nonrecord_values",
                "valueRegion": {
                    "parentRegionId": region["parentRegionId"],
                    "instruction": (
                        "Read the offered bindings as scalar values outside the compiled record. "
                        "Table cells in context are definitions/context, not additional values. "
                        "Preserve subtotal and note contents; never regenerate records."
                    ),
                },
            }
            if region.get("tableContextRef")
            else {}
        ),
        **({"boundaryContext": boundary} if boundary else {}),
        "relations": [
            r
            for r in (
                (observation.relations[i] for i in region["relationIds"])
                if "relationIds" in region
                else observation.relations
            )
            if (
                r.get("sourceRef") in node_set
                or r.get("targetRef") in node_set
                or r.get("tableRef") in tables
                or (r.get("labelBinding") in binding_set and r.get("valueBinding") in binding_set)
            )
            and r.get("kind") not in {"recognitionSourceSupport", "observationEquivalence"}
            and (
                r.get("kind") != "contains"
                or (r.get("sourceRef") in node_set and r.get("targetRef") in node_set)
            )
        ],
    }


def route_table_values(observation, region, frozen, compiled, *, context_chars, metadata):
    """Give observed non-record cells an ordinary scalar region, not a made-up value.

    The parent keeps them as meaning context. Only their value bindings/owned
    nodes move; source observations, geometry and compiled record reads do not.
    Unclassified rows remain unresolved in the parent until structure is repaired.
    """
    from .semantic_prompts import SYSTEM
    from .semantic_types import region_output_schema

    table = observation.tables[region["tableRef"]]
    roles = {role.row: role.role for repeat in frozen.repeats for role in repeat.rowRoles}
    read_sources = {observation.bindings[bid]["sourceRef"] for bid in compiled.consumed_bindings}
    record_sources = {
        cell["sourceRef"]
        for repeat in frozen.repeats
        for cell in table["cells"]
        if any(
            roles.get(row) == "data"
            for row in range(
                max(repeat.rowStart, cell["row"]),
                min(repeat.rowEnd + 1, cell["row"] + cell.get("rowSpan", 1)),
            )
        )
        and any(
            cell["col"] <= column.column < cell["col"] + cell.get("colSpan", 1)
            for column in repeat.columns
        )
    }
    definitions = {d["sourceRef"] for d in compiled.dispositions if d.get("role") == "structural"}
    routed, record = set(), []
    for cell in table["cells"]:
        ref = cell["sourceRef"]
        if ref in read_sources:
            destination = "record_value"
        elif ref in record_sources:
            destination = "unresolved"
        elif declared_header(cell, table) or ref in definitions:
            destination = "definition"
        elif any(
            roles.get(row, "unresolved") == "unresolved"
            for row in range(cell["row"], cell["row"] + cell.get("rowSpan", 1))
        ):
            destination = "unresolved"
        else:
            destination = "scalar_region"
            routed.update(cell.get("sourceRefs", [ref]))
            routed.add(ref)
        record.append({"sourceRef": ref, "valueRoute": destination})
    routed &= set(region["nodeIds"])
    if not routed:
        return None, record
    child_id = region["id"] + ":nonrecord-values"
    bids = [bid for bid in region["bindingIds"] if observation.bindings[bid]["sourceRef"] in routed]
    child = {
        "id": child_id,
        "parentRegionId": region["id"],
        "tableContextRef": region["tableRef"],
        "nodeIds": [ref for ref in region["nodeIds"] if ref in routed],
        "contextNodeIds": list(
            dict.fromkeys(
                [
                    *[ref for ref in region["nodeIds"] if ref not in routed],
                    *region.get("contextNodeIds", []),
                ]
            )
        ),
        "bindingIds": bids,
        "requiredBindingIds": [bid for bid in region.get("requiredBindingIds", []) if bid in bids],
    }
    request = {
        **region_payload(observation, child),
        **metadata,
        "outputContract": region_output_schema(observation, child, metadata.get("targetHandles")),
    }
    child["inputChars"] = len(_encoded(region_payload(observation, child)))
    child["requestChars"] = len(SYSTEM) + len(_encoded(request))
    child["withinContextBudget"] = child["requestChars"] <= context_chars
    child["budgetReason"] = "nonrecord_value_region_exceeds_budget"
    region["nodeIds"] = [ref for ref in region["nodeIds"] if ref not in routed]
    region["contextNodeIds"] = list(
        dict.fromkeys([*region.get("contextNodeIds", []), *child["nodeIds"]])
    )
    region["bindingIds"] = [bid for bid in region["bindingIds"] if bid not in bids]
    region["requiredBindingIds"] = [
        bid for bid in region.get("requiredBindingIds", []) if bid not in bids
    ]
    for item in record:
        if item["valueRoute"] == "scalar_region":
            item["regionId"] = child_id
    return child, record


def prepare_regions(observation, *, context_chars, request_metadata=None):
    """Pack text, split exact source windows and preserve observed table row boundaries."""
    source_regions = copy.deepcopy(observation.regions)
    listing = {}
    for region in source_regions:
        if region.get("tableRef"):
            for ref in region.get("contextNodeIds", []):
                if observation.nodes.get(ref, {}).get("semanticRole") == "caption":
                    listing.setdefault(ref, []).append(region["tableRef"])
    # A caption that exactly one table lists is that table's own context: interpret
    # it with the table instead of as a separate region of guessed scalar values.
    captions = {ref for ref, tables in listing.items() if len(tables) == 1}
    if captions:
        kept = []
        for region in source_regions:
            if region.get("tableRef"):
                own = [
                    ref
                    for ref in region.get("contextNodeIds", [])
                    if ref in captions and ref not in region["nodeIds"]
                ]
                region["nodeIds"] = [*own, *region["nodeIds"]]
                kept.append(region)
                continue
            region["nodeIds"] = [ref for ref in region["nodeIds"] if ref not in captions]
            if region["nodeIds"]:
                kept.append(region)
        source_regions = kept
    result = []
    binding_by_node = {}
    for bid, binding in observation.bindings.items():
        # Token spans stay in the observation but are not offered as value choices:
        # the interpreter cannot identify them by offset and guessed spans produced
        # meaningless values. Whole-node content and delimiter pairs remain.
        if binding.get("candidateRole") == "lexeme":
            continue
        binding_by_node.setdefault(binding["sourceRef"], []).append(bid)

    relations_by_ref = {}
    relations_by_binding = {}
    for index, relation in enumerate(observation.relations):
        # Index contains by child only: a container with 100k children must not
        # re-scan all siblings for every bounded view. Both ends are checked later.
        keys = (
            ("targetRef",)
            if relation.get("kind") == "contains"
            else ("sourceRef", "targetRef", "tableRef")
        )
        for key in keys:
            if relation.get(key):
                relations_by_ref.setdefault(relation[key], []).append(index)
        if relation.get("valueBinding"):
            relations_by_binding.setdefault(relation["valueBinding"], []).append(index)

    def payload(observation, region):
        indices = set()
        for ref in [*region["nodeIds"], *region.get("contextNodeIds", []), region.get("tableRef")]:
            indices.update(relations_by_ref.get(ref, []))
        for bid in region["bindingIds"]:
            indices.update(relations_by_binding.get(bid, []))
        region["relationIds"] = sorted(indices)
        return region_payload(observation, region)

    def enrich(region):
        owned = set(region["nodeIds"])
        redundant_context = {
            ref
            for ref in region.get("contextNodeIds", [])
            if observation.nodes.get(ref, {}).get("semanticInput", {}).get("role")
            == "source_overlap_not_independent"
            and observation.nodes[ref]["semanticInput"].get("representativeRefs")
            and set(observation.nodes[ref]["semanticInput"]["representativeRefs"]) <= owned
        }
        context = [
            n
            for n in region.get("contextNodeIds", [])
            if n not in owned
            and n not in redundant_context
            and observation.nodes.get(n, {}).get("semanticRole") != "source_text"
        ]
        region["contextNodeIds"] = list(dict.fromkeys(context))
        # Table context explains definitions/applicability, not extra scalar
        # values. Its owner region retains any actual value bindings.
        selected_nodes = (
            region["nodeIds"] if region.get("tableRef") else [*region["nodeIds"], *context]
        )
        # Declared header cells and captions define or describe values; offering
        # their text as value choices made the interpreter read labels as data.
        not_values = {
            ref
            for ref in selected_nodes
            if observation.nodes.get(ref, {}).get("semanticRole") == "caption"
        }
        if region.get("tableRef"):
            not_values.update(
                cell["sourceRef"]
                for cell in observation.tables[region["tableRef"]]["cells"]
                if declared_header(cell, observation.tables[region["tableRef"]])
            )
        region["bindingIds"] = list(
            dict.fromkeys(
                bid
                for ref in selected_nodes
                if ref not in not_values
                for bid in binding_by_node.get(ref, [])
            )
        )
        required = []
        for ref in region["nodeIds"]:
            values = [
                bid
                for bid in binding_by_node.get(ref, [])
                if observation.bindings[bid].get("candidateRole") == "value"
            ]
            native = [
                bid
                for bid in binding_by_node.get(ref, [])
                if observation.bindings[bid].get("candidateRole") == "native_value"
            ]
            if values:
                required.extend(values)
            elif native:
                selected = preferred_binding(observation.bindings, ref)
                if selected:
                    required.append(selected)
        if region.get("tableRef"):
            for cell in observation.tables[region["tableRef"]]["cells"]:
                if declared_header(cell, observation.tables[region["tableRef"]]):
                    # Declared header text defines columns; requiring it as a value
                    # candidate pushed the interpreter to read headers as data.
                    continue
                cell_bindings = {
                    bid: observation.bindings[bid]
                    for bid in binding_by_node.get(cell["sourceRef"], [])
                }
                selected = preferred_binding(cell_bindings, cell["sourceRef"])
                if selected:
                    required.append(selected)
        region["requiredBindingIds"] = list(dict.fromkeys(required))
        return region

    limit = max(2000, context_chars - 12000)
    metadata = request_metadata or {"intent": "discover", "targetHandles": {}}

    def table_request_chars(region):
        # Plan the actual structure decision, not the retired all-in-one record
        # response. Meaning/scalar requests are checked against the same hard
        # input limit when dispatched; failed meaning retains frozen structure.
        request = {
            **structure_payload({**payload(observation, region), **metadata}),
            "outputContract": structure_schema(observation, region, metadata.get("targetHandles")),
        }
        return len(STRUCTURE_SYSTEM) + len(_encoded(request))

    def table_fits(region):
        # Final generated region IDs can differ from source IDs.
        return table_request_chars(region) + 64 <= context_chars

    current = None
    for source_region in source_regions:
        region = enrich(source_region)
        if region.get("tableRef"):
            if current:
                result.append(current)
                current = None
            if table_fits(region):
                result.append(region)
                continue
            table_ref = region["tableRef"]
            table = observation.tables[table_ref]
            row_cells, positions = {}, 0
            for cell_index, cell in enumerate(table["cells"]):
                span = cell.get("rowSpan", 1)
                positions += span
                if positions > 1000000:
                    break
                for row in range(cell["row"], cell["row"] + span):
                    row_cells.setdefault(row, []).append(cell_index)
                if len(row_cells) > 100000:
                    break
            if positions > 1000000 or len(row_cells) > 100000:
                observation.issue("table_region_row_budget_exceeded", tableRef=table_ref)
                result.append(region)
                continue
            rows = sorted(row_cells)
            if not rows:
                result.append(region)
                continue
            # Headers and linked notes remain context, even on later row slices.
            header_nodes = [c["sourceRef"] for c in table["cells"] if declared_header(c, table)]
            # With undeclared OCR headers, keep the observed first row as
            # unclassified context, not as an invented header or new value owner.
            leading_cells = (
                [table["cells"][i] for i in row_cells[rows[0]]] if not header_nodes else []
            )
            cell_nodes = {n for c in table["cells"] for n in c.get("sourceRefs", [c["sourceRef"]])}
            # Owned non-cell context (the table's caption) is read with the first slice.
            owned_context = [n for n in region["nodeIds"] if n not in cell_nodes]
            emitted = []
            # A leading declared header block is context for each data view,
            # not a synthetic header-only table for the model to turn into data.
            # Do not guess headers for OCR tables or drop an all-header blank form.
            header_count = 0
            for row in rows:
                if not all(declared_header(table["cells"][i], table) for i in row_cells[row]):
                    break
                header_count += 1
            if header_count < len(rows):
                rows = rows[header_count:]
            bucket = []

            def view(
                row_ids,
                table=table,
                table_ref=table_ref,
                region=region,
                header_nodes=header_nodes,
                leading_cells=leading_cells,
                row_cells=row_cells,
                owned_context=owned_context,
                emitted=emitted,
            ):
                cells = [
                    table["cells"][index]
                    for index in sorted({i for row in row_ids for i in row_cells[row]})
                ]
                view_ref = f"{table_ref}#rows:{row_ids[0]}:{row_ids[-1]}"
                shown_refs = {c["sourceRef"] for c in cells}
                leading_context = [c for c in leading_cells if c["sourceRef"] not in shown_refs]
                observation.tables[view_ref] = {
                    **table,
                    "id": view_ref,
                    "cells": cells,
                    "headerCells": [
                        copy.deepcopy(c) for c in table["cells"] if declared_header(c, table)
                    ],
                    "leadingCells": copy.deepcopy(leading_context),
                    "sourceTableRef": table_ref,
                    "viewRowStart": row_ids[0],
                    "viewRowEnd": row_ids[-1],
                    "derivation": "bounded_row_view",
                }
                return enrich(
                    {
                        "id": f"{region['id']}:{row_ids[0]}",
                        "tableRef": view_ref,
                        "nodeIds": list(
                            dict.fromkeys(
                                [
                                    *([] if emitted else owned_context),
                                    *(
                                        n
                                        for c in cells
                                        for n in c.get("sourceRefs", [c["sourceRef"]])
                                    ),
                                ]
                            )
                        ),
                        "contextNodeIds": [
                            *region["contextNodeIds"],
                            *header_nodes,
                            *(c["sourceRef"] for c in leading_context),
                        ],
                        "bindingIds": [],
                    }
                )

            for row in rows:
                candidate = view([*bucket, row])
                if bucket and not table_fits(candidate):
                    result.append(view(bucket))
                    emitted.append(True)
                    bucket = [row]
                else:
                    bucket.append(row)
            if bucket:
                result.append(view(bucket))
                emitted.append(True)
            # Remove trial views not used by a final region.
            used = {r.get("tableRef") for r in result}
            for ref in list(observation.tables):
                if ref.startswith(table_ref + "#rows:") and ref not in used:
                    del observation.tables[ref]
            continue
        if current is None:
            current = region
        else:
            merged = enrich(
                {
                    "id": current["id"],
                    "nodeIds": [*current["nodeIds"], *region["nodeIds"]],
                    "contextNodeIds": [*current["contextNodeIds"], *region["contextNodeIds"]],
                    "bindingIds": [],
                }
            )
            if len(_encoded(payload(observation, merged))) <= limit:
                current = merged
            else:
                result.append(current)
                current = region
    if current:
        result.append(current)
    bounded = []
    for region in result:
        if not region.get("tableRef") and len(_encoded(payload(observation, region))) > limit:
            bounded.extend(
                split_text_region(observation, region, binding_by_node, limit - 64, payload)
            )
        else:
            bounded.append(region)
    result = bounded
    for index, region in enumerate(result, 1):
        region["id"] = f"semantic-region:{index}"
        region["inputChars"] = len(_encoded(payload(observation, region)))
        if region.get("tableRef"):
            region["requestChars"] = table_request_chars(region)
            region["withinContextBudget"] = region["requestChars"] <= context_chars
            if not region["withinContextBudget"]:
                region["budgetReason"] = "table_row_or_required_context_exceeds_budget"
        else:
            region["withinContextBudget"] = region["inputChars"] <= limit
        if not region["withinContextBudget"] and not region.get("tableRef"):
            region.setdefault("budgetReason", "atomic_candidate_or_context_exceeds_budget")
    return result


def continuation_candidates(observation, regions):
    """Require positional and structural evidence; header similarity alone is insufficient."""
    candidates = []
    table_regions = [r for r in regions if r.get("tableRef")]
    for left, right in zip(table_regions, table_regions[1:], strict=False):
        a, b = observation.tables[left["tableRef"]], observation.tables[right["tableRef"]]
        native_a, native_b = (
            a.get("sourceTableRef", left["tableRef"]),
            b.get("sourceTableRef", right["tableRef"]),
        )
        same_source = native_a == native_b
        page_a, page_b = a.get("page"), b.get("page")
        page_adjacent = type(page_a) is int and type(page_b) is int and page_b == page_a + 1
        if not same_source and not (page_adjacent and a.get("colCount") == b.get("colCount")):
            continue
        candidates.append(
            {
                "id": f"continuation:{len(candidates) + 1}",
                "leftRegion": left["id"],
                "rightRegion": right["id"],
                "leftTable": left["tableRef"],
                "rightTable": right["tableRef"],
                "basis": "same_native_table" if same_source else "adjacent_page_column_candidate",
                "confirmed": same_source,
                "sourceRefs": list(
                    dict.fromkeys(
                        [
                            *left.get("contextNodeIds", []),
                            *right.get("contextNodeIds", []),
                            *[c["sourceRef"] for c in a["cells"][-4:]],
                            *[c["sourceRef"] for c in b["cells"][:4]],
                        ]
                    )
                ),
            }
        )
    return candidates
