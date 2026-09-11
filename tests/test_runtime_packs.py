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


@pytest.mark.parametrize("machine", ["aarch64", "arm64"])
def test_linux_arm_pack_target_does_not_accept_x64_execution(monkeypatch, machine):
    from document_files import runtime_packs

    monkeypatch.setattr(runtime_packs.platform, "system", lambda: "Linux")
    monkeypatch.setattr(runtime_packs.platform, "machine", lambda: machine)
    monkeypatch.setattr(runtime_packs.platform, "release", lambda: "6.17.0")
    monkeypatch.setattr(runtime_packs.platform, "libc_ver", lambda: ("glibc", "2.39"))
    assert current_target() == "linux-aarch64"
    manifest = {
        "platform": "linux-aarch64",
        "minimumOS": {"name": "linux", "version": "5.15"},
        "minimumGlibc": "2.36",
    }
    runtime_packs.check_host(manifest)
    with pytest.raises(PackError, match="platform_mismatch"):
        runtime_packs.check_host({**manifest, "platform": "linux-x86_64"})
    with pytest.raises(PackError, match="libc_incompatible"):
        runtime_packs.check_host({**manifest, "minimumGlibc": "2.40"})


def fixture_pack(tmp_path: Path, version="1", *, kind="core", extra=None, extra_files=None):
    contents = {"bin/server": b"native executable", "LICENSE": b"Apache License 2.0"}
    if kind == "model":
        contents["model.gguf"] = b"GGUFtest not a real model"
    contents.update(extra_files or {})
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
        # ZipInfo constructor normalizes backslashes on Windows; keep raw hostile bytes.
        info = zipfile.ZipInfo()
        info.filename = info.orig_filename = name
        bundle.writestr(info, b"evil")
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


def installed_cpu_packs(
    tmp_path, *, vision=False, projector=b"GGUFsynthetic projector", runtime_extra=None
):
    store = PackStore(tmp_path / "store")
    runtime = install(store, fixture_pack(tmp_path, kind="llama-cpp-runtime", extra=runtime_extra))
    store.activate("llama-cpp-runtime", "1")
    model = fixture_pack(
        tmp_path,
        kind="model",
        extra_files={"vision.gguf": projector} if vision else None,
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
                **(
                    {
                        "vision": {
                            "file": "vision.gguf",
                            "minImageTokens": 1024,
                            "maxImageTokens": 1536,
                        }
                    }
                    if vision
                    else {}
                ),
            },
        },
    )
    install(store, model)
    store.activate("model", "1")
    return store


