from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from document_files.runtime_packs import (
    PackError,
    PackStore,
    current_target,
    managed_llama_endpoint,
    sha256_file,
)


def fixture_pack(tmp_path: Path, version="1", *, kind="core", extra=None):
    contents = {"bin/server": b"native executable", "LICENSE": b"Apache License 2.0"}
    if kind == "model":
        contents["model.gguf"] = b"GGUFtest not a real model"
    manifest = {
        "schemaVersion": "document-files.pack.v1",
        "id": kind,
        "version": version,
        "kind": kind,
        "platform": "any" if kind == "model" else current_target(),
        "minimumOS": {"name": current_target().split("-")[0], "version": "0"},
        "files": [
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "license": "apache",
                "executable": name.startswith("bin/"),
            }
            for name, data in contents.items()
        ],
        "licenses": [{"id": "apache", "spdx": "Apache-2.0", "path": "LICENSE"}],
        "provenance": {
            "sources": [{"uri": "https://example.org/audited-test-source", "sha256": "1" * 64}]
        },
        "compatibleRuntimes": [],
        "entrypoints": {"server": "bin/server", "quantize": "bin/server"},
    }
    if extra:
        manifest.update(extra)
    target = tmp_path / f"{kind}-{version}.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, data in contents.items():
            archive.writestr(name, data)
    return target


def install(store, archive):
    return store.install(archive, sha256_file(archive))


def test_offline_install_activation_rollback_does_not_touch_results(tmp_path):
    store = PackStore(tmp_path / "store")
    result = tmp_path / "existing-result.json"
    result.write_text('{"source": "unchanged"}')
    for version in ("1", "2"):
        install(store, fixture_pack(tmp_path, version))
        store.activate("core", version)
    assert store.resolve("core").manifest["version"] == "2"
    assert store.rollback("core").manifest["version"] == "1"
    assert result.read_text() == '{"source": "unchanged"}'
    assert store.rollback("core").manifest["version"] == "2"


def test_checksum_is_mandatory_and_no_overwrite(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    with pytest.raises(PackError, match="archive_hash_mismatch"):
        store.install(archive, "0" * 64)
    install(store, archive)
    with pytest.raises(PackError, match="version_exists"):
        install(store, archive)
    assert not (store.root / "active.json").exists()


def test_manifest_limit_is_enforced_on_import_and_resolution(tmp_path, monkeypatch):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    imported = install(store, archive)
    monkeypatch.setattr("document_files.runtime_packs.MAX_MANIFEST_BYTES", 64)
    with pytest.raises(PackError, match="pack_manifest_limit"):
        store.resolve(imported.manifest["id"], imported.manifest["version"])
    with pytest.raises(PackError, match="pack_manifest_limit"):
        store.install(archive, sha256_file(archive))


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "C:/windows",
        "a\\b",
        "CON.txt",
        "a/../../x",
        "a/./x",
        "trailing. ",
    ],
)
def test_hostile_paths_rejected_before_install(tmp_path, name):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr(name, b"evil")
    with pytest.raises(PackError, match="unsafe_path"):
        install(store, archive)
    assert not (store.root / "packs").exists()


def test_archive_links_collisions_limits_and_unlisted_files(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    with pytest.raises(PackError, match="archive_limit"):
        store.install(archive, sha256_file(archive), max_bytes=1)
    with zipfile.ZipFile(archive, "a") as bundle:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "outside")
    with pytest.raises(PackError, match="nonregular"):
        install(store, archive)
    archive = fixture_pack(tmp_path, "2")
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("license", b"case collision")
    with pytest.raises(PackError, match="duplicate_path"):
        install(store, archive)
    archive = fixture_pack(tmp_path, "3")
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("extra", b"not inventoried")
    with pytest.raises(PackError, match="inventory_mismatch"):
        install(store, archive)


