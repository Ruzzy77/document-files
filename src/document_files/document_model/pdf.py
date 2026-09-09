"""Native PDF geometry and explicit optional recognition, kept as separate observations."""

from __future__ import annotations

import hashlib
import io
import math
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version

from .docling_adapter import RecognitionCheckpointError, import_docling
from .model import ObservationDocument
from .native import bind_spans
from .recognition_sources import import_source_observations


def _version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "unavailable"


def _native_pdf(doc: ObservationDocument, content: bytes) -> list[str]:
    try:
        import pdfplumber
    except ImportError:
        doc.issue("pdf_native_geometry_runtime_unavailable")
        return []
    ids = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            if page_number > 500:
                doc.issue("pdf_page_budget_exceeded")
                break
            page_ref = doc.node(
                f"pdf:page:{page_number}",
                "",
                role="page",
                locator={
                    "page": page_number,
                    "width": page.width,
                    "height": page.height,
                    "rotation": page.rotation,
                    "bbox": list(page.bbox),
                    "coordinateOrigin": "TOPLEFT",
                },
                observationBasis="native_pdf",
            )
            ids.append(page_ref)
            chars = page.chars
            if len(chars) > 200000:
                doc.issue("pdf_character_budget_exceeded", page=page_number)
                chars = chars[:200000]
            # Individual character bounds retain text/geometry relationships, including rotation.
            # Group only source-order contiguous characters sharing the same baseline/upright flag.
            groups = []
            for character in chars:
                if not isinstance(character.get("text"), str):
                    continue
                key = (round(character.get("top", 0), 1), bool(character.get("upright", True)))
                if not groups or groups[-1][0] != key:
                    groups.append((key, []))
                groups[-1][1].append(character)
            for line_number, (_, group) in enumerate(groups, 1):
                text = "".join(c["text"] for c in group)
                bbox = {
                    "left": min(c["x0"] for c in group),
                    "top": min(c["top"] for c in group),
                    "right": max(c["x1"] for c in group),
                    "bottom": max(c["bottom"] for c in group),
                    "origin": "TOPLEFT",
                }
                if not all(math.isfinite(float(v)) for k, v in bbox.items() if k != "origin"):
                    doc.issue("pdf_invalid_geometry", page=page_number)
                    continue
                node_id = doc.node(
                    f"{page_ref}/line/{line_number}",
                    text,
                    role="line",
                    locator={
                        "page": page_number,
                        "parentRef": page_ref,
                        "bbox": bbox,
                        "rotation": page.rotation,
                        "characters": [
                            {
                                k: c.get(k)
                                for k in (
                                    "text",
                                    "x0",
                                    "x1",
                                    "top",
                                    "bottom",
                                    "upright",
                                    "matrix",
                                )
                            }
                            for c in group
                        ],
                    },
                    observationBasis="native_pdf",
                    sourceKind="native_text",
                )
                bind_spans(doc, node_id)
                ids.append(node_id)
                doc.relations.append(
                    {"kind": "contains", "sourceRef": page_ref, "targetRef": node_id}
                )
            if not chars:
                doc.issue("pdf_page_has_no_native_text", page=page_number)
            if page.images:
                doc.nodes[page_ref]["sourceStructure"]["images"] = [
                    {k: image.get(k) for k in ("x0", "x1", "top", "bottom", "width", "height")}
                    for image in page.images
                ]
    doc.provenance["pdfNative"] = {
        "engine": "pdfplumber",
        "version": _version("pdfplumber"),
        "textOrdering": "source_order_baseline_groups",
        "ocr": False,
    }
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(content)
        try:
            doc.provenance["pdfium"] = {
                "version": _version("pypdfium2"),
                "pageCount": len(document),
                "rendered": False,
            }
            for index in range(min(len(document), 500)):
                page = document[index]
                try:
                    ref = f"pdf:page:{index + 1}"
                    if ref in doc.nodes:
                        doc.nodes[ref]["sourceStructure"]["pdfium"] = {
                            "width": page.get_width(),
                            "height": page.get_height(),
                            "rotation": page.get_rotation(),
                        }
                finally:
                    page.close()
        finally:
            document.close()
    except ImportError:
        doc.issue("pdfium_runtime_unavailable")
    return ids


