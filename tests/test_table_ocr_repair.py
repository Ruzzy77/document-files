"""Geometric cell OCR regression contracts; no model-quality claim."""

from copy import deepcopy
from types import SimpleNamespace

import pytest


def grid_image():
    pytest.importorskip("cv2")
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (417, 250), "white")
    draw = ImageDraw.Draw(image)
    for x in (0, 208, 415):
        draw.line((x, 0, x, 249), fill="black", width=2)
    for y in (0, 124, 248):
        draw.line((0, y, 416, y), fill="black", width=2)
    for x, y in ((40, 40), (250, 40), (40, 170)):
        draw.rectangle((x, y, x + 18, y + 24), fill="black")
    return image


def test_cells_keep_pixels_blank_evidence_and_exact_padding_transform():
    from document_files.document_model.table_ocr_repair import cell_ocr_units, remove_grid

    image = grid_image()
    before = image.tobytes()
    _, grid = remove_grid(image, max_pixels=200000)
    units, geometry = cell_ocr_units(image, grid)
    assert geometry["status"] == "verified_rectangular_grid"
    assert len(units) == 4 and image.tobytes() == before
    prepared, unit = units[0]
    assert unit["psm"] == 7 and unit["glyphPixelsChanged"] is False
    assert unit["pixelOffset"] == [30, 30]
    assert prepared.getpixel((10, 10)) == image.getpixel((40, 40))
    assert units[-1][0] is None and units[-1][1]["status"] == "no_ink_observed"
    assert units[-1][1]["blankValueProven"] is False
    repeated, _ = cell_ocr_units(image, grid)
    assert [u[1]["fingerprint"] for u in units] == [u[1]["fingerprint"] for u in repeated]


def test_merged_or_boundary_touching_cells_are_not_guessed():
    from PIL import ImageDraw

    from document_files.document_model.table_ocr_repair import cell_ocr_units, remove_grid

    image = grid_image()
    _, grid = remove_grid(image, max_pixels=200000)
    incomplete = deepcopy(grid)
    middle = next(v for v in incomplete["verticalLines"] if 200 < v[0] < 210)
    middle = list(middle)
    middle[3] = 100
    incomplete["verticalLines"] = [
        middle if 200 < v[0] < 210 else v for v in incomplete["verticalLines"]
    ]
    assert cell_ocr_units(image, incomplete)[1]["status"] == "merged_or_incomplete_grid_unsupported"
    ImageDraw.Draw(image).rectangle((4, 40, 8, 45), fill="black")
    units, _ = cell_ocr_units(image, grid)
    assert units[0][0] is None and units[0][1]["status"] == "glyph_boundary_contact_unresolved"


def test_cell_budget_resume_reuses_finished_cells_without_ocr_or_silent_replacement():
    pytest.importorskip("docling")
    image = grid_image()
    import pandas as pd
    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel

    from document_files.document_model.docling_adapter import RecognitionConfig
    from document_files.document_model.docling_pipeline import pipeline_class

    config = RecognitionConfig(
        "/models", "/tesseract", "/tessdata", table_ocr_repair="ruled_cells_v2", repair_max_calls=1
    )
    restored = {}
    cls = pipeline_class(config, {}, restored)._product_ocr_type

    def model():
        instance = cls.__new__(cls)
        instance.scale = 3
        instance.orientation = 0
        return instance

    bbox = BoundingBox(l=0, t=0, r=100, b=60, coord_origin=CoordOrigin.TOPLEFT)
    page = SimpleNamespace(
        size=SimpleNamespace(width=100, height=60),
        page_no=1,
        predictions=SimpleNamespace(
            layout=SimpleNamespace(
                clusters=[SimpleNamespace(label=DocItemLabel.TABLE, bbox=bbox, id=1)]
            )
        ),
        parsed_page=SimpleNamespace(textline_cells=[]),
        get_image=lambda **_: image,
    )
    calls = []

    def ocr(*_):
        calls.append(1)
        return pd.DataFrame(
            [
                {
                    "text": "00012345678901234567",
                    "left": 10,
                    "top": 10,
                    "width": 15,
                    "height": 20,
                    "conf": 90,
                }
            ]
        )

    a = model()
    a._run_tesseract = ocr
    first = {
        "original": [],
        "supplemental": [],
        "repairs": [],
        "issues": [],
        "originalOCRFingerprint": "stable-original",
    }
    a.repair(page, [], first)
    assert calls == [1]
    transform = first["repairs"][0]["transform"]
    assert transform["scaleX"] == image.width / 100
    assert transform["scaleY"] == image.height / 60
    assert transform["scaleX"] != transform["scaleY"]
    assert first["repairs"][0]["unitExecution"]["states"][0]["status"] == "observed"
    assert len(first["repairs"][0]["units"]) == 4
    assert first["repairs"][0]["unitExecution"]["states"][2]["status"] == "not_attempted"
    assert first["repairs"][0]["unitExecution"]["states"][2]["reason"] == "call_budget_exceeded"
    assert first["repairs"][0]["unitExecution"]["states"][3]["status"] == "no_ink_observed"
    from document_files.document_model.table_ocr_repair import cell_ocr_units

    assert first["repairs"][0]["units"] == [
        u for _, u in cell_ocr_units(image, first["repairs"][0])[0]
    ]
    assert any(i["code"] == "table_ocr_repair_budget_exceeded" for i in first["issues"])
    restored.update(
        originalOCRFingerprint="stable-original",
        tableRepairs=deepcopy(first["repairs"]),
        cells=deepcopy(first["supplemental"]),
    )
    b = model()
    b._run_tesseract = ocr
    second = {
        "original": [],
        "supplemental": [],
        "repairs": [],
        "issues": [],
        "originalOCRFingerprint": "stable-original",
    }
    page.parsed_page.textline_cells = []
    b.repair(page, [], second)
    assert calls == [1, 1]
    assert second["repairs"][0]["unitExecution"]["states"][0]["reusedFromCheckpoint"]
    assert second["repairs"][0]["unitExecution"]["states"][1]["status"] == "observed"
    assert len(second["supplemental"]) == 2
    assert all(c.orig == "00012345678901234567" for c in page.parsed_page.textline_cells)


