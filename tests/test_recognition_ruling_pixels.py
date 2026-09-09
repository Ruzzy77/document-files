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
    monkeypatch.setattr(recognition_coordinates, "coordinate_links", lambda *a: [link])
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
