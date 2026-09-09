"""Synthetic supplied-pixel contracts only; no renderer or OCR quality evidence."""

from copy import deepcopy

import pytest

from document_files.document_model.recognition_cell_observations import (
    cell_ocr_links,
    observe_table_cells,
    validate_cell_observation,
)
from document_files.document_model.recognition_coordinates import fingerprint, image_identity


def fixture(mode="RGB"):
    pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    image = Image.new(mode, (60, 40), "white")
    draw = draw_module.Draw(image)
    for x in (0, 29, 58):
        draw.rectangle((x, 0, x + 1, 39), fill="black")
    for y in (0, 19, 38):
        draw.rectangle((0, y, 59, y + 1), fill="black")
    grid = {
        "horizontalLines": [[0, y, 60, 2, 120] for y in (0, 19, 38)],
        "verticalLines": [[x, 0, 2, 40, 80] for x in (0, 29, 58)],
    }
    source = {
        "status": "captured",
        "sourceSha256": "a" * 64,
        "page_no": 7,
        "profile": {},
        "pixelSize": [60, 40],
        "pixelSha256": "b" * 64,
        "intrinsicRotation": 0,
        "pageSizeCanvasUnits": [60, 40],
        "pageBoxes": {
            "mediaDeclared": [0, 0, 60, 40],
            "cropDeclared": [0, 0, 60, 40],
            "effective": [0, 0, 60, 40],
        },
        "renderCoordinates": {"pixelToPageAffine": [1, 0, 0, -1, 0, 40]},
    }
    source["fingerprint"] = fingerprint(source)
    subset = {**deepcopy(source), "sourceSha256": "c" * 64, "page_no": 1}
    subset["fingerprint"] = fingerprint(subset)
    from document_files.document_model.recognition_coordinates import VERSION, subset_mapping

    mapping = subset_mapping(source, subset)
    frame = {
        "version": VERSION,
        "backend": "ThreadedDoclingParsePageBackend",
        "documentKey": "key=" + subset["sourceSha256"],
        "localPageNumber": 1,
        "boundaryType": "crop_box",
        "pageSize": [60, 40],
        "normalizedMediaBox": [0, 0, 60, 40],
        "normalizedCropBox": [0, 0, 60, 40],
        "normalizedAngle": 0,
        "canvas": image_identity(image),
        "requestedCropTopLeft": None,
        "cropPixelBounds": [0, 0, 60, 40],
        "pixelCoordinateOrigin": "TOPLEFT",
        "rounding": "python_round_then_clamp",
        "ocrTruthVerified": False,
    }
    return image, frame, grid, mapping


def collect(image, frame, grid, **kw):
    return observe_table_cells(
        image,
        source_frame=frame,
        table_crop_bounds=[0, 0, 60, 40],
        cluster_id=3,
        local_page_number=1,
        grid=grid,
        **kw,
    )


def check(record, mapping):
    return validate_cell_observation(record, mapping=mapping, source_sha256="a" * 64, page=7)


def test_full_interior_unit_and_edge_counts_partition_without_blank():
    image, frame, grid, mapping = fixture()
    before = image.tobytes()
    record = collect(image, frame, grid)
    assert record["status"] == "captured" and len(record["slots"]) == 4
    slot = record["slots"][0]
    assert slot["fullPixelBox"] == [0, 0, 31, 21]
    assert slot["interiorPixelBox"] == [2, 2, 29, 19]
    assert slot["ocrCellWindowPixelBox"] == [4, 4, 27, 17]
    assert slot["regions"]["interior"]["status"] == "observed_background"
    assert slot["regions"]["fullCell"]["status"] == "observed_nonwhite"
    result = check(record, mapping)
    assert result["status"] == "verified" and result["rawPixelsRecomputed"] is False
    assert record["blankValueProven"] is False and result["contentCoverageVerified"] is False
    assert image.tobytes() == before


def test_faint_edge_is_preserved_even_if_ocr_cell_window_is_white():
    image, frame, grid, mapping = fixture()
    image.putpixel((2, 5), (254, 254, 254))
    frame["canvas"] = image_identity(image)
    record = collect(image, frame, grid)
    regions = record["slots"][0]["regions"]
    assert regions["ocrCellWindow"]["nonWhiteRGBPixels"] == 0
    assert regions["interior"]["nonWhiteRGBPixels"] == 1
    assert sum(b["nonWhiteRGBPixels"] for b in regions["interiorOutsideUnitBands"]) == 1
    assert check(record, mapping)["status"] == "verified"


