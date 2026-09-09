"""Finite, checkpointed PDF page review before semantic region planning."""

from __future__ import annotations

import time
from copy import deepcopy

from .backends import MANAGED_VISION_VERSION, InferenceRequest, ModelError
from .legacy_engine import decode, encode
from .pdf_review_images import VERSION as IMAGE_VERSION
from .pdf_review_images import PdfReviewImageError, prepare_pdf_review_images
from .pdf_visual_apply import VERSION as APPLY_VERSION
from .pdf_visual_grid import VERSION as GRID_VERSION
from .pdf_visual_grid import VisualGridError
from .pdf_visual_pixels import VERSION as PIXEL_VERSION
from .pdf_visual_pixels import VisualPixelError, extract_visual_pixels
from .pdf_visual_plan import (
    SYSTEM,
    VERSION,
    PdfVisualReviewError,
    build_page_plan,
    digest,
    observation_page_fingerprint,
    output_schema,
    require,
    review_crop,
    review_payload,
    validate_decision,
)

MAX_OUTPUT_TOKENS = 2048


def review_identity(client):
    """Only a deliberately installed image-capable managed pack enables this path."""
    if client is None or not callable(getattr(client, "infer", None)):
        return None
    vision = client.identity.get("vision")
    if not isinstance(vision, dict) or vision.get("version") != MANAGED_VISION_VERSION:
        return None
    return {
        "version": VERSION,
        "images": IMAGE_VERSION,
        "pixels": PIXEL_VERSION,
        "grid": GRID_VERSION,
        "apply": APPLY_VERSION,
    }


def _validated_record(doc, page, record):
    plan, validation = record["plan"], record["validation"]
    require(plan["page"] == page, "visual_checkpoint_page_changed")
    require(
        plan["sourceObservationFingerprint"] == observation_page_fingerprint(doc, page),
        "visual_checkpoint_observation_changed",
    )
    require(
        validation
        == validate_decision(
            plan, validation["decision"], detail_bounds=validation["detailBounds"]
        ),
        "visual_checkpoint_decision_changed",
    )
    images = deepcopy(record["images"])
    fingerprint = images.pop("fingerprint")
    images["usage"].pop("elapsedSeconds")
    require(fingerprint == digest(images), "visual_checkpoint_images_changed")
    require(
        images["sourceSha256"] == plan["sourceSha256"] == doc.provenance["sourceSha256"]
        and images["sourceCaptureFingerprint"] == plan["captureFingerprint"]
        and images["pageNo"] == page
        and images["images"][0]["sourcePixelBounds"] == [0, 0, *plan["pixelSize"]]
        and (images["images"][1]["sourcePixelBounds"] if len(images["images"]) == 2 else None)
        == validation["detailBounds"],
        "visual_checkpoint_images_changed",
    )
    return validation["status"]


