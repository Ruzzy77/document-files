"""Local image-builder contracts; fake Docker never qualifies a real image."""

import base64
import csv
import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "image_builder", ROOT / "scripts/build_release_image.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
COMMIT = "a" * 40
BASE = "python@sha256:" + "b" * 64
BASE_ID = "sha256:" + "c" * 64
CONFIG = b'{"rootfs":{"type":"layers","diff_ids":[]}}'
IMAGE = "sha256:" + hashlib.sha256(CONFIG).hexdigest()


def archive(path, files):
    with tarfile.open(path, "w:gz" if path.name.endswith(".gz") else "w") as out:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            out.addfile(item, io.BytesIO(data))


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "deployment").mkdir(parents=True)
    (root / "evaluation").mkdir()
    (root / "scripts").mkdir()
    (root / "patches/rhwp").mkdir(parents=True)
    (root / "patches/rhwp/checkbox-preservation.patch").write_text("selected patch")
    shutil.copyfile(ROOT / "scripts/linux_abi.py", root / "scripts/linux_abi.py")
    shutil.copyfile(
        ROOT / "scripts/build_release_image.py", root / "scripts/build_release_image.py"
    )
    shutil.copyfile(
        ROOT / "evaluation/execution_identity.py", root / "evaluation/execution_identity.py"
    )
    for name in ("Dockerfile", "entrypoint.py"):
        shutil.copyfile(ROOT / "deployment" / name, root / "deployment" / name)
    (root / "src/document_files").mkdir(parents=True)
    (root / "src/document_files/__init__.py").write_text('VERSION="1.8.0"\n')
    (root / "pyproject.toml").write_text('[project]\nversion="1.8.0"\n')
    monkeypatch.setattr(builder, "ROOT", root)
    monkeypatch.setattr(builder, "source_identity", lambda: (COMMIT, "1.8.0"))
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    wheel = wheelhouse / "document_files-1.8.0-py3-none-any.whl"
    members = {
        "document_files/__init__.py": (root / "src/document_files/__init__.py").read_bytes(),
        "document_files-1.8.0.dist-info/METADATA": b"Name: document-files\nVersion: 1.8.0\n",
    }
    record = "document_files-1.8.0.dist-info/RECORD"
    target = io.StringIO()
    writer = csv.writer(target)
    for name, data in members.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        writer.writerow([name, "sha256=" + encoded, len(data)])
    writer.writerow([record, "", ""])
    with zipfile.ZipFile(wheel, "w") as out:
        for name, data in members.items():
            out.writestr(name, data)
        out.writestr(record, target.getvalue())
    source = tmp_path / "document_files-1.8.0.tar.gz"
    archive(
        source,
        {
            "document_files-1.8.0/" + p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        },
    )
    core_archive = tmp_path / "document-files-1.8.0-linux-x86_64.zip"
    binary = b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00" + b"fixture-not-executable"
    native = {
        "version": "0.8.6+pat.checkbox.1",
        "baseCommit": builder.RHWP_UPSTREAM,
        "patchSha256": builder.sha(root / "patches/rhwp/checkbox-preservation.patch"),
        "binarySha256": hashlib.sha256(binary).hexdigest(),
    }
    core_build = {
        "version": "1.8.0",
        "sourceCommit": COMMIT,
        "dirtySource": False,
        "target": "linux-x86_64",
        "wheelSha256": builder.sha(wheel),
        "rhwp": native,
    }
    with zipfile.ZipFile(core_archive, "w") as out:
        for name, raw in {
            "BUILD.json": json.dumps(core_build).encode(),
            "rhwp/rhwp": binary,
            "rhwp/build.json": json.dumps(native).encode(),
            "rhwp/LICENSE": b"upstream MIT notice",
        }.items():
            info = zipfile.ZipInfo("document-files/" + name)
            info.external_attr = (0o100755 if name == "rhwp/rhwp" else 0o100644) << 16
            out.writestr(info, raw)
    receipt = tmp_path / "core.json"
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": "document-files.build-inventory.v2",
                "version": "1.8.0",
                "sourceCommit": COMMIT,
                "dirtySource": False,
                "candidateMode": "stable",
                "target": "linux-x86_64",
                "artifacts": {
                    wheel.name: builder.sha(wheel),
                    source.name: builder.sha(source),
                    core_archive.name: builder.sha(core_archive),
                },
            }
        )
    )
    inventory = tmp_path / "wheels.json"
    inventory.write_text(json.dumps({wheel.name: builder.sha(wheel)}))
    lock = tmp_path / "requirements.lock"
    lock.write_text("document-files==1.8.0 --hash=sha256:" + builder.sha(wheel) + "\n")
    return SimpleNamespace(
        output=tmp_path / "out",
        core_archive=core_archive,
        core_receipt=receipt,
        core_receipt_sha256=builder.sha(receipt),
        wheel=wheel,
        source=source,
        wheelhouse=wheelhouse,
        wheelhouse_inventory=inventory,
        wheelhouse_inventory_sha256=builder.sha(inventory),
        requirements=lock,
        requirements_sha256=builder.sha(lock),
        base_image=BASE,
        base_image_id=BASE_ID,
    )


