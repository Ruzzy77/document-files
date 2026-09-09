"""Bounded exact OCR-input pixels and conservative native ruling support.

No rendering, OCR, text replacement, issue resolution, or document-completeness
promotion. RGB row runs are lossless, including every faint nonwhite pixel.
"""

from __future__ import annotations

import hashlib
import math
import time
from copy import deepcopy
from dataclasses import dataclass

from .recognition_coordinates import fingerprint

VERSION = "document-files.ocr-ruling-pixels.v1"
MAX_WINDOWS = 64
MAX_DETECTIONS = 4096
MAX_SIDE = 128
MARGIN = 16
MAX_PIXELS = 16000000
MAX_COMPARISONS = 65536
WINDOW_POLICY = "original_bbox_short_axis_long_axis_extension"


@dataclass(frozen=True)
class _InputPixels:
    pixels: bytes
    size: tuple
    mode: str
    sha256: str

    @property
    def identity(self):
        return {"size": list(self.size), "mode": self.mode, "sha256": self.sha256}


def capture_ocr_input(image):
    """Replace the existing in-process input hashing pass, not an extra pass."""
    pixels = image.tobytes()
    return _InputPixels(pixels, tuple(image.size), image.mode, hashlib.sha256(pixels).hexdigest())


def detection_identity(detection):
    return fingerprint({k: detection.get(k) for k in ("ordinal", "raw", "imageBBox", "accepted")})


def _box(detection, width, height):
    b = detection["imageBBox"]
    x, y, w, h = [b[k] for k in ("left", "top", "width", "height")]
    if (
        any(type(v) is not int for v in (x, y, w, h))
        or not 0 <= x < x + w <= width
        or not 0 <= y < y + h <= height
    ):
        raise ValueError("invalid_detection_box")
    return [x, y, x + w, y + h]


def _window(box):
    width, height = box[2] - box[0], box[3] - box[1]
    if width == height:
        raise ValueError("bbox_long_axis_ambiguous")
    if height > width:
        return "vertical", [box[0], box[1] - MARGIN, box[2], box[3] + MARGIN]
    return "horizontal", [box[0] - MARGIN, box[1], box[2] + MARGIN, box[3]]


def _profile():
    return {
        "maxSide": MAX_SIDE,
        "longAxisMargin": MARGIN,
        "maxDetections": MAX_DETECTIONS,
        "windowPolicy": WINDOW_POLICY,
    }


