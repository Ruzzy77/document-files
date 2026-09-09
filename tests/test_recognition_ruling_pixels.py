"""Synthetic exact pixels are contract evidence, not OCR quality evidence."""

import hashlib
from copy import deepcopy

import pytest

from document_files.document_model import recognition_ruling_pixels as rp
from document_files.document_model.recognition_coordinates import fingerprint


class Pixels:
    mode = "RGB"
    size = (100, 100)

    def __init__(self, edits=()):
        self.data = bytearray(b"\xff" * 30000)
        for y in range(100):
            self.data[(y * 100 + 49) * 3 : (y * 100 + 51) * 3] = b"\x00" * 6
        for x, y, rgb in edits:
            self.data[(y * 100 + x) * 3 : (y * 100 + x + 1) * 3] = bytes(rgb)
        self.reads = 0

    def tobytes(self):
        self.reads += 1
        return bytes(self.data)


def fixture(edits=()):
    image = Pixels(edits)
    source = rp.capture_ocr_input(image)
    raw = {"left": "48", "top": "40", "width": "4", "height": "20", "conf": "90", "text": "I"}
    tsv = "\t".join(raw) + "\n" + "\t".join(raw.values()) + "\n"
    capture = {
        "image": source.identity,
        "pixelFrame": {"inputImage": source.identity, "page": 1},
        "tsv": tsv,
        "tsvSha256": hashlib.sha256(tsv.encode()).hexdigest(),
        "status": "complete",
        "detections": [
            {
                "ordinal": 0,
                "raw": raw,
                "text": "I",
                "accepted": True,
                "confidence": 90.0,
                "imageBBox": {"left": 48, "top": 40, "width": 4, "height": 20},
            }
        ],
    }
    line = {
        "id": "native-line",
        "kind": "primitive_line",
        "bounds": [49, 0, 51, 100],
        "segments": [[50, 0, 50, 100]],
        "strokeWidth": 2,
        "strokeColor": [0, 0, 0, 255],
        "fill": False,
        "dash": [],
        "hasTransparency": False,
        "clipPathCount": 0,
        "lineCap": 0,
    }
    page = {"pageSize": [100, 100], "objects": [line]}
    link = {
        "status": "verified",
        "pageCoordinateOrigin": "BOTTOMLEFT",
        "inputPixelToOriginalPageAffine": [1, 0, 0, -1, 0, 100],
        "sourceSupportedInputPixelBounds": [0, 0, 100, 100],
    }
    return image, source, capture, page, link


def measured(edits=()):
    image, source, capture, page, link = fixture(edits)
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    entry, pixels = rp.validate_ruling_windows(record, capture)[0]
    return image, source, capture, page, link, record, entry, pixels


def test_exact_pixel_capture_and_conservative_structure_only():
    image, _, capture, page, link, record, entry, pixels = measured()
    assert image.reads == 1
    assert record["usage"] == {"examinedPixels": 4 * 52, "measuredWindows": 1}
    assert len(pixels) == 4 * 52 * 3
    original = deepcopy(capture)
    result = rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)
    assert result["status"] == "verified_native_ruling_support"
    assert result["ocrTruthVerified"] is False
    assert capture == original


@pytest.mark.parametrize(
    "edits",
    [
        [(48, 45, (254, 254, 254))],  # Even faint isolated ink is retained, not thresholded.
        [(50, 45, (255, 255, 255))],  # Broken stroke.
        [(48, 45, (0, 0, 0))],  # Glyph arm / crossing stroke.
        [(49, 45, (254, 253, 254))],  # Colored ink, even inside source width.
    ],
)
def test_nonuniform_extra_or_faint_ink_is_unresolved(edits):
    _, _, _, page, link, _, entry, pixels = measured(edits)
    assert bytes(edits[0][2]) in pixels
    assert (
        rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)["status"]
        == "unresolved"
    )


