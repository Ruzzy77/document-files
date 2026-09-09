"""Preserve typed intermediate text independently of inferred document structure."""

from __future__ import annotations

from .docling_adapter import box
from .native import bind_spans


def import_source_observations(doc, payload, exported, structural_ids, *, prefix):
    """Return only unassigned OCR candidates; matched originals stay as evidence.

    Geometry plus exact unique substring supports a structural text candidate,
    not the truth of OCR or the completeness of the recognized page.
    """
    if not isinstance(payload, dict):
        doc.issue("recognition_source_observations_unavailable")
        return []
    if payload.get("version") != "document-files.recognition-source-observations.v1":
        doc.issue("recognition_source_observations_invalid")
        return []
    if payload.get("unavailablePages"):
        doc.issue("recognition_source_observations_partial", pages=payload["unavailablePages"])
    for issue in payload.get("issues", []):
        if isinstance(issue, dict) and isinstance(issue.get("code"), str):
            doc.issue(issue["code"], recognitionBatch=prefix)
    pages = exported.get("pages", {})
    by_page = {}
    for ref in structural_ids:
        loc = doc.nodes[ref].get("sourceStructure", {})
        by_page.setdefault(loc.get("page"), []).append(ref)
    table_refs = {c["sourceRef"]: t for t in doc.tables.values() for c in t["cells"]}
    binding_index = {}
    for binding in doc.bindings.values():
        binding_index.setdefault(binding["sourceRef"], []).append(binding)
    primary = []
    imported_refs = []

    def overlap(a, b):
        if not a or not b or a.get("origin") != "TOPLEFT" or b.get("origin") != "TOPLEFT":
            return 0.0
        area = (a["right"] - a["left"]) * (a["bottom"] - a["top"])
        if area <= 0:
            return 0.0
        return (
            max(0, min(a["right"], b["right"]) - max(a["left"], b["left"]))
            * max(0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
            / area
        )

    for index, cell in enumerate(payload.get("cells", [])):
        if (
            not isinstance(cell, dict)
            or not isinstance(cell.get("text"), str)
            or not isinstance(cell.get("raw"), str)
            or type(cell.get("fromOcr")) is not bool
            or cell.get("sourceKind") != ("ocr" if cell["fromOcr"] else "pdf_text")
            or type(cell.get("page_no")) is not int
        ):
            doc.issue("recognition_source_cell_invalid")
            continue
        page = cell["page_no"]
        height = pages.get(str(page), pages.get(page, {})).get("size", {}).get("height")
        bounds = box(cell.get("bbox"), height)
        ref = doc.node(
            f"{prefix}:source:{index}",
            cell["text"],
            role="recognition_source_cell",
            locator={
                "page": page,
                "bbox": bounds,
                "sourceKind": cell["sourceKind"],
                "recognitionBatch": prefix,
                "backendCellIndex": cell.get("backendCellIndex"),
                "rectangle": cell.get("rectangle"),
            },
            observationBasis="ocr" if cell["fromOcr"] else "docling_pdf_text",
            recognizedText=cell["fromOcr"],
            originalRecognitionText=cell["raw"],
            recognitionConfidence=cell.get("confidence"),
            sourceObservationStage=cell.get("stage", payload.get("stage")),
            preprocessingRef=(
                f"{prefix}:repair:{cell['repairIndex']}" if "repairIndex" in cell else None
            ),
            originalCellIndices=cell.get("originalCellIndices", []),
            cellUnitIndex=cell.get("cellUnitIndex"),
            structureViewSelection=cell.get("structureView"),
        )
        doc.nodes[ref]["semantic"] = {
            "value": {"kind": "text", "raw": cell["raw"], "basis": "typed_source_cell_orig"}
        }
        own_bindings = [
            doc.bind(ref, path="/semantic/value/raw", candidateRole="recognized_original")
        ]
        own_bindings.extend(bind_spans(doc, ref))
        imported_refs.append(ref)
        binding_index[ref] = [doc.bindings[b] for b in own_bindings]
        selection = cell.get("structureView")
        selection = selection if isinstance(selection, dict) else {}
        evidence = selection.get("rulingLineEvidence")
        if (
            selection.get("selection") == "ruling_line_excluded"
            and isinstance(evidence, dict)
            and evidence.get("basis") == "all_observed_ink_within_closed_grid_long_stroke_mask"
            and evidence.get("nonRulingInkPixels") == 0
            and evidence.get("originalTextPreserved") is True
        ):
            # Keep the OCR token and competing geometric reading. This changes
            # candidate selection only; it does not claim the OCR lexeme is false.
            doc.nodes[ref]["semanticInput"] = {
                "role": "context_only",
                "reason": "ruling_line_candidate_with_preserved_ocr_text",
            }
            for binding_id in own_bindings:
                doc.bindings[binding_id]["candidateStatus"] = "unresolved_conflict"
            continue
        if cell.get("candidateStatus") == "unresolved_conflict":
            for binding_id in own_bindings:
                doc.bindings[binding_id]["candidateStatus"] = "unresolved_conflict"
        adjacent = [
            r
            for r in by_page.get(page, [])
            if overlap(bounds, doc.nodes[r]["sourceStructure"].get("bbox")) >= 0.8
        ]
        matching = []
        raw = cell["raw"]
        for target in adjacent:
            node = doc.nodes[target]
            text = node.get("originalRecognitionText", node["text"])
            if raw and cell["text"] == raw and text.count(raw) == 1:
                start = text.index(raw)
                matching.append((target, start, start + len(raw)))
        # Structural table cells are preferred only when they carry the exact text.
        cell_matches = [m for m in matching if m[0] in table_refs]
        if cell_matches:
            matching = cell_matches
        if len(matching) == 1:
            target, start, end = matching[0]
            doc.nodes[ref]["semanticInput"] = {
                "role": "source_overlap_not_independent",
                "representativeRefs": [target],
            }
            doc.relations.append(
                {
                    "kind": "recognitionSourceSupport",
                    "sourceRef": ref,
                    "targetRef": target,
                    "targetPath": "/originalRecognitionText"
                    if "originalRecognitionText" in doc.nodes[target]
                    else "/text",
                    "targetStart": start,
                    "targetEnd": end,
                    "basis": "exact_unique_text_and_geometry",
                    "truthVerified": False,
                }
            )
            continue
        if not cell["fromOcr"]:
            # The independent native PDF parser already owns digital text input.
            # Keep this backend observation without falsely calling it OCR.
            doc.nodes[ref]["semanticInput"] = {
                "role": "context_only",
                "reason": "digital_text_backend_observation",
            }
            continue
        intersecting_tables = [
            t
            for t in doc.tables.values()
            if t.get("page") == page and overlap(bounds, t.get("locator", {}).get("bbox")) >= 0.8
        ]
        table_ids = [t["id"] for t in intersecting_tables]
        doc.nodes[ref]["semanticInput"] = {
            "role": "unassigned_observation",
            "candidateTableRefs": table_ids,
        }
        primary.append(ref)
        if table_ids or adjacent:
            doc.issue(
                "recognition_unassigned_content",
                sourceRef=ref,
                candidateTableRefs=table_ids,
                reason="observed_text_not_uniquely_assigned_to_structure",
            )
        if adjacent:
            doc.nodes[ref]["semanticInput"]["role"] = "unresolved_conflict"
            for binding_id in own_bindings:
                doc.bindings[binding_id]["candidateStatus"] = "unresolved_conflict"
            for target in adjacent:
                for binding in binding_index.get(target, []):
                    binding["candidateStatus"] = "unresolved_conflict"
                doc.relations.append(
                    {
                        "kind": "observationConflict",
                        "sourceRef": ref,
                        "targetRef": target,
                        "resolution": "unresolved",
                        "basis": "source_cell_alignment_not_exact",
                    }
                )
        for table in intersecting_tables:
            table.setdefault("contextNodeIds", []).append(ref)
    if payload.get("tableRepairs"):
        doc.provenance.setdefault("tableOCRRepairs", []).extend(
            {"id": f"{prefix}:repair:{index}", **repair}
            for index, repair in enumerate(payload["tableRepairs"])
        )
    # Explicit baseline/derived conflict links survive downstream text clustering.
    sources = {
        doc.nodes[r]["sourceStructure"].get("backendCellIndex"): r
        for r in imported_refs
        if doc.nodes[r].get("sourceObservationStage") == "original_ocr"
    }
    support_targets = {}
    for relation in doc.relations:
        if relation.get("kind") == "recognitionSourceSupport":
            support_targets.setdefault(relation["sourceRef"], []).append(relation["targetRef"])
    for ref in imported_refs:
        node = doc.nodes[ref]
        if not any(b.get("candidateStatus") == "unresolved_conflict" for b in binding_index[ref]):
            continue
        for original_index in node.get("originalCellIndices", []):
            original_ref = sources.get(original_index)
            if original_ref is None:
                continue
            for affected in {ref, original_ref, *support_targets.get(original_ref, [])}:
                for binding in binding_index.get(affected, []):
                    binding["candidateStatus"] = "unresolved_conflict"
            doc.relations.append(
                {
                    "kind": "observationConflict",
                    "sourceRef": ref,
                    "targetRef": original_ref,
                    "basis": "original_vs_line_removed_ocr",
                    "resolution": "unresolved",
                }
            )
    doc.provenance.setdefault("recognitionSourceObservations", []).append(
        {
            "batch": prefix,
            "stage": payload.get("stage"),
            "coverage": payload.get("coverage"),
            "unassignedCandidateCount": len(primary),
            "allRawOCRDetectionsPreserved": False,
        }
    )
    return primary
