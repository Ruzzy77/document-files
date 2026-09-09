"""Tiny synthetic local transport fixtures, not real release assets."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def module():
    spec = importlib.util.spec_from_file_location("parts", ROOT / "scripts/release_parts.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def parts(tmp_path):
    tool = module()
    source = tmp_path / "model.pack.zip"
    source.write_bytes(b"0123456789abcdefghijklmnopqrstuvwxyz")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = tool.split(source, tmp_path / "parts", part_size=7)
    return tool, source, manifest, digest


def test_streamed_split_join_preserves_original_identity(parts, tmp_path):
    tool, source, manifest, digest = parts
    transport = tool.verify(manifest, expected_sha256=digest)
    assert [p["size"] for p in transport["parts"]] == [7, 7, 7, 7, 7, 1]
    output = tool.join(manifest, tmp_path / "joined.zip", expected_sha256=digest)
    assert output.read_bytes() == source.read_bytes()
    assert transport["archive"]["sha256"] == digest
    with pytest.raises(FileExistsError):
        tool.join(manifest, output, expected_sha256=digest)
    with pytest.raises(FileExistsError):
        tool.split(source, manifest.parent, part_size=7)


@pytest.mark.parametrize(
    "mutation", ["traversal", "duplicate", "missing", "tamper", "whole-sha", "size", "order"]
)
def test_invalid_transport_is_never_joined(parts, tmp_path, mutation):
    tool, _, manifest, digest = parts
    data = json.loads(manifest.read_text())
    if mutation == "traversal":
        data["parts"][0]["name"] = "../model.pack.zip"
    elif mutation == "duplicate":
        data["parts"][1]["name"] = data["parts"][0]["name"]
    elif mutation == "missing":
        (manifest.parent / data["parts"][0]["name"]).unlink()
    elif mutation == "tamper":
        (manifest.parent / data["parts"][0]["name"]).write_bytes(b"badpart")
    elif mutation == "whole-sha":
        data["archive"]["sha256"] = "a" * 64
    elif mutation == "size":
        data["parts"][0]["size"] += 1
    else:
        data["parts"].reverse()
    manifest.write_text(json.dumps(data))
    output = tmp_path / "joined.zip"
    with pytest.raises((ValueError, OSError)):
        tool.join(manifest, output, expected_sha256=digest)
    assert not output.exists()
    assert not list(tmp_path.glob(".document-files-join-*"))


def test_source_changes_are_detected(parts, tmp_path, monkeypatch):
    tool, source, _, _ = parts
    original = tool.identity
    calls = 0

    def changing(path):
        nonlocal calls
        calls += 1
        value = original(path)
        return (*value[:-1], value[-1] + 1) if calls > 1 else value

    monkeypatch.setattr(tool, "identity", changing)
    with pytest.raises(ValueError, match="changed during splitting"):
        tool.split(source, tmp_path / "changed", part_size=7)
    assert not list((tmp_path / "changed").glob("*.parts.json"))


def test_symlink_parts_and_sources_are_rejected(parts, tmp_path):
    tool, source, manifest, digest = parts
    linked = tmp_path / "linked.zip"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(ValueError, match="symlink"):
        tool.split(linked, tmp_path / "out")
    data = json.loads(manifest.read_text())
    first = manifest.parent / data["parts"][0]["name"]
    first.unlink()
    first.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        tool.verify(manifest, expected_sha256=digest)


def test_wrong_trusted_digest_and_oversized_part_size_are_rejected(parts, tmp_path):
    tool, source, manifest, _ = parts
    with pytest.raises(ValueError, match="expected original"):
        tool.join(manifest, tmp_path / "joined.zip", expected_sha256="f" * 64)
    with pytest.raises(ValueError, match="under 2 GiB"):
        tool.split(source, tmp_path / "too-large", part_size=2 * 1024**3)
