"""Synthetic alternate views and owned review/resume, not model quality approval."""

import hashlib
import io
import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from test_pdf_image_read import answer, fixture, make_plan

from document_files.document_model.recognition_cell_observations import fingerprint
from document_files.document_model.recognition_sources import page_render_fingerprint
from document_files.interpretation import pdf_visual_runner as runner
from document_files.interpretation.pdf_image_projection import propose_image_read
from document_files.interpretation.pdf_image_read import SYSTEM as READ_SYSTEM
from document_files.interpretation.pdf_image_read import validate_read
from document_files.interpretation.pdf_review_images import PdfReviewImages
from document_files.interpretation.pdf_visual_pixels import extract_visual_pixels
from document_files.interpretation.pdf_visual_plan import (
    PdfVisualReviewError,
    build_page_plan,
    digest,
    output_schema,
    review_payload,
    validate_decision,
)


def example(empty=False):
    doc, capture = fixture()
    record = doc.provenance["recognitionCellPixelObservations"][0]["observations"][0]
    geometry = record["observation"]["geometry"]
    geometry.update(
        horizontalLines=[[0, y, 61, 1] for y in (0, 20, 40, 60)],
        verticalLines=[[x, 0, 1, 61] for x in (0, 30, 60)],
    )
    record["observation"]["fingerprint"] = fingerprint(record["observation"])
    record["validation"]["observationFingerprint"] = record["observation"]["fingerprint"]
    image = Image.new("RGB", (90, 90), "white")
    draw = ImageDraw.Draw(image)
    for y in (0, 20, 40, 60):
        draw.line((0, y, 60, y), fill="black")
    for x in (0, 30, 60):
        draw.line((x, 0, x, 60), fill="black")
    for row in range(3):
        for col in range(2):
            if empty and (row, col) == (1, 1):
                continue
            draw.point((col * 30 + 10, row * 20 + 10), fill="black")
    draw.rectangle((5, 77, 12, 80), fill="black")
    doc.nodes["foot"]["sourceStructure"]["bbox"].update(top=24, bottom=28)
    out = io.BytesIO()
    image.save(out, format="PNG")
    png = out.getvalue()
    capture["pixelSha256"] = hashlib.sha256(image.tobytes()).hexdigest()
    capture["fingerprint"] = page_render_fingerprint(capture)
    plan = make_plan(doc, capture)
    decision = answer(plan)
    if empty:
        decision["entries"][3].update(state="empty", text="")
    reading = {
        "status": "read",
        "plan": plan,
        "validation": validate_read(plan, decision, detail_bounds=[0, 0, 90, 90]),
    }
    descriptor = {
        "sourceSha256": capture["sourceSha256"],
        "sourceCaptureFingerprint": capture["fingerprint"],
        "pageNo": 1,
        "usage": {"elapsedSeconds": 0},
        "images": [{"sourcePixelBounds": [0, 0, 90, 90]}, {"sourcePixelBounds": [0, 0, 90, 90]}],
    }
    descriptor["fingerprint"] = digest({**descriptor, "usage": {}})
    reading["images"] = descriptor
    images = PdfReviewImages((png, png), descriptor)
    pixels = extract_visual_pixels(
        png,
        expected_rgb_sha256=capture["pixelSha256"],
        expected_size=[90, 90],
        deadline=time.monotonic() + 30,
    )
    return doc, capture, reading, images, pixels


def proposal(doc, reading):
    return propose_image_read(doc, reading, deadline=time.monotonic() + 30)


def approved(plan):
    return {
        "units": [
            {
                "id": u["id"],
                "decision": ("text_and_border" if u["tableRefs"] else "source_text")
                if u["sourceIds"]
                else "table_border",
            }
            for u in plan["units"]
        ],
        "slots": [{"id": s["id"], "decision": "empty"} for s in plan["slots"]],
        "readingOrder": [b["id"] for b in plan["blocks"]],
        "unrepresentedContent": False,
        "sourceChecks": [{"id": s["id"], "decision": "exact"} for s in plan["sources"]],
        "gridChecks": [
            {"id": g["id"], "decision": "rectangular_grid"}
            for g in plan["imageReadProposal"]["grids"]
        ],
    }


