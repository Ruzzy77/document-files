"""Atomic application of owned page reviews, not independent OCR quality approval."""

from __future__ import annotations

from copy import deepcopy

from ..document_model.model import ObservationDocument
from ..document_model.recognition_cell_observations import fingerprint
from ..document_model.recognition_sources import page_render_fingerprint
from .pdf_visual_plan import (
    PdfVisualReviewError,
    _mapped_box,
    digest,
    observation_page_fingerprint,
    require,
    validate_decision,
)

VERSION = "document-files.pdf-visual-apply.v1"
MAX_ORDER_COMPARISONS = 1048576
_REPLACEMENT_CODES = {
    "pdf_page_has_no_native_text",
    "pdf_page_without_text",
    "reading_order_unverified",
    "recognition_content_completeness_unverified",
}


def _record_issue(output, issue, evidence):
    output["resolvedIssues"].append(
        {"originalIssue": deepcopy(issue), "issueFingerprint": digest(issue), **deepcopy(evidence)}
    )


def _validated(doc, reviews):
    count = doc.provenance.get("pdfium", {}).get("pageCount")
    require(type(count) is int and 0 < count <= 500, "visual_apply_page_inventory")
    require(isinstance(reviews, list) and len(reviews) == count, "visual_apply_page_inventory")
    source = doc.provenance.get("sourceSha256")
    require(isinstance(source, str) and len(source) == 64, "visual_apply_source")
    selected = {}
    for review in reviews:
        plan, validation = review["plan"], review["validation"]
        page = plan["page"]
        require(
            type(page) is int and 1 <= page <= count and page not in selected,
            "visual_apply_page_inventory",
        )
        require(plan["sourceSha256"] == source, "visual_apply_source")
        require(
            plan["sourceObservationFingerprint"] == observation_page_fingerprint(doc, page),
            "visual_apply_observation_changed",
        )
        captures = [
            entry
            for entry in doc.provenance.get("pdfPageRenderCaptures", [])
            if entry.get("page") == page
        ]
        require(len(captures) == 1, "visual_apply_capture")
        entry = captures[0]
        capture = entry["capture"]
        require(
            entry.get("bindingStatus") == "source_page_matched"
            and capture.get("sourceSha256") == source
            and capture.get("page_no") == page
            and capture.get("status") == "captured"
            and capture.get("fingerprint") == page_render_fingerprint(capture)
            and capture["fingerprint"] == plan["captureFingerprint"],
            "visual_apply_capture",
        )
        for item in plan["sources"]:
            node = doc.nodes[item["sourceRef"]]
            require(
                node["text"] == item["text"]
                and node.get("sourceStructure", {}).get("page") == page
                and node["sourceStructure"].get("tableRef") == item.get("tableRef"),
                "visual_apply_source_text_changed",
            )
        for slot in plan["slots"]:
            _verify_source_slot(doc, plan, capture, slot)
        require(
            validation
            == validate_decision(
                plan, validation["decision"], detail_bounds=validation["detailBounds"]
            )
            and validation["status"] == "reviewed",
            "visual_apply_not_reviewed",
        )
        selected[page] = review
    require(set(selected) == set(range(1, count + 1)), "visual_apply_page_inventory")
    return selected


def _verify_source_slot(doc, plan, capture, slot):
    """Original slot geometry is independent of new render-grid pixel rounding."""
    matches = []
    for batch in doc.provenance.get("recognitionCellPixelObservations", []):
        if batch.get("page") != plan["page"] or batch.get("sourceSha256") != plan["sourceSha256"]:
            continue
        for entry in batch.get("observations", []):
            association = entry.get("structureAssociation", {})
            if association.get("tableRef") != slot["tableRef"]:
                continue
            record, validation = entry["observation"], entry["validation"]
            require(
                association.get("status") == "unique_geometry_correspondence"
                and validation.get("status") == "verified"
                and record.get("fingerprint") == fingerprint(record)
                and record["fingerprint"] == slot["observationFingerprint"]
                and validation.get("observationFingerprint") == record["fingerprint"],
                "visual_apply_source_slot_changed",
            )
            for original in record["slots"]:
                if original["slotKey"] != slot["slotKey"]:
                    continue
                require(
                    original.get("geometryStatus") == "resolved"
                    and original.get("measurementStatus") == "measured"
                    and type(slot["row"]) is int
                    and type(slot["col"]) is int
                    and (original["row"], original["col"]) == (slot["row"], slot["col"]),
                    "visual_apply_source_slot_changed",
                )
                matches.append(
                    _mapped_box(
                        original["fullPixelBox"],
                        validation["canvasPixelToOriginalPageAffine"],
                        capture["pageSizeCanvasUnits"][1],
                    )
                )
    require(
        len(matches) == 1 and matches[0] == slot["sourceBounds"], "visual_apply_source_slot_changed"
    )


