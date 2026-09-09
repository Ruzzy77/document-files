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


CORRESPONDENCE_VERSION = "document-files.visual-correspondence.v1"
CORRESPONDENCE_LIMITS = {
    "sourceItems": 8192,
    "observationsPerKind": 1024,
    "bboxComparisons": 262144,
    "overlapCandidates": 8192,
}


def _project_box(bounds, matrix):
    import math

    left, top, right, bottom = bounds
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (*bounds, *matrix)):
        raise ValueError("non-finite geometry")
    if left >= right or top >= bottom or len(matrix) != 6:
        raise ValueError("invalid box")
    a, b, c, d, e, f = matrix
    points = [(a * x + c * y + e, b * x + d * y + f) for x in (left, right) for y in (top, bottom)]
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def _bbox_intersection(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def _visual_page_candidates(doc, render, visual, *, recognition_identity, limits):
    """Geometry candidates and exact-source proxies, NOT component assignments."""
    from .recognition_coordinates import _compose, coordinate_links, subset_mapping
    from .recognition_sources import page_render_fingerprint

    page, source_sha = render["page_no"], render["sourceSha256"]
    output = {
        "version": CORRESPONDENCE_VERSION,
        "page": page,
        "sourceSha256": source_sha,
        "renderFingerprint": render["fingerprint"],
        "visualFingerprint": visual["fingerprint"],
        "recognitionIdentityFingerprint": digest(recognition_identity)
        if isinstance(recognition_identity, dict)
        else None,
        "policy": "bbox_overlap_candidates_and_exact_source_proxies_only",
        "limits": deepcopy(limits),
        "sourceItemsExamined": 0,
        "bboxComparisons": 0,
        "observations": [],
        "components": [],
        "overlaps": [],
        "structures": [],
        "coordinateRenderEquivalences": [],
        "rawInventoryUnverified": False,
        "truncationReasons": [],
        "componentPixelIntersectionMeasured": False,
        "contentCoverageVerified": False,
        "ocrTruthVerified": False,
        "blankValueProven": False,
        "readingOrderVerified": False,
    }
    if (
        doc.provenance.get("sourceSha256") != source_sha
        or render["fingerprint"] != page_render_fingerprint(render)
        or not validate_visual(visual, render, source_sha=source_sha, page=page)
        or render.get("status") != "captured"
        or render.get("renderCoordinates", {}).get("status") != "verified"
        or visual.get("componentInventoryStatus") not in ("captured", "truncated")
    ):
        output.update(status="unavailable", reason="visual_or_render_binding_unverified")
        return output
    if visual["status"] != "captured":
        output["truncationReasons"].append("visual_inventory_incomplete")
    observations = output["observations"]
    structure_nodes = {}
    glyph_lookup = {}
    raw_lookup = {}
    kind_counts = {}
    page_to_pixels = render["renderCoordinates"]["pageToPixelAffine"]
    native_page = doc.nodes.get(f"pdf:page:{page}", {}).get("sourceStructure", {})
    width, height = render["pageSizeCanvasUnits"]
    native_geometry_valid = (
        render["intrinsicRotation"] == native_page.get("rotation") == 0
        and native_page.get("coordinateOrigin") == "TOPLEFT"
        and list(native_page.get("bbox") or []) == [0, 0, width, height]
        and native_page.get("width") == width
        and native_page.get("height") == height
        and list(render["pageBoxes"]["effective"] or []) == [0, 0, width, height]
        and list(render["pageBoxes"]["mediaDeclared"] or []) == [0, 0, width, height]
    )
    native_transform = _compose(page_to_pixels, [1, 0, 0, -1, 0, height])
    output["nativePageGeometryFingerprint"] = digest(native_page)

    def spend():
        if output["sourceItemsExamined"] >= limits["sourceItems"]:
            if "source_item_budget_exceeded" not in output["truncationReasons"]:
                output["truncationReasons"].append("source_item_budget_exceeded")
            return False
        output["sourceItemsExamined"] += 1
        return True

    def add(kind, ref, source, bounds=None, **details):
        if kind_counts.get(kind, 0) >= limits["observationsPerKind"]:
            reason = f"{kind}_observation_budget_exceeded"
            if reason not in output["truncationReasons"]:
                output["truncationReasons"].append(reason)
            return None
        index = len(observations)
        observations.append(
            {
                "kind": kind,
                "sourceRef": ref,
                "sourceObservationFingerprint": digest(source),
                "projectedPixelBounds": bounds,
                "geometryStatus": "available" if bounds else "unavailable",
                "candidateComponentIndices": [],
                "componentsCompared": 0,
                **details,
            }
        )
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        return index

    for ref, node in doc.nodes.items():
        if not spend():
            break
        loc = node.get("sourceStructure", {})
        if loc.get("page") != page:
            continue
        if node.get("observationBasis") == "recognition":
            if len(structure_nodes) < limits["observationsPerKind"]:
                structure_nodes[ref] = node
            elif "structure_observation_budget_exceeded" not in output["truncationReasons"]:
                output["truncationReasons"].append("structure_observation_budget_exceeded")
        if node.get("observationBasis") != "native_pdf" or not loc.get("characters"):
            continue
        offset = 0
        for index, char in enumerate(loc["characters"]):
            if not spend():
                break
            bounds = None
            text = char.get("text", "")
            if not isinstance(text, str):
                continue
            try:
                if native_geometry_valid and char.get("upright") is True:
                    bounds = _project_box(
                        [char[k] for k in ("x0", "top", "x1", "bottom")], native_transform
                    )
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
            observed = add(
                "native_glyph",
                ref,
                char,
                bounds,
                characterIndex=index,
                start=offset,
                end=offset + len(text),
                whitespace=text.isspace(),
                geometryBasis="unrotated_full_media_native_top_left_only",
            )
            if observed is not None:
                glyph_lookup[(ref, index)] = observed
            offset += len(text)

    # Existing imported coordinate evidence must still agree with the raw capture
    # and a source-bound render. Re-rendered visual observations may have a new
    # metadata fingerprint, but only exact RGB + page transform equality permits
    # a derived bridge. This never authorizes checkpoint reuse or rewrites it.
    render_records = {
        r["capture"].get("fingerprint"): r["capture"]
        for r in doc.provenance.get("pdfPageRenderCaptures", [])
        if isinstance(r.get("capture"), dict)
    }
    coordinates = [
        r
        for r in doc.provenance.get("recognitionCoordinateEvidence", [])
        if r.get("page") == page and r.get("status") == "verified"
    ]
    ledgers = [
        ledger
        for ledger in doc.provenance.get("recognitionProcessingLedgers", [])
        if page in ledger.get("processingDependencies", {}).get("pages", [])
    ]
    for ledger in ledgers:
        captures = ledger.get("rawOCRPasses", [])
        inventory_verified = ledger.get("allRawOCRDetectionsPreserved") is True
        output["rawInventoryUnverified"] |= not inventory_verified
        linked = {}
        for candidate in coordinates:
            evidence = candidate["evidence"]
            mapping = evidence["mapping"]
            prior = render_records.get(mapping["originalRenderFingerprint"], {})
            if (
                candidate.get("sourceSha256") != source_sha
                or prior.get("sourceSha256") != source_sha
                or prior.get("page_no") != page
                or prior.get("fingerprint") != page_render_fingerprint(prior)
                or any(
                    prior.get(k) != render.get(k)
                    for k in ("pixelSha256", "pixelSize", "renderCoordinates")
                )
            ):
                continue
            if mapping != subset_mapping(prior, mapping["subsetRender"]) or mapping[
                "subsetRender"
            ].get("fingerprint") != page_render_fingerprint(mapping["subsetRender"]):
                continue
            prefix = f"{ledger['batch']}:repair:"
            repair_index = {
                int(r["id"][len(prefix) :]): r
                for r in doc.provenance.get("tableOCRRepairs", [])
                if isinstance(r.get("id"), str)
                and r["id"].startswith(prefix)
                and r["id"][len(prefix) :].isdigit()
            }
            if sorted(repair_index) != list(range(len(repair_index))):
                continue
            computed = coordinate_links(
                mapping,
                captures,
                [repair_index[i] for i in range(len(repair_index))],
                ledger.get("rawOCRRuns", []),
            )
            original_links = evidence.get("rawPassLinks", [])
            if computed != original_links:
                continue
            if any(link.get("status") != "verified" for link in computed):
                continue
            linked = {link["passFingerprint"]: link for link in original_links}
            output["coordinateRenderEquivalences"].append(
                {
                    "coordinateFingerprint": digest(evidence),
                    "coordinateRenderFingerprint": prior["fingerprint"],
                    "visualRenderFingerprint": render["fingerprint"],
                    "basis": "same_source_page_exact_RGB_and_render_transform",
                }
            )
            break
        for capture_index, capture in enumerate(captures):
            link = linked.get(capture.get("fingerprint"))
            for index, detection in enumerate(capture.get("detections", [])):
                if not spend():
                    break
                bounds = None
                fraction = None
                try:
                    if not link or detection.get("accepted") is not True:
                        raise ValueError
                    b = detection["imageBBox"]
                    raw_box = [b["left"], b["top"], b["left"] + b["width"], b["top"] + b["height"]]
                    image_width, image_height = capture["image"]["size"]
                    if not (
                        0 <= raw_box[0] < raw_box[2] <= image_width
                        and 0 <= raw_box[1] < raw_box[3] <= image_height
                    ):
                        raise ValueError("input bbox outside captured image")
                    support = link["sourceSupportedInputPixelBounds"]
                    supported = [
                        max(raw_box[0], support[0]),
                        max(raw_box[1], support[1]),
                        min(raw_box[2], support[2]),
                        min(raw_box[3], support[3]),
                    ]
                    fraction = _bbox_intersection(raw_box, support) / (b["width"] * b["height"])
                    bounds = _project_box(
                        supported, _compose(page_to_pixels, link["inputPixelToOriginalPageAffine"])
                    )
                except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError):
                    bounds, fraction = None, None
                ref = f"{ledger['batch']}:raw:{capture_index}:{index}"
                observed = add(
                    "raw_ocr",
                    ref,
                    detection,
                    bounds,
                    passFingerprint=capture.get("fingerprint"),
                    rawInventoryVerified=inventory_verified,
                    coordinateLinkFingerprint=digest(link) if link else None,
                    sourceSupportedBBoxFraction=fraction,
                    geometryStatus="partial_source_support"
                    if bounds and fraction != 1
                    else "available"
                    if bounds
                    else "unavailable",
                )
                if observed is not None:
                    raw_lookup[ref] = (observed, detection)

    # No semantic choice is made by overlap. Multiple sources, partial bbox
    # intersections and enclosure by a large OCR box remain explicit candidates.
    for ci, component in enumerate(visual["components"]):
        state = {"componentIndex": ci, "candidateObservationIndices": [], "observationsCompared": 0}
        output["components"].append(state)
        for oi, observation in enumerate(observations):
            box = observation["projectedPixelBounds"]
            if box is None:
                continue
            if (
                output["bboxComparisons"] >= limits["bboxComparisons"]
                or len(output["overlaps"]) >= limits["overlapCandidates"]
            ):
                if "comparison_or_candidate_budget_exceeded" not in output["truncationReasons"]:
                    output["truncationReasons"].append("comparison_or_candidate_budget_exceeded")
                break
            output["bboxComparisons"] += 1
            state["observationsCompared"] += 1
            observation["componentsCompared"] += 1
            area = _bbox_intersection(component["pixelBounds"], box)
            if not area:
                continue
            cb = component["pixelBounds"]
            ca = (cb[2] - cb[0]) * (cb[3] - cb[1])
            oa = (box[2] - box[0]) * (box[3] - box[1])
            output["overlaps"].append(
                {
                    "componentIndex": ci,
                    "observationIndex": oi,
                    "bboxIntersectionArea": area,
                    "componentBBoxFraction": area / ca,
                    "observationBBoxFraction": area / oa,
                    "kind": "enclosure_candidate"
                    if area == ca
                    else "partial_bbox_overlap_candidate",
                }
            )
            state["candidateObservationIndices"].append(oi)
            observation["candidateComponentIndices"].append(ci)
    available_observations = sum(o["projectedPixelBounds"] is not None for o in observations)
    for state in output["components"]:
        count = len(state["candidateObservationIndices"])
        state["status"] = (
            "multiple_overlap_candidates"
            if count > 1
            else "single_overlap_candidate"
            if count
            else "no_overlap_candidate"
        )
        state["comparisonIncomplete"] = (
            bool(output["truncationReasons"])
            or state["observationsCompared"] != available_observations
        )
    for observation in observations:
        count = len(observation["candidateComponentIndices"])
        observation["status"] = (
            "geometry_unavailable"
            if observation["projectedPixelBounds"] is None
            else "multiple_component_candidates"
            if count > 1
            else "single_component_candidate"
            if count
            else "no_component_candidate"
        )
        observation["comparisonIncomplete"] = bool(output["truncationReasons"]) or observation[
            "componentsCompared"
        ] != len(visual["components"])

    proxies = {ref: [] for ref in structure_nodes}
    for ledger in ledgers:
        for entry in ledger.get("entries", []):
            if not spend():
                break
            source = raw_lookup.get(entry.get("rawRef"))
            targets = entry.get("targetRefs", [])
            if (
                not source
                or entry.get("status") != "structural_observation"
                or len(targets) != 1
                or targets[0] not in proxies
            ):
                continue
            node = structure_nodes[targets[0]]
            text = node.get("originalRecognitionText", node["text"])
            start, end = entry.get("targetStart"), entry.get("targetEnd")
            if (
                type(start) is int
                and type(end) is int
                and 0 <= start < end <= len(text)
                and text[start:end] == source[1].get("text")
            ):
                proxies[targets[0]].append(
                    {
                        "observationIndex": source[0],
                        "targetStart": start,
                        "targetEnd": end,
                        "basis": "existing_raw_exact_text_target_range",
                    }
                )
    for relation in doc.relations:
        if not spend():
            break
        ref = relation.get("targetRef")
        if (
            ref not in proxies
            or relation.get("kind") != "observationEquivalence"
            or relation.get("comparison") != "exact_characters"
            or relation.get("resolution") != "equivalent"
            or relation.get("basis") != "native_character_geometry"
        ):
            continue
        selected = []
        parts = []
        for segment in relation.get("sourceSegments", []):
            if not spend():
                break
            index = glyph_lookup.get((segment.get("sourceRef"), segment.get("characterIndex")))
            if index is None:
                break
            observed = observations[index]
            node = doc.nodes[observed["sourceRef"]]
            if (segment.get("start"), segment.get("end")) != (observed["start"], observed["end"]):
                break
            parts.append(node["text"][observed["start"] : observed["end"]])
            selected.append(index)
        else:
            target = structure_nodes[ref]
            text = target.get("originalRecognitionText", target["text"])
            if "".join(parts) == text:
                start = 0
                for index, part in zip(selected, parts, strict=True):
                    proxies[ref].append(
                        {
                            "observationIndex": index,
                            "targetStart": start,
                            "targetEnd": start + len(part),
                            "basis": "existing_native_exact_character_equivalence",
                        }
                    )
                    start += len(part)
    proxy_candidates = 0
    for ref, node in structure_nodes.items():
        candidates = set()
        for proxy in proxies[ref]:
            for index in observations[proxy["observationIndex"]]["candidateComponentIndices"]:
                if not spend():
                    break
                candidates.add(index)
        indices = sorted(candidates)
        ranges = sorted((p["targetStart"], p["targetEnd"]) for p in proxies[ref])
        supported_characters, previous_end = 0, 0
        for start, end in ranges:
            supported_characters += max(0, end - max(start, previous_end))
            previous_end = max(previous_end, end)
        original_text = node.get("originalRecognitionText", node["text"])
        remaining = max(0, limits["overlapCandidates"] - proxy_candidates)
        if len(indices) > remaining:
            if "structure_candidate_budget_exceeded" not in output["truncationReasons"]:
                output["truncationReasons"].append("structure_candidate_budget_exceeded")
            indices = indices[:remaining]
        proxy_candidates += len(indices)
        for index in indices:
            output["components"][index].setdefault("candidateStructuralRefs", []).append(ref)
        output["structures"].append(
            {
                "sourceRef": ref,
                "sourceObservationFingerprint": digest(
                    {"text": node["text"], "sourceStructure": node.get("sourceStructure")}
                ),
                "sourceProxies": proxies[ref],
                "candidateComponentIndices": indices,
                "status": "source_proxy_candidates" if proxies[ref] else "no_verified_source_proxy",
                "sourceProxyCharacterCount": supported_characters,
                "originalCharacterCount": len(original_text),
                "sourceProxyRangeStatus": "full_text_range"
                if supported_characters == len(original_text) and original_text
                else "partial_text_range"
                if supported_characters
                else "no_text_range",
                "reportedStructureBBoxUsed": False,
                "visualAssignmentVerified": False,
            }
        )
    output["geometryUnavailableObservationCount"] = sum(
        o["projectedPixelBounds"] is None for o in observations
    )
    output["partialSourceSupportedObservationCount"] = sum(
        o["geometryStatus"] == "partial_source_support" for o in observations
    )
    for edge in output["overlaps"]:
        if edge["kind"] == "partial_bbox_overlap_candidate":
            output["components"][edge["componentIndex"]]["hasPartialBBoxOverlap"] = True
    incomplete = (
        bool(output["truncationReasons"])
        or bool(output["geometryUnavailableObservationCount"])
        or output["rawInventoryUnverified"]
    )
    for state in [*output["components"], *observations, *output["structures"]]:
        state["comparisonIncomplete"] = incomplete or state.get("comparisonIncomplete", False)
    output["status"] = "partial_candidates" if incomplete else "captured_candidates"
    return output