@pytest.mark.parametrize("kind", ["text", "image", "primitive_line"])
def test_actual_glyph_or_second_or_crossing_source_content_blocks(kind):
    _, _, _, page, link, _, entry, pixels = measured()
    other = deepcopy(page["objects"][0])
    other.update(
        id="other", kind=kind, bounds=[45, 45, 55, 55], paintSupportBounds=[45, 45, 55, 55]
    )
    page["objects"].append(other)
    assert (
        rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)["reason"]
        == "native_content_conflict_or_nonunique_line"
    )


@pytest.mark.parametrize("change", ["short", "wide", "dash", "rotation", "budget"])
def test_native_stroke_limits(change):
    _, _, _, page, link, _, entry, pixels = measured()
    budget = 5
    if change == "short":
        page["objects"][0]["segments"] = [[50, 40, 50, 60]]
    elif change == "wide":
        page["objects"][0]["strokeWidth"] = 0.2
    elif change == "dash":
        page["objects"][0]["dash"] = [1, 1]
    elif change == "rotation":
        link["inputPixelToOriginalPageAffine"] = [0, 1, 1, 0, 0, 0]
    else:
        budget = 0
    assert (
        rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=budget)["status"]
        == "unresolved"
    )


def test_clipped_or_budget_limited_windows_preserve_detection():
    _, source, capture, _, _ = fixture()
    original = deepcopy(capture)
    record = rp.observe_ruling_windows(source, capture, max_pixels=0)
    assert record["windows"][0]["reason"] == "pixel_or_window_budget_exceeded"
    assert rp.validate_ruling_windows(record, capture) == []
    assert capture == original
    capture["detections"][0]["imageBBox"]["top"] = 0
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    assert record["windows"][0]["reason"] == "context_window_clipped"


@pytest.mark.parametrize(
    "field", ["frame", "image", "tsv", "detection", "pixels", "count", "version"]
)
def test_receiver_rechecks_original_links_and_lossless_bytes(field):
    _, _, capture, _, _, record, _, _ = measured()
    if field == "frame":
        capture["pixelFrame"]["page"] = 2
    elif field == "image":
        capture["image"]["sha256"] = "a" * 64
    elif field == "tsv":
        capture["tsvSha256"] = "b" * 64
    elif field == "detection":
        capture["detections"][0]["raw"]["text"] = "J"
    elif field == "pixels":
        record["windows"][0]["rows"][0][0][1] = 254
    elif field == "count":
        record["windows"][0]["pixelCount"] = True
    else:
        record["version"] = "old"
    record["fingerprint"] = fingerprint(record)
    with pytest.raises(ValueError):
        rp.validate_ruling_windows(record, capture)


def test_input_frame_different_image_is_not_measured():
    _, source, capture, _, _ = fixture()
    capture["image"]["sha256"] = "0" * 64
    result = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    assert result["status"] == "partial"
    assert result["usage"]["examinedPixels"] == 0


def test_deadline_failure_preserves_reserved_cost(monkeypatch):
    _, source, capture, _, _ = fixture()
    calls = iter([0, 0, 2])
    monkeypatch.setattr(rp.time, "monotonic", lambda: next(calls))
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000, deadline=1)
    assert record["status"] == "partial"
    assert record["usage"]["reservedBudgetConsumed"] == 10000
    assert record["usage"]["unknownWork"] is True
    assert record["windows"][0]["rows"]  # Partial bytes preserved, never used as support.
    with pytest.raises(ValueError):
        rp.validate_ruling_windows(record, capture)


def test_observation_exhausts_shared_budget_without_second_hash():
    image, source, capture, _, _ = fixture()
    first = rp.observe_ruling_windows(source, capture, max_pixels=300)
    second = rp.observe_ruling_windows(
        source, capture, max_pixels=300 - first["usage"]["examinedPixels"]
    )
    assert second["usage"]["examinedPixels"] == 0
    assert image.reads == 1


def test_source_support_clipping_is_not_ocr_input_content():
    _, _, _, page, link, _, entry, pixels = measured()
    link["sourceSupportedInputPixelBounds"] = [0, 30, 100, 100]
    assert (
        rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)["reason"]
        == "context_outside_source_supported_pixels"
    )


