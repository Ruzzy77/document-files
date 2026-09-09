#!/usr/bin/env python3
"""Verify release-hosted pack bytes against an independently trusted inventory hash.

Downloads occur only in this explicit CI/preparation command. No pack is activated,
no model runs and no release is created. Reconstructed ZIPs are never described as
uploaded subjects; verification attests the actual original-or-parts transport.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import check_release_qualification as gate
import release_parts

from document_files.runtime_packs import PackStore

REPOSITORY = "Ruzzy77/document-files"


def download(name: str, directory: Path, candidate: str) -> None:
    release_parts.safe_name(name)
    subprocess.run(
        [
            "gh",
            "release",
            "download",
            candidate,
            "--repo",
            REPOSITORY,
            "--pattern",
            name,
            "--dir",
            str(directory),
        ],
        check=True,
    )


def verify(
    inventory_path: Path, trusted_sha: str, commit: str, version: str, work: Path, *, fetch
) -> dict:
    _, inventory = gate._linked(inventory_path.parent, inventory_path.name, trusted_sha)
    gate._identity(inventory, commit, version)
    if inventory.get("schemaVersion") != "document-files.artifact-inventory.v2":
        raise ValueError("Trusted artifact inventory v2 required")
    if work.exists() or work.is_symlink():
        raise ValueError("Candidate verification requires a fresh directory")
    work.mkdir(parents=True)
    subjects, reconstructed = work / "subjects", work / "reconstructed"
    subjects.mkdir()
    reconstructed.mkdir()
    seen = set()
    verified, uploaded = [], []

    def upload_name(relative: str) -> str:
        ref = Path(relative)
        if ref.is_absolute() or ".." in ref.parts or "\\" in relative or ":" in relative:
            raise ValueError("Unsafe inventory asset path")
        return release_parts.safe_name(ref.name)

    def acquire(relative: str, digest: str) -> Path:
        name = upload_name(relative)
        if name.casefold() in seen:
            raise ValueError("Duplicate release asset basename")
        seen.add(name.casefold())
        fetch(name, subjects)
        path = gate._file(subjects, name, digest)
        uploaded.append({"name": name, "sha256": digest})
        return path

    ids = [asset["id"] for asset in inventory["artifacts"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate artifact identity")
    for asset in inventory["artifacts"]:
        if asset.get("kind") not in {"runtime", "recognition", "model"}:
            continue
        transport = asset.get("transport")
        if transport:
            ref = transport["manifest"]
            manifest_path = acquire(ref["path"], ref["sha256"])
            manifest = release_parts.load_manifest(manifest_path)
            original_name = upload_name(asset["path"])
            if (
                manifest["archive"]["sha256"] != asset["sha256"]
                or manifest["archive"]["name"] != original_name
            ):
                raise ValueError("Transport differs from trusted original archive")
            # Keep uploaded parts, reconstruction and installed bytes simultaneously.
            # Do not clear unrelated runner files to manufacture enough disk space.
            if shutil.disk_usage(work).free < 3 * manifest["archive"]["size"] + 64 * 1024**2:
                raise ValueError(
                    "Insufficient disk for retained transport, reconstruction and pack verification"
                )
            for part in manifest["parts"]:
                acquire(part["name"], part["sha256"])
            path = release_parts.join(
                manifest_path, reconstructed / original_name, expected_sha256=asset["sha256"]
            )
        else:
            path = acquire(asset["path"], asset["sha256"])
        with zipfile.ZipFile(path) as archive:
            required_disk = sum(member.file_size for member in archive.infolist()) + 64 * 1024**2
        if shutil.disk_usage(work).free < required_disk:
            raise ValueError("Insufficient disk for isolated pack verification")
        with tempfile.TemporaryDirectory(prefix="pack-install-", dir=work) as store_path:
            pack = PackStore(store_path).install(path, asset["sha256"])
            build = pack.manifest["provenance"]["build"]
            if (
                build.get("sourceCommit") != commit
                or build.get("dirtySource") is not False
                or pack.manifest_sha256 != asset["manifestSha256"]
                or pack.manifest["platform"] != asset["target"]
                or pack.manifest["kind"]
                != {"runtime": "llama-cpp-runtime"}.get(asset["kind"], asset["kind"])
            ):
                raise ValueError("Installed pack differs from trusted source and manifest")
            verified.append(
                {
                    "id": asset["id"],
                    "name": path.name,
                    "sha256": asset["sha256"],
                    "manifestSha256": pack.manifest_sha256,
                    "transport": "multipart" if transport else "original",
                }
            )
        if transport:
            path.unlink()  # Only this verifier's completed reconstruction, not source/subjects.
    if not verified:
        raise ValueError("No candidate packs in trusted inventory")
    result = {
        "schemaVersion": "document-files.pack-verification.v2",
        "sourceCommit": commit,
        "version": version,
        "inventorySha256": trusted_sha,
        "packs": verified,
        "uploadedSubjects": uploaded,
        "checks": [
            "trusted-inventory",
            "archive-sha256",
            "transport-reconstruction",
            "file-inventory",
            "manifest-contract",
            "clean-source",
        ],
        "modelQuality": "not-assessed",
        "upstreamBuild": "not-attested",
    }
    with (work / "verification.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--inventory-asset", default="artifact-inventory.json")
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    if (
        not release_parts.valid_digest(args.inventory_sha256)
        or not re.fullmatch(r"[a-f0-9]{40}", args.source_commit)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", args.candidate)
    ):
        parser.error(
            "An independently trusted inventory SHA256, full commit and safe tag are required"
        )
    release_parts.safe_name(args.inventory_asset)
    # Fetch the small trusted inventory first; no model/pack download before its hash check.
    with tempfile.TemporaryDirectory(prefix="candidate-inventory-") as metadata:
        directory = Path(metadata)
        download(args.inventory_asset, directory, args.candidate)
        result = verify(
            directory / args.inventory_asset,
            args.inventory_sha256,
            args.source_commit,
            args.version,
            args.work,
            fetch=lambda name, target: download(name, target, args.candidate),
        )
    print(json.dumps({"packsVerified": len(result["packs"]), "modelQuality": "not-assessed"}))


if __name__ == "__main__":
    main()
