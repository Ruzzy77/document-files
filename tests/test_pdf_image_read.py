"""Synthetic image-reading contracts; not a renderer or model quality test."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from document_files.document_model.recognition_cell_observations import fingerprint
from document_files.interpretation import pdf_image_read as reading
from document_files.interpretation import pdf_visual_runner as runner
from document_files.interpretation.backends import MANAGED_VISION_VERSION, ModelError
from document_files.interpretation.pdf_review_images import PdfReviewImages
from document_files.interpretation.pdf_visual_plan import PdfVisualReviewError, digest, review_crop


def fixture():
    from test_pdf_visual_apply import fixture as original

    doc, _ = original()
    capture = doc.provenance["pdfPageRenderCaptures"][0]["capture"]
    item = doc.provenance["recognitionCellPixelObservations"][0]["observations"][0]
    record = item["observation"]
    record.update(
        status="captured",
        canvas={"size": [90, 90], "mode": "RGB", "sha256": "c" * 64},
        tableCrop={"pixelBounds": [0, 0, 60, 60]},
        geometry={"rows": 3, "cols": 2, "status": "verified_rectangular_grid"},
        slots=[
            {
                "row": row,
                "col": col,
                "slotKey": f"slot-{row}-{col}",
                "geometryStatus": "resolved",
                "measurementStatus": "measured",
                "fullPixelBox": [col * 30, row * 20, (col + 1) * 30, (row + 1) * 20],
            }
            for row in range(3)
            for col in range(2)
        ],
    )
    record["fingerprint"] = fingerprint(record)
    item.update(
        observationStatus="verified",
        sourceCoordinateStatus="verified",
        structureAssociation={"status": "unlinked"},
    )
    item["validation"]["observationFingerprint"] = record["fingerprint"]
    return doc, capture


def make_plan(doc, capture):
    return reading.build_read_plan(doc, capture, deadline=time.monotonic() + 30)


def strip_images(plan):
    """Synthetic lossless strip images matching the plan's strips, for runner tests."""
    strips = reading.line_strips(plan)
    descriptor = {
        "sourceSha256": plan["sourceSha256"],
        "sourceCaptureFingerprint": plan["captureFingerprint"],
        "pageNo": plan["page"],
        "usage": {"elapsedSeconds": 0.01},
        "images": [
            {
                "requestedPurpose": "line_strip",
                "requestedSlotKey": s["id"],
                "sourcePixelBounds": s["pageBounds"],
            }
            for s in strips
        ],
    }
    descriptor["fingerprint"] = digest({**descriptor, "usage": {}})
    return PdfReviewImages(tuple(b"synthetic-strip" for _ in strips), descriptor)


def synthetic_sheet(sheet, strips, **kwargs):
    """Runner tests never rasterize: a synthetic sheet image with the real layout."""
    return b"synthetic-sheet", {
        "version": "document-files.pdf-line-sheet.v1",
        "id": sheet["id"],
        "requestedPurpose": "line_sheet",
        "pixelSize": sheet["pixelSize"],
        "pngSha256": "0" * 64,
        "sourceStrips": strips.descriptor["fingerprint"],
        "strips": sheet["strips"],
    }


def requested_ids(request):
    return [e["id"] for e in json.loads(request.messages[1]["content"][0]["text"])["entries"]]


def part_answer(plan, request):
    """The full synthetic answer restricted to the entries this request asks for."""
    ids = set(requested_ids(request))
    return {"entries": [e for e in answer(plan)["entries"] if e["id"] in ids]}


def answer(plan):
    strings = ["Item", "Length", "A-01", "0.020", "B-02", "8.25", "Unit: mm"]
    return {
        "entries": [
            {"id": e["id"], "state": "text", "text": strings[i]}
            for i, e in enumerate(plan["entries"])
        ]
    }


