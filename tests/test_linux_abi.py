"""Synthetic ABI/probe contracts. These do not qualify a Linux product build."""

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import linux_abi as abi  # noqa: E402

NEEDS = (
    "Version needs section '.gnu.version_r' contains 1 entry:\n"
    "  Name: GLIBC_2.35 Flags: none\n  Name: GLIBCXX_3.4.30 Flags: none\n"
)


def test_only_needed_versions_count_not_exports_or_symbol_listing():
    raw = "Version definition section '.gnu.version_d':\n  Name: GLIBC_2.39\n" + NEEDS
    raw += "Version symbols section '.gnu.version':\n Name: GLIBCXX_3.4.32\n"
    assert abi.requirements(raw) == ["GLIBCXX_3.4.30", "GLIBC_2.35"]
    abi.check_versions(abi.requirements(raw))


@pytest.mark.parametrize(
    "value", ["GLIBC_2.38", "GLIBCXX_3.4.32", "CXXABI_1.3.14", "GLIBC_PRIVATE", "GLIBC_ABI_DT_RELR"]
)
def test_excess_or_unknown_requirements_fail(value):
    with pytest.raises(ValueError):
        abi.check_versions([value])


def test_numeric_version_order_and_boundary():
    abi.check_versions(["GLIBC_2.9", "GLIBC_2.36", "CXXABI_1.3.13", "GLIBCXX_3.4.30"])


@pytest.mark.parametrize(
    "raw,code", [("bad output", 0), (NEEDS.replace("2.35", "2.38"), 0), ("readelf error", 1)]
)
def test_audit_keeps_failed_raw_evidence(tmp_path, monkeypatch, raw, code):
    binary = tmp_path / "binary"
    binary.write_bytes(b"\x7fELFfake")
    monkeypatch.setattr(
        abi.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, code, raw, "")
    )
    evidence = tmp_path / "raw.txt"
    with pytest.raises(ValueError):
        abi.audit(binary, evidence)
    assert evidence.read_text() == raw


def test_audit_binds_exact_bytes_and_evidence(tmp_path, monkeypatch):
    binary = tmp_path / "binary"
    binary.write_bytes(b"\x7fELFfake")
    monkeypatch.setattr(
        abi.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, NEEDS, "")
    )
    evidence = tmp_path / "raw.txt"
    result = abi.audit(binary, evidence)
    assert result["sha256"] == abi.sha(binary)
    assert result["rawEvidence"]["sha256"] == abi.sha(evidence)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a\\b", "C:/drive"])
def test_zip_rejects_unsafe_paths(tmp_path, name):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, b"x")
    with pytest.raises(ValueError):
        abi.unpack(archive, tmp_path / "unpacked")


def test_zip_rejects_symlink_and_existing_output(tmp_path):
    archive = tmp_path / "input.zip"
    entry = zipfile.ZipInfo("link")
    entry.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, b"/etc/passwd")
    with pytest.raises(ValueError):
        abi.unpack(archive, tmp_path / "unpacked")
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("file", b"data")
    abi.unpack(archive, tmp_path / "unpacked")
    with pytest.raises(FileExistsError):
        abi.unpack(archive, tmp_path / "unpacked")


def test_cli_failure_receipt_and_fresh_output(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    output = tmp_path / "evidence"
    monkeypatch.setattr(sys, "argv", ["linux_abi.py", "--tree", str(tree), "--output", str(output)])
    assert abi.main() == 1
    receipt = json.loads((output / "compatibility.json").read_text())
    assert receipt["passed"] is False and receipt["error"] == "No ELF inputs"
    assert receipt["finalImageQualified"] is False
    with pytest.raises(FileExistsError):
        abi.main()


def test_startup_discards_loader_and_python_environment(tmp_path, monkeypatch):
    root, output = tmp_path / "input", tmp_path / "output"
    root.mkdir()
    output.mkdir()
    for name in ["llama-server", "llama-quantize"]:
        (root / name).write_bytes(b"fake")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untrusted")
    monkeypatch.setenv("PYTHONPATH", "/untrusted")

    def run(command, **kwargs):
        assert "LD_LIBRARY_PATH" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
        assert kwargs["cwd"] == output and kwargs["timeout"] == 60
        quantizer = command[-1] == "--help"
        return subprocess.CompletedProcess(
            command, 1 if quantizer else 0, "--allow-requantize" if quantizer else "9dcf84e", ""
        )

    monkeypatch.setattr(abi.subprocess, "run", run)
    assert len(abi.startup(root, "cpu", output)) == 2


def test_actual_selected_compiler_must_be_gcc12(monkeypatch):
    monkeypatch.setattr(abi.subprocess, "check_output", lambda *a, **k: "13.2.0\n")
    with pytest.raises(ValueError, match="actual GCC 12"):
        abi.toolchain()


def test_workflows_keep_four_targets_and_exact_cleanup_and_baseline():
    root = SCRIPTS.parent
    for name in ["cpu-runtime", "recognition-native", "build"]:
        text = (root / ".github/workflows" / f"{name}.yml").read_text()
        assert "- os: ubuntu-22.04\n            target: linux-x86_64" in text
        assert "python:3.12-bookworm" in text and "RepoDigests" in text
        assert 'timeout 300 docker start --attach "$CID"' in text
        assert 'timeout 60 docker rm --force "$CID"' in text
        assert '--cidfile "$EVIDENCE/container-id.txt"' in text
    text = (root / ".github/workflows/build.yml").read_text()
    for target in ["macos-aarch64", "macos-x86_64", "linux-x86_64", "windows-x86_64"]:
        assert "target: " + target in text
    assert '--output "${RUNNER_TEMP}/rhwp-candidate"' in text
    assert "--archive dist/document-files-1.8.0-linux-x86_64.zip --output dist/linux-abi" in text


def test_rhwp_existing_output_rejected_before_commands(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("patched", SCRIPTS / "build_patched_rhwp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        sys,
        "argv",
        ["build_patched_rhwp.py", "--source", str(tmp_path / "new"), "--output", str(tmp_path)],
    )
    monkeypatch.setattr(module, "run", lambda *a, **k: pytest.fail("unexpected command"))
    with pytest.raises(SystemExit):
        module.main()


def test_binary_mutation_during_inspection_is_rejected(tmp_path, monkeypatch):
    binary = tmp_path / "binary"
    binary.write_bytes(b"\x7fELFone")

    def run(*args, **kwargs):
        binary.write_bytes(b"\x7fELFtwo")
        return subprocess.CompletedProcess(args, 0, NEEDS, "")

    monkeypatch.setattr(abi.subprocess, "run", run)
    with pytest.raises(ValueError, match="changed during"):
        abi.audit(binary, tmp_path / "raw.txt")


def test_native_linux_uses_prepared_python_not_jammy_system_python():
    text = (SCRIPTS.parent / ".github/workflows/recognition-native.yml").read_text()
    assert "uv sync --frozen --python 3.12" in text
    assert "uv run --frozen python scripts/build_recognition_native.py" in text
    assert "python3 scripts/build_recognition_native.py" not in text
