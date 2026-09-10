"""Synthetic review contracts, not actual OCR accuracy or quality approval."""

import hashlib
import io
import time
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from document_files.document_model.model import ObservationDocument
from document_files.interpretation import pdf_visual_plan as plan
from document_files.interpretation.pdf_visual_pixels import extract_visual_pixels


def example(extra=False):
    image = Image.new("RGB", (120, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((12, 12, 20, 18), fill="black")
    draw.rectangle((12, 60, 20, 66), fill="black")
    if extra:
        image.putpixel((110, 110), (254, 254, 254))
    out = io.BytesIO()
    image.save(out, format="PNG")
    rgb = hashlib.sha256(image.tobytes()).hexdigest()
    image.close()
    capture = {
        "sourceSha256": "a" * 64,
        "page_no": 1,
        "pixelSize": [120, 120],
        "pageSizeCanvasUnits": [40, 40],
        "pixelSha256": rgb,
        "fingerprint": "c" * 64,
    }
    doc = ObservationDocument(provenance={"sourceSha256": "a" * 64})
    for index, top in enumerate((4, 20)):
        doc.node(
            f"n{index}",
            f"item {index}",
            observationBasis="recognition",
            locator={
                "page": 1,
                "bbox": {"left": 4, "top": top, "right": 8, "bottom": top + 3, "origin": "TOPLEFT"},
            },
        )
    pixels = extract_visual_pixels(
        out.getvalue(),
        expected_rgb_sha256=rgb,
        expected_size=[120, 120],
        deadline=time.monotonic() + 10,
    )
    return doc, capture, pixels


def build(extra=False):
    doc, capture, pixels = example(extra)
    return plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)


def answer(value):
    return {
        "units": [{"id": u["id"], "decision": "source_text"} for u in value["units"]],
        "slots": [],
        "readingOrder": [b["id"] for b in value["blocks"]],
        "unrepresentedContent": False,
    }


def wire_response(value, decision):
    """Model fixture output follows plan order; checkpoints retain explicit IDs."""
    result = deepcopy(decision)
    inventories = {
        "units": [u["id"] for u in value["units"]],
        "slots": [s["id"] for s in value["slots"]],
    }
    if "imageReadProposal" in value:
        inventories.update(
            sourceChecks=value["imageReadProposal"]["sourceIds"],
            gridChecks=[g["id"] for g in value["imageReadProposal"]["grids"]],
        )
    for key, ids in inventories.items():
        choices = {v["id"]: v["decision"] for v in decision[key]}
        assert len(choices) == len(decision[key]) and set(choices) == set(ids)
        result[key] = [choices[i] for i in ids]
    return result


def test_exact_pixel_plan_and_independent_review_remain_distinct():
    doc, capture, pixels = example()
    before = deepcopy((doc, capture, pixels))
    result = plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)
    assert (doc, capture, pixels) == before
    assert len(result["sources"]) == len(result["units"]) == len(result["blocks"]) == 2
    assert result["foregroundPixelCount"] == 126
    assert result["precedences"] == [["o0", "o1"]]
    approved = plan.validate_decision(result, answer(result), detail_bounds=None)
    assert approved["status"] == "reviewed"
    assert approved["ocrTruthVerified"] is False
    assert "parts" not in plan.review_payload(result)["units"][0]


def test_actual_foreground_outside_sources_is_not_silently_dropped():
    value = build(extra=True)
    unmatched = [u for u in value["units"] if not u["sourceIds"]]
    assert len(unmatched) == 1 and unmatched[0]["pixelCount"] == 1
    decision = answer(value)
    with pytest.raises(plan.PdfVisualReviewError, match="without_source"):
        plan.validate_decision(value, decision, detail_bounds=None)
    decision["units"][-1]["decision"] = "unknown"
    assert plan.validate_decision(value, decision, detail_bounds=None)["status"] == "unresolved"


