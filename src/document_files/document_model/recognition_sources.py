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
            doc.issue("recognition_source_cell_invalid", recognitionBatch=prefix)
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
    raw_ledger = import_raw_ocr_ledger(
        doc, payload, exported, structural_ids, imported_refs, prefix=prefix
    )
    doc.provenance.setdefault("recognitionSourceObservations", []).append(
        {
            "batch": prefix,
            "stage": payload.get("stage"),
            "coverage": payload.get("coverage"),
            "unassignedCandidateCount": len(primary),
            "allRawOCRDetectionsPreserved": raw_ledger["allRawOCRDetectionsPreserved"],
            "observedProcessingCoverage": raw_ledger["observedProcessingCoverage"],
            "ocrTruthVerified": False,
        }
    )
    return primary


def raw_pass_fingerprint(capture):
    """Integrity of a local-page capture; page_no is remapped by the PDF batch worker."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {
                k: v
                for k, v in capture.items()
                if k not in {"fingerprint", "page_no", "batch_page_no"}
            },
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def import_raw_ocr_ledger(doc, payload, exported, structural_ids, imported_refs, *, prefix):
    """Account returned word detections, not undetected content or OCR correctness.

    Raw captures are immutable provenance. This derived ledger never invents a blank,
    suppresses a conflict, or promotes the document's content-completeness status.
    """
    import csv
    import hashlib
    import io
    import math
    from copy import deepcopy

    from .table_ocr_repair import parse_tsv

    captures = payload.get("rawOCRPasses", [])
    expected_pages = {int(p) for p in exported.get("pages", {}) if str(p).isdigit()}
    declared = payload.get("rawCapturePages", [])
    valid = (
        payload.get("rawCaptureVersion") == "document-files.raw-ocr.v1"
        and isinstance(captures, list)
        and isinstance(declared, list)
        and bool(expected_pages)
        and len(declared) == len(expected_pages)
        and all(
            isinstance(p, dict)
            and type(p.get("page_no")) is int
            and p.get("captureAvailable") is True
            for p in declared
        )
        and {p.get("page_no") for p in declared} == expected_pages
        and not payload.get("unavailablePages")
        and not any(
            i.get("code", "").startswith("recognition_raw_") for i in payload.get("issues", [])
        )
    )
    if valid:
        valid = all(
            p.get("passFingerprints")
            == [
                c.get("fingerprint")
                for c in captures
                if isinstance(c, dict) and c.get("page_no") == p["page_no"]
            ]
            for p in declared
        )
    ledger, seen, supported = [], set(), {}

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

    def verified_transform(capture, detection):
        try:
            image = capture["image"]
            width, height = image["size"]
            if not all(type(v) is int and v > 0 for v in (width, height)):
                return False
            raw_box = detection["imageBBox"]
            left, top = raw_box["left"], raw_box["top"]
            right, bottom = left + raw_box["width"], top + raw_box["height"]
            if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
                return False
            transform = capture["transform"]
            if capture["sourcePass"] == "page_ocr":
                angle = transform["orientation"]
                if angle == 90:
                    left, top, right, bottom = top, width - right, bottom, width - left
                elif angle == 180:
                    left, top, right, bottom = (
                        width - right,
                        height - bottom,
                        width - left,
                        height - top,
                    )
                elif angle == 270:
                    left, top, right, bottom = height - bottom, left, height - top, right
                elif angle != 0:
                    return False
                scale = transform["scale"]
                crop = transform["crop"]
                if crop["coord_origin"] != "TOPLEFT" or not math.isfinite(scale) or scale <= 0:
                    return False
                expected = [
                    left / scale + crop["l"],
                    top / scale + crop["t"],
                    right / scale + crop["l"],
                    bottom / scale + crop["t"],
                ]
            else:
                x, y = transform["pixelOrigin"]
                sx, sy = transform["scale"]
                if not all(math.isfinite(v) for v in (x, y, sx, sy)) or min(sx, sy) <= 0:
                    return False
                expected = [(x + left) / sx, (y + top) / sy, (x + right) / sx, (y + bottom) / sy]
            actual = detection["pageBBox"]
            return actual.get("coord_origin") == "TOPLEFT" and all(
                math.isfinite(v) and math.isclose(v, actual[k], rel_tol=1e-9, abs_tol=1e-7)
                for k, v in zip(("l", "t", "r", "b"), expected, strict=True)
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def matches(node, text, bounds, page):
        raw = node.get("originalRecognitionText", node["text"])
        loc = node.get("sourceStructure", {})
        return (
            bool(text)
            and raw.count(text) == 1
            and loc.get("page") == page
            and overlap(bounds, loc.get("bbox")) >= 0.8
        )

    digital_support = []
    # Digital PDF text has a distinct observation channel: requiring OCR support
    # for it would falsely report preserved native text as missing. Revalidate the
    # exact existing support relation; a sourceKind label alone is not evidence.
    for relation in doc.relations:
        source_ref, target_ref = relation.get("sourceRef"), relation.get("targetRef")
        if (
            relation.get("kind") != "recognitionSourceSupport"
            or relation.get("basis") != "exact_unique_text_and_geometry"
            or source_ref not in imported_refs
            or target_ref not in structural_ids
        ):
            continue
        node, target = doc.nodes[source_ref], doc.nodes[target_ref]
        loc = node.get("sourceStructure", {})
        raw = node.get("originalRecognitionText", node["text"])
        start, end = relation.get("targetStart"), relation.get("targetEnd")
        target_raw = target.get("originalRecognitionText", target["text"])
        if (
            loc.get("sourceKind") != "pdf_text"
            or node.get("recognizedText") is not False
            or node.get("observationBasis") != "docling_pdf_text"
            or node["text"] != raw
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(target_raw)
            or target_raw[start:end] != raw
            or not matches(target, raw, loc.get("bbox"), loc.get("page"))
        ):
            continue
        supported.setdefault(target_ref, set()).update(range(start, end))
        digital_support.append(
            {
                "sourceRef": source_ref,
                "targetRef": target_ref,
                "targetStart": start,
                "targetEnd": end,
                "basis": "exact_pdf_text_observation_and_geometry",
                "ocrEvidence": False,
            }
        )

    if not isinstance(captures, list):
        captures = []
    for capture_index, capture in enumerate(captures):
        capture_valid = True
        try:
            page, pass_id = capture["page_no"], capture["passId"]
            if (
                type(page) is not int
                or page not in expected_pages
                or not isinstance(pass_id, str)
                or (page, pass_id) in seen
            ):
                raise ValueError
            seen.add((page, pass_id))
            raw = capture["tsv"].encode("utf-8")
            if (
                capture.get("status") != "complete"
                or hashlib.sha256(raw).hexdigest() != capture["tsvSha256"]
                or raw_pass_fingerprint(capture) != capture["fingerprint"]
            ):
                raise ValueError
            words = [
                (i, r)
                for i, r in enumerate(csv.DictReader(io.StringIO(capture["tsv"]), delimiter="\t"))
                if r.get("text", "").strip()
            ]
            detections = capture["detections"]
            if len(words) != len(detections) or any(
                d.get("ordinal") != i or d.get("raw") != r or d.get("text") != r["text"]
                for (i, r), d in zip(words, detections, strict=True)
            ):
                raise ValueError
            parsed = parse_tsv(raw)
            accepted = [d for d in detections if d.get("accepted") is True]
            if len(accepted) != len(parsed) or any(
                d.get("imageBBox") != {k: r[k] for k in ("left", "top", "width", "height")}
                or d.get("confidence") != r["conf"]
                or d["text"] != r["text"]
                for d, r in zip(accepted, parsed, strict=True)
            ):
                raise ValueError
            if (
                not isinstance(capture.get("image"), dict)
                or not isinstance(capture.get("transform"), dict)
                or capture.get("sourcePass") not in {"page_ocr", "table_repair"}
            ):
                raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            capture_valid, valid = False, False
            detections = capture.get("detections", []) if isinstance(capture, dict) else []
            page = capture.get("page_no") if isinstance(capture, dict) else None
        if not isinstance(detections, list):
            detections = []
        for index, detection in enumerate(detections):
            entry = {
                "rawRef": f"{prefix}:raw:{capture_index}:{index}",
                "status": "unresolved",
                "targetRefs": [],
            }
            ledger.append(entry)
            if (
                not capture_valid
                or not isinstance(detection, dict)
                or detection.get("accepted") is not True
            ):
                entry["reason"] = "raw_capture_or_token_invalid"
                continue
            loc = detection.get("pageBBox")
            if (
                not isinstance(loc, dict)
                or not all(
                    type(loc.get(k)) in (float, int) and math.isfinite(loc[k])
                    for k in ("l", "t", "r", "b")
                )
                or not detection.get("mappingBasis")
                or not verified_transform(capture, detection)
            ):
                entry["reason"] = "page_transform_unresolved"
                continue
            height = (
                exported["pages"]
                .get(str(page), exported["pages"].get(page, {}))
                .get("size", {})
                .get("height")
            )
            bounds = box(loc, height)
            text = detection["text"]
            candidates = [r for r in structural_ids if matches(doc.nodes[r], text, bounds, page)]
            if len(candidates) == 1:
                target = candidates[0]
                target_text = doc.nodes[target].get(
                    "originalRecognitionText", doc.nodes[target]["text"]
                )
                start = target_text.index(text)
                supported.setdefault(target, set()).update(range(start, start + len(text)))
                entry.update(
                    status="structural_observation",
                    targetRefs=[target],
                    targetStart=start,
                    targetEnd=start + len(text),
                )
                continue
            if len(candidates) > 1:
                entry.update(reason="ambiguous_structural_alignment", targetRefs=candidates)
                continue
            sources = [r for r in imported_refs if matches(doc.nodes[r], text, bounds, page)]
            exclusions, independent = [], []
            for ref in sources:
                node = doc.nodes[ref]
                selection = node.get("structureViewSelection") or {}
                evidence = selection.get("rulingLineEvidence") or {}
                if (
                    selection.get("selection") == "ruling_line_excluded"
                    and evidence.get("basis")
                    == "all_observed_ink_within_closed_grid_long_stroke_mask"
                    and evidence.get("nonRulingInkPixels") == 0
                    and evidence.get("originalTextPreserved") is True
                ):
                    exclusions.append(ref)
                elif node.get("semanticInput", {}).get(
                    "role"
                ) == "unassigned_observation" and not node.get("semanticInput", {}).get(
                    "candidateTableRefs"
                ):
                    independent.append(ref)
            if len(exclusions) == 1 and not independent:
                entry.update(
                    status="explicit_geometric_exclusion",
                    targetRefs=exclusions,
                    truthVerified=False,
                )
            elif len(independent) == 1 and not exclusions:
                entry.update(status="independent_observation", targetRefs=independent)
            else:
                entry.update(reason="unassigned_or_conflicting_detection", targetRefs=sources)
    unsupported = []
    for ref in structural_ids:
        node = doc.nodes[ref]
        raw = node.get("originalRecognitionText", node["text"])
        gaps = [
            i
            for i, char in enumerate(raw)
            if not char.isspace() and i not in supported.get(ref, set())
        ]
        if gaps:
            unsupported.append({"sourceRef": ref, "unaccountedCharacterCount": len(gaps)})
    local_refs = set(structural_ids) | set(imported_refs)
    tables = [
        t
        for t in doc.tables.values()
        if t["id"].startswith(f"{prefix}:")
        or any(c["sourceRef"] in structural_ids for c in t["cells"])
    ]
    unverified_tables = [
        t["id"]
        for t in tables
        if not (
            type(t.get("declaredRowCount")) is int
            and type(t.get("declaredColCount")) is int
            and 0 < t["declaredRowCount"] * t["declaredColCount"] <= 100000
        )
    ]
    unresolved = sum(e["status"] == "unresolved" for e in ledger)
    table_ids = {t["id"] for t in tables}
    # Only this import's processing dependencies belong here. Native-channel,
    # whole-page completeness and previous derived coverage issues remain on the
    # document, but cannot be premises of this local proof (or create a cycle).
    structural_issue_codes = {
        "recognition_source_cell_invalid",
        "recognition_unassigned_content",
        "recognition_table_cells_missing",
        "recognition_table_cell_invalid",
        "recognition_rich_cell_unresolved",
        "recognition_cell_page_unresolved",
        "recognition_table_dimensions_conflict",
        "recognition_table_cells_overlap",
        "recognition_table_cells_unobserved",
    }
    local_issues = [
        deepcopy(issue)
        for issue in doc.issues
        if issue.get("code") in structural_issue_codes
        and (
            issue.get("recognitionBatch") == prefix
            or issue.get("sourceRef") in local_refs
            or issue.get("tableRef") in table_ids
            or issue.get("page") in expected_pages
        )
    ]
    # Invalid exported text has no sourceRef, so inspect this export rather than
    # using an unscoped recognition_text_invalid issue from another page.
    local_issues.extend(
        {"code": "recognition_text_invalid", "exportedTextIndex": index}
        for index, item in enumerate(exported.get("texts", []))
        if not isinstance(item.get("text", ""), str)
    )
    local_issues.extend(deepcopy(payload.get("issues", [])))
    complete = (
        valid and not unresolved and not unsupported and not unverified_tables and not local_issues
    )
    result = {
        "version": "document-files.observed-processing-ledger.v3",
        "batch": prefix,
        "scope": "returned_word_detections_and_exported_structure_only",
        "allRawOCRDetectionsPreserved": valid,
        "observedProcessingCoverage": "complete" if complete else "partial",
        "ocrTruthVerified": False,
        "pageContentCompletenessVerified": False,
        "unverifiedTableExtents": unverified_tables,
        "processingDependencies": {
            "pages": sorted(expected_pages),
            "structuralRefs": list(structural_ids),
            "sourceRefs": list(imported_refs),
            "tableRefs": sorted(table_ids),
            "issues": local_issues,
        },
        "digitalTextSupport": digital_support,
        "entries": ledger,
        "unsupportedStructuralText": unsupported,
        "rawOCRPasses": deepcopy(captures),
        "rawCapturePages": deepcopy(declared),
    }
    doc.provenance.setdefault("recognitionProcessingLedgers", []).append(result)
    if not complete:
        doc.issue(
            "recognition_observed_processing_partial",
            recognitionBatch=prefix,
            unresolvedDetections=unresolved,
            unsupportedStructuralNodes=len(unsupported),
            rawInventoryVerified=valid,
        )
    return result


def page_render_fingerprint(record):
    """Digest source-bound capture metadata, excluding only its own digest."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in record.items() if k != "fingerprint"},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def import_coordinate_evidence(doc, evidence, render, payload, *, source_hash, page):
    """Recheck immutable source/page/pass links; never replace original OCR bboxes."""
    from copy import deepcopy

    from .recognition_coordinates import coordinate_links, subset_mapping

    status = "unverified"
    validation_error = None
    try:
        mapping = evidence["mapping"]
        subset = mapping["subsetRender"]
        captures = payload.get("rawOCRPasses", [])
        if (
            render["sourceSha256"] != source_hash
            or render["page_no"] != page
            or render["fingerprint"] != page_render_fingerprint(render)
            or subset["fingerprint"] != page_render_fingerprint(subset)
            or mapping != subset_mapping(render, subset)
            or mapping["status"] != "verified"
            or evidence["rawPassLinks"]
            != coordinate_links(mapping, captures, payload.get("tableRepairs", []))
            or len({c["fingerprint"] for c in captures}) != len(captures)
            or any(link["status"] != "verified" for link in evidence["rawPassLinks"])
        ):
            raise ValueError
        status = "verified"
    except (
        TypeError,
        KeyError,
        ValueError,
        AttributeError,
        OverflowError,
        ZeroDivisionError,
    ) as exc:
        # Retain the supplied evidence and pre-existing issues; no exception text
        # is copied because it can contain document-controlled values.
        validation_error = type(exc).__name__
    doc.provenance.setdefault("recognitionCoordinateEvidence", []).append(
        {
            "sourceSha256": source_hash,
            "page": page,
            "status": status,
            "evidence": deepcopy(evidence),
            **({"validationErrorType": validation_error} if validation_error else {}),
            "scope": "captured_input_pixels_to_original_pdf_coordinates_only",
            "recognitionStructureCoordinatesVerified": False,
            "contentCoverageVerified": False,
            "ocrTruthVerified": False,
        }
    )
    if status != "verified":
        doc.issue("recognition_source_coordinates_unverified", page=page)
