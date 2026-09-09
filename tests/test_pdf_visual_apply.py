"""Synthetic source observations only; no PDF, rendering, OCR or model execution."""

from copy import deepcopy

import pytest

from document_files.document_model.model import ObservationDocument
from document_files.document_model.recognition_cell_observations import fingerprint
from document_files.document_model.recognition_sources import page_render_fingerprint
from document_files.interpretation.pdf_visual_apply import apply_page_reviews
from document_files.interpretation.pdf_visual_plan import (
    digest,
    observation_page_fingerprint,
    validate_decision,
)


def fixture():
    sha = "a" * 64
    doc = ObservationDocument()
    doc.provenance.update(
        sourceSha256=sha,
        format="pdf",
        pdfium={"pageCount": 1},
        nativeProjection={"unmodified": True},
    )
    capture = {
        "page_no": 1,
        "sourceSha256": sha,
        "status": "captured",
        "pixelSize": [90, 90],
        "pageSizeCanvasUnits": [30, 30],
        "pixelSha256": "b" * 64,
    }
    capture["fingerprint"] = page_render_fingerprint(capture)
    doc.provenance["pdfPageRenderCaptures"] = [
        {"page": 1, "bindingStatus": "source_page_matched", "capture": capture}
    ]
    table_ref, text_ref, foot_ref = "table", "cell", "foot"

    def box(x, y, r, b):
        return {"left": x, "top": y, "right": r, "bottom": b, "origin": "TOPLEFT"}

    doc.node(
        text_ref,
        "A",
        observationBasis="recognition",
        locator={"page": 1, "tableRef": table_ref, "bbox": box(0, 0, 10, 10)},
    )
    doc.node(
        foot_ref,
        "foot",
        observationBasis="recognition",
        locator={"page": 1, "bbox": box(0, 20, 10, 25)},
    )
    first = doc.bind(text_ref, start=0, end=1)
    second = doc.bind(foot_ref, start=0, end=4)
    doc.tables[table_ref] = {
        "id": table_ref,
        "page": 1,
        "locator": {"bbox": box(0, 0, 20, 10)},
        "declaredRowCount": 1,
        "declaredColCount": 2,
        "unobservedCellCount": 1,
        "cells": [{"sourceRef": text_ref, "row": 0, "col": 0, "rowSpan": 1, "colSpan": 1}],
    }
    doc.regions = [
        {"id": "foot-region", "nodeIds": [foot_ref], "bindingIds": [second], "contextNodeIds": []},
        {
            "id": "table-region",
            "tableRef": table_ref,
            "nodeIds": [text_ref],
            "bindingIds": [first],
            "contextNodeIds": [foot_ref],
        },
    ]
    slots = [
        {
            "row": 0,
            "col": col,
            "slotKey": f"slot{col}",
            "geometryStatus": "resolved",
            "measurementStatus": "measured",
            "fullPixelBox": [col * 30, 0, (col + 1) * 30, 30],
            "regions": {"boundaryBands": [{"pixelBox": [col * 30, 0, (col + 1) * 30, 1]}]},
        }
        for col in range(2)
    ]
    record = {"slots": slots}
    record["fingerprint"] = fingerprint(record)
    doc.provenance["recognitionCellPixelObservations"] = [
        {
            "page": 1,
            "sourceSha256": sha,
            "observations": [
                {
                    "observation": record,
                    "structureAssociation": {
                        "status": "unique_geometry_correspondence",
                        "tableRef": table_ref,
                    },
                    "validation": {
                        "status": "verified",
                        "observationFingerprint": record["fingerprint"],
                        "canvasPixelToOriginalPageAffine": [1 / 3, 0, 0, -1 / 3, 0, 30],
                    },
                }
            ],
        }
    ]
    missing = {
        "code": "recognition_table_cells_unobserved",
        "tableRef": table_ref,
        "count": 1,
        "meaning": "not_observed_not_proven_blank",
    }
    doc.issues = [
        missing,
        {
            "code": "recognition_observed_processing_partial",
            "recognitionBatch": "batch1",
            "unresolvedDetections": 0,
            "unsupportedStructuralNodes": 0,
            "rawInventoryVerified": True,
        },
        {"code": "pdf_page_has_no_native_text", "page": 1},
        {"code": "pdf_page_without_text", "details": {"page": 1}},
        {"code": "reading_order_unverified", "details": {"adapter": "pdf"}},
        {"code": "recognition_content_completeness_unverified"},
    ]
    doc.provenance["recognitionProcessingLedgers"] = [
        {
            "version": "document-files.observed-processing-ledger.v5",
            "batch": "batch1",
            "allRawOCRDetectionsPreserved": True,
            "observedProcessingCoverage": "partial",
            "unsupportedStructuralText": [],
            "unverifiedTableExtents": [],
            "entries": [{"rawRef": "raw1", "status": "supported", "targetRefs": [text_ref]}],
            "rawOCRPasses": [{"original": "bytes"}],
            "rawOCRRuns": [{"original": "TSV"}],
            "processingDependencies": {
                "pages": [1],
                "tableRefs": [table_ref],
                "issues": [deepcopy(missing)],
            },
        }
    ]
    doc.provenance["recognitionSourceObservations"] = [
        {"batch": "batch1", "observedProcessingCoverage": "partial"}
    ]
    doc.provenance["pdfObservationChannels"] = {
        "visualContentCoverage": "not_assessed",
        "readingOrderCoverage": "not_assessed",
        "ocrTruthVerified": False,
        "issueDispositions": [
            {
                "issue": deepcopy(i),
                "issueFingerprint": digest(i),
                "status": "unresolved",
                "resolutionEvidence": [],
            }
            for i in doc.issues[:3]
        ],
    }
    doc.coverage = {
        "recognition": "complete",
        "recognitionContentCompleteness": "unverified",
        "legacyAnalysis": {"reading_order": "unverified"},
    }
    plan = {
        "version": "document-files.pdf-visual-review.v1",
        "sourceSha256": sha,
        "page": 1,
        "sourceObservationFingerprint": observation_page_fingerprint(doc, 1),
        "captureFingerprint": capture["fingerprint"],
        "pixelFingerprint": "c" * 64,
        "pixelSize": [90, 90],
        "sources": [
            {
                "id": "s0",
                "sourceRef": text_ref,
                "text": "A",
                "bounds": [0, 0, 30, 30],
                "tableRef": table_ref,
            },
            {
                "id": "s1",
                "sourceRef": foot_ref,
                "text": "foot",
                "bounds": [0, 60, 30, 75],
                "tableRef": None,
            },
        ],
        "units": [
            {"id": "u0", "sourceIds": ["s0"], "tableRefs": [], "onlyBoundaryPixels": False},
            {"id": "u1", "sourceIds": ["s1"], "tableRefs": [], "onlyBoundaryPixels": False},
            {"id": "u2", "sourceIds": [], "tableRefs": [table_ref], "onlyBoundaryPixels": True},
        ],
        "slots": [
            {
                "id": "e0",
                "tableRef": table_ref,
                "row": 0,
                "col": 1,
                "slotKey": "slot1",
                "gridStatus": "candidate",
                "bounds": [30, 0, 60, 30],
                "sourceBounds": [10, 0, 20, 10],
                "observationFingerprint": record["fingerprint"],
                "unitIds": ["u2"],
            }
        ],
        "blocks": [
            {"id": "o0", "sourceRefs": [text_ref], "bounds": [0, 0, 60, 30], "tableRef": table_ref},
            {"id": "o1", "sourceRefs": [foot_ref], "bounds": [0, 60, 30, 75], "tableRef": None},
        ],
        "precedences": [["o0", "o1"]],
        "foregroundPixelCount": 32,
        "splitRunCount": 3,
    }
    plan["fingerprint"] = digest(plan)
    decision = {
        "units": [
            {"id": u["id"], "decision": "source_text" if u["sourceIds"] else "table_border"}
            for u in plan["units"]
        ],
        "slots": [{"id": s["id"], "decision": "empty"} for s in plan["slots"]],
        "readingOrder": [b["id"] for b in plan["blocks"]],
        "unrepresentedContent": False,
    }
    validation = validate_decision(plan, decision, detail_bounds=[0, 0, 90, 90])
    return doc, [{"plan": plan, "validation": validation}]