def review_pdf_pages(
    content,
    doc,
    *,
    client,
    usage,
    max_calls,
    deadline,
    context_chars,
    checkpoint,
    restore=None,
    cancelled=None,
):
    """No automatic retry of an ambiguous/interrupted or completed page call.

    Images exist only during preparation/inference. The owned checkpoint stores
    exact pixel membership, input identities, decisions and consumed usage, never
    a data URL. A pending page can resume within the remaining explicit budget.
    """
    state = (
        deepcopy(restore)
        if restore is not None
        else {"version": VERSION, "sourceSha256": doc.provenance.get("sourceSha256"), "pages": {}}
    )
    expected = doc.provenance.get("pdfium", {}).get("pageCount")
    if type(expected) is not int or not 0 < expected <= 500:
        state["haltReason"] = "pdf_visual_page_inventory_unavailable"
        checkpoint(state)
        return None, state
    captures = doc.provenance.get("pdfPageRenderCaptures", [])
    pages = {item.get("page"): item for item in captures}
    if (
        len(pages) != len(captures)
        or set(pages) != set(range(1, expected + 1))
        or any(item.get("bindingStatus") != "source_page_matched" for item in captures)
    ):
        state["haltReason"] = "pdf_visual_page_capture_unavailable"
        checkpoint(state)
        return None, state
    try:
        require(
            state["version"] == VERSION
            and state["sourceSha256"] == doc.provenance["sourceSha256"]
            and isinstance(state["pages"], dict),
            "visual_checkpoint_incompatible",
        )
        require(set(state["pages"]) <= {str(p) for p in pages}, "visual_checkpoint_page_changed")
        for key, record in state["pages"].items():
            require(
                record["status"] in {"running", "reviewed", "unresolved", "failed"},
                "visual_checkpoint_invalid",
            )
            if record["status"] in {"reviewed", "unresolved"}:
                require(
                    _validated_record(doc, int(key), record) == record["status"],
                    "visual_checkpoint_decision_changed",
                )
    except (KeyError, TypeError, ValueError, IndexError):
        raise ValueError("PDF review checkpoint is incompatible with source or policy") from None
    state.pop("haltReason", None)
    checkpoint(state)  # Own the completed observation before rendering or inference.

    def stop_reason():
        if cancelled and cancelled():
            return "ai_cancelled"
        if time.monotonic() >= deadline:
            return "completion_budget_exceeded"
        if usage["modelCalls"] >= max_calls:
            return "model_call_budget_exceeded"
        return None

    for page in sorted(pages):
        old = state["pages"].get(str(page))
        if old is not None:
            if old["status"] == "reviewed":
                continue
            state["haltReason"] = (
                "pdf_visual_review_interrupted"
                if old["status"] == "running"
                else old.get("reason", "pdf_visual_review_unresolved")
            )
            checkpoint(state)
            return None, state
        reason = stop_reason()
        if reason:
            state["haltReason"] = reason
            checkpoint(state)
            return None, state
        record = None
        try:
            capture = pages[page]["capture"]
            crop = review_crop(doc, capture)
            images = prepare_pdf_review_images(
                content, capture, crop=crop, deadline=deadline, cancelled=cancelled
            )
            pixels = extract_visual_pixels(
                images.png_images[0],
                expected_rgb_sha256=capture["pixelSha256"],
                expected_size=capture["pixelSize"],
                deadline=deadline,
                cancelled=cancelled,
            )
            plan = build_page_plan(doc, capture, pixels, deadline=deadline, cancelled=cancelled)
            contract = output_schema(plan)
            payload = encode({**review_payload(plan), "outputContract": contract})
            if len(SYSTEM) + len(payload) > context_chars:
                raise ModelError("pdf_visual_context_budget_exceeded")
            reason = stop_reason()
            if reason:
                state["haltReason"] = reason
                checkpoint(state)
                return None, state
            record = {"status": "running", "plan": plan, "images": images.descriptor}
            state["pages"][str(page)] = record
            usage["modelCalls"] += 1
            usage["unreportedUsageCalls"] += 1
            checkpoint(state)  # Count the attempt durably before external inference.
            response = client.infer(
                InferenceRequest(
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": payload}, *images.content_parts()],
                        },
                    ],
                    output_schema=contract,
                    max_output_tokens=min(
                        MAX_OUTPUT_TOKENS,
                        getattr(client, "max_output_tokens", None) or MAX_OUTPUT_TOKENS,
                    ),
                    timeout=deadline - time.monotonic(),
                    cancelled=cancelled,
                )
            )
            if all(
                type(response.usage.get(k)) is int and response.usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                usage["unreportedUsageCalls"] -= 1
            for key, target in (
                ("prompt_tokens", "promptTokens"),
                ("completion_tokens", "completionTokens"),
            ):
                value = response.usage.get(key, 0)
                if type(value) is int and value >= 0:
                    usage[target] += value
            if response.finish_reason != "stop":
                raise ModelError("ai_response_incomplete")
            validation = validate_decision(
                plan, decode(response.text), detail_bounds=crop["pixelBounds"] if crop else None
            )
            record.update(status=validation["status"], validation=validation)
            checkpoint(state)
            if validation["status"] != "reviewed":
                state["haltReason"] = "pdf_visual_review_unresolved"
                checkpoint(state)
                return None, state
        except (
            ModelError,
            PdfReviewImageError,
            VisualPixelError,
            PdfVisualReviewError,
            VisualGridError,
        ) as exc:
            reason = exc.code if hasattr(exc, "code") else str(exc)
            if record is None:
                record = {}
                state["pages"][str(page)] = record
            record.update(status="failed", reason=reason)
            state["haltReason"] = reason
            checkpoint(state)
            return None, state
    from .pdf_visual_apply import apply_page_reviews

    reviewed = apply_page_reviews(doc, list(state["pages"].values()))
    application = reviewed.provenance.get("pdfVisualReviewApplication", {})
    if application.get("status") != "applied":
        state["haltReason"] = application.get("reason", "pdf_visual_review_application_unresolved")
        checkpoint(state)
        return None, state
    checkpoint(state)
    return reviewed, state
