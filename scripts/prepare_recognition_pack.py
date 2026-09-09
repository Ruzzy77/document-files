#!/usr/bin/env python3
"""Verify an audited recognition stage offline; optionally invoke the existing pack builder.

No installation, download, native compilation, model loading or activation occurs here.
Supply a separately reviewed audit SHA256, declaration and all original source artifacts.
The audit binds exact staged bytes, licenses, wheel lock and target linkage evidence.
Verification is a packaging check, not proof of upstream authenticity, target execution,
model accuracy, or a reproducible native build. Those require independent CI evidence.

Audit v1: platform, declarationSha256, files[{path,size,sha256,license,sourceSha256}],
sources[{sha256,role,licenseIds}], wheelLock{path,sha256}, nativeLinkage{path,sha256}.
Linkage v1: platform, binaries[{path,sha256,tool,rawEvidence{path,sha256},
dependencies:[{name,origin:"system"}|{name,origin:"pack",path}]}].
Evidence references are relative to the audit directory. Every file and component
must be covered; model/OCR data are original source bytes, not silently transformed.
Native linkage must be collected on the target (readelf/otool/dumpbin or equivalent)
and reviewed before its audit hash is approved. No target runtime is installed here.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import struct
import sys
import zipfile
from email.parser import Parser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_runtime_pack import build_pack  # noqa: E402

from document_files.runtime_packs import (  # noqa: E402
    PackError,
    safe_relative,
    sha256_file,
    validate_manifest,
)

ROLES = {"python-runtime", "wheel", "native", "layout-model", "table-model", "ocr-data"}
HERON = "docling-project--docling-layout-heron"
TABLE = "docling-project--docling-models/model_artifacts/tableformer/accurate"
LINUX_SYSTEM = {
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libm.so.6",
    "libdl.so.2",
    "libpthread.so.0",
    "librt.so.1",
    "libutil.so.1",
    "libresolv.so.2",
}
WINDOWS_SYSTEM = {
    "kernel32.dll",
    "ntdll.dll",
    "user32.dll",
    "advapi32.dll",
    "ws2_32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "shell32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "secur32.dll",
    "rpcrt4.dll",
    "version.dll",
    "winmm.dll",
    "gdi32.dll",
    "comdlg32.dll",
    "shlwapi.dll",
    "normaliz.dll",
    "psapi.dll",
    "iphlpapi.dll",
    "dbghelp.dll",
    "ucrtbase.dll",
}


def load(path: Path) -> dict:
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackError("recognition_duplicate_json_key")
            result[key] = value
        return result

    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise PackError("recognition_invalid_audit")
    return result


def verified_file(root: Path, reference: dict) -> Path:
    path = root / safe_relative(reference["path"])
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise PackError("recognition_unverified_file")
    if sha256_file(path) != reference["sha256"]:
        raise PackError("recognition_file_hash_mismatch")
    return path


def binary_targets(path: Path) -> set[str]:
    """Read only executable headers; never execute a staged file to identify it."""
    with path.open("rb") as stream:
        head = stream.read(4096)
    if head.startswith(b"\x7fELF"):
        if len(head) < 20 or head[4:6] != b"\x02\x01":
            return {"unsupported-elf"}
        return {
            {62: "linux-x86_64", 183: "linux-aarch64"}.get(
                struct.unpack_from("<H", head, 18)[0], "other-elf"
            )
        }
    if head[:2] == b"MZ":
        if len(head) < 64:
            return {"invalid-pe"}
        offset = struct.unpack_from("<I", head, 60)[0]
        with path.open("rb") as stream:
            stream.seek(offset)
            pe = stream.read(6)
        return {"windows-x86_64"} if pe == b"PE\0\0\x64\x86" else {"other-pe"}
    magics = {b"\xcf\xfa\xed\xfe": "<", b"\xfe\xed\xfa\xcf": ">"}
    cpus = {0x0100000C: "macos-aarch64", 0x01000007: "macos-x86_64"}
    if head[:4] in magics and len(head) >= 8:
        return {cpus.get(struct.unpack_from(magics[head[:4]] + "I", head, 4)[0], "other-macho")}
    fats = {
        b"\xca\xfe\xba\xbe": (">", 20),
        b"\xbe\xba\xfe\xca": ("<", 20),
        b"\xca\xfe\xba\xbf": (">", 32),
        b"\xbf\xba\xfe\xca": ("<", 32),
    }
    if head[:4] in fats and len(head) >= 8:
        endian, width = fats[head[:4]]
        count = struct.unpack_from(endian + "I", head, 4)[0]
        if not 1 <= count <= 16 or len(head) < 8 + count * width:
            return {"invalid-fat"}
        return {
            cpus.get(struct.unpack_from(endian + "I", head, 8 + i * width)[0], "other-macho")
            for i in range(count)
        }
    return set()


def cpu_torch(stage: Path, files: dict, platform: str) -> None:
    versions = [name for name in files if name.endswith("/site-packages/torch/version.py")]
    if len(versions) != 1:
        raise PackError("recognition_missing_torch_cpu_evidence")
    values = {}
    for node in ast.parse((stage / versions[0]).read_text()).body:
        if isinstance(node, ast.Assign):
            names, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            names, value = [node.target], node.value
        else:
            continue
        for name in names:
            if isinstance(name, ast.Name) and name.id in {"cuda", "hip", "__version__"}:
                try:
                    values[name.id] = ast.literal_eval(value)
                except (ValueError, TypeError) as exc:
                    raise PackError("recognition_unverifiable_torch_cpu") from exc
    if values.get("cuda", "missing") is not None or values.get("hip", "missing") is not None:
        raise PackError("recognition_gpu_torch_rejected")
    if platform == "linux-x86_64" and "+cpu" not in values.get("__version__", ""):
        raise PackError("recognition_linux_torch_cpu_build_required")


def verify_arm_cpu_wheel(stage, files, sources, wheel_origins, provenance):
    """ARM CPU releases need not have +cpu; verify exact official wheel bytes instead."""
    origins = [digest for digest, (name, _) in wheel_origins.items() if name == "torch"]
    if len(origins) != 1:
        raise PackError("recognition_missing_torch_cpu_evidence")
    digest = origins[0]
    wheel = sources[digest]
    links = [entry["uri"] for entry in provenance if entry["sha256"] == digest]

    def official(url):
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname in {"download.pytorch.org", "download-r2.pytorch.org"}
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query
            and unquote(parsed.path) == "/whl/cpu/" + wheel.name
            and (not parsed.fragment or parsed.fragment == "sha256=" + digest)
        )

    if not any(official(url) for url in links) or not re.fullmatch(
        r"torch-[^-]+-cp[0-9]+-cp[0-9]+-manylinux_[0-9_]+_aarch64.whl", wheel.name
    ):
        raise PackError("recognition_arm_torch_official_cpu_source_required")
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or any(
            re.search(
                r"(?:cuda|cudnn|cublas|nvrtc|amdhip|hiprtc|rocblas).*\.(?:so|dll)", name, re.I
            )
            for name in names
        ):
            raise PackError("recognition_gpu_wheel")
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        meta = Parser().parsestr(archive.read(metadata[0]).decode())
        if any(
            re.search(r"nvidia|cuda|rocm|triton", dep, re.I)
            for dep in meta.get_all("Requires-Dist", [])
        ):
            raise PackError("recognition_gpu_wheel")
        seen_version = False
        for name, item in files.items():
            if "/site-packages/torch/" not in name:
                continue
            relative = name.split("/site-packages/", 1)[1]
            if item.get("sourceSha256") != digest or relative not in names:
                raise PackError("recognition_torch_file_origin_mismatch")
            import hashlib

            with archive.open(relative) as stream:
                original = hashlib.file_digest(stream, "sha256").hexdigest()
            if original != item["sha256"]:
                raise PackError("recognition_torch_file_origin_mismatch")
            seen_version |= relative == "torch/version.py"
        if not seen_version:
            raise PackError("recognition_missing_torch_cpu_evidence")


def verify_linkage(stage: Path, files: dict, audit_path: Path, audit: dict, target: str) -> None:
    evidence = load(verified_file(audit_path.parent, audit["nativeLinkage"]))
    if evidence.get("schemaVersion") != "document-files.recognition-native-linkage.v1" or (
        evidence.get("platform") != target
    ):
        raise PackError("recognition_wrong_linkage_target")
    binaries = {}
    for name in files:
        targets = binary_targets(stage / name)
        if targets:
            if target not in targets:
                raise PackError("recognition_foreign_native_binary")
            if re.search(r"cuda|cudnn|cublas|nvrtc|amdhip|hiprtc|rocblas", Path(name).name, re.I):
                raise PackError("recognition_gpu_native_library")
            if target.startswith("linux-"):
                from linux_abi import inspect_header

                try:
                    inspect_header(stage / name, target)
                except ValueError as exc:
                    raise PackError("recognition_foreign_native_binary") from exc
                # Linux stages use the system glibc loader, not a bundled copy.
                # This covers ld-linux*.so names (including relocated hash names),
                # not arbitrary renamed ELF loaders or their symbol semantics.
                if re.fullmatch(r"ld-linux[^/]*\.so(?:\.[^/]+)*", Path(name).name):
                    raise PackError("recognition_bundled_linux_loader")
            binaries[name] = files[name]["sha256"]
    entries = evidence.get("binaries", [])
    if len(entries) != len(binaries) or {item["path"] for item in entries} != set(binaries):
        raise PackError("recognition_incomplete_native_linkage")
    for item in entries:
        if item.get("sha256") != binaries[item["path"]] or not item.get("tool"):
            raise PackError("recognition_stale_native_linkage")
        # Preserve raw target-tool output; the verifier does not pretend to have run it.
        verified_file(audit_path.parent, item["rawEvidence"])
        if not isinstance(item.get("dependencies"), list):
            raise PackError("recognition_missing_native_dependencies")
        for dep in item["dependencies"]:
            name = dep["name"]
            if dep.get("origin") == "pack":
                referenced = safe_relative(dep["path"])
                if referenced not in binaries:
                    raise PackError("recognition_missing_native_dependency")
            elif dep.get("origin") == "system":
                allowed = (
                    target == "macos-aarch64"
                    and name.startswith(("/usr/lib/", "/System/Library/"))
                    or target.startswith("linux-")
                    and name
                    in (
                        (LINUX_SYSTEM - {"ld-linux-x86-64.so.2"}) | {"ld-linux-aarch64.so.1"}
                        if target == "linux-aarch64"
                        else LINUX_SYSTEM
                    )
                    or target == "windows-x86_64"
                    and (
                        name.lower() in WINDOWS_SYSTEM
                        or re.fullmatch(r"api-ms-win-[a-z0-9-]+\.dll", name.lower()) is not None
                    )
                )
                if not allowed:
                    raise PackError("recognition_unbundled_native_dependency")
            else:
                raise PackError("recognition_unknown_native_dependency")


def verify_stage(
    stage: Path,
    declaration_path: Path,
    audit_path: Path,
    audit_sha256: str,
    sources: dict[str, Path],
) -> tuple[dict, dict]:
    if sha256_file(audit_path) != audit_sha256:
        raise PackError("recognition_untrusted_audit")
    audit, declaration = load(audit_path), load(declaration_path)
    if audit.get("schemaVersion") != "document-files.recognition-stage-audit.v1" or (
        audit.get("declarationSha256") != sha256_file(declaration_path)
    ):
        raise PackError("recognition_wrong_declaration")
    target = declaration.get("platform")
    if target == "macos-x86_64":
        raise PackError("pack_intel_recognition_requires_linux_container")
    if target not in {"macos-aarch64", "linux-x86_64", "linux-aarch64", "windows-x86_64"} or (
        audit.get("platform") != target or declaration.get("kind") != "recognition"
    ):
        raise PackError("recognition_wrong_target")
    if target.startswith("linux-") and not re.fullmatch(
        r"\d+\.\d+(?:\.\d+)?", declaration.get("minimumGlibc", "")
    ):
        raise PackError("recognition_missing_glibc_baseline")
    if stage.is_symlink() or not stage.is_dir() or any(p.is_symlink() for p in stage.parents):
        raise PackError("recognition_invalid_stage")
    entries = audit.get("files", [])
    files = {safe_relative(item["path"]): item for item in entries}
    if not files or len(files) != len(entries):
        raise PackError("recognition_invalid_file_inventory")
    actual = set()
    for path in stage.rglob("*"):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise PackError("recognition_nonregular_stage_file")
        if path.is_file():
            actual.add(path.relative_to(stage).as_posix())
    if actual != set(files):
        raise PackError("recognition_stage_inventory_mismatch")
    provenance = {item["sha256"] for item in declaration["provenance"]["sources"]}
    source_rows = audit.get("sources", [])
    audited_sources = {item["sha256"]: item for item in source_rows}
    if set(audited_sources) != provenance or len(audited_sources) != len(source_rows):
        raise PackError("recognition_source_inventory_mismatch")
    if set(sources) != provenance:
        raise PackError("recognition_source_arguments_mismatch")
    if not {item.get("role") for item in source_rows} >= ROLES:
        raise PackError("recognition_missing_upstream_component")
    licenses = {item["id"] for item in declaration["licenses"]}
    lock = verified_file(audit_path.parent, audit["wheelLock"]).read_text()
    # Scope each digest to its own fully pinned package stanza.
    locked = {}
    package_key = None
    for line in lock.splitlines():
        requirement = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", line)
        if requirement:
            package_key = (re.sub(r"[-_.]+", "-", requirement[1]).lower(), requirement[2])
            locked.setdefault(package_key, set())
        if package_key and not line.lstrip().startswith("#"):
            locked[package_key].update(re.findall(r"--hash=sha256:([a-f0-9]{64})", line))
    wheels = set()
    wheel_origins = {}
    for digest, source in audited_sources.items():
        path = sources.get(digest)
        if path is None or path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise PackError("pack_unverified_build_source")
        if not source.get("licenseIds") or not set(source["licenseIds"]) <= licenses:
            raise PackError("recognition_unlicensed_upstream_component")
        if source.get("role") == "wheel":
            if path.suffix != ".whl":
                raise PackError("recognition_unlocked_wheel")
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
                if len(names) != 1:
                    raise PackError("recognition_invalid_wheel")
                meta = Parser().parsestr(archive.read(names[0]).decode())
                package = re.sub(r"[-_.]+", "-", meta.get("Name", "")).lower()
                version = meta.get("Version", "")
                if re.search(r"nvidia|cuda|rocm|triton", package, re.I):
                    raise PackError("recognition_gpu_wheel")
                if digest not in locked.get((package, version), set()):
                    raise PackError("recognition_unpinned_wheel_version")
                wheel_origins[digest] = (package, version)
                wheels.add((package, version))
    installed = set()
    manifest_files = []
    for name, item in files.items():
        path = verified_file(stage, {"path": name, "sha256": item["sha256"]})
        source = audited_sources.get(item.get("sourceSha256"), {})
        chosen_license = declaration.get("fileLicenses", {}).get(
            name, declaration.get("defaultLicense")
        )
        if (
            path.stat().st_size != item["size"]
            or item.get("license") != chosen_license
            or chosen_license not in source.get("licenseIds", [])
        ):
            raise PackError("recognition_unverified_stage_origin")
        manifest_files.append(
            {
                "path": name,
                "size": item["size"],
                "sha256": item["sha256"],
                "license": chosen_license,
                "executable": name in declaration.get("executables", []),
            }
        )
        if name.endswith(".dist-info/METADATA"):
            meta = Parser().parsestr(path.read_text())
            package = (re.sub(r"[-_.]+", "-", meta["Name"]).lower(), meta["Version"])
            if wheel_origins.get(item.get("sourceSha256")) != package:
                raise PackError("recognition_installed_wheel_origin_mismatch")
            installed.add(package)
    if installed != wheels or not {"docling", "torch"} <= {name for name, _ in installed}:
        raise PackError("recognition_installed_wheel_mismatch")
    manifest = {**declaration, "files": manifest_files}
    validate_manifest(manifest)
    for license in declaration["licenses"]:
        if not (stage / license["path"]).read_bytes().strip():
            raise PackError("recognition_empty_license_notice")
    settings = declaration["recognition"]
    required = (
        {
            f"{settings['artifacts']}/{HERON}/{name}"
            for name in ("config.json", "preprocessor_config.json", "model.safetensors")
        }
        | {
            f"{settings['artifacts']}/{TABLE}/{name}"
            for name in ("tm_config.json", "tableformer_accurate.safetensors")
        }
        | {
            f"{settings['tessdata']}/{name}"
            for name in ("kor.traineddata", "eng.traineddata", "osd.traineddata", "configs/tsv")
        }
    )
    if not required <= actual or any(files[name]["size"] == 0 for name in required):
        raise PackError("recognition_missing_model_resource")
    for name in required:
        origin = audited_sources[files[name]["sourceSha256"]]
        role = (
            "ocr-data"
            if name.startswith(settings["tessdata"] + "/")
            else ("layout-model" if f"/{HERON}/" in name else "table-model")
        )
        if origin.get("role") != role or files[name]["sourceSha256"] != files[name]["sha256"]:
            raise PackError("recognition_model_not_original_source_bytes")
    for key in ("python", "tesseract"):
        expected_role = "python-runtime" if key == "python" else "native"
        if audited_sources[files[settings[key]]["sourceSha256"]].get("role") != expected_role:
            raise PackError("recognition_entrypoint_origin_mismatch")
        if target not in binary_targets(stage / settings[key]):
            raise PackError("recognition_non_native_entrypoint")
    cpu_torch(stage, files, target)
    if target == "linux-aarch64":
        verify_arm_cpu_wheel(
            stage, files, sources, wheel_origins, declaration["provenance"]["sources"]
        )
    verify_linkage(stage, files, audit_path, audit, target)
    receipt = {
        "schemaVersion": "document-files.recognition-stage-verification.v1",
        "auditSha256": audit_sha256,
        "declarationSha256": sha256_file(declaration_path),
        "platform": target,
        "filesVerified": len(files),
        "upstreamArtifactsVerified": len(audited_sources),
        "executionVerified": False,
        "modelQuality": "not-assessed",
        "checks": [
            "exact-stage-bytes",
            "source-hashes",
            "per-file-licenses",
            "cpu-wheel-lock",
            "native-target-headers",
            "recorded-linkage-closure",
            "offline-model-resources",
        ],
    }
    return declaration, receipt


def build_verified(
    stage: Path,
    declaration_path: Path,
    audit_path: Path,
    audit_sha256: str,
    sources: dict[str, Path],
    output: Path,
) -> dict:
    artifacts = [
        Path(str(output) + suffix) for suffix in ("", ".sha256", ".manifest.json", ".cdx.json")
    ]
    if any(path.exists() or path.is_symlink() for path in artifacts):
        raise PackError("pack_output_exists")
    declaration, receipt = verify_stage(stage, declaration_path, audit_path, audit_sha256, sources)
    expected = {item["path"]: item for item in load(audit_path)["files"]}
    try:
        receipt["pack"] = build_pack(stage, declaration, output, sources)
        packed = load(Path(str(output) + ".manifest.json"))["files"]
        # The generic builder reinventories actual bytes. Check its sealed result
        # against the approved audit so a stage change between verification and
        # packing cannot silently become a newly approved file inventory.
        if (
            sha256_file(audit_path) != audit_sha256
            or sha256_file(declaration_path) != receipt["declarationSha256"]
            or len(packed) != len(expected)
            or any(
                item["path"] not in expected
                or any(
                    item[key] != expected[item["path"]][key]
                    for key in ("size", "sha256", "license")
                )
                for item in packed
            )
        ):
            raise PackError("recognition_stage_changed_during_build")
    except Exception:
        for path in artifacts:
            path.unlink(missing_ok=True)  # Only this invocation's new output files.
        raise
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--audit-sha256", required=True)
    parser.add_argument("--source-artifact", action="append", default=[], metavar="SHA256=PATH")
    parser.add_argument(
        "--output", type=Path, help="Optional NEW pack archive; omission verifies only"
    )
    args = parser.parse_args()
    sources = {}
    for value in args.source_artifact:
        digest, path = value.split("=", 1)
        if digest in sources:
            raise PackError("recognition_duplicate_source_argument")
        sources[digest] = Path(path)
    if args.output:
        receipt = build_verified(
            args.stage, args.declaration, args.audit, args.audit_sha256, sources, args.output
        )
    else:
        _, receipt = verify_stage(
            args.stage, args.declaration, args.audit, args.audit_sha256, sources
        )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