def _region_order(doc, reviews):
    """Every existing region and every reviewed block must map exactly once."""
    require(
        len(doc.regions) * sum(len(r["plan"]["blocks"]) for r in reviews.values())
        <= MAX_ORDER_COMPARISONS,
        "visual_apply_order_budget",
    )
    ordered, used = [], set()
    for page, review in sorted(reviews.items()):
        blocks = {b["id"]: b for b in review["plan"]["blocks"]}
        require(len(blocks) == len(review["plan"]["blocks"]), "visual_apply_order_inventory")
        for block_id in review["validation"]["decision"]["readingOrder"]:
            block = blocks[block_id]
            refs = block["sourceRefs"]
            require(
                refs
                and len(set(refs)) == len(refs)
                and all(doc.nodes[r].get("sourceStructure", {}).get("page") == page for r in refs),
                "visual_apply_order_source",
            )
            candidates = [
                i
                for i, region in enumerate(doc.regions)
                if region.get("tableRef") == block.get("tableRef")
                and set(region["nodeIds"]) == set(refs)
            ]
            require(len(candidates) == 1 and candidates[0] not in used, "visual_apply_order_region")
            used.add(candidates[0])
            ordered.append(candidates[0])
    require(used == set(range(len(doc.regions))), "visual_apply_order_region")
    return ordered


