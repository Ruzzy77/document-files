#!/usr/bin/env python3
"""Install the pinned rhwp release into the Document Files user cache."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.8.2"
RELEASE_BASE = f"https://github.com/edwardkim/rhwp/releases/download/v{VERSION}"
ASSETS = {
    "macos-aarch64": (
        f"rhwp-v{VERSION}-macos-aarch64.tar.gz",
        "2833431bed6034a0af03f7d889f1a41603e61b4bda5e16c93d2fc58efee5b5ea",
    ),
    "macos-x86_64": (
        f"rhwp-v{VERSION}-macos-x86_64.tar.gz",
        "7f53cb75dc3ff2a8c3d3178caaa0d3bffb396e7a768d215a747254e79471cbbd",
    ),
    "linux-x86_64": (
        f"rhwp-v{VERSION}-linux-x86_64.tar.gz",
        "3225246533eca2b10ec2926228aee0d1cbf0ea6de0553e053ec8d6cb79fa9570",
    ),
    "windows-x86_64": (
        f"rhwp-v{VERSION}-windows-x86_64.zip",
        "d99b952ce2322d59530b86453a7314ebe18e86bdea165d2b75ef0b2af39ec6de",
    ),
}


def platform_key() -> str:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return "macos-aarch64"
    if system == "darwin" and machine in {"x86_64", "amd64"}:
        return "macos-x86_64"
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "windows" and machine in {"x86_64", "amd64"}:
        return "windows-x86_64"
    raise SystemExit(f"Unsupported rhwp platform: {platform.system()} {platform.machine()}")


def cache_root() -> Path:
    system = platform.system().casefold()
    if system == "darwin":
        return Path.home() / "Library" / "Caches" / "Document Files"
    if system == "windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Document Files" / "Cache"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "document-files"


def archive_member(archive: Path, member_name: str, destination: Path) -> None:
    if archive.suffix == ".zip":
        with (
            zipfile.ZipFile(archive) as package,
            package.open(member_name) as source,
            destination.open("wb") as target,
        ):
            shutil.copyfileobj(source, target)
    else:
        with tarfile.open(archive, mode="r:gz") as package:
            member = package.getmember(member_name)
            source = package.extractfile(member)
            if source is None or not member.isfile():
                raise SystemExit(f"Expected executable missing from archive: {member_name}")
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)


def main() -> None:
    key = platform_key()
    asset, expected_sha256 = ASSETS[key]
    executable_name = "rhwp.exe" if key == "windows-x86_64" else "rhwp"
    destination_dir = cache_root() / "rhwp" / f"v{VERSION}" / key / "bin"
    destination = destination_dir / executable_name
    license_destination = destination_dir.parent / "LICENSE"
    if destination.is_file() and license_destination.is_file():
        print(destination)
        return

    with tempfile.TemporaryDirectory(prefix="document-files-rhwp-") as folder:
        staging = Path(folder)
        archive = staging / asset
        urllib.request.urlretrieve(f"{RELEASE_BASE}/{asset}", archive)  # noqa: S310
        actual_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise SystemExit(
                f"rhwp checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
            )
        staged_executable = staging / executable_name
        staged_license = staging / "LICENSE"
        executable_member = "rhwp/rhwp.exe" if key == "windows-x86_64" else "rhwp/rhwp"
        archive_member(archive, executable_member, staged_executable)
        archive_member(archive, "rhwp/LICENSE", staged_license)
        staged_executable.chmod(
            staged_executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staged_executable, destination)
        os.replace(staged_license, license_destination)
        destination.chmod(0o755)
    print(destination)


if __name__ == "__main__":
    main()