def test_corrupt_payload_never_activated(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(
        tmp_path,
        extra={
            "files": [
                {"path": "bin/server", "size": 17, "sha256": "0" * 64, "license": "apache"},
                {"path": "LICENSE", "size": 18, "sha256": "0" * 64, "license": "apache"},
            ]
        },
    )
    with pytest.raises(PackError, match="file_hash_mismatch"):
        install(store, archive)
    assert not (store.root / "active.json").exists()
    assert not list(store.root.glob(".install-*"))


def test_tampered_install_and_activation_fail_closed(tmp_path):
    store = PackStore(tmp_path / "store")
    installed = install(store, fixture_pack(tmp_path))
    store.activate("core", "1")
    installed.file("bin/server").write_bytes(b"tampered")
    with pytest.raises(PackError, match="file_hash_mismatch"):
        store.resolve("core")
    assert store.inspect()["packs"]["core"]["current"]["version"] == "1"


def test_busy_store_and_foreign_runtime(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    (store.root / ".write.lock").write_text("operator must inspect")
    with pytest.raises(PackError, match="store_busy"):
        install(store, archive)
    (store.root / ".write.lock").unlink()
    foreign = "windows-x86_64" if current_target() != "windows-x86_64" else "linux-x86_64"
    archive = fixture_pack(tmp_path, "2", extra={"platform": foreign})
    install(store, archive)
    with pytest.raises(PackError, match="platform_mismatch"):
        store.activate("core", "2")


def installed_cpu_packs(tmp_path):
    store = PackStore(tmp_path / "store")
    runtime = install(store, fixture_pack(tmp_path, kind="llama-cpp-runtime"))
    store.activate("llama-cpp-runtime", "1")
    model = fixture_pack(
        tmp_path,
        kind="model",
        extra={
            "compatibleRuntimes": [
                {
                    "id": "llama-cpp-runtime",
                    "version": "1",
                    "manifestSha256": runtime.manifest_sha256,
                }
            ],
            "model": {
                "file": "model.gguf",
                "name": "Qwen3.5-9B",
                "quantization": "Q4_K_M",
                "tokenizer": "embedded-gguf",
                "chatTemplate": "embedded-gguf",
            },
        },
    )
    install(store, model)
    store.activate("model", "1")
    return store


def test_private_cpu_command_and_guaranteed_shutdown(tmp_path):
    store = installed_cpu_packs(tmp_path)
    with (
        patch("document_files.runtime_packs.subprocess.Popen") as popen,
        patch("document_files.runtime_packs.WindowsJob") as job,
        patch("document_files.runtime_packs.kill_process_tree") as kill,
        patch("document_files.runtime_packs.urllib.request.build_opener") as opener,
    ):
        popen.return_value.poll.return_value = None
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b'{"data":[{"id":"Qwen3.5-9B-Q4_K_M"}]}'
        with pytest.raises(RuntimeError, match="consumer failed"):  # noqa: SIM117
            with managed_llama_endpoint(store, "llama-cpp-runtime", "model") as endpoint:
                assert endpoint.base_url.startswith("http://127.0.0.1:")
                assert endpoint.base_url.endswith("/v1")
                assert endpoint.api_key not in repr(endpoint)
                command = popen.call_args.args[0]
                assert "--api-key-file" in command and endpoint.api_key not in command
                assert command[command.index("--parallel") + 1] == "1"
                assert command[command.index("--n-gpu-layers") + 1] == "0"
                assert command[command.index("--device") + 1] == "none"
                assert '{"enable_thinking":false}' in command
                assert "--no-mmproj-offload" in command
                assert not any("huggingface" in arg for arg in command)
                raise RuntimeError("consumer failed")
        kill.assert_called_once_with(popen.return_value)
        job.return_value.close.assert_called_once()
        popen.return_value.wait.assert_called_once()
    assert not list(store.root.glob(".llama-*"))


def test_thread_settings_are_explicit_flags_and_fail_closed(tmp_path):
    store = installed_cpu_packs(tmp_path)
    with (
        patch("document_files.runtime_packs.subprocess.Popen") as popen,
        patch("document_files.runtime_packs.WindowsJob"),
        patch("document_files.runtime_packs.kill_process_tree"),
        patch("document_files.runtime_packs.urllib.request.build_opener") as opener,
    ):
        popen.return_value.poll.return_value = None
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b'{"data":[{"id":"Qwen3.5-9B-Q4_K_M"}]}'
        with managed_llama_endpoint(
            store, "llama-cpp-runtime", "model", threads=4, threads_batch=10
        ):
            command = popen.call_args.args[0]
            assert command[command.index("--threads") + 1] == "4"
            assert command[command.index("--threads-batch") + 1] == "10"
        with managed_llama_endpoint(store, "llama-cpp-runtime", "model"):
            command = popen.call_args.args[0]
            assert "--threads" not in command and "--threads-batch" not in command
        assert popen.call_count == 2
    invalid_settings = (
        {"threads": 0},
        {"threads": True},
        {"threads_batch": 1025},
        {"threads_batch": 2.0},
    )
    for invalid in invalid_settings:
        with (
            pytest.raises(PackError, match="local_model_invalid_budget"),
            managed_llama_endpoint(store, "llama-cpp-runtime", "model", **invalid),
        ):
            pass


def test_reproducible_builder_requires_matching_source_evidence(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "build_runtime_pack", Path(__file__).parents[1] / "scripts/build_runtime_pack.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / "upstream.zip"
    source.write_bytes(b"audited upstream")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "LICENSE").write_text("Apache License")
    archive = fixture_pack(tmp_path)
    with zipfile.ZipFile(archive) as bundle:
        declaration = json.loads(bundle.read("manifest.json"))
    declaration["entrypoints"] = {}
    declaration["defaultLicense"] = "apache"
    declaration["provenance"]["sources"][0]["sha256"] = sha256_file(source)
    with pytest.raises(PackError, match="unverified_build_source"):
        builder.build_pack(stage, declaration, tmp_path / "fail.zip", {})
    outputs = [tmp_path / "a.zip", tmp_path / "b.zip"]
    for output in outputs:
        builder.build_pack(stage, declaration, output, {sha256_file(source): source})
    assert sha256_file(outputs[0]) == sha256_file(outputs[1])
    store = PackStore(tmp_path / "store")
    assert install(store, outputs[0]).manifest["id"] == "core"


def test_unicode_normalization_collision_rejected(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = fixture_pack(tmp_path)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("caf\u00e9", b"first")
        bundle.writestr("cafe\u0301", b"second")
    with pytest.raises(PackError, match="duplicate_path"):
        install(store, archive)


def test_recognition_rejects_intel_native_and_online_configuration(tmp_path):
    from document_files.runtime_packs import validate_manifest

    archive = fixture_pack(tmp_path)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    manifest["kind"] = "recognition"
    manifest["platform"] = "macos-x86_64"
    manifest["recognition"] = {
        "backend": "docling",
        "offline": True,
        "device": "cpu",
        "layout": "heron",
        "tableMode": "accurate",
        "languages": ["kor", "eng"],
    }
    with pytest.raises(PackError, match="intel_recognition_requires_linux_container"):
        validate_manifest(manifest)
    manifest["platform"] = "linux-x86_64"
    manifest["recognition"]["offline"] = False
    with pytest.raises(PackError, match="unapproved_recognition_configuration"):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "bad",
    [
        {"tableOcrRepair": "try-all-engines"},
        {"repairBudget": {"maxCalls": True}},
        {"repairBudget": {"maxPixels": 64000001}},
        {"repairBudget": {"unknown": 1}},
    ],
)
@pytest.mark.parametrize("policy", ["off", "ruled_tables_v1", "ruled_cells_v2"])
def test_recognition_policy_is_pinned_and_budgeted_in_manifest(tmp_path, bad, policy):
    from document_files.runtime_packs import validate_manifest

    with zipfile.ZipFile(fixture_pack(tmp_path)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest.update(
        kind="recognition", platform="linux-x86_64", minimumOS={"name": "linux", "version": "0"}
    )
    manifest["recognition"] = {
        "backend": "docling",
        "offline": True,
        "device": "cpu",
        "layout": "heron",
        "tableMode": "accurate",
        "languages": ["kor", "eng"],
        "python": "bin/server",
        "tesseract": "bin/server",
        "artifacts": "bin",
        "tessdata": "bin",
        "tableOcrRepair": policy,
        "repairBudget": {"maxCalls": 2},
    }
    validate_manifest(manifest)
    manifest["recognition"].update(bad)
    with pytest.raises(PackError, match="unapproved_recognition_configuration"):
        validate_manifest(manifest)


@pytest.mark.parametrize("policy", ["off", "ruled_tables_v1", "ruled_cells_v2"])
def test_pinned_recognition_policy_reaches_the_same_backend_identity(tmp_path, monkeypatch, policy):
    from types import SimpleNamespace

    from document_files.jobs import ModelProfile
    from document_files.profiles import build_observation_backend

    (tmp_path / "artifacts").mkdir()
    (tmp_path / "data/configs").mkdir(parents=True)
    for name in [
        "python",
        "tesseract",
        "data/kor.traineddata",
        "data/eng.traineddata",
        "data/osd.traineddata",
        "data/configs/tsv",
    ]:
        (tmp_path / name).touch()
    pack = SimpleNamespace(
        root=tmp_path,
        file=lambda name: tmp_path / name,
        manifest_sha256="f" * 64,
        manifest={
            "kind": "recognition",
            "version": "1",
            "recognition": {
                "python": "python",
                "tesseract": "tesseract",
                "artifacts": "artifacts",
                "tessdata": "data",
                "tableOcrRepair": policy,
                "repairBudget": {"maxCalls": 2},
            },
        },
    )
    monkeypatch.setattr(
        "document_files.profiles.PackStore", lambda _: SimpleNamespace(resolve=lambda _: pack)
    )
    backend = build_observation_backend(
        ModelProfile(
            "cpu", "1", "local-pack", {"packRoot": str(tmp_path), "recognitionPackId": "ocr"}
        )
    )
    assert backend.config.table_ocr_repair == policy
    assert backend.config.repair_max_calls == 2
    assert backend.identity["configuration"]["table_ocr_repair"] == policy
    assert backend.identity["packManifestSha256"] == "f" * 64


def test_bad_zip_has_safe_typed_error(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(PackError, match="pack_invalid_archive"):
        install(store, archive)