@pytest.mark.parametrize("vision", [False, True])
def test_private_cpu_command_and_guaranteed_shutdown(tmp_path, vision):
    store = installed_cpu_packs(tmp_path, vision=vision)
    with (
        # Isolate launch-command assertions from private_fs's real SID subprocess.
        # Native private-storage behavior is exercised by PackStore above and its tests.
        patch("document_files.runtime_packs.private_path"),
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
                assert ("--mmproj" in command) is vision
                if vision:
                    assert Path(command[command.index("--mmproj") + 1]).name == "vision.gguf"
                    assert command[command.index("--image-min-tokens") + 1] == "1024"
                    assert command[command.index("--image-max-tokens") + 1] == "1536"
                assert not any("huggingface" in arg for arg in command)
                raise RuntimeError("consumer failed")
        kill.assert_called_once_with(popen.return_value)
        job.return_value.close.assert_called_once()
        popen.return_value.wait.assert_called_once()
    assert not list(store.root.glob(".llama-*"))


def test_thread_settings_are_explicit_flags_and_fail_closed(tmp_path):
    store = installed_cpu_packs(tmp_path)
    with (
        # Isolate launch-command assertions from private_fs's real SID subprocess.
        # Native private-storage behavior is exercised by PackStore above and its tests.
        patch("document_files.runtime_packs.private_path"),
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


def derived_manifest(tmp_path):
    archive = fixture_pack(
        tmp_path,
        extra_files={
            "derivation/recipe.txt": b"Synthetic recipe",
            "derivation/build.txt": b"Synthetic build record",
        },
    )
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    files = {item["path"]: item for item in manifest["files"]}
    manifest["provenance"].update(
        schemaVersion="document-files.pack-provenance.v2",
        derivedArtifacts=[
            {
                "sha256": "2" * 64,
                "inputs": ["1" * 64],
                "recipe": {k: files["derivation/recipe.txt"][k] for k in ("path", "sha256")},
                "buildEvidence": {k: files["derivation/build.txt"][k] for k in ("path", "sha256")},
            }
        ],
    )
    return manifest


def test_derived_provenance_binds_local_outputs_without_download_urls(tmp_path):
    from document_files.runtime_packs import pack_artifact_digests, validate_manifest

    manifest = derived_manifest(tmp_path)
    assert validate_manifest(manifest) is manifest
    provenance = manifest["provenance"]
    second = json.loads(json.dumps(provenance["derivedArtifacts"][0]))
    second.update(sha256="3" * 64, inputs=["2" * 64, "1" * 64])
    provenance["derivedArtifacts"].append(second)
    assert pack_artifact_digests(provenance) == ["1" * 64, "2" * 64, "3" * 64]
    validate_manifest(manifest)


@pytest.mark.parametrize(
    "fault,code",
    [
        ("legacy-version", "pack_invalid_provenance_version"),
        ("future-version", "pack_invalid_provenance_version"),
        ("invented-url", "pack_invalid_derived_artifact"),
        ("unknown-parent", "pack_invalid_derived_inputs"),
        ("self-cycle", "pack_invalid_derived_inputs"),
        ("duplicate-parent", "pack_invalid_derived_inputs"),
        ("original-as-derived", "pack_invalid_derived_inputs"),
        ("duplicate-derived", "pack_invalid_derived_inputs"),
        ("empty-inputs", "pack_invalid_derived_inputs"),
        ("unsafe-recipe", "pack_unsafe_path"),
        ("missing-evidence", "pack_unbound_derivation_reference"),
        ("stale-evidence", "pack_unbound_derivation_reference"),
    ],
)
def test_invalid_or_unbound_derived_provenance_is_rejected(tmp_path, fault, code):
    from document_files.runtime_packs import validate_manifest

    manifest = derived_manifest(tmp_path)
    provenance = manifest["provenance"]
    item = provenance["derivedArtifacts"][0]
    if fault == "legacy-version":
        del provenance["schemaVersion"]
    elif fault == "future-version":
        provenance["schemaVersion"] = "document-files.pack-provenance.v3"
    elif fault == "invented-url":
        item["uri"] = "https://example.org/original-not-this-built-wheel"
    elif fault in {"unknown-parent", "self-cycle"}:
        item["inputs"] = [("9" if fault == "unknown-parent" else "2") * 64]
    elif fault == "duplicate-parent":
        item["inputs"] *= 2
    elif fault == "original-as-derived":
        item["sha256"] = "1" * 64
    elif fault == "duplicate-derived":
        provenance["derivedArtifacts"].append(item.copy())
    elif fault == "empty-inputs":
        item["inputs"] = []
    elif fault == "unsafe-recipe":
        item["recipe"]["path"] = "../recipe"
    elif fault == "missing-evidence":
        item["buildEvidence"]["path"] = "missing-evidence"
    elif fault == "stale-evidence":
        item["buildEvidence"]["sha256"] = "9" * 64
    with pytest.raises(PackError, match=code):
        validate_manifest(manifest)


def test_builder_hashes_derived_artifacts_and_installer_retains_provenance(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "build_runtime_pack", Path(__file__).parents[1] / "scripts/build_runtime_pack.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    declaration = derived_manifest(tmp_path)
    declaration["entrypoints"] = {}
    declaration["defaultLicense"] = "apache"
    stage = tmp_path / "stage"
    stage.mkdir()
    with zipfile.ZipFile(tmp_path / "core-1.zip") as archive:
        for name in ("LICENSE", "derivation/recipe.txt", "derivation/build.txt"):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(name))
    original, derived = tmp_path / "source.tar", tmp_path / "local.whl"
    original.write_bytes(b"Synthetic original source")
    derived.write_bytes(b"Synthetic derived artifact")
    original_sha, derived_sha = sha256_file(original), sha256_file(derived)
    provenance = declaration["provenance"]
    provenance["sources"][0]["sha256"] = original_sha
    provenance["derivedArtifacts"][0].update(sha256=derived_sha, inputs=[original_sha])
    with pytest.raises(PackError, match="pack_unverified_build_source"):
        builder.build_pack(stage, declaration, tmp_path / "missing.zip", {original_sha: original})
    result = builder.build_pack(
        stage, declaration, tmp_path / "built.zip", {original_sha: original, derived_sha: derived}
    )
    store = PackStore(tmp_path / "store")
    installed = store.install(tmp_path / "built.zip", result["sha256"])
    assert installed.manifest["provenance"]["derivedArtifacts"] == provenance["derivedArtifacts"]
    derived.write_bytes(b"Changed local artifact")
    with pytest.raises(PackError, match="pack_unverified_build_source"):
        builder.build_pack(
            stage,
            declaration,
            tmp_path / "changed.zip",
            {original_sha: original, derived_sha: derived},
        )
    assert not (tmp_path / "changed.zip").exists()


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
        *[
            {"repairBudget": {key: value}}
            for key, maximum in (("batchSize", 2), ("maxImages", 64), ("maxInputPixels", 64000000))
            for value in (True, False, 0, -1, maximum + 1, 1.0, "1", None)
        ],
        {"tableOcrRepair": "off", "repairBudget": {"batchSize": 2}},
        {"tableOcrRepair": "ruled_tables_v1", "repairBudget": {"batchSize": 2}},
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
    before = json.dumps(manifest, sort_keys=True)
    validate_manifest(manifest)
    assert json.dumps(manifest, sort_keys=True) == before  # Never modify immutable pack defaults.
    for budget in (
        {"batchSize": 1, "maxImages": 1, "maxInputPixels": 1},
        {
            "batchSize": 2 if policy == "ruled_cells_v2" else 1,
            "maxImages": 64,
            "maxInputPixels": 64000000,
        },
    ):
        manifest["recognition"]["repairBudget"] = budget
        validate_manifest(manifest)
    manifest["recognition"].update(bad)
    with pytest.raises(PackError, match="unapproved_recognition_configuration"):
        validate_manifest(manifest)


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("policy", ["off", "ruled_tables_v1", "ruled_cells_v2"])
def test_pinned_recognition_policy_reaches_the_same_backend_identity(
    tmp_path, monkeypatch, policy, explicit
):
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
    settings = (
        {
            "batchSize": 2 if policy == "ruled_cells_v2" else 1,
            "maxImages": 64,
            "maxInputPixels": 64000000,
        }
        if explicit
        else {}
    )
    pack.manifest["recognition"]["repairBudget"].update(settings)
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
    for key, field, default in (
        ("batchSize", "repair_batch_size", 1),
        ("maxImages", "repair_max_images", 8),
        ("maxInputPixels", "repair_max_input_pixels", 16000000),
    ):
        assert getattr(backend.config, field) == settings.get(key, default)
        assert backend.identity["configuration"][field] == settings.get(key, default)
    assert backend.identity["configuration"]["table_ocr_repair"] == policy
    assert backend.identity["packManifestSha256"] == "f" * 64


def recognition_loader_pack(tmp_path):
    return fixture_pack(
        tmp_path,
        kind="recognition",
        extra={
            "platform": "linux-aarch64",
            "minimumOS": {"name": "linux", "version": "0"},
            "recognition": {
                "backend": "docling",
                "offline": True,
                "device": "cpu",
                "layout": "heron",
                "tableMode": "accurate",
                "languages": ["kor", "eng"],
                "python": "bin/server",
                "tesseract": "bin/server",
                "artifacts": "bin",
                "tessdata": "data",
                "nativeLibraryDirectories": ["python/lib"],
            },
        },
        extra_files={
            "python/lib/fixture.so": b"not an actual executable",
            **{f"data/{language}.traineddata": b"fixture" for language in ("kor", "eng", "osd")},
            "data/configs/tsv": b"fixture",
        },
    )


@pytest.mark.parametrize(
    "directories",
    [
        None,
        "python/lib",
        [None],
        [[]],
        [""],
        ["."],
        ["/lib"],
        ["../lib"],
        ["python//lib"],
        ["python/lib", "python/lib"],
        ["missing"],
        ["python/lib;other"],
        ["$ORIGIN/lib"],
        [f"lib/{n}" for n in range(9)],
    ],
)
def test_recognition_manifest_rejects_unsafe_loader_directories(tmp_path, directories):
    from document_files.runtime_packs import validate_manifest

    with zipfile.ZipFile(recognition_loader_pack(tmp_path)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    validate_manifest(manifest)
    manifest["recognition"]["nativeLibraryDirectories"] = directories
    with pytest.raises(PackError):
        validate_manifest(manifest)


@pytest.mark.parametrize(
    "target", ["linux-aarch64", "linux-x86_64", "macos-aarch64", "windows-x86_64"]
)
def test_native_loader_directories_are_linux_only_and_optional(tmp_path, target):
    from document_files.runtime_packs import validate_manifest

    with zipfile.ZipFile(recognition_loader_pack(tmp_path)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["platform"] = target
    manifest["minimumOS"]["name"] = target.split("-")[0]
    if target.startswith("linux-"):
        validate_manifest(manifest)
    else:
        with pytest.raises(PackError, match="invalid_native_library_directories"):
            validate_manifest(manifest)
    manifest["recognition"].pop("nativeLibraryDirectories")
    validate_manifest(manifest)


def test_verified_pack_loader_directories_reach_profile_environment(tmp_path, monkeypatch):
    import os

    from document_files import runtime_packs
    from document_files.document_model import docling_adapter
    from document_files.interpretation.backends import ModelError
    from document_files.jobs import ModelProfile
    from document_files.profiles import build_observation_backend

    if os.name == "nt":
        pytest.skip("Linux loader paths use POSIX filesystem paths")
    monkeypatch.setattr(runtime_packs, "current_target", lambda: "linux-aarch64")
    monkeypatch.setattr(docling_adapter.sys, "platform", "linux")
    store = PackStore(tmp_path.resolve() / "store")
    pack = install(store, recognition_loader_pack(tmp_path))
    store.activate("recognition", "1")
    profile = ModelProfile(
        "cpu", "1", "local-pack", {"packRoot": str(store.root), "recognitionPackId": "recognition"}
    )
    backend = build_observation_backend(profile)
    expected = str(pack.root / "python/lib")
    assert backend.config.native_library_directories == (expected,)
    assert backend.identity["configuration"]["native_library_directories"] == (expected,)
    assert backend.identity["packManifestSha256"] == pack.manifest_sha256
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untrusted/host")
    assert backend.worker_environment()["LD_LIBRARY_PATH"] == expected
    # An installed file changed after activation must not bypass PackStore verification.
    (pack.root / "python/lib/fixture.so").write_bytes(b"changed")
    with pytest.raises(ModelError, match="recognition_pack_unavailable"):
        build_observation_backend(profile)


def test_bad_zip_has_safe_typed_error(tmp_path):
    store = PackStore(tmp_path / "store")
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(PackError, match="pack_invalid_archive"):
        install(store, archive)


@pytest.mark.parametrize(
    "change",
    [
        None,
        {},
        {"file": "missing.gguf"},
        {"file": "model.gguf"},
        {"file": "../bad.gguf"},
        {"url": "https://example.invalid/model"},
        {"minImageTokens": True},
        {"minImageTokens": 0},
        {"maxImageTokens": -1},
        {"minImageTokens": 1537},
        {"maxImageTokens": 1537},
        {"maxImageTokens": 1023},
        {"maxImageTokens": 1024.0},
    ],
)
def test_vision_manifest_rejects_unbound_or_unbounded_projector(tmp_path, change):
    from copy import deepcopy

    from document_files.runtime_packs import validate_manifest

    store = installed_cpu_packs(tmp_path, vision=True)
    manifest = deepcopy(store.resolve("model").manifest)
    original = manifest["model"]["vision"]
    manifest["model"]["vision"] = change if change in (None, {}) else {**original, **change}
    with pytest.raises(PackError):
        validate_manifest(manifest)


def test_projector_mutation_and_non_gguf_never_launch(tmp_path):
    store = installed_cpu_packs(tmp_path, vision=True, projector=b"NOPEinvalid gguf")
    with patch("document_files.runtime_packs.subprocess.Popen") as popen:
        with (
            pytest.raises(PackError, match="local_model_invalid_projector"),
            managed_llama_endpoint(store, "llama-cpp-runtime", "model"),
        ):
            pytest.fail("invalid projector must never start")
        popen.assert_not_called()
    model = store.resolve("model")
    model.file("vision.gguf").write_bytes(b"GGUFmutated after installation")
    with patch("document_files.runtime_packs.subprocess.Popen") as popen:
        with (
            pytest.raises(PackError),
            managed_llama_endpoint(store, "llama-cpp-runtime", "model"),
        ):
            pytest.fail("mutated projector must never start")
        popen.assert_not_called()


LINUX_CUDA = {
    "platform": "linux-aarch64",
    "minimumOS": {"name": "linux", "version": "5.15"},
    "accelerator": "cuda",
    "cudaArchitectures": ["121a-real"],
    "minimumDriverVersion": "580.0",
}


@pytest.mark.parametrize(
    "change,code",
    [
        ({"cudaArchitectures": None}, "cuda_architectures"),
        ({"cudaArchitectures": []}, "cuda_architectures"),
        ({"cudaArchitectures": ["sm_121"]}, "cuda_architectures"),
        ({"accelerator": "rocm"}, "invalid_accelerator"),
        ({"minimumDriverVersion": "latest"}, "driver_version"),
        (
            {"platform": "macos-aarch64", "minimumOS": {"name": "macos", "version": "13.3"}},
            "cuda_requires_linux",
        ),
    ],
)
def test_cuda_runtime_manifest_is_explicit_and_linux_only(tmp_path, change, code):
    store = PackStore(tmp_path / "store")
    install(store, fixture_pack(tmp_path, "cuda", kind="llama-cpp-runtime", extra=LINUX_CUDA))
    extra = {k: v for k, v in {**LINUX_CUDA, **change}.items() if v is not None}
    with pytest.raises(PackError, match=code):
        install(store, fixture_pack(tmp_path, "bad", kind="llama-cpp-runtime", extra=extra))


def test_accelerator_belongs_to_runtime_packs_only(tmp_path):
    store = PackStore(tmp_path / "store")
    with pytest.raises(PackError, match="invalid_accelerator"):
        install(store, fixture_pack(tmp_path, "core-cuda", extra={"accelerator": "cuda"}))
    explicit = install(
        store, fixture_pack(tmp_path, "cpu", kind="llama-cpp-runtime", extra={"accelerator": "cpu"})
    )
    assert runtime_packs_module().runtime_accelerator(explicit.manifest) == "cpu"


def runtime_packs_module():
    from document_files import runtime_packs

    return runtime_packs


def test_cuda_runtime_offloads_every_layer_and_keeps_the_projector_on_device(tmp_path, monkeypatch):
    runtime_packs = runtime_packs_module()
    monkeypatch.setattr(runtime_packs, "check_host", lambda manifest: None)
    store = installed_cpu_packs(tmp_path, vision=True, runtime_extra=LINUX_CUDA)
    with (
        patch("document_files.runtime_packs.private_path"),
        patch("document_files.runtime_packs.subprocess.Popen") as popen,
        patch("document_files.runtime_packs.WindowsJob"),
        patch("document_files.runtime_packs.kill_process_tree"),
        patch("document_files.runtime_packs.urllib.request.build_opener") as opener,
    ):
        popen.return_value.poll.return_value = None
        response = opener.return_value.open.return_value.__enter__.return_value
        response.read.return_value = b'{"data":[{"id":"Qwen3.5-9B-Q4_K_M"}]}'
        with managed_llama_endpoint(store, "llama-cpp-runtime", "model") as endpoint:
            assert endpoint.accelerator == "cuda"
            command = popen.call_args.args[0]
            assert command[command.index("--n-gpu-layers") + 1] == "999"
            assert command[command.index("--device") + 1] == "CUDA0"
            assert "--mmproj" in command
            assert not {"--no-mmproj-offload", "--no-op-offload", "--no-kv-offload"} & set(command)
            assert "--parallel" in command and '{"enable_thinking":false}' in command
    from document_files.interpretation.backends import ManagedPackClient

    cuda = ManagedPackClient(store.root, "llama-cpp-runtime", "model")
    assert cuda.identity["accelerator"] == "cuda"
    cpu_store = installed_cpu_packs(tmp_path / "cpu")
    cpu = ManagedPackClient(cpu_store.root, "llama-cpp-runtime", "model")
    assert "accelerator" not in cpu.identity