def test_ruling_selection_requires_only_grid_ink_not_token_spelling():
    from PIL import ImageDraw

    from document_files.document_model.table_ocr_repair import remove_grid, ruling_line_evidence

    image = grid_image()
    _, grid = remove_grid(image, max_pixels=200000)
    evidence = ruling_line_evidence(image, grid, (200, 20, 215, 80))
    assert evidence and evidence["nonRulingInkPixels"] == 0
    assert evidence["originalTextPreserved"]
    # One non-grid glyph pixel is enough to keep an overlapping token as text.
    ImageDraw.Draw(image).point((202, 45), fill="black")
    assert ruling_line_evidence(image, grid, (200, 20, 215, 80)) is None
    assert ruling_line_evidence(image, grid, (20, 20, 80, 90)) is None


def test_geometric_view_orders_same_line_without_rewriting_sources():
    from document_files.document_model.table_ocr_repair import geometric_structure_order

    records = [
        {"text": "right", "bbox": {"l": 60, "r": 80, "t": 9, "b": 21, "coord_origin": "TOPLEFT"}},
        {"text": "left", "bbox": {"l": 20, "r": 40, "t": 10, "b": 20, "coord_origin": "TOPLEFT"}},
    ]
    before = deepcopy(records)
    repairs = [
        {
            "policy": "ruled_cells_v2",
            "transform": {"cropPixelOrigin": [0, 0], "scaleX": 1, "scaleY": 1},
            "units": [{"cellPixelBox": [0, 0, 100, 30]}],
        }
    ]
    assert geometric_structure_order(records, repairs) == [1, 0]
    assert records == before


def test_structure_view_reindexes_clones_without_changing_source_identity():
    pytest.importorskip("docling")
    from copy import deepcopy
    from types import SimpleNamespace

    from docling_core.types.doc import BoundingBox, CoordOrigin
    from docling_core.types.doc.page import BoundingRectangle, TextCell

    from document_files.document_model.docling_adapter import RecognitionConfig
    from document_files.document_model.docling_pipeline import pipeline_class

    config = RecognitionConfig(
        "/models", "/tesseract", "/tessdata", table_ocr_repair="ruled_cells_v2"
    )
    cls = pipeline_class(config, {})._product_ocr_type
    model = cls.__new__(cls)
    cells = [
        TextCell(
            index=index,
            text=text,
            orig=text,
            from_ocr=True,
            rect=BoundingRectangle.from_bounding_box(
                BoundingBox(l=left, t=10, r=right, b=20, coord_origin=CoordOrigin.TOPLEFT)
            ),
        )
        for index, text, left, right in [(0, "English", 40, 80), (1, "한국어", 10, 35)]
    ]
    original = [c.model_dump() for c in cells]
    snapshot = {
        "original": [
            {"backendCellIndex": 0, "text": "English"},
            {"backendCellIndex": 1, "text": "한국어"},
        ],
        "supplemental": [],
        "issues": [],
        "repairs": [
            {
                "policy": "ruled_cells_v2",
                "transform": {"cropPixelOrigin": [0, 0], "scaleX": 1, "scaleY": 1},
                "units": [{"cellPixelBox": [0, 0, 100, 30]}],
            }
        ],
    }
    before = deepcopy(snapshot["original"])
    page = SimpleNamespace(parsed_page=SimpleNamespace(textline_cells=cells))
    model.apply_structure_view(page, snapshot)
    assert [
        (c.index, c.text) for c in sorted(page.parsed_page.textline_cells, key=lambda c: c.index)
    ] == [(0, "한국어"), (1, "English")]
    assert [c.model_dump() for c in cells] == original
    assert [
        {k: v for k, v in c.items() if k != "structureView"} for c in snapshot["original"]
    ] == before
    assert [c["structureView"]["structureInputIndex"] for c in snapshot["original"]] == [1, 0]


