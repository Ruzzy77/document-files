"""Release packaging contract and fail-closed gate regressions."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_four_platform_python_inputs_have_pinned_digests():
    pins = json.loads((ROOT / "scripts/python-runtimes.json").read_text())
    assert set(pins["targets"]) == {
        "macos-aarch64",
        "macos-x86_64",
        "windows-x86_64",
        "linux-x86_64",
    }
    for item in pins["targets"].values():
        assert item["url"].startswith("https://github.com/astral-sh/python-build-standalone/")
        assert len(bytes.fromhex(item["sha256"])) == 32


def test_launchers_cannot_provision_during_document_processing():
    for name in (
        "document-files",
        "document-files-mcp",
        "document-files.cmd",
        "document-files-mcp.cmd",
        "run.py",
    ):
        body = (ROOT / "launchers" / name).read_text()
        assert "uv run" not in body
        assert "pip install" not in body
        assert "urlretrieve" not in body


def test_runtime_license_provenance_matches_contents():
    directory = ROOT / "scripts/python-licenses"
    provenance = json.loads((directory / "SOURCE.json").read_text())
    for name, digest in provenance["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest


def test_stable_gate_rejects_missing_checks_and_stale_identity(tmp_path):
    gate = load_script("check_release_qualification")
    manifest = tmp_path / "qualification.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "document-files.qualification.v1",
                "version": "1.8.0",
                "sourceCommit": "a" * 40,
                "checks": [],
            }
        )
    )
    with pytest.raises(ValueError, match="Missing successful"):
        gate.check(manifest, tmp_path, "a" * 40, "1.8.0")
    with pytest.raises(ValueError, match="identity"):
        gate.check(manifest, tmp_path, "b" * 40, "1.8.0")


def test_stable_gate_does_not_accept_mock_model_generic_test_record(tmp_path):
    gate = load_script("check_release_qualification")
    commit = "a" * 40
    generic = {
        "version": "1.8.0",
        "sourceCommit": commit,
        "tests": [{"id": "fake", "passed": True}],
        "model": {"adapter": "scripted-test", "model": "test"},
    }
    raw = json.dumps(generic).encode()
    (tmp_path / "evidence.json").write_bytes(raw)
    manifest = tmp_path / "qualification.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "document-files.qualification.v1",
                "version": "1.8.0",
                "sourceCommit": commit,
                "checks": [
                    {
                        "id": name,
                        "passed": True,
                        "evidence": {
                            "path": "evidence.json",
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        },
                    }
                    for name in gate.REQUIRED
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="Actual model"):
        gate.check(manifest, tmp_path, commit, "1.8.0")
