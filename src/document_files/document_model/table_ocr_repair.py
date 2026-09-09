"""Bounded, source-preserving ruled-table OCR preprocessing (no semantic guesses)."""

from __future__ import annotations

import csv
import io
import math
import os
import subprocess
import tempfile
import time


def parse_tsv(raw: bytes, *, max_rows=200000) -> list[dict]:
    """Keep the text column lexical, including zeros, precision and NA/null words."""
    result = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t"):
        if not row.get("text", "").strip():
            continue
        values = {k: int(row[k]) for k in ("left", "top", "width", "height")}
        confidence = float(row["conf"])
        if not math.isfinite(confidence) or values["width"] <= 0 or values["height"] <= 0:
            continue
        if len(result) >= max_rows:
            raise ValueError("OCR token budget exceeded")
        result.append({**row, **values, "conf": confidence, "text": row["text"]})
    return result


def bounded_tsv(command, *, timeout, max_bytes):
    """Spool child stdout, enforce its byte/time budget, and always reap the child.

    This bounds captured TSV bytes, not framework/Python RSS. File-size sampling
    may permit a small write burst over the limit; no oversized file is read.
    """
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            command, stdout=output, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, shell=False
        )
        try:
            while True:
                if os.fstat(output.fileno()).st_size > max_bytes:
                    raise ValueError("OCR output budget exceeded")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("fixed OCR process", timeout)
                try:
                    process.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, "fixed OCR process")
            if os.fstat(output.fileno()).st_size > max_bytes:
                raise ValueError("OCR output budget exceeded")
            output.seek(0)
            return output.read(max_bytes + 1)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def bounded_tsv_result(command, *, timeout, max_bytes):
    """Retain bounded raw output on failure; callers must not accept partial runs."""
    started = time.monotonic()
    result = {
        "exitCode": None,
        "timedOut": False,
        "status": "failed",
        "outputTruncated": False,
        "processStarted": False,
    }
    with tempfile.TemporaryFile() as output:
        process = None
        try:
            process = subprocess.Popen(
                command,
                stdout=output,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
            result["processStarted"] = True
            while True:
                if os.fstat(output.fileno()).st_size > max_bytes:
                    raise ValueError("OCR output budget exceeded")
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("fixed OCR batch process", timeout)
                try:
                    process.wait(timeout=min(0.05, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, "fixed OCR batch process")
            if os.fstat(output.fileno()).st_size > max_bytes:
                raise ValueError("OCR output budget exceeded")
            result["status"] = "returned"
        except BaseException as error:
            result["error"] = error
            result["errorType"] = type(error).__name__
            result["timedOut"] = isinstance(error, subprocess.TimeoutExpired)
            result["status"] = (
                "timed_out"
                if result["timedOut"]
                else "failed"
                if isinstance(error, Exception)
                else "cancelled"
            )
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                result["exitCode"] = process.returncode
            result["outputTruncated"] = os.fstat(output.fileno()).st_size > max_bytes
            output.seek(0)
            result["raw"] = output.read(max_bytes)
            result["elapsedSeconds"] = time.monotonic() - started
    return result


def remove_grid(image, *, max_pixels: int):
    """Return a separate image/mask only with a verified closed axis-aligned grid.

    The image is an already detected TABLE crop. No values or cell positions are
    supplied. Original glyphs touching grid strokes can be damaged: retain inputs.
    """
    import cv2
    import numpy as np
    from PIL import Image

    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if width * height > max_pixels or min(width, height) < 12:
        return None, {"status": "pixel_budget_or_small_region"}
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, max(3, width // 3)), np.uint8))
    vertical = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((max(3, height // 3), 1), np.uint8))

    def lines(mask, axis):
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        # Reject thick text blocks: selected objects must be thin, long strokes.
        return [
            tuple(map(int, s))
            for s in stats[1:n]
            if s[axis + 2] >= (width if axis == 0 else height) / 3
            and s[3 - axis] <= max(8, min(width, height) * 0.03)
        ]

    hs, vs = lines(horizontal, 0), lines(vertical, 1)
    if len(hs) > 128 or len(vs) > 128:
        return None, {"status": "grid_geometry_budget_exceeded"}

    def selected(mask, strokes):
        kept = np.zeros_like(mask)
        for x, y, w, h, _ in strokes:
            kept[y : y + h, x : x + w] = mask[y : y + h, x : x + w]
        return kept

    horizontal, vertical = selected(horizontal, hs), selected(vertical, vs)
    intersections = cv2.bitwise_and(horizontal, vertical)
    n, _, _, _ = cv2.connectedComponentsWithStats(intersections, 8)
    # A closed rectangle needs two crossing lines each way, not merely a cross.
    closed = False
    for h1 in hs:
        for h2 in hs:
            if h2[1] <= h1[1] + h1[3]:
                continue
            crossings = [
                v
                for v in vs
                if v[1] <= h1[1] + h1[3]
                and v[1] + v[3] >= h2[1]
                and h1[0] <= v[0] <= h1[0] + h1[2]
                and h2[0] <= v[0] <= h2[0] + h2[2]
            ]
            if len(crossings) >= 2:
                closed = True
                break
        if closed:
            break
    if not closed or n - 1 < 4:
        return None, {"status": "closed_grid_not_observed"}
    mask = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8))
    derived = rgb.copy()
    derived[mask != 0] = 255
    # Encode the mask as rectangles/runs, not an opaque framework object.
    runs = []
    for y, row in enumerate(mask != 0):
        changes = np.flatnonzero(np.diff(np.r_[False, row, False]))
        runs.extend([y, int(a), int(b)] for a, b in zip(changes[::2], changes[1::2], strict=True))
    return Image.fromarray(derived), {
        "status": "grid_removed",
        "horizontalLines": hs,
        "verticalLines": vs,
        "intersectionCount": n - 1,
        "maskRuns": runs,
        "size": [width, height],
        "algorithm": "ruled_tables_v1",
        "originalUnchanged": True,
    }


def box_overlap(a, b):
    """Minimum-area overlap for conservative conflicting token detection."""
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    if area <= 0:
        return 0.0
    return (
        max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    ) / area


def resolved_cell_geometry(grid, *, width, height, max_cells=4096):
    """Separate closed geometric windows from OCR and from non-text classification."""
    hs, vs = grid.get("horizontalLines", []), grid.get("verticalLines", [])
    if not isinstance(hs, list) or not isinstance(vs, list) or len(hs) > 128 or len(vs) > 128:
        return [], {"status": "cell_geometry_budget_or_missing_grid"}
    for line in [*hs, *vs]:
        if (
            not isinstance(line, (list, tuple))
            or len(line) < 4
            or any(type(v) is not int for v in line[:4])
        ):
            return [], {"status": "cell_geometry_invalid"}
        x, y, w, h = line[:4]
        if not (0 <= x < x + w <= width and 0 <= y < y + h <= height):
            return [], {"status": "cell_geometry_invalid"}
    ys = sorted({(int(h[1]), int(h[1] + h[3])) for h in hs})
    xs = sorted({(int(v[0]), int(v[0] + v[2])) for v in vs})
    count = max(0, len(xs) - 1) * max(0, len(ys) - 1)
    if len(xs) < 2 or len(ys) < 2 or count > max_cells:
        return [], {"status": "cell_geometry_budget_or_missing_grid", "candidateCellCount": count}
    cells = []
    for row in range(len(ys) - 1):
        for col in range(len(xs) - 1):
            top, bottom = ys[row], ys[row + 1]
            left, right = xs[col], xs[col + 1]
            horizontal_ok = all(
                any(
                    abs(h[1] - edge[0]) <= 1 and h[0] <= left[0] + 2 and h[0] + h[2] >= right[1] - 2
                    for h in hs
                )
                for edge in (top, bottom)
            )
            vertical_ok = all(
                any(
                    abs(v[0] - edge[0]) <= 1 and v[1] <= top[0] + 2 and v[1] + v[3] >= bottom[1] - 2
                    for v in vs
                )
                for edge in (left, right)
            )
            interior = [left[1], top[1], right[0], bottom[0]]
            unit = [left[1] + 2, top[1] + 2, right[0] - 2, bottom[0] - 2]
            resolved = horizontal_ok and vertical_ok and unit[0] < unit[2] and unit[1] < unit[3]
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "fullPixelBox": [left[0], top[0], right[1], bottom[1]],
                    "interiorPixelBox": interior,
                    "unitPixelBox": unit,
                    "geometryStatus": "resolved" if resolved else "ambiguous_boundary",
                }
            )
    status = (
        "verified_rectangular_grid"
        if all(c["geometryStatus"] == "resolved" for c in cells)
        else "merged_or_incomplete_grid_unsupported"
    )
    return cells, {"status": status, "rows": len(ys) - 1, "cols": len(xs) - 1}


def cell_ocr_units(image, grid, *, max_cells=4096):
    """Use only complete rectangular grids; never guess missing merged-cell edges.

    Each unit keeps original glyph pixels, crops excess background and adds a
    small white border. Geometry determines PSM before any OCR result is seen.
    """
    import hashlib
    import json

    import cv2
    import numpy as np
    from PIL import ImageOps

    hs, vs = grid.get("horizontalLines", []), grid.get("verticalLines", [])
    # Merge only parallel strokes at the same coordinate; retain their coverage.
    ys = sorted({(int(h[1]), int(h[1] + h[3])) for h in hs})
    xs = sorted({(int(v[0]), int(v[0] + v[2])) for v in vs})
    if len(xs) < 2 or len(ys) < 2 or (len(xs) - 1) * (len(ys) - 1) > max_cells:
        return [], {"status": "cell_geometry_budget_or_missing_grid"}
    rectangles = []
    for row in range(len(ys) - 1):
        for col in range(len(xs) - 1):
            xlo, xhi = xs[col][0], xs[col + 1][1]
            ylo, yhi = ys[row][0], ys[row + 1][1]
            top, bottom = ys[row], ys[row + 1]
            left, right = xs[col], xs[col + 1]
            horizontal_ok = all(
                any(
                    abs(h[1] - edge[0]) <= 1 and h[0] <= xlo + 2 and h[0] + h[2] >= xhi - 2
                    for h in hs
                )
                for edge in (top, bottom)
            )
            vertical_ok = all(
                any(
                    abs(v[0] - edge[0]) <= 1 and v[1] <= ylo + 2 and v[1] + v[3] >= yhi - 2
                    for v in vs
                )
                for edge in (left, right)
            )
            if not horizontal_ok or not vertical_ok:
                return [], {"status": "merged_or_incomplete_grid_unsupported"}
            rectangles.append((row, col, (left[1] + 2, top[1] + 2, right[0] - 2, bottom[0] - 2)))
    units = []
    for row, col, bounds in rectangles:
        left, top, right, bottom = bounds
        info = {
            "row": row,
            "col": col,
            "cellPixelBox": list(bounds),
            "processing": "ruled_cells_v2",
        }
        if right <= left or bottom <= top:
            return [], {"status": "cell_geometry_invalid"}
        original = image.crop(bounds)
        gray = np.asarray(original.convert("L"))
        ink = gray < 180
        info["inkPixels"] = int(ink.sum())
        if not ink.any():
            info.update(status="no_ink_observed", blankValueProven=False)
            processed = None
        elif ink[:2, :].any() or ink[-2:, :].any() or ink[:, :2].any() or ink[:, -2:].any():
            info.update(status="glyph_boundary_contact_unresolved")
            processed = None
        else:
            rows, cols = np.where(ink)
            box = [int(cols.min()), int(rows.min()), int(cols.max() + 1), int(rows.max() + 1)]
            band = (ink.any(axis=1)).astype(np.uint8)
            n, _, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), 8)
            heights = stats[1:n, 3]
            gap = max(2, round(float(np.median(heights)) * 0.3)) if len(heights) else 2
            closed = cv2.morphologyEx(band[:, None], cv2.MORPH_CLOSE, np.ones((gap, 1), np.uint8))[
                :, 0
            ]
            lines = int(np.count_nonzero(np.diff(np.r_[0, closed, 0].astype(int)) == 1))
            psm = 7 if lines == 1 else 6
            processed = ImageOps.expand(original.crop(box), border=10, fill="white")
            info.update(
                status="ready",
                inkCrop=box,
                padding=10,
                psm=psm,
                lineCount=lines,
                pixelOffset=[left + box[0] - 10, top + box[1] - 10],
                glyphPixelsChanged=False,
            )
        info["sourcePixelsSha256"] = hashlib.sha256(original.tobytes()).hexdigest()
        info["fingerprint"] = hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()
        units.append((processed, info))
    return units, {"status": "verified_rectangular_grid", "rows": len(ys) - 1, "cols": len(xs) - 1}