def test_proposal_preserves_raw_evidence_and_only_offers_new_regions():
    doc, capture, reading, _, pixels = example()
    before = deepcopy(doc)
    view = proposal(doc, reading)
    assert doc == before
    for key in ("nodes", "bindings", "tables"):
        assert all(getattr(view, key)[k] == v for k, v in getattr(doc, key).items())
    assert view.issues == doc.issues and view.coverage == doc.coverage
    p = view.provenance["pdfImageReadProjections"][0]
    assert p["originalRegions"] == doc.regions and p["originalIssues"] == doc.issues
    assert view.tables["table"]["declaredRowCount"] == 1
    assert view.tables[p["selectedTableRefs"][0]]["declaredRowCount"] == 3
    assert all(not c["isHeader"] for c in view.tables[p["selectedTableRefs"][0]]["cells"])
    assert "0.020" in [view.nodes[r]["text"] for r in p["selectedNodeIds"]]
    plan = build_page_plan(view, capture, pixels, deadline=time.monotonic() + 30)
    assert len(plan["sources"]) == 7 and len(plan["blocks"]) == 2 and not plan["slots"]
    assert {s["sourceRef"] for s in plan["sources"]} == set(p["selectedNodeIds"])
    payload = review_payload(plan)
    assert payload["imageReadProposal"]["grids"][0]["rows"] == 3
    assert "priorTableRef" not in json.dumps(payload)
    assert (
        validate_decision(plan, approved(plan), detail_bounds=[0, 0, 90, 90])["status"]
        == "reviewed"
    )
    assert "sourceChecks" in output_schema(plan)["required"]


@pytest.mark.parametrize(
    "change",
    [
        "unknown_text",
        "unknown_grid",
        "no_detail",
        "missing_check",
        "duplicate_check",
        "false_choice",
    ],
)
def test_each_literal_and_grid_requires_separate_review(change):
    doc, capture, reading, _, pixels = example()
    view = proposal(doc, reading)
    plan = build_page_plan(view, capture, pixels, deadline=time.monotonic() + 30)
    decision = approved(plan)
    detail = [0, 0, 90, 90]
    if change == "unknown_text":
        decision["sourceChecks"][0]["decision"] = "unknown"
    elif change == "unknown_grid":
        decision["gridChecks"][0]["decision"] = "unknown"
    elif change == "no_detail":
        detail = None
    elif change == "missing_check":
        del decision["sourceChecks"]
    elif change == "duplicate_check":
        decision["sourceChecks"][1]["id"] = decision["sourceChecks"][0]["id"]
    else:
        decision["sourceChecks"][0]["decision"] = "corrected"
    if change.startswith("unknown"):
        assert validate_decision(plan, decision, detail_bounds=detail)["status"] == "unresolved"
    else:
        with pytest.raises(PdfVisualReviewError):
            validate_decision(plan, decision, detail_bounds=detail)


def test_empty_read_is_still_missing_and_needs_pixel_border_review():
    doc, capture, reading, _, pixels = example(empty=True)
    view = proposal(doc, reading)
    p = view.provenance["pdfImageReadProjections"][0]
    table = view.tables[p["selectedTableRefs"][0]]
    assert len(table["cells"]) == 5 and table["unobservedCellCount"] == 1
    assert not any(n["text"] == "" for n in view.nodes.values())
    plan = build_page_plan(view, capture, pixels, deadline=time.monotonic() + 30)
    assert len(plan["slots"]) == 1
    decision = approved(plan)
    with pytest.raises(PdfVisualReviewError, match="visual_slot_"):
        validate_decision(plan, decision, detail_bounds=[0, 0, 90, 90])
    decision["slots"][0]["decision"] = "unknown"
    assert validate_decision(plan, decision, detail_bounds=[0, 0, 90, 90])["status"] == "unresolved"


