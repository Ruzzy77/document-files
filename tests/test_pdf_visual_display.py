"""Exact synthetic display bytes and review ownership, not model quality evidence."""

import hashlib
import io
import time
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw
from test_pdf_image_projection import approved, example, proposal
from test_pdf_image_read import answer, make_plan

from document_files.document_model.recognition_sources import page_render_fingerprint
from document_files.interpretation import pdf_visual_display as display
from document_files.interpretation.pdf_image_read import validate_read
from document_files.interpretation.pdf_review_images import PdfReviewImages
from document_files.interpretation.pdf_visual_pixels import extract_visual_pixels
from document_files.interpretation.pdf_visual_plan import (
    PdfVisualReviewError,
    build_page_plan,
    digest,
    validate_decision,
)


def fixture():
    doc, capture, _, images, _ = example()
    with Image.open(io.BytesIO(images.png_images[0])) as image:
        draw = ImageDraw.Draw(image)
        draw.line((0, 62, 60, 62), fill=(254, 254, 254))
        draw.point((0, 61), fill=(254, 254, 254))
        capture["pixelSha256"] = hashlib.sha256(image.tobytes()).hexdigest()
        capture["fingerprint"] = page_render_fingerprint(capture)
        out = io.BytesIO()
        image.save(out, format="PNG")
        png = out.getvalue()
    doc.provenance["pdfPageRenderCaptures"][0]["capture"] = capture
    read_plan = make_plan(doc, capture)
    reading = {
        "status": "read",
        "plan": read_plan,
        "validation": validate_read(read_plan, answer(read_plan), detail_bounds=[0, 0, 90, 90]),
    }
    base = {
        "version": "document-files.pdf-review-images.v1",
        "sourceSha256": capture["sourceSha256"],
        "sourceCaptureFingerprint": capture["fingerprint"],
        "sourcePixelSha256": capture["pixelSha256"],
        "pageNo": 1,
        "usage": {"elapsedSeconds": 0},
        "images": [
            {
                "id": f"i{i}",
                "sourcePixelBounds": [0, 0, 90, 90],
                "pixelSize": [90, 90],
                "pixelMode": "RGB",
                "pixelSha256": capture["pixelSha256"],
                "pngSha256": hashlib.sha256(png).hexdigest(),
                "pngBytes": len(png),
                "resampling": False,
            }
            for i in range(2)
        ],
    }
    base["fingerprint"] = digest({**base, "usage": {}})
    images = PdfReviewImages((png, png), base)
    reading["images"] = base
    pixels = extract_visual_pixels(
        png,
        expected_rgb_sha256=capture["pixelSha256"],
        expected_size=[90, 90],
        deadline=time.monotonic() + 10,
    )
    view = proposal(doc, reading)
    plan = build_page_plan(view, capture, pixels, deadline=time.monotonic() + 10)
    assert any(u["ruleEdgeTableRefs"] for u in plan["units"])
    return doc, view, capture, reading, pixels, plan, images


def prepare(plan, images):
    return display.prepare_display(plan, images, deadline=time.monotonic() + 10)


def test_membership_masks_preserve_all_parts_and_keep_original_rgb_separate():
    _, _, _, _, _, plan, images = fixture()
    before = deepcopy(plan), images.png_images, images.descriptor
    rendered = prepare(plan, images)
    descriptor = rendered.descriptor
    assert rendered.png_images[0] == images.png_images[0] and len(rendered.png_images) == 2
    assert descriptor["originalImages"] == images.descriptor
    assert "sourcePixelBounds" not in descriptor["images"][1]
    assert "pixelToOriginalPageAffine" not in descriptor["images"][1]
    assert (
        display.validate_display(plan, descriptor, detail_bounds=[0, 0, 90, 90])
        == descriptor["fingerprint"]
    )
    units = {u["id"]: u for u in plan["units"]}
    with Image.open(io.BytesIO(rendered.png_images[1])) as atlas:
        for panel in descriptor["images"][1]["panels"]:
            with atlas.crop(panel["imageBounds"]) as content:
                assert hashlib.sha256(content.tobytes()).hexdigest() == panel["pixelSha256"]
                if panel["kind"] == "source_rgb":
                    with Image.open(io.BytesIO(images.png_images[1])) as original:
                        assert content.tobytes() == original.tobytes()
                    assert content.getpixel((30, 62)) == (254, 254, 254)
                else:
                    unit = units[panel["unitId"]]
                    x0, y0, _, _ = panel["sourcePixelBounds"]
                    expected = {
                        (x - x0, y - y0)
                        for part in unit["parts"]
                        for y, left, right in part["runs"]
                        for x in range(left, right)
                    }
                    actual = {
                        (x, y)
                        for y in range(content.height)
                        for x in range(content.width)
                        if content.getpixel((x, y)) == (0, 0, 0)
                    }
                    assert actual == expected and len(actual) == unit["pixelCount"]
                    assert {
                        color for _, color in content.getcolors(content.width * content.height)
                    } <= {(0, 0, 0), (255, 255, 255)}
    assert (plan, images.png_images, images.descriptor) == before
    assert prepare(plan, images).png_images == rendered.png_images
    decision = approved(plan)
    for unit, choice in zip(plan["units"], decision["units"], strict=True):
        if unit["ruleEdgeTableRefs"]:
            choice["decision"] = "rule_edge"
    validation = validate_decision(plan, decision, detail_bounds=[0, 0, 90, 90], display=descriptor)
    assert (
        validation["status"] == "reviewed"
        and validation["unitDisplayFingerprint"] == descriptor["fingerprint"]
    )
    assert not validation["ocrTruthVerified"]