def test_unlinked_grid_and_text_regions_do_not_supply_reference_answers_or_replace_structure():
    doc, capture = fixture()
    before = deepcopy(doc)
    plan = make_plan(doc, capture)
    assert len(plan["entries"]) == 7
    assert plan["grids"][0]["rows"] == 3
    assert doc.tables["table"]["declaredRowCount"] == 1
    assert doc == before
    payload = json.dumps(reading.read_payload(plan))
    assert "foot" not in payload and "sourceRef" not in payload and '"text"' not in payload
    assert review_crop(doc, capture)["pixelBounds"] == [0, 0, 72, 72]
    validation = reading.validate_read(plan, answer(plan), detail_bounds=[0, 0, 90, 90])
    assert validation["status"] == "read" and validation["ocrTruthVerified"] is False
    summary = reading.candidate_summary({"plan": plan, "validation": validation})
    assert summary["entries"][3]["text"] == "0.020"
    assert summary["entries"][2]["text"] == "A-01"
    assert summary["applicationStatus"] == "requires_text_and_structure_review"
    assert doc == before


def test_read_contract_binds_the_state_to_the_literal_string():
    from jsonschema import Draft202012Validator

    from document_files.interpretation.backends import _local_grammar_schema, _strict_wire_schema

    assert reading.VERSION == "document-files.pdf-image-read.v5"
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    contract = reading.read_schema(plan, detail_bounds=[0, 0, 90, 90])
    branches = contract["properties"]["entries"]["items"]["anyOf"]
    ids = [e["id"] for e in plan["entries"]]
    cells = [e["id"] for e in plan["entries"] if e["kind"] == "cell"]
    assert [b["properties"]["state"]["const"] for b in branches] == ["text", "empty", "uncertain"]
    assert all(list(b["properties"]) == ["id", "text", "state"] for b in branches)
    assert [b["properties"]["id"]["enum"] for b in branches] == [ids, cells, ids]
    assert all(b["additionalProperties"] is False for b in branches)
    # Only cells entirely inside the detail are offered the empty branch at all.
    partial = reading.read_schema(plan, detail_bounds=[0, 0, 60, 40])
    assert partial["properties"]["entries"]["items"]["anyOf"][1]["properties"]["id"]["enum"] == [
        "i0",
        "i1",
        "i2",
        "i3",
    ]
    without = reading.read_schema(plan, detail_bounds=None)
    assert [
        b["properties"]["state"]["const"]
        for b in without["properties"]["entries"]["items"]["anyOf"]
    ] == ["text", "uncertain"]
    text, empty, uncertain = (b["properties"]["text"] for b in branches)
    assert text == {"type": "string", "maxLength": reading.MAX_TEXT_CHARS, "minLength": 1}
    assert empty == {"type": "string", "const": ""}
    assert uncertain == {"type": "string", "maxLength": reading.MAX_TEXT_CHARS}

    def reply(**first):
        value = answer(plan)
        value["entries"][0] = {"id": ids[0], **first}
        return value

    wire = _strict_wire_schema(_local_grammar_schema(contract))
    assert wire["properties"]["entries"]["items"]["anyOf"][1]["properties"]["text"] == {
        "type": "string",
        "enum": [""],
    }
    for schema in (contract, wire):
        valid = Draft202012Validator(schema).is_valid
        assert valid(reply(text="A-01", state="text"))
        assert valid(reply(text="", state="empty"))
        assert valid(reply(text="", state="uncertain"))
        assert valid(reply(text="0.0?", state="uncertain"))
        # The combinations that halted the first two GPU whole-path runs cannot be
        # generated: text with nothing read, and empty for a text line.
        assert not valid(reply(text="", state="text"))
        assert not valid(reply(text="0", state="empty"))
        assert not valid(reply(text="A", state="inferred"))
        assert not valid(reply(text="A" * (reading.MAX_TEXT_CHARS + 1), state="text"))
        line = answer(plan)
        line["entries"][6] = {"id": ids[6], "text": "", "state": "empty"}
        assert plan["entries"][6]["kind"] == "text_region" and not valid(line)
    # Whitespace-only text still fails product validation, not only the grammar.
    with pytest.raises(PdfVisualReviewError):
        reading.validate_read(plan, reply(text=" ", state="text"), detail_bounds=[0, 0, 90, 90])


