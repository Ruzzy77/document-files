"""Verified offline packs and a private CPU-only llama.cpp process.

Installing a pack grants its publisher permission to execute native code. A hash
proves identity, not publisher trust: acquire the expected hash independently.
This module never downloads dependencies, evaluates model Python, or mutates jobs.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import secrets
import socket
import stat
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .portability import WindowsJob, kill_process_tree, process_options, subprocess_environment
from .private_fs import private_path

PACK_SCHEMA = "document-files.pack.v1"
ACTIVE_SCHEMA = "document-files.active-packs.v1"
# A CPU recognition pack contains tens of thousands of inventoried Python files.
# Bound its metadata separately from the multi-GB weights and archive file count.
MAX_MANIFEST_BYTES = 16 * 1024**2
KINDS = {"core", "recognition", "llama-cpp-runtime", "model"}
TARGETS = {
    "macos-aarch64",
    "macos-x86_64",
    "windows-x86_64",
    "linux-x86_64",
    "linux-aarch64",
    "any",
}
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._+-]{0,127}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}


class PackError(ValueError):
    """A safe error code, never a document, token or upstream stderr dump."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value in {".", ".."}:
        raise PackError("pack_invalid_identifier")
    return value


def safe_relative(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(c in value for c in ':<>"|?*')
    ):
        raise PackError("pack_unsafe_path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise PackError("pack_unsafe_path")
    for part in path.parts:
        if part in {".", ".."} or part.rstrip(". ") != part or any(ord(c) < 32 for c in part):
            raise PackError("pack_unsafe_path")
        if part.split(".")[0].upper() in _RESERVED:
            raise PackError("pack_unsafe_path")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise PackError("pack_invalid_sha256")
    return value


def pack_artifact_digests(provenance: dict) -> list[str]:
    """Original downloads and explicitly derived local inputs, never invented URLs.

    Derived entries are topologically ordered and carry shipped recipe/evidence
    references. Hashes bind those records; they do not prove a build was executed
    or that its upstream inputs/license decisions have been independently reviewed.
    """
    try:
        version = provenance.get("schemaVersion", "document-files.pack-provenance.v1")
        if version not in {
            "document-files.pack-provenance.v1",
            "document-files.pack-provenance.v2",
        } or ("derivedArtifacts" in provenance and version != "document-files.pack-provenance.v2"):
            raise PackError("pack_invalid_provenance_version")
        original = provenance["sources"]
        if not isinstance(original, list) or not original:
            raise PackError("pack_missing_provenance")
        digests = list(dict.fromkeys(_digest(item["sha256"]) for item in original))
        known = set(digests)
        derived = provenance.get("derivedArtifacts", [])
        if not isinstance(derived, list):
            raise PackError("pack_invalid_derived_artifact")
        for item in derived:
            if not isinstance(item, dict) or set(item) != {
                "sha256",
                "inputs",
                "recipe",
                "buildEvidence",
            }:
                raise PackError("pack_invalid_derived_artifact")
            digest = _digest(item["sha256"])
            inputs = item["inputs"]
            if not isinstance(inputs, list) or not inputs:
                raise PackError("pack_invalid_derived_inputs")
            parents = [_digest(value) for value in inputs]
            if digest in known or len(set(parents)) != len(parents) or not set(parents) <= known:
                raise PackError("pack_invalid_derived_inputs")
            for key in ("recipe", "buildEvidence"):
                ref = item[key]
                if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
                    raise PackError("pack_invalid_derivation_reference")
                safe_relative(ref["path"])
                _digest(ref["sha256"])
            digests.append(digest)
            known.add(digest)
        return digests
    except (KeyError, TypeError, AttributeError) as exc:
        raise PackError("pack_invalid_provenance") from exc


def _json(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackError("pack_duplicate_json_key")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackError("pack_invalid_json") from exc
    if not isinstance(value, dict):
        raise PackError("pack_invalid_json")
    return value


def model_vision_config(manifest: dict) -> dict | None:
    """An optional, bounded projector declaration; never discover nearby files."""
    model = manifest.get("model", {})
    if "vision" not in model:
        return None
    vision = model["vision"]
    if not isinstance(vision, dict) or set(vision) != {"file", "minImageTokens", "maxImageTokens"}:
        raise PackError("pack_invalid_vision_configuration")
    name = safe_relative(vision["file"])
    if (
        not name.endswith(".gguf")
        or name == model.get("file")
        or type(vision["minImageTokens"]) is not int
        or type(vision["maxImageTokens"]) is not int
        or not 1024 <= vision["minImageTokens"] <= vision["maxImageTokens"] <= 1536
    ):
        raise PackError("pack_invalid_vision_configuration")
    rows = [item for item in manifest.get("files", []) if item.get("path") == name]
    if len(rows) != 1 or rows[0].get("executable") or rows[0].get("size", 0) < 4:
        raise PackError("pack_missing_vision_projector")
    _digest(rows[0].get("sha256"))
    return dict(vision)


def validate_manifest(manifest: dict) -> dict:
    """Validate a manifest independently of the archive it describes."""
    try:
        if manifest["schemaVersion"] != PACK_SCHEMA or manifest["kind"] not in KINDS:
            raise PackError("pack_invalid_schema")
        _identifier(manifest["id"])
        _identifier(manifest["version"])
        if manifest["platform"] not in TARGETS:
            raise PackError("pack_invalid_platform")
        if manifest["kind"] != "model" and manifest["platform"] == "any":
            raise PackError("pack_runtime_requires_platform")
        minimum = manifest["minimumOS"]
        if not isinstance(minimum, dict) or not all(
            isinstance(minimum.get(k), str) and minimum[k] for k in ("name", "version")
        ):
            raise PackError("pack_missing_os_baseline")
        files = manifest["files"]
        licenses = manifest["licenses"]
        if (
            not isinstance(files, list)
            or not files
            or not isinstance(licenses, list)
            or not licenses
        ):
            raise PackError("pack_missing_inventory")
        names, license_ids = set(), set()
        for license_entry in licenses:
            lid = _identifier(license_entry["id"])
            if lid in license_ids or not license_entry.get("spdx"):
                raise PackError("pack_invalid_license")
            license_ids.add(lid)
            safe_relative(license_entry["path"])
        for item in files:
            name = safe_relative(item["path"])
            if name == "manifest.json" or unicodedata.normalize("NFC", name).casefold() in names:
                raise PackError("pack_duplicate_path")
            names.add(unicodedata.normalize("NFC", name).casefold())
            _digest(item["sha256"])
            if type(item["size"]) is not int or item["size"] < 0:
                raise PackError("pack_invalid_size")
            if (
                item.get("license") not in license_ids
                or type(item.get("executable", False)) is not bool
            ):
                raise PackError("pack_invalid_file_metadata")
        listed = {item["path"] for item in files}
        if any(entry["path"] not in listed for entry in licenses):
            raise PackError("pack_missing_license_file")
        sources = manifest["provenance"]["sources"]
        if not isinstance(sources, list) or not sources:
            raise PackError("pack_missing_provenance")
        for source in sources:
            if not isinstance(source["uri"], str) or not source["uri"].startswith("https://"):
                raise PackError("pack_invalid_source")
            # Archive SHA pins non-git upstreams; git/HF sources additionally record revision.
            _digest(source["sha256"])
            if "revision" in source and not re.fullmatch(r"[a-f0-9]{40,64}", source["revision"]):
                raise PackError("pack_unpinned_revision")
        pack_artifact_digests(manifest["provenance"])
        inventory = {item["path"]: item for item in files}
        for artifact in manifest["provenance"].get("derivedArtifacts", []):
            for key in ("recipe", "buildEvidence"):
                ref = artifact[key]
                if inventory.get(ref["path"], {}).get("sha256") != ref["sha256"]:
                    raise PackError("pack_unbound_derivation_reference")
        if not isinstance(manifest["compatibleRuntimes"], list):
            raise PackError("pack_invalid_compatibility")
        for runtime in manifest["compatibleRuntimes"]:
            _identifier(runtime["id"])
            _identifier(runtime["version"])
            _digest(runtime["manifestSha256"])
        for path in manifest.get("entrypoints", {}).values():
            if safe_relative(path) not in listed:
                raise PackError("pack_missing_entrypoint")
        executable_files = {item["path"] for item in files if item.get("executable")}
        if manifest["kind"] == "llama-cpp-runtime" and any(
            manifest.get("entrypoints", {}).get(key) not in executable_files
            for key in ("server", "quantize")
        ):
            raise PackError("pack_missing_llama_executable")
        if manifest["kind"] == "recognition":
            recognition = manifest["recognition"]
            required_settings = {
                "backend": "docling",
                "offline": True,
                "device": "cpu",
                "layout": "heron",
                "tableMode": "accurate",
            }
            if any(recognition.get(k) != v for k, v in required_settings.items()):
                raise PackError("pack_unapproved_recognition_configuration")
            if recognition.get("languages") != ["kor", "eng"]:
                raise PackError("pack_unapproved_ocr_languages")
            if recognition.get("tableOcrRepair", "off") not in {
                "off",
                "ruled_tables_v1",
                "ruled_cells_v2",
            }:
                raise PackError("pack_unapproved_recognition_configuration")
            repair_budget = recognition.get("repairBudget", {})
            limits = {
                "maxTables": 32,
                "maxCalls": 32,
                "maxPixels": 64000000,
                "maxSeconds": 300,
                "batchSize": 2,
                "maxImages": 64,
                "maxInputPixels": 64000000,
            }
            if (
                not isinstance(repair_budget, dict)
                or set(repair_budget) - set(limits)
                or any(
                    type(value) is not int or not 1 <= value <= limits[key]
                    for key, value in repair_budget.items()
                )
            ):
                raise PackError("pack_unapproved_recognition_configuration")
            if (
                repair_budget.get("batchSize", 1) == 2
                and recognition.get("tableOcrRepair", "off") != "ruled_cells_v2"
            ):
                raise PackError("pack_unapproved_recognition_configuration")
            if manifest["platform"] == "macos-x86_64":
                raise PackError("pack_intel_recognition_requires_linux_container")
            directories = recognition.get("nativeLibraryDirectories", [])
            if (
                not isinstance(directories, list)
                or len(directories) > 8
                or any(not isinstance(value, str) for value in directories)
                or len(set(directories)) != len(directories)
                or (directories and manifest["platform"] not in {"linux-aarch64", "linux-x86_64"})
            ):
                raise PackError("pack_invalid_native_library_directories")
            for value in directories:
                prefix = safe_relative(value) + "/"
                if any(c in value for c in ";$") or not any(
                    name.startswith(prefix) for name in listed
                ):
                    raise PackError("pack_invalid_native_library_directories")
            for key in ("python", "tesseract"):
                if safe_relative(recognition[key]) not in executable_files:
                    raise PackError("pack_missing_recognition_entrypoint")
            for key in ("artifacts", "tessdata"):
                prefix = safe_relative(recognition[key]) + "/"
                if not any(name.startswith(prefix) for name in listed):
                    raise PackError("pack_missing_recognition_models")
        if manifest["kind"] == "model":
            model = manifest["model"]
            if model["file"] not in listed or not manifest["compatibleRuntimes"]:
                raise PackError("pack_missing_model_runtime")
            if (
                model.get("tokenizer") != "embedded-gguf"
                or model.get("chatTemplate") != "embedded-gguf"
            ):
                raise PackError("pack_missing_tokenizer_template")
            model_vision_config(manifest)
        return manifest
    except (KeyError, TypeError, AttributeError) as exc:
        raise PackError("pack_invalid_manifest") from exc


def current_target() -> str:
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "AMD64": "x86_64", "x86_64": "x86_64"}.get(
        platform.machine()
    )
    target = f"{system}-{machine}"
    if target not in TARGETS:
        raise PackError("pack_unsupported_host")
    return target


def _version(value: str) -> tuple[int, ...]:
    match = re.match(r"\d+(?:\.\d+)*", value)
    if not match:
        raise PackError("pack_invalid_os_version")
    return tuple(int(part) for part in match[0].split("."))


def check_host(manifest: dict) -> None:
    target = current_target()
    if manifest["platform"] not in {target, "any"}:
        raise PackError("pack_platform_mismatch")
    if manifest["platform"] == "any":
        return
    name = target.split("-")[0]
    minimum = manifest["minimumOS"]
    if minimum["name"] != name:
        raise PackError("pack_os_mismatch")
    actual = platform.mac_ver()[0] if name == "macos" else platform.version()
    if name == "linux":
        actual = platform.release()
    if _version(actual) < _version(minimum["version"]):
        raise PackError("pack_os_too_old")
    if "minimumGlibc" in manifest:
        libc, version = platform.libc_ver()
        if libc != "glibc" or _version(version) < _version(manifest["minimumGlibc"]):
            raise PackError("pack_libc_incompatible")


@contextlib.contextmanager
def _archive(source):
    try:
        with zipfile.ZipFile(source) as bundle:
            yield bundle
    except (zipfile.BadZipFile, NotImplementedError, EOFError) as exc:
        raise PackError("pack_invalid_archive") from exc


@dataclass(frozen=True)
class InstalledPack:
    root: Path
    manifest: dict
    manifest_sha256: str

    def file(self, name: str) -> Path:
        safe_relative(name)
        if name not in {item["path"] for item in self.manifest["files"]}:
            raise PackError("pack_unlisted_file")
        return self.root / name


class PackStore:
    """Private, immutable-by-convention packs; explicit activation and rollback.

    Foreign-platform packs may be imported, but cannot be resolved for execution.
    Re-verification on resolve prevents a changed installation being used silently.
    A crashed writer leaves a lock: an operator must inspect/remove it, never TTL-steal.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        self._safe_ancestors(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        private_path(self.root, directory=True)

    @staticmethod
    def _safe_ancestors(path: Path) -> None:
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise PackError("pack_symlink_rejected")

    @contextlib.contextmanager
    def _lock(self):
        path = self.root / ".write.lock"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise PackError("pack_store_busy") from exc
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            yield
        finally:
            os.close(fd)
            path.unlink()

    def install(
        self,
        archive: str | Path,
        expected_sha256: str,
        *,
        max_bytes: int = 64 * 1024**3,
        max_files: int = 100000,
    ) -> InstalledPack:
        _digest(expected_sha256)
        archive = Path(archive)
        self._safe_ancestors(archive.absolute())
        # Keep a single opened archive handle: a renamed caller path cannot replace it.
        with self._lock(), archive.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected_sha256:
                raise PackError("pack_archive_hash_mismatch")
            source.seek(0)
            with _archive(source) as bundle:
                infos = bundle.infolist()
                if len(infos) > max_files or sum(item.file_size for item in infos) > max_bytes:
                    raise PackError("pack_archive_limit")
                names = set()
                for info in infos:
                    # ZipInfo may normalize Windows separators or truncate a NUL.
                    # Validate the original archive name before accepting any rewritten name.
                    name = safe_relative(info.orig_filename)
                    if name != info.filename:
                        raise PackError("pack_unsafe_path")
                    mode = info.external_attr >> 16
                    if (
                        info.is_dir()
                        or stat.S_ISLNK(mode)
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                        or info.flag_bits & 1
                    ):
                        raise PackError("pack_nonregular_archive_member")
                    if unicodedata.normalize("NFC", name).casefold() in names:
                        raise PackError("pack_duplicate_path")
                    names.add(unicodedata.normalize("NFC", name).casefold())
                if "manifest.json" not in {item.filename for item in infos}:
                    raise PackError("pack_missing_manifest")
                if bundle.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                    raise PackError("pack_manifest_limit")
                manifest_bytes = bundle.read("manifest.json")
                manifest = validate_manifest(_json(manifest_bytes))
                inventory = {item["path"]: item for item in manifest["files"]}
                if {info.filename for info in infos} != set(inventory) | {"manifest.json"}:
                    raise PackError("pack_inventory_mismatch")
                destination = self.root / "packs" / manifest["id"] / manifest["version"]
                self._safe_ancestors(destination)
                if destination.exists():
                    raise PackError("pack_version_exists")
                with tempfile.TemporaryDirectory(prefix=".install-", dir=self.root) as temporary:
                    stage = Path(temporary) / "pack"
                    stage.mkdir()
                    private_path(stage, directory=True)
                    for info in infos:
                        path = stage / info.filename
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with bundle.open(info) as incoming, path.open("xb") as outgoing:
                            digest = hashlib.sha256()
                            size = 0
                            while chunk := incoming.read(1024 * 1024):
                                size += len(chunk)
                                if size > info.file_size:
                                    raise PackError("pack_member_size_mismatch")
                                digest.update(chunk)
                                outgoing.write(chunk)
                        if info.filename != "manifest.json":
                            item = inventory[info.filename]
                            if size != item["size"] or digest.hexdigest() != item["sha256"]:
                                raise PackError("pack_file_hash_mismatch")
                            path.chmod(0o700 if item.get("executable") else 0o600)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage, destination)
                return self.resolve(manifest["id"], manifest["version"], require_host=False)

    def _active(self) -> dict:
        path = self.root / "active.json"
        self._safe_ancestors(path)
        if not path.exists():
            return {"schemaVersion": ACTIVE_SCHEMA, "packs": {}}
        result = _json(path.read_bytes())
        if result.get("schemaVersion") != ACTIVE_SCHEMA or not isinstance(
            result.get("packs"), dict
        ):
            raise PackError("pack_invalid_activation")
        return result

    def _write_active(self, value: dict) -> None:
        fd, name = tempfile.mkstemp(prefix=".active-", dir=self.root)
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(value, sort_keys=True, indent=2).encode())
                stream.flush()
                os.fsync(stream.fileno())
            private_path(path)
            os.replace(path, self.root / "active.json")
        finally:
            path.unlink(missing_ok=True)

    def activate(self, pack_id: str, version: str) -> InstalledPack:
        with self._lock():
            pack = self.resolve(pack_id, version)
            active = self._active()
            old = active["packs"].get(pack_id, {}).get("current")
            current = {"version": version, "manifestSha256": pack.manifest_sha256}
            if old != current:
                active["packs"][pack_id] = {"current": current, "previous": old}
                self._write_active(active)
            return pack

    def rollback(self, pack_id: str) -> InstalledPack:
        _identifier(pack_id)
        with self._lock():
            active = self._active()
            entry = active["packs"].get(pack_id, {})
            previous = entry.get("previous")
            if not previous:
                raise PackError("pack_no_previous_version")
            pack = self.resolve(pack_id, previous["version"])
            if pack.manifest_sha256 != previous["manifestSha256"]:
                raise PackError("pack_manifest_changed")
            entry["current"], entry["previous"] = previous, entry["current"]
            self._write_active(active)
            return pack

    def resolve(
        self, pack_id: str, version: str | None = None, *, require_host: bool = True
    ) -> InstalledPack:
        _identifier(pack_id)
        expected = None
        if version is None:
            try:
                current = self._active()["packs"][pack_id]["current"]
                version, expected = current["version"], current["manifestSha256"]
            except KeyError as exc:
                raise PackError("pack_not_active") from exc
        _identifier(version)
        root = self.root / "packs" / pack_id / version
        self._safe_ancestors(root)
        if not root.is_dir():
            raise PackError("pack_not_installed")
        path = root / "manifest.json"
        self._safe_ancestors(path)
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise PackError("pack_manifest_limit")
        manifest = validate_manifest(_json(path.read_bytes()))
        digest = sha256_file(path)
        if expected is not None and digest != expected:
            raise PackError("pack_manifest_changed")
        if manifest["id"] != pack_id or manifest["version"] != version:
            raise PackError("pack_identity_mismatch")
        listed = {item["path"] for item in manifest["files"]} | {"manifest.json"}
        actual = set()
        for child in root.rglob("*"):
            if child.is_symlink():
                raise PackError("pack_symlink_rejected")
            if child.is_file():
                actual.add(child.relative_to(root).as_posix())
            elif not child.is_dir():
                raise PackError("pack_nonregular_file")
        if listed != actual:
            raise PackError("pack_inventory_mismatch")
        for item in manifest["files"]:
            file = root / item["path"]
            if file.stat().st_size != item["size"] or sha256_file(file) != item["sha256"]:
                raise PackError("pack_file_hash_mismatch")
        if require_host:
            check_host(manifest)
        return InstalledPack(root, manifest, digest)

    def inspect(self) -> dict:
        return self._active()


@dataclass(frozen=True)
class LocalModelEndpoint:
    base_url: str
    api_key: str = field(repr=False)
    model: str = "Qwen3.5-9B-Q4_K_M"
    context_tokens: int = 8192
    runtime_manifest_sha256: str = ""
    model_manifest_sha256: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PackError("local_model_redirect_rejected")


@contextlib.contextmanager
def managed_llama_endpoint(
    store: PackStore,
    runtime_id: str,
    model_id: str,
    *,
    context_tokens: int = 8192,
    startup_timeout: float = 180,
    threads: int | None = None,
    threads_batch: int | None = None,
    parent_managed: bool = False,
):
    """Start one CPU slot using only verified local files; stop its whole process tree.

    No model selection fallback, inherited LLAMA_ARG/HF/proxy variables, model URLs,
    or arbitrary command arguments are accepted. Native code is not sandboxed.
    Thread counts are explicit administrator settings: ``threads`` bounds generation
    and ``threads_batch`` bounds prompt processing. Unset values keep the pinned
    llama.cpp defaults; nothing is derived from the host core count.
    """
    runtime, model = store.resolve(runtime_id), store.resolve(model_id)
    if runtime.manifest["kind"] != "llama-cpp-runtime" or model.manifest["kind"] != "model":
        raise PackError("local_model_wrong_pack_kind")
    identity = {
        "id": runtime_id,
        "version": runtime.manifest["version"],
        "manifestSha256": runtime.manifest_sha256,
    }
    if identity not in model.manifest["compatibleRuntimes"]:
        raise PackError("local_model_incompatible_runtime")
    config = model.manifest["model"]
    if config.get("name") != "Qwen3.5-9B" or config.get("quantization") != "Q4_K_M":
        raise PackError("local_model_not_approved")
    if type(context_tokens) is not int or not 1024 <= context_tokens <= config.get(
        "maxContextTokens", 8192
    ):
        raise PackError("local_model_invalid_context")
    if not 0 < startup_timeout <= 3600 or any(
        value is not None and (type(value) is not int or not 1 <= value <= 1024)
        for value in (threads, threads_batch)
    ):
        raise PackError("local_model_invalid_budget")
    executable = runtime.file(runtime.manifest["entrypoints"]["server"])
    model_file = model.file(config["file"])
    with model_file.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise PackError("local_model_invalid_gguf")
    vision = model_vision_config(model.manifest)
    projector = model.file(vision["file"]) if vision else None
    if projector is not None:
        with projector.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise PackError("local_model_invalid_projector")
    # Binding port zero selects a currently-free loopback port. Token-authenticated
    # readiness below fails closed if another listener wins the close/start race.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_urlsafe(32)
    alias = "Qwen3.5-9B-Q4_K_M"
    process = job = None
    with tempfile.TemporaryDirectory(prefix=".llama-", dir=store.root) as temporary:
        private_path(Path(temporary), directory=True)
        token_path = Path(temporary) / "api-key"
        token_path.write_text(token, encoding="ascii")
        private_path(token_path)
        command = [
            str(executable),
            "--model",
            str(model_file),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--alias",
            alias,
            "--api-key-file",
            str(token_path),
            "--ctx-size",
            str(context_tokens),
            "--parallel",
            "1",
            "--n-gpu-layers",
            "0",
            "--device",
            "none",
            "--no-mmproj-offload",
            "--no-op-offload",
            "--no-kv-offload",
            "--jinja",
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
            "--no-webui",
            "--no-agent",
            "--no-slots",
            "--no-warmup",
        ]
        if vision:
            command += [
                "--mmproj",
                str(projector),
                "--image-min-tokens",
                str(vision["minImageTokens"]),
                "--image-max-tokens",
                str(vision["maxImageTokens"]),
            ]
        if threads is not None:
            command += ["--threads", str(threads)]
        if threads_batch is not None:
            command += ["--threads-batch", str(threads_batch)]
        env = subprocess_environment()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "DO_NOT_TRACK": "1"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        options = process_options(supervised=True)
        if parent_managed and os.name == "posix":
            options["start_new_session"] = False
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=runtime.root,
                env=env,
                **options,
            )
            job = WindowsJob(process)
            deadline = time.monotonic() + startup_timeout
            ready = False
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise PackError("local_model_start_failed")
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/models",
                    headers={"Authorization": f"Bearer {token}"},
                )
                try:
                    with opener.open(
                        request, timeout=min(2, max(0.01, deadline - time.monotonic()))
                    ) as response:
                        result = json.loads(response.read(1024 * 1024))
                    ready = any(item.get("id") == alias for item in result.get("data", []))
                    if ready:
                        break
                except (OSError, urllib.error.URLError, json.JSONDecodeError):
                    pass
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            if not ready:
                raise PackError("local_model_start_timeout")
            if process.poll() is not None:
                raise PackError("local_model_start_failed")
            yield LocalModelEndpoint(
                f"http://127.0.0.1:{port}/v1",
                token,
                alias,
                context_tokens,
                runtime.manifest_sha256,
                model.manifest_sha256,
            )
        finally:
            if process is not None:
                if parent_managed and os.name == "posix":
                    # The job worker owns this process group. Never kill the worker
                    # from a child context cleanup; its supervisor kills the group
                    # on cancellation. llama.cpp uses threads, not child workers.
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                else:
                    kill_process_tree(process)
            if job is not None:
                job.close()
            if process is not None:
                process.wait(timeout=10)