def test_connected_bounding_rectangle_is_not_all_owned_pixels():
    doc, capture, pixels = example()
    # Pixel extraction tests establish these disjoint exact components. Here the
    # source candidate overlaps a component's bbox but none of its actual pixels.
    pixels["components"] = [{"id": "border", "runs": [[0, 0, 120], [119, 0, 120]]}]
    pixels["foregroundPixelCount"] = 240
    result = plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)
    assert result["units"][0]["sourceIds"] == []
    assert result["units"][0]["onlyBoundaryPixels"] is False


@pytest.mark.parametrize("variant", ["duplicate", "omitted", "extra", "type", "order", "structure"])
def test_wrong_decision_cannot_pass(variant):
    value = build()
    decision = answer(value)
    if variant == "duplicate":
        decision["units"][1] = decision["units"][0]
    elif variant == "omitted":
        decision["units"].pop()
    elif variant == "extra":
        decision["complete"] = True
    elif variant == "type":
        decision["unrepresentedContent"] = 0
    elif variant == "order":
        decision["readingOrder"].reverse()
    else:
        decision["units"][0]["decision"] = "table_border"
    with pytest.raises(plan.PdfVisualReviewError):
        plan.validate_decision(value, decision, detail_bounds=None)


def test_fingerprint_and_observation_source_changes():
    value = build()
    decision = answer(value)
    value["sources"][0]["text"] = "changed"
    with pytest.raises(plan.PdfVisualReviewError, match="plan_changed"):
        plan.validate_decision(value, decision, detail_bounds=None)
    doc, capture, pixels = example()
    before = plan.observation_page_fingerprint(doc, 1)
    doc.nodes["n0"]["text"] += " changed"
    assert plan.observation_page_fingerprint(doc, 1) != before
    capture["sourceSha256"] = "b" * 64
    with pytest.raises(plan.PdfVisualReviewError, match="source_changed"):
        plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 1)


@pytest.mark.parametrize("text", ["", "legacy whole-page text"])
def test_legacy_page_projection_does_not_change_review_input(text):
    doc, capture, pixels = example()
    before = plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)
    legacy = {
        "sourceUnitType": "page",
        "semanticRole": "page",
        "sourceStructure": {"page": 1},
        "semantic": {"basis": "source_structure"},
        "derivation": {"method": "native_text"},
        "text": text,
    }
    doc.nodes["legacy"] = deepcopy(legacy)
    after = plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)
    assert after == before
    assert doc.nodes["legacy"] == legacy
    # A real additive observation, or an unfamiliar page node, stays bound.
    doc.nodes["legacy"]["observationBasis"] = "native_pdf"
    assert plan.observation_page_fingerprint(doc, 1) != before["sourceObservationFingerprint"]
    del doc.nodes["legacy"]["observationBasis"]
    doc.nodes["legacy"]["derivation"]["method"] = "unfamiliar"
    assert plan.observation_page_fingerprint(doc, 1) != before["sourceObservationFingerprint"]


@pytest.mark.parametrize(
    "evidence",
    ["pdfPageRenderCaptures", "recognitionCellPixelObservations", "recognitionCoordinateEvidence"],
)
def test_page_evidence_remains_bound(evidence):
    doc, _, _ = example()
    before = plan.observation_page_fingerprint(doc, 1)
    doc.provenance[evidence] = [{"page": 1, "changed": True}]
    assert plan.observation_page_fingerprint(doc, 1) != before


@pytest.mark.parametrize("version", ["v1", "v7", "v8"])
def test_previous_plan_version_cannot_be_accepted_by_rehashing(version):
    value = build()
    value["version"] = "document-files.pdf-visual-review." + version
    value["fingerprint"] = plan.digest({k: v for k, v in value.items() if k != "fingerprint"})
    with pytest.raises(plan.PdfVisualReviewError, match="version_incompatible"):
        plan.validate_decision(value, answer(value), detail_bounds=None)