def _conflicts(doc: ObservationDocument, native_ids: list[str], recognized_ids: list[str]) -> dict:
    """Compare covered native characters, not the entire line containing a cell.

    A glyph is covered at >=80% box area, outside at <=20%. Intermediate
    intersections, non-upright glyphs and degenerate boxes remain ambiguous.
    Text is compared exactly; whitespace, punctuation and digits are not erased.
    """
    policy = {
        "includedAreaRatio": 0.8,
        "excludedAreaRatio": 0.2,
        "comparison": "exact_characters",
        "coordinateOrigin": "TOPLEFT",
    }
    doc.provenance["pdfConflictComparison"] = policy
    comparisons = {}
    native_by_page = {}
    for candidate in native_ids:
        locator = doc.nodes[candidate].get("sourceStructure", {})
        if locator.get("characters"):
            native_by_page.setdefault(locator.get("page"), []).append(candidate)
    for node_id in recognized_ids:
        observed = doc.nodes[node_id]
        loc = observed.get("sourceStructure", {})
        bounds = loc.get("bbox")
        if not isinstance(bounds, dict) or bounds.get("origin") != "TOPLEFT":
            if native_by_page.get(loc.get("page")):
                doc.issue("native_recognition_alignment_unresolved", recognizedRef=node_id)
            comparisons[node_id] = {
                "status": "unresolved_alignment"
                if native_by_page.get(loc.get("page"))
                else "unaligned",
                "sourceSegments": [],
            }
            continue
        selected, ambiguous, native = [], [], []
        for candidate in native_by_page.get(loc.get("page"), []):
            original = doc.nodes[candidate]
            original_loc = original.get("sourceStructure", {})
            line_box = original_loc.get("bbox")
            if isinstance(line_box, dict) and (
                line_box.get("right", math.inf) <= bounds["left"]
                or line_box.get("left", -math.inf) >= bounds["right"]
                or line_box.get("bottom", math.inf) <= bounds["top"]
                or line_box.get("top", -math.inf) >= bounds["bottom"]
            ):
                continue
            offset = 0
            for ordinal, char in enumerate(original_loc.get("characters", [])):
                text = char.get("text", "")
                if not isinstance(text, str):
                    continue
                segment = {
                    "sourceRef": candidate,
                    "start": offset,
                    "end": offset + len(text),
                    "characterIndex": ordinal,
                }
                offset += len(text)
                try:
                    x0, x1, top, bottom = (float(char[k]) for k in ("x0", "x1", "top", "bottom"))
                    if not all(math.isfinite(v) for v in (x0, x1, top, bottom)):
                        raise ValueError
                    width = max(0.0, min(x1, bounds["right"]) - max(x0, bounds["left"]))
                    height = max(0.0, min(bottom, bounds["bottom"]) - max(top, bounds["top"]))
                    if width == 0 or height == 0:
                        continue
                    area = (x1 - x0) * (bottom - top)
                    ratio = width * height / area if area > 0 else 0.5
                except (ValueError, TypeError, KeyError):
                    ambiguous.append(segment)
                    continue
                if ratio <= 0.2:
                    continue
                if ratio < 0.8 or char.get("upright") is False:
                    ambiguous.append({**segment, "intersectionRatio": ratio})
                    continue
                selected.append({**segment, "intersectionRatio": ratio})
                native.append(text)
        if ambiguous:
            comparisons[node_id] = {"status": "ambiguous", "sourceSegments": selected + ambiguous}
            doc.issue(
                "native_recognition_geometry_ambiguous",
                recognizedRef=node_id,
                sourceSegments=ambiguous,
            )
            doc.relations.append(
                {
                    "kind": "observationOverlap",
                    "targetRef": node_id,
                    "sourceSegments": selected + ambiguous,
                    "resolution": "geometry_ambiguous",
                }
            )
        elif selected and (
            "".join(native) != observed.get("text", "")
            or (
                isinstance(observed.get("originalRecognitionText"), str)
                and "".join(native) != observed["originalRecognitionText"]
            )
        ):
            comparisons[node_id] = {"status": "conflict", "sourceSegments": selected}
            refs = list(dict.fromkeys(s["sourceRef"] for s in selected))
            doc.issue("native_recognition_text_conflict", sourceRefs=refs, recognizedRef=node_id)
            doc.relations.append(
                {
                    "kind": "observationConflict",
                    "sourceRefs": refs,
                    "sourceSegments": selected,
                    "targetRef": node_id,
                    "comparison": "exact_characters",
                    "resolution": "unresolved",
                }
            )

        elif selected:
            comparisons[node_id] = {"status": "equivalent", "sourceSegments": selected}
            doc.relations.append(
                {
                    "kind": "observationEquivalence",
                    "targetRef": node_id,
                    "sourceRefs": list(dict.fromkeys(s["sourceRef"] for s in selected)),
                    "sourceSegments": selected,
                    "comparison": "exact_characters",
                    "basis": "native_character_geometry",
                    "resolution": "equivalent",
                }
            )
        else:
            comparisons[node_id] = {"status": "unaligned", "sourceSegments": []}
    return comparisons


