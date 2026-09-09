"""Finite page calls/checkpoints with synthetic observations and an in-process model."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from document_files.interpretation import pdf_visual_runner as runner
from document_files.interpretation.backends import MANAGED_VISION_VERSION, ModelError
from document_files.interpretation.pdf_review_images import PdfReviewImages
from document_files.interpretation.pdf_visual_plan import digest


def setup(monkeypatch, *, failure=None):
    from test_pdf_visual_apply import fixture

    doc, reviews = fixture()
    plan, validation = reviews[0]["plan"], reviews[0]["validation"]
    for unit in plan["units"]:
        unit.setdefault("bounds", [0, 0, 1, 1])
        unit.setdefault("pixelCount", 1)
        unit.setdefault("slotIds", [])
    for block in plan["blocks"]:
        block.setdefault("bounds", [0, 0, 1, 1])
    plan["fingerprint"] = digest({k: v for k, v in plan.items() if k != "fingerprint"})
    validation = runner.validate_decision(
        plan, validation["decision"], detail_bounds=validation["detailBounds"]
    )
    # Input transport is tested with real source/render hashes elsewhere. This
    # harness makes no PDF/Pillow/OCR/network calls and supplies no reference answer.
    descriptor = {
        "sourceSha256": plan["sourceSha256"],
        "sourceCaptureFingerprint": plan["captureFingerprint"],
        "pageNo": 1,
        "usage": {"elapsedSeconds": 0.01},
        "images": [
            {"sourcePixelBounds": [0, 0, *plan["pixelSize"]]},
            {"sourcePixelBounds": validation["detailBounds"]},
        ],
    }
    descriptor["fingerprint"] = digest({**descriptor, "usage": {}})
    images = PdfReviewImages((b"png", b"crop"), descriptor)
    calls = {"render": 0, "pixel": 0, "model": 0}

    def render(*a, **kw):
        calls["render"] += 1
        return images

    def pixels(*a, **kw):
        calls["pixel"] += 1
        return {}

    monkeypatch.setattr(runner, "prepare_pdf_review_images", render)
    monkeypatch.setattr(runner, "extract_visual_pixels", pixels)
    monkeypatch.setattr(
        runner, "review_crop", lambda *a: {"pixelBounds": validation["detailBounds"]}
    )
    monkeypatch.setattr(runner, "build_page_plan", lambda *a, **kw: deepcopy(plan))
    checkpoints = []
    usage = {
        "modelCalls": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "unreportedUsageCalls": 0,
        "elapsedSeconds": 0.0,
    }

    class Client:
        identity = {"vision": {"version": MANAGED_VISION_VERSION}}
        max_output_tokens = 2000

        def infer(self, request):
            calls["model"] += 1
            assert checkpoints[-1]["pages"]["1"]["status"] == "running"
            assert usage["modelCalls"] == usage["unreportedUsageCalls"] == 1
            assert request.max_output_tokens == 2000
            assert 0 < request.timeout <= 30
            assert request.messages[1]["content"][1]["type"] == "image_url"
            if failure == "timeout":
                raise ModelError("ai_timeout")
            decision = deepcopy(validation["decision"])
            if failure == "unknown":
                decision["unrepresentedContent"] = True
            return SimpleNamespace(
                text=json.dumps(decision),
                finish_reason="stop",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            )

    client = Client()

    def run(*, restore=None, max_calls=2, expired=False, cancelled=None):
        return runner.review_pdf_pages(
            b"synthetic",
            doc,
            client=client,
            usage=usage,
            max_calls=max_calls,
            deadline=time.monotonic() + (-1 if expired else 30),
            context_chars=20000,
            checkpoint=lambda s: checkpoints.append(deepcopy(s)),
            restore=restore,
            cancelled=cancelled,
        )

    return doc, calls, checkpoints, usage, run, client


def test_actual_positive_application_and_no_repeat_on_resume(monkeypatch):
    doc, calls, checkpoints, usage, run, client = setup(monkeypatch)
    before = deepcopy(doc)
    reviewed, state = run()
    assert doc == before
    assert reviewed.provenance["pdfVisualReviewApplication"]["status"] == "applied"
    assert reviewed.issues == []
    assert calls == {"render": 1, "pixel": 1, "model": 1}
    assert usage == {
        "modelCalls": 1,
        "promptTokens": 100,
        "completionTokens": 20,
        "unreportedUsageCalls": 0,
        "elapsedSeconds": 0.0,
    }
    assert "data:image" not in json.dumps(checkpoints)
    assert runner.review_identity(client)["version"] == runner.VERSION
    again, _ = run(restore=state)
    assert again == reviewed
    assert calls == {"render": 1, "pixel": 1, "model": 1}


@pytest.mark.parametrize("failure", ["timeout", "unknown"])
def test_unknown_and_failed_response_stop_without_changing_observation(monkeypatch, failure):
    doc, calls, checkpoints, usage, run, _ = setup(monkeypatch, failure=failure)
    before = deepcopy(doc)
    reviewed, state = run()
    assert reviewed is None and doc == before
    assert state["haltReason"] in {"ai_timeout", "pdf_visual_review_unresolved"}
    assert run(restore=state)[0] is None
    assert calls["model"] == 1
    assert usage["unreportedUsageCalls"] == (failure == "timeout")


@pytest.mark.parametrize(
    "option,code",
    [
        ("calls", "model_call_budget_exceeded"),
        ("time", "completion_budget_exceeded"),
        ("cancel", "ai_cancelled"),
    ],
)
def test_no_work_after_budget_or_cancellation(monkeypatch, option, code):
    _, calls, _, _, run, _ = setup(monkeypatch)
    kwargs = (
        {"max_calls": 0}
        if option == "calls"
        else {"expired": True}
        if option == "time"
        else {"cancelled": lambda: True}
    )
    reviewed, state = run(**kwargs)
    assert reviewed is None and state["haltReason"] == code
    assert calls == {"render": 0, "pixel": 0, "model": 0}
    if option == "calls":
        assert run(restore=state)[0] is not None  # Pending, not a repeated attempt.


def test_interrupted_call_is_not_silently_replayed(monkeypatch):
    _, calls, checkpoints, _, run, _ = setup(monkeypatch)
    run()
    interrupted = next(s for s in checkpoints if s["pages"].get("1", {}).get("status") == "running")
    result, state = run(restore=interrupted)
    assert result is None and state["haltReason"] == "pdf_visual_review_interrupted"
    assert calls["model"] == 1


@pytest.mark.parametrize("mutation", ["source", "decision", "image", "version"])
def test_changed_review_checkpoint_is_rejected_without_another_call(monkeypatch, mutation):
    doc, calls, _, _, run, _ = setup(monkeypatch)
    _, state = run()
    if mutation == "source":
        doc.nodes["cell"]["text"] = "different"
    elif mutation == "decision":
        state["pages"]["1"]["validation"]["decisionFingerprint"] = "0" * 64
    elif mutation == "image":
        state["pages"]["1"]["images"]["images"][1]["sourcePixelBounds"] = [0, 0, 1, 1]
    else:
        state["version"] = "document-files.pdf-visual-review.v1"
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(restore=state)
    assert calls["model"] == 1


def test_text_only_and_implicit_cloud_clients_do_not_enable_images():
    assert runner.review_identity(None) is None
    assert runner.review_identity(SimpleNamespace(identity={}, infer=lambda: None)) is None
    assert (
        runner.review_identity(SimpleNamespace(identity={"vision": True}, infer=lambda: None))
        is None
    )


def test_engine_owns_review_checkpoint_and_plans_only_after_acceptance(monkeypatch):
    import io

    from document_files.analysis import AnalysisInput, AnalysisJob
    from document_files.interpretation import engine
    from document_files.interpretation.contracts import ExtractionOptions

    doc, _, _, _, _, _ = setup(monkeypatch)
    from test_pdf_visual_apply import fixture

    _, fixture_reviews = fixture()
    answer = fixture_reviews[0]["validation"]["decision"]
    checkpoints, observed, requests = [], [], []

    def infer(request):
        requests.append(request)
        assert checkpoints[-1]["phase"] == "reviewing_pdf"
        assert checkpoints[-1]["usage"]["modelCalls"] == 1
        assert not checkpoints[-1].get("regions")
        return SimpleNamespace(
            text=json.dumps(answer),
            finish_reason="stop",
            usage={"prompt_tokens": 12, "completion_tokens": 5},
        )

    client = SimpleNamespace(identity={"vision": {"version": MANAGED_VISION_VERSION}}, infer=infer)
    monkeypatch.setattr(
        engine,
        "analyze_document",
        lambda *a, **kw: SimpleNamespace(
            analyzer=SimpleNamespace(to_dict=lambda: {"id": "synthetic"}),
            extraction=SimpleNamespace(units=[]),
        ),
    )
    monkeypatch.setattr(
        engine,
        "project_structured_extraction",
        lambda *a, **kw: {"units": [], "issues": [], "coverage": {}},
    )

    def observe(*a, **kw):
        observed.append(True)
        return deepcopy(doc)

    monkeypatch.setattr(engine, "observe_document", observe)
    content = b"%PDF-synthetic"
    job = AnalysisJob(job_id="visual", input=AnalysisInput.from_bytes(content, format_id="pdf"))
    options = ExtractionOptions(reconstructionContext=False, maxModelCalls=1)
    result = engine.extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=client,
        options=options,
        checkpoint=lambda s: checkpoints.append(deepcopy(s)),
    )
    assert len(observed) == len(requests) == 1
    assert result["provenance"]["observation"]["pdfVisualReviewApplication"]["status"] == "applied"
    assert result["document"]["nodes"]["table/visual-blank/0:1"]["text"] == ""
    assert result["extraction"]["modelCalls"] == 1
    assert result["extraction"]["status"] == "partial"  # Review is not semantic extraction.
    assert checkpoints[-1]["regions"]
    assert checkpoints[-1]["identity"]["pdfVisualReview"]["version"] == runner.VERSION
    # Resume the durable visual result before semantic planning, without OCR or
    # another page call. Normal regional checkpoints remain resumable too.
    visual = next(s for s in reversed(checkpoints) if s.get("phase") == "reviewing_pdf")
    again = engine.extract_schema_from_stream(
        job, io.BytesIO(content), model_client=client, options=options, restore=visual
    )
    assert again["document"] == result["document"]
    assert len(observed) == len(requests) == 1
    normal = engine.extract_schema_from_stream(
        job, io.BytesIO(content), model_client=client, options=options, restore=checkpoints[-1]
    )
    assert normal["document"] == result["document"]
    assert len(observed) == len(requests) == 1
    assert "data:image" not in json.dumps(checkpoints)