@pytest.mark.parametrize("claim", [None, True, {"displayed": True}])
def test_claim_is_not_pixel_display_evidence(claim):
    *_, plan, _ = fixture()
    with pytest.raises(PdfVisualReviewError, match="visual_rule_context_not_displayed"):
        display.validate_display(plan, claim, detail_bounds=[0, 0, 90, 90])


@pytest.mark.parametrize(
    "change",
    [
        "missing_panel",
        "unit_id",
        "source_bounds",
        "affine",
        "mask_hash",
        "original_hash",
        "plan_hash",
        "count",
        "global_affine",
        "detail",
    ],
)
def test_changed_display_cannot_pass_even_after_descriptor_rehash(change):
    *_, plan, images = fixture()
    descriptor = prepare(plan, images).descriptor
    panel = descriptor["images"][1]["panels"][-1]
    bounds = [0, 0, 90, 90]
    if change == "missing_panel":
        descriptor["images"][1]["panels"].pop()
    elif change == "unit_id":
        panel["unitId"] = "u999"
    elif change == "source_bounds":
        panel["sourcePixelBounds"][0] += 1
    elif change == "affine":
        panel["sourcePixelToPanelAffine"][-1] += 1
    elif change == "mask_hash":
        panel["pixelSha256"] = "f" * 64
    elif change == "original_hash":
        descriptor["images"][0]["pixelSha256"] = "f" * 64
    elif change == "plan_hash":
        descriptor["planFingerprint"] = "f" * 64
    elif change == "count":
        panel["pixelCount"] += 1
    elif change == "global_affine":
        descriptor["images"][1]["pixelToOriginalPageAffine"] = [1, 0, 0, 1, 0, 0]
    else:
        bounds[2] -= 1
    descriptor["fingerprint"] = digest({k: v for k, v in descriptor.items() if k != "fingerprint"})
    with pytest.raises(PdfVisualReviewError):
        display.validate_display(plan, descriptor, detail_bounds=bounds)


@pytest.mark.parametrize(
    "change", ["bytes", "pixel_budget", "byte_budget", "runs", "overlap", "timeout", "cancelled"]
)
def test_source_and_resource_limits_do_not_produce_partial_success(change, monkeypatch):
    *_, plan, images = fixture()
    kwargs = {"deadline": time.monotonic() + 10}
    if change == "bytes":
        images = PdfReviewImages(
            (images.png_images[0], images.png_images[1] + b"changed"), images.descriptor
        )
    elif change == "pixel_budget":
        monkeypatch.setattr(display, "MAX_IMAGE_PIXELS", 100)
    elif change == "byte_budget":
        monkeypatch.setattr(display, "MAX_IMAGE_BYTES", 10)
    elif change == "timeout":
        kwargs["deadline"] = time.monotonic() - 1
    elif change == "cancelled":
        kwargs["cancelled"] = lambda: True
    else:
        unit = next(u for u in plan["units"] if u["ruleEdgeTableRefs"])
        part = unit["parts"][0]
        if change == "runs":
            part["runs"][0][1] = -1
        else:
            part["runs"].append(part["runs"][0])
        part["runsSha256"] = digest(part["runs"])
        unit["membershipSha256"] = digest(unit["parts"])
        plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
    with pytest.raises(PdfVisualReviewError):
        display.prepare_display(plan, images, **kwargs)


