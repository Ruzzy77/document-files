"""Reversible image-reading proposals, never in-place OCR/table correction."""

from __future__ import annotations

from copy import deepcopy

from ..document_model.native import bind_spans
from .pdf_image_read import build_read_plan, validate_read
from .pdf_visual_plan import _box, digest, require

VERSION = "document-files.pdf-image-projection.v1"


def page_projection(doc, page):
    values = [p for p in doc.provenance.get("pdfImageReadProjections", []) if p["page"] == page]
    require(len(values) <= 1, "image_projection_page_duplicate")
    return values[0] if values else None


def grid_association(doc, page, observed):
    """An alternate association belongs to the review view, not the raw measurement."""
    projection = page_projection(doc, page)
    if projection:
        identity = observed.get("observation", {}).get("fingerprint")
        matches = [g for g in projection["grids"] if g["observationFingerprint"] == identity]
        require(len(matches) <= 1, "image_projection_grid_duplicate")
        if matches:
            return {"status": "unique_geometry_correspondence", "tableRef": matches[0]["tableRef"]}
    return observed.get("structureAssociation", {})


def propose_image_read(doc, reading, *, deadline, cancelled=None):
    """Prepare a separate active view for full-page text/geometry review.

    Original nodes, bindings, tables, regions, issues and OCR evidence are retained.
    No header role, unit, blank value or quality approval is assigned here. Unknown
    readings and ambiguous/overlapping region mappings keep the original view.
    """
    plan, validation = reading["plan"], reading["validation"]
    page = plan["page"]
    require(page_projection(doc, page) is None, "image_projection_already_present")
    captures = [
        p["capture"]
        for p in doc.provenance.get("pdfPageRenderCaptures", [])
        if p.get("page") == page
    ]
    require(len(captures) == 1, "image_projection_capture")
    capture = captures[0]
    require(
        plan == build_read_plan(doc, capture, deadline=deadline, cancelled=cancelled),
        "image_projection_read_input_changed",
    )
    require(
        validation
        == validate_read(plan, validation["decision"], detail_bounds=validation["detailBounds"])
        and validation["status"] == "read",
        "image_projection_read_unresolved",
    )
    require(not plan["unplannedObservationFingerprints"], "image_projection_unplanned_geometry")
    values = {v["id"]: v for v in validation["decision"]["entries"]}
    entries = {e["id"]: e for e in plan["entries"]}
    width, height = capture["pageSizeCanvasUnits"]
    tables = {k: t for k, t in doc.tables.items() if t.get("page") == page}
    require(len(tables) <= 128, "image_projection_table_budget")

    def overlap(a, b):
        return max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])

    grids, replacements = [], {}
    for grid in plan["grids"]:
        require(
            not any(overlap(grid["bounds"], other["bounds"]) for other in grids),
            "image_projection_overlapping_grids",
        )
        matches = [
            k
            for k, t in tables.items()
            if overlap(grid["bounds"], _box(t["locator"]["bbox"], width, height))
        ]
        # Overlap proposes a replacement for review, not proof of correspondence.
        require(
            len(matches) == 1 and matches[0] not in replacements,
            "image_projection_table_mapping_ambiguous",
        )
        replacements[matches[0]] = grid["id"]
        grids.append(grid)
    require(set(replacements) == set(tables), "image_projection_table_inventory")
    text_entries = {e["sourceRef"]: e for e in entries.values() if e["kind"] == "text_region"}
    require(
        len(text_entries) == sum(e["kind"] == "text_region" for e in entries.values()),
        "image_projection_text_duplicate",
    )
    require(
        not any(overlap(e["bounds"], g["bounds"]) for e in text_entries.values() for g in grids),
        "image_projection_text_grid_overlap",
    )
    old_regions = []
    for region in doc.regions:
        region_pages = {
            doc.nodes[r].get("sourceStructure", {}).get("page") for r in region["nodeIds"]
        }
        if page not in region_pages:
            continue
        require(region_pages == {page}, "image_projection_cross_page_region")
        if region.get("tableRef"):
            require(
                region["tableRef"] in replacements
                and set(region["nodeIds"])
                == {c["sourceRef"] for c in tables[region["tableRef"]]["cells"]},
                "image_projection_region_inventory",
            )
        else:
            require(
                len(region["nodeIds"]) == 1 and region["nodeIds"][0] in text_entries,
                "image_projection_region_inventory",
            )
        old_regions.append(region)
    require(
        len(old_regions) == len(tables) + len(text_entries)
        and len({r["id"] for r in doc.regions}) == len(doc.regions),
        "image_projection_region_inventory",
    )
    # Conservatively preflight every possible full/lexeme/label binding before copying.
    require(
        len(doc.nodes) + len(entries) <= 100000
        and len(doc.bindings) + sum(1 + 3 * len(v["text"]) for v in values.values()) <= 200000,
        "image_projection_observation_budget",
    )
    result = deepcopy(doc)
    identity = digest([plan["fingerprint"], validation["decisionFingerprint"]])[:24]
    prefix = f"pdf:image-read:{page}:{identity}"
    refs, bindings, new_tables = {}, {}, {}
    for entry in entries.values():
        if values[entry["id"]]["state"] == "empty":
            continue  # A proposed empty cell remains missing until the pixel review.
        ref = f"{prefix}/{entry['id']}"
        table_ref = f"{prefix}/{entry['gridId']}" if entry["kind"] == "cell" else None
        bounds = entry.get("sourceBounds")
        bbox = (
            dict(zip(("left", "top", "right", "bottom"), bounds, strict=True))
            | {"origin": "TOPLEFT"}
            if bounds
            else deepcopy(doc.nodes[entry["sourceRef"]]["sourceStructure"]["bbox"])
        )
        role = (
            "table_cell" if table_ref else doc.nodes[entry["sourceRef"]].get("semanticRole", "span")
        )
        result.node(
            ref,
            values[entry["id"]]["text"],
            role=role,
            observationBasis="pdf_image_read_candidate",
            recognizedText=True,
            locator={
                "page": page,
                "bbox": bbox,
                **({"tableRef": table_ref} if table_ref else {}),
                "sourceSha256": plan["sourceSha256"],
                "imageReadPlanFingerprint": plan["fingerprint"],
                "imageReadDecisionFingerprint": validation["decisionFingerprint"],
                "imageReadEntryId": entry["id"],
                "sourcePixelBounds": entry["bounds"],
                **(
                    {
                        "slotKey": entry["slotKey"],
                        "observationFingerprint": entry["observationFingerprint"],
                    }
                    if table_ref
                    else {"priorObservationRef": entry["sourceRef"]}
                ),
            },
        )
        refs[entry["id"]] = ref
        bindings[entry["id"]] = bind_spans(result, ref)
    proposal_grids = []
    for grid in grids:
        ref = f"{prefix}/{grid['id']}"
        require(ref not in result.tables, "image_projection_table_collision")
        cells = [
            {
                "sourceRef": refs[i],
                "row": entries[i]["row"],
                "col": entries[i]["column"],
                "rowSpan": 1,
                "colSpan": 1,
                "isHeader": False,
                "columnHeader": False,
                "rowHeader": False,
                "rowSection": False,
            }
            for i in grid["entryIds"]
            if i in refs
        ]
        # Bounds are the union of measured slots, not the older recognizer table.
        boxes = [entries[i]["sourceBounds"] for i in grid["entryIds"]]
        bounds = [
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        ]
        missing = grid["rows"] * grid["columns"] - len(cells)
        require(cells, "image_projection_empty_table_unresolved")
        result.tables[ref] = {
            "id": ref,
            "page": page,
            "basis": "pdf_image_read_grid_candidate",
            "indexBase": 0,
            "declaredRowCount": grid["rows"],
            "rowCount": grid["rows"],
            "declaredColCount": grid["columns"],
            "colCount": grid["columns"],
            "cells": cells,
            "unobservedCellCount": missing,
            "contentCompleteness": "unverified",
            "locator": {
                "page": page,
                "bbox": dict(zip(("left", "top", "right", "bottom"), bounds, strict=True))
                | {"origin": "TOPLEFT"},
            },
        }
        if missing:
            result.issue(
                "recognition_table_cells_unobserved",
                tableRef=ref,
                count=missing,
                meaning="image_read_empty_candidate_not_proven_blank",
            )
        new_tables[grid["id"]] = ref
        proposal_grids.append(
            {
                "id": grid["id"],
                "tableRef": ref,
                "bounds": grid["bounds"],
                "rows": grid["rows"],
                "columns": grid["columns"],
                "observationFingerprint": grid["observationFingerprint"],
                "priorTableRef": next(k for k, v in replacements.items() if v == grid["id"]),
            }
        )
    text_refs = {old: refs[e["id"]] for old, e in text_entries.items()}
    new_regions = []
    for region in old_regions:
        if region.get("tableRef"):
            grid_id = replacements[region["tableRef"]]
            ids = next(g["entryIds"] for g in grids if g["id"] == grid_id)
            table_ref = new_tables[grid_id]
        else:
            ids = [text_entries[region["nodeIds"][0]]["id"]]
            table_ref = None
        context = [text_refs[r] for r in region.get("contextNodeIds", []) if r in text_refs]
        new_regions.append(
            {
                "id": f"{prefix}/region/{len(new_regions)}",
                "nodeIds": [refs[i] for i in ids if i in refs],
                "bindingIds": [b for i in ids for b in bindings.get(i, [])],
                "contextNodeIds": list(dict.fromkeys(context)),
                **({"tableRef": table_ref} if table_ref else {}),
            }
        )
        if table_ref:
            result.tables[table_ref]["contextNodeIds"] = list(dict.fromkeys(context))
    old_ids = {r["id"] for r in old_regions}
    result.regions = [r for r in result.regions if r["id"] not in old_ids] + new_regions
    projection = {
        "version": VERSION,
        "page": page,
        "status": "proposed",
        "readPlanFingerprint": plan["fingerprint"],
        "readDecisionFingerprint": validation["decisionFingerprint"],
        "originalPageFingerprint": plan["sourceObservationFingerprint"],
        "originalRegions": deepcopy(old_regions),
        "originalIssues": deepcopy(doc.issues),
        "selectedNodeIds": list(refs.values()),
        "selectedTableRefs": list(new_tables.values()),
        "regionIds": [r["id"] for r in new_regions],
        "grids": proposal_grids,
        "sourceObservationsPreserved": True,
        "independentQualityApproval": False,
    }
    projection["fingerprint"] = digest(projection)
    result.provenance.setdefault("pdfImageReadProjections", []).append(projection)
    return result