def observe_ruling_windows(source, capture, *, max_pixels, max_windows=MAX_WINDOWS, deadline=None):
    """Only use the immutable bytes from this same input-capture operation."""
    result = {
        "version": VERSION,
        "status": "partial",
        "inputImage": deepcopy(capture.get("image")),
        "pixelFrameFingerprint": fingerprint(capture["pixelFrame"])
        if isinstance(capture.get("pixelFrame"), dict)
        else None,
        "tsvSha256": capture.get("tsvSha256"),
        "windows": [],
        "profile": _profile(),
        "budget": {"maxPixels": max_pixels, "maxWindows": max_windows},
        "usage": {"examinedPixels": 0, "measuredWindows": 0},
        "ocrTruthVerified": False,
        "documentCompletenessVerified": False,
    }
    try:
        if (
            type(max_pixels) is not int
            or not 0 <= max_pixels <= MAX_PIXELS
            or type(max_windows) is not int
            or not 0 <= max_windows <= MAX_WINDOWS
        ):
            raise ValueError("invalid_pixel_budget")
        if (
            not isinstance(source, _InputPixels)
            or source.identity != capture.get("image")
            or source.mode != "RGB"
            or len(source.pixels) != source.size[0] * source.size[1] * 3
        ):
            raise ValueError("input_pixels_unavailable_or_mismatched")
        width, height = source.size
        if not capture.get("pixelFrame"):
            raise ValueError("input_coordinate_frame_unavailable")
        detections = capture.get("detections", [])
        if len(detections) > MAX_DETECTIONS:
            raise ValueError("detection_planning_budget_exceeded")
        for detection in detections:
            entry = {
                "detectionFingerprint": detection_identity(detection),
                "ordinal": detection.get("ordinal"),
                "status": "unexamined",
            }
            result["windows"].append(entry)
            if deadline is not None and time.monotonic() >= deadline:
                entry.update(status="unavailable", reason="pixel_observation_deadline_exceeded")
                continue
            if detection.get("accepted") is not True:
                entry.update(status="unavailable", reason="raw_detection_not_accepted")
                continue
            try:
                box = _box(detection, width, height)
            except (KeyError, TypeError, ValueError) as error:
                entry.update(status="unavailable", reason=type(error).__name__)
                continue
            entry["detectionPixelBox"] = box
            if box[2] - box[0] > MAX_SIDE or box[3] - box[1] > MAX_SIDE:
                entry.update(status="not_selected", reason="bbox_exceeds_small_window_profile")
                continue
            try:
                axis, window = _window(box)
            except ValueError as error:
                entry.update(status="not_selected", reason=str(error))
                continue
            entry["longAxis"] = axis
            entry["windowPixelBox"] = window
            if window[0] < 0 or window[1] < 0 or window[2] > width or window[3] > height:
                entry.update(status="unavailable", reason="context_window_clipped")
                continue
            area = (window[2] - window[0]) * (window[3] - window[1])
            if (
                result["usage"]["measuredWindows"] >= max_windows
                or area > max_pixels - result["usage"]["examinedPixels"]
            ):
                entry.update(status="unavailable", reason="pixel_or_window_budget_exceeded")
                continue
            # Reserve before touching any region bytes. Failure does not release it.
            result["usage"]["examinedPixels"] += area
            result["usage"]["measuredWindows"] += 1
            entry["rows"] = []
            hashed = hashlib.sha256()
            for y in range(window[1], window[3]):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("pixel_observation_deadline_exceeded")
                row = source.pixels[(y * width + window[0]) * 3 : (y * width + window[2]) * 3]
                hashed.update(row)
                runs = []
                for n in range(0, len(row), 3):
                    rgb = list(row[n : n + 3])
                    if runs and runs[-1][1:] == rgb:
                        runs[-1][0] += 1
                    else:
                        runs.append([1, *rgb])
                entry["rows"].append(runs)
            entry.update(status="measured", pixelCount=area, pixelSha256=hashed.hexdigest())
        result["status"] = "observed"
    except Exception as error:
        result["errorType"] = type(error).__name__
        result["reason"] = str(error)
        if result["usage"]["examinedPixels"]:
            result["usage"]["reservedBudgetConsumed"] = max_pixels
            result["usage"]["unknownWork"] = True
    result["fingerprint"] = fingerprint(result)
    return result