@pytest.mark.parametrize(
    "change",
    ["text", "region", "unknown", "extra_table", "unplanned", "collision", "cancel", "deadline"],
)
def test_invalid_read_or_ambiguous_replacement_is_atomic(change):
    doc, _, reading, _, _ = example()
    if change == "text":
        reading["validation"]["decision"]["entries"][0]["text"] = "different"
    elif change == "region":
        doc.regions[0]["nodeIds"].append("cell")
    elif change == "unknown":
        decision = deepcopy(reading["validation"]["decision"])
        decision["entries"][0].update(state="uncertain", text="?")
        reading["validation"] = validate_read(
            reading["plan"], decision, detail_bounds=[0, 0, 90, 90]
        )
    elif change == "extra_table":
        doc.tables["other"] = deepcopy(doc.tables["table"])
        reading["plan"] = make_plan(doc, doc.provenance["pdfPageRenderCaptures"][0]["capture"])
        reading["validation"] = validate_read(
            reading["plan"], answer(reading["plan"]), detail_bounds=[0, 0, 90, 90]
        )
    elif change == "unplanned":
        reading["plan"]["unplannedObservationFingerprints"].append("z" * 64)
    elif change == "collision":
        doc = proposal(doc, reading)
    before = deepcopy(doc)
    with pytest.raises(PdfVisualReviewError):
        propose_image_read(
            doc,
            reading,
            deadline=time.monotonic() + (-1 if change == "deadline" else 30),
            cancelled=lambda: change == "cancel",
        )
    assert doc == before


def setup(monkeypatch, choice="accepted"):
    doc, capture, reading, images, pixels = example()
    before = deepcopy(doc)
    plan = build_page_plan(proposal(doc, reading), capture, pixels, deadline=time.monotonic() + 30)
    calls = []
    checkpoints = []
    usage = {"modelCalls": 0, "unreportedUsageCalls": 0, "promptTokens": 0, "completionTokens": 0}
    monkeypatch.setattr(runner, "prepare_pdf_review_images", lambda *a, **kw: images)
    monkeypatch.setattr(runner, "extract_visual_pixels", lambda *a, **kw: pixels)
    original = runner.build_page_plan

    def build(doc, *args, **kw):
        if "pdfImageReadProjections" not in doc.provenance:
            raise PdfVisualReviewError("visual_slot_inventory_incomplete")
        return original(doc, *args, **kw)

    monkeypatch.setattr(runner, "build_page_plan", build)

    def infer(request):
        calls.append(request)
        assert usage["modelCalls"] == len(calls) and usage["unreportedUsageCalls"] == 1
        is_read = request.messages[0]["content"] == READ_SYSTEM
        if is_read:
            decision = reading["validation"]["decision"]
        else:
            assert checkpoints[-1]["pages"]["1"]["imageReview"]["status"] == "running"
            if choice == "timeout":
                raise runner.ModelError("ai_timeout")
            decision = approved(plan)
            if choice == "unknown":
                decision["sourceChecks"][0]["decision"] = "unknown"
        return SimpleNamespace(
            text=json.dumps(decision),
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
        )

    client = SimpleNamespace(infer=infer)

    def run(restore=None, max_calls=2):
        result, state = runner.review_pdf_pages(
            b"synthetic",
            doc,
            client=client,
            usage=usage,
            max_calls=max_calls,
            deadline=time.monotonic() + 30,
            context_chars=16000,
            checkpoint=lambda s: checkpoints.append(deepcopy(s)),
            restore=restore,
        )
        assert doc == before
        return result, state

    return run, calls, checkpoints, usage, doc


def test_reachable_review_applies_only_selected_view_and_restores_without_new_model_calls(
    monkeypatch,
):
    run, calls, checkpoints, usage, doc = setup(monkeypatch)
    result, state = run()
    assert result is not None and len(calls) == 2
    assert result.provenance["pdfVisualReviewApplication"]["status"] == "applied"
    assert result.provenance["pdfVisualReviewApplication"]["selectedImageProjectionFingerprints"]
    assert result.tables["table"] == doc.tables["table"] and result.issues == doc.issues
    assert (
        result.coverage["status"] == "partial"
    )  # Original processing issues are not silently cleared.
    assert run(state)[0] == result and len(calls) == 2
    interrupted = next(
        s
        for s in checkpoints
        if s["pages"].get("1", {}).get("imageReview", {}).get("status") == "running"
    )
    assert run(interrupted)[1]["haltReason"] == "pdf_image_projection_review_interrupted"
    assert usage["modelCalls"] == 2 and "data:image" not in json.dumps(checkpoints)


