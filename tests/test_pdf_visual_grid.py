"""Bounded geometry-candidate regressions, not semantic line/blank approvals."""

import hashlib
import io
import time
from copy import deepcopy

import pytest

from document_files.interpretation import pdf_visual_grid as grid
from document_files.interpretation.pdf_visual_pixels import extract_visual_pixels

Image = pytest.importorskip("PIL.Image")


def fixture(*, thickness=2, alter=None, faint=False):
    colors = {}
    for y in (20, 80, 140):
        for yy in range(y, y + thickness):
            for x in range(20, 140 + thickness):
                colors[x, yy] = (254, 255, 255) if faint else (0, 0, 0)
    for x in (20, 80, 140):
        for xx in range(x, x + thickness):
            for y in range(20, 140 + thickness):
                colors[xx, y] = (254, 255, 255) if faint else (0, 0, 0)
    if alter:
        alter(colors)
    with Image.new("RGB", (170, 170), "white") as image:
        for point, color in colors.items():
            image.putpixel(point, color)
        rgb = hashlib.sha256(image.tobytes()).hexdigest()
        output = io.BytesIO()
        image.save(output, format="PNG")
    pixels = extract_visual_pixels(
        output.getvalue(),
        expected_rgb_sha256=rgb,
        expected_size=(170, 170),
        deadline=time.monotonic() + 30,
    )
    expected = {
        "tableRef": "table-1",
        "sourceSha256": "a" * 64,
        "rgbSha256": rgb,
        "observationFingerprint": "b" * 64,
        "horizontalLines": [20.5, 80.5, 140.5],
        "verticalLines": [20.5, 80.5, 140.5],
        "missingSlots": [{"row": 0, "col": 1, "slotKey": "missing-note"}],
    }
    return pixels, [expected]


def run(data, **kwargs):
    return grid.measure_grid_candidates(
        data[0],
        expected_grids=data[1],
        deadline=kwargs.pop("deadline", time.monotonic() + 30),
        **kwargs,
    )


def band(result, name):
    return next(b for b in result["grids"][0]["bands"] if b["id"] == name)


def points(runs):
    return {(x, y) for y, left, right in runs for x in range(left, right)}


def assert_preserved(data, result):
    original = points([r for c in data[0]["components"] for r in c["runs"]])
    accepted, remaining = points(result["candidateRuns"]), points(result["unassignedRuns"])
    assert accepted.isdisjoint(remaining)
    assert accepted | remaining == original
    assert len(accepted) == result["coverage"]["candidatePixelCount"]
    assert len(remaining) == result["coverage"]["unassignedPixelCount"]
    assert result["coverage"]["discardedPixels"] == 0
    assert result["coverage"]["blankValueProven"] is False
    assert result["coverage"]["contentCompletenessVerified"] is False
    assert result["coverage"]["semanticClassificationVerified"] is False
    assert result["coverage"]["visualReviewRequired"] is True


def test_full_grid_and_missing_slot_are_only_candidates():
    data = fixture()
    original = deepcopy(data)
    result = run(data)
    assert data == original
    assert all(b["status"] == "candidate" for b in result["grids"][0]["bands"])
    assert band(result, "h0")["bounds"] == [20, 20, 142, 22]
    assert band(result, "v1")["bounds"] == [80, 20, 82, 142]
    slot = result["grids"][0]["slots"][0]
    assert slot["fullBounds"] == [80, 20, 142, 82]
    assert slot["interiorBounds"] == [82, 22, 140, 80]
    assert slot["slotAssociationVerified"] is False
    assert slot["blankValueProven"] is False
    assert_preserved(data, result)


def test_single_pixel_faint_line_not_erased():
    data = fixture(thickness=1, faint=True)
    result = run(data)
    assert all(b["status"] == "candidate" for b in result["grids"][0]["bands"])
    assert result["coverage"]["candidatePixelCount"] == data[0]["lowContrastPixelCount"]
    assert_preserved(data, result)


def test_faint_detached_dot_is_not_a_band():
    data = fixture(alter=lambda c: c.update({(85, 55): (254, 255, 255)}))
    result = run(data)
    assert band(result, "v1")["status"] == "candidate"
    assert result["grids"][0]["measurementBasis"] == "contrast_core"
    assert (85, 55) in points(result["unassignedRuns"])
    assert_preserved(data, result)