@pytest.mark.parametrize("outcome", ["timeout", "cancel", "failure", "time_budget", "empty"])
def test_complete_unit_plan_survives_stops_with_separate_execution_state(monkeypatch, outcome):
    pytest.importorskip("docling")
    import subprocess

    import pandas as pd
    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel

    from document_files.document_model import docling_pipeline
    from document_files.document_model.docling_adapter import RecognitionConfig
    from document_files.document_model.table_ocr_repair import cell_ocr_units

    image = grid_image()
    config = RecognitionConfig(
        "/models", "/tesseract", "/tessdata", table_ocr_repair="ruled_cells_v2"
    )
    cls = docling_pipeline.pipeline_class(config, {})._product_ocr_type
    model = cls.__new__(cls)
    model.scale, model.orientation = 3, 0
    bbox = BoundingBox(l=0, t=0, r=100, b=60, coord_origin=CoordOrigin.TOPLEFT)
    page = SimpleNamespace(
        size=SimpleNamespace(width=100, height=60),
        page_no=1,
        predictions=SimpleNamespace(
            layout=SimpleNamespace(
                clusters=[SimpleNamespace(label=DocItemLabel.TABLE, bbox=bbox, id=1)]
            )
        ),
        parsed_page=SimpleNamespace(textline_cells=[]),
        get_image=lambda **_: image,
    )
    snapshot = {"original": [], "supplemental": [], "repairs": [], "issues": []}
    initial_units = []

    def prepare(*args, **kwargs):
        units, geometry = cell_ocr_units(*args, **kwargs)
        initial_units.extend(deepcopy(u) for _, u in units)
        if outcome == "time_budget":
            model.repair_elapsed = config.repair_max_seconds
        return units, geometry

    monkeypatch.setattr(docling_pipeline, "cell_ocr_units", prepare)
    calls = []

    def ocr(*_):
        # The entire plan must already be in the snapshot before any external call.
        assert snapshot["repairs"][0]["units"] == initial_units
        calls.append(1)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("test", 1)
        if outcome == "cancel":
            raise KeyboardInterrupt
        if outcome == "failure":
            raise RuntimeError("test")
        return pd.DataFrame(columns=["text", "left", "top", "width", "height", "conf"])

    model._run_tesseract = ocr
    errors = {
        "timeout": subprocess.TimeoutExpired,
        "cancel": KeyboardInterrupt,
        "failure": RuntimeError,
    }
    if outcome in errors:
        with pytest.raises(errors[outcome]):
            model.repair(page, [], snapshot)
    else:
        model.repair(page, [], snapshot)
    repair = snapshot["repairs"][0]
    assert len(repair["units"]) == 4 and repair["units"] == initial_units
    execution = repair["unitExecution"]
    assert execution["order"] == [0, 1, 2, 3]
    states = execution["states"]
    assert [s["unitIndex"] for s in states] == [0, 1, 2, 3]
    assert [s["unitFingerprint"] for s in states] == [u["fingerprint"] for u in initial_units]
    assert states[3]["status"] == "no_ink_observed" and states[3]["ocrAttempted"] is False
    assert all(s["blankValueProven"] is False for s in states)
    if outcome == "empty":
        assert len(calls) == 3
        assert all(s["status"] == "observed_no_tokens" for s in states[:3])
        assert all(u["status"] == "ready" for u in repair["units"][:3])
    else:
        expected = {
            "timeout": "timed_out",
            "cancel": "cancelled",
            "failure": "failed",
            "time_budget": "time_budget_exceeded",
        }[outcome]
        assert execution["stopReason"] == expected
        assert states[0]["status"] == ("not_attempted" if outcome == "time_budget" else expected)
        assert states[2]["status"] == "not_attempted" and states[2]["reason"] == expected
        assert len(calls) == (0 if outcome == "time_budget" else 1)