def test_receiver_dataflow_preserves_raw_nodes_bindings_and_issues(monkeypatch):
    from document_files.document_model import pdf_native_objects, recognition_coordinates
    from document_files.document_model.model import ObservationDocument
    from document_files.document_model.recognition_sources import (
        import_native_ruling_observations,
        raw_pass_fingerprint,
    )

    _, _, capture, page, link, record, _, _ = measured()
    capture["rulingPixelObservation"] = record
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload = {"rawOCRPasses": [capture]}
    doc = ObservationDocument(
        nodes={"n": {"text": "I"}},
        bindings={"b": {"nodeId": "n"}},
        issues=[{"code": "recognition_content_completeness_unverified"}],
        coverage={"status": "partial"},
    )
    mapping = {"sourceSha256": "a" * 64, "originalPageNumber": 1, "fingerprint": "mapping"}
    doc.provenance["recognitionCoordinateEvidence"] = [
        {
            "sourceSha256": "a" * 64,
            "page": 1,
            "status": "verified",
            "evidence": {"mapping": mapping},
        }
    ]
    # Synthetic inventory/coordinate contract; actual raw TSV + window validators run.
    monkeypatch.setattr(
        pdf_native_objects,
        "validate_native_inventory",
        lambda *a, **k: {"status": "verified", "pageInventory": page},
    )
    monkeypatch.setattr(
        recognition_coordinates, "coordinate_links", lambda *a: [deepcopy(link) for _ in a[1]]
    )
    original = deepcopy((doc.nodes, doc.bindings, doc.issues, doc.coverage, payload))
    import_native_ruling_observations(doc, payload, source_hash="a" * 64, page=1, prefix="p1")
    decision = doc.provenance["recognitionNativeRulingObservations"][0]["passes"][0]["decisions"][0]
    assert decision["status"] == "verified_native_ruling_support"
    assert decision["rawRef"] == "p1:raw:0:0"
    assert (doc.nodes, doc.bindings, doc.issues, doc.coverage, payload) == original
    # Wrong source/page and altered original TSV cannot obtain a new decision.
    mapping["sourceSha256"] = "b" * 64
    import_native_ruling_observations(doc, payload, source_hash="a" * 64, page=1, prefix="p1")
    assert doc.provenance["recognitionNativeRulingObservations"][-1]["status"] == "unverified"
    mapping["sourceSha256"] = "a" * 64
    capture["tsv"] += "unexpected"
    import_native_ruling_observations(doc, payload, source_hash="a" * 64, page=1, prefix="p1")
    assert doc.provenance["recognitionNativeRulingObservations"][-1]["passes"][0]["decisions"] == []


def test_missing_frame_preserves_ocr_capture_without_raising():
    _, source, capture, _, _ = fixture()
    del capture["pixelFrame"]
    original = deepcopy(capture)
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    assert record["status"] == "partial"
    assert record["reason"] == "input_coordinate_frame_unavailable"
    assert record["usage"]["examinedPixels"] == 0
    assert capture == original


def test_receiver_remaining_pixel_budget_is_checked_before_decoding():
    _, _, capture, _, _, record, _, _ = measured()
    # A malformed row would fail differently if decoding occurred first.
    record["windows"][0]["rows"][0] = None
    record["fingerprint"] = fingerprint(record)
    with pytest.raises(ValueError, match="pixel_record_budget_exceeded"):
        rp.validate_ruling_windows(record, capture, max_pixels=0)


def test_long_axis_only_window_preserves_original_short_extent():
    _, _, _, page, link, record, entry, pixels = measured()
    assert entry["longAxis"] == "vertical"
    assert entry["windowPixelBox"] == [48, 24, 52, 76]
    assert entry["windowPixelBox"][::2] == entry["detectionPixelBox"][::2]
    result = rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)
    assert result["shortAxisBackground"] == {
        "withinOriginalDetection": True,
        "verified": True,
        "fullyOutsideStrokePixelTracks": [1, 1],
        "rgb": [255, 255, 255],
    }
    assert result["longitudinalContinuity"]["pixelProfilesIdentical"] is True
    assert record["usage"]["examinedPixels"] == 208