def assert_unchanged(result, original):
    for key in ("nodes", "bindings", "tables", "regions", "issues", "coverage"):
        assert getattr(result, key) == getattr(original, key)
    expected = deepcopy(result.provenance)
    expected.pop("pdfVisualReviewApplication")
    assert expected == original.provenance
    assert result.provenance["pdfVisualReviewApplication"]["status"] == "unresolved"


def test_actual_plan_validator_applies_blank_order_and_exact_resolutions():
    doc, reviews = fixture()
    before = deepcopy(doc)
    result = apply_page_reviews(doc, reviews)
    assert doc == before
    assert result.provenance["pdfVisualReviewApplication"]["status"] == "applied"
    assert result.issues == []
    ref = "table/visual-blank/0:1"
    assert result.nodes[ref]["text"] == ""
    assert result.nodes[ref]["observationBasis"] == "visual_pdf_page_review"
    assert result.nodes[ref]["sourceStructure"]["bbox"] == {
        "left": 10,
        "top": 0,
        "right": 20,
        "bottom": 10,
        "origin": "TOPLEFT",
    }
    assert {k: result.nodes[k] for k in before.nodes} == before.nodes
    assert {k: result.bindings[k] for k in before.bindings} == before.bindings
    assert result.bindings["b3"]["blank"] is True
    assert [r["id"] for r in result.regions] == ["table-region", "foot-region"]
    assert result.regions[0]["contextNodeIds"] == ["foot"]
    assert result.tables["table"]["unobservedCellCount"] == 0
    assert result.provenance["nativeProjection"] == before.provenance["nativeProjection"]
    assert result.coverage["legacyAnalysis"] == before.coverage["legacyAnalysis"]
    for key in (
        "entries",
        "rawOCRPasses",
        "rawOCRRuns",
        "unsupportedStructuralText",
        "unverifiedTableExtents",
    ):
        assert (
            result.provenance["recognitionProcessingLedgers"][0][key]
            == before.provenance["recognitionProcessingLedgers"][0][key]
        )
    assert (
        result.provenance["recognitionSourceObservations"][0]["observedProcessingCoverage"]
        == "complete"
    )
    dispositions = result.provenance["pdfObservationChannels"]["issueDispositions"]
    assert {d["issueFingerprint"] for d in dispositions} == {digest(i) for i in before.issues}
    assert all(
        d["resolutionEvidence"] and d["status"] == "resolved_by_visual_review" for d in dispositions
    )
    assert result.provenance["pdfObservationChannels"]["ocrTruthVerified"] is False


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "missing_page",
        "decision_hash",
        "source",
        "capture",
        "node",
        "table",
        "region",
        "count_issue",
        "dependency",
        "node_collision",
    ],
)
def test_invalid_or_unknown_is_atomic(change):
    doc, reviews = fixture()
    if change == "unknown":
        decision = reviews[0]["validation"]["decision"]
        decision["slots"][0]["decision"] = "unknown"
        reviews[0]["validation"] = validate_decision(
            reviews[0]["plan"], decision, detail_bounds=[0, 0, 90, 90]
        )
    elif change == "missing_page":
        doc.provenance["pdfium"]["pageCount"] = 2
    elif change == "decision_hash":
        reviews[0]["validation"]["decisionFingerprint"] = "0" * 64
    elif change == "source":
        doc.provenance["sourceSha256"] = "f" * 64
    elif change == "capture":
        doc.provenance["pdfPageRenderCaptures"][0]["capture"]["pixelSha256"] = "f" * 64
    elif change == "node":
        doc.nodes["cell"]["text"] = "other"
    elif change == "table":
        doc.tables["table"]["cells"][0]["col"] = 1
    elif change == "region":
        doc.regions[0]["nodeIds"] = ["cell"]
    elif change == "count_issue":
        doc.issues[0]["count"] = 2
    elif change == "dependency":
        doc.provenance["recognitionProcessingLedgers"][0]["processingDependencies"]["issues"] = []
    elif change == "node_collision":
        doc.node("table/visual-blank/0:1", "original", locator={"page": 2})
    before = deepcopy(doc)
    result = apply_page_reviews(doc, reviews)
    assert doc == before
    assert_unchanged(result, before)


