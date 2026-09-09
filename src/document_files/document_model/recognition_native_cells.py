"""A narrow native-PDF blank-cell proof, separate from OCR and page completeness."""

from __future__ import annotations

import math
from collections import Counter
from copy import deepcopy

from .recognition_coordinates import fingerprint

VERSION = "document-files.native-cell-decision.v2"
MAX_COMPARISONS = 262144


def _rectangle(value):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 4
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
        or value[0] >= value[2]
        or value[1] >= value[3]
    ):
        raise ValueError("invalid rectangle")
    return list(value)


def _touches(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def _content_bounds(obj):
    def bounds(value):
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or value[0] > value[2]
            or value[1] > value[3]
        ):
            raise ValueError("object bounds unavailable")
        return value

    original = bounds(obj.get("bounds"))
    if obj.get("kind") != "text":
        return original
    if obj.get("paintBoundsStatus") != "verified":
        raise ValueError("text paint bounds unverified")
    painted = bounds(obj.get("paintSupportBounds"))
    if not (
        painted[0] <= original[0] <= original[2] <= painted[2]
        and painted[1] <= original[1] <= original[3] <= painted[3]
    ):
        raise ValueError("text logical bounds not preserved")
    return painted


def _source_boxes(record, slot, validation, native_page):
    width, height = native_page["pageSize"]
    affine = validation["canvasPixelToOriginalPageAffine"]
    a, b, c, d, e, f = affine
    # The first implementation supports unrotated, zero-origin source pages only.
    # Do not substitute normalized layout coordinates for a proved source transform.
    if (
        native_page["rotation"] != 0
        or native_page["coordinateOrigin"] != "TOPLEFT"
        or b != 0
        or c != 0
        or a <= 0
        or d >= 0
        or e != 0
        or f != height
        or record["sourceFrame"]["pageSize"] != [width, height]
    ):
        raise ValueError("unsupported original page transform")

    def convert(box):
        x, y, u, v = box
        return _rectangle([a * x, height - (d * y + f), a * u, height - (d * v + f)])

    return convert(slot["fullPixelBox"]), convert(slot["interiorPixelBox"])


def _line_edge(obj, outer, inner):
    """Match one native painted line to one excluded raster border band."""
    if (
        obj.get("kind") != "primitive_line"
        or obj.get("stroke") is not True
        or obj.get("fill") is not False
        or obj.get("dash") not in ([], [[], 0])
        or len(obj.get("segments", [])) != 1
    ):
        return None
    width = obj.get("strokeWidth")
    if type(width) not in (int, float) or not math.isfinite(width) or width <= 0:
        return None
    segment = obj["segments"][0]
    if len(segment) != 4 or any(
        type(v) not in (int, float) or not math.isfinite(v) for v in segment
    ):
        return None
    x, y, u, v = segment
    half = width / 2
    if x == u and min(y, v) <= inner[1] and max(y, v) >= inner[3]:
        if outer[0] <= x - half and x + half <= inner[0]:
            return "left", x, min(y, v), max(y, v)
        if inner[2] <= x - half and x + half <= outer[2]:
            return "right", x, min(y, v), max(y, v)
    if y == v and min(x, u) <= inner[0] and max(x, u) >= inner[2]:
        if outer[1] <= y - half and y + half <= inner[1]:
            return "top", y, min(x, u), max(x, u)
        if inner[3] <= y - half and y + half <= outer[3]:
            return "bottom", y, min(x, u), max(x, u)
    return None


