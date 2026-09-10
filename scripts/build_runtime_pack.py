#!/usr/bin/env python3
"""Build an offline pack from an audited staging directory, never download anything.

A declaration supplies pack metadata and per-file licenses. Every provenance
source must match a --source-artifact SHA256=PATH supplied by the release builder.
The output includes an exact file inventory, CycloneDX inventory and SHA256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from document_files.runtime_packs import (  # noqa: E402
    MAX_MANIFEST_BYTES,
    PackError,
    pack_artifact_digests,
    safe_relative,
    sha256_file,
    validate_manifest,
)


def build_pack(stage: Path, declaration: dict, output: Path, sources: dict[str, Path]) -> dict:
    if output.exists():
        raise PackError("pack_output_exists")
    if stage.is_symlink() or not stage.is_dir():
        raise PackError("pack_invalid_stage")
    manifest = json.loads(json.dumps(declaration))
    default_license = manifest.pop("defaultLicense", None)
    file_licenses = manifest.pop("fileLicenses", {})
    executables = set(manifest.pop("executables", []))
    for digest in pack_artifact_digests(manifest["provenance"]):
        if digest not in sources or sha256_file(sources[digest]) != digest:
            raise PackError("pack_unverified_build_source")
    files = []
    for path in sorted(stage.rglob("*")):
        if path.is_symlink():
            raise PackError("pack_symlink_rejected")
        if path.is_dir():
            continue
        if not path.is_file():
            raise PackError("pack_nonregular_file")
        name = safe_relative(path.relative_to(stage).as_posix())
        if name == "manifest.json":
            raise PackError("pack_manifest_in_stage")
        files.append(
            {
                "path": name,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "license": file_licenses.get(name, default_license),
                "executable": name in executables,
            }
        )
    if executables - {item["path"] for item in files}:
        raise PackError("pack_missing_executable")
    manifest["files"] = files
    repository = Path(__file__).resolve().parents[1]
    manifest["provenance"]["build"] = {
        "sourceCommit": subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
        ).strip(),
        "dirtySource": bool(
            subprocess.check_output(
                ["git", "-C", str(repository), "status", "--porcelain"], text=True
            ).strip()
        ),
        "builder": "scripts/build_runtime_pack.py",
    }
    validate_manifest(manifest)
    payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if len(payload) > MAX_MANIFEST_BYTES:
        raise PackError("pack_manifest_limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stored ZIP avoids CPU-heavy compression of already-quantized multi-GB weights.
    # Fixed timestamps/order make the same audited inputs byte reproducible.
    try:
        with zipfile.ZipFile(
            output, "x", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            info = zipfile.ZipInfo("manifest.json", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, payload)
            for item in files:
                info = zipfile.ZipInfo(item["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = (stat.S_IFREG | (0o700 if item["executable"] else 0o600)) << 16
                digest = hashlib.sha256()
                with (
                    (stage / item["path"]).open("rb") as incoming,
                    archive.open(info, "w", force_zip64=True) as outgoing,
                ):
                    while chunk := incoming.read(1024 * 1024):
                        digest.update(chunk)
                        outgoing.write(chunk)
                if digest.hexdigest() != item["sha256"]:
                    raise PackError("pack_build_input_changed")
    except Exception:
        output.unlink(missing_ok=True)
        raise
    sha = sha256_file(output)
    output.with_suffix(output.suffix + ".sha256").write_text(f"{sha}  {output.name}\n")
    output.with_suffix(output.suffix + ".manifest.json").write_bytes(payload)
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": manifest["id"],
                "version": manifest["version"],
            }
        },
        "components": manifest.get("components", [])
        + [
            {
                "type": "file",
                "name": item["path"],
                "hashes": [{"alg": "SHA-256", "content": item["sha256"]}],
                "licenses": [
                    {
                        "expression": next(
                            license["spdx"]
                            for license in manifest["licenses"]
                            if license["id"] == item["license"]
                        )
                    }
                ],
            }
            for item in files
        ],
    }
    output.with_suffix(output.suffix + ".cdx.json").write_text(json.dumps(sbom, indent=2) + "\n")
    return {
        "archive": output.name,
        "sha256": sha,
        "manifestSha256": hashlib.sha256(payload).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--declaration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-artifact", action="append", default=[], metavar="SHA256=PATH")
    args = parser.parse_args()
    sources = {}
    for value in args.source_artifact:
        digest, path = value.split("=", 1)
        sources[digest] = Path(path)
    print(
        json.dumps(
            build_pack(args.stage, json.loads(args.declaration.read_text()), args.output, sources)
        )
    )


if __name__ == "__main__":
    main()