@pytest.mark.parametrize(
    "code",
    [
        "recognition_page_render_unavailable",
        "recognition_raw_capture_invalid",
        "native_recognition_text_conflict",
        "completion_budget_exceeded",
        "semantic_scope_unresolved",
    ],
)
def test_other_issues_never_resolved(code):
    doc, reviews = fixture()
    doc.issue(code, page=1)
    result = apply_page_reviews(doc, reviews)
    assert {"code": code, "page": 1} in result.issues
    assert {"code": "recognition_content_completeness_unverified"} in result.issues
    assert result.coverage["recognitionContentCompleteness"] == "unverified"


def test_stale_source_even_if_plan_digest_is_recomputed():
    doc, reviews = fixture()
    doc.nodes["cell"]["text"] = "changed"
    reviews[0]["plan"]["fingerprint"] = digest(
        {k: v for k, v in reviews[0]["plan"].items() if k != "fingerprint"}
    )
    assert_unchanged(apply_page_reviews(doc, reviews), doc)


def test_empty_review_list_and_non_mapping_fail_closed():
    doc, _ = fixture()
    for reviews in ([], [None], None):
        assert_unchanged(apply_page_reviews(doc, reviews), doc)


@pytest.mark.parametrize("field", ["sourceBounds", "slotKey", "row", "text"])
def test_resigned_plan_cannot_change_source_slot_or_literal(field):
    doc, reviews = fixture()
    plan = reviews[0]["plan"]
    if field == "sourceBounds":
        plan["slots"][0][field] = [11, 0, 20, 10]
    elif field == "slotKey":
        plan["slots"][0][field] = "not-the-source-slot"
    elif field == "row":
        plan["slots"][0][field] = False
    else:
        plan["sources"][0][field] = "other"
    plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
    reviews[0]["validation"] = validate_decision(
        plan, reviews[0]["validation"]["decision"], detail_bounds=[0, 0, 90, 90]
    )
    assert_unchanged(apply_page_reviews(doc, reviews), doc)