def prove_native_blank(record, slot, validation, native_page, *, comparison_budget):
    """Return a per-cell decision; no layout, pixel or source input is changed."""
    decision = {"status": "unresolved", "blankValueProven": False, "comparisons": 0}
    if (
        slot.get("measurementStatus") != "measured"
        or slot.get("geometryStatus") != "resolved"
        or slot.get("regions", {}).get("interior", {}).get("status") != "observed_background"
    ):
        return {**decision, "reason": "interior_not_fully_observed_white"}
    region = slot["regions"]["interior"]
    if (
        region.get("opaqueWhitePixels") != region.get("pixelCount")
        or not region.get("pixelCount")
        or any(region.get(k) != 0 for k in ("unknownAlphaPixels", "nonWhiteRGBPixels"))
        or any(band.get("unknownAlphaPixels") != 0 for band in slot["regions"]["boundaryBands"])
    ):
        return {**decision, "reason": "interior_or_boundary_pixels_unverified"}
    try:
        outer, inner = _source_boxes(record, slot, validation, native_page)
        objects = native_page["objects"]
        if len(objects) > comparison_budget:
            return {**decision, "reason": "native_object_comparison_budget_exceeded"}
        edges, conflicts = {}, []
        for obj in objects:
            decision["comparisons"] += 1
            bounds = _content_bounds(obj)
            if not _touches(bounds, outer):
                continue
            edge = _line_edge(obj, outer, inner)
            if edge is None:
                conflicts.append(obj["id"])
            else:
                edges.setdefault(edge[0], []).append((obj["id"], edge[1:]))
        if conflicts:
            return {
                **decision,
                "reason": "source_content_touches_cell",
                "conflictObjectRefs": conflicts,
            }
        if set(edges) != {"left", "right", "top", "bottom"} or any(
            len(items) != 1 for items in edges.values()
        ):
            return {**decision, "reason": "native_borders_not_unique"}
        coordinates = {name: items[0][1][0] for name, items in edges.items()}
        for name, items in edges.items():
            _, (_, start, end) = items[0]
            lo, hi = ("top", "bottom") if name in {"left", "right"} else ("left", "right")
            if not start <= coordinates[lo] < coordinates[hi] <= end:
                return {**decision, "reason": "native_borders_not_closed"}
        return {
            **decision,
            "status": "proven_blank",
            "blankValueProven": True,
            "basis": "complete_native_objects_four_unique_closed_borders_and_white_interior",
            "sourceCellBounds": outer,
            "sourceInteriorBounds": inner,
            "borderObjectRefs": {name: items[0][0] for name, items in edges.items()},
            "meaning": "source_cell_empty_not_absent_zero_or_not_applicable",
        }
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**decision, "reason": "source_cell_geometry_unverified"}