def _semantic_candidates(doc, native_ids, recognized_ids, comparisons):
    """Select an inference view, never delete observations or decide source conflicts."""
    table_cells = {cell["sourceRef"] for table in doc.tables.values() for cell in table["cells"]}
    # Structural cells win presentation priority only after an exact native comparison.
    ordered = sorted(recognized_ids, key=lambda ref: ref not in table_cells)
    claimed, covered, selected, suppressed, context = {}, {}, [], [], {}

    def annotate(ref, role, **details):
        doc.nodes[ref]["semanticInput"] = {"role": role, **details}

    def mark_conflict(ref):
        for binding in doc.bindings.values():
            if binding["sourceRef"] == ref:
                binding["candidateStatus"] = "unresolved_conflict"

    def spans(segments):
        grouped = {}
        for segment in segments:
            grouped.setdefault(segment["sourceRef"], []).append((segment["start"], segment["end"]))
        result = []
        for ref, ranges in grouped.items():
            merged = []
            for start, end in sorted(set(ranges)):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            result.extend((ref, start, end) for start, end in merged)
        return result

    def native_span(ref, start, end, node_id, role):
        source = doc.nodes[ref]
        loc = deepcopy(source.get("sourceStructure", {}))
        characters, offset = [], 0
        for char in loc.get("characters", []):
            length = len(char.get("text", ""))
            if start <= offset and offset + length <= end:
                characters.append(char)
            offset += length
        loc["characters"] = characters
        if characters:
            loc["bbox"] = {
                "left": min(c["x0"] for c in characters),
                "right": max(c["x1"] for c in characters),
                "top": min(c["top"] for c in characters),
                "bottom": max(c["bottom"] for c in characters),
                "origin": "TOPLEFT",
            }
        segment = {
            "sourceRef": ref,
            "sourceStart": start,
            "sourceEnd": end,
            "start": 0,
            "end": end - start,
        }
        created = doc.node(
            node_id,
            source.get("text", "")[start:end],
            role="line",
            locator=loc,
            observationBasis="native_pdf",
            parentRef=ref,
            sourceSegments=[segment],
            normalization="exact_native_substring",
        )
        annotate(created, role)
        bind_spans(doc, created)
        doc.relations.append(
            {
                "kind": "nativeSpan",
                "sourceRef": created,
                "targetRef": ref,
                "sourceSegments": [segment],
                "basis": "exact_native_substring",
            }
        )
        return created

    for ref in ordered:
        comparison = comparisons.get(ref, {"status": "unaligned", "sourceSegments": []})
        status, segments = comparison["status"], comparison["sourceSegments"]
        anchors = {(s["sourceRef"], s["start"], s["end"]) for s in segments}
        overlapping = list(dict.fromkeys(claimed[a] for a in anchors if a in claimed))
        if status == "equivalent" and overlapping and ref not in table_cells:
            suppressed.append(ref)
            annotate(ref, "source_overlap_not_independent", representativeRefs=overlapping)
            doc.relations.append(
                {
                    "kind": "semanticInputOverlap",
                    "sourceRef": ref,
                    "targetRef": overlapping[0],
                    "representativeRefs": overlapping,
                    "basis": "exact_native_anchor_intersection",
                }
            )
            continue
        selected.append(ref)
        if overlapping and (ref in table_cells or status == "conflict"):
            doc.issue("recognition_structure_overlap_unresolved", sourceRefs=[ref, *overlapping])
            mark_conflict(ref)
            for other in overlapping:
                mark_conflict(other)
                annotate(other, "unresolved_conflict", reason="recognition_structure_overlap")
                doc.relations.append(
                    {
                        "kind": "observationConflict",
                        "sourceRef": other,
                        "targetRef": ref,
                        "resolution": "unresolved",
                        "basis": "overlapping_native_character_anchors",
                    }
                )
        if status not in {"equivalent", "conflict"}:
            if status in {"ambiguous", "unresolved_alignment"}:
                mark_conflict(ref)
            annotate(
                ref, "alignment_unresolved" if status == "ambiguous" else "independent_observation"
            )
            continue
        annotate(
            ref,
            "unresolved_conflict" if status == "conflict" else "representative",
            sourceSegments=segments,
            basis="native_character_geometry",
        )
        if status == "conflict":
            mark_conflict(ref)
            alternatives = []
            for ordinal, (native_ref, start, end) in enumerate(spans(segments)):
                alternative = native_span(
                    native_ref,
                    start,
                    end,
                    f"{ref}/native-alternative/{ordinal}",
                    "conflict_alternative",
                )
                mark_conflict(alternative)
                alternatives.append(alternative)
                doc.relations.append(
                    {
                        "kind": "observationConflict",
                        "sourceRef": alternative,
                        "targetRef": ref,
                        "resolution": "unresolved",
                        "comparison": "exact_characters",
                    }
                )
            context[ref] = alternatives
        for anchor in anchors:
            claimed[anchor] = ref
        for native_ref, start, end in spans(segments):
            covered.setdefault(native_ref, []).append((start, end))

    primary_native = []
    for ref in native_ids:
        intervals = spans(
            [{"sourceRef": ref, "start": start, "end": end} for start, end in covered.get(ref, [])]
        )
        if not intervals:
            primary_native.append(ref)
            continue
        suppressed.append(ref)
        representatives = list(
            dict.fromkeys(owner for anchor, owner in claimed.items() if anchor[0] == ref)
        )
        annotate(ref, "source_evidence_not_independent", representativeRefs=representatives)
        cursor = 0
        for _, start, end in [
            *intervals,
            (ref, len(doc.nodes[ref].get("text", "")), len(doc.nodes[ref].get("text", ""))),
        ]:
            if cursor < start:
                remainder = native_span(
                    ref, cursor, start, f"{ref}/remainder/{cursor}:{start}", "native_remainder"
                )
                if doc.nodes[remainder]["text"].strip():
                    primary_native.append(remainder)
            cursor = max(cursor, end)
    doc.provenance["pdfSemanticProjection"] = {
        "policy": "exact_native_geometry_equivalence_only",
        "primaryNodeIds": primary_native + selected,
        "suppressedNodeIds": suppressed,
        "contextByNode": context,
        "sourceObservationsPreserved": True,
        "conflictSelection": "unresolved",
    }
    return primary_native + selected