def test_text_fragments_on_one_visual_line_form_one_entry_in_reading_order():
    doc, capture = fixture()

    def box(x, y, r, b):
        return {"left": x, "top": y, "right": r, "bottom": b, "origin": "TOPLEFT"}

    # foot is [0, 60, 30, 75] px. near: 6 px gap on the same line (height 15);
    # far: 24 px gap on the same line; below: the next line.
    for ref, bbox in (
        ("below", box(0, 26, 10, 29)),
        ("far", box(28, 20, 30, 25)),
        ("near", box(12, 20, 20, 25)),
    ):
        doc.node(ref, ref, observationBasis="recognition", locator={"page": 1, "bbox": bbox})
        doc.regions.append(
            {"id": f"{ref}-region", "nodeIds": [ref], "bindingIds": [], "contextNodeIds": []}
        )
    plan = make_plan(doc, capture)
    lines = [e for e in plan["entries"] if e["kind"] == "text_region"]
    assert [e["sourceRefs"] for e in lines] == [["foot", "near"], ["far"], ["below"]]
    assert lines[0]["bounds"] == [0, 60, 60, 75] and lines[0]["sourceBounds"] == [0, 20, 20, 25]
    assert [e["id"] for e in lines] == ["i6", "i7", "i8"]
    payload = json.dumps(reading.read_payload(plan))
    assert "sourceRef" not in payload and "near" not in payload and "sourceBounds" not in payload
    assert reading.same_visual_line([0, 0, 10, 10], [20, 0, 30, 10])
    assert not reading.same_visual_line([0, 0, 10, 10], [21, 0, 30, 10])
    assert not reading.same_visual_line([0, 0, 10, 10], [0, 6, 10, 16])
    assert plan == make_plan(doc, capture)


@pytest.mark.parametrize(
    "state,text,status", [("uncertain", "0.0?", "unresolved"), ("empty", "", "read")]
)
def test_uncertainty_and_empty_candidates_are_not_final_values(state, text, status):
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    value = answer(plan)
    value["entries"][0].update(state=state, text=text)
    result = reading.validate_read(plan, value, detail_bounds=[0, 0, 90, 90])
    assert result["status"] == status and result["independentQualityApproval"] is False
    assert result["decision"]["entries"][0]["text"] == text


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "missing",
        "extra",
        "state",
        "blank_text",
        "invented_empty",
        "empty_outside",
        "control",
        "surrogate",
        "oversize",
        "plan_changed",
    ],
)
def test_invalid_or_unbounded_readings_are_rejected(change):
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    value, detail = answer(plan), [0, 0, 90, 90]
    if change == "duplicate":
        value["entries"][1]["id"] = "i0"
    elif change == "missing":
        value["entries"].pop()
    elif change == "extra":
        value["complete"] = True
    elif change == "state":
        value["entries"][0]["state"] = "inferred"
    elif change == "blank_text":
        value["entries"][0]["text"] = " "
    elif change == "invented_empty":
        value["entries"][0].update(state="empty", text="0")
    elif change == "empty_outside":
        value["entries"][0].update(state="empty", text="")
        detail = [1, 1, 90, 90]
    elif change == "control":
        value["entries"][0]["text"] = "A\x00"
    elif change == "surrogate":
        value["entries"][0]["text"] = "\ud800"
    elif change == "oversize":
        value["entries"][0]["text"] = "A" * (reading.MAX_TEXT_CHARS + 1)
    elif change == "plan_changed":
        plan["entries"][0]["bounds"][0] += 1
    with pytest.raises(PdfVisualReviewError):
        reading.validate_read(plan, value, detail_bounds=detail)


