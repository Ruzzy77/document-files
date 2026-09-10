"""Source RGB plus labeled exact membership masks, never semantic/noise labels."""

from __future__ import annotations

import hashlib
import io
import math
import re
import time
from copy import deepcopy
from importlib.metadata import version

from .pdf_review_images import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, PdfReviewImages, _BoundedPng
from .pdf_visual_plan import MAX_SPLIT_RUNS, MAX_UNITS, PdfVisualReviewError, digest, require

VERSION = "document-files.pdf-unit-display.v2"
MARGIN = 8
LABEL_HEIGHT = 24
MIN_WIDTH = 320
POLICY = "original-rgb-and-separate-black-membership-masks-no-semantic-labels"


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _is_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _check(deadline, cancelled):
    require(
        deadline is None or type(deadline) in (int, float) and math.isfinite(deadline),
        "visual_display_configuration",
    )
    require(cancelled is None or callable(cancelled), "visual_display_configuration")
    if cancelled is not None and cancelled():
        raise PdfVisualReviewError("visual_display_cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise PdfVisualReviewError("visual_display_timeout")


def _inside(inner, outer):
    return (
        outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
    )


def _box(box, size):
    require(
        isinstance(box, list)
        and len(box) == 4
        and all(type(x) is int for x in box)
        and _inside(box, [0, 0, *size]),
        "visual_display_bounds",
    )


def _base(plan, descriptor):
    """Check the original two-image identity; never treat a composite as a source."""
    require(isinstance(descriptor, dict), "visual_display_source_invalid")
    base = deepcopy(descriptor)
    claimed = base.pop("fingerprint", None)
    require(isinstance(base.get("usage"), dict), "visual_display_source_invalid")
    base["usage"].pop("elapsedSeconds", None)
    require(claimed == digest(base), "visual_display_source_changed")
    require(
        descriptor.get("version") == "document-files.pdf-review-images.v1"
        and descriptor.get("sourceSha256") == plan["sourceSha256"]
        and descriptor.get("sourceCaptureFingerprint") == plan["captureFingerprint"]
        and descriptor.get("sourcePixelSha256") == plan["rgbSha256"]
        and descriptor.get("pageNo") == plan["page"],
        "visual_display_source_changed",
    )
    images = descriptor.get("images")
    require(isinstance(images, list) and len(images) == 2, "visual_rule_context_not_displayed")
    for i, item in enumerate(images):
        require(isinstance(item, dict), "visual_display_source_invalid")
        bounds = item.get("sourcePixelBounds")
        _box(bounds, plan["pixelSize"])
        require(
            item.get("pixelSize") == [bounds[2] - bounds[0], bounds[3] - bounds[1]]
            and item.get("pixelMode") == "RGB"
            and item.get("resampling") is False,
            "visual_display_source_invalid",
        )
        require(
            all(_is_sha(item.get(k)) for k in ["pngSha256", "pixelSha256"])
            and type(item.get("pngBytes")) is int
            and item["pngBytes"] > 0,
            "visual_display_source_invalid",
        )
        if i == 0:
            require(
                bounds == [0, 0, *plan["pixelSize"]] and item["pixelSha256"] == plan["rgbSha256"],
                "visual_display_source_changed",
            )
    return images


def _units(plan):
    require(
        isinstance(plan.get("units"), list) and len(plan["units"]) <= MAX_UNITS,
        "visual_display_unit_budget",
    )
    require(all(isinstance(u, dict) for u in plan["units"]), "visual_display_unit_inventory")
    # A source-box intersection can group lettering with residual pixels from
    # the original connected rule component, even outside the edge-search band.
    # Display that entire group; membership does not grant it an edge decision.
    components, boundary_components, part_count = {}, set(), 0
    for i, unit in enumerate(plan["units"]):
        parts = unit.get("parts")
        require(isinstance(parts, list), "visual_display_unit_inventory")
        part_count += len(parts)
        require(part_count <= MAX_SPLIT_RUNS, "visual_display_run_budget")
        require(
            all(
                isinstance(p, dict)
                and isinstance(p.get("componentId"), str)
                and bool(p["componentId"])
                for p in parts
            ),
            "visual_display_unit_inventory",
        )
        components[i] = {p["componentId"] for p in parts}
        if unit.get("onlyBoundaryPixels") and unit.get("tableRefs"):
            boundary_components.update(components[i])
    result = [
        u
        for i, u in enumerate(plan["units"])
        if u.get("ruleEdgeTableRefs")
        or not u.get("onlyBoundaryPixels")
        and components[i] & boundary_components
    ]
    require(
        len({u["id"] for u in result}) == len(result)
        and all(
            isinstance(u.get("id"), str) and re.fullmatch(r"u[0-9]{1,3}", u["id"]) for u in result
        ),
        "visual_display_unit_inventory",
    )
    count = 0
    for unit in result:
        require(
            isinstance(unit.get("parts"), list) and len(unit["parts"]) <= MAX_SPLIT_RUNS,
            "visual_display_run_budget",
        )
        for part in unit["parts"]:
            require(
                isinstance(part, dict)
                and isinstance(part.get("runs"), list)
                and bool(part["runs"]),
                "visual_display_run_invalid",
            )
            count += len(part["runs"])
            require(count <= MAX_SPLIT_RUNS, "visual_display_run_budget")
    return result


def _layout(plan, originals):
    units = _units(plan)
    detail = originals[1]["sourcePixelBounds"]
    for unit in units:
        _box(unit["bounds"], plan["pixelSize"])
        require(_inside(unit["bounds"], detail), "visual_display_detail_incomplete")
    entries = [("source-detail", None, detail)] + [(u["id"], u, u["bounds"]) for u in units]
    width = max(MIN_WIDTH, *(b[2] - b[0] for _, _, b in entries)) + MARGIN * 2
    y, panels = MARGIN, []
    for name, unit, box in entries:
        w, h = box[2] - box[0], box[3] - box[1]
        label = "SOURCE DETAIL - ORIGINAL RGB" if unit is None else name + " - MEMBERSHIP ONLY"
        panel = {
            "id": name,
            "kind": "source_rgb" if unit is None else "unit_membership",
            "label": label,
            "labelBounds": [MARGIN, y, width - MARGIN, y + LABEL_HEIGHT],
            "imageBounds": [MARGIN, y + LABEL_HEIGHT, MARGIN + w, y + LABEL_HEIGHT + h],
            "sourcePixelBounds": deepcopy(box),
            "sourcePixelToPanelAffine": [1, 0, 0, 1, MARGIN - box[0], y + LABEL_HEIGHT - box[1]],
            "resampling": False,
        }
        if unit is None:
            panel["pixelSha256"] = originals[1]["pixelSha256"]
        else:
            panel.update(
                unitId=unit["id"],
                membershipSha256=unit["membershipSha256"],
                pixelCount=unit["pixelCount"],
                maskMeaning="black-is-membership-not-source-color-or-semantic-class",
            )
        panels.append(panel)
        y += LABEL_HEIGHT + h + MARGIN
    require(
        plan["pixelSize"][0] * plan["pixelSize"][1] + width * y <= MAX_IMAGE_PIXELS,
        "visual_display_pixel_budget",
    )
    return [width, y], panels


def _mask(plan, unit, *, deadline=None, cancelled=None):
    """Reconstruct exact RGB membership independently of Pillow and source colors."""
    _check(deadline, cancelled)
    require(
        unit.get("membershipSha256") == digest(unit["parts"]), "visual_display_membership_changed"
    )
    box = unit["bounds"]
    _box(box, plan["pixelSize"])
    w, h = box[2] - box[0], box[3] - box[1]
    require(w * h <= MAX_IMAGE_PIXELS, "visual_display_pixel_budget")
    runs = []
    for part in unit["parts"]:
        _check(deadline, cancelled)
        require(
            isinstance(part.get("runs"), list) and len(runs) + len(part["runs"]) <= MAX_SPLIT_RUNS,
            "visual_display_run_budget",
        )
        require(part.get("runsSha256") == digest(part["runs"]), "visual_display_membership_changed")
        for run in part["runs"]:
            require(
                isinstance(run, list) and len(run) == 3 and all(type(v) is int for v in run),
                "visual_display_run_invalid",
            )
            y, left, right = run
            require(
                box[1] <= y < box[3] and box[0] <= left < right <= box[2],
                "visual_display_run_invalid",
            )
            runs.append(run)
    runs.sort()
    raw = bytearray(b"\xff" * (w * h * 3))
    previous, count = None, 0
    for y, left, right in runs:
        _check(deadline, cancelled)
        require(
            previous is None or previous[0] != y or previous[2] <= left,
            "visual_display_overlapping_runs",
        )
        start = ((y - box[1]) * w + left - box[0]) * 3
        raw[start : start + (right - left) * 3] = b"\x00" * ((right - left) * 3)
        count += right - left
        previous = (y, left, right)
    require(count == unit.get("pixelCount"), "visual_display_pixel_count")
    _check(deadline, cancelled)
    return bytes(raw)


def validate_display(plan, descriptor, *, detail_bounds, deadline=None, cancelled=None):
    """Bind the actual panel recipe/hashes to this plan; a boolean is not evidence."""
    _check(deadline, cancelled)
    units = _units(plan)
    if not units:
        require(descriptor is None, "visual_display_unexpected")
        return None
    require(
        isinstance(descriptor, dict) and descriptor.get("version") == VERSION,
        "visual_rule_context_not_displayed",
    )
    require(
        set(descriptor)
        == {
            "version",
            "policy",
            "planFingerprint",
            "sourceSha256",
            "pageNo",
            "sourceCaptureFingerprint",
            "sourcePixelSha256",
            "originalImages",
            "images",
            "limits",
            "usage",
            "pngEncoder",
            "fingerprint",
        },
        "visual_display_descriptor_invalid",
    )
    unsigned = {k: v for k, v in descriptor.items() if k != "fingerprint"}
    require(descriptor["fingerprint"] == digest(unsigned), "visual_display_changed")
    require(
        descriptor["policy"] == POLICY
        and descriptor["planFingerprint"] == plan["fingerprint"]
        and descriptor["sourceSha256"] == plan["sourceSha256"]
        and descriptor["sourceCaptureFingerprint"] == plan["captureFingerprint"]
        and descriptor["sourcePixelSha256"] == plan["rgbSha256"]
        and descriptor["pageNo"] == plan["page"],
        "visual_display_plan_changed",
    )
    originals = _base(plan, descriptor["originalImages"])
    require(detail_bounds == originals[1]["sourcePixelBounds"], "visual_display_detail_changed")
    size, panels = _layout(plan, originals)
    for unit, panel in zip(units, panels[1:], strict=True):
        panel["pixelSha256"] = _sha(_mask(plan, unit, deadline=deadline, cancelled=cancelled))
    images = descriptor["images"]
    require(
        isinstance(images, list) and len(images) == 2 and images[0] == originals[0],
        "visual_display_images_changed",
    )
    atlas = images[1]
    require(
        isinstance(atlas, dict)
        and set(atlas)
        == {
            "id",
            "requestedPurpose",
            "pixelSize",
            "pixelMode",
            "pixelSha256",
            "pngSha256",
            "pngBytes",
            "panels",
            "resampling",
        },
        "visual_display_atlas_invalid",
    )
    require(
        atlas["id"] == "unit-membership-panels"
        and atlas["requestedPurpose"] == "source-detail-and-exact-unit-membership"
        and atlas["panels"] == panels
        and atlas["pixelSize"] == size
        and atlas["pixelMode"] == "RGB"
        and atlas["resampling"] is False,
        "visual_display_atlas_changed",
    )
    require(
        all(_is_sha(atlas[k]) for k in ["pixelSha256", "pngSha256"])
        and type(atlas["pngBytes"]) is int
        and atlas["pngBytes"] > 0,
        "visual_display_atlas_invalid",
    )
    count = sum(i["pixelSize"][0] * i["pixelSize"][1] for i in images)
    total_bytes = sum(i["pngBytes"] for i in images)
    require(
        count <= MAX_IMAGE_PIXELS
        and total_bytes <= MAX_IMAGE_BYTES
        and descriptor["limits"]
        == {"images": 2, "imageBytes": MAX_IMAGE_BYTES, "imagePixels": MAX_IMAGE_PIXELS}
        and descriptor["usage"] == {"imagePixels": count, "pngBytes": total_bytes},
        "visual_display_budget",
    )
    require(
        isinstance(descriptor["pngEncoder"], dict)
        and descriptor["pngEncoder"].get("library") == "Pillow"
        and descriptor["pngEncoder"].get("compressLevel") == 6
        and descriptor["pngEncoder"].get("optimize") is False,
        "visual_display_encoder_invalid",
    )
    _check(deadline, cancelled)
    return descriptor["fingerprint"]


def prepare_display(plan, images, *, deadline, cancelled=None):
    """Keep the full source PNG, replace only the second image with labeled panels."""
    _check(deadline, cancelled)
    units = _units(plan)
    if not units:
        return images
    require(
        plan.get("fingerprint") == digest({k: v for k, v in plan.items() if k != "fingerprint"}),
        "visual_plan_changed",
    )
    base = images.descriptor
    originals = _base(plan, base)
    require(len(images.png_images) == 2, "visual_display_source_invalid")
    size, panels = _layout(plan, originals)
    require(sum(len(b) for b in images.png_images) <= MAX_IMAGE_BYTES, "visual_display_byte_budget")
    from PIL import Image, ImageDraw, ImageFont

    atlas = full = detail = None
    try:
        for data, expected in zip(images.png_images, originals, strict=True):
            _check(deadline, cancelled)
            require(
                type(data) is bytes
                and len(data) == expected["pngBytes"]
                and _sha(data) == expected["pngSha256"],
                "visual_display_source_changed",
            )
        full = Image.open(io.BytesIO(images.png_images[0]))
        detail = Image.open(io.BytesIO(images.png_images[1]))
        for current, expected in zip([full, detail], originals, strict=True):
            require(
                current.format == "PNG"
                and current.mode == "RGB"
                and getattr(current, "n_frames", 1) == 1
                and list(current.size) == expected["pixelSize"],
                "visual_display_source_invalid",
            )
            require(
                _sha(current.tobytes()) == expected["pixelSha256"], "visual_display_source_changed"
            )
            _check(deadline, cancelled)
        with full.crop(originals[1]["sourcePixelBounds"]) as cropped:
            require(cropped.tobytes() == detail.tobytes(), "visual_display_detail_changed")
        atlas = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(atlas)
        font = ImageFont.load_default(size=16)
        for i, panel in enumerate(panels):
            _check(deadline, cancelled)
            x, y, right, bottom = panel["imageBounds"]
            label = panel["label"]
            box = draw.textbbox((0, 0), label, font=font)
            require(
                box[2] - box[0] <= size[0] - MARGIN * 2 and box[3] - box[1] < LABEL_HEIGHT,
                "visual_display_label_budget",
            )
            draw.text((MARGIN, panel["labelBounds"][1]), label, fill="black", font=font)
            if i == 0:
                atlas.paste(detail, (x, y))
            else:
                unit = units[i - 1]
                raw = _mask(plan, unit, deadline=deadline, cancelled=cancelled)
                panel["pixelSha256"] = _sha(raw)
                with Image.frombytes("RGB", (right - x, bottom - y), raw) as mask:
                    atlas.paste(mask, (x, y))
        _check(deadline, cancelled)
        with _BoundedPng(MAX_IMAGE_BYTES - len(images.png_images[0])) as out:
            atlas.save(out, format="PNG", compress_level=6, optimize=False)
            png = out.getvalue()
        _check(deadline, cancelled)
        output = [
            deepcopy(originals[0]),
            {
                "id": "unit-membership-panels",
                "requestedPurpose": "source-detail-and-exact-unit-membership",
                "pixelSize": size,
                "pixelMode": "RGB",
                "pixelSha256": _sha(atlas.tobytes()),
                "pngSha256": _sha(png),
                "pngBytes": len(png),
                "panels": panels,
                "resampling": False,
            },
        ]
        descriptor = {
            "version": VERSION,
            "policy": POLICY,
            "planFingerprint": plan["fingerprint"],
            "sourceSha256": plan["sourceSha256"],
            "pageNo": plan["page"],
            "sourceCaptureFingerprint": plan["captureFingerprint"],
            "sourcePixelSha256": plan["rgbSha256"],
            "originalImages": base,
            "images": output,
            "limits": {"images": 2, "imageBytes": MAX_IMAGE_BYTES, "imagePixels": MAX_IMAGE_PIXELS},
            "usage": {
                "imagePixels": sum(i["pixelSize"][0] * i["pixelSize"][1] for i in output),
                "pngBytes": sum(i["pngBytes"] for i in output),
            },
            "pngEncoder": {
                "library": "Pillow",
                "version": version("Pillow"),
                "compressLevel": 6,
                "optimize": False,
            },
        }
        descriptor["fingerprint"] = digest(descriptor)
        validate_display(
            plan,
            descriptor,
            detail_bounds=originals[1]["sourcePixelBounds"],
            deadline=deadline,
            cancelled=cancelled,
        )
        return PdfReviewImages((images.png_images[0], png), descriptor)
    finally:
        for current in [atlas, detail, full]:
            if current is not None:
                current.close()


def display_payload(descriptor):
    if descriptor.get("version") != VERSION:
        return {}
    return {
        "unitDisplay": {
            "imageIndex": 1,
            "pixelSize": descriptor["images"][1]["pixelSize"],
            "panelColumns": ["id", "imageBounds", "sourcePixelBounds"],
            "panels": [
                [p[k] for k in ["id", "imageBounds", "sourcePixelBounds"]]
                for p in descriptor["images"][1]["panels"]
            ],
        }
    }


def display_argument(images):
    return images if isinstance(images, dict) and images.get("version") == VERSION else None