def ruling_line_evidence(image, grid, pixel_box):
    """Classify only ink wholly covered by observed long grid strokes, not text."""
    import math

    import numpy as np

    if grid.get("status") != "grid_removed":
        return None
    left, top, right, bottom = pixel_box
    if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
        return None
    x0, y0, x1, y1 = math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom)
    ink = np.asarray(image.crop((x0, y0, x1, y1)).convert("L")) < 180
    count = int(ink.sum())
    if not count:
        return None
    mask = np.zeros_like(ink)
    for y, start, end in grid.get("maskRuns", []):
        if y0 <= y < y1:
            mask[y - y0, max(0, start - x0) : max(0, min(x1, end) - x0)] = True
    if (ink & ~mask).any():
        return None
    supporting = []
    for axis, lines in (
        ("horizontal", grid["horizontalLines"]),
        ("vertical", grid["verticalLines"]),
    ):
        for line in lines:
            x, y, width, height, _ = line
            if axis == "vertical" and y <= y0 and y + height >= y1 and x0 <= x < x1:
                supporting.append({"axis": axis, "stroke": list(line)})
            if axis == "horizontal" and x <= x0 and x + width >= x1 and y0 <= y < y1:
                supporting.append({"axis": axis, "stroke": list(line)})
    if not supporting:
        return None
    return {
        "basis": "all_observed_ink_within_closed_grid_long_stroke_mask",
        "pixelBox": [x0, y0, x1, y1],
        "inkPixels": count,
        "nonRulingInkPixels": 0,
        "supportingStrokes": supporting,
        "conflictStatus": "unresolved_ocr_text_vs_ruling_geometry",
        "originalTextPreserved": True,
    }


