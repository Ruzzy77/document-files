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
    review_path = root / document["redistributionReview"]["path"]
    review = json.loads(review_path.read_bytes())
    review["artifactInventory"] = document["artifactInventory"]
    review_path.write_text(json.dumps(review))
    document["redistributionReview"]["sha256"] = hashlib.sha256(
        review_path.read_bytes()
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


@pytest.mark.parametrize(
    "include_required,multipart", [(False, False), (True, False), (True, True)]
)
def test_required_sources_public_private_metadata_optional(
    evidence, monkeypatch, include_required, multipart
):
    _, document, run, root = evidence
    tool = promotion(monkeypatch)
    inventory_path = root / document["artifactInventory"]["path"]
    inventory = json.loads(inventory_path.read_bytes())
    for name in ["corresponding-source.tar.gz", "private-review.txt"]:
        path = root / name
        path.write_bytes(b"synthetic " + name.encode())
        asset = {
            "id": name,
            "kind": "metadata",
            "path": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if multipart and name.startswith("corresponding"):
            transport = tool.release_parts.split(path, root / "source-parts", part_size=11)
            asset["transport"] = {
                "manifest": {
                    "path": str(transport.relative_to(root)),
                    "sha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
                }
            }
        inventory["artifacts"].append(asset)
    inventory_path.write_text(json.dumps(inventory))
    document["artifactInventory"]["sha256"] = hashlib.sha256(
        inventory_path.read_bytes()
    ).hexdigest()
    review_path = root / document["redistributionReview"]["path"]
    review = json.loads(review_path.read_bytes())
    review["artifactInventory"] = document["artifactInventory"]
    source = next(a for a in inventory["artifacts"] if a["id"].startswith("corresponding"))
    review["artifacts"][0]["requiredPublicArtifacts"] = [
        {
            "artifactId": source["id"],
            "sha256": source["sha256"],
            "role": "source",
            "reason": "Synthetic required corresponding sources.",
        }
    ]
    review_path.write_text(json.dumps(review))
    document["redistributionReview"]["sha256"] = hashlib.sha256(
        review_path.read_bytes()
    ).hexdigest()
    if include_required:
        document["releaseAssets"].append(source["id"])
    run()
    if not include_required:
        with pytest.raises(ValueError, match="omit"):
            tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
        return
    files = tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
    assert "private-review.txt" not in {a["id"] for a in files}
    assert any(a["id"].startswith(source["id"]) for a in files)
    if multipart:
        assert any(a["kind"] == "transport-part" for a in files)
        part = next(a for a in files if a["kind"] == "transport-part")
        part["verifiedPath"].write_bytes(b"corrupt")
    else:
        (root / source["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        tool.verify(root / "manifest.json", root, document["sourceCommit"], document["version"])
