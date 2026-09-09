"""Exact executable source identity fixtures; no model inference."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]


def identity_helper():
    spec = importlib.util.spec_from_file_location(
        "identity", ROOT / "evaluation/execution_identity.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def make_execution_artifacts(root, identity):
    helper = identity_helper()
    package, evaluator = root / "installed-package", root / "evaluator"
    package.mkdir()
    evaluator.mkdir()
    (package / "__init__.py").write_text('__version__ = "1.8.0"\n')
    (evaluator / "run.py").write_text("# synthetic evaluator\n")
    (evaluator / "execution_identity.py").write_text("# synthetic identity helper\n")
    wheel = root / "document_files-1.8.0-py3-none-any.whl"
    contents = {
        "document_files/__init__.py": (package / "__init__.py").read_bytes(),
        "document_files-1.8.0.dist-info/METADATA": b"Name: document-files\nVersion: 1.8.0\n",
    }
    rows = [
        [
            name,
            "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="),
            str(len(raw)),
        ]
        for name, raw in contents.items()
    ]
    record_name = "document_files-1.8.0.dist-info/RECORD"
    rows.append([record_name, "", ""])
    output = io.StringIO()
    csv.writer(output).writerows(rows)
    contents[record_name] = output.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in contents.items():
            archive.writestr(name, raw)
    source = root / "document_files-1.8.0.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for path in evaluator.iterdir():
            archive.add(path, arcname="document_files-1.8.0/evaluation/" + path.name)
        raw = b"# synthetic recorder\n"
        member = tarfile.TarInfo("document_files-1.8.0/scripts/measure_cpu_execution.py")
        member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
    build = {
        **identity,
        "schemaVersion": "document-files.build-inventory.v2",
        "candidateMode": "stable",
        "target": "linux-x86_64",
        "artifacts": {p.name: helper.sha256(p) for p in (wheel, source)},
    }
    receipt = root / "execution-build.json"
    receipt.write_text(json.dumps(build))
    assets = [
        {
            "id": name,
            "kind": "core",
            "target": "linux-x86_64",
            "path": path.name,
            "sha256": helper.sha256(path),
            "buildReceipt": {"path": receipt.name, "sha256": helper.sha256(receipt)},
        }
        for name, path in (("executed-wheel", wheel), ("evaluator-source", source))
    ]
    execution = {
        "verification": "wheel-record-and-source-exact.v1",
        "coreArtifactId": "executed-wheel",
        "coreArtifactSha256": helper.sha256(wheel),
        "packageTreeSha256": helper.tree_digest(helper.wheel_package(wheel)),
        "packageFileCount": 1,
        "evaluatorArtifactId": "evaluator-source",
        "evaluatorArtifactSha256": helper.sha256(source),
        "evaluatorFilesSha256": helper.tree_digest(helper.sdist_evaluator(source)),
        "recorderSha256": helper.recorder_digest(source),
    }
    return assets, execution, package, evaluator


def test_actual_imported_tree_matches_wheel_without_any_git(tmp_path, monkeypatch):
    identity = {"sourceCommit": "a" * 40, "version": "1.8.0", "dirtySource": False}
    assets, expected, package, evaluator = make_execution_artifacts(tmp_path, identity)
    helper = identity_helper()
    monkeypatch.setattr(helper, "sys", SimpleNamespace(modules={}))
    inventory = {**identity, "artifacts": assets}
    args = (
        inventory,
        tmp_path / "inventory.json",
        tmp_path,
        [a["id"] for a in assets],
        "executed-wheel",
        "evaluator-source",
        package,
        evaluator,
        {"adapter": "cloud-test"},
        None,
    )
    actual, commit = helper.verify_execution(*args)
    assert actual == expected and commit == "a" * 40
    (package / "__init__.py").write_text("# a different same-version installed wheel\n")
    with pytest.raises(ValueError, match="imported document_files tree"):
        helper.verify_execution(*args)


def test_extra_importable_files_or_changed_evaluator_are_rejected(tmp_path, monkeypatch):
    identity = {"sourceCommit": "a" * 40, "version": "1.8.0", "dirtySource": False}
    assets, _, package, evaluator = make_execution_artifacts(tmp_path, identity)
    helper = identity_helper()
    monkeypatch.setattr(helper, "sys", SimpleNamespace(modules={}))
    args = (
        {**identity, "artifacts": assets},
        tmp_path / "inventory.json",
        tmp_path,
        [a["id"] for a in assets],
        "executed-wheel",
        "evaluator-source",
        package,
        evaluator,
        {"adapter": "cloud-test"},
        None,
    )
    extra = package / "unexpected.py"
    extra.write_text("# extra\n")
    with pytest.raises(ValueError, match="imported document_files tree"):
        helper.verify_execution(*args)
    extra.unlink()
    (evaluator / "run.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="executed evaluator"):
        helper.verify_execution(*args)