def test_empty_slot_inventory_has_no_dummy_response_branch():
    value = build()
    schema = plan.output_schema(value)
    assert schema["properties"]["slots"] == {
        "type": "array",
        "items": {"type": "null"},
        "maxItems": 0,
    }
    assert "unused" not in str(schema)
    for key, inventory in (("units", "units"), ("readingOrder", "blocks")):
        assert schema["properties"][key]["minItems"] == len(value[inventory])
        assert schema["properties"][key]["maxItems"] == len(value[inventory])


@pytest.mark.parametrize("variant", ["exact", "text", "page", "ambiguous", "disjoint"])
def test_native_geometry_only_offers_unique_exact_text_candidates(variant):
    doc, capture, pixels = example()
    # A detached stroke lies just above the recognition box. Exact connected
    # components are the pixel helper's contract, separately tested from this join.
    pixels["components"].append({"id": "detached", "runs": [[10, 14, 16]]})
    pixels["foregroundPixelCount"] += 2
    bbox = {"left": 4, "top": 3, "right": 8, "bottom": 7, "origin": "TOPLEFT"}
    if variant == "disjoint":
        bbox.update(top=1, bottom=3.9)
    doc.node(
        "native",
        "item 0 " if variant == "text" else "item 0",
        role="line",
        observationBasis="native_pdf",
        locator={"page": 2 if variant == "page" else 1, "bbox": bbox},
    )
    if variant == "ambiguous":
        doc.nodes["other-native"] = deepcopy(doc.nodes["native"])
    before = deepcopy(doc)
    value = plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)
    assert doc == before
    assert value["foregroundPixelCount"] == 128
    unmatched = [u for u in value["units"] if not u["sourceIds"]]
    if variant == "exact":
        assert not unmatched
        assert value["sources"][0]["bounds"] == [12, 12, 24, 21]
        assert value["sources"][0]["additionalObservation"] == {
            "sourceRef": "native",
            "bounds": [12, 9, 24, 21],
        }
        assert plan.review_payload(value)["sources"][0]["additionalBounds"] == [12, 9, 24, 21]
        decision = answer(value)
        decision["units"][0]["decision"] = "unknown"
        assert plan.validate_decision(value, decision, detail_bounds=None)["status"] == "unresolved"
    else:
        assert len(unmatched) == 1 and unmatched[0]["pixelCount"] == 2


@pytest.mark.parametrize(
    "budget", ["cancelled", "timeout", "comparisons", "sources", "units", "runs"]
)
def test_limits_do_not_return_a_successful_truncated_plan(budget, monkeypatch):
    doc, capture, pixels = example()
    kwargs = {"deadline": time.monotonic() + 10}
    if budget == "cancelled":
        kwargs["cancelled"] = lambda: True
    elif budget == "timeout":
        kwargs["deadline"] = time.monotonic() - 1
    else:
        monkeypatch.setattr(
            plan,
            {
                "comparisons": "MAX_COMPARISONS",
                "sources": "MAX_SOURCES",
                "units": "MAX_UNITS",
                "runs": "MAX_SPLIT_RUNS",
            }[budget],
            1,
        )
    with pytest.raises(plan.PdfVisualReviewError):
        plan.build_page_plan(doc, capture, pixels, **kwargs)