def test_source_inventory_deadline_cancel_and_count_limits(monkeypatch):
    doc, capture = fixture()
    with pytest.raises(PdfVisualReviewError, match="timeout"):
        reading.build_read_plan(doc, capture, deadline=time.monotonic() - 1)
    with pytest.raises(PdfVisualReviewError, match="cancelled"):
        reading.build_read_plan(
            doc, capture, deadline=time.monotonic() + 30, cancelled=lambda: True
        )
    monkeypatch.setattr(reading, "MAX_ENTRIES", 5)
    with pytest.raises(PdfVisualReviewError, match="budget"):
        make_plan(doc, capture)


def setup_runner(monkeypatch, failure=None):
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    descriptors = {
        "sourceSha256": plan["sourceSha256"],
        "sourceCaptureFingerprint": plan["captureFingerprint"],
        "pageNo": 1,
        "usage": {"elapsedSeconds": 0.01},
        "images": [{"sourcePixelBounds": [0, 0, 90, 90]}, {"sourcePixelBounds": [0, 0, 72, 72]}],
    }
    descriptors["fingerprint"] = digest({**descriptors, "usage": {}})
    images = PdfReviewImages((b"synthetic-full", b"synthetic-detail"), descriptors)
    strips = strip_images(plan)
    calls, checkpoints = {"render": 0, "pixel": 0, "model": 0}, []
    usage = {
        "modelCalls": 0,
        "unreportedUsageCalls": 0,
        "promptTokens": 0,
        "completionTokens": 0,
        "elapsedSeconds": 0.0,
    }

    def render(*a, **kw):
        calls["render"] += 1
        return images

    def pixels(*a, **kw):
        calls["pixel"] += 1
        if failure == "budget_before_read":
            usage["modelCalls"] = 1
        return {}

    def review(*a, **kw):
        raise PdfVisualReviewError("visual_slot_inventory_incomplete")

    monkeypatch.setattr(runner, "prepare_pdf_review_images", render)
    monkeypatch.setattr(runner, "prepare_pdf_line_strips", lambda *a, **kw: strips)
    monkeypatch.setattr(runner, "compose_line_sheet", synthetic_sheet)
    monkeypatch.setattr(runner, "extract_visual_pixels", pixels)
    monkeypatch.setattr(runner, "build_page_plan", review)

    class Client:
        identity = {"vision": {"version": MANAGED_VISION_VERSION}}
        max_output_tokens = 1500

        def infer(self, request):
            calls["model"] += 1
            assert request.messages[0]["content"] == reading.SYSTEM
            assert request.max_output_tokens == 1500 and request.timeout > 0
            assert checkpoints[-1]["pages"]["1"]["status"] == "reading"
            assert usage["unreportedUsageCalls"] == 1
            if failure == "timeout":
                raise ModelError("ai_timeout")
            value = part_answer(plan, request)
            if failure == "unknown":
                value["entries"][0].update(state="uncertain", text="I?")
            return SimpleNamespace(
                text=json.dumps(value),
                finish_reason="length" if failure == "length" else "stop",
                usage={"prompt_tokens": 100, "completion_tokens": 20},
            )

    def run(*, restore=None, max_calls=2, expired=False, cancelled=None, context_chars=16000):
        return runner.review_pdf_pages(
            b"synthetic",
            doc,
            client=Client(),
            usage=usage,
            max_calls=max_calls,
            deadline=time.monotonic() + (-1 if expired else 30),
            context_chars=context_chars,
            checkpoint=lambda s: checkpoints.append(deepcopy(s)),
            restore=restore,
            cancelled=cancelled,
        )

    return doc, calls, checkpoints, usage, run


