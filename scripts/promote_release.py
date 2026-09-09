#!/usr/bin/env python3
"""Verify frozen release bytes; dry-run by default. Never overwrite a release.

--publish is an explicit external-publication action. A new draft is uploaded,
its downloaded bytes verified, and only then made public. Failure leaves the draft
for operator inspection; neither tags nor existing releases are removed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import check_release_qualification as gate
import release_parts

REPOSITORY = "Ruzzy77/document-files"
GITHUB_ASSET_LIMIT = 2 * 1024**3


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(manifest: Path, root: Path, commit: str, version: str) -> list[dict]:
    gate.check(manifest, root, commit, version)
    document = json.loads(manifest.read_text())
    assets = gate.artifact_inventory(document, root, commit, version)
    if document.get("publishArtifactInventory") is not True:
        raise ValueError("Explicit public aggregate inventory publication is required")
    selected = document.get("releaseAssets", [])
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(key not in assets for key in selected)
    ):
        raise ValueError("Explicit unique releaseAssets required")
    # All executable/data artifacts that qualified must be delivered, not a subset.
    required = {key for key, asset in assets.items() if asset["kind"] != "metadata"}
    if not required <= set(selected):
        raise ValueError("Release assets omit qualified pipeline artifacts")
    inventory_ref = document["artifactInventory"]
    files = [
        {
            "id": "@artifact-inventory",
            "kind": "metadata",
            "verifiedPath": gate._file(root, inventory_ref["path"], inventory_ref["sha256"]),
            "sha256": inventory_ref["sha256"],
        }
    ]
    for key in selected:
        asset = assets[key]
        transport = asset.get("transport")
        if transport is None:
            files.append(asset)
            continue
        ref = transport["manifest"]
        manifest_path = gate._file(root, ref["path"], ref["sha256"])
        manifest = release_parts.verify(manifest_path, expected_sha256=asset["sha256"])
        if (
            manifest["archive"]["name"] != asset["verifiedPath"].name
            or manifest["archive"]["size"] != asset["verifiedPath"].stat().st_size
        ):
            raise ValueError("Transport original archive identity mismatch")
        files.append(
            {
                "id": key + ":transport",
                "kind": "transport-manifest",
                "verifiedPath": manifest_path,
                "sha256": ref["sha256"],
            }
        )
        files.extend(
            {
                "id": key + ":" + part["name"],
                "kind": "transport-part",
                "verifiedPath": manifest_path.parent / part["name"],
                "sha256": part["sha256"],
            }
            for part in manifest["parts"]
        )
    if len({a["verifiedPath"].name.casefold() for a in files}) != len(files):
        raise ValueError("Release asset basenames must be unique")
    check_asset_sizes(files)
    return files


def check_asset_sizes(files: list[dict]) -> None:
    # https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
    if any(asset["verifiedPath"].stat().st_size >= GITHUB_ASSET_LIMIT for asset in files):
        raise ValueError(
            "GitHub assets must be under 2 GiB; prepare an approved multipart transport"
        )


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True)


def optional_api(path: str) -> dict | None:
    result = subprocess.run(["gh", "api", path], text=True, capture_output=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise ValueError("GitHub identity check failed; no release was created")
    return json.loads(result.stdout)


def verify_remote(tag: str, commit: str) -> None:
    if optional_api(f"repos/{REPOSITORY}/releases/tags/{tag}") is not None:
        raise ValueError("Existing releases are never overwritten")
    verify_tag(tag, commit)


def verify_tag(tag: str, commit: str) -> None:
    ref = optional_api(f"repos/{REPOSITORY}/git/ref/tags/{tag}")
    if ref is None:
        return
    obj = ref["object"]
    for _ in range(8):
        if obj["type"] == "commit":
            if obj["sha"] != commit:
                raise ValueError("Existing release tag points at different source")
            return
        if obj["type"] != "tag":
            break
        obj = json.loads(gh("api", f"repos/{REPOSITORY}/git/tags/{obj['sha']}"))["object"]
    raise ValueError("Unable to establish release tag source identity")


def publish(files: list[dict], tag: str, commit: str, notes: Path) -> None:
    check_asset_sizes(files)
    verify_remote(tag, commit)
    with tempfile.TemporaryDirectory(prefix="document-files-promotion-") as temporary:
        stage = Path(temporary)
        uploads = []
        for asset in files:
            path = stage / asset["verifiedPath"].name
            shutil.copyfile(asset["verifiedPath"], path)
            if digest(path) != asset["sha256"]:
                raise ValueError("Release artifact changed after qualification")
            uploads.append(path)
        # Never upload private evidence implicitly. Only explicit releaseAssets.
        gh(
            "release",
            "create",
            tag,
            *(str(path) for path in uploads),
            "--repo",
            REPOSITORY,
            "--target",
            commit,
            "--draft",
            "--title",
            tag,
            "--notes-file",
            str(notes),
        )
        downloaded = stage / "downloaded"
        downloaded.mkdir()
        gh("release", "download", tag, "--repo", REPOSITORY, "--dir", str(downloaded))
        if {path.name for path in downloaded.iterdir()} != {path.name for path in uploads}:
            raise ValueError("Uploaded draft asset inventory mismatch; draft retained")
        for asset in files:
            if digest(downloaded / asset["verifiedPath"].name) != asset["sha256"]:
                raise ValueError("Uploaded draft bytes mismatch; draft retained")
        # Recheck the tag after creation, without permitting an existing release overwrite.
        release = json.loads(gh("api", f"repos/{REPOSITORY}/releases/tags/{tag}"))
        if release.get("draft") is not True:
            raise ValueError("Draft state changed; refusing promotion")
        verify_tag(tag, commit)
        gh("release", "edit", tag, "--repo", REPOSITORY, "--draft=false")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--notes", type=Path, help="Reviewed public release notes; required to publish"
    )
    args = parser.parse_args()
    version = tomllib.loads((gate.ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=gate.ROOT, text=True).strip()
    try:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=gate.ROOT, text=True):
            raise ValueError("Promotion requires a clean checkout")
        files = verify(args.manifest, args.evidence_root, commit, version)
        if args.publish:
            if args.notes is None or not args.notes.is_file():
                raise ValueError("Reviewed public release notes are required")
            publish(files, f"v{version}", commit, args.notes)
        print(
            json.dumps(
                {
                    "published": args.publish,
                    "sourceCommit": commit,
                    "tag": f"v{version}",
                    "artifacts": [
                        {"name": a["verifiedPath"].name, "sha256": a["sha256"]} for a in files
                    ],
                },
                indent=2,
            )
        )
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Release NOT promoted: {exc}") from exc


if __name__ == "__main__":
    main()