def geometric_structure_order(records, repairs):
    """Order a structure-only view within proven grid cells; keep source records."""
    result = list(range(len(records)))
    for repair in repairs:
        if repair.get("policy") != "ruled_cells_v2" or not repair.get("units"):
            continue
        transform = repair["transform"]
        ox, oy = transform["cropPixelOrigin"]
        sx, sy = transform["scaleX"], transform["scaleY"]
        groups = []
        for unit in repair["units"]:
            x0, y0, x1, y1 = unit["cellPixelBox"]
            matched = []
            for index in result:
                b = records[index]["bbox"]
                if b.get("coord_origin") != "TOPLEFT":
                    continue
                # Entire token must belong to one cell; crossing boxes are not guessed.
                if (ox + x0) / sx <= b["l"] < b["r"] <= (ox + x1) / sx and (oy + y0) / sy <= b[
                    "t"
                ] < b["b"] <= (oy + y1) / sy:
                    matched.append(index)
            lines = []
            for index in sorted(matched, key=lambda i: records[i]["bbox"]["t"]):
                b = records[index]["bbox"]
                line = next(
                    (
                        line
                        for line in lines
                        if min(line[1], b["b"]) - max(line[0], b["t"])
                        >= 0.5 * min(line[1] - line[0], b["b"] - b["t"])
                    ),
                    None,
                )
                if line is None:
                    lines.append([b["t"], b["b"], [index]])
                else:
                    line[2].append(index)
            groups.extend(
                i for line in lines for i in sorted(line[2], key=lambda i: records[i]["bbox"]["l"])
            )
        positions = sorted(result.index(i) for i in groups)
        for position, index in zip(positions, groups, strict=True):
            result[position] = index
    return result
