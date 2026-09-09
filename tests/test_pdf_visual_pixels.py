"""Exact synthetic pixel membership tests; not visual/OCR quality approval."""

import hashlib
import io
import json
import random
import time
from collections import deque

import pytest

from document_files.interpretation import pdf_visual_pixels as pixels

Image = pytest.importorskip("PIL.Image")


def encoded(size, colors=None, mode="RGB"):
    with Image.new(mode, size, "white") as image:
        for point, color in (colors or {}).items():
            image.putpixel(point, color)
        raw = image.tobytes()
        output = io.BytesIO()
        image.save(output, format="PNG")
    return output.getvalue(), hashlib.sha256(raw).hexdigest(), size


def run(data, **kwargs):
    png, rgb_sha, size = data
    return pixels.extract_visual_pixels(
        png,
        expected_rgb_sha256=kwargs.pop("expected_rgb_sha256", rgb_sha),
        expected_size=kwargs.pop("expected_size", size),
        deadline=kwargs.pop("deadline", time.monotonic() + 30),
        **kwargs,
    )


def members(component):
    return {(x, y) for y, start, end in component["runs"] for x in range(start, end)}


def test_white_is_only_a_pixel_observation():
    result = run(encoded((8, 7)))
    assert result["components"] == []
    assert result["foregroundPixelCount"] == 0
    assert result["exactWhitePixelCount"] == 56
    assert result["foregroundMaskSha256"] == hashlib.sha256(bytes(56)).hexdigest()
    assert result["coverage"]["exactPixelMembershipVerified"] is True
    for key in (
        "blankValueProven",
        "contentCompletenessVerified",
        "ocrTruthVerified",
        "readingOrderVerified",
    ):
        assert result["coverage"][key] is False


def test_diagonal_touch_and_one_pixel_gap():
    colors = {(0, 0): (0, 0, 0), (1, 1): (0, 0, 0), (3, 1): (0, 0, 0)}
    result = run(encoded((5, 3), colors))
    assert [c["runs"] for c in result["components"]] == [[[0, 0, 1], [1, 1, 2]], [[1, 3, 4]]]
    assert [c["pixelCount"] for c in result["components"]] == [2, 1]


def test_bounding_box_does_not_own_enclosed_component():
    colors = {(x, y): (0, 0, 0) for y in range(7) for x in range(7) if x in (0, 6) or y in (0, 6)}
    colors[(3, 3)] = (254, 255, 255)
    result = run(encoded((7, 7), colors))
    outer, inner = result["components"]
    assert outer["bounds"] == [0, 0, 7, 7]
    assert (3, 3) not in members(outer)
    assert members(inner) == {(3, 3)}
    assert outer["pixelCount"] == 24
    assert inner["lowContrastPixelCount"] == 1
    assert members(outer).isdisjoint(members(inner))


def test_low_contrast_rgb254_and_single_channel254_preserved():
    colors = {
        (0, 0): (254, 254, 254),
        (1, 0): (255, 254, 255),
        (2, 0): (224, 255, 255),
        (3, 0): (223, 255, 255),
    }
    result = run(encoded((5, 1), colors))
    assert result["foregroundPixelCount"] == 4
    assert result["lowContrastPixelCount"] == 3
    assert result["components"][0]["lowContrastPixelCount"] == 3
    assert result["foregroundMaskSha256"] == hashlib.sha256(bytes([1, 1, 1, 1, 0])).hexdigest()
    assert result["lowContrastMaskSha256"] == hashlib.sha256(bytes([1, 1, 1, 0, 0])).hexdigest()


def test_disconnected_prefix_merges_in_later_row(monkeypatch):
    # A provisional component-count check would incorrectly fail the first row.
    monkeypatch.setattr(pixels, "MAX_COMPONENTS", 1)
    colors = {(x, 0): (0, 0, 0) for x in (0, 4, 8)}
    colors.update({(x, 1): (0, 0, 0) for x in range(9)})
    result = run(encoded((9, 2), colors))
    assert result["componentCount"] == 1
    assert result["foregroundRunCount"] == 4


@pytest.mark.parametrize("seed", range(8))
def test_membership_matches_independent_flood_fill(seed):
    rng = random.Random(seed)
    colors = {
        (x, y): rng.choice([(0, 0, 0), (254, 255, 255)])
        for y in range(17)
        for x in range(19)
        if rng.random() < 0.27
    }
    unseen = set(colors)
    expected = []
    while unseen:
        origin = min(unseen)
        unseen.remove(origin)
        group, pending = {origin}, deque([origin])
        while pending:
            x, y = pending.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neighbor = (x + dx, y + dy)
                    if neighbor in unseen:
                        unseen.remove(neighbor)
                        group.add(neighbor)
                        pending.append(neighbor)
        expected.append(frozenset(group))
    result = run(encoded((19, 17), colors))
    observed = [frozenset(members(c)) for c in result["components"]]
    assert set(observed) == set(expected)
    assert sum(len(group) for group in observed) == len(colors)
    for component in result["components"]:
        assert component["runs"] == sorted(component["runs"])
        assert component["pixelCount"] == len(members(component))
        assert component["lowContrastPixelCount"] == sum(
            min(colors[p]) >= 224 for p in members(component)
        )
        assert (
            component["runsSha256"]
            == hashlib.sha256(
                json.dumps(component["runs"], separators=(",", ":")).encode()
            ).hexdigest()
        )