def fake_docker(inputs, calls, *, fail=None, wrong_core=False):
    alive = False
    probe = {}

    def execute(command, log, **kwargs):
        nonlocal alive
        calls.append(command)
        log.write_text("")
        if fail and fail in command:
            raise ValueError("fake command failure")
        if command[1:3] == ["image", "inspect"]:
            base = command[3] == BASE
            return json.dumps(
                {
                    "imageId": BASE_ID if base else IMAGE,
                    "os": "linux",
                    "architecture": "amd64",
                    "repoDigests": [BASE] if base else [],
                    "user": "" if base else "10001:10001",
                    "entrypoint": [] if base else ["python", "/opt/document-files-entrypoint.py"],
                }
            )
        if command[1] == "version":
            return '"test-version"'
        if command[1] == "build":
            Path(command[command.index("--iidfile") + 1]).write_text(IMAGE)
        if command[1] == "create":
            alive = True
            probe.update(
                id="d" * 64,
                name="/" + command[command.index("--name") + 1],
                imageId=IMAGE,
                runId=command[command.index("--label") + 1].split("=", 1)[1],
            )
            Path(command[command.index("--cidfile") + 1]).write_text(probe["id"])
            return probe["id"]
        if command[1] == "start":
            _, native = builder.portable_rhwp(
                inputs.core_archive, COMMIT, "1.8.0", builder.sha(inputs.wheel)
            )
            raw = json.dumps(
                {
                    "core": {} if wrong_core else builder.wheel_tree(inputs.wheel),
                    "rhwp": {k: native[k] for k in ("path", "version", "files")},
                }
            )
            log.write_text(raw)
            return raw
        if command[1:3] == ["container", "ls"]:
            return probe["id"] if alive else ""
        if command[1:3] == ["container", "rm"]:
            assert command[-1] == probe["id"]
            alive = False
        if command[1:3] == ["container", "inspect"]:
            return json.dumps(
                {"running": False, "exitCode": 0} if ".State.Running" in command[-1] else probe
            )

        if command[1:3] == ["image", "save"]:
            path = Path(command[command.index("--output") + 1])
            archive(
                path,
                {
                    "manifest.json": json.dumps([{"Config": "config.json", "Layers": []}]).encode(),
                    "config.json": CONFIG,
                },
            )
        return ""

    return execute


def test_build_exact_context_actual_id_and_archive_without_qualification(inputs, monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "execute", fake_docker(inputs, calls))
    result = builder.build(inputs)
    assert result["status"] == "built-unqualified" and result["releaseQualification"] is False
    assert result["imageId"] == IMAGE and "imageDigest" not in result
    assert result["archiveSha256"] == builder.sha(inputs.output / result["archive"])
    assert result["builder"]["daemonNetworkIsolation"] == "not-assessed"
    command = next(c for c in calls if c[1] == "build")
    assert {"--pull=false", "--network=none", "--no-cache", "--platform=linux/amd64"} <= set(
        command
    )
    assert not any("--push" in c or "pull" in c for c in calls)
    assert set(result["inputs"]["context"]) == {
        "Dockerfile",
        "entrypoint.py",
        "requirements.lock",
        "wheelhouse/" + inputs.wheel.name,
        "rhwp/rhwp",
        "rhwp/LICENSE",
        "rhwp/build.json",
    }


@pytest.mark.parametrize("stage", ["inspect", "build", "start", "save"])
def test_failed_docker_command_keeps_failure_receipt(inputs, monkeypatch, stage):
    monkeypatch.setattr(builder, "execute", fake_docker(inputs, [], fail=stage))
    with pytest.raises(ValueError):
        builder.build(inputs)
    report = json.loads((inputs.output / "image-build.json").read_text())
    assert report["status"] == "failed" and report["endedAt"]
    assert report["archiveSha256"] is None and report["imageId"] is None