def cell_batch_tsv(page_numbers=(1, 2), *, empty=()):
    import csv
    import io

    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(
        [
            "level",
            "page_num",
            "block_num",
            "par_num",
            "line_num",
            "word_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        ]
    )
    for page in page_numbers:
        writer.writerow([1, page, 0, 0, 0, 0, 0, 0, 39, 45, -1, ""])
        if page not in empty:
            writer.writerow(
                [5, page, 1, 1, 1, 1, 10, 10, 15, 20, 90, "000123" if page == 1 else "0.020"]
            )
    return stream.getvalue().encode()


def cell_batch_fixture(monkeypatch, *, restored=None, **limits):
    pytest.importorskip("docling")
    from pathlib import Path

    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel

    from document_files.document_model import docling_pipeline
    from document_files.document_model.docling_adapter import RecognitionConfig

    image = grid_image()
    config = RecognitionConfig(
        "/models",
        "/ocr",
        "/data",
        table_ocr_repair="ruled_cells_v2",
        repair_batch_size=2,
        repair_max_images=16,
        **limits,
    )
    cls = docling_pipeline.pipeline_class(config, {}, restored)._product_ocr_type
    model = cls.__new__(cls)
    model.scale, model.orientation = 3, 0
    model.options = SimpleNamespace(lang=["kor", "eng"], psm=3)
    model._safe_tesseract_cmd, model._safe_tessdata_path = "/ocr", "/data"
    bbox = BoundingBox(l=0, t=0, r=100, b=60, coord_origin=CoordOrigin.TOPLEFT)
    page = SimpleNamespace(
        size=SimpleNamespace(width=100, height=60),
        page_no=1,
        predictions=SimpleNamespace(
            layout=SimpleNamespace(
                clusters=[SimpleNamespace(label=DocItemLabel.TABLE, bbox=bbox, id=1)]
            )
        ),
        parsed_page=SimpleNamespace(textline_cells=[]),
        get_image=lambda **_: image,
    )
    snapshot = {
        "original": [],
        "supplemental": [],
        "repairs": [],
        "issues": [],
        "originalOCRFingerprint": "stable-original",
    }
    calls = []

    def process(command, **_):
        paths = Path(command[-3]).read_text().splitlines()
        calls.append(paths)
        assert 1 <= len(paths) <= 2
        assert command[command.index("--psm") + 1] == "7"
        return {
            "raw": cell_batch_tsv(tuple(range(1, len(paths) + 1))),
            "exitCode": 0,
            "timedOut": False,
            "status": "returned",
            "processStarted": True,
            "outputTruncated": False,
        }

    monkeypatch.setattr(docling_pipeline, "bounded_tsv_result", process)
    return model, page, snapshot, calls


def test_batch_pipeline_preserves_shared_raw_original_ordinals_and_full_plan(monkeypatch):
    from document_files.document_model.recognition_sources import (
        _validated_raw_words,
        raw_pass_fingerprint,
    )

    model, page, snapshot, calls = cell_batch_fixture(monkeypatch)
    model.repair(page, [], snapshot)
    assert [len(c) for c in calls] == [2, 1]
    units = snapshot["repairs"][0]["units"]
    states = snapshot["repairs"][0]["unitExecution"]["states"]
    assert len(units) == 4
    assert [s["status"] for s in states] == ["observed"] * 3 + ["no_ink_observed"]
    assert all(s["blankValueProven"] is False for s in states)
    assert model.repair_calls == 2 and model.repair_images == 3
    assert model.repair_input_pixels == 3 * 39 * 45
    assert len(snapshot["rawOCRRuns"]) == 2 and len(snapshot["rawOCRPasses"]) == 3
    for capture in snapshot["rawOCRPasses"]:
        capture["fingerprint"] = raw_pass_fingerprint(capture)
        assert "tsv" not in capture
        rows = _validated_raw_words(capture, snapshot)
        assert len(rows) == 1
        assert rows[0]["raw"]["page_num"] == str(capture["tsvInputPageNumber"])
    second = snapshot["rawOCRPasses"][1]
    assert second["tsvInputPageNumber"] == 2 and second["page_no"] == 1
    assert second["detections"][0]["ordinal"] == 3
    assert second["detections"][0]["text"] == "0.020"
    assert snapshot["rawOCRRuns"][0]["tsv"].encode() == cell_batch_tsv()
    assert [u["fingerprint"] for u in units] == [s["unitFingerprint"] for s in states]


@pytest.mark.parametrize(
    "limit,expected,reason",
    [
        ({"repair_max_calls": 1}, 2, "call_budget_exceeded"),
        ({"repair_max_input_pixels": 39 * 45}, 1, "input_pixel_budget_exceeded"),
    ],
)
def test_batch_execution_limits_leave_suffix_unattempted(monkeypatch, limit, expected, reason):
    model, page, snapshot, calls = cell_batch_fixture(monkeypatch, **limit)
    model.repair(page, [], snapshot)
    execution = snapshot["repairs"][0]["unitExecution"]
    assert model.repair_images == expected and len(calls) == 1
    assert execution["stopReason"] == reason
    assert execution["states"][expected]["status"] == "not_attempted"
    assert len(snapshot["repairs"][0]["units"]) == 4


def test_batch_image_limit_is_separate_from_process_budget(monkeypatch):
    from dataclasses import replace

    from document_files.document_model.docling_pipeline import pipeline_class

    model, page, snapshot, calls = cell_batch_fixture(monkeypatch)
    # Rebuild closure with a stricter image budget while keeping the configured process budget.
    from document_files.document_model.docling_adapter import RecognitionConfig

    config = replace(
        RecognitionConfig("/models", "/ocr", "/data", table_ocr_repair="ruled_cells_v2"),
        repair_batch_size=2,
        repair_max_images=1,
    )
    cls = pipeline_class(config, {})._product_ocr_type
    limited = cls.__new__(cls)
    limited.__dict__.update(model.__dict__)
    limited.repair(page, [], snapshot)
    assert [len(c) for c in calls] == [1]
    assert snapshot["repairs"][0]["unitExecution"]["stopReason"] == "image_budget_exceeded"


@pytest.mark.parametrize("failure", ["second", "missing", "timeout", "cancel", "empty"])
def test_batch_failure_retains_raw_but_never_accepts_first_input(monkeypatch, failure):
    import subprocess

    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch)
    raw = b"" if failure == "empty" else cell_batch_tsv((1,))
    error = {
        "second": subprocess.CalledProcessError(1, "ocr"),
        "timeout": subprocess.TimeoutExpired("ocr", 1),
        "cancel": KeyboardInterrupt(),
    }.get(failure)
    monkeypatch.setattr(
        docling_pipeline,
        "bounded_tsv_result",
        lambda *a, **k: {
            "raw": raw,
            "exitCode": 1 if error else 0,
            "timedOut": failure == "timeout",
            "status": "failed" if error else "returned",
            "error": error,
            "processStarted": True,
            "outputTruncated": False,
        },
    )
    with pytest.raises((Exception, KeyboardInterrupt)):
        model.repair(page, [], snapshot)
    run = snapshot["rawOCRRuns"][0]
    assert run["tsv"].encode() == raw and run["status"] != "complete"
    assert not snapshot["supplemental"]
    states = snapshot["repairs"][0]["unitExecution"]["states"]
    assert all(states[i]["status"] in {"failed", "timed_out", "cancelled"} for i in (0, 1))
    assert states[2]["status"] == "not_attempted"
    assert states[3]["status"] == "no_ink_observed"
    assert all(c["status"] == "failed" for c in snapshot["rawOCRPasses"])


