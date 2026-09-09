#!/usr/bin/env python3
"""Build/export a local Linux image from checked release inputs; never publish.

Requires a clean source checkout, stable core build receipt, a prepared exact
wheelhouse and an already local digest-pinned Python 3.12 base. Docker RUN steps
have no network; --pull=false is NOT a daemon-wide network-isolation claim.
The caller supplies a JSON object {wheelFilename: sha256} and its trusted SHA256.
The platform-resolved requirements lock accepts only exact pins and SHA256 hashes.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import tomllib
import uuid
import zipfile
from contextlib import suppress
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RHWP_PATH = "/opt/document-files-native/rhwp/rhwp"
RHWP_VERSION = "rhwp v0.8.6+pat.checkbox.1"
RHWP_UPSTREAM = "f1f9c6ae58344ee9368996d3543f76b9345cf227"
IMAGE_ID = r"sha256:[a-f0-9]{64}"
INSPECT = (
    '{"imageId":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},'
    '"repoDigests":{{json .RepoDigests}},"user":{{json .Config.User}},'
    '"entrypoint":{{json .Config.Entrypoint}}}'
)

PROBE_INSPECT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"imageId":{{json .Image}},'
    '"runId":{{json (index .Config.Labels "org.document-files.image-build")}}}'
)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked(path, expected=None):
    if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Input must be a regular non-symlink file")
    actual = sha(path)
    if expected is not None and (not re.fullmatch(r"[a-f0-9]{64}", expected) or actual != expected):
        raise ValueError("Input checksum mismatch")
    return actual


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON input key")
            result[key] = value
        return result

    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def source_identity():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if (
        not re.fullmatch(r"[a-f0-9]{40}", commit)
        or subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    ):
        raise ValueError("Clean committed release source required")
    return commit, tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def safe_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name):
        raise ValueError("Unsafe input filename")
    return name


def source_files(path):
    result = {}
    with tarfile.open(path, "r:gz") as archive:
        roots = set()
        for item in archive.getmembers():
            parts = Path(item.name).parts
            if not parts or Path(item.name).is_absolute() or ".." in parts or "\\" in item.name:
                raise ValueError("Unsafe source archive")
            roots.add(parts[0])
            if item.isdir():
                continue
            name = "/".join(parts[1:])
            if not item.isfile() or name in result:
                raise ValueError("Duplicate or nonregular source member")
            result[name] = hashlib.sha256(archive.extractfile(item).read()).hexdigest()
        if len(roots) != 1:
            raise ValueError("One source archive root required")
    return result


def wheel_tree(path):
    spec = importlib.util.spec_from_file_location(
        "image_execution_identity", ROOT / "evaluation/execution_identity.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.wheel_package(path)


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identity(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(names) != 1:
            raise ValueError("One wheel METADATA required")
        metadata = BytesParser().parsebytes(archive.read(names[0]))
    if not metadata.get("Name") or not metadata.get("Version"):
        raise ValueError("Wheel name/version missing")
    return canonical(metadata["Name"]), metadata["Version"]


def lock_requirements(path):
    # Explicitly platform-resolved: no indexes, URLs, includes, markers or editable inputs.
    requirements = {}
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = shlex.split(line)
        match = re.fullmatch(
            r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)", tokens[0]
        )
        if not match or len(tokens) < 2:
            raise ValueError("Platform-resolved exact hashed requirements required")
        hashes = set()
        for token in tokens[1:]:
            item = re.fullmatch(r"--hash=sha256:([a-f0-9]{64})", token)
            if not item:
                raise ValueError("Only explicit requirement SHA256 hashes allowed")
            hashes.add(item[1])
        name = canonical(match[1])
        if name in requirements:
            raise ValueError("Duplicate requirement")
        requirements[name] = (match[2], hashes)
    return requirements


def portable_rhwp(archive, commit, version, wheel_sha, patch_sha=None):
    """Reuse bounded ZIP inspection; return only the selected three native files."""
    spec = importlib.util.spec_from_file_location("image_linux_zip", ROOT / "scripts/linux_abi.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    before = checked(archive)
    with tempfile.TemporaryDirectory(prefix="document-files-image-core-") as folder:
        unpacked = Path(folder)
        helper.unpack(archive, unpacked)
        # The bounded unpacker has checked paths, duplicates and file types.
        # Windows extraction cannot preserve Unix execute bits; inspect the
        # Linux archive's declared mode, not this inspection host's stat mode.
        with zipfile.ZipFile(archive) as source:
            executable_mode = source.getinfo("document-files/rhwp/rhwp").external_attr >> 16
        base = unpacked / "document-files"
        build = read_json(base / "BUILD.json")
        native = read_json(base / "rhwp/build.json")
        if (
            build.get("sourceCommit") != commit
            or build.get("dirtySource") is not False
            or build.get("version") != version
            or build.get("target") != "linux-x86_64"
            or build.get("wheelSha256") != wheel_sha
            or build.get("rhwp") != native
            or native.get("version") != RHWP_VERSION.removeprefix("rhwp v")
            or native.get("baseCommit") != RHWP_UPSTREAM
            or not re.fullmatch(r"[a-f0-9]{64}", native.get("patchSha256", ""))
            or (patch_sha is not None and native["patchSha256"] != patch_sha)
        ):
            raise ValueError("Portable core/rhwp identity mismatch")
        files = {
            name: (base / "rhwp" / name).read_bytes() for name in ("rhwp", "LICENSE", "build.json")
        }
        raw = files["rhwp"]
        if (
            len(raw) < 20
            or raw[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(raw[18:20], "little") != 62
            or not executable_mode & 0o111
            or hashlib.sha256(raw).hexdigest() != native.get("binarySha256")
            or not files["LICENSE"].strip()
        ):
            raise ValueError("Portable rhwp bytes, executable target or notice invalid")
    if checked(archive) != before:
        raise ValueError("Portable core archive changed during inspection")
    return files, {
        "path": RHWP_PATH,
        "version": RHWP_VERSION,
        "upstreamCommit": RHWP_UPSTREAM,
        "patchSha256": native["patchSha256"],
        "files": {n: hashlib.sha256(b).hexdigest() for n, b in files.items()},
    }


def prepare_inputs(args, commit, version, context):
    originals = {}

    def accept(path, expected=None):
        value = checked(path, expected)
        originals[path] = value
        return value

    accept(args.core_receipt, args.core_receipt_sha256)
    core = read_json(args.core_receipt)
    if (
        core.get("schemaVersion") != "document-files.build-inventory.v2"
        or core.get("sourceCommit") != commit
        or core.get("version") != version
        or core.get("dirtySource") is not False
        or core.get("candidateMode") != "stable"
        or core.get("target") != "linux-x86_64"
    ):
        raise ValueError("Matching stable Linux core receipt required")
    for path, suffix in (
        (args.wheel, ".whl"),
        (args.source, ".tar.gz"),
        (args.core_archive, ".zip"),
    ):
        if not path.name.endswith(suffix) or path.name not in core.get("artifacts", {}):
            raise ValueError("Selected wheel/source/core archive missing from build receipt")
        accept(path, core["artifacts"][path.name])
    if wheel_identity(args.wheel) != ("document-files", version):
        raise ValueError("Wrong core wheel identity")
    source = source_files(args.source)
    for name, expected in source.items():
        if name.startswith("src/document_files/") or name == "pyproject.toml":
            accept(ROOT / name, expected)
    for name in (
        "scripts/build_release_image.py",
        "evaluation/execution_identity.py",
        "scripts/linux_abi.py",
    ):
        if name not in source:
            raise ValueError("Image builder/identity helper missing from source artifact")
        accept(ROOT / name, source[name])
    tree = wheel_tree(args.wheel)
    if tree != {
        k.removeprefix("src/document_files/"): v
        for k, v in source.items()
        if k.startswith("src/document_files/")
    }:
        raise ValueError("Core wheel and source package bytes differ")
    patch_sha = source.get("patches/rhwp/checkbox-preservation.patch")
    if patch_sha is None:
        raise ValueError("Selected source lacks rhwp patch")
    accept(ROOT / "patches/rhwp/checkbox-preservation.patch", patch_sha)
    native_files, native_identity = portable_rhwp(
        args.core_archive, commit, version, originals[args.wheel], patch_sha
    )
    context.mkdir()
    (context / "rhwp").mkdir()
    for name, raw in native_files.items():
        (context / "rhwp" / name).write_bytes(raw)
        (context / "rhwp" / name).chmod(0o755 if name == "rhwp" else 0o644)
    for name in ("Dockerfile", "entrypoint.py"):
        path = ROOT / "deployment" / name
        value = accept(path)
        if source.get("deployment/" + name) != value:
            raise ValueError("Image recipe differs from selected source artifact")
        shutil.copyfile(path, context / name)
    recipe = (context / "Dockerfile").read_text()
    if re.search(r"^\s*#\s*syntax\s*=", recipe, re.M | re.I):
        raise ValueError("External Dockerfile frontend forbidden; use builtin frontend")
    accept(args.requirements, args.requirements_sha256)
    accept(args.wheelhouse_inventory, args.wheelhouse_inventory_sha256)
    inventory = read_json(args.wheelhouse_inventory)
    if not isinstance(inventory, dict) or not inventory:
        raise ValueError("Exact wheelhouse inventory required")
    actual = {p.name for p in args.wheelhouse.iterdir()}
    if actual != set(inventory) or len({name.casefold() for name in inventory}) != len(inventory):
        raise ValueError("Mixed or incomplete wheelhouse")
    (context / "wheelhouse").mkdir()
    packages = {}
    for name, expected in inventory.items():
        safe_name(name)
        if not name.endswith(".whl"):
            raise ValueError("Wheelhouse may contain only wheels")
        path = args.wheelhouse / name
        accept(path, expected)
        package, package_version = wheel_identity(path)
        if package in packages:
            raise ValueError("Multiple wheels for one package")
        packages[package] = (package_version, expected)
        shutil.copyfile(path, context / "wheelhouse" / name)
        if sha(context / "wheelhouse" / name) != expected:
            raise ValueError("Wheel changed during context copy")
    lock = lock_requirements(args.requirements)
    if set(packages) != set(lock) or any(
        v != lock[n][0] or h not in lock[n][1] for n, (v, h) in packages.items()
    ):
        raise ValueError("Wheelhouse and resolved requirements differ")
    if inventory.get(args.wheel.name) != originals[args.wheel]:
        raise ValueError("Wheelhouse does not contain the selected exact core wheel")
    shutil.copyfile(args.requirements, context / "requirements.lock")
    copied = {p.relative_to(context).as_posix(): sha(p) for p in context.rglob("*") if p.is_file()}
    expected_context = {
        **{"rhwp/" + n: h for n, h in native_identity["files"].items()},
        "Dockerfile": originals[ROOT / "deployment/Dockerfile"],
        "entrypoint.py": originals[ROOT / "deployment/entrypoint.py"],
        "requirements.lock": originals[args.requirements],
        **{"wheelhouse/" + n: h for n, h in inventory.items()},
    }
    if copied != expected_context:
        raise ValueError("Build context changed during preparation")
    return originals, copied, tree, native_identity


def terminate_group(process, event):
    """Only the process group created for this command; never kill by process name."""
    event["terminationRequested"] = True
    try:
        if os.name == "posix":
            event["cleanupOperation"] = f"killpg({process.pid},SIGTERM)"
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            # Reap an exited leader before signaling again: Darwin can return EPERM
            # for SIGKILL aimed at an already-dead, unreaped process group.
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=0.1)
            event["cleanupOperation"] = f"killpg({process.pid},0)"
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                event["cleanupOperation"] = f"killpg({process.pid},SIGKILL)"
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
        else:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=True,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        event["cleanupError"] = type(exc).__name__
        event["cleanupErrno"] = getattr(exc, "errno", None)
        # A group-control failure is never a tree-cleanup success. Attempt only the
        # still-owned unreaped child as a last resort, and retain the error.
        try:
            process.kill()
            event["leaderOnlyTermination"] = True
        except OSError:
            pass
    try:
        process.wait(timeout=5)
        event["leaderReaped"] = True
    except (OSError, subprocess.SubprocessError) as exc:
        event["cleanupError"] = type(exc).__name__
    # Docker's daemon is not in this CLI process group.
    event["daemonWorkTermination"] = "not-verified"


def execute(command, log, *, timeout=30, events=None):
    if not 0 < timeout <= 7200:
        raise ValueError("Finite Docker command timeout required")
    event = {"log": log.name, "timeoutSeconds": timeout, "status": "launching"}
    if events is not None:
        events.append(event)
    try:
        with log.open("wb") as stream:
            options = (
                {"start_new_session": True}
                if os.name == "posix"
                else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            )
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, **options)
            event.update(pid=process.pid, processGroupId=process.pid, status="running")
            try:
                code = process.wait(timeout=timeout)
            except BaseException as exc:
                event["status"] = (
                    "timed-out" if isinstance(exc, subprocess.TimeoutExpired) else "interrupted"
                )
                terminate_group(process, event)
                raise
        event.update(returnCode=code, status="exited")
        if code:
            terminate_group(process, event)
            raise ValueError(f"Docker command failed; see {log.name}")
    except BaseException:
        if event["status"] == "launching":
            event["status"] = "launch-failed"
        raise
    return log.read_text(encoding="utf-8").strip() if log.stat().st_size < 1024 * 1024 else ""


def cleanup_probe(probe, output, events):
    if not probe.get("attempted"):
        return {"status": "not-created"}
    result = {"status": "failed", "containerId": probe.get("containerId")}
    try:
        ids = execute(
            [
                "docker",
                "container",
                "ls",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=org.document-files.image-build=" + probe["runId"],
            ],
            output / "probe-cleanup-list.log",
            timeout=15,
            events=events,
        ).splitlines()
        if not ids:
            return {"status": "absent", "containerId": probe.get("containerId")}
        if len(ids) != 1 or not re.fullmatch(r"[a-f0-9]{64}", ids[0]):
            raise ValueError("Ambiguous probe containers; none removed")
        cid = ids[0]
        found = json.loads(
            execute(
                ["docker", "container", "inspect", cid, "--format", PROBE_INSPECT],
                output / "probe-cleanup-inspect.json",
                timeout=15,
                events=events,
            )
        )
        if found != {
            "id": cid,
            "name": "/" + probe["name"],
            "imageId": probe["imageId"],
            "runId": probe["runId"],
        } or (probe.get("containerId") is not None and probe["containerId"] != cid):
            raise ValueError("Probe identity differs; container not removed")
        result["containerId"] = cid
        execute(
            ["docker", "container", "rm", "--force", cid],
            output / "probe-cleanup-remove.log",
            timeout=15,
            events=events,
        )
        remaining = execute(
            [
                "docker",
                "container",
                "ls",
                "-aq",
                "--no-trunc",
                "--filter",
                "label=org.document-files.image-build=" + probe["runId"],
            ],
            output / "probe-cleanup-after.log",
            timeout=15,
            events=events,
        )
        if remaining:
            raise ValueError("Probe container remains after cleanup")
        result["status"] = "removed"
    except (Exception, KeyboardInterrupt) as exc:
        result["error"] = type(exc).__name__
    return result


def inspect_image(reference, output, run=execute):
    data = json.loads(run(["docker", "image", "inspect", reference, "--format", INSPECT], output))
    if (
        not re.fullmatch(IMAGE_ID, data.get("imageId", ""))
        or data.get("os") != "linux"
        or data.get("architecture") != "amd64"
    ):
        raise ValueError("Actual Linux amd64 image identity required")
    return data


MAX_EXPORT_BYTES = 32 * 1024**3
MAX_EXPORT_MEMBERS = 10000
MAX_METADATA_BYTES = 4 * 1024**2
MAX_LAYER_BYTES = 8 * 1024**3
MAX_LAYER_TOTAL_BYTES = 32 * 1024**3


class BoundedTarReads:
    def __init__(self, stream):
        self.stream = stream

    def read(self, size=-1):
        if size < 0 or size > MAX_METADATA_BYTES:
            raise ValueError("Image tar read exceeds metadata/chunk bound")
        return self.stream.read(size)

    def __getattr__(self, name):
        return getattr(self.stream, name)


def export_identity(path, image_id):
    """Bind ordered uncompressed layer tar bytes to rootfs.diff_ids, without extraction."""
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError("Image export exceeds size bound")
    with (
        path.open("rb") as source,
        tarfile.open(fileobj=BoundedTarReads(source), mode="r:") as archive,
    ):
        by_name = {}
        for member in archive:
            if len(by_name) >= MAX_EXPORT_MEMBERS:
                raise ValueError("Too many image export members")
            if (
                member.name in by_name
                or Path(member.name).is_absolute()
                or ".." in Path(member.name).parts
                or "\\" in member.name
                or not (member.isfile() or member.isdir())
                or member.issparse()
                or member.size < 0
                or member.size > MAX_EXPORT_BYTES
            ):
                raise ValueError("Unsafe or duplicate image export member")
            by_name[member.name] = member

        def member_stream(name, limit):
            member = by_name.get(name)
            if member is None or not member.isfile() or member.size > limit:
                raise ValueError("Missing or oversized image export member")
            return archive.extractfile(member)

        with member_stream("manifest.json", MAX_METADATA_BYTES) as stream:
            manifest = json.load(stream)
        if not isinstance(manifest, list) or len(manifest) != 1:
            raise ValueError("One exported image required")
        with member_stream(manifest[0]["Config"], MAX_METADATA_BYTES) as stream:
            raw = stream.read()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != image_id:
            raise ValueError("Export differs from actual built image ID")
        rootfs = json.loads(raw).get("rootfs", {})
        diff_ids = rootfs.get("diff_ids")
        layers = manifest[0].get("Layers")
        if (
            rootfs.get("type") != "layers"
            or not isinstance(diff_ids, list)
            or not isinstance(layers, list)
            or len(layers) != len(diff_ids)
            or len(layers) > 1024
            or any(not isinstance(v, str) or not re.fullmatch(IMAGE_ID, v) for v in diff_ids)
        ):
            raise ValueError("Image layer count or DiffID declaration mismatch")
        total = 0
        for name, expected in zip(layers, diff_ids, strict=True):
            with member_stream(name, MAX_LAYER_BYTES) as source:
                magic = source.read(4)
                source.seek(0)
                # Docker save legacy tar layers and gzip OCI blobs are supported.
                # Unknown compressed codecs fail closed; never hash compressed bytes as DiffIDs.
                if magic == b"\x28\xb5\x2f\xfd":
                    raise ValueError("Zstd layer export is not supported")
                stream = gzip.GzipFile(fileobj=source) if magic[:2] == b"\x1f\x8b" else source
                value, size = hashlib.sha256(), 0
                try:
                    while chunk := stream.read(1024 * 1024):
                        size += len(chunk)
                        total += len(chunk)
                        if size > MAX_LAYER_BYTES or total > MAX_LAYER_TOTAL_BYTES:
                            raise ValueError("Uncompressed layer size bound exceeded")
                        value.update(chunk)
                finally:
                    if stream is not source:
                        stream.close()
                if "sha256:" + value.hexdigest() != expected:
                    raise ValueError("Exported layer bytes or order differ from config DiffIDs")


def build(args):
    output = args.output.absolute()
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Output symlink forbidden")
    output.mkdir(parents=False, exist_ok=False)
    receipt = {
        "schemaVersion": "document-files.image-build.v2",
        "status": "started",
        "releaseQualification": False,
        "securityAndLicenseReview": "not-assessed",
        "startedAt": datetime.now(UTC).isoformat(),
        "archiveSha256": None,
        "imageId": None,
        "stage": "inputs",
    }
    path = output / "image-build.json"

    def save():
        path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    timeout = getattr(args, "timeout_seconds", 1800)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 7200:
        raise ValueError("Build timeout must be finite and at most 7200 seconds")
    deadline = time.monotonic() + timeout
    receipt["timeoutSeconds"] = timeout
    receipt["probeCleanupCommandBudgetSeconds"] = 60
    receipt["processTerminationGraceSeconds"] = 15
    receipt["commands"] = []
    probe_identity = {"runId": uuid.uuid4().hex, "attempted": False}
    receipt["probe"] = probe_identity

    def run(command, log):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Image build command budget exhausted")
        return execute(command, log, timeout=remaining, events=receipt["commands"])

    save()
    try:
        commit, version = source_identity()
        receipt.update(
            sourceCommit=commit, version=version, dirtySource=False, target="linux-x86_64"
        )
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./:_-]*@sha256:[a-f0-9]{64}", args.base_image
        ) or not re.fullmatch(IMAGE_ID, args.base_image_id):
            raise ValueError("Explicit pinned base reference and actual base ID required")
        context = output / "context"
        originals, copied, tree, native_identity = prepare_inputs(args, commit, version, context)
        receipt["inputs"] = {
            "coreReceiptSha256": args.core_receipt_sha256,
            "coreArchiveSha256": originals[args.core_archive],
            "wheelSha256": originals[args.wheel],
            "sourceArchiveSha256": originals[args.source],
            "wheelhouseInventorySha256": args.wheelhouse_inventory_sha256,
            "context": copied,
        }
        receipt["rhwp"] = native_identity
        receipt["stage"] = "base-inspection"
        save()
        base = inspect_image(args.base_image, output / "base-inspect.json", run)
        if base["imageId"] != args.base_image_id or args.base_image not in (
            base.get("repoDigests") or []
        ):
            raise ValueError("Local base image differs from pinned identity")
        receipt["baseImage"] = {"reference": args.base_image, **base}
        receipt["builder"] = {
            "frontend": "builtin",
            "runNetwork": "none",
            "pull": False,
            "daemonNetworkIsolation": "not-assessed",
            "version": run(
                ["docker", "version", "--format", "{{json .Server.Version}}"],
                output / "docker-version.json",
            ),
        }
        receipt["stage"] = "build"
        save()
        iid = output / "image-id.txt"
        command = [
            "docker",
            "build",
            "--pull=false",
            "--network=none",
            "--no-cache",
            "--platform=linux/amd64",
            "--iidfile",
            str(iid),
            "--build-arg",
            "BASE_IMAGE=" + args.base_image,
            str(context),
        ]
        run(command, output / "build.log")
        image_id = iid.read_text().strip()
        if not re.fullmatch(IMAGE_ID, image_id):
            raise ValueError("Build did not produce an actual image ID")
        actual = inspect_image(image_id, output / "image-inspect.json", run)
        if (
            actual["imageId"] != image_id
            or actual.get("user") != "10001:10001"
            or actual.get("entrypoint") != ["python", "/opt/document-files-entrypoint.py"]
        ):
            raise ValueError("Final image configuration mismatch")
        receipt["stage"] = "installed-bytes"
        save()
        # Fixed read-only check, no document, model, token, caller code or processing endpoint.
        probe = (
            "import document_files,pathlib,hashlib,json,sys,os,subprocess; "
            "from document_files.rhwp_backend import resolve_rhwp; "
            "p=pathlib.Path(document_files.__file__).parent; "
            "assert sys.version_info[:2]==(3,12); "
            "assert not any(f.is_symlink() for f in p.rglob('*')); "
            "core={str(f.relative_to(p)):hashlib.sha256(f.read_bytes()).hexdigest() "
            "for f in p.rglob('*') if f.is_file() and '__pycache__' not in f.parts "
            "and f.suffix not in ('.pyc','.pyo')}; "
            f"n=pathlib.Path({RHWP_PATH!r}); "
            "assert os.environ.get('DOCUMENT_FILES_RHWP')==str(n); "
            "assert resolve_rhwp()==n and not any(f.is_symlink() for f in (n,*n.parents)); "
            "raw=n.read_bytes(); assert raw[:6]==b'\\x7fELF\\x02\\x01'; "
            "assert int.from_bytes(raw[18:20],'little')==62; "
            "assert not os.access(n,os.W_OK); "
            "assert not any((n.parent/name).is_symlink() "
            "for name in ('rhwp','LICENSE','build.json')); "
            "files={name:hashlib.sha256((n.parent/name).read_bytes()).hexdigest() "
            "for name in ('rhwp','LICENSE','build.json')}; "
            "v=subprocess.run([str(n),'--version'],stdin=subprocess.DEVNULL, "
            "capture_output=True,text=True,check=True,timeout=30).stdout.strip(); "
            "print(json.dumps({'core':core,'rhwp':{'path':str(n),'version':v,'files':files}},sort_keys=True))"
        )
        cidfile = output / "probe-container-id.txt"
        probe_identity.update(
            attempted=True, name="df-image-probe-" + probe_identity["runId"], imageId=image_id
        )
        save()
        created_id = run(
            [
                "docker",
                "create",
                "--cidfile",
                str(cidfile),
                "--name",
                probe_identity["name"],
                "--label",
                "org.document-files.image-build=" + probe_identity["runId"],
                "--pull=never",
                "--network=none",
                "--read-only",
                "--user=10001:10001",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--entrypoint=python",
                image_id,
                "-I",
                "-B",
                "-c",
                probe,
            ],
            output / "probe-create.log",
        )
        cid = cidfile.read_text().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", cid) or created_id.strip() != cid:
            raise ValueError("Probe did not return one matching full container ID")
        probe_identity["containerId"] = cid
        save()
        found = json.loads(
            run(
                ["docker", "container", "inspect", cid, "--format", PROBE_INSPECT],
                output / "probe-created-identity.json",
            )
        )
        if found != {
            "id": cid,
            "name": "/" + probe_identity["name"],
            "imageId": image_id,
            "runId": probe_identity["runId"],
        }:
            raise ValueError("Created probe identity mismatch; not started")
        observed = json.loads(
            run(["docker", "start", "--attach", cid], output / "installed-core.json")
        )
        state = json.loads(
            run(
                [
                    "docker",
                    "container",
                    "inspect",
                    cid,
                    "--format",
                    '{"running":{{json .State.Running}},"exitCode":{{json .State.ExitCode}}}',
                ],
                output / "probe-exit.json",
            )
        )
        if state != {"running": False, "exitCode": 0}:
            raise ValueError("Probe container did not exit successfully")
        if observed.get("core") != tree:
            raise ValueError("Installed core differs from selected wheel")
        expected_native = {k: native_identity[k] for k in ("path", "version", "files")}
        if observed.get("rhwp") != expected_native:
            raise ValueError("Installed rhwp differs from selected core archive")
        receipt["installedNativeVerification"] = "selected-core-rhwp-exact"
        receipt["installedNativeEvidence"] = {
            "path": "installed-core.json",
            "sha256": sha(output / "installed-core.json"),
        }
        receipt["stage"] = "export"
        save()
        archive = output / f"document-files-{version}-linux-x86_64-image.tar"
        run(["docker", "image", "save", "--output", str(archive), image_id], output / "export.log")
        export_identity(archive, image_id)
        if source_identity() != (commit, version) or any(
            checked(p) != h for p, h in originals.items()
        ):
            raise ValueError("Release source or inputs changed while building")
        if {
            p.relative_to(context).as_posix(): checked(p) for p in context.rglob("*") if p.is_file()
        } != copied:
            raise ValueError("Build context changed while building")
        receipt.update(
            status="built-unqualified",
            imageId=image_id,
            archiveSha256=sha(archive),
            archive=archive.name,
            installedCoreVerification="selected-wheel-exact",
            stage="complete",
        )
        return receipt
    except BaseException:
        receipt["status"] = "failed"
        receipt["archiveSha256"] = None
        receipt["imageId"] = None
        raise
    finally:
        receipt["probeCleanup"] = cleanup_probe(probe_identity, output, receipt["commands"])
        cleanup_failed = receipt["probeCleanup"]["status"] == "failed" or any(
            event.get("cleanupError") for event in receipt["commands"]
        )
        was_complete = receipt["status"] == "built-unqualified"
        if cleanup_failed:
            receipt.update(status="failed", imageId=None, archiveSha256=None)
        receipt["endedAt"] = datetime.now(UTC).isoformat()
        save()
        if was_complete and cleanup_failed:
            raise ValueError("Image probe cleanup incomplete; see failure receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "output",
        "core-receipt",
        "core-archive",
        "wheel",
        "source",
        "wheelhouse",
        "wheelhouse-inventory",
        "requirements",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in (
        "core-receipt-sha256",
        "wheelhouse-inventory-sha256",
        "requirements-sha256",
        "base-image",
        "base-image-id",
    ):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800,
        help="Total Docker command budget, at most 7200s; no automatic retries",
    )

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Image build interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