def validate_ruling_windows(record, capture, *, max_pixels=MAX_PIXELS, max_windows=MAX_WINDOWS):
    """Recompute retained window bytes/statistics, not unavailable full input RGB."""
    if (
        record["version"] != VERSION
        or record["fingerprint"] != fingerprint(record)
        or record["status"] != "observed"
        or record["inputImage"] != capture["image"]
        or record["pixelFrameFingerprint"] != fingerprint(capture["pixelFrame"])
        or record["tsvSha256"] != capture["tsvSha256"]
    ):
        raise ValueError("pixel_record_identity_mismatch")
    if record["profile"] != _profile() or record["inputImage"]["mode"] != "RGB":
        raise ValueError("pixel_profile_unsupported")
    budget = record["budget"]
    if (
        type(budget["maxPixels"]) is not int
        or not 0 <= budget["maxPixels"] <= MAX_PIXELS
        or type(budget["maxWindows"]) is not int
        or not 0 <= budget["maxWindows"] <= MAX_WINDOWS
    ):
        raise ValueError("pixel_budget_invalid")
    width, height = capture["image"]["size"]
    if any(type(v) is not int or v <= 0 for v in (width, height)):
        raise ValueError("pixel_image_dimensions_invalid")
    windows = record["windows"]
    if len(windows) != len(capture["detections"]) or len(windows) > MAX_DETECTIONS:
        raise ValueError("pixel_detection_inventory_mismatch")
    measured = []
    total = 0
    for entry, detection in zip(windows, capture["detections"], strict=True):
        if (
            entry["detectionFingerprint"] != detection_identity(detection)
            or entry["ordinal"] != detection["ordinal"]
        ):
            raise ValueError("pixel_detection_identity_mismatch")
        if entry["status"] != "measured":
            continue
        if detection.get("accepted") is not True:
            raise ValueError("pixel_detection_not_accepted")
        box = _box(detection, width, height)
        axis, window = _window(box)
        if (
            entry["longAxis"] != axis
            or entry["detectionPixelBox"] != box
            or entry["windowPixelBox"] != window
            or window[0] < 0
            or window[1] < 0
            or window[2] > width
            or window[3] > height
            or box[2] - box[0] > MAX_SIDE
            or box[3] - box[1] > MAX_SIDE
        ):
            raise ValueError("pixel_window_geometry_mismatch")
        w, h = window[2] - window[0], window[3] - window[1]
        total += w * h
        if (
            total > min(budget["maxPixels"], max_pixels)
            or len(measured) >= min(budget["maxWindows"], max_windows)
            or len(entry["rows"]) != h
        ):
            raise ValueError("pixel_record_budget_exceeded")
        pixels = bytearray()
        for row in entry["rows"]:
            count = 0
            for run in row:
                if (
                    len(run) != 4
                    or any(type(v) is not int for v in run)
                    or not 0 < run[0] <= w
                    or any(not 0 <= v <= 255 for v in run[1:])
                ):
                    raise ValueError("pixel_run_invalid")
                count += run[0]
                if count > w:
                    raise ValueError("pixel_run_overflow")
                pixels.extend(bytes(run[1:]) * run[0])
            if count != w:
                raise ValueError("pixel_row_incomplete")
        if (
            type(entry["pixelCount"]) is not int
            or entry["pixelCount"] != w * h
            or hashlib.sha256(pixels).hexdigest() != entry["pixelSha256"]
        ):
            raise ValueError("pixel_bytes_mismatch")
        measured.append((entry, bytes(pixels)))
    if record["usage"] != {"examinedPixels": total, "measuredWindows": len(measured)}:
        raise ValueError("pixel_usage_mismatch")
    return measured