def test_horizontal_window_and_side_background_are_symmetric():
    image, _, capture, page, link = fixture()
    before = bytes(image.data)
    for y in range(100):
        for x in range(100):
            image.data[(y * 100 + x) * 3 : (y * 100 + x + 1) * 3] = before[
                (x * 100 + y) * 3 : (x * 100 + y + 1) * 3
            ]
    source = rp.capture_ocr_input(image)
    capture["image"] = source.identity
    capture["pixelFrame"]["inputImage"] = source.identity
    capture["detections"][0]["imageBBox"] = {"left": 40, "top": 48, "width": 20, "height": 4}
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    entry, pixels = rp.validate_ruling_windows(record, capture)[0]
    assert entry["longAxis"] == "horizontal"
    assert entry["windowPixelBox"] == [24, 48, 76, 52]
    page["objects"][0].update(bounds=[0, 49, 100, 51], segments=[[0, 50, 100, 50]])
    result = rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)
    assert result["status"] == "verified_native_ruling_support"
    assert result["shortAxisBackground"]["fullyOutsideStrokePixelTracks"] == [1, 1]


@pytest.mark.parametrize("left,width", [(49, 2), (48, 3), (49, 3)])
def test_bbox_without_both_full_source_background_tracks_is_unresolved(left, width):
    _, source, capture, page, link = fixture()
    capture["detections"][0]["imageBBox"].update(left=left, width=width)
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    entry, pixels = rp.validate_ruling_windows(record, capture)[0]
    assert (
        rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)["reason"]
        == "source_backed_side_background_unavailable"
    )


def test_square_detection_does_not_choose_an_arbitrary_axis():
    _, source, capture, _, _ = fixture()
    capture["detections"][0]["imageBBox"]["width"] = 20
    record = rp.observe_ruling_windows(source, capture, max_pixels=10000)
    assert record["windows"][0]["reason"] == "bbox_long_axis_ambiguous"
    assert record["usage"]["examinedPixels"] == 0
    assert rp.validate_ruling_windows(record, capture) == []


@pytest.mark.parametrize(
    "bounds,accepted",
    [
        ([53, 40, 60, 60], True),  # Outside original bbox and longitudinal window.
        ([51, 40, 60, 60], False),  # Real glyph overlapping original bbox.
        ([48, 25, 52, 30], False),  # Real glyph in longitudinal context.
    ],
)
def test_glyph_conflicts_use_only_the_full_preserved_directional_window(bounds, accepted):
    _, _, _, page, link, _, entry, pixels = measured()
    page["objects"].append(
        {"id": "glyph", "kind": "text", "bounds": bounds, "paintSupportBounds": bounds}
    )
    result = rp.prove_native_ruling(entry, pixels, link, page, comparison_budget=5)
    assert (result["status"] == "verified_native_ruling_support") is accepted


def test_old_four_side_profile_or_tampered_axis_is_rejected():
    _, _, capture, _, _, record, _, _ = measured()
    original = deepcopy(record)
    record["profile"]["windowPolicy"] = "four_sides"
    record["fingerprint"] = fingerprint(record)
    with pytest.raises(ValueError, match="pixel_profile_unsupported"):
        rp.validate_ruling_windows(record, capture)
    original["windows"][0]["longAxis"] = "horizontal"
    original["fingerprint"] = fingerprint(original)
    with pytest.raises(ValueError, match="pixel_window_geometry_mismatch"):
        rp.validate_ruling_windows(original, capture)