def test_transparent_white_is_unknown_not_observed_background():
    image, frame, grid, mapping = fixture("RGBA")
    image.putpixel((5, 5), (255, 255, 255, 0))
    frame["canvas"] = image_identity(image)
    record = collect(image, frame, grid)
    region = record["slots"][0]["regions"]["ocrCellWindow"]
    assert region["status"] == "unknown_alpha" and region["unknownAlphaPixels"] == 1
    assert check(record, mapping)["status"] == "verified"


def test_pixel_budget_preserves_full_geometry_and_unexamined_suffix():
    image, frame, grid, mapping = fixture()
    complete = collect(image, frame, grid)
    first = complete["slots"][0]["regions"]
    cost = sum(
        v["pixelCount"] if isinstance(v, dict) else sum(x["pixelCount"] for x in v)
        for v in first.values()
    )
    record = collect(image, frame, grid, max_pixels=4800 + cost)
    assert len(record["slots"]) == 4 and record["usage"]["examinedCells"] == 1
    assert record["slots"][1]["measurementStatus"] == "unexamined"
    assert record["status"] == "partial" and check(record, mapping)["status"] == "verified"
    denied = collect(image, frame, grid, max_pixels=1)
    assert not denied["identitiesMeasured"] and denied["usage"]["totalExaminedPixels"] == 0
    assert check(denied, mapping)["status"] == "unverified"
    limited = collect(image, frame, grid, max_cells=3)
    assert limited["geometry"]["candidateCellCount"] == 4 and not limited["slots"]


def test_ambiguous_boundary_is_not_invented_or_measured():
    image, frame, grid, mapping = fixture()
    grid["verticalLines"][1][3] = 10
    record = collect(image, frame, grid)
    assert any(s["geometryStatus"] == "ambiguous_boundary" for s in record["slots"])
    assert all(not s["regions"] for s in record["slots"] if s["geometryStatus"] != "resolved")
    assert record["status"] == "partial"
    assert check(record, mapping)["status"] == "verified"


@pytest.mark.parametrize(
    "field",
    [
        "canvas_hash",
        "crop_hash",
        "frame_page",
        "source",
        "statistics",
        "bool_count",
        "bool_row",
        "window",
        "bands",
        "status",
        "budget",
    ],
)
def test_receiver_rejects_inconsistent_or_cross_source_records(field):
    image, frame, grid, mapping = fixture()
    record = collect(image, frame, grid)
    if field == "canvas_hash":
        record["canvas"]["sha256"] = record["sourceFrame"]["canvas"]["sha256"] = ""
        record["sourceFrameFingerprint"] = fingerprint(record["sourceFrame"])
    elif field == "crop_hash":
        record["tableCrop"]["image"]["sha256"] = ""
    elif field == "frame_page":
        record["sourceFrame"]["localPageNumber"] = 2
    elif field == "source":
        record["sourceFrame"]["documentKey"] = "key=" + "d" * 64
    elif field == "statistics":
        record["slots"][0]["regions"]["interior"]["opaqueWhitePixels"] -= 1
    elif field == "bool_count":
        record["slots"][0]["regions"]["interior"]["nonWhiteRGBPixels"] = False
    elif field == "bool_row":
        record["slots"][0]["row"] = False
    elif field == "window":
        record["slots"][0]["ocrCellWindowPixelBox"][0] += 1
    elif field == "bands":
        record["slots"][0]["regions"]["boundaryBands"].pop()
    elif field == "status":
        record["slots"][0]["measurementStatus"] = "unexamined"
    else:
        record["usage"]["totalExaminedPixels"] -= 1
    record["fingerprint"] = fingerprint(record)
    assert check(record, mapping)["status"] == "unverified"


