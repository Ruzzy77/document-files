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


def test_five_platform_python_inputs_have_pinned_digests():
    pins = json.loads((ROOT / "scripts/python-runtimes.json").read_text())
    assert set(pins["targets"]) == {
        "macos-aarch64",
        "macos-x86_64",
        "windows-x86_64",
        "linux-x86_64",
        "linux-aarch64",
    }
    for item in pins["targets"].values():
        assert item["url"].startswith("https://github.com/astral-sh/python-build-standalone/")
        assert len(bytes.fromhex(item["sha256"])) == 32
    arm = pins["targets"]["linux-aarch64"]
    assert "3.12.14%2B20260901-aarch64-unknown-linux-gnu-install_only" in arm["url"]
    assert arm["sha256"] == "b61b856c3e1a4fc65b8f6e6b0495ef975dd0924f90c59f3ea61b38a079173b84"


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


def test_sdist_includes_the_windows_preparation_entrypoint_and_release_helpers():
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    line = next(
        line for line in manifest.splitlines() if line.startswith("recursive-include scripts ")
    )
    assert {"*.py", "*.ps1"} <= set(line.split()[2:])
    for name in (
        "prepare_windows_build.ps1",
        "windows_build_evidence.py",
        "build_release_image.py",
        "review_operational.py",
    ):
        assert (ROOT / "scripts" / name).is_file()


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
    body = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")
    assert 'command(args.uv, "build", "--out-dir", output, cwd=ROOT)' in body
    assert "if not wheel.is_file()" not in body


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_packaged_python_checks_actual_machine_and_version(monkeypatch, tmp_path, machine):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_release")
    checks = []
    monkeypatch.setattr(builder, "verify_native_target", lambda *args: checks.append(args))
    actual = {"version": "3.12.14", "machine": machine}
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *a, **k: json.dumps(actual))
    python = tmp_path / "python"
    assert builder.verify_python_runtime(python, "linux-aarch64", "3.12.14") == actual
    assert checks == [(python, "linux-aarch64")]
    with pytest.raises(SystemExit, match="version/architecture mismatch"):
        builder.verify_python_runtime(python, "linux-x86_64", "3.12.14")
    actual["version"] = "3.12.13"
    with pytest.raises(SystemExit, match="version/architecture mismatch"):
        builder.verify_python_runtime(python, "linux-aarch64", "3.12.14")


def test_linux_python_foreign_elf_rejected_before_any_execution(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_release")
    import linux_abi

    python = tmp_path / "python"
    python.write_bytes(b"wrong ELF")
    checks = []

    def reject(binary, target):
        checks.append((binary, target))
        raise ValueError("foreign ELF")

    monkeypatch.setattr(linux_abi, "inspect_header", reject)
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *a, **k: pytest.fail("executed"))
    with pytest.raises(ValueError, match="foreign ELF"):
        builder.verify_python_runtime(python, "linux-aarch64", "3.12.14")
    assert checks == [(python.resolve(), "linux-aarch64")]


def test_native_arm_ci_keeps_x64_and_uses_explicit_bookworm_architecture():
    for name in ("build.yml", "cpu-runtime.yml"):
        body = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert '"linux-aarch64":"ubuntu-22.04-arm"' in body
        assert '"linux-x86_64":"ubuntu-22.04"' in body
        assert "linux-aarch64) PROBE_PLATFORM=linux/arm64; EXPECTED_ARCH=arm64" in body
        assert 'docker pull --platform "$PROBE_PLATFORM" python:3.12-bookworm' in body
        assert '--target "$TARGET" --startup' in body
        assert "container-image.json" in body
        assert "--privileged" not in body


def test_core_linux_targets_do_not_claim_claude_desktop_platform():
    body = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")
    assert 'if not target.startswith("linux-"):' in body


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_all_existing_rhwp_platform_resolvers_recognize_linux_arm(monkeypatch, machine):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    provision = load_script("provision_rhwp")
    from document_files import extraction_rhwp, rhwp_backend

    monkeypatch.setattr(provision.platform, "system", lambda: "Linux")
    monkeypatch.setattr(provision.platform, "machine", lambda: machine)
    assert provision.platform_key() == "linux-aarch64"
    assert extraction_rhwp._platform_key() == "linux-aarch64"
    assert rhwp_backend._platform_key() == "linux-aarch64"


def test_unconfigured_host_python_launcher_remains_architecture_neutral():
    body = (ROOT / "openai-runtime/document-files").read_text(encoding="utf-8")
    assert "PYTHON=${DOCUMENT_FILES_HOST_PYTHON:-python3}" in body
    assert "sys.version_info >= (3, 11)" in body
    assert "pip install" not in body and "uv run" not in body


@pytest.mark.parametrize(
    "target,triple", [("linux-aarch64", "AARCH64"), ("linux-x86_64", "X86_64")]
)
def test_patched_rhwp_passes_actual_linux_target_to_linker_and_abi(
    monkeypatch, tmp_path, target, triple
):
    import sys

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    builder = load_script("build_patched_rhwp")
    import linux_abi

    source, output = tmp_path / "source", tmp_path / "output"
    binary = source / "target/release/rhwp"
    commands, audits = [], []
    monkeypatch.setattr(builder, "platform_key", lambda: target)
    monkeypatch.setattr(linux_abi, "toolchain", lambda: {"fixture": "gcc12"})

    def run(*args, **kwargs):
        commands.append((args, kwargs))
        if args[0] == "cargo":
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"not a real executable")

    def audit(binary_path, evidence, *, target):
        audits.append((binary_path, target))
        evidence.write_text("fixture raw ABI evidence")
        return {"target": target}

    monkeypatch.setattr(builder, "run", run)
    monkeypatch.setattr(linux_abi, "audit", audit)
    monkeypatch.setattr(builder, "rustc_version", lambda *a: "fixture pinned Rust")
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *a, **k: f"rhwp v{builder.VERSION}"
    )
    monkeypatch.setattr(
        sys, "argv", ["build_patched_rhwp.py", "--source", str(source), "--output", str(output)]
    )
    builder.main()
    cargo = next(kwargs for args, kwargs in commands if args[0] == "cargo")
    assert cargo["env"][f"CARGO_TARGET_{triple}_UNKNOWN_LINUX_GNU_LINKER"] == linux_abi.CC
    assert audits == [(binary, target)]
    metadata = json.loads((output / "build.json").read_text())
    assert metadata["linuxAbi"] == {"target": target}