def test_stable_identity_and_independent_returns():
    data = encoded((4, 2), {(0, 0): (0, 0, 0)})
    first, second = run(data), run(data)
    assert first["fingerprint"] == second["fingerprint"]
    assert first["components"][0]["id"] == second["components"][0]["id"]
    first["components"][0]["runs"][0][1] = 99
    assert second["components"][0]["runs"][0][1] == 0
    assert second["diagnostics"]["elapsedSeconds"] >= 0


@pytest.mark.parametrize(
    "name,limit,code",
    [
        ("MAX_IMAGE_BYTES", 10, "byte_budget_exceeded"),
        ("MAX_PIXELS", 5, "pixel_budget_exceeded"),
        ("MAX_RUNS", 1, "run_budget_exceeded"),
        ("MAX_COMPONENTS", 1, "component_budget_exceeded"),
    ],
)
def test_limits_fail_without_partial_success(monkeypatch, name, limit, code):
    monkeypatch.setattr(pixels, name, limit)
    with pytest.raises(pixels.VisualPixelError, match=code):
        run(encoded((6, 2), {(0, 0): (0, 0, 0), (4, 0): (0, 0, 0)}))


def test_exact_limits_are_allowed(monkeypatch):
    monkeypatch.setattr(pixels, "MAX_PIXELS", 12)
    monkeypatch.setattr(pixels, "MAX_RUNS", 2)
    monkeypatch.setattr(pixels, "MAX_COMPONENTS", 2)
    assert run(encoded((6, 2), {(0, 0): (0, 0, 0), (4, 0): (0, 0, 0)}))["componentCount"] == 2


def test_expected_hash_and_size_are_checked():
    data = encoded((5, 3))
    with pytest.raises(pixels.VisualPixelError, match="rgb_hash_mismatch"):
        run(data, expected_rgb_sha256="0" * 64)
    with pytest.raises(pixels.VisualPixelError, match="size_mismatch"):
        run(data, expected_size=(4, 3))


@pytest.mark.parametrize("size", [(True, 2), (0, 2), (2.0, 3), [3], "3x3"])
def test_invalid_expected_size(size):
    with pytest.raises(pixels.VisualPixelError, match="input_invalid"):
        run(encoded((3, 3)), expected_size=size)


@pytest.mark.parametrize("mode", ["RGBA", "L", "P"])
def test_non_rgb_rejected(mode):
    with pytest.raises(pixels.VisualPixelError, match="input_invalid"):
        run(encoded((3, 3), mode=mode))


def test_truncated_corrupt_and_concatenated_stream_rejected():
    png, sha, size = encoded((3, 3))
    corrupt = bytearray(png)
    corrupt[-5] ^= 1
    for invalid in (png[:-1], bytes(corrupt), png + png, bytearray(png), "/tmp/source.png"):
        with pytest.raises(pixels.VisualPixelError):
            run((invalid, sha, size))


def test_exif_and_animation_rejected():
    data = encoded((3, 3))
    with Image.new("RGB", (3, 3), "white") as image:
        output = io.BytesIO()
        exif = Image.Exif()
        exif[274] = 6
        image.save(output, format="PNG", exif=exif)
        with pytest.raises(pixels.VisualPixelError, match="profile_unsupported"):
            run((output.getvalue(), data[1], data[2]))
        output = io.BytesIO()
        with Image.new("RGB", (3, 3), "black") as second:
            image.save(output, format="PNG", save_all=True, append_images=[second])
        with pytest.raises(pixels.VisualPixelError, match="profile_unsupported"):
            run((output.getvalue(), data[1], data[2]))


def test_cancel_before_decode_and_during_rows(monkeypatch):
    data = encoded((8, 8), {(0, 0): (0, 0, 0)})
    with pytest.raises(pixels.VisualPixelError, match="cancelled"):
        run(data, cancelled=lambda: True)
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 12

    with pytest.raises(pixels.VisualPixelError, match="cancelled"):
        run(data, cancelled=cancelled)
    assert calls == 12


def test_expired_deadline_and_native_decode_timeout(monkeypatch):
    data = encoded((8, 8))
    with pytest.raises(pixels.VisualPixelError, match="timeout"):
        run(data, deadline=time.monotonic() - 1)
    original_load = Image.Image.load
    state = {"now": 1.0}

    def load(*args, **kwargs):
        result = original_load(*args, **kwargs)
        state["now"] = 20.0
        return result

    monkeypatch.setattr(Image.Image, "load", load)
    monkeypatch.setattr(pixels.time, "monotonic", lambda: state["now"])
    with pytest.raises(pixels.VisualPixelError, match="timeout"):
        run(data, deadline=10.0)


def test_optional_cv2_reference_has_same_synthetic_geometry():
    pytest.importorskip("cv2")
    from document_files.document_model.recognition_visual import observe_rgb, visual_profile

    colors = {(x, 1): (0, 0, 0) for x in range(2, 7)}
    colors.update({(7, 2): (254, 255, 255), (9, 5): (224, 224, 224)})
    data = encoded((12, 8), colors)
    current = run(data)
    with Image.open(io.BytesIO(data[0])) as image:
        reference = observe_rgb(
            image,
            source_sha="a" * 64,
            page=1,
            render_profile={"visualObservation": visual_profile()},
        )
    assert reference["status"] == "captured"
    for key in (
        "foregroundMaskSha256",
        "lowContrastMaskSha256",
        "foregroundPixelCount",
        "lowContrastPixelCount",
        "foregroundRunCount",
    ):
        assert current[key] == reference[key]
    actual = sorted(
        (c["bounds"], c["pixelCount"], c["lowContrastPixelCount"]) for c in current["components"]
    )
    expected = sorted(
        (c["pixelBounds"], c["foregroundPixelCount"], c["lowContrastPixelCount"])
        for c in reference["components"]
    )
    assert actual == expected