def test_installed_package_must_match_selected_wheel(inputs, monkeypatch):
    monkeypatch.setattr(builder, "execute", fake_docker(inputs, [], wrong_core=True))
    with pytest.raises(ValueError, match="Installed core"):
        builder.build(inputs)


@pytest.mark.parametrize(
    "change", ["extra-wheel", "lock", "recipe", "source", "core-receipt", "tag"]
)
def test_unverified_or_mixed_inputs_never_reach_docker(inputs, monkeypatch, change):
    if change == "extra-wheel":
        (inputs.wheelhouse / "stale.whl").write_bytes(b"stale")
    elif change == "lock":
        inputs.requirements.write_text("--index-url https://example.com\n")
        inputs.requirements_sha256 = builder.sha(inputs.requirements)
    elif change == "recipe":
        (builder.ROOT / "deployment/Dockerfile").write_text("FROM wrong\n")
    elif change == "source":
        (builder.ROOT / "src/document_files/__init__.py").write_text("wrong")
    elif change == "tag":
        inputs.base_image = "python:latest"
    else:
        inputs.core_receipt_sha256 = "0" * 64
    monkeypatch.setattr(builder, "execute", lambda *a: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError):
        builder.build(inputs)
    assert json.loads((inputs.output / "image-build.json").read_text())["status"] == "failed"


def test_existing_output_is_untouched(inputs):
    inputs.output.mkdir()
    marker = inputs.output / "existing"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        builder.build(inputs)
    assert marker.read_text() == "keep"


def test_export_config_must_match_docker_image_id(tmp_path):
    path = tmp_path / "image.tar"
    archive(
        path, {"manifest.json": b'[{"Config":"config.json","Layers":[]}]', "config.json": CONFIG}
    )
    with pytest.raises(ValueError, match="Export differs"):
        builder.export_identity(path, BASE_ID)


def test_changed_input_after_build_fails_and_keeps_export_for_diagnosis(inputs, monkeypatch):
    original = fake_docker(inputs, [])

    def changing(command, log, **kwargs):
        result = original(command, log, **kwargs)
        if command[1:3] == ["image", "save"]:
            inputs.requirements.write_text("changed during build")
        return result

    monkeypatch.setattr(builder, "execute", changing)
    with pytest.raises(ValueError, match="inputs changed"):
        builder.build(inputs)
    report = json.loads((inputs.output / "image-build.json").read_text())
    assert report["status"] == "failed" and report["archiveSha256"] is None
    assert list(inputs.output.glob("*-image.tar"))


def test_duplicate_inventory_keys_are_not_silently_overwritten(inputs, monkeypatch):
    value = builder.sha(inputs.wheel)
    inputs.wheelhouse_inventory.write_text(
        '{"' + inputs.wheel.name + '":"' + value + '","' + inputs.wheel.name + '":"' + value + '"}'
    )
    inputs.wheelhouse_inventory_sha256 = builder.sha(inputs.wheelhouse_inventory)
    monkeypatch.setattr(builder, "execute", lambda *a: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="Duplicate JSON"):
        builder.build(inputs)


def test_wrong_actual_base_id_cannot_build(inputs, monkeypatch):
    inputs.base_image_id = IMAGE
    calls = []
    monkeypatch.setattr(builder, "execute", fake_docker(inputs, calls))
    with pytest.raises(ValueError, match="Local base image differs"):
        builder.build(inputs)
    assert not any(c[1] == "build" for c in calls)


def test_symlink_input_rejected_without_following(tmp_path):
    real = tmp_path / "real"
    real.write_text("source")
    link = tmp_path / "link"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("Host has no symlink capability")
    with pytest.raises(ValueError, match="non-symlink"):
        builder.checked(link)


def test_dockerfile_uses_builtin_frontend_and_reinstalls_only_checked_wheels():
    recipe = (ROOT / "deployment/Dockerfile").read_text()
    assert "# syntax=" not in recipe
    assert "pip install --force-reinstall --no-index --require-hashes" in recipe
    assert "--find-links=/opt/wheelhouse -r /opt/requirements.lock" in recipe


