"""Independent, bounded full-render pixel observations, never content completion."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

VERSION = "document-files.full-render-visual.v1"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def visual_profile(*, max_pixels=16000000, max_runs=65536, max_components=4096):
    return {
        "version": VERSION,
        "maxPixels": max_pixels,
        "maxForegroundRuns": max_runs,
        "maxComponents": max_components,
        "connectivity": 8,
        "referenceRGB": [255, 255, 255],
        "lowContrastMaxChannelDifference": 31,
        "backgroundPolicy": "exact_white_reference_not_semantic_background",
        "resampling": False,
    }


def observe_rgb(image, *, source_sha, page, render_profile):
    """Inspect actual full RGB pixels without any OCR rectangle or text input.

    First count *all* non-white pixels, including faint/color pixels. Bound the
    component algorithm by a foreground-run prefix before allocating native
    labels/statistics. An omitted suffix/geometry is explicitly truncated.
    """
    settings = deepcopy(render_profile["visualObservation"])
    width, height = image.size
    result = {
        "version": VERSION,
        "sourceSha256": source_sha,
        "page": page,
        "renderProfileSha256": digest(render_profile),
        "rgbSha256": None,
        "pixelSize": [width, height],
        "pixelMode": image.mode,
        "settings": settings,
        "scope": "full_displayed_render_pixels_only",
        "status": "unavailable",
        "pixelInventoryStatus": "not_run",
        "componentInventoryStatus": "not_run",
        "inputPixelCount": width * height,
        "processedPixelBounds": [0, 0, 0, 0],
        "componentAnalyzedPixelBounds": [0, 0, 0, 0],
        "components": [],
        "truncationReasons": [],
        "ocrBBoxesUsed": False,
        "contentCoverageVerified": False,
        "readingOrderVerified": False,
        "ocrTruthVerified": False,
        "blankValueProven": False,
        "backgroundClassification": "unverified",
    }
    try:
        if image.mode != "RGB" or width <= 0 or height <= 0:
            raise ValueError("invalid full RGB image")
        for key, ceiling in (
            ("maxPixels", 16000000),
            ("maxForegroundRuns", 65536),
            ("maxComponents", 4096),
        ):
            if type(settings[key]) is not int or not 0 < settings[key] <= ceiling:
                raise ValueError("invalid visual budget")
        if settings != visual_profile(
            max_pixels=settings["maxPixels"],
            max_runs=settings["maxForegroundRuns"],
            max_components=settings["maxComponents"],
        ):
            raise ValueError("unsupported visual profile")
        if width * height > settings["maxPixels"]:
            result.update(status="truncated", truncationReasons=["pixel_budget_exceeded"])
        else:
            # Hash only after the pixel bound, before any classification.
            result["rgbSha256"] = hashlib.sha256(image.tobytes()).hexdigest()
            import cv2
            import numpy as np

            result["implementation"] = {"numpy": np.__version__, "opencv": cv2.__version__}
            darkest = np.asarray(image).min(axis=2)
            foreground = darkest != 255
            low_contrast = foreground & (
                darkest >= 255 - settings["lowContrastMaxChannelDifference"]
            )
            foreground_count = int(np.count_nonzero(foreground))
            low_count = int(np.count_nonzero(low_contrast))
            result.update(
                pixelInventoryStatus="captured",
                processedPixelBounds=[0, 0, width, height],
                exactWhitePixelCount=width * height - foreground_count,
                foregroundPixelCount=foreground_count,
                lowContrastPixelCount=low_count,
                higherContrastPixelCount=foreground_count - low_count,
                foregroundPixelsDiscarded=0,
                foregroundMaskSha256=hashlib.sha256(foreground.tobytes()).hexdigest(),
                lowContrastMaskSha256=hashlib.sha256(low_contrast.tobytes()).hexdigest(),
                foregroundMeaning="non_white_candidate_not_confirmed_text_or_graphic",
            )
            runs_per_row = foreground[:, 0].astype(np.int64)
            runs_per_row += np.count_nonzero(foreground[:, 1:] & ~foreground[:, :-1], axis=1)
            cumulative_runs = np.cumsum(runs_per_row)
            rows = int(
                np.searchsorted(cumulative_runs, settings["maxForegroundRuns"], side="right")
            )
            rows = min(rows, height)
            result.update(
                foregroundRunCount=int(cumulative_runs[-1]),
                analyzedForegroundRunCount=int(cumulative_runs[rows - 1]) if rows else 0,
                componentAnalyzedPixelBounds=[0, 0, width, rows],
                analyzedForegroundPixelCount=int(np.count_nonzero(foreground[:rows])),
                analyzedLowContrastPixelCount=int(np.count_nonzero(low_contrast[:rows])),
                componentInventoryStatus="captured" if rows == height else "truncated",
            )
            if rows != height:
                result["truncationReasons"].append("foreground_run_budget_exceeded")
            if rows:
                binary = np.ascontiguousarray(foreground[:rows], dtype=np.uint8)
                count, labels, stats, _ = cv2.connectedComponentsWithStats(
                    binary, connectivity=8, ltype=cv2.CV_32S
                )
                low_counts = np.bincount(labels[low_contrast[:rows]], minlength=count)
                geometry = sorted(
                    (
                        (int(top), int(left), int(h), int(w), int(area), int(low_counts[label]))
                        for label, (left, top, w, h, area) in enumerate(stats)
                        if label
                    ),
                )
            else:
                geometry = []
            result.update(
                componentCountInAnalyzedBounds=len(geometry),
                omittedComponentCount=max(0, len(geometry) - settings["maxComponents"]),
            )
            for top, left, h, w, area, low in geometry[: settings["maxComponents"]]:
                right, bottom = left + w, top + h
                result["components"].append(
                    {
                        "pixelBounds": [left, top, right, bottom],
                        "foregroundPixelCount": area,
                        "lowContrastPixelCount": low,
                        "touchesPageEdges": [
                            edge
                            for edge, touches in (
                                ("left", left == 0),
                                ("top", top == 0),
                                ("right", right == width),
                                ("bottom", bottom == height),
                            )
                            if touches
                        ],
                        "touchesUnprocessedBoundary": rows < height and bottom == rows,
                        "largeExtentOrDenseRegion": w * h >= width * height / 4
                        or area >= width * height / 4,
                        "contentKind": "unclassified",
                    }
                )
            if result["omittedComponentCount"]:
                result["componentInventoryStatus"] = "truncated"
                result["truncationReasons"].append("component_budget_exceeded")
            result["status"] = "truncated" if result["truncationReasons"] else "captured"
    except Exception as exc:
        result.update(
            status="unavailable",
            componentInventoryStatus="unavailable",
            errorType=type(exc).__name__,
        )
    result["fingerprint"] = digest(result)
    return result


def validate_visual(record, render, *, source_sha, page):
    """Validate a captured artifact's binding, never its interpretation or coverage."""
    try:
        if (
            record["version"] != VERSION
            or record["sourceSha256"] != source_sha
            or type(record["page"]) is not int
            or record["page"] != page
            or record["renderProfileSha256"] != digest(render["profile"])
            or record["settings"] != render["profile"]["visualObservation"]
            or record["pixelSize"] != render["pixelSize"]
            or record["pixelMode"] != "RGB"
            or record["rgbSha256"] != render["pixelSha256"]
            or record["fingerprint"]
            != digest({k: v for k, v in record.items() if k != "fingerprint"})
            or record["scope"] != "full_displayed_render_pixels_only"
            or any(
                record[key] is not False
                for key in (
                    "ocrBBoxesUsed",
                    "contentCoverageVerified",
                    "readingOrderVerified",
                    "ocrTruthVerified",
                    "blankValueProven",
                )
            )
        ):
            return False
        width, height = record["pixelSize"]
        if not all(type(v) is int and v > 0 for v in (width, height)):
            return False
        if record["status"] not in ("captured", "truncated", "unavailable"):
            return False
        if record["pixelInventoryStatus"] == "captured":
            counts = [
                record[k]
                for k in (
                    "foregroundPixelCount",
                    "exactWhitePixelCount",
                    "lowContrastPixelCount",
                    "higherContrastPixelCount",
                )
            ]
            if (
                any(type(v) is not int or v < 0 for v in counts)
                or sum(counts[:2]) != width * height
                or counts[2] + counts[3] != counts[0]
                or record["processedPixelBounds"] != [0, 0, width, height]
                or record["foregroundPixelsDiscarded"] != 0
            ):
                return False
        settings = record["settings"]
        if (
            record["inputPixelCount"] != width * height
            or record["backgroundClassification"] != "unverified"
            or len(record["components"]) > settings["maxComponents"]
        ):
            return False
        if record["status"] in ("captured", "truncated"):
            if (
                record["pixelInventoryStatus"] != "captured"
                or width * height > settings["maxPixels"]
            ):
                return False
            _, _, extent_width, rows = record["componentAnalyzedPixelBounds"]
            if (
                type(rows) is not int
                or not 0 <= rows <= height
                or record["componentAnalyzedPixelBounds"] != [0, 0, width, rows]
                or extent_width != width
                or not 0 <= record["analyzedForegroundRunCount"] <= settings["maxForegroundRuns"]
                or record["componentCountInAnalyzedBounds"] > record["analyzedForegroundRunCount"]
                or record["omittedComponentCount"]
                != record["componentCountInAnalyzedBounds"] - len(record["components"])
            ):
                return False
            area_sum = low_sum = 0
            for component in record["components"]:
                left, top, right, bottom = component["pixelBounds"]
                area, low = component["foregroundPixelCount"], component["lowContrastPixelCount"]
                if (
                    not all(type(v) is int for v in (left, top, right, bottom, area, low))
                    or not 0 <= left < right <= width
                    or not 0 <= top < bottom <= rows
                    or not 0 <= low <= area <= (right - left) * (bottom - top)
                    or area == 0
                    or component["contentKind"] != "unclassified"
                    or component["touchesPageEdges"]
                    != [
                        edge
                        for edge, touches in (
                            ("left", left == 0),
                            ("top", top == 0),
                            ("right", right == width),
                            ("bottom", bottom == height),
                        )
                        if touches
                    ]
                    or component["touchesUnprocessedBoundary"] != (rows < height and bottom == rows)
                ):
                    return False
                area_sum += area
                low_sum += low
            if (
                area_sum > record["analyzedForegroundPixelCount"]
                or low_sum > record["analyzedLowContrastPixelCount"]
            ):
                return False
            if record["status"] == "captured" and (
                rows != height
                or record["truncationReasons"]
                or record["omittedComponentCount"]
                or record["componentInventoryStatus"] != "captured"
                or area_sum != record["foregroundPixelCount"]
                or low_sum != record["lowContrastPixelCount"]
            ):
                return False
            if record["status"] == "truncated" and not record["truncationReasons"]:
                return False
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
