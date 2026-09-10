"""Finite, checkpointed PDF page review before semantic region planning."""

from __future__ import annotations

import time
from copy import deepcopy

from .backends import MANAGED_VISION_VERSION, InferenceRequest, ModelError
from .legacy_engine import decode, encode
from .pdf_image_projection import VERSION as PROJECTION_VERSION
from .pdf_image_projection import propose_image_read
from .pdf_image_read import MAX_OUTPUT_TOKENS as READ_MAX_OUTPUT_TOKENS
from .pdf_image_read import SYSTEM as READ_SYSTEM
from .pdf_image_read import VERSION as READ_VERSION
from .pdf_image_read import build_read_plan, read_payload, read_schema, validate_read
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
    require_proposal_measurements,
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
        "imageRead": READ_VERSION,
        "imageProjection": PROJECTION_VERSION,
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
    _validated_images(doc, plan, record["images"], validation["detailBounds"])
    return validation["status"]


def _validated_images(doc, plan, descriptor, detail_bounds):
    images = deepcopy(descriptor)
    require(
        isinstance(images.get("images"), list) and 1 <= len(images["images"]) <= 2,
        "visual_checkpoint_images_changed",
    )
    fingerprint = images.pop("fingerprint")
    images["usage"].pop("elapsedSeconds")
    require(fingerprint == digest(images), "visual_checkpoint_images_changed")
    require(
        images["sourceSha256"] == plan["sourceSha256"] == doc.provenance["sourceSha256"]
        and images["sourceCaptureFingerprint"] == plan["captureFingerprint"]
        and images["pageNo"] == plan["page"]
        and images["images"][0]["sourcePixelBounds"] == [0, 0, *plan["pixelSize"]]
        and (images["images"][1]["sourcePixelBounds"] if len(images["images"]) == 2 else None)
        == detail_bounds,
        "visual_checkpoint_images_changed",
    )


def _validated_read(doc, page, record):
    plan = record["plan"]
    require(
        record["status"] in {"running", "read", "unresolved", "failed"}
        and plan["version"] == READ_VERSION
        and plan["page"] == page
        and plan["sourceObservationFingerprint"] == observation_page_fingerprint(doc, page)
        and plan["fingerprint"] == digest({k: v for k, v in plan.items() if k != "fingerprint"}),
        "image_read_checkpoint_changed",
    )
    descriptors = record["images"]["images"]
    detail = descriptors[1]["sourcePixelBounds"] if len(descriptors) == 2 else None
    _validated_images(doc, plan, record["images"], detail)
    if record["status"] in {"read", "unresolved"}:
        validation = record["validation"]
        require(
            validation == validate_read(plan, validation["decision"], detail_bounds=detail)
            and record["status"] == validation["status"],
            "image_read_checkpoint_changed",
        )


