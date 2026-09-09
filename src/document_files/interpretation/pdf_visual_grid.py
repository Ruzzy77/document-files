"""Measured straight-band candidates from exact pixels, never semantic rulings."""

from __future__ import annotations

import hashlib
import json
import math
import time
from bisect import bisect_left
from copy import deepcopy

VERSION = "document-files.pdf-visual-grid.v1"
SEARCH_RADIUS = 8
MAX_BAND_WIDTH = 7
MIN_LINE_LENGTH = 32
MAX_COMPARISONS = 1048576
MAX_RUNS = 65536
MAX_GRIDS = 16
MAX_LINES = 128
MAX_SLOTS = 128


class VisualGridError(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _require(value, code="visual_grid_input_invalid"):
    if not value:
        raise VisualGridError(code)


def _hash(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _segments(values):
    result = []
    for value in values:
        if result and result[-1][1] == value:
            result[-1][1] += 1
        else:
            result.append([value, value + 1])
    return result


def _merge(intervals):
    output = []
    for start, end in sorted(intervals):
        if output and start <= output[-1][1]:
            output[-1][1] = max(output[-1][1], end)
        else:
            output.append([start, end])
    return output


def measure_grid_candidates(pixels, *, expected_grids, deadline, cancelled=None):
    """Measure bounded continuous stripes near independently supplied grid centers.

    Expected coordinates are full-render pixel coordinates, not OCR canvas bounds.
    Rectangles propose search areas only. Returned bands carry their exact pixel
    runs. Their semantic classification and every slot's blank/reading status stay
    unverified, even for a geometrically continuous band. Short strokes, gaps,
    over-wide/split stripes, or nearby ink outside crossing bands stay unresolved.
    """
    started, comparisons = time.monotonic(), 0
    _require(
        type(deadline) in (int, float)
        and math.isfinite(deadline)
        and (cancelled is None or callable(cancelled)),
        "visual_grid_configuration_invalid",
    )

    def spend(count=1):
        nonlocal comparisons
        comparisons += count
        _require(comparisons <= MAX_COMPARISONS, "visual_grid_comparison_budget")
        _require(not (cancelled and cancelled()), "visual_grid_cancelled")
        _require(time.monotonic() < deadline, "visual_grid_timeout")

    spend()
    try:
        _require(isinstance(pixels, dict) and isinstance(expected_grids, list))
        _require(len(expected_grids) <= MAX_GRIDS)
        # Inputs are copied before any caller-visible callback can run again.
        pixels, requested = deepcopy(pixels), deepcopy(expected_grids)
        _require(pixels.get("version") == "document-files.pdf-visual-pixels.v1")
        _require(_hash(pixels.get("rgbSha256")) and _hash(pixels.get("fingerprint")))
        _require(
            pixels["fingerprint"]
            == _digest(
                {k: v for k, v in pixels.items() if k not in {"fingerprint", "diagnostics"}}
            ),
            "visual_grid_pixels_changed",
        )
        width, height = pixels["pixelSize"]
        _require(
            all(type(v) is int and v > 0 for v in (width, height)) and width * height <= 16000000
        )
        components = pixels["components"]
        _require(isinstance(components, list) and len(components) <= 4096)
        runs = []
        for component in components:
            spend()
            _require(isinstance(component, dict) and isinstance(component.get("runs"), list))
            _require(len(runs) + len(component["runs"]) <= MAX_RUNS, "visual_grid_run_budget")
            runs.extend(component["runs"])
        for run in runs:
            spend()
            _require(isinstance(run, list) and len(run) == 3 and all(type(v) is int for v in run))
            y, start, end = run
            _require(0 <= y < height and 0 <= start < end <= width)
        runs.sort()
        rows = {}
        for y, start, end in runs:
            current = rows.setdefault(y, [])
            _require(not current or current[-1][1] <= start, "visual_grid_overlapping_membership")
            current.append([start, end])
        count = sum(end - start for _, start, end in runs)
        _require(count == pixels["foregroundPixelCount"])
        row_numbers = sorted(rows)

        def intersect(box):
            left, top, right, bottom = box
            selected = []
            for y in row_numbers[bisect_left(row_numbers, top) : bisect_left(row_numbers, bottom)]:
                for x, end in rows[y]:
                    spend()
                    if x < right and left < end:
                        selected.append([y, max(x, left), min(end, right)])
            return selected

        all_bands, grids = [], []
        table_refs, source_hashes = set(), set()
        for grid in requested:
            spend()
            _require(
                isinstance(grid, dict)
                and set(grid)
                == {
                    "tableRef",
                    "sourceSha256",
                    "rgbSha256",
                    "observationFingerprint",
                    "horizontalLines",
                    "verticalLines",
                    "missingSlots",
                }
            )
            table_ref = grid["tableRef"]
            _require(
                isinstance(table_ref, str)
                and 0 < len(table_ref) <= 512
                and table_ref not in table_refs
            )
            table_refs.add(table_ref)
            _require(
                _hash(grid["sourceSha256"])
                and _hash(grid["observationFingerprint"])
                and grid["rgbSha256"] == pixels["rgbSha256"],
                "visual_grid_source_changed",
            )
            source_hashes.add(grid["sourceSha256"])
            _require(len(source_hashes) == 1, "visual_grid_source_changed")
            xs, ys = grid["verticalLines"], grid["horizontalLines"]
            for coordinates, extent in ((xs, width), (ys, height)):
                _require(isinstance(coordinates, list) and 2 <= len(coordinates) <= MAX_LINES)
                _require(
                    all(
                        type(v) in (int, float) and math.isfinite(v) and 0 <= v < extent
                        for v in coordinates
                    )
                )
                _require(
                    all(
                        right - left > 2 * SEARCH_RADIUS
                        for left, right in zip(coordinates, coordinates[1:], strict=False)
                    ),
                    "visual_grid_ambiguous_expected_geometry",
                )
            _require(len(xs) + len(ys) <= MAX_LINES)
            slots = grid["missingSlots"]
            _require(isinstance(slots, list) and len(slots) <= MAX_SLOTS)
            slot_keys, slot_positions = set(), set()
            for slot in slots:
                _require(isinstance(slot, dict) and set(slot) == {"row", "col", "slotKey"})
                row, col, key = slot["row"], slot["col"], slot["slotKey"]
                _require(
                    type(row) is int
                    and type(col) is int
                    and 0 <= row < len(ys) - 1
                    and 0 <= col < len(xs) - 1
                )
                _require(
                    isinstance(key, str)
                    and 0 < len(key) <= 512
                    and key not in slot_keys
                    and (row, col) not in slot_positions
                )
                slot_keys.add(key)
                slot_positions.add((row, col))
            bands = []
            for axis, positions, ends, extent in (
                ("horizontal", ys, xs, height),
                ("vertical", xs, ys, width),
            ):
                for index, center in enumerate(positions):
                    spend()
                    low, high = (
                        max(0, math.floor(center - SEARCH_RADIUS)),
                        min(extent, math.ceil(center + SEARCH_RADIUS)),
                    )
                    core_start, core_end = (
                        math.ceil(ends[0] + SEARCH_RADIUS),
                        math.floor(ends[-1] - SEARCH_RADIUS),
                    )
                    band = {
                        "id": f"{axis[0]}{index}",
                        "axis": axis,
                        "index": index,
                        "status": "unresolved",
                        "bounds": None,
                        "runs": [],
                        "pixelCount": 0,
                        "reasons": [],
                        "semanticClassificationVerified": False,
                    }
                    bands.append(band)
                    if core_end - core_start < MIN_LINE_LENGTH:
                        band["reasons"] = ["insufficient_continuous_length"]
                        continue
                    continuous = []
                    intervals_by_cross = {}
                    if axis == "horizontal":
                        for cross in range(low, high):
                            intervals_by_cross[cross] = rows.get(cross, [])
                    else:
                        for cross in range(low, high):
                            intervals_by_cross[cross] = []
                        for y in row_numbers:
                            for left, right in rows[y]:
                                spend()
                                for cross in range(max(low, left), min(high, right)):
                                    spend()
                                    intervals_by_cross[cross].append(y)
                        intervals_by_cross = {
                            cross: _segments(values) for cross, values in intervals_by_cross.items()
                        }
                    for cross, intervals in intervals_by_cross.items():
                        for left, right in intervals:
                            spend()
                            if left <= core_start and right >= core_end:
                                continuous.append(cross)
                                break
                    stripes = _segments(continuous)
                    if len(stripes) != 1:
                        band["reasons"] = ["broken_or_ambiguous_stripe"]
                        continue
                    cross_start, cross_end = stripes[0]
                    if cross_end - cross_start > MAX_BAND_WIDTH:
                        band["reasons"] = ["stripe_width_exceeded"]
                        continue
                    longitudinal = []
                    for cross in range(cross_start, cross_end):
                        for left, right in intervals_by_cross[cross]:
                            if left <= core_start and right >= core_end:
                                longitudinal.append([left, right])
                    start = min(item[0] for item in longitudinal)
                    end = max(item[1] for item in longitudinal)
                    if abs(start - ends[0]) > SEARCH_RADIUS or abs(end - ends[-1]) > SEARCH_RADIUS:
                        band["reasons"] = ["endpoint_protrusion_or_truncation"]
                        continue
                    bounds = (
                        [start, cross_start, end, cross_end]
                        if axis == "horizontal"
                        else [cross_start, start, cross_end, end]
                    )
                    exact = intersect(bounds)
                    band.update(
                        status="candidate",
                        bounds=bounds,
                        runs=exact,
                        pixelCount=sum(r - x for _, x, r in exact),
                    )
            # A stable continuous stripe does not authorize nearby dots or glyphs.
            # At intersections only pixels actually inside measured perpendicular
            # stripes are excepted. All remaining search-halo ink blocks this band.
            provisional = [deepcopy(b) for b in bands if b["status"] == "candidate"]
            for band in bands:
                if band["status"] != "candidate":
                    continue
                perpendicular = "vertical" if band["axis"] == "horizontal" else "horizontal"
                endpoint_index = 0 if band["axis"] == "horizontal" else 1
                last_index = len(xs) - 1 if perpendicular == "vertical" else len(ys) - 1
                endpoint_bands = {
                    b["index"]: b
                    for b in provisional
                    if b["axis"] == perpendicular and b["index"] in (0, last_index)
                }
                if len(endpoint_bands) != 2:
                    band.update(
                        status="unresolved",
                        reasons=["endpoint_support_unresolved"],
                        bounds=None,
                        runs=[],
                        pixelCount=0,
                    )
                    continue
                # Search tolerance locates an expected stripe; it is not license
                # to swallow short protrusions beyond the actual outer cross-stripes.
                if (
                    band["bounds"][endpoint_index] < endpoint_bands[0]["bounds"][endpoint_index]
                    or band["bounds"][endpoint_index + 2]
                    > endpoint_bands[last_index]["bounds"][endpoint_index + 2]
                ):
                    band.update(
                        status="unresolved",
                        reasons=["endpoint_outside_cross_stripes"],
                        bounds=None,
                        runs=[],
                        pixelCount=0,
                    )
                    continue
                axis, center = (
                    band["axis"],
                    (ys if band["axis"] == "horizontal" else xs)[band["index"]],
                )
                bounds = band["bounds"]
                halo = (
                    [
                        bounds[0],
                        max(0, math.floor(center - SEARCH_RADIUS)),
                        bounds[2],
                        min(height, math.ceil(center + SEARCH_RADIUS)),
                    ]
                    if axis == "horizontal"
                    else [
                        max(0, math.floor(center - SEARCH_RADIUS)),
                        bounds[1],
                        min(width, math.ceil(center + SEARCH_RADIUS)),
                        bounds[3],
                    ]
                )
                accepted = [band] + [b for b in provisional if b["axis"] != axis]
                for y, start, end in intersect(halo):
                    allowed = []
                    for other in accepted:
                        spend()
                        x, top, right, bottom = other["bounds"]
                        if top <= y < bottom and x < end and start < right:
                            allowed.append([max(start, x), min(end, right)])
                    if sum(right - left for left, right in _merge(allowed)) != end - start:
                        band.update(
                            status="unresolved",
                            reasons=["nearby_ink_or_incomplete_stripe"],
                            bounds=None,
                            runs=[],
                            pixelCount=0,
                        )
                        break
            measured_slots = []
            lookup = {(b["axis"], b["index"]): b for b in bands}
            for slot in slots:
                row, col = slot["row"], slot["col"]
                surrounding = [
                    lookup[("vertical", col)],
                    lookup[("horizontal", row)],
                    lookup[("vertical", col + 1)],
                    lookup[("horizontal", row + 1)],
                ]
                item = {
                    **slot,
                    "status": "unresolved",
                    "fullBounds": None,
                    "interiorBounds": None,
                    "bandIds": [b["id"] for b in surrounding],
                    "slotAssociationVerified": False,
                    "blankValueProven": False,
                }
                if all(b["status"] == "candidate" for b in surrounding):
                    left, top, right, bottom = [b["bounds"] for b in surrounding]
                    full = [left[0], top[1], right[2], bottom[3]]
                    interior = [left[2], top[3], right[0], bottom[1]]
                    if interior[0] < interior[2] and interior[1] < interior[3]:
                        item.update(status="candidate", fullBounds=full, interiorBounds=interior)
                measured_slots.append(item)
            for band in bands:
                band["runsSha256"] = _digest(band["runs"])
                band["fingerprint"] = _digest(band)
            grids.append(
                {
                    "tableRef": table_ref,
                    "sourceSha256": grid["sourceSha256"],
                    "observationFingerprint": grid["observationFingerprint"],
                    "expectedGeometrySha256": _digest(grid),
                    "bands": bands,
                    "slots": measured_slots,
                }
            )
            all_bands.extend(b for b in bands if b["status"] == "candidate")
        claimed_rows = {}
        for band in all_bands:
            for y, start, end in band["runs"]:
                spend()
                claimed_rows.setdefault(y, []).append([start, end])
        claimed_rows = {y: _merge(values) for y, values in claimed_rows.items()}
        assigned, unassigned, assigned_count = [], [], 0
        for y, start, end in runs:
            cursor = start
            for left, right in claimed_rows.get(y, []):
                spend()
                if left < end and start < right:
                    left, right = max(left, start), min(right, end)
                    if cursor < left:
                        unassigned.append([y, cursor, left])
                    assigned.append([y, left, right])
                    assigned_count += right - left
                    cursor = right
            if cursor < end:
                unassigned.append([y, cursor, end])
            _require(len(assigned) + len(unassigned) <= MAX_RUNS, "visual_grid_output_run_budget")
        _require(
            assigned_count + sum(r - x for _, x, r in unassigned) == count,
            "visual_grid_membership_inconsistent",
        )
        result = {
            "version": VERSION,
            "rgbSha256": pixels["rgbSha256"],
            "pixelSize": [width, height],
            "pixelInventoryFingerprint": pixels["fingerprint"],
            "foregroundMaskSha256": pixels["foregroundMaskSha256"],
            "grids": grids,
            "candidateRuns": assigned,
            "unassignedRuns": unassigned,
            "coverage": {
                "foregroundPixelCount": count,
                "candidatePixelCount": assigned_count,
                "unassignedPixelCount": count - assigned_count,
                "discardedPixels": 0,
                "contentCompletenessVerified": False,
                "blankValueProven": False,
                "semanticClassificationVerified": False,
                "visualReviewRequired": True,
            },
            "limits": {
                "searchRadius": SEARCH_RADIUS,
                "maxBandWidth": MAX_BAND_WIDTH,
                "minLineLength": MIN_LINE_LENGTH,
                "comparisons": MAX_COMPARISONS,
                "inputAndOutputRuns": MAX_RUNS,
            },
        }
        result["fingerprint"] = _digest(result)
        spend()
        result["diagnostics"] = {
            "comparisons": comparisons,
            "elapsedSeconds": time.monotonic() - started,
        }
        return result
    except VisualGridError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RuntimeError):
        raise VisualGridError("visual_grid_input_invalid") from None