def observe_pdf(doc: ObservationDocument, content: bytes, *, recognition=None) -> list[str]:
    try:
        native_ids = _native_pdf(doc, content)
    except Exception:
        doc.issue("pdf_native_observation_failed")
        native_ids = []
    if recognition is None:
        doc.issue("pdf_layout_recognition_unavailable")
        return native_ids
    try:
        result = recognition.observe(content)
        if not isinstance(result, dict):
            raise ValueError
        for issue in result.get("issues", []):
            if isinstance(issue, dict) and isinstance(issue.get("code"), str):
                doc.issue(issue["code"])
        recognized_ids = []
        page_results = result.get("pageResults", [])
        source_hash = hashlib.sha256(content).hexdigest()
        for page_result in page_results:
            page = page_result.get("page")
            if (
                type(page) is not int
                or page < 1
                or page_result.get("sourceSha256") != source_hash
                or not isinstance(page_result.get("document"), dict)
            ):
                doc.issue("recognition_page_checkpoint_invalid")
                continue
            prefix = f"docling:page:{page}"
            page_ids = import_docling(doc, page_result["document"], prefix=prefix)
            source_ids = import_source_observations(
                doc,
                page_result.get("sourceObservations"),
                page_result["document"],
                page_ids,
                prefix=prefix,
            )
            recognized_ids.extend([*page_ids, *source_ids])
        if page_results:
            doc.provenance["recognitionPages"] = {
                "completedPages": result.get("completedPages", []),
                "processedPages": result.get("processedPages", []),
                "pageStatuses": {str(p["page"]): p.get("status", "partial") for p in page_results},
                "pageCount": result.get("pageCount"),
                "batchSize": 1,
                "sourceSha256": source_hash,
            }
        exported = result.get("document")
        if isinstance(exported, dict):
            page_ids = import_docling(doc, exported)
            source_ids = import_source_observations(
                doc,
                result.get("sourceObservations"),
                exported,
                page_ids,
                prefix="docling",
            )
            recognized_ids.extend([*page_ids, *source_ids])
        comparisons = _conflicts(doc, native_ids, recognized_ids)
        primary_ids = _semantic_candidates(doc, native_ids, recognized_ids, comparisons)
        doc.provenance["recognition"] = result.get("provenance", {})
        doc.coverage["recognition"] = result.get("status", "partial")
        doc.coverage["recognitionConversion"] = result.get("status", "partial")
        doc.coverage["recognitionContentCompleteness"] = "unverified"
        doc.issue("recognition_content_completeness_unverified")
        if result.get("status") != "complete":
            doc.issue("pdf_layout_recognition_partial")
        return primary_ids
    except RecognitionCheckpointError:
        raise
    except Exception:
        doc.issue("pdf_layout_recognition_failed")
        return native_ids