def grid_plan(extra=False):
    from test_pdf_visual_grid import fixture

    from document_files.document_model.recognition_cell_observations import fingerprint

    def marks(colors):
        colors[35, 35] = (0, 0, 0)
        if extra:
            colors[100, 100] = (254, 254, 254)

    pixels, _ = fixture(alter=marks)
    height = 170 / 3
    capture = {
        "sourceSha256": "a" * 64,
        "page_no": 1,
        "pixelSize": [170, 170],
        "pageSizeCanvasUnits": [height, height],
        "pixelSha256": pixels["rgbSha256"],
        "fingerprint": "c" * 64,
    }
    doc = ObservationDocument(provenance={"sourceSha256": "a" * 64})

    def bbox(values):
        return {
            **dict(zip(("left", "top", "right", "bottom"), [v / 3 for v in values], strict=True)),
            "origin": "TOPLEFT",
        }

    doc.node(
        "text",
        "A",
        observationBasis="recognition",
        locator={"page": 1, "tableRef": "table", "bbox": bbox([34, 34, 37, 37])},
    )
    doc.tables["table"] = {
        "page": 1,
        "locator": {"bbox": bbox([20, 20, 142, 142])},
        "declaredRowCount": 2,
        "declaredColCount": 2,
        "unobservedCellCount": 3,
        "cells": [{"sourceRef": "text", "row": 0, "col": 0, "rowSpan": 1, "colSpan": 1}],
    }
    record = {
        "slots": [
            {
                "row": row,
                "col": col,
                "slotKey": f"{row}:{col}",
                "geometryStatus": "resolved",
                "measurementStatus": "measured",
                "fullPixelBox": [20 + 60 * col, 20 + 60 * row, 82 + 60 * col, 82 + 60 * row],
            }
            for row in range(2)
            for col in range(2)
        ],
        "tableCrop": {"pixelBounds": [0, 0, 170, 170]},
        "geometry": {
            "status": "verified_rectangular_grid",
            "horizontalLines": [[20, y, 122, 2] for y in (20, 80, 140)],
            "verticalLines": [[x, 20, 2, 122] for x in (20, 80, 140)],
        },
    }
    record["fingerprint"] = fingerprint(record)
    doc.provenance["recognitionCellPixelObservations"] = [
        {
            "page": 1,
            "sourceSha256": "a" * 64,
            "observations": [
                {
                    "structureAssociation": {
                        "status": "unique_geometry_correspondence",
                        "tableRef": "table",
                    },
                    "observation": record,
                    "validation": {
                        "status": "verified",
                        "observationFingerprint": record["fingerprint"],
                        "canvasPixelToOriginalPageAffine": [1 / 3, 0, 0, -1 / 3, 0, height],
                    },
                }
            ],
        }
    ]
    return plan.build_page_plan(doc, capture, pixels, deadline=time.monotonic() + 10)


def test_same_render_grid_drives_full_slot_and_keeps_every_pixel():
    value = grid_plan()
    assert len(value["slots"]) == 3
    assert value["slots"][-1]["bounds"] == [80, 80, 142, 142]
    decision = answer(value)
    for unit, chosen in zip(value["units"], decision["units"], strict=True):
        chosen["decision"] = "source_text" if unit["sourceIds"] else "table_border"
    decision["slots"] = [{"id": slot["id"], "decision": "empty"} for slot in value["slots"]]
    assert (
        plan.validate_decision(value, decision, detail_bounds=[0, 0, 170, 170])["status"]
        == "reviewed"
    )
    with pytest.raises(plan.PdfVisualReviewError, match="detail_missing"):
        plan.validate_decision(value, decision, detail_bounds=[82, 82, 140, 140])


def test_grid_measurement_timing_does_not_change_plan_identity(monkeypatch):
    from document_files.interpretation import pdf_visual_grid

    measure = pdf_visual_grid.measure_grid_candidates
    elapsed = iter([0.01, 100.0])

    def timed(*args, **kwargs):
        result = measure(*args, **kwargs)
        result["diagnostics"]["elapsedSeconds"] = next(elapsed)
        return result

    monkeypatch.setattr(pdf_visual_grid, "measure_grid_candidates", timed)
    first, second = grid_plan(), grid_plan()
    assert first == second
    assert "diagnostics" not in first["grid"]
    assert first["grid"]["grids"]


def test_one_faint_interior_pixel_cannot_be_a_table_border_or_empty_value():
    value = grid_plan(extra=True)
    unmatched = [u for u in value["units"] if not u["sourceIds"] and not u["onlyBoundaryPixels"]]
    assert len(unmatched) == 1 and unmatched[0]["pixelCount"] == 1
    decision = answer(value)
    for unit, chosen in zip(value["units"], decision["units"], strict=True):
        chosen["decision"] = "source_text" if unit["sourceIds"] else "table_border"
    decision["slots"] = [{"id": slot["id"], "decision": "empty"} for slot in value["slots"]]
    with pytest.raises(plan.PdfVisualReviewError, match="unproven_structural_pixels"):
        plan.validate_decision(value, decision, detail_bounds=[0, 0, 170, 170])
    next(v for v in decision["units"] if v["id"] == unmatched[0]["id"])["decision"] = "unknown"
    with pytest.raises(plan.PdfVisualReviewError, match="slot_not_empty"):
        plan.validate_decision(value, decision, detail_bounds=[0, 0, 170, 170])