def layer_export(path, *, compressed=False, altered=False, reverse=False, count=False):
    import gzip

    layers = [b"first layer tar bytes", b"second layer tar bytes"]
    config = json.dumps(
        {
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + hashlib.sha256(x).hexdigest() for x in layers],
            }
        }
    ).encode()
    names = ["one/layer.tar", "two/layer.tar"]
    values = [gzip.compress(x) for x in layers] if compressed else list(layers)
    if altered:
        values[0] += b"changed"
    order = list(reversed(names)) if reverse else names[:1] if count else names
    archive(
        path,
        {
            "manifest.json": json.dumps([{"Config": "config.json", "Layers": order}]).encode(),
            "config.json": config,
            **dict(zip(names, values, strict=True)),
        },
    )
    return "sha256:" + hashlib.sha256(config).hexdigest()


@pytest.mark.parametrize("compressed", [False, True])
def test_export_verifies_uncompressed_diff_ids_in_order(tmp_path, compressed):
    path = tmp_path / "image.tar"
    identity = layer_export(path, compressed=compressed)
    builder.export_identity(path, identity)


@pytest.mark.parametrize("change", ["altered", "reverse", "count"])
def test_modified_layer_content_order_or_count_is_rejected(tmp_path, change):
    path = tmp_path / "image.tar"
    identity = layer_export(path, **{change: True})
    with pytest.raises(ValueError, match="layer|DiffID"):
        builder.export_identity(path, identity)


def test_compressed_layer_resource_limit_is_on_uncompressed_bytes(tmp_path, monkeypatch):
    path = tmp_path / "image.tar"
    identity = layer_export(path, compressed=True)
    monkeypatch.setattr(builder, "MAX_LAYER_TOTAL_BYTES", 10)
    with pytest.raises(ValueError, match="Uncompressed layer"):
        builder.export_identity(path, identity)


def test_export_metadata_count_and_file_size_limits(tmp_path, monkeypatch):
    path = tmp_path / "image.tar"
    identity = layer_export(path)
    monkeypatch.setattr(builder, "MAX_EXPORT_MEMBERS", 1)
    with pytest.raises(ValueError, match="Too many"):
        builder.export_identity(path, identity)
    monkeypatch.setattr(builder, "MAX_EXPORT_BYTES", 1)
    with pytest.raises(ValueError, match="size bound"):
        builder.export_identity(path, identity)


def test_probe_create_timeout_recovers_id_by_unique_run_label_and_removes_only_it(
    inputs, monkeypatch
):
    import subprocess

    calls = []
    original = fake_docker(inputs, calls)

    def interrupted(command, log, **kwargs):
        result = original(command, log, **kwargs)
        if command[1] == "create":
            raise subprocess.TimeoutExpired("docker create", 1)
        return result

    monkeypatch.setattr(builder, "execute", interrupted)
    with pytest.raises(subprocess.TimeoutExpired):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_text())
    assert receipt["probeCleanup"] == {"status": "removed", "containerId": "d" * 64}
    assert [c for c in calls if c[1:3] == ["container", "rm"]] == [
        ["docker", "container", "rm", "--force", "d" * 64]
    ]


def test_probe_identity_conflict_never_removes_container(inputs, monkeypatch):
    calls = []
    original = fake_docker(inputs, calls)

    def conflict(command, log, **kwargs):
        result = original(command, log, **kwargs)
        if command[1:3] == ["container", "inspect"] and log.name == "probe-cleanup-inspect.json":
            found = json.loads(result)
            found["imageId"] = BASE_ID
            return json.dumps(found)
        return result

    monkeypatch.setattr(builder, "execute", conflict)
    with pytest.raises(ValueError, match="cleanup incomplete"):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_text())
    assert receipt["probeCleanup"]["status"] == "failed" and receipt["imageId"] is None
    assert not any(c[1:3] == ["container", "rm"] for c in calls)


def test_cancelled_probe_is_removed_and_failure_receipt_survives(inputs, monkeypatch):
    original = fake_docker(inputs, [])

    def cancel(command, log, **kwargs):
        if command[1] == "start":
            raise KeyboardInterrupt()
        return original(command, log, **kwargs)

    monkeypatch.setattr(builder, "execute", cancel)
    with pytest.raises(KeyboardInterrupt):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_text())
    assert receipt["status"] == "failed" and receipt["probeCleanup"]["status"] == "removed"


