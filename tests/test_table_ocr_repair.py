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