def test_slot_and_ocr_linkage_are_separate_and_cross_table_is_rejected():
    image, frame, grid, _ = fixture()
    unit = {"row": 0, "col": 0, "cellPixelBox": [4, 4, 27, 17], "fingerprint": "unit"}
    record = collect(image, frame, grid, units=[unit])
    assert record["slots"][0]["ocrUnit"] == {"unitIndex": 0, "unitFingerprint": "unit"}
    capture = {
        "passId": "pass-1",
        "sourcePass": "table_repair",
        "unitFingerprint": "unit",
        "transform": {"cellUnitIndex": 0},
        "image": {"sha256": "e" * 64},
        "status": "failed",
        "pixelFrame": {
            "tableCropPixelBounds": [0, 0, 60, 40],
            "documentKey": frame["documentKey"],
            "localPageNumber": 1,
        },
    }
    assert cell_ocr_links([record], []) == []
    assert cell_ocr_links([record], [capture])[0]["status"] == "failed"
    capture["pixelFrame"]["tableCropPixelBounds"][0] = 1
    assert cell_ocr_links([record], [capture]) == []


def test_preparation_pixel_passes_are_explicit_and_bounded():
    image, frame, grid, mapping = fixture()
    record = collect(
        image,
        frame,
        grid,
        preparation_pixels={"frameIdentityPixels": 2400, "gridDetectionPixels": 2400},
    )
    assert record["usage"]["preparationPixels"] == 4800
    assert record["usage"]["totalExaminedPixels"] == (
        4800 + record["usage"]["identityPixels"] + record["usage"]["measuredRegionPixels"]
    )
    assert check(record, mapping)["status"] == "verified"
    record["preparation"]["frameIdentityPixels"] = 1
    record["fingerprint"] = fingerprint(record)
    assert check(record, mapping)["status"] == "unverified"


def test_exhausted_pixel_budget_never_reads_or_crops_supplied_canvas(monkeypatch):
    image, frame, grid, _ = fixture()

    def forbidden(*_args, **_kwargs):
        pytest.fail("pixel budget must be checked before pixel access")

    monkeypatch.setattr(image, "tobytes", forbidden)
    monkeypatch.setattr(image, "crop", forbidden)
    record = collect(image, frame, grid, max_pixels=0)
    assert len(record["slots"]) == 4 and record["usage"]["totalExaminedPixels"] == 0


def framework_result(frame):
    from types import SimpleNamespace

    dim = SimpleNamespace(
        get_media_bbox=lambda: frame["normalizedMediaBox"],
        get_crop_bbox=lambda: frame["normalizedCropBox"],
        get_angle=lambda: frame["normalizedAngle"],
    )
    return SimpleNamespace(
        _page_decoder=SimpleNamespace(get_page_dimension=lambda: dim),
        page_width=frame["pageSize"][0],
        page_height=frame["pageSize"][1],
        page_number=frame["localPageNumber"],
        doc_key=frame["documentKey"],
        _boundary_type=frame["boundaryType"],
    )


def framework_collect(image, frame, grid, **options):
    from document_files.document_model.recognition_cell_observations import (
        observe_framework_table_cells,
    )

    return observe_framework_table_cells(
        image,
        framework_result=framework_result(frame),
        table_crop_bounds=[0, 0, 60, 40],
        cluster_id=3,
        local_page_number=1,
        grid=grid,
        **options,
    )


def test_framework_observation_reads_full_canvas_once_and_keeps_frame_and_links(monkeypatch):
    image, frame, grid, mapping = fixture()
    standalone = collect(image, frame, grid)
    raw = image.tobytes
    reads = []

    def count(*args, **kwargs):
        reads.append(1)
        return raw(*args, **kwargs)

    monkeypatch.setattr(image, "tobytes", count)
    record = framework_collect(image, frame, grid)
    assert len(reads) == 1
    assert record == standalone
    assert record["usage"]["identityPixels"] == 4800
    assert record["preparation"]["frameIdentityPixels"] == 0
    assert check(record, mapping)["status"] == "verified"
    assert record["localPageNumber"] == record["sourceFrame"]["localPageNumber"] == 1