def test_processing_unknown_preserves_other_raw_evidence_and_global_guard():
    doc, reviews = fixture()
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    ledger["unsupportedStructuralText"] = ["foot"]
    doc.issues[1]["unsupportedStructuralNodes"] = 1
    before = deepcopy(ledger)
    result = apply_page_reviews(doc, reviews)
    assert result.tables["table"]["unobservedCellCount"] == 0
    current = result.provenance["recognitionProcessingLedgers"][0]
    for field in ("entries", "rawOCRRuns", "rawOCRPasses", "unsupportedStructuralText"):
        assert current[field] == before[field]
    assert current["observedProcessingCoverage"] == "partial"
    assert any(i["code"] == "recognition_observed_processing_partial" for i in result.issues)
    assert any(i["code"] == "recognition_content_completeness_unverified" for i in result.issues)


def test_order_comparison_budget_preserves_original(monkeypatch):
    from document_files.interpretation import pdf_visual_apply

    doc, reviews = fixture()
    monkeypatch.setattr(pdf_visual_apply, "MAX_ORDER_COMPARISONS", 1)
    assert_unchanged(apply_page_reviews(doc, reviews), doc)


def test_applied_counts_and_processing_status_are_recomputed():
    doc, reviews = fixture()
    doc.coverage.update(
        nodeCount=len(doc.nodes),
        bindingCount=len(doc.bindings),
        tableCount=len(doc.tables),
        status="partial",
    )
    doc.tables["table"]["contentCompleteness"] = "unverified"
    # Fixing metadata before a review changes its source inventory as intended.
    review = reviews[0]
    review["plan"]["sourceObservationFingerprint"] = observation_page_fingerprint(doc, 1)
    review["plan"]["fingerprint"] = digest(
        {k: v for k, v in review["plan"].items() if k != "fingerprint"}
    )
    review["validation"] = validate_decision(
        review["plan"], review["validation"]["decision"], detail_bounds=[0, 0, 90, 90]
    )
    result = apply_page_reviews(doc, reviews)
    assert result.coverage["nodeCount"] == len(result.nodes) == 3
    assert result.coverage["bindingCount"] == len(result.bindings) == 3
    assert result.coverage["tableCount"] == len(result.tables) == 1
    assert result.coverage["status"] == "observed"
    assert result.tables["table"]["contentCompleteness"] == "unverified"
    doc.issue("native_recognition_text_conflict", page=1)
    result = apply_page_reviews(doc, reviews)
    assert result.coverage["status"] == "partial"