@pytest.mark.parametrize("choice", ["unknown", "timeout"])
def test_failed_or_unknown_review_retains_original_and_is_not_retried(monkeypatch, choice):
    run, calls, _, _, _ = setup(monkeypatch, choice)
    result, state = run()
    assert result is None and len(calls) == 2
    assert run(state)[0] is None and len(calls) == 2


def test_unattempted_review_resumes_without_reading_again(monkeypatch):
    run, calls, _, _, _ = setup(monkeypatch)
    result, state = run(max_calls=1)
    assert result is None and len(calls) == 1
    assert state["haltReason"] == "model_call_budget_exceeded"
    assert run(state, max_calls=1)[0] is None and len(calls) == 1
    assert run(state)[0] is not None and len(calls) == 2


@pytest.mark.parametrize("change", ["text", "grid", "image", "input", "decision"])
def test_completed_review_checkpoint_cannot_change_inputs_or_decisions(monkeypatch, change):
    run, calls, _, _, doc = setup(monkeypatch)
    _, state = run()
    review = state["pages"]["1"]["imageReview"]
    if change == "text":
        review["plan"]["sources"][0]["text"] = "bad"
    elif change == "grid":
        review["plan"]["imageReadProposal"]["grids"][0]["rows"] = 100
    elif change == "image":
        review["images"]["images"][0]["sourcePixelBounds"][0] = 1
    elif change == "input":
        doc.nodes["cell"]["text"] = "bad"
    else:
        review["validation"]["decision"]["sourceChecks"][0]["decision"] = "unknown"
    with pytest.raises(ValueError, match="incompatible"):
        run(state)
    assert len(calls) == 2


def test_failed_preparation_is_preserved_without_repeated_pixel_work(monkeypatch):
    run, calls, _, _, _ = setup(monkeypatch)
    original = runner.build_page_plan
    preparations = []

    def fail(doc, *args, **kwargs):
        if "pdfImageReadProjections" in doc.provenance:
            preparations.append(True)
            raise PdfVisualReviewError("visual_comparison_budget")
        return original(doc, *args, **kwargs)

    monkeypatch.setattr(runner, "build_page_plan", fail)
    result, state = run()
    assert result is None and state["haltReason"] == "visual_comparison_budget"
    assert run(state)[0] is None and len(calls) == len(preparations) == 1


@pytest.mark.parametrize("change", ["unresolved", "missing", "duplicate", "wrong_observation"])
def test_model_approval_cannot_override_unverified_grid_measurements(change):
    doc, capture, reading, _, pixels = example()
    plan = build_page_plan(proposal(doc, reading), capture, pixels, deadline=time.monotonic() + 30)
    measured = plan["grid"]["grids"][0]
    if change == "unresolved":
        measured["bands"][0]["status"] = "unresolved"
    elif change == "missing":
        measured["bands"].pop()
    elif change == "duplicate":
        measured["bands"][1] = deepcopy(measured["bands"][0])
    else:
        measured["observationFingerprint"] = "z" * 64
    plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
    with pytest.raises(PdfVisualReviewError, match="visual_proposal_grid_unmeasured"):
        validate_decision(plan, approved(plan), detail_bounds=[0, 0, 90, 90])


def test_runner_rejects_unmeasured_proposal_before_spending_review_call(monkeypatch):
    run, calls, _, _, _ = setup(monkeypatch)
    original = runner.build_page_plan

    def unmeasured(doc, *args, **kwargs):
        plan = original(doc, *args, **kwargs)
        plan["grid"]["grids"][0]["bands"][0]["status"] = "unresolved"
        plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
        return plan

    monkeypatch.setattr(runner, "build_page_plan", unmeasured)
    result, state = run()
    assert result is None and state["haltReason"] == "visual_proposal_grid_unmeasured"
    assert run(state)[0] is None and len(calls) == 1