def test_batch_empty_page_is_not_blank_or_missing_output(monkeypatch):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch, repair_max_calls=1)
    monkeypatch.setattr(
        docling_pipeline,
        "bounded_tsv_result",
        lambda *a, **k: {
            "raw": cell_batch_tsv(empty=(2,)),
            "exitCode": 0,
            "timedOut": False,
            "status": "returned",
            "processStarted": True,
            "outputTruncated": False,
        },
    )
    model.repair(page, [], snapshot)
    state = snapshot["repairs"][0]["unitExecution"]["states"][1]
    assert state["status"] == "observed_no_tokens" and state["blankValueProven"] is False
    assert snapshot["rawOCRRuns"][0]["status"] == "complete"
    assert any(i["code"] == "table_ocr_ink_without_tokens" for i in snapshot["issues"])


def test_batch_resume_checks_complete_run_membership_before_reuse(monkeypatch):
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    model, page, first, _ = cell_batch_fixture(monkeypatch, repair_max_calls=1)
    model.repair(page, [], first)
    for c in first["rawOCRPasses"]:
        c["fingerprint"] = raw_pass_fingerprint(c)
    restored = {
        "originalOCRFingerprint": "stable-original",
        "rawCaptureVersion": first["rawCaptureVersion"],
        "tableRepairs": deepcopy(first["repairs"]),
        "cells": deepcopy(first["supplemental"]),
        "rawOCRRuns": deepcopy(first["rawOCRRuns"]),
        "rawOCRPasses": deepcopy(first["rawOCRPasses"]),
    }
    resumed, page, second, calls = cell_batch_fixture(
        monkeypatch, restored=restored, repair_max_calls=1
    )
    resumed.repair(page, [], second)
    states = second["repairs"][0]["unitExecution"]["states"]
    assert [s["status"] for s in states[:3]] == ["reused", "reused", "observed"]
    assert [len(c) for c in calls] == [1]
    assert len(second["rawOCRRuns"]) == 2 and len(second["rawOCRPasses"]) == 3
    # A previous-version plan cannot provide completed work for this execution.
    restored["tableRepairs"][0].pop("executionPolicy")
    denied, page, third, calls = cell_batch_fixture(
        monkeypatch, restored=restored, repair_max_calls=1
    )
    denied.repair(page, [], third)
    assert [len(c) for c in calls] == [2]
    assert third["repairs"][0]["unitExecution"]["states"][0]["status"] == "observed"


@pytest.mark.parametrize("pages", [(1,), (1, 1), (2, 1), (1, 3), (1, 2, 3)])
def test_batch_tsv_rejects_missing_duplicate_reordered_extra_inputs(pages):
    from document_files.document_model.recognition_batches import batch_rows

    with pytest.raises(ValueError):
        batch_rows(cell_batch_tsv(pages), 2)


def test_batch_tsv_preserves_global_rows_and_empty_page():
    from document_files.document_model.recognition_batches import batch_rows

    raw = cell_batch_tsv(empty=(1,))
    rows = batch_rows(raw, 2)
    assert rows[1][0][0] == 0 and rows[2][1][0] == 2
    assert rows[2][1][1]["text"] == "0.020"