def prove_native_ruling(entry, pixels, link, native_page, *, comparison_budget):
    result = {
        "status": "unresolved",
        "ordinal": entry["ordinal"],
        "detectionFingerprint": entry["detectionFingerprint"],
        "comparisons": 0,
        "ocrTruthVerified": False,
        "originalObservationPreserved": True,
    }
    try:
        if link["status"] != "verified" or link["pageCoordinateOrigin"] != "BOTTOMLEFT":
            raise ValueError("pixel_coordinates_unverified")
        a, b, c, d, e, f = link["inputPixelToOriginalPageAffine"]
        if (
            any(type(v) not in (int, float) or not math.isfinite(v) for v in (a, b, c, d, e, f))
            or not a > 0
            or not d < 0
            or b != 0
            or c != 0
        ):
            raise ValueError("pixel_transform_unsupported")
        height = native_page["pageSize"][1]

        def point(x, y):
            return [a * x + e, height - (d * y + f)]

        window = entry["windowPixelBox"]
        box = entry["detectionPixelBox"]
        supported = link["sourceSupportedInputPixelBounds"]
        if not (
            supported[0] <= window[0] < window[2] <= supported[2]
            and supported[1] <= window[1] < window[3] <= supported[3]
        ):
            raise ValueError("context_outside_source_supported_pixels")
        bounds = point(*window[:2]) + point(*window[2:])
        hits = []
        for obj in native_page["objects"]:
            if result["comparisons"] >= comparison_budget:
                raise ValueError("native_comparison_budget_exceeded")
            result["comparisons"] += 1
            other = obj.get("paintSupportBounds") if obj["kind"] == "text" else obj["bounds"]
            if not other:
                raise ValueError("native_content_bounds_unverified")
            if max(bounds[0], other[0]) <= min(bounds[2], other[2]) and max(
                bounds[1], other[1]
            ) <= min(bounds[3], other[3]):
                hits.append(obj)
        if len(hits) != 1 or hits[0]["kind"] != "primitive_line":
            raise ValueError("native_content_conflict_or_nonunique_line")
        line = hits[0]
        x1, y1, x2, y2 = line["segments"][0]
        half = line["strokeWidth"] / 2
        if (
            line["strokeColor"] != [0, 0, 0, 255]
            or line["fill"]
            or line["dash"]
            or line["hasTransparency"]
            or line["clipPathCount"] != 0
            or line["lineCap"] != 0
        ):
            raise ValueError("native_stroke_unsupported")
        vertical = x1 == x2
        if entry["longAxis"] != ("vertical" if vertical else "horizontal"):
            raise ValueError("native_stroke_axis_differs_from_detection")
        if vertical:
            if not min(y1, y2) <= bounds[1] < bounds[3] <= max(y1, y2):
                raise ValueError("native_line_does_not_span_context")
            support = [x1 - half, bounds[1], x1 + half, bounds[3]]
        else:
            if y1 != y2 or not min(x1, x2) <= bounds[0] < bounds[2] <= max(x1, x2):
                raise ValueError("native_line_does_not_span_context")
            support = [bounds[0], y1 - half, bounds[2], y1 + half]
        w, h = window[2] - window[0], window[3] - window[1]
        if len(pixels) != w * h * 3:
            raise ValueError("pixel_length_mismatch")
        # Side evidence stays inside the original OCR short-axis extent. The
        # source stroke footprint must leave an entire source-backed pixel on
        # each side, not just an antialiased/slightly overlapping white sample.
        guards = [0, 0]
        for offset in range(w if vertical else h):
            px, py = (
                (window[0] + offset, window[1]) if vertical else (window[0], window[1] + offset)
            )
            p, q = point(px, py), point(px + 1, py + 1)
            axis_index = 0 if vertical else 1
            if q[axis_index] <= support[axis_index]:
                guards[0] += 1
            if p[axis_index] >= support[axis_index + 2]:
                guards[1] += 1
        if min(guards) < 1:
            raise ValueError("source_backed_side_background_unavailable")
        first_track = None
        for outer in range(h if vertical else w):
            track = []
            for inner in range(w if vertical else h):
                xx, yy = (inner, outer) if vertical else (outer, inner)
                pos = (yy * w + xx) * 3
                rgb = list(pixels[pos : pos + 3])
                track.append(rgb)
                p = point(window[0] + xx, window[1] + yy)
                q = point(window[0] + xx + 1, window[1] + yy + 1)
                fully_inside = (
                    support[0] <= p[0] < q[0] <= support[2]
                    and support[1] <= p[1] < q[1] <= support[3]
                )
                if fully_inside and rgb != [0, 0, 0]:
                    raise ValueError("opaque_source_stroke_width_not_observed")
                if rgb == [255, 255, 255]:
                    continue
                if rgb == [0, 0, 0] and not fully_inside:
                    raise ValueError("black_pixel_outside_full_stroke_support")
                if (
                    not (
                        max(p[0], support[0]) < min(q[0], support[2])
                        and max(p[1], support[1]) < min(q[1], support[3])
                    )
                    or len(set(rgb)) != 1
                ):
                    raise ValueError("nonwhite_pixels_not_supported_by_stroke")
            if not any(rgb == [0, 0, 0] for rgb in track):
                raise ValueError("opaque_stroke_continuity_unverified")
            if track[0] != [255, 255, 255] or track[-1] != [255, 255, 255]:
                raise ValueError("white_side_guards_unverified")
            if first_track is not None and track != first_track:
                raise ValueError("stroke_pixel_profile_not_continuous")
            first_track = track
        # The stroke must run through the OCR box, not only its surroundings.
        inner = point(*box[:2]) + point(*box[2:])
        if not (inner[0] <= x1 <= inner[2] if vertical else inner[1] <= y1 <= inner[3]):
            raise ValueError("line_outside_detection")
        result.update(
            status="verified_native_ruling_support",
            nativeObjectRef=line["id"],
            windowSourceBounds=bounds,
            sourceStrokeBounds=support,
            pixelSha256=entry["pixelSha256"],
            longitudinalContinuity={
                "axis": entry["longAxis"],
                "extensionPixelsEachEnd": MARGIN,
                "verified": True,
                "pixelProfilesIdentical": True,
            },
            shortAxisBackground={
                "withinOriginalDetection": True,
                "verified": True,
                "fullyOutsideStrokePixelTracks": guards,
                "rgb": [255, 255, 255],
            },
            basis="unique_native_stroke_long_axis_continuity_and_original_bbox_side_background",
        )
    except (KeyError, ValueError, TypeError, IndexError, OverflowError) as error:
        result["reason"] = str(error)
    return result