def _add_empty_slots(doc, reviews, output):
    for page, review in sorted(reviews.items()):
        plan = review["plan"]
        by_table = {}
        for slot in plan["slots"]:
            by_table.setdefault(slot["tableRef"], []).append(slot)
        for table_ref, slots in by_table.items():
            table = doc.tables[table_ref]
            require(table.get("page") == page, "visual_apply_slot_page")
            occupied = {
                (r, c)
                for cell in table["cells"]
                for r in range(cell["row"], cell["row"] + cell["rowSpan"])
                for c in range(cell["col"], cell["col"] + cell["colSpan"])
            }
            rows, cols = table.get("declaredRowCount"), table.get("declaredColCount")
            require(
                type(rows) is int and type(cols) is int and 0 < rows * cols <= 100000,
                "visual_apply_slot_dimensions",
            )
            expected = {(r, c) for r in range(rows) for c in range(cols)} - occupied
            positions = {(s["row"], s["col"]) for s in slots}
            require(
                len(positions) == len(slots)
                and positions == expected
                and table.get("unobservedCellCount") == len(slots),
                "visual_apply_slot_count",
            )
            issues = [
                i
                for i in doc.issues
                if i.get("code") == "recognition_table_cells_unobserved"
                and i.get("tableRef") == table_ref
            ]
            require(
                len(issues) == 1 and issues[0].get("count") == len(slots), "visual_apply_slot_issue"
            )
            issue = issues[0]
            require(
                len(doc.nodes) + len(slots) <= 100000 and len(doc.bindings) + len(slots) <= 200000,
                "visual_apply_observation_budget",
            )
            regions = [r for r in doc.regions if r.get("tableRef") == table_ref]
            require(len(regions) == 1, "visual_apply_slot_region")
            created = []
            for slot in sorted(slots, key=lambda s: (s["row"], s["col"])):
                ref = f"{table_ref}/visual-blank/{slot['row']}:{slot['col']}"
                require(ref not in doc.nodes, "visual_apply_node_collision")
                bounds = slot["sourceBounds"]
                require(
                    len(bounds) == 4 and bounds[0] < bounds[2] and bounds[1] < bounds[3],
                    "visual_apply_slot_bounds",
                )
                doc.node(
                    ref,
                    "",
                    role="table_cell",
                    observationBasis="visual_pdf_page_review",
                    recognizedText=False,
                    locator={
                        "page": page,
                        "tableRef": table_ref,
                        "sourceSha256": plan["sourceSha256"],
                        "bbox": dict(zip(("left", "top", "right", "bottom"), bounds, strict=True))
                        | {"origin": "TOPLEFT"},
                        "slotKey": slot["slotKey"],
                        "observationFingerprint": slot["observationFingerprint"],
                        "visualDecisionFingerprint": review["validation"]["decisionFingerprint"],
                        "visualPlanFingerprint": plan["fingerprint"],
                    },
                )
                binding = doc.bind(ref, start=0, end=0, candidateRole="content", blank=True)
                table["cells"].append(
                    {
                        "sourceRef": ref,
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
                regions[0]["nodeIds"].append(ref)
                regions[0]["bindingIds"].append(binding)
                created.append(ref)
            table["unobservedCellCount"] = 0
            table["visualBlankCellCount"] = len(created)
            doc.issues.remove(issue)
            evidence = {
                "page": page,
                "sourceRefs": created,
                "planFingerprint": plan["fingerprint"],
                "decisionFingerprint": review["validation"]["decisionFingerprint"],
                "remainingUnobservedCellCount": 0,
                "basis": "owned_visual_page_review",
            }
            _record_issue(output, issue, evidence)
            ledgers = [
                item
                for item in doc.provenance.get("recognitionProcessingLedgers", [])
                if table_ref in item.get("processingDependencies", {}).get("tableRefs", [])
            ]
            require(len(ledgers) == 1, "visual_apply_processing_dependency")
            ledger = ledgers[0]
            dependencies = ledger["processingDependencies"]
            require(
                dependencies["pages"] == [page] and dependencies["issues"].count(issue) == 1,
                "visual_apply_processing_dependency",
            )
            dependencies["issues"].remove(issue)
            ledger.setdefault("visualReviewResolutions", []).append(
                deepcopy(output["resolvedIssues"][-1])
            )
            ledger["visualReviewApplicationVersion"] = VERSION
            _update_processing(doc, ledger, output, evidence)


def _processing_complete(ledger):
    return (
        ledger.get("allRawOCRDetectionsPreserved") is True
        and not any(e["status"] == "unresolved" for e in ledger["entries"])
        and not ledger["unsupportedStructuralText"]
        and not ledger["unverifiedTableExtents"]
        and not ledger["processingDependencies"]["issues"]
    )


def _update_processing(doc, ledger, output, evidence):
    complete = _processing_complete(ledger)
    ledger["observedProcessingCoverage"] = "complete" if complete else "partial"
    if not complete:
        return
    unresolved = sum(e["status"] == "unresolved" for e in ledger["entries"])
    for issue in list(doc.issues):
        if (
            issue.get("code") == "recognition_observed_processing_partial"
            and issue.get("recognitionBatch") == ledger["batch"]
            and issue.get("unresolvedDetections") == unresolved
            and issue.get("unsupportedStructuralNodes") == len(ledger["unsupportedStructuralText"])
            and issue.get("rawInventoryVerified") is ledger["allRawOCRDetectionsPreserved"]
        ):
            _record_issue(
                output, issue, evidence | {"scope": "this_batch_returned_detections_only"}
            )
            doc.issues.remove(issue)
    for summary in doc.provenance.get("recognitionSourceObservations", []):
        if summary.get("batch") == ledger["batch"]:
            summary["observedProcessingCoverage"] = ledger["observedProcessingCoverage"]


def _replace_channel_issues(doc, reviews, output):
    # Execution errors, content conflicts and all semantic errors are not replaced.
    if any(i.get("code") not in _REPLACEMENT_CODES for i in doc.issues):
        return
    ledgers = doc.provenance.get("recognitionProcessingLedgers", [])
    require(
        len(ledgers) == len(reviews)
        and {tuple(item["processingDependencies"]["pages"]) for item in ledgers}
        == {(p,) for p in reviews}
        and all(
            item["observedProcessingCoverage"] == "complete" and _processing_complete(item)
            for item in ledgers
        ),
        "visual_apply_processing_incomplete",
    )
    for issue in list(doc.issues):
        page = issue.get("page")
        if issue["code"] in {"pdf_page_has_no_native_text", "pdf_page_without_text"}:
            # Legacy projection stores extractor page details under details.
            page = page if page is not None else issue.get("details", {}).get("page")
            require(type(page) is int and page in reviews, "visual_apply_issue_page")
        evidence = {
            "basis": "owned_visual_replacement_channel",
            "page": page,
            "reviewFingerprints": [
                reviews[p]["plan"]["fingerprint"]
                for p in ([page] if page in reviews else sorted(reviews))
            ],
        }
        _record_issue(output, issue, evidence)
        doc.issues.remove(issue)
    doc.coverage["recognitionContentCompleteness"] = "complete"
    channels = doc.provenance.get("pdfObservationChannels")
    if channels is not None:
        channels["visualContentCoverage"] = "complete"
        channels["readingOrderCoverage"] = "complete"
        channels["ocrTruthVerified"] = False


def _preserve_dispositions(doc, output):
    channels = doc.provenance.get("pdfObservationChannels")
    if channels is None:
        return
    dispositions = channels.setdefault("issueDispositions", [])
    for resolution in output["resolvedIssues"]:
        matching = [d for d in dispositions if d.get("issue") == resolution["originalIssue"]]
        if not matching:
            matching = [
                {
                    "issue": deepcopy(resolution["originalIssue"]),
                    "issueFingerprint": resolution["issueFingerprint"],
                    "channel": "legacy_native_pdf",
                    "resolutionEvidence": [],
                }
            ]
            dispositions.extend(matching)
        for entry in matching:
            entry["status"] = "resolved_by_visual_review"
            entry.setdefault("resolutionEvidence", []).append(deepcopy(resolution))


def apply_page_reviews(doc: ObservationDocument, reviews) -> ObservationDocument:
    """Apply all pages atomically; failure preserves every original observation field.

    Only the internal owned runner supplies these reviews. Digests detect changed
    inputs, not caller authority or independent accuracy. No native source bytes,
    raw OCR captures, existing text/bindings, or native projection are rewritten.
    """
    output = {
        "version": VERSION,
        "status": "unresolved",
        "resolvedIssues": [],
        "ocrTruthVerified": False,
        "independentQualityApproval": False,
    }
    original = deepcopy(doc)
    try:
        selected = _validated(doc, reviews)
        order = _region_order(doc, selected)
        result = deepcopy(doc)
        _add_empty_slots(result, selected, output)
        result.regions = [result.regions[i] for i in order]
        _replace_channel_issues(result, selected, output)
        _preserve_dispositions(result, output)
        result.coverage.update(
            nodeCount=len(result.nodes),
            bindingCount=len(result.bindings),
            tableCount=len(result.tables),
            status="partial" if result.issues else "observed",
        )
        output.update(
            status="applied",
            sourceSha256=doc.provenance["sourceSha256"],
            pageReviews=[
                {
                    "page": p,
                    "planFingerprint": r["plan"]["fingerprint"],
                    "decisionFingerprint": r["validation"]["decisionFingerprint"],
                }
                for p, r in sorted(selected.items())
            ],
            regionOrder=[r["id"] for r in result.regions],
        )
        output["fingerprint"] = digest(output)
        result.provenance["pdfVisualReviewApplication"] = output
        return result
    except (
        PdfVisualReviewError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OverflowError,
        AttributeError,
        RecursionError,
    ) as exc:
        output = {
            "version": VERSION,
            "status": "unresolved",
            "resolvedIssues": [],
            "reason": str(exc)
            if isinstance(exc, PdfVisualReviewError)
            else "visual_apply_input_invalid",
            "ocrTruthVerified": False,
            "independentQualityApproval": False,
        }
        output["fingerprint"] = digest(output)
        original.provenance["pdfVisualReviewApplication"] = output
        return original