def import_native_blank_cells(doc, *, source_hash, page, prefix):
    """Add only previously missing cells proved from this process's native inventory."""
    from .pdf_native_objects import validate_native_inventory
    from .recognition_cell_observations import validate_cell_observation
    from .recognition_sources import _cell_observation_table_candidates

    inventory = doc.provenance.get("pdfNativeObjects")
    validation = validate_native_inventory(inventory, source_sha256=source_hash, page=page)
    if validation.get("status") != "verified":
        return []
    native_page = validation["pageInventory"]
    completeness = native_page.get("completeness", {})
    if not all(
        completeness.get(key) is True
        for key in ("eligibleForNativeCellReasoning", "textPaintBoundsVerified")
    ):
        return []
    batches = [
        batch
        for batch in doc.provenance.get("recognitionCellPixelObservations", [])
        if batch.get("batch") == prefix
        and batch.get("page") == page
        and batch.get("sourceSha256") == source_hash
    ]
    if len(batches) != 1:
        return []
    mappings = [
        item["evidence"]["mapping"]
        for item in doc.provenance.get("recognitionCoordinateEvidence", [])
        if item.get("status") == "verified"
        and item.get("sourceSha256") == source_hash
        and item.get("page") == page
    ]
    if len(mappings) != 1:
        return []
    entries = batches[0]["observations"]
    counts = Counter(e["structureAssociation"].get("tableRef") for e in entries)
    output = {
        "version": VERSION,
        "sourceSha256": source_hash,
        "page": page,
        "batch": prefix,
        "nativeInventoryFingerprint": inventory["fingerprint"],
        "scope": "previously_missing_native_table_cells_only",
        "decisions": [],
        "resolvedIssues": [],
        "comparisons": 0,
        "maxComparisons": MAX_COMPARISONS,
        "ocrTruthVerified": False,
        "pageContentCompletenessVerified": False,
    }
    created = []
    for entry in entries:
        association, record = entry["structureAssociation"], entry["observation"]
        table_ref = association.get("tableRef")
        if (
            entry.get("observationStatus") != "verified"
            or entry.get("sourceCoordinateStatus") != "verified"
            or association.get("status") != "unique_geometry_correspondence"
            or counts[table_ref] != 1
            or record.get("fingerprint") != fingerprint(record)
            or validate_cell_observation(
                record, mapping=mappings[0], source_sha256=source_hash, page=page
            )
            != entry.get("validation")
            or _cell_observation_table_candidates(doc, record, page=page, prefix=prefix)
            != [table_ref]
        ):
            continue
        table = doc.tables[table_ref]
        occupied = {(c["row"], c["col"]) for c in table["cells"]}
        missing = [s for s in record["slots"] if (s["row"], s["col"]) not in occupied]
        if table.get("unobservedCellCount") != len(missing):
            continue
        pending = []
        for slot in missing:
            result = prove_native_blank(
                record,
                slot,
                entry["validation"],
                native_page,
                comparison_budget=MAX_COMPARISONS - output["comparisons"],
            )
            output["comparisons"] += result["comparisons"]
            result.update(
                tableRef=table_ref,
                row=slot["row"],
                col=slot["col"],
                slotKey=slot["slotKey"],
                observationFingerprint=record["fingerprint"],
            )
            output["decisions"].append(result)
            if result["blankValueProven"]:
                node_id = f"{table_ref}/native-blank/{slot['row']}:{slot['col']}"
                if node_id in doc.nodes:
                    result.update(
                        status="unresolved", blankValueProven=False, reason="node_id_collision"
                    )
                else:
                    pending.append((node_id, slot, result))
        if len(doc.nodes) + len(pending) > 100000 or len(doc.bindings) + len(pending) > 200000:
            for _, _, decision in pending:
                decision.update(
                    status="unresolved",
                    blankValueProven=False,
                    reason="observation_budget_exceeded",
                )
            continue
        for node_id, slot, result in pending:
            result["sourceRef"] = node_id
            result["fingerprint"] = fingerprint(result)
            x, y, u, v = result["sourceCellBounds"]
            doc.node(
                node_id,
                "",
                role="table_cell",
                observationBasis="native_pdf_cell_and_pixels",
                recognizedText=False,
                locator={
                    "page": page,
                    "tableRef": table_ref,
                    "sourceSha256": source_hash,
                    "bbox": {"left": x, "top": y, "right": u, "bottom": v, "origin": "TOPLEFT"},
                    "nativeCellDecisionFingerprint": result["fingerprint"],
                    "nativeInventoryFingerprint": inventory["fingerprint"],
                },
            )
            doc.bind(node_id, start=0, end=0, candidateRole="content", blank=True)
            table["cells"].append(
                {
                    "sourceRef": node_id,
                    "row": slot["row"],
                    "col": slot["col"],
                    "rowSpan": 1,
                    "colSpan": 1,
                    "isHeader": False,
                    "columnHeader": False,
                    "rowHeader": False,
                    "rowSection": False,
                }
            )
            created.append(node_id)
        if pending:
            remaining = len(missing) - len(pending)
            table["unobservedCellCount"] = remaining
            table["nativeBlankCellCount"] = len(pending)
            for issue in list(doc.issues):
                if (
                    issue.get("code") == "recognition_table_cells_unobserved"
                    and issue.get("tableRef") == table_ref
                    and issue.get("count") == len(missing)
                ):
                    output["resolvedIssues"].append(
                        {
                            "originalIssue": deepcopy(issue),
                            "remainingUnobservedCellCount": remaining,
                            "sourceRefs": [n for n, _, _ in pending],
                        }
                    )
                    doc.issues.remove(issue)
                    if remaining:
                        doc.issue(
                            issue["code"],
                            tableRef=table_ref,
                            count=remaining,
                            meaning="not_observed_not_proven_blank",
                        )
    if output["decisions"]:
        output["fingerprint"] = fingerprint(output)
        doc.provenance.setdefault("recognitionNativeCellDecisions", []).append(output)
    return created