@pytest.mark.parametrize("change", ["hash", "other_canvas", "page", "crop"])
def test_standalone_frame_mismatch_is_rejected_from_actual_pixels(change):
    image, frame, grid, _ = fixture()
    if change == "hash":
        frame["canvas"]["sha256"] = "0" * 64
    elif change == "other_canvas":
        image.putpixel((10, 10), (254, 254, 254))
    elif change == "page":
        frame["localPageNumber"] = 2
    else:
        frame["requestedCropTopLeft"] = [0, 0, 10, 10]
    with pytest.raises(ValueError):
        collect(image, frame, grid)


def test_framework_path_uses_current_canvas_not_external_frame_identity():
    image, frame, grid, mapping = fixture()
    image.putpixel((10, 10), (254, 254, 254))
    frame["canvas"]["sha256"] = (
        "0" * 64
    )  # This object is never an identity input on framework path.
    record = framework_collect(image, frame, grid)
    assert record["canvas"] == image_identity(image)
    assert record["slots"][0]["regions"]["interior"]["nonWhiteRGBPixels"] == 1
    assert check(record, mapping)["status"] == "verified"
    with pytest.raises(TypeError):
        collect(image, frame, grid, framework_result=framework_result(frame))


def test_framework_budget_denial_never_creates_frame_or_reads_pixels(monkeypatch):
    from document_files.document_model import recognition_cell_observations as module

    image, frame, grid, _ = fixture()

    def forbidden(*_args, **_kwargs):
        pytest.fail("budget denial must precede framework frame creation")

    monkeypatch.setattr(module, "framework_frame", forbidden)
    monkeypatch.setattr(image, "tobytes", forbidden)
    monkeypatch.setattr(image, "crop", forbidden)
    record = framework_collect(image, frame, grid, max_pixels=4799)
    assert record["status"] == "partial" and record["usage"]["totalExaminedPixels"] == 0
    assert not record["identitiesMeasured"]


def test_framework_wrong_local_page_is_not_linked():
    image, frame, grid, _ = fixture()
    frame["localPageNumber"] = 2
    with pytest.raises(ValueError):
        framework_collect(image, frame, grid)


def test_previous_cell_observation_version_is_not_reused():
    image, frame, grid, mapping = fixture()
    record = collect(image, frame, grid)
    record["version"] = "document-files.cell-observation.v1"
    record["fingerprint"] = fingerprint(record)
    assert check(record, mapping)["status"] == "unverified"


def test_large_synthetic_canvas_twelve_slots_fit_single_pass_budget(monkeypatch):
    """Recorded development geometry, but entirely synthetic pixels and no renderer/OCR."""
    from document_files.document_model.recognition_cell_observations import (
        observe_framework_table_cells,
    )

    _, frame, _, _ = fixture()
    from PIL import Image

    with Image.new("RGB", (2480, 3509), "white") as image:
        frame.update(
            pageSize=[595, 842],
            normalizedMediaBox=[0, 0, 595, 842],
            normalizedCropBox=[0, 0, 595, 842],
        )
        grid = {
            "horizontalLines": [
                [4, 4, 2134, 5],
                [4, 213, 2134, 4],
                [4, 421, 2134, 4],
                [4, 630, 2134, 4],
            ],
            "verticalLines": [
                [4, 4, 4, 630],
                [738, 4, 4, 630],
                [1079, 4, 5, 630],
                [1542, 4, 4, 630],
                [2134, 4, 4, 630],
            ],
        }
        raw = image.tobytes
        reads = []

        def count(*args, **kwargs):
            reads.append(1)
            return raw(*args, **kwargs)

        monkeypatch.setattr(image, "tobytes", count)
        record = observe_framework_table_cells(
            image,
            framework_result=framework_result(frame),
            table_crop_bounds=[169, 606, 2308, 1240],
            cluster_id=0,
            local_page_number=1,
            grid=grid,
            max_pixels=16000000,
        )
        assert len(reads) == 1
        assert record["status"] == "captured" and len(record["slots"]) == 12
        assert record["usage"]["identityPixels"] == 8702320 + 1356126
        assert record["usage"]["totalExaminedPixels"] <= 16000000
        assert all(s["measurementStatus"] == "measured" for s in record["slots"])
        # The old separate full-frame hash alone would exceed this same budget.
        assert record["usage"]["totalExaminedPixels"] + 8702320 > 16000000
        assert record["blankValueProven"] is False and record["contentCoverageVerified"] is False