def consumption_fixture(monkeypatch):
    from document_files.document_model import pdf_native_objects, recognition_coordinates
    from document_files.document_model.model import ObservationDocument
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    _, _, capture, page, link, record, _, _ = measured()
    capture.update(page_no=1, sourcePass="page_ocr", passId="p0", rulingPixelObservation=record)
    detection = capture["detections"][0]
    loc = {"l": 48.0, "t": 40.0, "r": 52.0, "b": 60.0, "coord_origin": "TOPLEFT"}
    detection.update(pageBBox=loc, mappingBasis="pinned_upstream_pre_merge_order_text_confidence")
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    cell = {
        "page_no": 1,
        "stage": "original_ocr",
        "fromOcr": True,
        "sourceKind": "ocr",
        "raw": "I",
        "text": "I",
        "bbox": loc,
        "confidence": 0.9,
        "backendCellIndex": 0,
    }
    payload = {"rawOCRPasses": [capture], "cells": [cell]}
    ref = "p1:source:0"
    issue = {
        "code": "recognition_unassigned_content",
        "sourceRef": ref,
        "candidateTableRefs": ["table"],
        "reason": "observed_text_not_uniquely_assigned_to_structure",
    }
    partial = {
        "code": "recognition_observed_processing_partial",
        "recognitionBatch": "p1",
        "unresolvedDetections": 1,
        "unsupportedStructuralNodes": 0,
        "rawInventoryVerified": True,
    }
    doc = ObservationDocument(
        nodes={
            ref: {
                "text": "I",
                "originalRecognitionText": "I",
                "recognizedText": True,
                "observationBasis": "ocr",
                "sourceObservationStage": "original_ocr",
                "semanticInput": {
                    "role": "unassigned_observation",
                    "candidateTableRefs": ["table"],
                },
                "sourceStructure": {
                    "page": 1,
                    "recognitionBatch": "p1",
                    "backendCellIndex": 0,
                    "sourceKind": "ocr",
                    "bbox": {
                        "left": 48.0,
                        "top": 40.0,
                        "right": 52.0,
                        "bottom": 60.0,
                        "origin": "TOPLEFT",
                    },
                },
            }
        },
        bindings={"b": {"sourceRef": ref, "path": "/text"}},
        tables={"table": {"id": "table", "contextNodeIds": [ref], "cells": []}},
        issues=[issue, partial, {"code": "recognition_content_completeness_unverified"}],
        coverage={"recognitionContentCompleteness": "unverified"},
    )
    doc.provenance["sourceSha256"] = "a" * 64
    mapping = {"sourceSha256": "a" * 64, "originalPageNumber": 1, "fingerprint": "mapping"}
    doc.provenance["recognitionCoordinateEvidence"] = [
        {
            "sourceSha256": "a" * 64,
            "page": 1,
            "status": "verified",
            "evidence": {"mapping": mapping},
        }
    ]
    monkeypatch.setattr(
        pdf_native_objects,
        "validate_native_inventory",
        lambda *a, **k: {"status": "verified", "pageInventory": page},
    )
    monkeypatch.setattr(
        recognition_coordinates, "coordinate_links", lambda *a: [deepcopy(link) for _ in a[1]]
    )
    ledger = {
        "version": "document-files.observed-processing-ledger.v5",
        "batch": "p1",
        "processingDependencies": {
            "pages": [1],
            "sourceRefs": [ref],
            "structuralRefs": [],
            "issues": [deepcopy(issue)],
        },
        "entries": [
            {
                "rawRef": "p1:raw:0:0",
                "status": "unresolved",
                "targetRefs": [ref],
                "reason": "unassigned_or_conflicting_detection",
            }
        ],
        "allRawOCRDetectionsPreserved": True,
        "observedProcessingCoverage": "partial",
        "unsupportedStructuralText": [],
        "unverifiedTableExtents": [],
        "rawOCRPasses": deepcopy(payload["rawOCRPasses"]),
    }
    from document_files.document_model import recognition_sources

    monkeypatch.setattr(
        recognition_sources,
        "_native_table_ruling_support",
        lambda *a, **k: {
            "status": "verified",
            "tableRef": "table",
            "comparisons": 4,
            "basis": "synthetic_table_boundary_contract_only",
        },
    )
    return doc, payload, ledger, ref