def append_visual_correspondences(doc, *, recognition_identity=None, limits=None):
    """Append derived candidates only; preserve original nodes, captures and issues."""
    limits = deepcopy(CORRESPONDENCE_LIMITS if limits is None else limits)
    if any(
        type(limits.get(k)) is not int or not 0 < limits[k] <= v
        for k, v in CORRESPONDENCE_LIMITS.items()
    ):
        raise ValueError("invalid correspondence limits")
    renders = {
        r["capture"].get("fingerprint"): r["capture"]
        for r in doc.provenance.get("pdfPageRenderCaptures", [])
        if isinstance(r.get("capture"), dict)
    }
    records = []
    for imported in doc.provenance.get("pdfPageVisualObservations", []):
        render = renders.get(imported.get("renderFingerprint"))
        if not render or imported.get("bindingStatus") != "source_page_render_matched":
            continue
        try:
            result = _visual_page_candidates(
                doc,
                render,
                imported["observation"],
                recognition_identity=recognition_identity,
                limits=limits,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            IndexError,
            AttributeError,
            ZeroDivisionError,
            OverflowError,
        ) as exc:
            result = {
                "version": CORRESPONDENCE_VERSION,
                "status": "unavailable",
                "page": imported.get("page"),
                "renderFingerprint": imported.get("renderFingerprint"),
                "errorType": type(exc).__name__,
                "contentCoverageVerified": False,
                "ocrTruthVerified": False,
                "blankValueProven": False,
            }
        result["fingerprint"] = digest(result)
        records.append(result)
    doc.provenance["visualCorrespondences"] = records
    return records
