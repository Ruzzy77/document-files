"""Source-pixel cell observations, never business blanks or completeness approval."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy

from .recognition_coordinates import _frame_to_page, fingerprint, image_identity
from .table_ocr_repair import resolved_cell_geometry

VERSION = "document-files.cell-observation.v1"
MAX_CELLS = 4096
MAX_PIXELS = 64000000


def _area(box):
    return (box[2] - box[0]) * (box[3] - box[1])


def _box(box, width, height):
    if (
        not isinstance(box, (list, tuple))
        or len(box) != 4
        or any(type(v) is not int for v in box)
        or not 0 <= box[0] < box[2] <= width
        or not 0 <= box[1] < box[3] <= height
    ):
        raise ValueError("cell pixel bounds unavailable")
    return list(box)


def _bands(outer, inner):
    left, t, r, b = outer
    x, y, u, v = inner
    if not (left <= x < u <= r and t <= y < v <= b):
        raise ValueError("cell windows not nested")
    return [
        box
        for box in ([left, t, r, y], [left, v, r, b], [left, y, x, v], [u, y, r, v])
        if box[0] < box[2] and box[1] < box[3]
    ]


def _region(image, box):
    import numpy as np

    with image.crop(box) as region:
        values = np.asarray(region)
        rgb = values[:, :, :3]
        alpha = (
            values[:, :, 3]
            if image.mode == "RGBA"
            else np.full(values.shape[:2], 255, dtype=np.uint8)
        )
        white = (rgb == 255).all(axis=2)
        opaque = alpha == 255
        unknown = int((~opaque).sum())
        nonwhite = int((~white).sum())
        return {
            "pixelBox": list(box),
            "pixelCount": _area(box),
            "rgbSha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
            "alphaSha256": hashlib.sha256(alpha.tobytes()).hexdigest(),
            "opaqueWhitePixels": int((opaque & white).sum()),
            "opaqueNonWhitePixels": int((opaque & ~white).sum()),
            "unknownAlphaPixels": unknown,
            "nonWhiteRGBPixels": nonwhite,
            "minRGB": [int(v) for v in rgb.min(axis=(0, 1))],
            "maxRGB": [int(v) for v in rgb.max(axis=(0, 1))],
            "status": "unknown_alpha"
            if unknown
            else "observed_nonwhite"
            if nonwhite
            else "observed_background",
        }


def unavailable_cell_observation(*, cluster_id, local_page_number, reason):
    record = {
        "version": VERSION,
        "clusterId": cluster_id,
        "localPageNumber": local_page_number,
        "status": "unavailable",
        "reason": reason,
        "slots": [],
        "blankValueProven": False,
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
        "usage": {
            "totalExaminedPixels": 0,
            "measuredRegionPixels": 0,
            "identityPixels": 0,
            "examinedCells": 0,
        },
    }
    record["fingerprint"] = fingerprint(record)
    return record


def observe_table_cells(
    canvas,
    *,
    source_frame,
    table_crop_bounds,
    cluster_id,
    local_page_number,
    grid,
    units=(),
    max_pixels=16000000,
    max_cells=4096,
    preparation_pixels=None,
):
    """Inspect supplied pixels only. Boxes are canvas TOPLEFT; grid is crop-local."""
    if (
        type(max_pixels) is not int
        or not 0 <= max_pixels <= MAX_PIXELS
        or type(max_cells) is not int
        or not 0 <= max_cells <= MAX_CELLS
    ):
        raise ValueError("cell observation budget invalid")
    preparation = dict(preparation_pixels or {"frameIdentityPixels": 0, "gridDetectionPixels": 0})
    if (
        set(preparation) != {"frameIdentityPixels", "gridDetectionPixels"}
        or any(type(n) is not int or n < 0 for n in preparation.values())
        or sum(preparation.values()) > max_pixels
    ):
        raise ValueError("cell preparation budget invalid")
    preparation_cost = sum(preparation.values())
    if not isinstance(source_frame, dict):
        return unavailable_cell_observation(
            cluster_id=cluster_id,
            local_page_number=local_page_number,
            reason="source_frame_unavailable",
        )
    if canvas.mode not in {"RGB", "RGBA"}:
        return unavailable_cell_observation(
            cluster_id=cluster_id,
            local_page_number=local_page_number,
            reason="unsupported_pixel_mode",
        )
    width, height = canvas.size
    bounds = _box(table_crop_bounds, width, height)
    left, t, r, b = bounds
    cells, geometry = resolved_cell_geometry(
        grid, width=r - left, height=b - t, max_cells=max_cells
    )
    geometry.update(
        horizontalLines=deepcopy(grid.get("horizontalLines", [])),
        verticalLines=deepcopy(grid.get("verticalLines", [])),
    )
    geometry["fingerprint"] = fingerprint(geometry)
    record = {
        "version": VERSION,
        "localPageNumber": local_page_number,
        "clusterId": cluster_id,
        "sourceFrame": deepcopy(source_frame),
        "sourceFrameFingerprint": fingerprint(source_frame),
        "canvas": {"size": [width, height], "mode": canvas.mode},
        "tableCrop": {"pixelBounds": bounds},
        "geometry": geometry,
        "slots": [],
        "status": "partial",
        "identitiesMeasured": False,
        "limits": {"maxPixels": max_pixels, "maxCells": max_cells},
        "preparation": preparation,
        "usage": {
            "totalExaminedPixels": preparation_cost,
            "preparationPixels": preparation_cost,
            "identityPixels": 0,
            "measuredRegionPixels": 0,
            "examinedCells": 0,
        },
        "blankValueProven": False,
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
    }
    # Identity hashing is included in the same pixel-work budget, not a hidden extra pass.
    identity_cost = width * height + _area(bounds)
    if preparation_cost + identity_cost <= max_pixels:
        record["canvas"] = image_identity(canvas)
        with canvas.crop(bounds) as crop:
            record["tableCrop"]["image"] = image_identity(crop)
        record["identitiesMeasured"] = True
        record["usage"].update(
            totalExaminedPixels=preparation_cost + identity_cost, identityPixels=identity_cost
        )
    unit_map = {(u.get("row"), u.get("col")): (i, u) for i, u in enumerate(units)}
    for cell in cells:

        def translate(box):
            return [box[0] + left, box[1] + t, box[2] + left, box[3] + t]

        slot = {
            "row": cell["row"],
            "col": cell["col"],
            "fullPixelBox": translate(cell["fullPixelBox"]),
            "interiorPixelBox": translate(cell["interiorPixelBox"]),
            "ocrCellWindowPixelBox": translate(cell["unitPixelBox"]),
            "geometryStatus": cell["geometryStatus"],
            "measurementStatus": "unexamined",
            "regions": {},
            "ocrUnit": None,
        }
        slot["slotKey"] = fingerprint(
            {
                "sourceFrameFingerprint": record["sourceFrameFingerprint"],
                "tableCropBounds": bounds,
                "geometryFingerprint": geometry["fingerprint"],
                "row": slot["row"],
                "col": slot["col"],
                "fullPixelBox": slot["fullPixelBox"],
            }
        )
        selected = unit_map.get((cell["row"], cell["col"]))
        if selected and selected[1].get("cellPixelBox") == cell["unitPixelBox"]:
            slot["ocrUnit"] = {
                "unitIndex": selected[0],
                "unitFingerprint": selected[1]["fingerprint"],
            }
        record["slots"].append(slot)
        if cell["geometryStatus"] != "resolved":
            slot["reason"] = "ambiguous_boundary"
            continue
        boxes = {
            "fullCell": slot["fullPixelBox"],
            "interior": slot["interiorPixelBox"],
            "ocrCellWindow": slot["ocrCellWindowPixelBox"],
        }
        groups = {
            "boundaryBands": _bands(boxes["fullCell"], boxes["interior"]),
            "interiorOutsideUnitBands": _bands(boxes["interior"], boxes["ocrCellWindow"]),
        }
        cost = sum(_area(box) for box in boxes.values()) + sum(
            _area(box) for bands in groups.values() for box in bands
        )
        if (
            not record["identitiesMeasured"]
            or record["usage"]["totalExaminedPixels"] + cost > max_pixels
        ):
            slot["reason"] = "pixel_budget_exceeded"
            continue
        slot["regions"] = {key: _region(canvas, box) for key, box in boxes.items()}
        slot["regions"].update(
            {key: [_region(canvas, box) for box in bands] for key, bands in groups.items()}
        )
        slot["measurementStatus"] = "measured"
        record["usage"]["measuredRegionPixels"] += cost
        record["usage"]["totalExaminedPixels"] += cost
        record["usage"]["examinedCells"] += 1
    if (
        cells
        and all(s["measurementStatus"] == "measured" for s in record["slots"])
        and geometry["status"] == "verified_rectangular_grid"
    ):
        record["status"] = "captured"
    else:
        record["reason"] = "cell_geometry_or_measurement_incomplete"
    record["fingerprint"] = fingerprint(record)
    return record


def cell_ocr_links(observations, captures):
    links = []
    for observation in observations:
        for slot in observation.get("slots", []):
            unit = slot.get("ocrUnit")
            if unit is None:
                continue
            for capture in captures:
                if not isinstance(capture.get("pixelFrame"), dict) or not isinstance(
                    capture.get("transform"), dict
                ):
                    continue
                if (
                    capture.get("sourcePass") == "table_repair"
                    and capture.get(
                        "unitFingerprint", capture.get("pixelFrame", {}).get("unitFingerprint")
                    )
                    == unit["unitFingerprint"]
                    and capture.get("transform", {}).get("cellUnitIndex") == unit["unitIndex"]
                    and capture.get("image") is not None
                    and capture.get("pixelFrame", {}).get("tableCropPixelBounds")
                    == observation.get("tableCrop", {}).get("pixelBounds")
                    and capture.get("pixelFrame", {}).get("documentKey")
                    == observation.get("sourceFrame", {}).get("documentKey")
                    and capture.get("pixelFrame", {}).get("localPageNumber")
                    == observation.get("localPageNumber")
                ):
                    links.append(
                        {
                            "observationFingerprint": observation["fingerprint"],
                            "slotKey": slot["slotKey"],
                            **unit,
                            "capturePassId": capture.get("passId"),
                            "runFingerprint": capture.get("runFingerprint"),
                            "tsvInputPageNumber": capture.get("tsvInputPageNumber"),
                            "status": capture.get("status"),
                        }
                    )
    return links


def validate_cell_observation(record, *, mapping, source_sha256, page):
    """Recheck internal statistics/geometry and frame linkage, NOT absent source pixels."""
    result = {
        "status": "unverified",
        "observationFingerprint": record.get("fingerprint"),
        "rawPixelsRecomputed": False,
        "statisticsInternallyConsistent": False,
        "blankValueProven": False,
        "ocrTruthVerified": False,
        "contentCoverageVerified": False,
    }
    try:
        if (
            record["version"] != VERSION
            or record["fingerprint"] != fingerprint(record)
            or record.get("identitiesMeasured") is not True
            or any(
                record.get(k) is not False
                for k in ("blankValueProven", "ocrTruthVerified", "contentCoverageVerified")
            )
        ):
            raise ValueError("cell record identity invalid")
        frame = record["sourceFrame"]
        if (
            frame is None
            or record["sourceFrameFingerprint"] != fingerprint(frame)
            or record["canvas"] != frame["canvas"]
            or frame["requestedCropTopLeft"] is not None
            or mapping["status"] != "verified"
            or mapping["fingerprint"] != fingerprint(mapping)
            or mapping["sourceSha256"] != source_sha256
            or type(page) is not int
            or type(record["localPageNumber"]) is not int
            or type(frame["localPageNumber"]) is not int
            or mapping["originalPageNumber"] != page
            or record["localPageNumber"] != frame["localPageNumber"]
        ):
            raise ValueError("cell source frame invalid")
        matrix = _frame_to_page(frame, mapping)
        if (
            record["canvas"].get("mode") not in {"RGB", "RGBA"}
            or not isinstance(record["canvas"].get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["canvas"]["sha256"])
        ):
            raise ValueError("cell canvas identity invalid")
        width, height = record["canvas"]["size"]
        bounds = _box(record["tableCrop"]["pixelBounds"], width, height)
        left, t, r, b = bounds
        image = record["tableCrop"]["image"]
        if (
            image["size"] != [r - left, b - t]
            or image["mode"] != record["canvas"]["mode"]
            or not re.fullmatch(r"[a-f0-9]{64}", image["sha256"])
        ):
            raise ValueError("cell crop identity invalid")
        limits = record["limits"]
        if (
            type(limits["maxPixels"]) is not int
            or not 0 <= limits["maxPixels"] <= MAX_PIXELS
            or type(limits["maxCells"]) is not int
            or not 0 <= limits["maxCells"] <= MAX_CELLS
        ):
            raise ValueError("cell limits invalid")
        geometry = record["geometry"]
        if geometry["fingerprint"] != fingerprint(geometry):
            raise ValueError("cell geometry changed")
        cells, expected_geometry = resolved_cell_geometry(
            geometry, width=r - left, height=b - t, max_cells=limits["maxCells"]
        )
        if any(geometry.get(k) != v for k, v in expected_geometry.items()) or len(
            record["slots"]
        ) != len(cells):
            raise ValueError("cell plan mismatch")
        counted = measured = 0
        for slot, cell in zip(record["slots"], cells, strict=True):

            def translated(box):
                return [box[0] + left, box[1] + t, box[2] + left, box[3] + t]

            if (
                type(slot["row"]) is not int
                or type(slot["col"]) is not int
                or slot["row"] != cell["row"]
                or slot["col"] != cell["col"]
                or slot["geometryStatus"] != cell["geometryStatus"]
                or slot["fullPixelBox"] != translated(cell["fullPixelBox"])
                or slot["interiorPixelBox"] != translated(cell["interiorPixelBox"])
                or slot["ocrCellWindowPixelBox"] != translated(cell["unitPixelBox"])
            ):
                raise ValueError("cell slot geometry changed")
            if slot["slotKey"] != fingerprint(
                {
                    "sourceFrameFingerprint": record["sourceFrameFingerprint"],
                    "tableCropBounds": bounds,
                    "geometryFingerprint": geometry["fingerprint"],
                    "row": slot["row"],
                    "col": slot["col"],
                    "fullPixelBox": slot["fullPixelBox"],
                }
            ):
                raise ValueError("cell slot identity changed")
            if slot["measurementStatus"] != "measured":
                if slot["regions"]:
                    raise ValueError("unexamined cell has measurements")
                continue
            if cell["geometryStatus"] != "resolved":
                raise ValueError("ambiguous cell measured")
            regions = slot["regions"]
            expected = {
                "fullCell": slot["fullPixelBox"],
                "interior": slot["interiorPixelBox"],
                "ocrCellWindow": slot["ocrCellWindowPixelBox"],
            }
            all_regions = []
            for key, box in expected.items():
                if regions[key]["pixelBox"] != box:
                    raise ValueError("cell measurement window changed")
                all_regions.append(regions[key])
            for key, boxes in (
                ("boundaryBands", _bands(expected["fullCell"], expected["interior"])),
                (
                    "interiorOutsideUnitBands",
                    _bands(expected["interior"], expected["ocrCellWindow"]),
                ),
            ):
                if [v["pixelBox"] for v in regions[key]] != boxes:
                    raise ValueError("cell bands changed")
                all_regions.extend(regions[key])
            for region in all_regions:
                total = _area(_box(region["pixelBox"], width, height))
                counts = [
                    region[k]
                    for k in ("opaqueWhitePixels", "opaqueNonWhitePixels", "unknownAlphaPixels")
                ]
                if (
                    type(region["pixelCount"]) is not int
                    or type(region["nonWhiteRGBPixels"]) is not int
                    or region["pixelCount"] != total
                    or any(type(n) is not int or n < 0 for n in counts)
                    or sum(counts) != total
                    or not counts[1] <= region["nonWhiteRGBPixels"] <= counts[1] + counts[2]
                    or any(
                        not re.fullmatch(r"[a-f0-9]{64}", region[k])
                        for k in ("rgbSha256", "alphaSha256")
                    )
                ):
                    raise ValueError("cell statistics inconsistent")
                status = (
                    "unknown_alpha"
                    if counts[2]
                    else "observed_nonwhite"
                    if counts[1]
                    else "observed_background"
                )
                minimum, maximum = region["minRGB"], region["maxRGB"]
                if (
                    not isinstance(minimum, list)
                    or not isinstance(maximum, list)
                    or len(minimum) != 3
                    or len(maximum) != 3
                    or any(type(v) is not int or not 0 <= v <= 255 for v in minimum + maximum)
                    or any(a > b for a, b in zip(minimum, maximum, strict=True))
                    or (region["nonWhiteRGBPixels"] == 0 and minimum != [255, 255, 255])
                ):
                    raise ValueError("cell channel extrema invalid")
                if region["status"] != status:
                    raise ValueError("cell background claim inconsistent")
                counted += total
            for parent, child, bands in (
                ("fullCell", "interior", "boundaryBands"),
                ("interior", "ocrCellWindow", "interiorOutsideUnitBands"),
            ):
                for key in (
                    "pixelCount",
                    "opaqueWhitePixels",
                    "opaqueNonWhitePixels",
                    "unknownAlphaPixels",
                    "nonWhiteRGBPixels",
                ):
                    if regions[parent][key] != regions[child][key] + sum(
                        v[key] for v in regions[bands]
                    ):
                        raise ValueError("cell region coverage is inconsistent")
            measured += 1
        expected_status = (
            "captured"
            if cells
            and measured == len(cells)
            and geometry["status"] == "verified_rectangular_grid"
            else "partial"
        )
        if record["status"] != expected_status:
            raise ValueError("cell collection status inconsistent")
        identity_cost = width * height + _area(bounds)
        preparation = record["preparation"]
        if (
            set(preparation) != {"frameIdentityPixels", "gridDetectionPixels"}
            or any(type(n) is not int or n < 0 for n in preparation.values())
            or preparation["frameIdentityPixels"] not in {0, width * height}
            or preparation["gridDetectionPixels"] not in {0, _area(bounds)}
        ):
            raise ValueError("cell preparation count invalid")
        preparation_cost = sum(preparation.values())
        usage = record["usage"]
        if (
            usage
            != {
                "totalExaminedPixels": preparation_cost + identity_cost + counted,
                "preparationPixels": preparation_cost,
                "identityPixels": identity_cost,
                "measuredRegionPixels": counted,
                "examinedCells": measured,
            }
            or preparation_cost + identity_cost + counted > limits["maxPixels"]
        ):
            raise ValueError("cell measurement budget inconsistent")
        result.update(
            status="verified",
            statisticsInternallyConsistent=True,
            canvasPixelToOriginalPageAffine=matrix,
        )
    except (
        KeyError,
        ValueError,
        TypeError,
        IndexError,
        OverflowError,
        ZeroDivisionError,
        AttributeError,
    ) as error:
        result["errorType"] = type(error).__name__
    return result