def test_consumption_preserves_raw_bindings_context_and_global_partial(monkeypatch):
    from document_files.document_model.recognition_sources import consume_native_rulings

    doc, payload, ledger, ref = consumption_fixture(monkeypatch)
    original = deepcopy((payload, doc.bindings, doc.tables, doc.coverage))
    consumed = consume_native_rulings(doc, payload, ledger, prefix="p1")
    assert consumed == {ref}
    assert doc.nodes[ref]["text"] == doc.nodes[ref]["originalRecognitionText"] == "I"
    assert doc.nodes[ref]["semanticInput"]["role"] == "context_only"
    assert (payload, doc.bindings, doc.tables, doc.coverage) == original
    assert ledger["entries"][0]["status"] == "native_ruling_non_data"
    assert ledger["observedProcessingCoverage"] == "complete"  # Only returned-word processing.
    assert doc.issues == [{"code": "recognition_content_completeness_unverified"}]
    result = doc.provenance["recognitionNativeRulingConsumptions"][0]
    assert len(result["resolvedIssues"]) == 2
    assert result["decisions"][0]["originalDisposition"]["status"] == "unresolved"
    assert result["documentCompletenessVerified"] is False


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_cell",
        "duplicate_raw",
        "conflict",
        "support",
        "node_text",
        "node_box",
        "stage",
        "raw_mutation",
        "foreign_source",
        "forged_provenance",
    ],
)
def test_consumption_rejects_ambiguous_changed_or_conflicting_membership(monkeypatch, change):
    from document_files.document_model.recognition_sources import consume_native_rulings

    doc, payload, ledger, ref = consumption_fixture(monkeypatch)
    if change == "duplicate_cell":
        payload["cells"].append(deepcopy(payload["cells"][0]))
    elif change == "duplicate_raw":
        payload["rawOCRPasses"].append(deepcopy(payload["rawOCRPasses"][0]))
        ledger["rawOCRPasses"] = deepcopy(payload["rawOCRPasses"])
    elif change == "conflict":
        doc.bindings["b"]["candidateStatus"] = "unresolved_conflict"
    elif change == "support":
        doc.relations.append(
            {"kind": "recognitionSourceSupport", "sourceRef": ref, "targetRef": "structural"}
        )
    elif change == "node_text":
        doc.nodes[ref]["text"] = "different"
    elif change == "node_box":
        doc.nodes[ref]["sourceStructure"]["bbox"]["left"] = 47
    elif change == "stage":
        payload["cells"][0]["stage"] = "derived_ocr"
    elif change == "raw_mutation":
        payload["rawOCRPasses"][0]["tsv"] += "different"
    elif change == "foreign_source":
        doc.provenance["sourceSha256"] = "b" * 64
    else:
        doc.provenance["recognitionNativeRulingObservations"] = [
            {"status": "verified", "rawRef": "p1:raw:0:0"}
        ]
        payload["rawOCRPasses"][0].pop("rulingPixelObservation")
    before = deepcopy((doc.nodes, doc.bindings, doc.issues, ledger))
    assert consume_native_rulings(doc, payload, ledger, prefix="p1") == set()
    assert (doc.nodes, doc.bindings, doc.issues, ledger) == before


def test_consumption_does_not_clear_other_raw_or_page_dependencies(monkeypatch):
    from document_files.document_model.recognition_sources import consume_native_rulings

    doc, payload, ledger, ref = consumption_fixture(monkeypatch)
    ledger["entries"].append({"rawRef": "other", "status": "unresolved", "reason": "unrelated"})
    doc.issues[1]["unresolvedDetections"] = 2
    other = {"code": "recognition_table_cells_unobserved", "tableRef": "scan", "count": 1}
    ledger["processingDependencies"]["issues"].append(deepcopy(other))
    doc.issues.append(deepcopy(other))
    page2 = {
        "code": "recognition_observed_processing_partial",
        "recognitionBatch": "p2",
        "unresolvedDetections": 1,
    }
    doc.issues.append(deepcopy(page2))
    assert consume_native_rulings(doc, payload, ledger, prefix="p1") == {ref}
    assert ledger["observedProcessingCoverage"] == "partial"
    assert ledger["entries"][1]["status"] == "unresolved"
    assert other in doc.issues and page2 in doc.issues
    assert (
        next(i for i in doc.issues if i.get("recognitionBatch") == "p1")["unresolvedDetections"]
        == 1
    )