def test_letters_inside_grid_bbox_are_not_band_members():
    ink = {(100, y): (0, 0, 0) for y in range(45, 55)}
    ink.update({(x, 50): (0, 0, 0) for x in range(100, 107)})
    data = fixture(alter=lambda c: c.update(ink))
    result = run(data)
    assert set(ink).issubset(points(result["unassignedRuns"]))
    assert set(ink).isdisjoint(points(result["candidateRuns"]))
    assert_preserved(data, result)


@pytest.mark.parametrize("partial_edge", [False, True])
def test_broken_line_cannot_pass_by_dropping_a_faint_edge(partial_edge):
    def gap(colors):
        colors.pop((81, 55))
        if not partial_edge:
            colors.pop((80, 55))

    data = fixture(alter=gap)
    result = run(data)
    assert band(result, "v1")["status"] == "unresolved"
    assert result["grids"][0]["slots"][0]["status"] == "unresolved"
    assert_preserved(data, result)


def test_touching_glyph_protrusion_is_unresolved():
    data = fixture(alter=lambda c: c.update({(x, 55): (0, 0, 0) for x in range(82, 87)}))
    result = run(data)
    assert band(result, "v1")["reasons"] == ["nearby_ink_or_incomplete_stripe"]
    assert_preserved(data, result)


def test_endpoint_extension_not_silently_clipped():
    data = fixture(alter=lambda c: c.update({(x, 80): (0, 0, 0) for x in range(5, 20)}))
    result = run(data)
    assert band(result, "h1")["reasons"] == ["endpoint_protrusion_or_truncation"]
    assert_preserved(data, result)


def test_overwide_stripe_rejected():
    data = fixture(thickness=8)
    result = run(data)
    assert band(result, "h0")["reasons"] == ["stripe_width_exceeded"]
    assert_preserved(data, result)


def test_parallel_nearby_stripes_ambiguous():
    def double(colors):
        colors.update({(x, y): (0, 0, 0) for x in range(20, 142) for y in range(25, 27)})

    data = fixture(alter=double)
    result = run(data)
    assert band(result, "h0")["reasons"] == ["broken_or_ambiguous_stripe"]
    assert_preserved(data, result)


def test_rounded_end_keeps_exact_runs_not_box_fill():
    def rounded(colors):
        colors.pop((20, 20))
        colors.pop((141, 20))

    data = fixture(alter=rounded)
    result = run(data)
    assert band(result, "h0")["status"] == "candidate"
    assert band(result, "h0")["bounds"] == [20, 20, 142, 22]
    assert (20, 20) not in points(band(result, "h0")["runs"])
    assert (141, 20) not in points(result["candidateRuns"])
    assert_preserved(data, result)


def test_expected_positions_are_candidates_not_exact_pixel_limits():
    data = fixture()
    data[1][0]["horizontalLines"] = [22.0, 82.0, 142.0]
    data[1][0]["verticalLines"] = [22.0, 82.0, 142.0]
    result = run(data)
    assert band(result, "h0")["bounds"] == [20, 20, 142, 22]
    assert_preserved(data, result)


def test_reproducible_fingerprint_ignores_elapsed_time():
    data = fixture()
    first, second = run(data), run(data)
    assert first["fingerprint"] == second["fingerprint"]
    first["grids"][0]["bands"][0]["runs"][0][1] = 0
    assert second["grids"][0]["bands"][0]["runs"][0][1] == 20


@pytest.mark.parametrize(
    "field,value",
    [
        ("horizontalLines", [20, 20, 140]),
        ("verticalLines", [20, 30, 140]),
        ("horizontalLines", [True, 80, 140]),
        ("verticalLines", [20, float("nan"), 140]),
        ("rgbSha256", "c" * 64),
        ("observationFingerprint", "invalid"),
        ("missingSlots", [{"row": True, "col": 1, "slotKey": "x"}]),
        ("missingSlots", [{"row": 0, "col": 5, "slotKey": "x"}]),
    ],
)
def test_invalid_or_changed_expected_geometry(field, value):
    data = fixture()
    data[1][0][field] = value
    with pytest.raises(grid.VisualGridError):
        run(data)