def test_reading_attempt_is_counted_and_preserved_without_applying_or_repeating(monkeypatch):
    doc, calls, checkpoints, usage, run = setup_runner(monkeypatch)
    before = deepcopy(doc)
    result, state = run()
    assert result is None and doc == before
    # Cells over page and detail, then text lines over lossless strips: two parts.
    assert calls == {"render": 1, "pixel": 1, "model": 2}
    record = state["pages"]["1"]
    assert record["imageRead"]["status"] == "read"
    assert [r["kind"] for r in record["imageRead"]["requests"]] == ["cells", "lines"]
    assert record["imageRead"]["lineImages"]["images"][0]["requestedPurpose"] == "line_strip"
    assert state["haltReason"] == "model_call_budget_exceeded"
    assert usage["modelCalls"] == 2 and usage["unreportedUsageCalls"] == 0
    assert "data:image" not in json.dumps(checkpoints)
    assert run(restore=state)[0] is None
    assert calls == {"render": 1, "pixel": 1, "model": 2}
    interrupted = next(s for s in checkpoints if s["pages"].get("1", {}).get("status") == "reading")
    assert run(restore=interrupted)[1]["haltReason"] == "pdf_image_read_interrupted"
    assert calls["model"] == 2


@pytest.mark.parametrize(
    "failure,code,spent",
    [
        ("timeout", "ai_timeout", 1),
        ("length", "ai_response_incomplete", 1),
        ("unknown", "pdf_image_read_requires_review", 2),
    ],
)
def test_failed_or_uncertain_reading_does_not_become_complete(monkeypatch, failure, code, spent):
    _, calls, _, _, run = setup_runner(monkeypatch, failure)
    result, state = run()
    assert result is None and state["haltReason"] == code
    assert run(restore=state)[0] is None and calls["model"] == spent


def test_unattempted_read_can_resume_without_replaying_the_review(monkeypatch):
    _, calls, _, usage, run = setup_runner(monkeypatch, "budget_before_read")
    result, state = run(max_calls=1)
    assert result is None and state["pages"]["1"]["status"] == "read_pending"
    assert calls["model"] == 0
    assert run(restore=state, max_calls=1)[0] is None and calls["render"] == 1
    # Both parts of one attempt must fit the remaining budget; two calls cannot.
    assert run(restore=state, max_calls=2)[1]["pages"]["1"]["status"] == "read_pending"
    assert calls["model"] == 0
    result, resumed = run(restore=state, max_calls=3)
    assert result is None and resumed["haltReason"] == "model_call_budget_exceeded"
    assert calls == {"render": 3, "pixel": 1, "model": 2}
    assert usage["modelCalls"] == 3  # The saved earlier call was not reset.


@pytest.mark.parametrize("change", ["text", "source", "image", "promote"])
def test_changed_read_checkpoint_or_false_completion_is_rejected(monkeypatch, change):
    doc, calls, _, _, run = setup_runner(monkeypatch)
    _, state = run()
    read = state["pages"]["1"]["imageRead"]
    if change == "text":
        read["validation"]["decision"]["entries"][0]["text"] = "Changed"
    elif change == "source":
        doc.nodes["foot"]["text"] = "Changed"
    elif change == "image":
        read["images"]["images"][0]["sourcePixelBounds"][2] -= 1
    elif change == "promote":
        state["pages"]["1"]["status"] = "reviewed"
    with pytest.raises(ValueError, match="incompatible"):
        run(restore=state)
    assert calls["model"] == 2


def test_context_budget_stops_before_the_additional_model_call(monkeypatch):
    _, calls, _, _, run = setup_runner(monkeypatch)
    _, state = run(context_chars=1)
    assert state["haltReason"] == "pdf_image_read_context_budget_exceeded"
    assert calls["model"] == 0


