#!/usr/bin/env python3
"""Explicit local release transport only; never download or alter a pack manifest.

Split a verified archive before upload, or join locally prepared parts before
PackStore installation. The ordered transport manifest preserves the original
archive identity. Existing outputs are never replaced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

DEFAULT_PART_SIZE = 1024**3
MAX_PART_SIZE = 2 * 1024**3 - 1
CHUNK_SIZE = 1024**2
SCHEMA = "document-files.release-parts.v1"


def safe_path(path: Path, *, existing: bool = True) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Transport symlinks are not allowed")
    if existing and not path.is_file():
        raise ValueError("Transport input must be a regular file")
    return path


def safe_name(name: str) -> str:
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)
        or name in {".", ".."}
    ):
        raise ValueError("Unsafe transport filename")
    return name


def valid_digest(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def identity(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def load_manifest(path: Path) -> dict:
    path = safe_path(path)
    if path.stat().st_size > 1024**2:
        raise ValueError("Transport manifest is too large")
    manifest = json.loads(path.read_bytes())
    if manifest.get("schemaVersion") != SCHEMA:
        raise ValueError("Unknown transport manifest")
    archive = manifest["archive"]
    name = safe_name(archive["name"])
    part_size = manifest["partSizeBytes"]
    if (
        not valid_digest(archive["sha256"])
        or type(archive["size"]) is not int
        or archive["size"] <= 0
        or type(part_size) is not int
        or not 0 < part_size <= MAX_PART_SIZE
    ):
        raise ValueError("Invalid transport archive identity or size")
    parts = manifest["parts"]
    if not isinstance(parts, list) or not parts or len(parts) > 999999:
        raise ValueError("Invalid transport part inventory")
    for index, part in enumerate(parts, 1):
        if (
            safe_name(part["name"]) != f"{name}.part-{index:06d}"
            or type(part["size"]) is not int
            or not 0 < part["size"] <= part_size
            or (index < len(parts) and part["size"] != part_size)
            or not valid_digest(part["sha256"])
        ):
            raise ValueError("Transport parts must be complete, ordered and unique")
    if sum(part["size"] for part in parts) != archive["size"]:
        raise ValueError("Transport size total mismatch")
    return manifest


def split(source: Path, output: Path, *, part_size: int = DEFAULT_PART_SIZE) -> Path:
    source = safe_path(source)
    name = safe_name(source.name)
    before = identity(source)
    if before[2] <= 0 or type(part_size) is not int or not 0 < part_size <= MAX_PART_SIZE:
        raise ValueError("Nonempty archive and a part size under 2 GiB are required")
    output = safe_path(output, existing=False)
    output.mkdir()  # Exclusive, deliberately not exist_ok. Failed stages remain inspectable.
    whole = hashlib.sha256()
    parts = []
    with source.open("rb") as incoming:
        while True:
            first = incoming.read(min(CHUNK_SIZE, part_size))
            if not first:
                break
            index = len(parts) + 1
            if index > 999999:
                raise ValueError("Too many transport parts")
            part_name = f"{name}.part-{index:06d}"
            checksum = hashlib.sha256()
            size = 0
            with (output / part_name).open("xb") as outgoing:
                chunk = first
                while chunk:
                    outgoing.write(chunk)
                    checksum.update(chunk)
                    whole.update(chunk)
                    size += len(chunk)
                    if size == part_size:
                        break
                    chunk = incoming.read(min(CHUNK_SIZE, part_size - size))
                outgoing.flush()
                os.fsync(outgoing.fileno())
            parts.append({"name": part_name, "size": size, "sha256": checksum.hexdigest()})
    if identity(source) != before or sum(part["size"] for part in parts) != before[2]:
        raise ValueError("Source archive changed during splitting; incomplete stage retained")
    manifest = {
        "schemaVersion": SCHEMA,
        "archive": {
            "name": name,
            "size": before[2],
            "sha256": whole.hexdigest(),
        },
        "partSizeBytes": part_size,
        "parts": parts,
    }
    manifest_path = output / f"{name}.parts.json"
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    return manifest_path


def verify(path: Path, *, expected_sha256: str | None = None, sink=None) -> dict:
    """Verify ordered parts and original bytes, optionally streaming into a private sink."""
    path = safe_path(path)
    before_manifest = identity(path)
    manifest = load_manifest(path)
    if expected_sha256 is not None and manifest["archive"]["sha256"] != expected_sha256:
        raise ValueError("Transport does not match the expected original archive")
    whole = hashlib.sha256()
    for part in manifest["parts"]:
        source = safe_path(path.parent / part["name"])
        before = identity(source)
        if before[2] != part["size"]:
            raise ValueError("Transport part size mismatch")
        checksum = hashlib.sha256()
        count = 0
        with source.open("rb") as incoming:
            while chunk := incoming.read(CHUNK_SIZE):
                count += len(chunk)
                if count > part["size"]:
                    raise ValueError("Transport part changed during verification")
                checksum.update(chunk)
                whole.update(chunk)
                if sink is not None:
                    sink.write(chunk)
        if (
            identity(source) != before
            or count != part["size"]
            or checksum.hexdigest() != part["sha256"]
        ):
            raise ValueError("Transport part changed or checksum mismatch")
    if identity(path) != before_manifest or whole.hexdigest() != manifest["archive"]["sha256"]:
        raise ValueError("Transport whole archive checksum mismatch")
    return manifest


def join(manifest: Path, output: Path, *, expected_sha256: str) -> Path:
    if not valid_digest(expected_sha256):
        raise ValueError("Trusted original SHA256 is required to join")
    output = safe_path(output, existing=False)
    if output.exists():
        raise FileExistsError("Reconstructed archive output already exists")
    # Link the verified same-directory temporary file atomically, never replace a target.
    descriptor, name = tempfile.mkstemp(prefix=".document-files-join-", dir=output.parent)
    temporary_path = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            verify(manifest, expected_sha256=expected_sha256, sink=temporary)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(temporary_path, output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    splitter = commands.add_parser("split")
    splitter.add_argument("archive", type=Path)
    splitter.add_argument(
        "--output", type=Path, required=True, help="New directory under an existing parent"
    )
    splitter.add_argument("--part-size", type=int, default=DEFAULT_PART_SIZE)
    joiner = commands.add_parser("join")
    joiner.add_argument("manifest", type=Path)
    joiner.add_argument("--output", type=Path, required=True)
    joiner.add_argument(
        "--sha256", required=True, help="Independently trusted original archive SHA256"
    )
    args = parser.parse_args()
    try:
        result = (
            split(args.archive, args.output, part_size=args.part_size)
            if args.operation == "split"
            else join(args.manifest, args.output, expected_sha256=args.sha256)
        )
        print(result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Release transport failed: {exc}") from exc


if __name__ == "__main__":
    main()