def test_changed_inventory_rejected():
    data = fixture()
    data[0]["components"][0]["runs"][0][1] = 0
    with pytest.raises(grid.VisualGridError, match="pixels_changed"):
        run(data)


def test_comparison_and_run_budget_fail_explicitly(monkeypatch):
    data = fixture()
    monkeypatch.setattr(grid, "MAX_COMPARISONS", 3)
    with pytest.raises(grid.VisualGridError, match="comparison_budget"):
        run(data)
    monkeypatch.setattr(grid, "MAX_COMPARISONS", 1048576)
    monkeypatch.setattr(grid, "MAX_RUNS", 1)
    with pytest.raises(grid.VisualGridError, match="run_budget"):
        run(data)


def test_deadline_and_cancel():
    data = fixture()
    with pytest.raises(grid.VisualGridError, match="timeout"):
        run(data, deadline=time.monotonic() - 1)
    with pytest.raises(grid.VisualGridError, match="cancelled"):
        run(data, cancelled=lambda: True)
    count = 0

    def cancelled():
        nonlocal count
        count += 1
        return count == 10

    with pytest.raises(grid.VisualGridError, match="cancelled"):
        run(data, cancelled=cancelled)
    assert count == 10


def test_without_grid_every_foreground_pixel_remains_unassigned():
    data = fixture()
    data[1].clear()
    result = run(data)
    assert result["candidateRuns"] == []
    assert_preserved(data, result)


def test_short_endpoint_protrusion_not_absorbed_by_search_tolerance():
    data = fixture(alter=lambda c: c.update({(x, 80): (254, 255, 255) for x in range(17, 20)}))
    result = run(data)
    assert band(result, "h1")["status"] == "candidate"
    assert result["grids"][0]["measurementBasis"] == "contrast_core"
    assert {(x, 80) for x in range(17, 20)} <= points(result["unassignedRuns"])
    assert (17, 80) in points(result["unassignedRuns"])
    assert_preserved(data, result)


def test_parallel_faint_ringing_uses_core_geometry_without_erasing_any_pixels():
    ringing = {(x, y): (253, 254, 255) for y in (15, 17, 75, 77, 135, 137) for x in range(20, 142)}

    def add_ringing(colors):
        for point, color in list(ringing.items()):
            if point in colors:
                del ringing[point]
            else:
                colors[point] = color

    data = fixture(alter=add_ringing)
    result = run(data)
    assert result["grids"][0]["measurementBasis"] == "contrast_core"
    assert all(
        b["status"] == "candidate" and b["surroundingPixelsClaimed"] is False
        for b in result["grids"][0]["bands"]
    )
    assert set(ringing) <= points(result["unassignedRuns"])
    assert_preserved(data, result)


def test_dark_mark_near_core_is_not_absorbed_when_faint_ringing_is_present():
    additions = {(x, 17): (254, 254, 254) for x in range(20, 142)}
    additions[(85, 55)] = (0, 0, 0)
    data = fixture(alter=lambda c: c.update(additions))
    result = run(data)
    assert result["grids"][0]["measurementBasis"] == "contrast_core"
    assert (85, 55) in points(result["unassignedRuns"])
    assert (85, 55) not in points(result["candidateRuns"])
    assert_preserved(data, result)


@pytest.mark.parametrize("change", ["run_hash", "count", "rule", "outside", "overlap", "budget"])
def test_contrast_geometry_mask_must_match_the_original_inventory(monkeypatch, change):
    data = fixture()
    p = data[0]
    core = p["contrastCore"]
    if change == "run_hash":
        core["runsSha256"] = "0" * 64
    elif change == "count":
        core["pixelCount"] -= 1
    elif change == "rule":
        core["rule"] = "all_pixels"
    elif change == "outside":
        core["runs"][0][1] = 0
        core["runsSha256"] = grid._digest(core["runs"])
    elif change == "overlap":
        core["runs"].insert(1, list(core["runs"][0]))
        core["runsSha256"] = grid._digest(core["runs"])
    else:
        monkeypatch.setattr(grid, "MAX_RUNS", p["foregroundRunCount"])
    p["fingerprint"] = grid._digest(
        {k: v for k, v in p.items() if k not in {"fingerprint", "diagnostics"}}
    )
    with pytest.raises(grid.VisualGridError):
        run(data)