def test_each_command_uses_remaining_budget_not_a_fresh_timeout(inputs, monkeypatch):
    original = fake_docker(inputs, [])
    values = []

    def timed(command, log, **kwargs):
        if not log.name.startswith("probe-cleanup"):
            values.append(kwargs["timeout"])
        return original(command, log, **kwargs)

    monkeypatch.setattr(builder, "execute", timed)
    inputs.timeout_seconds = 10
    builder.build(inputs)
    assert all(0 < x <= 10 for x in values)
    assert values == sorted(values, reverse=True)


def test_real_process_timeout_terminates_owned_group_and_records_it(tmp_path):
    import os
    import subprocess
    import sys

    if os.name != "posix":
        pytest.skip("POSIX process group integration; Windows taskkill remains separate")
    events = []
    with pytest.raises(subprocess.TimeoutExpired):
        builder.execute(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            tmp_path / "sleep.log",
            timeout=0.05,
            events=events,
        )
    assert events[0]["status"] == "timed-out"
    assert "cleanupError" not in events[0]
    assert events[0]["terminationRequested"] and events[0]["leaderReaped"]
    assert events[0]["daemonWorkTermination"] == "not-verified"
    with pytest.raises(ProcessLookupError):
        os.killpg(events[0]["processGroupId"], 0)


def test_timeout_process_group_contract_without_host_signal_permissions(tmp_path, monkeypatch):
    import subprocess
    from enum import IntEnum

    class PosixSignal(IntEnum):
        SIGTERM = 15
        SIGKILL = 9

    class Child:
        pid = 43210
        calls = 0

        def wait(self, *, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    child = Child()
    spawned = []
    signals = []

    def popen(command, **kwargs):
        spawned.append(kwargs)
        return child

    # Simulate the complete POSIX boundary, including APIs absent on Windows.
    # Do not change the shared os.name used by pathlib/pytest or require host SIGKILL.
    monkeypatch.setattr(
        builder,
        "os",
        SimpleNamespace(name="posix", killpg=lambda pid, signum: signals.append((pid, signum))),
    )
    monkeypatch.setattr(builder, "signal", PosixSignal)
    monkeypatch.setattr(builder.subprocess, "Popen", popen)
    events = []
    with pytest.raises(subprocess.TimeoutExpired):
        builder.execute(["fixture"], tmp_path / "group.log", timeout=0.01, events=events)
    assert spawned[0]["start_new_session"] is True
    assert signals == [
        (child.pid, PosixSignal.SIGTERM),
        (child.pid, 0),
        (child.pid, PosixSignal.SIGKILL),
    ]
    assert events[0]["leaderReaped"] and "cleanupError" not in events[0]


def test_tar_pax_read_allocation_is_bounded():
    with pytest.raises(ValueError, match="metadata/chunk bound"):
        builder.BoundedTarReads(io.BytesIO(b"small")).read(builder.MAX_METADATA_BYTES + 1)


def test_invalid_compressed_layer_fails_closed(tmp_path):
    path = tmp_path / "image.tar"
    identity = layer_export(path, compressed=True, altered=True)
    with pytest.raises((ValueError, OSError)):
        builder.export_identity(path, identity)


def rewrite_portable(inputs, change):
    with zipfile.ZipFile(inputs.core_archive) as archive:
        entries = [(info, archive.read(info)) for info in archive.infolist()]
    entries = change(entries)
    with zipfile.ZipFile(inputs.core_archive, "w") as archive:
        for info, raw in entries:
            archive.writestr(info, raw)
    receipt = json.loads(inputs.core_receipt.read_bytes())
    receipt["artifacts"][inputs.core_archive.name] = builder.sha(inputs.core_archive)
    inputs.core_receipt.write_text(json.dumps(receipt))
    inputs.core_receipt_sha256 = builder.sha(inputs.core_receipt)


@pytest.mark.parametrize(
    "mutation",
    [
        "wheel",
        "source",
        "target",
        "patch",
        "upstream",
        "binary",
        "notice",
        "missing",
        "traversal",
        "symlink",
        "mode",
    ],
)
def test_native_inputs_are_checked_even_with_updated_core_archive_hash(
    inputs, monkeypatch, mutation
):
    def change(entries):
        result = []
        for info, raw in entries:
            if info.filename == "document-files/BUILD.json":
                build = json.loads(raw)
                if mutation in ("wheel", "source", "target"):
                    build[
                        {"wheel": "wheelSha256", "source": "sourceCommit", "target": "target"}[
                            mutation
                        ]
                    ] = "wrong"
                if mutation in ("patch", "upstream"):
                    build["rhwp"]["patchSha256" if mutation == "patch" else "baseCommit"] = "b" * (
                        64 if mutation == "patch" else 40
                    )
                raw = json.dumps(build).encode()
            if info.filename == "document-files/rhwp/build.json" and mutation in (
                "patch",
                "upstream",
            ):
                native = json.loads(raw)
                native["patchSha256" if mutation == "patch" else "baseCommit"] = "b" * (
                    64 if mutation == "patch" else 40
                )
                raw = json.dumps(native).encode()
            if info.filename == "document-files/rhwp/rhwp":
                if mutation == "binary":
                    raw = b"wrong binary"
                elif mutation == "mode":
                    info.external_attr = 0o100644 << 16
                elif mutation == "symlink":
                    info.external_attr = 0o120777 << 16
            if info.filename == "document-files/rhwp/LICENSE":
                if mutation == "notice":
                    raw = b" "
                elif mutation == "missing":
                    continue
                elif mutation == "traversal":
                    info.filename = "../LICENSE"
            result.append((info, raw))
        return result

    rewrite_portable(inputs, change)
    monkeypatch.setattr(builder, "execute", lambda *a, **k: pytest.fail("Docker must not run"))
    with pytest.raises((ValueError, OSError)):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_bytes())
    assert receipt["status"] == "failed" and receipt["imageId"] is None