def test_source_import_excludes_only_proven_nondata_before_final_projection(monkeypatch):
    from document_files.document_model.observe import _regions
    from document_files.document_model.pdf import _semantic_candidates
    from document_files.document_model.recognition_sources import (
        import_source_observations,
        raw_pass_fingerprint,
    )

    doc, payload, _, ref = consumption_fixture(monkeypatch)
    doc.nodes.clear()
    doc.bindings.clear()
    doc.issues = [{"code": "recognition_content_completeness_unverified"}]
    table = doc.tables["table"]
    table.update(
        page=1,
        locator={"bbox": {"left": 0, "top": 0, "right": 100, "bottom": 100, "origin": "TOPLEFT"}},
        declaredRowCount=1,
        declaredColCount=1,
    )
    table["contextNodeIds"] = []
    capture = payload["rawOCRPasses"][0]
    capture["transform"] = {
        "scale": 1,
        "orientation": 0,
        "crop": {"l": 0, "t": 0, "r": 100, "b": 100, "coord_origin": "TOPLEFT"},
    }
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload.update(
        version="document-files.recognition-source-observations.v1",
        rawCaptureVersion="document-files.raw-ocr.v1",
        rawCapturePages=[
            {"page_no": 1, "captureAvailable": True, "passFingerprints": [capture["fingerprint"]]}
        ],
    )
    exported = {"pages": {"1": {"size": {"height": 100}}}, "texts": []}
    primary = import_source_observations(doc, payload, exported, [], prefix="p1")
    assert primary == []
    preferred = _semantic_candidates(doc, [], primary, {})
    assert doc.nodes[ref]["semanticInput"]["role"] == "context_only"
    _regions(doc, preferred)
    assert ref in table["contextNodeIds"]
    assert doc.bindings and all(b["sourceRef"] == ref for b in doc.bindings.values())
    assert all(not r["bindingIds"] and ref not in r["nodeIds"] for r in doc.regions)
    assert doc.provenance["recognitionSourceObservations"][0]["unassignedCandidateCount"] == 0
    assert (
        doc.provenance["recognitionProcessingLedgers"][0]["entries"][0]["status"]
        == "native_ruling_non_data"
    )


def test_consumer_cell_budget_cannot_be_bypassed(monkeypatch):
    from document_files.document_model.recognition_sources import consume_native_rulings

    doc, payload, ledger, _ = consumption_fixture(monkeypatch)
    payload["cells"] *= 8193
    before = deepcopy((doc.nodes, doc.bindings, doc.issues, ledger))
    assert consume_native_rulings(doc, payload, ledger, prefix="p1") == set()
    assert (doc.nodes, doc.bindings, doc.issues, ledger) == before
    assert (
        doc.provenance["recognitionNativeRulingConsumptions"][-1]["reason"]
        == "consumer_input_budget_or_capture_mismatch"
    )


def table_boundary_fixture(monkeypatch):
    from document_files.document_model import (
        pdf_native_objects,
        recognition_cell_observations,
        recognition_sources,
    )
    from document_files.document_model.model import ObservationDocument

    _, _, _, page, _, _, _, _ = measured()
    page.update(rotation=0, coordinateOrigin="TOPLEFT")
    target = page["objects"][0]
    target["stroke"] = True
    for name, segment in [
        ("left", [20, 20, 20, 80]),
        ("top", [20, 20, 50, 20]),
        ("bottom", [20, 80, 50, 80]),
    ]:
        obj = deepcopy(target)
        obj.update(id=name, segments=[segment])
        page["objects"].append(obj)
    slot = {
        "fullPixelBox": [19, 19, 51, 81],
        "interiorPixelBox": [21, 21, 49, 79],
        "slotKey": "slot",
    }
    record = {
        "sourceFrame": {"pageSize": [100, 100]},
        "geometry": {"status": "verified_rectangular_grid"},
        "slots": [slot],
    }
    record["fingerprint"] = fingerprint(record)
    validation = {"status": "verified", "canvasPixelToOriginalPageAffine": [1, 0, 0, -1, 0, 100]}
    entry = {
        "observationStatus": "verified",
        "sourceCoordinateStatus": "verified",
        "observation": record,
        "validation": validation,
        "structureAssociation": {"status": "unique_geometry_correspondence", "tableRef": "table"},
    }
    doc = ObservationDocument(
        provenance={
            "pdfNativeObjects": {"fingerprint": "inventory"},
            "recognitionCoordinateEvidence": [
                {
                    "sourceSha256": "a" * 64,
                    "page": 1,
                    "status": "verified",
                    "evidence": {"mapping": {}},
                }
            ],
            "recognitionCellPixelObservations": [
                {"sourceSha256": "a" * 64, "page": 1, "batch": "p1", "observations": [entry]}
            ],
        }
    )
    # Native inventory and source-frame validators have separate direct contracts;
    # these tests exercise actual line-edge, closure, uniqueness and consumer wiring.
    monkeypatch.setattr(
        pdf_native_objects,
        "validate_native_inventory",
        lambda *a, **k: {"status": "verified", "pageInventory": page},
    )
    monkeypatch.setattr(
        recognition_cell_observations, "validate_cell_observation", lambda *a, **k: validation
    )
    monkeypatch.setattr(
        recognition_sources, "_cell_observation_table_candidates", lambda *a, **k: ["table"]
    )
    decision = {"nativeObjectRef": "native-line", "windowSourceBounds": [48, 24, 52, 76]}
    return doc, decision, page, record, entry