def test_bounded_batch_result_retains_stdout_on_crash_and_timeout():
    import sys

    from document_files.document_model.table_ocr_repair import bounded_tsv_result

    crash = bounded_tsv_result(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'partial'); sys.exit(2)"],
        timeout=5,
        max_bytes=1000,
    )
    assert crash["raw"] == b"partial" and crash["exitCode"] == 2
    assert crash["status"] == "failed"
    timeout = bounded_tsv_result(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.buffer.write(b'partial'); "
            "sys.stdout.flush(); time.sleep(10)",
        ],
        timeout=1.0,
        max_bytes=1000,
    )
    assert timeout["raw"] == b"partial" and timeout["timedOut"] is True
    assert timeout["exitCode"] is not None


@pytest.mark.parametrize("change", ["tsv", "image", "frame", "unit", "page", "member", "status"])
def test_batch_raw_import_rejects_broken_membership(monkeypatch, change):
    from document_files.document_model.recognition_sources import (
        _validated_raw_words,
        raw_pass_fingerprint,
    )

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch, repair_max_calls=1)
    model.repair(page, [], snapshot)
    capture = snapshot["rawOCRPasses"][0]
    if change == "tsv":
        snapshot["rawOCRRuns"][0]["tsv"] += "changed"
    elif change == "image":
        capture["image"]["sha256"] = "0" * 64
    elif change == "frame":
        capture["pixelFrame"] = {"status": "invented"}
    elif change == "unit":
        capture["unitFingerprint"] = "wrong"
    elif change == "page":
        capture["tsvInputPageNumber"] = 2
    elif change == "member":
        snapshot["rawOCRPasses"].pop()
    else:
        snapshot["rawOCRRuns"][0]["status"] = "failed"
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    with pytest.raises(ValueError):
        _validated_raw_words(capture, snapshot)


def test_batch_psm_difference_keeps_independent_single_inputs(monkeypatch):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch)
    original = docling_pipeline.cell_ocr_units

    def different(*args, **kwargs):
        units, grid = original(*args, **kwargs)
        units[1][1]["psm"] = 6
        return units, grid

    calls = []

    def process(command, **_):
        from pathlib import Path

        paths = Path(command[-3]).read_text().splitlines()
        calls.append((len(paths), command[command.index("--psm") + 1]))
        return {
            "raw": cell_batch_tsv((1,)),
            "exitCode": 0,
            "timedOut": False,
            "status": "returned",
            "processStarted": True,
            "outputTruncated": False,
        }

    monkeypatch.setattr(docling_pipeline, "cell_ocr_units", different)
    monkeypatch.setattr(docling_pipeline, "bounded_tsv_result", process)
    model.repair(page, [], snapshot)
    assert calls == [(1, "7"), (1, "6"), (1, "7")]


@pytest.mark.parametrize("reverse", [False, True])
def test_explicit_batch_input_order_is_not_pdf_page_number(monkeypatch, tmp_path, reverse):
    from document_files.document_model.recognition_coordinates import image_identity
    from document_files.document_model.recognition_sources import (
        _validated_raw_words,
        raw_pass_fingerprint,
    )

    model, _, _, _ = cell_batch_fixture(monkeypatch)
    model.raw_page_no = 9
    units = []
    for index in (0, 1):
        image = grid_image().crop((0, 0, 39, 45))
        path = tmp_path / f"input-{index}.png"
        image.save(path)
        units.append(
            {
                "path": str(path),
                "image": image_identity(image),
                "unitFingerprint": f"unit-{index}",
                "localPdfPageNumber": 9,
                "pixelFrame": None,
                "psm": 7,
                "languages": ["kor", "eng"],
                "transform": {
                    "repairIndex": 0,
                    "cellUnitIndex": index,
                    "pixelOrigin": [index * 50, 20],
                    "scale": [2, 3],
                },
            }
        )
    if reverse:
        units.reverse()
    results = model._run_cell_image_batch(units, languages=["kor", "eng"], psm=7, timeout=10)
    for capture in model.raw_passes:
        capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload = {"rawOCRPasses": model.raw_passes, "rawOCRRuns": model.raw_runs}
    for page_num, item in enumerate(units, 1):
        capture = model.raw_passes[page_num - 1]
        words = _validated_raw_words(capture, payload)
        assert capture["page_no"] == 9 and capture["tsvInputPageNumber"] == page_num
        assert capture["transform"]["cellUnitIndex"] == item["transform"]["cellUnitIndex"]
        assert words[0]["raw"]["page_num"] == str(page_num)
        assert words[0]["pageBBox"]["l"] == (item["transform"]["pixelOrigin"][0] + 10) / 2
        assert len(results[item["transform"]["cellUnitIndex"]]) == 1
    before = len(model.raw_runs)
    units[1]["languages"] = ["eng"]
    with pytest.raises(ValueError, match="settings differ"):
        model._run_cell_image_batch(units, languages=["kor", "eng"], psm=7, timeout=10)
    assert len(model.raw_runs) == before