@pytest.mark.parametrize("field", ["path", "version", "files"])
def test_wrong_installed_native_proof_removes_probe_and_rejects_image(inputs, monkeypatch, field):
    original = fake_docker(inputs, [])

    def incorrect(command, log, **kwargs):
        result = original(command, log, **kwargs)
        if command[1] == "start":
            value = json.loads(result)
            value["rhwp"][field] = {} if field == "files" else "wrong"
            result = json.dumps(value)
            log.write_text(result)
        return result

    monkeypatch.setattr(builder, "execute", incorrect)
    with pytest.raises(ValueError, match="Installed rhwp"):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_bytes())
    assert receipt["probeCleanup"]["status"] == "removed"
    assert receipt["status"] == "failed" and receipt["archiveSha256"] is None


def test_native_probe_is_fixed_readonly_and_inside_existing_budget(inputs, monkeypatch):
    import ast

    calls = []
    monkeypatch.setattr(builder, "execute", fake_docker(inputs, calls))
    receipt = builder.build(inputs)
    command = next(c for c in calls if c[1] == "create")
    tree = ast.parse(command[-1])
    assert any(
        isinstance(n, ast.Constant) and n.value == b"\x7fELF\x02\x01" for n in ast.walk(tree)
    )
    assert "timeout=30" in command[-1] and "resolve_rhwp()==n" in command[-1]
    assert "--read-only" in command and "--user=10001:10001" in command
    assert receipt["schemaVersion"] == "document-files.image-build.v2"
    assert receipt["inputs"]["coreArchiveSha256"] == builder.sha(inputs.core_archive)
    assert receipt["installedNativeEvidence"]["sha256"] == builder.sha(
        inputs.output / "installed-core.json"
    )


@pytest.mark.parametrize("interrupt", ["timeout", "cancel"])
def test_native_probe_timeout_or_cancel_preserves_failure_and_removes_exact_container(
    inputs, monkeypatch, interrupt
):
    original = fake_docker(inputs, [])

    def interrupted(command, log, **kwargs):
        if command[1] == "start":
            if interrupt == "timeout":
                raise builder.subprocess.TimeoutExpired(command, 1)
            raise KeyboardInterrupt()
        return original(command, log, **kwargs)

    monkeypatch.setattr(builder, "execute", interrupted)
    with pytest.raises(
        builder.subprocess.TimeoutExpired if interrupt == "timeout" else KeyboardInterrupt
    ):
        builder.build(inputs)
    receipt = json.loads((inputs.output / "image-build.json").read_bytes())
    assert receipt["probeCleanup"] == {"status": "removed", "containerId": "d" * 64}
    assert receipt["status"] == "failed" and receipt["imageId"] is None
    assert receipt["archiveSha256"] is None


def test_duplicate_portable_member_is_rejected_before_docker(inputs, monkeypatch):
    import copy

    with pytest.warns(UserWarning, match="Duplicate name"):
        rewrite_portable(
            inputs, lambda entries: entries + [(copy.copy(entries[0][0]), entries[0][1])]
        )
    monkeypatch.setattr(builder, "execute", lambda *a, **k: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="Unsafe ZIP"):
        builder.build(inputs)
