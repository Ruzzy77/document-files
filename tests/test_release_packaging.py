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
    with pytest.raises(ValueError, match="identity"):
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
    with pytest.raises(ValueError, match="identity"):
        gate.check(manifest, tmp_path, commit, "1.8.0")


def test_generated_skill_uses_platform_launcher_and_remote_upload_keeps_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_release")
    source = (ROOT / "skills/document-files/SKILL.md").read_text(encoding="utf-8")
    windows = builder.portable_skill(source, windows=True)
    assert 'sh "${SKILL_DIR}/../../launchers/document-files.cmd"' not in windows
    assert '"${SKILL_DIR}/../../launchers/document-files.cmd" capabilities' in windows
    assert 'DOCUMENT_FILES_HOST_PYTHON="$HOST_PYTHON"' not in windows
    unix = builder.portable_skill(source, windows=False)
    assert 'sh "${SKILL_DIR}/../../launchers/document-files" capabilities' in unix
    stage = tmp_path / "skill"
    builder.skill_bundle(stage)
    assert (stage / "agents/openai.yaml").is_file()
    assert "${SKILL_DIR}/scripts/document-files/document-files" in (stage / "SKILL.md").read_text(
        encoding="utf-8"
    )


def test_evaluation_limits_formats_and_never_promotes_or_overwrites_evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from test_regional_interpretation import ReferenceModel

    spec = importlib.util.spec_from_file_location("evaluation_run", ROOT / "evaluation/run.py")
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)

    def public_inputs(directory):
        paths = [directory / "case.txt", directory / "case.html"]
        paths[0].write_text("count: 0", encoding="utf-8")
        paths[1].write_text("<p>count: 0</p>", encoding="utf-8")
        return paths

    monkeypatch.setattr(evaluator, "fixtures", public_inputs)
    args = SimpleNamespace(
        kind="local",
        output=tmp_path / "evidence",
        options=None,
        case_format=["txt"],
        holdout_only=False,
        holdout=None,
    )
    model = ReferenceModel()
    evaluator.evaluate(args, model, None)
    report = json.loads((args.output / "local_model.json").read_text())
    assert [c["format"] for c in report["cases"]] == ["txt"]
    assert report["passed"] is False
    assert report["cases"][0]["semanticReview"]["status"] == "pending"
    assert report["cases"][0]["holdout"] is False
    before = model.calls
    with pytest.raises(ValueError, match="not overwritten"):
        evaluator.evaluate(args, model, None)
    assert model.calls == before


def test_candidate_output_rejects_existing_same_version_wheel(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_release")
    builder.prepare_output(tmp_path)
    (tmp_path / "document_files-1.8.0-py3-none-any.whl").write_bytes(b"stale")
    with pytest.raises(ValueError, match="new or empty"):
        builder.prepare_output(tmp_path)


def test_stable_builder_rejects_dirty_source_but_development_is_explicit(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_release")
    monkeypatch.setattr(
        builder.subprocess,
        "check_output",
        lambda command, **kwargs: "a" * 40 if "rev-parse" in command else " M source.py",
    )
    with pytest.raises(ValueError, match="clean source"):
        builder.source_identity(development=False)
    assert builder.source_identity(development=True) == ("a" * 40, True)


def test_builder_builds_wheel_instead_of_accepting_one():
    body = (ROOT / "scripts/build_release.py").read_text()
    assert 'command(args.uv, "build", "--out-dir", output, cwd=ROOT)' in body
    assert "if not wheel.is_file()" not in body