def test_runner_transmits_the_real_panels_and_resumes_without_render_or_model(monkeypatch):
    import base64
    import json
    from types import SimpleNamespace

    from test_pdf_visual_plan import wire_response

    from document_files.interpretation import pdf_visual_runner as runner
    from document_files.interpretation.pdf_image_read import SYSTEM as READ_SYSTEM

    doc, _, _, reading, _, plan, images = fixture()
    before = deepcopy(doc)
    calls, checkpoints = [], []
    usage = {"modelCalls": 0, "unreportedUsageCalls": 0, "promptTokens": 0, "completionTokens": 0}
    monkeypatch.setattr(runner, "prepare_pdf_review_images", lambda *a, **kw: images)
    original_build = runner.build_page_plan

    def build(doc, *args, **kwargs):
        if "pdfImageReadProjections" not in doc.provenance:
            raise PdfVisualReviewError("visual_slot_inventory_incomplete")
        return original_build(doc, *args, **kwargs)

    monkeypatch.setattr(runner, "build_page_plan", build)

    def infer(request):
        calls.append(request)
        if request.messages[0]["content"] == READ_SYSTEM:
            decision = reading["validation"]["decision"]
        else:
            record = checkpoints[-1]["pages"]["1"]["imageReview"]
            assert record["status"] == "running" and record["images"]["version"] == display.VERSION
            assert record["plan"] == plan and usage["modelCalls"] == 2
            content = request.messages[1]["content"]
            wire = json.loads(content[0]["text"])
            assert wire["unitDisplay"]["imageIndex"] == 1
            pngs = [base64.b64decode(p["image_url"]["url"].split(",", 1)[1]) for p in content[1:]]
            assert len(pngs) == 2 and pngs[0] == images.png_images[0]
            assert hashlib.sha256(pngs[1]).hexdigest() == record["images"]["images"][1]["pngSha256"]
            decision = approved(plan)
            for unit, choice in zip(plan["units"], decision["units"], strict=True):
                if unit["ruleEdgeTableRefs"]:
                    choice["decision"] = "rule_edge"
            decision = wire_response(plan, decision)
        return SimpleNamespace(
            text=json.dumps(decision),
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

    def run(restore=None):
        return runner.review_pdf_pages(
            b"synthetic",
            doc,
            client=SimpleNamespace(infer=infer),
            usage=usage,
            max_calls=2,
            deadline=time.monotonic() + 30,
            context_chars=16000,
            checkpoint=lambda s: checkpoints.append(deepcopy(s)),
            restore=restore,
        )

    result, state = run()
    assert result is not None and doc == before and len(calls) == 2
    record = state["pages"]["1"]["imageReview"]
    assert record["validation"]["unitDisplayFingerprint"] == record["images"]["fingerprint"]
    assert (
        result.provenance["pdfVisualReviewApplication"]["pageReviews"][0]["unitDisplayFingerprint"]
        == record["images"]["fingerprint"]
    )
    assert "data:image" not in json.dumps(state)
    monkeypatch.setattr(runner, "prepare_display", lambda *a, **kw: pytest.fail("display replay"))
    monkeypatch.setattr(
        runner, "prepare_pdf_review_images", lambda *a, **kw: pytest.fail("render replay")
    )
    again, _ = run(state)
    assert again.to_dict() == result.to_dict() and len(calls) == usage["modelCalls"] == 2
    broken = deepcopy(state)
    broken["pages"]["1"]["imageReview"]["images"] = images.descriptor
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(broken)
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["absent", "original_only", "forged_rgb"])
def test_application_requires_display_bound_to_the_actual_capture(change):
    from document_files.interpretation.pdf_visual_apply import apply_page_reviews

    _, view, _, _, _, plan, images = fixture()
    descriptor = prepare(plan, images).descriptor
    decision = approved(plan)
    for unit, choice in zip(plan["units"], decision["units"], strict=True):
        if unit["ruleEdgeTableRefs"]:
            choice["decision"] = "rule_edge"
    if change == "forged_rgb":
        plan["rgbSha256"] = "f" * 64
        plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
        descriptor["planFingerprint"] = plan["fingerprint"]
        descriptor["sourcePixelSha256"] = "f" * 64
        original = descriptor["originalImages"]
        original["sourcePixelSha256"] = "f" * 64
        original["images"][0]["pixelSha256"] = "f" * 64
        original["fingerprint"] = digest(
            {k: ({} if k == "usage" else v) for k, v in original.items() if k != "fingerprint"}
        )
        descriptor["images"][0]["pixelSha256"] = "f" * 64
        descriptor["fingerprint"] = digest(
            {k: v for k, v in descriptor.items() if k != "fingerprint"}
        )
    validation = validate_decision(plan, decision, detail_bounds=[0, 0, 90, 90], display=descriptor)
    record = {"plan": plan, "validation": validation, "images": descriptor}
    if change == "absent":
        del record["images"]
    elif change == "original_only":
        record["images"] = images.descriptor
    before = deepcopy(view)
    result = apply_page_reviews(view, [record])
    assert result.provenance["pdfVisualReviewApplication"]["status"] != "applied"
    assert view == before and result.nodes == before.nodes and result.tables == before.tables


@pytest.mark.parametrize("value", [None, "unit", True])
def test_invalid_unit_objects_fail_with_a_fixed_error(value):
    *_, plan, images = fixture()
    plan["units"][0] = value
    plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
    with pytest.raises(PdfVisualReviewError, match="visual_display_unit_inventory"):
        prepare(plan, images)
