"""Synthetic offline candidate verification; no actual downloads or model execution."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_runtime_packs import fixture_pack

ROOT = Path(__file__).parents[1]


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "candidate", ROOT / "scripts/verify_candidate_packs.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    tool = module(monkeypatch)
    hosted = tmp_path / "hosted"
    hosted.mkdir()
    archive = fixture_pack(
        hosted,
        kind="llama-cpp-runtime",
        extra={
            "provenance": {
                "sources": [{"uri": "https://example.org/synthetic", "sha256": "1" * 64}],
                "build": {"sourceCommit": "a" * 40, "dirtySource": False},
            }
        },
    )
    with zipfile.ZipFile(archive) as bundle:
        raw = bundle.read("manifest.json")
    manifest = json.loads(raw)
    inventory = {
        "schemaVersion": "document-files.artifact-inventory.v2",
        "version": "1.8.0",
        "sourceCommit": "a" * 40,
        "dirtySource": False,
        "artifacts": [
            {
                "id": "runtime",
                "kind": "runtime",
                "target": manifest["platform"],
                "path": "assets/" + archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "manifestSha256": hashlib.sha256(raw).hexdigest(),
            }
        ],
    }
    inventory_path = tmp_path / "artifact-inventory.json"
    calls = []

    def fetch(name, directory):
        calls.append(name)
        shutil.copyfile(hosted / name, directory / name)

    def run():
        inventory_path.write_text(json.dumps(inventory))
        digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        return tool.verify(
            inventory_path, digest, "a" * 40, "1.8.0", tmp_path / "verified", fetch=fetch
        )

    return tool, inventory, inventory_path, hosted, calls, run


def test_direct_uploaded_pack_is_verified_without_activation(candidate):
    _, _, path, _, calls, run = candidate
    result = run()
    assert len(result["packs"]) == 1
    assert result["packs"][0]["transport"] == "original"
    assert result["uploadedSubjects"][0]["name"] == calls[0]
    assert result["modelQuality"] == "not-assessed"
    assert not list(path.parent.rglob("active.json"))


def test_multipart_attests_only_actual_uploaded_subjects(candidate):
    tool, inventory, path, hosted, calls, run = candidate
    asset = inventory["artifacts"][0]
    archive = hosted / Path(asset["path"]).name
    transport = tool.release_parts.split(archive, hosted / "parts", part_size=211)
    for part in transport.parent.iterdir():
        shutil.copyfile(part, hosted / part.name)
    asset["transport"] = {
        "manifest": {
            "path": "transport/" + transport.name,
            "sha256": hashlib.sha256(transport.read_bytes()).hexdigest(),
        }
    }
    result = run()
    assert result["packs"][0]["transport"] == "multipart"
    assert archive.name not in calls
    assert transport.name in calls
    assert not list((path.parent / "verified/reconstructed").iterdir())
    assert {r["name"] for r in result["uploadedSubjects"]} == set(calls)


def test_untrusted_inventory_rejected_before_pack_fetch(candidate):
    tool, inventory, path, _, calls, _ = candidate
    path.write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match="checksum"):
        tool.verify(
            path,
            "f" * 64,
            "a" * 40,
            "1.8.0",
            path.parent / "out",
            fetch=lambda *args: calls.append(args),
        )
    assert calls == []


def test_stale_source_inventory_rejected_before_pack_fetch(candidate):
    _, inventory, _, _, calls, run = candidate
    inventory["sourceCommit"] = "b" * 40
    with pytest.raises(ValueError, match="identity"):
        run()
    assert calls == []


def test_same_pack_cannot_be_relabelled_with_other_manifest(candidate):
    _, inventory, _, _, _, run = candidate
    inventory["artifacts"][0]["manifestSha256"] = "f" * 64
    with pytest.raises(ValueError, match="trusted source"):
        run()


def test_cpu_and_candidate_cli_help_does_not_build_or_download(monkeypatch):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for script in ("build_cpu_runtime.py", "verify_candidate_packs.py"):
        output = subprocess.check_output(
            [sys.executable, str(ROOT / "scripts" / script), "--help"], env=env, text=True
        )
        assert "usage:" in output


def test_cpu_runtime_workflow_has_narrow_triggers_matching_hosts_and_real_windows_notice():
    body = (ROOT / ".github/workflows/cpu-runtime.yml").read_text()
    assert (
        "paths: ['scripts/windows_runtime_notice.py', 'tests/test_windows_runtime_notice.py', "
        "'scripts/build_cpu_runtime.py', '.github/workflows/cpu-runtime.yml']"
    ) in body
    assert "workflow_dispatch:" in body and "pull_request:" not in body
    for target in ("macos-aarch64", "macos-x86_64", "linux-x86_64", "windows-x86_64"):
        assert f"target: {target}" in body
    assert '--work "$RUNNER_TEMP/cpu-runtime-build"' in body
    assert "Launch-VsDevShell.ps1" in body and "-SkipAutomaticLocation" in body
    assert "Get-Command cl.exe, dumpbin.exe, cmake.exe" in body
    assert "Get-ChildItem -LiteralPath $installation -Filter License.rtf" in body
    assert "--windows-runtime-license $license" in body and "$hashes.Count -ne 1" in body
    assert "Set-ExecutionPolicy" not in body and "gh release create" not in body


def test_pack_attestation_requires_trusted_inventory_and_only_uploaded_subjects():
    body = (ROOT / ".github/workflows/packs.yml").read_text()
    assert "inventory_sha256:" in body
    assert '--inventory-sha256 "$INVENTORY_SHA256"' in body
    assert "subject-path: ${{ runner.temp }}/verified-candidate/subjects/*" in body
    assert "pack-verification/v2" in body
    assert "reconstructed/*" not in body
    for name in ("packs.yml", "cpu-runtime.yml"):
        workflow = (ROOT / ".github/workflows" / name).read_text()
        assert all(
            re.fullmatch(r"[a-f0-9]{40}", pin)
            for pin in re.findall(r"uses: [^@\s]+@([^\s]+)", workflow)
        )