def _response_usage(usage, response):
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
                record["status"]
                in {"read_pending", "reading", "running", "reviewed", "unresolved", "failed"},
                "visual_checkpoint_invalid",
            )
            if "imageRead" in record:
                _validated_read(doc, int(key), record["imageRead"])
                require(record["status"] != "reviewed", "image_read_not_applied")
                if "imageReview" in record:
                    proposal = propose_image_read(
                        doc,
                        record["imageRead"],
                        deadline=time.monotonic() + 30,
                        cancelled=cancelled,
                    )
                    image_review = record["imageReview"]
                    require(
                        image_review["status"] in {"running", "reviewed", "unresolved", "failed"},
                        "image_projection_checkpoint_changed",
                    )
                    if image_review["status"] in {"reviewed", "unresolved"}:
                        require(
                            _validated_record(proposal, int(key), image_review)
                            == image_review["status"],
                            "image_projection_checkpoint_changed",
                        )
                    else:
                        require(
                            image_review["plan"]["sourceObservationFingerprint"]
                            == observation_page_fingerprint(proposal, int(key)),
                            "image_projection_checkpoint_changed",
                        )
                if "validation" in record:
                    require(
                        _validated_record(doc, int(key), record) == "unresolved",
                        "image_read_review_changed",
                    )
                if record["imageRead"]["status"] in {"read", "unresolved"}:
                    require(record["status"] == "unresolved", "image_read_not_applied")
            if record["status"] == "reading":
                require(record["imageRead"]["status"] == "running", "image_read_checkpoint_changed")
            if record["status"] == "read_pending":
                require(
                    "imageRead" not in record
                    and record["readSourceObservationFingerprint"]
                    == observation_page_fingerprint(doc, int(key)),
                    "image_read_checkpoint_changed",
                )
                capture = pages[int(key)]["capture"]
                descriptors = record["readImages"]["images"]
                _validated_images(
                    doc,
                    {
                        "sourceSha256": capture["sourceSha256"],
                        "captureFingerprint": capture["fingerprint"],
                        "page": int(key),
                        "pixelSize": capture["pixelSize"],
                    },
                    record["readImages"],
                    descriptors[1]["sourcePixelBounds"] if len(descriptors) == 2 else None,
                )
                if "validation" in record:
                    require(
                        _validated_record(doc, int(key), record) == "unresolved",
                        "image_read_review_changed",
                    )
            if record["status"] == "reviewed" or (
                record["status"] == "unresolved" and "imageRead" not in record
            ):
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

    def read_unresolved(page, record, images, cause):
        """One additional bounded reading. It does not clear the review failure."""
        record["reviewReason"] = cause
        attempt = None

        def pending(reason):
            record.update(
                status="read_pending",
                reason=reason,
                readImages=images.descriptor,
                readSourceObservationFingerprint=observation_page_fingerprint(doc, page),
            )
            state["haltReason"] = reason
            checkpoint(state)

        try:
            reason = stop_reason()
            if reason:
                pending(reason)
                return
            plan = build_read_plan(
                doc, pages[page]["capture"], deadline=deadline, cancelled=cancelled
            )
            if plan is None:
                record.update(
                    status="unresolved" if "validation" in record else "failed", reason=cause
                )
                state["haltReason"] = cause
                checkpoint(state)
                return
            contract = read_schema(plan)
            payload = encode({**read_payload(plan), "outputContract": contract})
            if len(READ_SYSTEM) + len(payload) > context_chars:
                raise ModelError("pdf_image_read_context_budget_exceeded")
            reason = stop_reason()
            if reason:
                pending(reason)
                return
            attempt = {"status": "running", "plan": plan, "images": images.descriptor}
            record.update(status="reading", imageRead=attempt)
            record.pop("reason", None)
            state.pop("haltReason", None)
            usage["modelCalls"] += 1
            usage["unreportedUsageCalls"] += 1
            checkpoint(state)
            response = client.infer(
                InferenceRequest(
                    messages=[
                        {"role": "system", "content": READ_SYSTEM},
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": payload}, *images.content_parts()],
                        },
                    ],
                    output_schema=contract,
                    max_output_tokens=min(
                        READ_MAX_OUTPUT_TOKENS,
                        getattr(client, "max_output_tokens", None) or READ_MAX_OUTPUT_TOKENS,
                    ),
                    timeout=deadline - time.monotonic(),
                    cancelled=cancelled,
                )
            )
            _response_usage(usage, response)
            if response.finish_reason != "stop":
                raise ModelError("ai_response_incomplete")
            descriptors = images.descriptor["images"]
            detail = descriptors[1]["sourcePixelBounds"] if len(descriptors) == 2 else None
            validation = validate_read(plan, decode(response.text), detail_bounds=detail)
            attempt.update(status=validation["status"], validation=validation)
            record.update(status="unresolved", reason="pdf_image_read_requires_review")
        except (ModelError, PdfVisualReviewError) as exc:
            reason = exc.code if hasattr(exc, "code") else str(exc)
            if attempt is not None:
                attempt.update(status="failed", reason=reason)
            record.update(status="failed", reason=reason)
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            if attempt is not None:
                attempt.update(status="failed", reason="pdf_image_read_input_invalid")
            record.update(status="failed", reason="pdf_image_read_input_invalid")
        state["haltReason"] = record["reason"]
        checkpoint(state)

    def review_reading(page, record, prepared=None):
        """A separate, counted text/grid review; never replay a completed attempt."""
        nonlocal working
        attempt = record.get("imageReview")
        if "imageReviewPreparationFailure" in record:
            state["haltReason"] = record["imageReviewPreparationFailure"]
            checkpoint(state)
            return False
        if attempt is not None and attempt["status"] != "reviewed":
            state["haltReason"] = (
                "pdf_image_projection_review_interrupted"
                if attempt["status"] == "running"
                else attempt.get("reason", "pdf_image_projection_review_unresolved")
            )
            checkpoint(state)
            return False
        try:
            if attempt is None:
                reason = stop_reason()
                if reason:
                    state["haltReason"] = reason
                    checkpoint(state)
                    return False
            proposal = propose_image_read(
                working, record["imageRead"], deadline=deadline, cancelled=cancelled
            )
            if attempt is not None:
                require(
                    _validated_record(proposal, page, attempt) == "reviewed",
                    "image_projection_checkpoint_changed",
                )
                working = proposal
                return True
            descriptors = record["imageRead"]["images"]["images"]
            detail = descriptors[1]["sourcePixelBounds"] if len(descriptors) == 2 else None
            images = prepared or prepare_pdf_review_images(
                content,
                pages[page]["capture"],
                crop={"pixelBounds": detail, "kind": "detail", "slotKey": None} if detail else None,
                deadline=deadline,
                cancelled=cancelled,
            )
            require(
                images.descriptor["fingerprint"] == record["imageRead"]["images"]["fingerprint"],
                "image_projection_images_changed",
            )
            capture = pages[page]["capture"]
            pixels = extract_visual_pixels(
                images.png_images[0],
                expected_rgb_sha256=capture["pixelSha256"],
                expected_size=capture["pixelSize"],
                deadline=deadline,
                cancelled=cancelled,
            )
            plan = build_page_plan(
                proposal, capture, pixels, deadline=deadline, cancelled=cancelled
            )
            require_proposal_measurements(plan)
            contract = output_schema(plan)
            payload = encode({**review_payload(plan), "outputContract": contract})
            if len(SYSTEM) + len(payload) > context_chars:
                raise ModelError("pdf_image_projection_context_budget_exceeded")
            reason = stop_reason()
            if reason:
                state["haltReason"] = reason
                checkpoint(state)
                return False
            attempt = {"status": "running", "plan": plan, "images": images.descriptor}
            record["imageReview"] = attempt
            usage["modelCalls"] += 1
            usage["unreportedUsageCalls"] += 1
            state.pop("haltReason", None)
            checkpoint(state)
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
            _response_usage(usage, response)
            if response.finish_reason != "stop":
                raise ModelError("ai_response_incomplete")
            validation = validate_decision(plan, decode(response.text), detail_bounds=detail)
            attempt.update(status=validation["status"], validation=validation)
            if validation["status"] == "reviewed":
                working = proposal
                checkpoint(state)
                return True
            reason = "pdf_image_projection_review_unresolved"
        except (
            ModelError,
            PdfReviewImageError,
            VisualPixelError,
            PdfVisualReviewError,
            VisualGridError,
        ) as exc:
            reason = exc.code if hasattr(exc, "code") else str(exc)
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            reason = "pdf_image_projection_input_invalid"
        if attempt is not None and attempt["status"] == "running":
            attempt.update(status="failed", reason=reason)
        elif attempt is None:
            record["imageReviewPreparationFailure"] = reason
        state["haltReason"] = reason
        checkpoint(state)
        return False

    working = doc
    for page in sorted(pages):
        old = state["pages"].get(str(page))
        if old is not None and old["status"] == "read_pending":
            reason = stop_reason()
            if reason:
                state["haltReason"] = reason
                checkpoint(state)
                return None, state
            try:
                descriptors = old["readImages"]["images"]
                crop = (
                    {
                        "pixelBounds": descriptors[1]["sourcePixelBounds"],
                        "kind": "detail",
                        "slotKey": None,
                    }
                    if len(descriptors) == 2
                    else None
                )
                images = prepare_pdf_review_images(
                    content,
                    pages[page]["capture"],
                    crop=crop,
                    deadline=deadline,
                    cancelled=cancelled,
                )
                require(
                    images.descriptor["fingerprint"] == old["readImages"]["fingerprint"],
                    "image_read_resume_images_changed",
                )
                read_unresolved(page, old, images, old["reviewReason"])
            except (PdfReviewImageError, PdfVisualReviewError) as exc:
                reason = exc.code if hasattr(exc, "code") else str(exc)
                old.update(status="failed", reason=reason)
                state["haltReason"] = reason
                checkpoint(state)
            if old.get("imageRead", {}).get("status") == "read" and review_reading(
                page, old, images
            ):
                continue
            return None, state
        if old is not None and old.get("imageRead", {}).get("status") == "read":
            if review_reading(page, old):
                continue
            return None, state
        if old is not None:
            if old["status"] == "reviewed":
                continue
            state["haltReason"] = (
                "pdf_image_read_interrupted"
                if old["status"] == "reading"
                else "pdf_visual_review_interrupted"
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
        record = images = None
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
            require_proposal_measurements(plan)
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
            _response_usage(usage, response)
            if response.finish_reason != "stop":
                raise ModelError("ai_response_incomplete")
            validation = validate_decision(
                plan, decode(response.text), detail_bounds=crop["pixelBounds"] if crop else None
            )
            record.update(status=validation["status"], validation=validation)
            checkpoint(state)
            if validation["status"] != "reviewed":
                read_unresolved(page, record, images, "pdf_visual_review_unresolved")
                if record.get("imageRead", {}).get("status") == "read" and review_reading(
                    page, record, images
                ):
                    continue
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
            if reason == "visual_slot_inventory_incomplete" and images is not None:
                read_unresolved(page, record, images, reason)
                if record.get("imageRead", {}).get("status") == "read" and review_reading(
                    page, record, images
                ):
                    continue
            return None, state
    from .pdf_visual_apply import apply_page_reviews

    reviewed = apply_page_reviews(
        working, [record.get("imageReview", record) for record in state["pages"].values()]
    )
    application = reviewed.provenance.get("pdfVisualReviewApplication", {})
    if application.get("status") != "applied":
        state["haltReason"] = application.get("reason", "pdf_visual_review_application_unresolved")
        checkpoint(state)
        return None, state
    checkpoint(state)
    return reviewed, state
