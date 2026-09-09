"""Host identity projection tests; no real Docker or model execution."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from test_qualification_gate import evidence as evidence
from test_qualification_gate import gate_module

ROOT = Path(__file__).parents[1]


def tool(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "host_identity", ROOT / "scripts/capture_container_identity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_container_receipt_and_image_id_are_bound(evidence, monkeypatch):
    _, document, run, root = evidence
    run()
    collector = tool(monkeypatch)
    check = next(c for c in document["checks"] if c["id"] == "local_model")
    original = json.loads((root / check["containerIdentityReceipt"]["path"]).read_text())
    monkeypatch.setattr(
        collector,
        "inspect",
        lambda kind, *_: (
            original["dockerInspect"] if kind == "container" else original["imageInspect"]
        ),
    )
    execution_path = root / check["executionReceipt"]["path"]
    monkeypatch.setattr(collector, "container_receipt", lambda *_: execution_path.read_bytes())
    output = root / "captured.json"
    observed = collector.capture(
        root,
        root / "artifacts.json",
        document["artifactInventory"]["sha256"],
        "image",
        execution_path,
        "c" * 64,
        "/evidence/run/receipt.json",
        output,
    )
    assert observed["imageId"] == "sha256:" + "6" * 64
    assert observed["imageInspect"]["repoDigests"] == ["registry/image@sha256:" + "5" * 64]
    assert observed["containerReceiptSha256"] == check["executionReceipt"]["sha256"]
    with pytest.raises(ValueError, match="never overwritten"):
        collector.capture(
            root,
            root / "artifacts.json",
            document["artifactInventory"]["sha256"],
            "image",
            execution_path,
            "c" * 64,
            "/evidence/run/receipt.json",
            output,
        )


def test_capture_does_not_accept_another_run_receipt(evidence, monkeypatch):
    _, document, run, root = evidence
    run()
    collector = tool(monkeypatch)
    check = next(c for c in document["checks"] if c["id"] == "local_model")
    original = json.loads((root / check["containerIdentityReceipt"]["path"]).read_text())
    monkeypatch.setattr(
        collector,
        "inspect",
        lambda kind, *_: (
            original["dockerInspect"] if kind == "container" else original["imageInspect"]
        ),
    )
    monkeypatch.setattr(collector, "container_receipt", lambda *_: b"another run")
    with pytest.raises(ValueError, match="original run receipt"):
        collector.capture(
            root,
            root / "artifacts.json",
            document["artifactInventory"]["sha256"],
            "image",
            root / check["executionReceipt"]["path"],
            "c" * 64,
            "/evidence/run/receipt.json",
            root / "not-written.json",
        )
    assert not (root / "not-written.json").exists()


@pytest.mark.parametrize("fault", ["missing", "image-id", "receipt", "run", "network", "mount"])
def test_gate_rejects_declared_or_mismatched_image_execution(evidence, fault):
    _, document, run, root = evidence
    run()
    check = next(c for c in document["checks"] if c["id"] == "local_model")
    if fault == "missing":
        check.pop("containerIdentityReceipt")
    else:
        ref = check["containerIdentityReceipt"]
        path = root / ref["path"]
        host = json.loads(path.read_text())
        if fault == "image-id":
            host["imageId"] = "sha256:" + "5" * 64  # registry digest is not image ID
        elif fault == "receipt":
            host["containerReceiptSha256"] = "f" * 64
        elif fault == "run":
            host["executionRunId"] = "another-run"
        elif fault == "network":
            host["dockerInspect"]["networkMode"] = "bridge"
        else:
            host["dockerInspect"]["mounts"] = [{"destination": "/usr/local/lib", "readOnly": True}]
        path.write_text(json.dumps(host))
        ref["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        gate_module().check(manifest, root, document["sourceCommit"], document["version"])


def test_inspect_projection_never_requests_environment_or_commands(monkeypatch):
    collector = tool(monkeypatch)
    assert ".Image" in collector.CONTAINER_FORMAT
    assert ".RepoDigests" in collector.IMAGE_FORMAT
    for field in (".Config.Env", ".Config.Cmd", ".Config.Entrypoint", ".Path", ".Args"):
        assert field not in collector.CONTAINER_FORMAT + collector.IMAGE_FORMAT


def test_local_image_needs_actual_image_id_but_not_invented_registry_digest(evidence):
    _, document, run, root = evidence
    run()
    inventory_path = root / document["artifactInventory"]["path"]
    inventory = json.loads(inventory_path.read_text())
    image = next(a for a in inventory["artifacts"] if a["kind"] == "image")
    receipt_path = root / image["buildReceipt"]["path"]
    build = json.loads(receipt_path.read_text())
    image.pop("imageDigest")
    build.pop("imageDigest")
    receipt_path.write_text(json.dumps(build))
    image["buildReceipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    inventory_path.write_text(json.dumps(inventory))
    document["artifactInventory"]["sha256"] = hashlib.sha256(
        inventory_path.read_bytes()
    ).hexdigest()
    verified = gate_module().artifact_inventory(
        document, root, document["sourceCommit"], document["version"]
    )
    assert verified["image"]["imageId"] == "sha256:" + "6" * 64


def test_collector_rejects_container_names_before_docker(monkeypatch, tmp_path):
    collector = tool(monkeypatch)
    monkeypatch.setattr(collector, "inspect", lambda *_: pytest.fail("must reject before Docker"))
    with pytest.raises(ValueError, match="full Docker container ID"):
        collector.capture(
            tmp_path,
            tmp_path / "inventory.json",
            "a" * 64,
            "image",
            tmp_path / "receipt.json",
            "some-container-name",
            "/evidence/receipt.json",
            tmp_path / "output.json",
        )
