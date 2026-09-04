#!/usr/bin/env python3
"""Install the pinned rhwp release into the Document Files user cache."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.8.6"
RELEASE_BASE = f"https://github.com/edwardkim/rhwp/releases/download/v{VERSION}"
ASSETS = {
    "macos-aarch64": (
        f"rhwp-v{VERSION}-macos-aarch64.tar.gz",
        "7d8928faeb03f00c35c8028f0b562f08f3da22bef86f750e0d7e0cd19344eaa3",
    ),
    "macos-x86_64": (
        f"rhwp-v{VERSION}-macos-x86_64.tar.gz",
        "a9581617e7ccab75481b9d36069d28c46e30a543a1fb0aa9ff581567a25321b8",
    ),
    "linux-x86_64": (
        f"rhwp-v{VERSION}-linux-x86_64.tar.gz",
        "458de22a6b9b86088dfdcd59552d4e8fab5362a64578c64761fa28df9be45e9a",
    ),
    "linux-aarch64": (
        f"rhwp-v{VERSION}-linux-aarch64.tar.gz",
        "1288aa5609a67574a6b1372397bef9a8941fda9ad16b1224342512f915ae016e",
    ),
    "windows-x86_64": (
        f"rhwp-v{VERSION}-windows-x86_64.zip",
        "867e0a84b778ebda92b88433ede301818eaea21e7d58eb77ff9a732f41d170d9",
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
    if system == "linux" and machine in {"arm64", "aarch64"}:
        return "linux-aarch64"
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


def verify_executable(path: Path, key: str) -> None:
    """Verify the extracted binary before it becomes an installed backend."""

    try:
        completed = subprocess.run(
            [str(path), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit("rhwp executable verification failed") from exc
    if completed.returncode != 0 or completed.stdout.strip() != f"rhwp v{VERSION}":
        raise SystemExit("rhwp executable version does not match the pinned release")
    if key.startswith("macos-"):
        verified = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if verified.returncode != 0:
            raise SystemExit("rhwp macOS code signature verification failed")


def main() -> None:
    key = platform_key()
    asset, expected_sha256 = ASSETS[key]
    executable_name = "rhwp.exe" if key == "windows-x86_64" else "rhwp"
    destination_dir = cache_root() / "rhwp" / f"v{VERSION}" / key / "bin"
    destination = destination_dir / executable_name
    license_destination = destination_dir.parent / "LICENSE"
    if destination.is_file() and license_destination.is_file():
        verify_executable(destination, key)
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
        verify_executable(staged_executable, key)
        destination_dir.mkdir(parents=True, exist_ok=True)
        os.replace(staged_executable, destination)
        os.replace(staged_license, license_destination)
        destination.chmod(0o755)
    print(destination)


if __name__ == "__main__":
    main()