def test_batch_configuration_identity_and_invalid_settings():
    from dataclasses import replace

    from document_files.document_model.docling_adapter import DoclingRecognition, RecognitionConfig

    config = RecognitionConfig("/models", "/ocr", "/data")
    assert config.repair_batch_size == 1 and config.repair_max_images == 8
    assert config.repair_max_input_pixels == 16000000
    original = DoclingRecognition(config).identity
    changed = replace(
        config, table_ocr_repair="ruled_cells_v2", repair_batch_size=2, repair_max_images=16
    )
    assert DoclingRecognition(changed).identity != original
    assert DoclingRecognition(changed).identity["adapterVersion"] == "23"
    for key, value in (
        ("repair_batch_size", 3),
        ("repair_max_images", True),
        ("repair_max_images", 65),
        ("repair_max_input_pixels", 64000001),
    ):
        with pytest.raises(ValueError, match="budget"):
            replace(config, **{key: value}).validate()
    with pytest.raises(ValueError, match="ruled_cells_v2"):
        replace(config, repair_batch_size=2).validate()


def test_batch_partial_run_cannot_be_reused_even_with_stale_completed_states(monkeypatch):
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    model, page, first, _ = cell_batch_fixture(monkeypatch, repair_max_calls=1)
    model.repair(page, [], first)
    for capture in first["rawOCRPasses"]:
        capture["fingerprint"] = raw_pass_fingerprint(capture)
    restored = {
        "originalOCRFingerprint": "stable-original",
        "rawCaptureVersion": first["rawCaptureVersion"],
        "tableRepairs": deepcopy(first["repairs"]),
        "cells": deepcopy(first["supplemental"]),
        "rawOCRRuns": deepcopy(first["rawOCRRuns"]),
        "rawOCRPasses": deepcopy(first["rawOCRPasses"]),
    }
    restored["rawOCRRuns"][0]["status"] = "failed"
    resumed, page, second, calls = cell_batch_fixture(
        monkeypatch, restored=restored, repair_max_calls=1
    )
    resumed.repair(page, [], second)
    assert calls and not any(
        s.get("reusedFromCheckpoint") for s in second["repairs"][0]["unitExecution"]["states"]
    )
    assert all(run["status"] == "complete" for run in second["rawOCRRuns"])


def test_bounded_batch_result_marks_truncated_output_and_start_failure():
    import sys

    from document_files.document_model.table_ocr_repair import bounded_tsv_result

    result = bounded_tsv_result([sys.executable, "-c", "print('x'*4096)"], timeout=5, max_bytes=32)
    assert result["status"] == "failed" and result["outputTruncated"] is True
    assert len(result["raw"]) == 32 and result["exitCode"] is not None
    result = bounded_tsv_result(["/missing/ocr-executable"], timeout=5, max_bytes=32)
    assert result["status"] == "failed" and result["processStarted"] is False
    assert result["raw"] == b"" and result["exitCode"] is None


@pytest.mark.parametrize("native", [False, True])
def test_cell_pixel_producer_budget_blocks_frame_hash_crop_and_grid(monkeypatch, native):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch)
    canvas = page.get_image()
    page._image_cache = {3.0: canvas}
    model.cell_observation_pixels = 16000000
    cluster = page.predictions.layout.clusters[0]

    def forbidden(*_args, **_kwargs):
        pytest.fail("exhausted observation budget must not access pixel bytes")

    monkeypatch.setattr(canvas, "tobytes", forbidden)
    monkeypatch.setattr(canvas, "crop", forbidden)
    monkeypatch.setattr(docling_pipeline, "framework_frame", forbidden)
    monkeypatch.setattr(docling_pipeline, "remove_grid", forbidden)
    page.get_image = forbidden
    if native:
        model._observe_cached_native_cells(page, snapshot, cluster, cluster.bbox)
    else:
        model._observe_cell_pixels(page, snapshot, cluster, canvas, [0, 0, 417, 250], {})
    assert snapshot["cellObservations"][0]["status"] == "unavailable"
    assert model.cell_observation_pixels == 16000000


def cell_framework_result(page):
    width, height = page.size.width, page.size.height
    dim = SimpleNamespace(
        get_media_bbox=lambda: [0, 0, width, height],
        get_crop_bbox=lambda: [0, 0, width, height],
        get_angle=lambda: 0,
    )
    return SimpleNamespace(
        _page_decoder=SimpleNamespace(get_page_dimension=lambda: dim),
        page_width=width,
        page_height=height,
        page_number=page.page_no,
        doc_key="key=" + "c" * 64,
        _boundary_type="crop_box",
    )


