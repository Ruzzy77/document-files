"""Preserve typed intermediate text independently of inferred document structure."""

from __future__ import annotations

from .docling_adapter import box
from .native import bind_spans

ORDERED_SOURCE_VERSION = "document-files.ordered-ocr-source.v2"


def _validated_raw_words(capture, payload=None):
    """Check the saved TSV and detections together, without interpreting text."""
    import csv
    import hashlib
    import io

    from .table_ocr_repair import parse_tsv

    if (
        capture.get("status") != "complete"
        or raw_pass_fingerprint(capture) != capture["fingerprint"]
    ):
        raise ValueError
    if "runFingerprint" in capture:
        from .recognition_batches import validated_batch_capture

        if payload is None:
            raise ValueError("OCR batch provenance unavailable")
        selected = validated_batch_capture(
            capture, payload.get("rawOCRRuns", []), payload.get("rawOCRPasses", [])
        )
        words = [(i, row) for i, row in selected if row.get("text", "").strip()]
        # Re-encode only a derived parser view. Original TSV/ordinals remain in the run.
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(selected[0][1]), delimiter="\t")
        writer.writeheader()
        writer.writerows(row for _, row in selected)
        raw = stream.getvalue().encode("utf-8")
    else:
        raw = capture["tsv"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != capture["tsvSha256"]:
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
    return detections


def _ordered_source_alignments(doc, payload, structural_ids, *, prefix):
    """Only unique, whole-target, anchored runs in one verified horizontal OCR line.

    No table cells, reordered/overlapping words, normalized tokens, partial tokens,
    cross-line joins or guesses about page reading order are accepted.
    """
    import math
    from collections import Counter

    from .recognition_coordinates import coordinate_links, subset_mapping

    result = {
        "version": ORDERED_SOURCE_VERSION,
        "batch": prefix,
        "scope": "single_horizontal_raw_ocr_line_exact_target_sequence_only",
        "maxComparisons": 65536,
        "comparisonBudgetUnit": "target_characters_and_token_comparisons",
        "maxAlignmentPaths": 1024,
        "comparisons": 0,
        "truncated": False,
        "alignments": [],
        "ambiguousIncompleteLineTargets": [],
        "ocrTruthVerified": False,
        "readingOrderVerified": False,
    }
    captures, cells = payload.get("rawOCRPasses", []), payload.get("cells", [])
    if (
        not isinstance(captures, list)
        or not isinstance(cells, list)
        or (len(captures) > 256 or len(cells) > 8192 or len(structural_ids) > 2048)
    ):
        result["truncated"] = True
        return result
    table_nodes = {c["sourceRef"] for t in doc.tables.values() for c in t["cells"]}
    # Exclude non-cell text geometrically inside a table as well: line order
    # cannot choose among columns or make a merged-cell decision.
    table_boxes = [(t.get("page"), t.get("locator", {}).get("bbox")) for t in doc.tables.values()]
    source_sha = doc.provenance.get("sourceSha256")
    renders = {
        r["capture"].get("fingerprint"): r["capture"]
        for r in doc.provenance.get("pdfPageRenderCaptures", [])
        if isinstance(r.get("capture"), dict)
    }

    def contained(a, b):
        return (
            b
            and all(math.isfinite(v) for v in (*a, *b))
            and (b[0] <= a[0] < a[2] <= b[2] and b[1] <= a[1] < a[3] <= b[3])
        )

    def coords(b):
        if not b or b.get("origin") != "TOPLEFT":
            raise ValueError
        values = [b[k] for k in ("left", "top", "right", "bottom")]
        if not all(type(v) in (float, int) and math.isfinite(v) for v in values):
            raise ValueError
        return values

    def overlaps_table(bounds, page):
        for table_page, raw_bounds in table_boxes:
            if table_page != page:
                continue
            if not raw_bounds:
                return True  # Cannot establish that this target is outside the table.
            table = coords(raw_bounds)
            if min(bounds[2], table[2]) > max(bounds[0], table[0]) and min(
                bounds[3], table[3]
            ) > max(bounds[1], table[1]):
                return True
        return False

    proposals = {}
    uncertain_lines = {}
    path_count = 0

    def spend(cost=1):
        if result["comparisons"] + cost > result["maxComparisons"]:
            result["truncated"] = True
            raise ValueError("ordered source comparison budget exceeded")
        result["comparisons"] += cost

    try:
        if sum(len(c.get("tsv", "")) for c in captures) > 4_000_000:
            result["truncated"] = True
            return result
        cell_index = {}
        for ci, cell in enumerate(cells):
            if (
                cell.get("fromOcr") is not True
                or cell.get("sourceKind") != "ocr"
                or cell.get("text") != cell.get("raw")
                or cell.get("candidateStatus") == "unresolved_conflict"
                or (cell.get("structureView") or {}).get("selection") == "ruling_line_excluded"
            ):
                continue
            b = box(cell.get("bbox"), None)  # only an explicit TOPLEFT source is supported
            if b is None:
                continue
            key = (cell.get("page_no"), cell.get("raw"), tuple(coords(b)))
            cell_index.setdefault(key, []).append(ci)
        links = {}
        page_heights = {}
        for imported in doc.provenance.get("recognitionCoordinateEvidence", []):
            if imported.get("status") != "verified" or imported.get("sourceSha256") != source_sha:
                continue
            evidence = imported["evidence"]
            mapping = evidence["mapping"]
            render = renders.get(mapping["originalRenderFingerprint"])
            if (
                not render
                or render["sourceSha256"] != source_sha
                or (
                    render["fingerprint"] != page_render_fingerprint(render)
                    or imported.get("page") != render["page_no"]
                    or render["intrinsicRotation"] != 0
                    or mapping != subset_mapping(render, mapping["subsetRender"])
                    or mapping["status"] != "verified"
                    or mapping["subsetRender"]["fingerprint"]
                    != page_render_fingerprint(mapping["subsetRender"])
                )
            ):
                continue
            w, h = render["pageSizeCanvasUnits"]
            if list(render["pageBoxes"]["effective"]) != [0, 0, w, h] or list(
                render["pageBoxes"]["mediaDeclared"]
            ) != [0, 0, w, h]:
                continue
            computed = coordinate_links(
                mapping, captures, payload.get("tableRepairs", []), payload.get("rawOCRRuns", [])
            )
            if computed != evidence["rawPassLinks"] or len(
                {c["fingerprint"] for c in captures}
            ) != len(captures):
                continue
            for link in computed:
                if link["status"] == "verified":
                    links[link["passFingerprint"]] = link
                    page_heights[link["passFingerprint"]] = h
        for capture_index, capture in enumerate(captures):
            link = links.get(capture.get("fingerprint"))
            if (
                not link
                or capture.get("sourcePass") != "page_ocr"
                or capture["transform"].get("orientation") != 0
            ):
                continue
            detections = _validated_raw_words(capture, payload)
            groups = {}
            for di, detection in enumerate(detections):
                raw = detection["raw"]
                line = tuple(int(raw[k]) for k in ("page_num", "block_num", "par_num", "line_num"))
                if any(v <= 0 for v in line) or line[0] != 1 or raw.get("level") != "5":
                    raise ValueError("raw line identity unavailable")
                groups.setdefault(line, []).append((di, detection))
            for line, group in groups.items():
                if len(group) > 128:
                    result["truncated"] = True
                    raise ValueError("ordered source line token budget exceeded")
                if len(group) < 3:
                    continue
                tokens = []
                invalid = False
                last_right, last_word = -math.inf, 0
                top, bottom = -math.inf, math.inf
                for di, detection in group:
                    text = detection["text"]
                    b = detection["imageBBox"]
                    image_box = [
                        b["left"],
                        b["top"],
                        b["left"] + b["width"],
                        b["top"] + b["height"],
                    ]
                    word = int(detection["raw"]["word_num"])
                    if (
                        detection.get("accepted") is not True
                        or not text
                        or any(c.isspace() for c in text)
                        or word != last_word + 1
                        or image_box[0] < last_right
                        or not contained(image_box, link["sourceSupportedInputPixelBounds"])
                    ):
                        invalid = True
                        break
                    a, b_, c, d, e, f = link["inputPixelToOriginalPageAffine"]
                    # The initial version deliberately supports only the actual
                    # unrotated horizontal transform and its full source bounds.
                    if a <= 0 or d >= 0 or b_ != 0 or c != 0:
                        invalid = True
                        break
                    height = page_heights[capture["fingerprint"]]
                    mapped = [
                        a * image_box[0] + e,
                        height - (d * image_box[1] + f),
                        a * image_box[2] + e,
                        height - (d * image_box[3] + f),
                    ]
                    reported = detection["pageBBox"]
                    pb = [reported[k] for k in ("l", "t", "r", "b")]
                    if reported.get("coord_origin") != "TOPLEFT" or not all(
                        math.isclose(x, y, abs_tol=1e-7, rel_tol=1e-9)
                        for x, y in zip(mapped, pb, strict=True)
                    ):
                        invalid = True
                        break
                    indices = cell_index.get((capture["page_no"], text, tuple(pb)), [])
                    if len(indices) != 1:
                        invalid = True
                        break
                    tokens.append((di, indices[0], text, pb))
                    last_right, last_word = image_box[2], word
                    top, bottom = max(top, image_box[1]), min(bottom, image_box[3])
                if invalid or top >= bottom:
                    # A failed typed-source/geometry match cannot erase a second
                    # raw occurrence and make the remaining occurrence unique.
                    uncertain_lines.setdefault(capture_index, []).append(
                        [detection["text"] for _, detection in group]
                    )
                    continue
                for target in structural_ids:
                    if target in table_nodes:
                        continue
                    node = doc.nodes[target]
                    loc = node.get("sourceStructure", {})
                    target_text = node.get("originalRecognitionText", node["text"])
                    spend(len(target_text))
                    if (
                        loc.get("page") != capture["page_no"]
                        or not target_text
                        or target_text != node["text"]
                        or target_text.strip() != target_text
                        or len(target_text) > 8192
                    ):
                        continue
                    target_box = coords(loc.get("bbox"))
                    if overlaps_table(target_box, capture["page_no"]):
                        continue
                    for start_index in range(len(tokens)):
                        spend()
                        position, selected = 0, []
                        for di, ci, text, pb in tokens[start_index:]:
                            spend(len(text))
                            if (
                                not contained(pb, target_box)
                                or overlaps_table(pb, capture["page_no"])
                                or not target_text.startswith(text, position)
                            ):
                                break
                            end = position + len(text)
                            if end < len(target_text) and not target_text[end].isspace():
                                break  # No token splitting or merging against target words.
                            selected.append(
                                {
                                    "rawRef": f"{prefix}:raw:{capture_index}:{di}",
                                    "sourceCellIndex": ci,
                                    "targetRef": target,
                                    "targetStart": position,
                                    "targetEnd": end,
                                }
                            )
                            position = end
                            while position < len(target_text) and target_text[position].isspace():
                                spend()
                                position += 1
                            if position == len(target_text):
                                words = [
                                    target_text[x["targetStart"] : x["targetEnd"]] for x in selected
                                ]
                                if (
                                    len(words) >= 3
                                    and target_text.count(words[0]) == 1
                                    and target_text.count(words[-1]) == 1
                                    and any(target_text.count(t) > 1 for t in words)
                                ):
                                    witness = {
                                        "targetRef": target,
                                        "targetText": target_text,
                                        "targetGeometry": loc,
                                        "rawPassFingerprint": capture["fingerprint"],
                                        "coordinateLink": link,
                                        "line": list(line),
                                        "sequence": selected,
                                    }
                                    fp = page_render_fingerprint(witness)
                                    path_count += 1
                                    if path_count > result["maxAlignmentPaths"]:
                                        result["truncated"] = True
                                        raise ValueError("ordered source path budget exceeded")
                                    proposals.setdefault(target, []).append(
                                        (selected, fp, words, capture_index)
                                    )
                                break
        candidates = []
        for paths in proposals.values():
            if len(paths) != 1:
                continue
            selected, fp, words, capture_index = paths[0]
            ambiguous = False
            for raw_words in uncertain_lines.get(capture_index, []):
                spend(len(raw_words) * len(words))
                if any(
                    raw_words[i : i + len(words)] == words
                    for i in range(len(raw_words) - len(words) + 1)
                ):
                    ambiguous = True
                    break
            if ambiguous:
                result["ambiguousIncompleteLineTargets"].append(selected[0]["targetRef"])
                continue
            for entry, text in zip(selected, words, strict=True):
                target_text = doc.nodes[entry["targetRef"]].get(
                    "originalRecognitionText", doc.nodes[entry["targetRef"]]["text"]
                )
                if target_text.count(text) > 1:
                    candidates.append({**entry, "witnessFingerprint": fp})
        raw_counts = Counter(e["rawRef"] for e in candidates)
        cell_counts = Counter(e["sourceCellIndex"] for e in candidates)
        for entry in candidates:
            if raw_counts[entry["rawRef"]] == cell_counts[entry["sourceCellIndex"]] == 1:
                result["alignments"].append(entry)
    except (
        KeyError,
        TypeError,
        ValueError,
        IndexError,
        AttributeError,
        OverflowError,
        ZeroDivisionError,
    ):
        result["alignments"] = []
    return result


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
    ordered = _ordered_source_alignments(doc, payload, structural_ids, prefix=prefix)
    ordered["fingerprint"] = page_render_fingerprint(ordered)
    doc.provenance.setdefault("recognitionOrderedSourceAlignments", []).append(ordered)
    ordered_cells = {e["sourceCellIndex"]: e for e in ordered["alignments"]}
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
        order_match = ordered_cells.get(index)
        if order_match and order_match["targetRef"] in adjacent:
            matching.append(
                (order_match["targetRef"], order_match["targetStart"], order_match["targetEnd"])
            )
        # Structural table cells are preferred only when they carry the exact text.
        cell_matches = [m for m in matching if m[0] in table_refs]
        if cell_matches:
            matching = cell_matches
        if len(matching) == 1:
            target, start, end = matching[0]
            if order_match and (target, start, end) != (
                order_match["targetRef"],
                order_match["targetStart"],
                order_match["targetEnd"],
            ):
                order_match = None
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
                    "basis": "exact_unique_ocr_line_order_v1"
                    if order_match
                    else "exact_unique_text_and_geometry",
                    **(
                        {
                            "orderedAlignmentFingerprint": ordered["fingerprint"],
                            "rawRef": order_match["rawRef"],
                        }
                        if order_match
                        else {}
                    ),
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
        doc, payload, exported, structural_ids, imported_refs, prefix=prefix, ordered=ordered
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


def import_raw_ocr_ledger(
    doc, payload, exported, structural_ids, imported_refs, *, prefix, ordered=None
):
    """Account returned word detections, not undetected content or OCR correctness.

    Raw captures are immutable provenance. This derived ledger never invents a blank,
    suppresses a conflict, or promotes the document's content-completeness status.
    """
    import math
    from copy import deepcopy

    ordered_raw = {e["rawRef"]: e for e in (ordered or {}).get("alignments", [])}
    captures = payload.get("rawOCRPasses", [])
    expected_pages = {int(p) for p in exported.get("pages", {}) if str(p).isdigit()}
    declared = payload.get("rawCapturePages", [])
    valid = (
        payload.get("rawCaptureVersion")
        in {"document-files.raw-ocr.v1", "document-files.raw-ocr.v2"}
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
    if valid and payload.get("rawCaptureVersion") == "document-files.raw-ocr.v2":
        runs = payload.get("rawOCRRuns", [])
        valid = (
            isinstance(runs, list)
            and len({r.get("fingerprint") for r in runs}) == len(runs)
            and all(
                any(c.get("runFingerprint") == r.get("fingerprint") for c in captures) for r in runs
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
            detections = _validated_raw_words(capture, payload)
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
            ordered_match = ordered_raw.get(entry["rawRef"])
            if ordered_match:
                target = ordered_match["targetRef"]
                source_ref = f"{prefix}:source:{ordered_match['sourceCellIndex']}"
                # Only a support actually accepted by this import can affect raw
                # processing. Never consume an externally supplied result flag.
                if any(
                    r.get("kind") == "recognitionSourceSupport"
                    and r.get("basis") == "exact_unique_ocr_line_order_v1"
                    and r.get("sourceRef") == source_ref
                    and r.get("targetRef") == target
                    and r.get("rawRef") == entry["rawRef"]
                    and r.get("orderedAlignmentFingerprint") == ordered.get("fingerprint")
                    for r in doc.relations
                ):
                    candidates.append(target)
                else:
                    ordered_match = None
            if len(candidates) == 1:
                target = candidates[0]
                target_text = doc.nodes[target].get(
                    "originalRecognitionText", doc.nodes[target]["text"]
                )
                start = ordered_match["targetStart"] if ordered_match else target_text.index(text)
                if target_text[start : start + len(text)] != text:
                    entry["reason"] = "ordered_target_changed"
                    continue
                if ordered_match:
                    entry["orderedAlignmentFingerprint"] = ordered["fingerprint"]
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
        "version": "document-files.observed-processing-ledger.v4",
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
        "rawOCRRuns": deepcopy(payload.get("rawOCRRuns", [])),
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
            != coordinate_links(
                mapping, captures, payload.get("tableRepairs", []), payload.get("rawOCRRuns", [])
            )
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


def _cell_observation_table_candidates(doc, record, *, page, prefix):
    """Find unique layout correspondence, not a correct or blank cell value."""
    import math

    frame = record["sourceFrame"]
    fw, fh = frame["pageSize"]
    cw, ch = record["canvas"]["size"]
    sx, sy = fw / cw, fh / ch

    def normalized(bounds):
        left, top, right, bottom = bounds
        return [left * sx, top * sy, right * sx, bottom * sy]

    def node_box(value):
        if not isinstance(value, dict) or value.get("origin") != "TOPLEFT":
            return None
        values = [value.get(k) for k in ("left", "top", "right", "bottom")]
        if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
            return None
        return values if values[0] < values[2] and values[1] < values[3] else None

    slots = {(s["row"], s["col"]): s for s in record["slots"]}
    if (
        not slots
        or len(slots) != len(record["slots"])
        or any(s.get("measurementStatus") != "measured" for s in record["slots"])
    ):
        return []
    rows = max(r for r, _ in slots) + 1
    cols = max(c for _, c in slots) + 1
    if len(slots) != rows * cols:
        return []
    crop = normalized(record["tableCrop"]["pixelBounds"])
    candidates = []
    for table in doc.tables.values():
        if (
            not table["id"].startswith(prefix + ":")
            or table.get("page") != page
            or type(table.get("declaredRowCount")) is not int
            or type(table.get("declaredColCount")) is not int
            or table["declaredRowCount"] != rows
            or table["declaredColCount"] != cols
            or not table.get("cells")
        ):
            continue
        bounds = node_box(table.get("locator", {}).get("bbox"))
        # Only raster rounding tolerance, not a broad overlap heuristic.
        if bounds is None or any(
            abs(a - b) > tolerance
            for a, b, tolerance in zip(bounds, crop, [sx, sy, sx, sy], strict=True)
        ):
            continue
        occupied = set()
        consistent = True
        for cell in table["cells"]:
            row, col = cell["row"], cell["col"]
            # Rectangular grid observation cannot validate a merged layout.
            if cell.get("rowSpan") != 1 or cell.get("colSpan") != 1:
                consistent = False
                break
            if (row, col) not in slots or (row, col) in occupied:
                consistent = False
                break
            occupied.add((row, col))
            node = doc.nodes.get(cell["sourceRef"], {})
            loc = node.get("sourceStructure", {})
            value_box = node_box(loc.get("bbox"))
            slot_box = normalized(slots[row, col]["fullPixelBox"])
            if (
                loc.get("page") != page
                or value_box is None
                or value_box[0] < slot_box[0] - sx
                or value_box[1] < slot_box[1] - sy
                or value_box[2] > slot_box[2] + sx
                or value_box[3] > slot_box[3] + sy
            ):
                consistent = False
                break
        if consistent:
            candidates.append(table["id"])
    return candidates


def import_cell_pixel_observations(
    doc,
    records,
    render,
    coordinate_evidence,
    *,
    source_hash,
    page,
    prefix,
    ocr_links=None,
    source_payload=None,
):
    """Preserve pixel measurements separately from structure/value completeness.

    No OCR capture is required: a native or no-ink slot can have an independent
    source frame. Producer statistics are checked, never recomputed without RGB.
    """
    from collections import Counter
    from copy import deepcopy

    if records is None and ocr_links is None:
        return
    if records is None:
        records = []

    from .recognition_cell_observations import validate_cell_observation
    from .recognition_coordinates import subset_mapping

    output = {
        "version": "document-files.cell-observation-import.v1",
        "sourceSha256": source_hash,
        "page": page,
        "batch": prefix,
        "scope": "captured_slot_pixels_and_unique_layout_correspondence_only",
        "observations": [],
        "ocrLinkEvidence": {
            "rawLinks": deepcopy(ocr_links),
            "verificationStatus": "unverified",
            "status": "not_provided" if ocr_links is None else "preserved_unverified",
            "checks": [],
            "ocrTruthVerified": False,
            "contentCoverageVerified": False,
        },
        "rawPixelsRecomputed": False,
        "blankValueProven": False,
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
    }
    doc.provenance.setdefault("recognitionCellPixelObservations", []).append(output)
    if not isinstance(records, list) or len(records) > 4096:
        output.update(status="unverified", reason="invalid_or_excessive_observation_list")
        return
    mapping = None
    try:
        proposed = coordinate_evidence["mapping"]
        subset = proposed["subsetRender"]
        if (
            render["sourceSha256"] != source_hash
            or render["page_no"] != page
            or render["fingerprint"] != page_render_fingerprint(render)
            or subset["fingerprint"] != page_render_fingerprint(subset)
            or proposed != subset_mapping(render, subset)
            or proposed["status"] != "verified"
            or not any(
                r.get("page") == page
                and r.get("sourceSha256") == source_hash
                and r.get("bindingStatus") == "source_page_matched"
                and r.get("capture") == render
                for r in doc.provenance.get("pdfPageRenderCaptures", [])
            )
        ):
            raise ValueError
        mapping = proposed
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        pass
    fingerprints = Counter(
        r.get("fingerprint")
        for r in records
        if isinstance(r, dict) and isinstance(r.get("fingerprint"), str)
    )
    for record in records:
        entry = {
            "observation": deepcopy(record),
            "observationStatus": "unverified",
            "pixelMeasurementStatus": record.get("status") if isinstance(record, dict) else None,
            "sourceCoordinateStatus": "unverified",
            "structureAssociation": {"status": "unlinked", "candidateTableRefs": [], "slots": []},
        }
        output["observations"].append(entry)
        if mapping is None:
            entry["reason"] = "source_page_mapping_unverified"
            continue
        try:
            validation = validate_cell_observation(
                record, mapping=mapping, source_sha256=source_hash, page=page
            )
            entry["validation"] = deepcopy(validation)
            if validation.get("status") != "verified":
                entry["reason"] = "cell_observation_unverified"
                continue
            entry.update(observationStatus="verified", sourceCoordinateStatus="verified")
            if fingerprints[record["fingerprint"]] != 1:
                entry["reason"] = "duplicate_observation"
                continue
            candidates = _cell_observation_table_candidates(doc, record, page=page, prefix=prefix)
            entry["structureAssociation"]["candidateTableRefs"] = candidates
            if len(candidates) == 1:
                entry["structureAssociation"].update(
                    status="unique_geometry_correspondence",
                    basis="same_page_crop_grid_dimensions_and_existing_cell_geometry",
                    tableRef=candidates[0],
                    slots=[
                        {"slotKey": s["slotKey"], "row": s["row"], "col": s["col"]}
                        for s in record["slots"]
                    ],
                    valueObserved=False,
                    blankValueProven=False,
                )
            else:
                entry["reason"] = "ambiguous_or_unmatched_table_geometry"
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError, ZeroDivisionError):
            entry["reason"] = "cell_observation_validation_failed"
    # Different measurements of the same slot are preserved, not arbitrarily selected.
    associated = Counter(
        e["structureAssociation"].get("tableRef")
        for e in output["observations"]
        if e["structureAssociation"]["status"] == "unique_geometry_correspondence"
    )
    for entry in output["observations"]:
        association = entry["structureAssociation"]
        if associated[association.get("tableRef")] > 1:
            entry["structureAssociation"] = {
                "status": "unlinked",
                "candidateTableRefs": association["candidateTableRefs"],
                "slots": [],
            }
            entry["reason"] = "multiple_observations_for_table"
    output["ocrLinkEvidence"] = _cell_ocr_link_diagnostics(
        ocr_links, records, source_payload, output["observations"], mapping=mapping, page=page
    )
    output["status"] = "observational_metadata_only"


def _cell_ocr_link_diagnostics(links, records, payload, observations, *, mapping, page):
    """Preserve execution-link claims, never promote them to OCR approval.

    Reference equality is useful for diagnosis but does not prove raw TSV,
    capture integrity, coordinate alignment or complete batch membership.
    """
    from copy import deepcopy

    from .recognition_cell_observations import cell_ocr_links

    result = {
        "rawLinks": deepcopy(links),
        "verificationStatus": "unverified",
        "status": "not_provided" if links is None else "preserved_unverified",
        "checks": [],
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
        "executionSuccessInferred": False,
    }
    if links is None:
        return result
    if not isinstance(links, list) or len(links) > 4096 or not isinstance(payload, dict):
        result["reason"] = "invalid_or_excessive_link_payload"
        return result
    captures, repairs = payload.get("rawOCRPasses", []), payload.get("tableRepairs", [])
    try:
        recomputed = cell_ocr_links(records, captures)
        result["producerLinksEqual"] = links == recomputed
    except (TypeError, KeyError, ValueError, AttributeError):
        recomputed = []
        result["producerLinksEqual"] = False
    for link in links:
        check = {"verificationStatus": "unverified", "referenceConsistency": "unmatched"}
        result["checks"].append(check)
        try:
            matched_observations = [
                e
                for e in observations
                if e["observationStatus"] == "verified"
                and e["observation"]["fingerprint"] == link["observationFingerprint"]
            ]
            matched_captures = [c for c in captures if c.get("passId") == link["capturePassId"]]
            if mapping is None or len(matched_observations) != 1 or len(matched_captures) != 1:
                raise ValueError
            observation = matched_observations[0]["observation"]
            slots = [s for s in observation["slots"] if s["slotKey"] == link["slotKey"]]
            if len(slots) != 1 or slots[0].get("measurementStatus") != "measured":
                raise ValueError
            slot, capture = slots[0], matched_captures[0]
            unit_ref = {"unitIndex": link["unitIndex"], "unitFingerprint": link["unitFingerprint"]}
            index = capture["transform"]["repairIndex"]
            if type(index) is not int or not 0 <= index < len(repairs):
                raise ValueError
            repair = repairs[index]
            unit_index = link["unitIndex"]
            if type(unit_index) is not int or not 0 <= unit_index < len(repair["units"]):
                raise ValueError
            unit = repair["units"][unit_index]
            if (
                not result["producerLinksEqual"]
                or link not in recomputed
                or slot.get("ocrUnit") != unit_ref
                or capture.get("status") != "complete"
                or capture.get("sourcePass") != "table_repair"
                or capture.get("page_no") != page
                or repair.get("page_no") != page
                or unit.get("status") != "ready"
                or unit.get("fingerprint") != link["unitFingerprint"]
                or unit.get("row") != slot["row"]
                or unit.get("col") != slot["col"]
                or link.get("runFingerprint") != capture.get("runFingerprint")
                or link.get("tsvInputPageNumber") != capture.get("tsvInputPageNumber")
            ):
                raise ValueError
            check.update(
                referenceConsistency="matched",
                reason="references_only_not_raw_capture_coordinate_or_batch_verification",
            )
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            check["reason"] = "missing_conflicting_or_failed_execution_reference"
    return result


def import_native_ruling_observations(doc, payload, *, source_hash, page, prefix):
    """Append structure evidence only; never edit nodes, bindings or issues."""
    from copy import deepcopy

    from .pdf_native_objects import validate_native_inventory
    from .recognition_coordinates import coordinate_links, fingerprint
    from .recognition_ruling_pixels import (
        MAX_COMPARISONS,
        MAX_PIXELS,
        MAX_WINDOWS,
        VERSION,
        prove_native_ruling,
        validate_ruling_windows,
    )

    payload = payload or {}
    captures = payload.get("rawOCRPasses", [])
    if not any("rulingPixelObservation" in c for c in captures):
        return
    result = {
        "version": VERSION,
        "sourceSha256": source_hash,
        "page": page,
        "batch": prefix,
        "status": "observed",
        "passes": [],
        "comparisons": 0,
        "validatedPixels": 0,
        "validatedWindows": 0,
        "originalObservationsPreserved": True,
        "issuesUnchanged": True,
        "ocrTruthVerified": False,
        "documentCompletenessVerified": False,
    }
    try:
        if len(captures) > 256:
            raise ValueError("raw_pass_budget_exceeded")
        native = validate_native_inventory(
            doc.provenance.get("pdfNativeObjects", {}), source_sha256=source_hash, page=page
        )
        coords = [
            c
            for c in doc.provenance.get("recognitionCoordinateEvidence", [])
            if c.get("sourceSha256") == source_hash
            and c.get("page") == page
            and c.get("status") == "verified"
        ]
        if len(coords) != 1:
            raise ValueError("source_coordinate_evidence_unverified")
        mapping = coords[0]["evidence"]["mapping"]
        if mapping.get("sourceSha256") != source_hash or mapping.get("originalPageNumber") != page:
            raise ValueError("source_mapping_identity_mismatch")
        links = coordinate_links(
            mapping, captures, payload.get("tableRepairs", []), payload.get("rawOCRRuns", [])
        )
        for ci, (capture, link) in enumerate(zip(captures, links, strict=True)):
            record = capture.get("rulingPixelObservation")
            if record is None:
                continue
            entry = {
                "passFingerprint": capture.get("fingerprint"),
                "observation": deepcopy(record),
                "status": "unverified",
                "decisions": [],
            }
            result["passes"].append(entry)
            try:
                _validated_raw_words(capture, payload)
                measured = validate_ruling_windows(
                    record,
                    capture,
                    max_pixels=0
                    if result.get("validationBudgetConsumedOnFailure")
                    else MAX_PIXELS - result["validatedPixels"],
                    max_windows=0
                    if result.get("validationBudgetConsumedOnFailure")
                    else MAX_WINDOWS - result["validatedWindows"],
                )
                pixels_count = sum(len(pixels) // 3 for _, pixels in measured)
                if (
                    pixels_count > MAX_PIXELS - result["validatedPixels"]
                    or len(measured) > MAX_WINDOWS - result["validatedWindows"]
                ):
                    raise ValueError("page_pixel_support_budget_exceeded")
                result["validatedPixels"] += pixels_count
                result["validatedWindows"] += len(measured)
                if link["status"] != "verified":
                    raise ValueError("input_frame_mapping_unverified")
                if native["status"] != "verified":
                    raise ValueError("native_source_inventory_unverified")
                entry["status"] = "verified_pixels_and_coordinates"
                entry["mappingFingerprint"] = mapping["fingerprint"]
                for window, pixels in measured:
                    decision = prove_native_ruling(
                        window,
                        pixels,
                        link,
                        native["pageInventory"],
                        comparison_budget=MAX_COMPARISONS - result["comparisons"],
                    )
                    result["comparisons"] += decision["comparisons"]
                    di = next(
                        i
                        for i, d in enumerate(capture["detections"])
                        if d["ordinal"] == window["ordinal"]
                    )
                    decision["rawRef"] = f"{prefix}:raw:{ci}:{di}"
                    decision["fingerprint"] = fingerprint(decision)
                    entry["decisions"].append(decision)
            except (
                ValueError,
                KeyError,
                TypeError,
                IndexError,
                OverflowError,
                AttributeError,
            ) as error:
                entry.update(reason=str(error), errorType=type(error).__name__)
                result["validationBudgetConsumedOnFailure"] = True
                result["validationRemainingWorkUnknown"] = True
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, AttributeError) as error:
        result.update(status="unverified", reason=str(error), errorType=type(error).__name__)
        result["unvalidatedObservations"] = [
            deepcopy(c.get("rulingPixelObservation"))
            for c in captures
            if "rulingPixelObservation" in c
        ]
    result["fingerprint"] = fingerprint(result)
    doc.provenance.setdefault("recognitionNativeRulingObservations", []).append(result)
