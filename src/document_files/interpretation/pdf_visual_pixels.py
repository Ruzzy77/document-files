"""Exact RGB foreground membership; no text, blank or content interpretation."""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import time
import zlib
from collections.abc import Callable

VERSION = "document-files.pdf-visual-pixels.v1"
MAX_IMAGE_BYTES = 16 * 1024**2
MAX_PIXELS = 16000000
MAX_RUNS = 65536
MAX_COMPONENTS = 4096


class VisualPixelError(ValueError):
    """A fixed error code, never input bytes or decoder exception text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require(condition, code="visual_pixels_input_invalid"):
    if not condition:
        raise VisualPixelError(code)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _png_size(data, check):
    """Accept the exact opaque RGB PNG encoding used by the review-image helper."""
    _require(data.startswith(b"\x89PNG\r\n\x1a\n"))
    position, chunks, size, ended, idat = 8, 0, None, False, False
    while position < len(data):
        check()
        chunks += 1
        _require(chunks <= 65536 and position + 12 <= len(data))
        length = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        _require(end <= len(data) and not ended)
        payload = memoryview(data)[position + 8 : end - 4]
        crc = zlib.crc32(payload, zlib.crc32(kind))
        _require(crc == struct.unpack_from(">I", data, end - 4)[0])
        if kind == b"IHDR":
            _require(chunks == 1 and length == 13)
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            _require(depth == 8 and color == 2 and compression == filtering == interlace == 0)
            size = (width, height)
        elif kind == b"IDAT":
            _require(size is not None)
            idat = True
        elif kind == b"IEND":
            _require(size is not None and idat and length == 0 and end == len(data))
            ended = True
        else:
            # No alpha/transparency, APNG, EXIF, color profiles or unknown chunks.
            raise VisualPixelError("visual_pixels_png_profile_unsupported")
        position = end
    _require(ended and size is not None)
    return size


def extract_visual_pixels(
    png_bytes: bytes,
    *,
    expected_rgb_sha256: str,
    expected_size: tuple[int, int] | list[int],
    deadline: float,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Reconstruct all 8-neighbor components of exactly non-white RGB pixels.

    Runs and bounds use half-open pixel edges. A component may surround another
    component: only its explicit runs belong to it, never every pixel in its box.
    The mask digest hashes row-major bytes (0=white, 1=non-white). Bounds, counts
    and geometry do not classify text, rules, noise, empty values or completeness.
    Deadline checks surround native decoding and each Python row; a supervisor
    remains responsible for hard preemption of the native Pillow decoder.
    """
    started = time.monotonic()
    _require(
        type(deadline) in (int, float)
        and math.isfinite(deadline)
        and (cancelled is None or callable(cancelled)),
        "visual_pixels_configuration_invalid",
    )

    def check():
        if cancelled is not None and cancelled():
            raise VisualPixelError("visual_pixels_cancelled")
        if time.monotonic() >= deadline:
            raise VisualPixelError("visual_pixels_timeout")

    check()
    _require(type(png_bytes) is bytes)
    _require(len(png_bytes) <= MAX_IMAGE_BYTES, "visual_pixels_byte_budget_exceeded")
    _require(
        isinstance(expected_rgb_sha256, str)
        and len(expected_rgb_sha256) == 64
        and all(c in "0123456789abcdef" for c in expected_rgb_sha256)
        and isinstance(expected_size, (list, tuple))
        and len(expected_size) == 2
        and all(type(v) is int and v > 0 for v in expected_size)
    )
    size = tuple(expected_size)
    _require(size[0] * size[1] <= MAX_PIXELS, "visual_pixels_pixel_budget_exceeded")
    try:
        from PIL import Image

        _require(_png_size(png_bytes, check) == size, "visual_pixels_size_mismatch")
        check()
        with Image.open(io.BytesIO(png_bytes)) as image:
            _require(image.format == "PNG" and image.mode == "RGB" and image.size == size)
            _require(getattr(image, "n_frames", 1) == 1)
            image.load()
            check()
            raw = image.tobytes()
        check()
        _require(_sha(raw) == expected_rgb_sha256, "visual_pixels_rgb_hash_mismatch")
        width, height = size
        parents, ranks, runs, low_counts = [], [], [], []
        previous = []
        foreground_hash, low_hash = hashlib.sha256(), hashlib.sha256()
        total, low_total = 0, 0

        def find(label):
            while parents[label] != label:
                parents[label] = parents[parents[label]]
                label = parents[label]
            return label

        def union(left, right):
            left, right = find(left), find(right)
            if left == right:
                return
            if ranks[left] < ranks[right]:
                left, right = right, left
            parents[right] = left
            if ranks[left] == ranks[right]:
                ranks[left] += 1

        for y in range(height):
            check()
            row = raw[y * width * 3 : (y + 1) * width * 3]
            foreground, low_mask = bytearray(width), bytearray(width)
            current, start, low_count = [], None, 0
            for x, (red, green, blue) in enumerate(
                zip(row[0::3], row[1::3], row[2::3], strict=True)
            ):
                if x % 4096 == 0:
                    check()
                darkest = min(red, green, blue)
                if darkest != 255:
                    foreground[x] = 1
                    low = darkest >= 224
                    low_mask[x] = low
                    low_count += low
                    if start is None:
                        start = x
                if start is not None and (darkest == 255 or x == width - 1):
                    end = x if darkest == 255 else x + 1
                    _require(len(runs) < MAX_RUNS, "visual_pixels_run_budget_exceeded")
                    label = len(runs)
                    runs.append([y, start, end])
                    parents.append(label)
                    ranks.append(0)
                    low_counts.append(low_count)
                    current.append(label)
                    total += end - start
                    low_total += low_count
                    start, low_count = None, 0
            foreground_hash.update(foreground)
            low_hash.update(low_mask)
            left = 0
            for label in current:
                _, x0, x1 = runs[label]
                while left < len(previous) and runs[previous[left]][2] < x0:
                    left += 1
                position = left
                while position < len(previous) and runs[previous[position]][1] <= x1:
                    union(label, previous[position])
                    position += 1
            previous = current
        check()
        groups = {}
        for label, run in enumerate(runs):
            root = find(label)
            if root not in groups:
                _require(len(groups) < MAX_COMPONENTS, "visual_pixels_component_budget_exceeded")
                groups[root] = {"runs": [], "pixelCount": 0, "lowContrastPixelCount": 0}
            group = groups[root]
            group["runs"].append(run)
            group["pixelCount"] += run[2] - run[1]
            group["lowContrastPixelCount"] += low_counts[label]
        components, ids = [], set()
        for group in groups.values():
            check()
            selected = group["runs"]  # Already row-major from the input scan.
            group["bounds"] = [
                min(r[1] for r in selected),
                selected[0][0],
                max(r[2] for r in selected),
                selected[-1][0] + 1,
            ]
            group["runsSha256"] = _digest(selected)
            group["id"] = "px-" + _digest([expected_rgb_sha256, group["runsSha256"]])[:24]
            _require(group["id"] not in ids, "visual_pixels_identity_collision")
            ids.add(group["id"])
            group["fingerprint"] = _digest(group)
            components.append(group)
        components.sort(key=lambda item: item["runs"][0])
        check()
        _require(
            sum(c["pixelCount"] for c in components) == total
            and sum(c["lowContrastPixelCount"] for c in components) == low_total
            and sum(len(c["runs"]) for c in components) == len(runs),
            "visual_pixels_membership_inconsistent",
        )
        result = {
            "version": VERSION,
            "pngSha256": _sha(png_bytes),
            "rgbSha256": expected_rgb_sha256,
            "pixelSize": list(size),
            "pixelMode": "RGB",
            "connectivity": 8,
            "boundsConvention": "half_open_pixel_edges",
            "foregroundRule": "min(R,G,B)!=255",
            "lowContrastRule": "foreground and min(R,G,B)>=224",
            "foregroundMaskSha256": foreground_hash.hexdigest(),
            "lowContrastMaskSha256": low_hash.hexdigest(),
            "foregroundPixelCount": total,
            "lowContrastPixelCount": low_total,
            "exactWhitePixelCount": width * height - total,
            "foregroundRunCount": len(runs),
            "componentCount": len(components),
            "components": components,
            "coverage": {
                "processedPixelBounds": [0, 0, width, height],
                "processedPixelCount": width * height,
                "assignedForegroundPixelCount": total,
                "unassignedForegroundPixelCount": 0,
                "multiplyAssignedForegroundPixelCount": 0,
                "exactPixelMembershipVerified": True,
                "contentCompletenessVerified": False,
                "ocrTruthVerified": False,
                "blankValueProven": False,
                "readingOrderVerified": False,
            },
            "limits": {
                "imageBytes": MAX_IMAGE_BYTES,
                "pixels": MAX_PIXELS,
                "foregroundRuns": MAX_RUNS,
                "components": MAX_COMPONENTS,
            },
        }
        result["fingerprint"] = _digest(result)
        check()
        result["diagnostics"] = {"elapsedSeconds": time.monotonic() - started}
        return result
    except VisualPixelError:
        raise
    except ImportError:
        raise VisualPixelError("visual_pixels_decoder_unavailable") from None
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, OverflowError):
        raise VisualPixelError("visual_pixels_decode_or_inventory_invalid") from None
