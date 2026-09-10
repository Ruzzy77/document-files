"""Residual geometry offers review context, not automatic content approval."""

import hashlib
import io
import time

import pytest
from PIL import Image, ImageDraw

from document_files.interpretation.pdf_visual_contexts import partition_units
from document_files.interpretation.pdf_visual_pixels import extract_visual_pixels
from document_files.interpretation.pdf_visual_plan import PdfVisualReviewError


def fixture(alter=None, *, proposal=True, max_runs=65536, max_units=128, sources=None):
    image = Image.new("RGB", (100, 60), "white")
    draw = ImageDraw.Draw(image)
    draw.line((10, 20, 89, 20), fill="black")
    if alter:
        alter(image, draw)
    out = io.BytesIO()
    image.save(out, format="PNG")
    pixels = extract_visual_pixels(
        out.getvalue(),
        expected_rgb_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
        expected_size=list(image.size),
        deadline=time.monotonic() + 10,
    )
    grid = {
        "grids": [{"tableRef": "t", "bands": [{"status": "candidate", "bounds": [10, 20, 90, 21]}]}]
    }
    charges = []
    groups, count = partition_units(
        pixels,
        sources or [],
        [],
        grid,
        {20: [(10, 90, "t")]},
        proposal=proposal,
        spend=lambda n=1: charges.append(n),
        max_runs=max_runs,
        max_units=max_units,
    )
    units = list(groups.values())
    original = {
        (x, y)
        for c in pixels["components"]
        for y, left, right in c["runs"]
        for x in range(left, right)
    }
    output = [
        (x, y)
        for u in units
        for p in u["parts"]
        for y, left, right in p["runs"]
        for x in range(left, right)
    ]
    assert len(output) == len(set(output)) and set(output) == original
    assert count > 0 and sum(charges) > 0
    return units


def points(unit):
    return {
        (x, y) for p in unit["parts"] for y, left, right in p["runs"] for x in range(left, right)
    }


def owner(units, point):
    return next(u for u in units if point in points(u))


@pytest.mark.parametrize("proposal", [False, True])
def test_long_connected_edge_is_candidate_only_for_alternative_view(proposal):
    units = fixture(
        lambda image, draw: draw.line((10, 19, 89, 19), fill=(254, 254, 254)), proposal=proposal
    )
    edge = owner(units, (30, 19))
    assert edge["ruleEdgeTableRefs"] == (["t"] if proposal else [])
    assert not edge["onlyBoundaryPixels"] and not edge["tableRefs"]
    core = owner(units, (30, 20))
    assert core["onlyBoundaryPixels"] and core["tableRefs"] == ["t"]
    assert not core["sourceIds"] and not core["ruleEdgeTableRefs"]


@pytest.mark.parametrize("mark", ["detached_dot", "short_spur", "detached_long", "outside_context"])
def test_proximity_or_original_connection_cannot_approve_marks(mark):
    def alter(image, draw):
        if mark == "detached_dot":
            image.putpixel((30, 25), (254, 254, 254))
        elif mark == "short_spur":
            draw.line((30, 21, 30, 25), fill=(254, 254, 254))
        elif mark == "detached_long":
            draw.line((10, 25, 89, 25), fill=(254, 254, 254))
        else:
            draw.line((30, 21, 30, 40), fill=(254, 254, 254))
            draw.line((10, 40, 89, 40), fill=(254, 254, 254))

    units = fixture(alter)
    point = (30, 40 if mark == "outside_context" else 25)
    assert owner(units, point)["ruleEdgeTableRefs"] == []
    assert not owner(units, point)["onlyBoundaryPixels"]


def test_remove_core_and_context_before_attaching_cell_text():
    def alter(image, draw):
        for x in (30, 70):
            draw.line((x, 21, x, 35), fill=(254, 254, 254))
            draw.rectangle((x - 2, 34, x + 2, 38), fill="black")

    sources = [{"id": "a", "bounds": [28, 33, 33, 39]}, {"id": "b", "bounds": [68, 33, 73, 39]}]
    units = fixture(alter, sources=sources)
    assert owner(units, (30, 35))["sourceIds"] == ["a"]
    assert owner(units, (70, 35))["sourceIds"] == ["b"]
    assert not owner(units, (50, 20))["sourceIds"]


def test_unique_text_component_keeps_pixels_outside_observed_rectangle():
    units = fixture(
        lambda image, draw: draw.line((30, 40, 40, 40), fill=(254, 254, 254)),
        sources=[{"id": "a", "bounds": [33, 39, 38, 42]}],
    )
    assert owner(units, (30, 40))["sourceIds"] == ["a"]
    assert owner(units, (40, 40))["sourceIds"] == ["a"]
    assert not owner(units, (30, 40))["ruleEdgeTableRefs"]


@pytest.mark.parametrize(
    "kwargs,code",
    [({"max_runs": 1}, "visual_run_budget"), ({"max_units": 1}, "visual_unit_budget")],
)
def test_partition_budgets_fail_without_truncation(kwargs, code):
    with pytest.raises(PdfVisualReviewError, match=code):
        fixture(lambda image, draw: draw.line((10, 19, 89, 19), fill=(254, 254, 254)), **kwargs)