def test_native_stroke_must_form_a_real_closed_cell_boundary(monkeypatch):
    from document_files.document_model.recognition_sources import _native_table_ruling_support

    doc, decision, _, _, _ = table_boundary_fixture(monkeypatch)
    result = _native_table_ruling_support(
        doc, decision, page=1, prefix="p1", source_hash="a" * 64, comparison_budget=100
    )
    assert result["status"] == "verified"
    assert result["slots"][0]["edge"] == "right"
    assert set(result["slots"][0]["borderObjectRefs"]) == {"left", "right", "top", "bottom"}


@pytest.mark.parametrize(
    "change",
    [
        "interior_mark",
        "missing_edge",
        "gap",
        "duplicate_edge",
        "wrong_grid",
        "ambiguous_table",
        "unsupported_grid",
        "budget",
        "foreign_page",
    ],
)
def test_native_table_relationship_is_not_inferred_from_context_overlap(monkeypatch, change):
    from document_files.document_model.recognition_sources import _native_table_ruling_support

    doc, decision, page, record, entry = table_boundary_fixture(monkeypatch)
    budget = 100
    source_page = 1
    if change == "interior_mark":
        obj = deepcopy(page["objects"][0])
        obj.update(id="mark", segments=[[35, 25, 35, 75]])
        page["objects"].append(obj)
        decision["nativeObjectRef"] = "mark"
    elif change == "missing_edge":
        page["objects"].pop()
    elif change == "gap":
        page["objects"][1]["segments"] = [[20, 21, 20, 79]]
    elif change == "duplicate_edge":
        page["objects"].append(deepcopy(page["objects"][1]))
    elif change == "wrong_grid":
        record["slots"][0]["fullPixelBox"][2] = 61
        record["slots"][0]["interiorPixelBox"][2] = 59
        record["fingerprint"] = fingerprint(record)
    elif change == "ambiguous_table":
        doc.provenance["recognitionCellPixelObservations"][0]["observations"].append(
            deepcopy(entry)
        )
    elif change == "unsupported_grid":
        record["geometry"]["status"] = "partial"
        record["fingerprint"] = fingerprint(record)
    elif change == "budget":
        budget = 0
    else:
        source_page = 2
    result = _native_table_ruling_support(
        doc, decision, page=source_page, prefix="p1", source_hash="a" * 64, comparison_budget=budget
    )
    assert result["status"] == "unresolved"


def test_consumer_rejects_pixel_proof_without_table_boundary_support(monkeypatch):
    from document_files.document_model import recognition_sources

    doc, payload, ledger, _ = consumption_fixture(monkeypatch)
    monkeypatch.setattr(
        recognition_sources,
        "_native_table_ruling_support",
        lambda *a, **k: {"status": "unresolved", "comparisons": 4},
    )
    before = deepcopy((doc.nodes, doc.bindings, doc.issues, ledger))
    assert recognition_sources.consume_native_rulings(doc, payload, ledger, prefix="p1") == set()
    assert (doc.nodes, doc.bindings, doc.issues, ledger) == before
