"""Synthetic release promotion contracts; no network or published artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from test_qualification_gate import evidence as evidence

ROOT = Path(__file__).parents[1]


def promotion(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("promotion", ROOT / "scripts/promote_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_promotes_only_the_exact_qualified_inventory(evidence, monkeypatch):
    _, document, run, root = evidence
    run()
    tool = promotion(monkeypatch)
    files = tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
    assert {a["id"] for a in files} == set(document["releaseAssets"]) | {"@artifact-inventory"}
    (root / "model.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])


def test_incomplete_release_assets_are_rejected(evidence, monkeypatch):
    _, document, run, root = evidence
    document["releaseAssets"].remove("model")
    run()
    with pytest.raises(ValueError, match="omit"):
        promotion(monkeypatch).verify(
            root / "manifest.json", root, document["sourceCommit"], document["version"]
        )


def test_existing_release_and_conflicting_tag_are_never_overwritten(monkeypatch):
    tool = promotion(monkeypatch)
    monkeypatch.setattr(tool, "optional_api", lambda path: {"id": 1})
    with pytest.raises(ValueError, match="never overwritten"):
        tool.verify_remote("v1.8.0", "a" * 40)
    monkeypatch.setattr(
        tool,
        "optional_api",
        lambda path: (
            None if "/releases/" in path else {"object": {"type": "commit", "sha": "b" * 40}}
        ),
    )
    with pytest.raises(ValueError, match="different source"):
        tool.verify_remote("v1.8.0", "a" * 40)
    monkeypatch.setattr(
        tool,
        "optional_api",
        lambda path: (
            None if "/releases/" in path else {"object": {"type": "commit", "sha": "a" * 40}}
        ),
    )
    tool.verify_remote("v1.8.0", "a" * 40)


def test_default_cli_is_local_dry_run(monkeypatch, tmp_path, capsys):
    tool = promotion(monkeypatch)
    monkeypatch.setattr(tool.gate, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.8.0"\n')
    monkeypatch.setattr(
        tool.subprocess,
        "check_output",
        lambda command, **kw: "a" * 40 if "rev-parse" in command else "",
    )
    monkeypatch.setattr(tool, "verify", lambda *args: [])
    monkeypatch.setattr(tool, "publish", lambda *args: pytest.fail("dry run must not publish"))
    monkeypatch.setattr(tool, "optional_api", lambda *args: pytest.fail("dry run stays local"))
    monkeypatch.setattr(
        sys, "argv", ["promote_release.py", "qualification.json", "--evidence-root", str(tmp_path)]
    )
    tool.main()
    assert json.loads(capsys.readouterr().out)["published"] is False


def test_draft_is_not_promoted_if_downloaded_bytes_differ(monkeypatch, tmp_path):
    tool = promotion(monkeypatch)
    path = tmp_path / "core.zip"
    path.write_bytes(b"qualified")
    files = [{"verifiedPath": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]
    monkeypatch.setattr(tool, "verify_remote", lambda *args: None)
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[:2] == ("release", "download"):
            directory = Path(args[args.index("--dir") + 1])
            (directory / path.name).write_bytes(b"not the same bytes")
        return "{}"

    monkeypatch.setattr(tool, "gh", fake_gh)
    with pytest.raises(ValueError, match="draft bytes mismatch"):
        tool.publish(files, "v1.8.0", "a" * 40, tmp_path / "public-notes.md")
    assert any(call[:2] == ("release", "create") for call in calls)
    assert not any(call[:2] == ("release", "edit") for call in calls)


def test_oversized_github_asset_is_rejected_before_upload(monkeypatch):
    from types import SimpleNamespace

    tool = promotion(monkeypatch)
    asset = {"verifiedPath": SimpleNamespace(stat=lambda: SimpleNamespace(st_size=2 * 1024**3))}
    with pytest.raises(ValueError, match="under 2 GiB"):
        tool.check_asset_sizes([asset])


def test_multipart_uploads_parts_but_keeps_original_artifact_identity(evidence, monkeypatch):
    _, document, run, root = evidence
    tool = promotion(monkeypatch)
    original = root / "model.zip"
    transport = tool.release_parts.split(original, root / "transport", part_size=11)
    inventory_path = root / "artifacts.json"
    inventory = json.loads(inventory_path.read_text())
    model = next(a for a in inventory["artifacts"] if a["id"] == "model")
    model["transport"] = {
        "manifest": {
            "path": transport.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
        }
    }
    inventory_path.write_text(json.dumps(inventory))
    document["artifactInventory"]["sha256"] = hashlib.sha256(
        inventory_path.read_bytes()
    ).hexdigest()
    run()
    files = tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
    assert original not in [a["verifiedPath"] for a in files]
    assert transport in [a["verifiedPath"] for a in files]
    assert any(a["kind"] == "transport-part" for a in files)
    # A correct original archive does not excuse corrupt transport bytes.
    first = tool.release_parts.load_manifest(transport)["parts"][0]
    (transport.parent / first["name"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="part size"):
        tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