def test_compact_wire_restores_plan_owned_ids_without_changing_checkpoint_decision():
    value = build()
    decision = answer(value)
    decision["units"][1]["decision"] = "unknown"
    wire = wire_response(value, decision)
    before = deepcopy(wire)
    assert wire["units"] == ["source_text", "unknown"]
    assert plan.decode_review_response(value, wire) == decision and wire == before
    assert (
        plan.validate_decision(value, plan.decode_review_response(value, wire), detail_bounds=None)[
            "status"
        ]
        == "unresolved"
    )
    wire["units"].reverse()
    assert plan.decode_review_response(value, wire)["units"][0] == {
        "id": value["units"][0]["id"],
        "decision": "unknown",
    }
    payload = plan.review_payload(value)
    assert "parts" not in payload["unitColumns"]
    assert (
        dict(zip(payload["unitColumns"], payload["units"][0], strict=True))["sourceIds"]
        == value["units"][0]["sourceIds"]
    )
    assert plan.output_schema(value)["properties"]["units"]["items"]["type"] == "string"


@pytest.mark.parametrize(
    "change", ["legacy", "omitted", "extra", "object", "null", "nonstring", "bad_choice"]
)
def test_compact_wire_rejects_old_or_malformed_decisions(change):
    value = build()
    wire = wire_response(value, answer(value))
    if change == "legacy":
        wire = answer(value)
    elif change == "omitted":
        wire["units"].pop()
    elif change == "extra":
        wire["complete"] = True
    elif change == "object":
        wire["units"] = {"u0": "source_text", "u1": "source_text"}
    elif change == "null":
        wire["slots"] = None
    elif change == "nonstring":
        wire["units"][0] = 1
    else:
        wire["units"][0] = "discard_noise"
    with pytest.raises(plan.PdfVisualReviewError):
        plan.validate_decision(value, plan.decode_review_response(value, wire), detail_bounds=None)


def test_rule_edge_cannot_be_selected_without_candidate_or_replace_missing_slot_evidence():
    value = build()
    decision = answer(value)
    decision["units"][0]["decision"] = "rule_edge"
    with pytest.raises(plan.PdfVisualReviewError, match="visual_rule_edge_without_candidate"):
        plan.validate_decision(value, decision, detail_bounds=None)
    value, detail = grid_plan(extra=True), [0, 0, 170, 170]
    # Even an offered edge does not make a missing slot empty. Keep the synthetic
    # proposal empty to exercise the unit/slot invariant independently of image reads.
    value["imageReadProposal"] = {"sourceIds": [], "grids": []}
    for unit in value["units"]:
        if not unit["onlyBoundaryPixels"] and not unit["sourceIds"]:
            unit["ruleEdgeTableRefs"] = ["table"]
    value["fingerprint"] = plan.digest({k: v for k, v in value.items() if k != "fingerprint"})
    decision = {
        "units": [
            {
                "id": u["id"],
                "decision": "source_text"
                if u["sourceIds"]
                else "table_border"
                if u["onlyBoundaryPixels"]
                else "rule_edge",
            }
            for u in value["units"]
        ],
        "slots": [{"id": s["id"], "decision": "empty"} for s in value["slots"]],
        "readingOrder": [b["id"] for b in value["blocks"]],
        "unrepresentedContent": False,
        "sourceChecks": [],
        "gridChecks": [],
    }
    with pytest.raises(plan.PdfVisualReviewError, match="visual_rule_context_not_displayed"):
        plan.validate_decision(value, decision, detail_bounds=detail)