def test_engine_returns_additional_readings_in_partial_result_and_resumes_without_new_calls(
    monkeypatch,
):
    import io

    from document_files.analysis import AnalysisInput, AnalysisJob
    from document_files.interpretation import engine
    from document_files.interpretation.contracts import ExtractionOptions

    doc, calls, _, _, _ = setup_runner(monkeypatch)
    plan = make_plan(doc, doc.provenance["pdfPageRenderCaptures"][0]["capture"])
    before = deepcopy(doc)
    checkpoints, observations = [], []

    def infer(request):
        calls["model"] += 1
        assert request.messages[0]["content"] == reading.SYSTEM
        assert checkpoints[-1]["phase"] == "reviewing_pdf"
        assert checkpoints[-1]["usage"]["modelCalls"] == calls["model"]
        return SimpleNamespace(
            text=json.dumps(part_answer(plan, request)),
            finish_reason="stop",
            usage={"prompt_tokens": 100, "completion_tokens": 20},
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
        observations.append(True)
        return deepcopy(doc)

    monkeypatch.setattr(engine, "observe_document", observe)
    content = b"%PDF-synthetic-image-reading"
    job = AnalysisJob(job_id="image-read", input=AnalysisInput.from_bytes(content, format_id="pdf"))
    options = ExtractionOptions(reconstructionContext=False, maxModelCalls=2)
    result = engine.extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=client,
        options=options,
        checkpoint=lambda s: checkpoints.append(deepcopy(s)),
    )
    candidates = result["provenance"]["observation"]["pdfImageReadCandidates"]
    assert candidates[0]["entries"][3]["text"] == "0.020"
    assert result["extraction"]["status"] == "partial" and result["data"] is None
    assert result["document"]["nodes"] == before.nodes
    assert result["document"]["structure"]["tables"] == before.tables
    assert doc == before and calls["model"] == 2 and len(observations) == 1
    assert checkpoints[-1]["identity"]["pdfVisualReview"]["imageRead"] == reading.VERSION
    again = engine.extract_schema_from_stream(
        job, io.BytesIO(content), model_client=client, options=options, restore=checkpoints[-1]
    )
    assert again["provenance"]["observation"]["pdfImageReadCandidates"] == candidates
    assert again["data"] is None and calls["model"] == 2 and len(observations) == 1


def test_cells_and_lines_are_read_in_two_bounded_requests_with_line_sheets():
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    assert reading.VERSION == "document-files.pdf-image-read.v5"
    strips = reading.line_strips(plan)
    line = next(e for e in plan["entries"] if e["kind"] == "text_region")
    assert [s["entryIds"] for s in strips] == [[line["id"]]]
    b = line["bounds"]
    assert strips[0]["pageBounds"] == [
        max(0, b[0] - reading.STRIP_MARGIN),
        max(0, b[1] - reading.STRIP_MARGIN),
        min(plan["pixelSize"][0], b[2] + reading.STRIP_MARGIN),
        min(plan["pixelSize"][1], b[3] + reading.STRIP_MARGIN),
    ]
    cells, lines = reading.read_requests(plan, detail_bounds=[0, 0, 90, 90])
    assert cells["kind"] == "cells" and len(cells["entryIds"]) == 6
    assert cells["payload"]["grids"] and all(
        e["kind"] == "cell" for e in cells["payload"]["entries"]
    )
    branches = cells["contract"]["properties"]["entries"]["items"]["anyOf"]
    assert [br["properties"]["state"]["const"] for br in branches] == ["text", "empty", "uncertain"]
    assert branches[0]["properties"]["id"]["enum"] == cells["entryIds"]
    assert lines["kind"] == "lines" and lines["entryIds"] == [line["id"]]
    (sheet,) = reading.line_sheets(plan)
    assert lines["sheet"] == sheet and sheet["strips"][0]["label"] == "1"
    placed = sheet["strips"][0]
    o, sb = placed["pageBounds"], placed["sheetBounds"]
    assert (
        sb[0] == reading.SHEET_MARGIN + reading.SHEET_LABEL_WIDTH and sb[1] == reading.SHEET_MARGIN
    )
    entry = lines["payload"]["entries"][0]
    assert entry["image"] == 1 and entry["label"] == "1"
    assert entry["imageBounds"] == [
        b[0] - o[0] + sb[0],
        b[1] - o[1] + sb[1],
        b[2] - o[0] + sb[0],
        b[3] - o[1] + sb[1],
    ]
    image = lines["payload"]["images"][0]
    assert image["kind"] == "line_sheet" and image["lossless"] is True and image["image"] == 1
    assert "grids" not in lines["payload"]
    line_branches = lines["contract"]["properties"]["entries"]["items"]["anyOf"]
    assert [br["properties"]["state"]["const"] for br in line_branches] == ["text", "uncertain"]
    full = answer(plan)
    parts = [
        {"entries": [e for e in full["entries"] if e["id"] in cells["entryIds"]]},
        {"entries": [e for e in full["entries"] if e["id"] in lines["entryIds"]]},
    ]
    merged = reading.merge_read_parts(plan, parts)
    assert merged == full
    assert reading.validate_read(plan, merged, detail_bounds=[0, 0, 90, 90])["status"] == "read"
    with pytest.raises(PdfVisualReviewError, match="image_read_entry_inventory"):
        reading.merge_read_parts(plan, parts[:1])
    with pytest.raises(PdfVisualReviewError, match="image_read_entry_inventory"):
        reading.merge_read_parts(plan, [parts[0], parts[0], parts[1]])