def test_native_cell_pixel_producer_uses_only_cached_image_and_accounts_grid(monkeypatch):
    model, page, snapshot, calls = cell_batch_fixture(monkeypatch)
    canvas = page.get_image()
    page._image_cache = {3.0: canvas}
    page._backend = SimpleNamespace(_result=cell_framework_result(page))
    cluster = page.predictions.layout.clusters[0]
    page.get_image = lambda **_: pytest.fail("native observation must not render")
    model._observe_cached_native_cells(page, snapshot, cluster, cluster.bbox)
    record = snapshot["cellObservations"][0]
    assert record["status"] == "captured" and len(record["slots"]) == 4
    assert record["preparation"] == {"frameIdentityPixels": 0, "gridDetectionPixels": 104250}
    assert model.cell_observation_pixels == record["usage"]["totalExaminedPixels"]
    assert all(s["ocrUnit"] is None for s in record["slots"])
    assert not calls and not snapshot["issues"]


def test_native_cell_pixel_producer_without_cache_is_explicitly_unavailable(monkeypatch):
    model, page, snapshot, calls = cell_batch_fixture(monkeypatch)
    page.get_image = lambda **_: pytest.fail("cache miss must not render")
    cluster = page.predictions.layout.clusters[0]
    model._observe_cached_native_cells(page, snapshot, cluster, cluster.bbox)
    assert snapshot["cellObservations"][0]["reason"] == "cached_canvas_unavailable"
    assert not calls


def test_cell_region_failure_consumes_unknown_remaining_reservation(monkeypatch):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch)
    canvas = page.get_image()
    page._backend = SimpleNamespace(_result=cell_framework_result(page))
    cluster = page.predictions.layout.clusters[0]

    def partial_failure(image, **_kwargs):
        with image.crop([0, 0, 5, 5]) as prefix:
            prefix.tobytes()
        raise RuntimeError("partial pixel inspection")

    monkeypatch.setattr(docling_pipeline, "observe_framework_table_cells", partial_failure)
    model._observe_cell_pixels(page, snapshot, cluster, canvas, [0, 0, 417, 250], {})
    record = snapshot["cellObservations"][0]
    assert record["status"] == "unavailable" and record["unknownWork"] is True
    assert record["reservedBudgetConsumed"] == 16000000
    assert record["usage"]["totalExaminedPixels"] == 0  # No measured prefix is known.
    assert model.cell_observation_pixels == 16000000
    monkeypatch.setattr(canvas, "crop", lambda *_: pytest.fail("reservation reused"))
    model._observe_cell_pixels(page, snapshot, cluster, canvas, [0, 0, 417, 250], {})
    assert len(snapshot["cellObservations"]) == 2


def test_native_grid_failure_preserves_unknown_consumed_work(monkeypatch):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, _ = cell_batch_fixture(monkeypatch)
    canvas = page.get_image()
    page._image_cache = {3.0: canvas}
    cluster = page.predictions.layout.clusters[0]

    def fail_grid(*_args, **_kwargs):
        raise RuntimeError("partial grid processing")

    monkeypatch.setattr(docling_pipeline, "remove_grid", fail_grid)
    model._observe_cached_native_cells(page, snapshot, cluster, cluster.bbox)
    record = snapshot["cellObservations"][0]
    assert record["status"] == "unavailable" and record["unknownWork"] is True
    assert record["reservedBudgetConsumed"] == 104250
    assert record["reservedWork"] == {"gridDetectionPixels": 104250}
    assert record["usage"]["totalExaminedPixels"] == 0
    assert model.cell_observation_pixels == 104250


def test_cell_observation_tables_share_cumulative_budget_without_rehash_after_exhaustion(
    monkeypatch,
):
    from document_files.document_model import docling_pipeline

    model, page, snapshot, calls = cell_batch_fixture(monkeypatch, repair_max_pixels=700000)
    canvas = page.get_image()
    page._backend = SimpleNamespace(_result=cell_framework_result(page))
    cluster = page.predictions.layout.clusters[0]
    _, grid = docling_pipeline.remove_grid(canvas, max_pixels=700000)
    model._observe_cell_pixels(page, snapshot, cluster, canvas, [0, 0, 417, 250], grid)
    first = snapshot["cellObservations"][0]
    assert first["status"] == "captured"
    consumed = first["usage"]["totalExaminedPixels"]
    assert model.cell_observation_pixels == consumed
    assert 700000 - consumed < 2 * canvas.width * canvas.height
    monkeypatch.setattr(canvas, "tobytes", lambda *_: pytest.fail("second table exceeded budget"))
    monkeypatch.setattr(canvas, "crop", lambda *_: pytest.fail("second table exceeded budget"))
    model._observe_cell_pixels(page, snapshot, cluster, canvas, [0, 0, 417, 250], grid)
    assert snapshot["cellObservations"][1]["reason"] == "cell_pixel_budget_exceeded"
    assert model.cell_observation_pixels == consumed and not calls