def test_line_strips_are_one_line_each_and_line_requests_are_chunked():
    doc, capture = fixture()
    plan = make_plan(doc, capture)
    cells = [e for e in plan["entries"] if e["kind"] == "cell"]

    def line(i, top, height=30, left=10):
        return {"id": f"t{i}", "kind": "text_region", "bounds": [left, top, 900, top + height]}

    tall = {**plan, "pixelSize": [2000, 4000]}
    # A title with a marker beside it, a statement below, then two condition lines.
    tall["entries"] = cells + [
        line(0, 127, 71),
        line(1, 144, 35, left=904),
        line(2, 253, 38),
        line(3, 972, 34),
        line(4, 1033, 35),
    ]
    strips = reading.line_strips(tall)
    assert [s["entryIds"] for s in strips] == [["t0"], ["t1"], ["t2"], ["t3"], ["t4"]]
    assert strips[0]["pageBounds"] == [0, 103, 924, 222]
    assert strips[1]["pageBounds"] == [880, 120, 924, 203]
    many = {**tall, "entries": cells + [line(i, 100 + i * 40) for i in range(30)]}
    requests = reading.read_requests(many, detail_bounds=[0, 0, 90, 90])
    assert [r["kind"] for r in requests] == ["cells", "lines", "lines", "lines"]
    assert [len(r["entryIds"]) for r in requests[1:]] == [12, 12, 6]
    third = requests[3]
    assert [e["label"] for e in third["payload"]["entries"]] == ["1", "2", "3", "4", "5", "6"]
    assert all(e["image"] == 1 for e in third["payload"]["entries"])
    assert [s["id"] for s in third["sheet"]["strips"]] == [f"s{i}" for i in range(24, 30)]
    assert third["contract"]["properties"]["entries"]["minItems"] == 6
    # Strips are stacked in entry order with a label column and gaps.
    first, second = third["sheet"]["strips"][:2]
    assert first["sheetBounds"][1] == reading.SHEET_MARGIN
    assert second["sheetBounds"][1] == first["sheetBounds"][3] + reading.SHEET_GAP
    assert (
        third["sheet"]["pixelSize"][1]
        == third["sheet"]["strips"][-1]["sheetBounds"][3] + reading.SHEET_MARGIN
    )
    # A sheet never exceeds its height budget: tall strips split into more sheets.
    huge = {**tall, "entries": cells + [line(i, 100 + i * 700, 650) for i in range(6)]}
    assert [len(r["entryIds"]) for r in reading.read_requests(huge, detail_bounds=None)[1:]] == [
        4,
        2,
    ]
