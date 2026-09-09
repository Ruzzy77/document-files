#!/usr/bin/env python3
"""Bookworm ABI/startup evidence, not model, kernel or final-image qualification.

Only explicit prepared local files are inspected. No installation or downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

LIMITS = {"GLIBC": "2.36", "GLIBCXX": "3.4.30", "CXXABI": "1.3.13"}
CC, CXX = "/usr/bin/gcc-12", "/usr/bin/g++-12"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def requirements(raw):
    """Read VERNEED, never symbol definitions exported by a bundled library."""
    active = False
    found = set()
    for line in raw.splitlines():
        if line.startswith("Version "):
            active = line.startswith("Version needs section")
        if active:
            match = re.search(r"\bName: ((?:GLIBC|GLIBCXX|CXXABI)_[^\s]+)", line)
            if match:
                found.add(match[1])
    return sorted(found)


def check_versions(values):
    for value in values:
        match = re.fullmatch(r"(GLIBC|GLIBCXX|CXXABI)_(\d+(?:\.\d+)+)", value)
        if not match:
            raise ValueError(f"Unrecognized Linux ABI requirement: {value}")
        family, version = match.groups()
        if tuple(map(int, version.split("."))) > tuple(map(int, LIMITS[family].split("."))):
            raise ValueError(f"Bookworm ABI exceeded: {value} > {family}_{LIMITS[family]}")


TARGETS = {
    "linux-x86_64": {"machine": 62, "loader": "/lib64/ld-linux-x86-64.so.2"},
    "linux-aarch64": {"machine": 183, "loader": "/lib/ld-linux-aarch64.so.1"},
}


def inspect_header(binary, target="linux-x86_64"):
    """Read ELF64 LE geometry and PT_INTERP without executing the input."""
    if target not in TARGETS:
        raise ValueError("Unsupported Linux target")
    binary = Path(binary)
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("Expected regular ELF input")
    size = binary.stat().st_size
    with binary.open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
            raise ValueError("Expected ELF64 little-endian version 1")
        kind, machine, version = struct.unpack_from("<HHI", header, 16)
        offset = struct.unpack_from("<Q", header, 32)[0]
        ehsize, entsize, count = struct.unpack_from("<HHH", header, 52)
        if kind not in (2, 3) or machine != TARGETS[target]["machine"] or version != 1:
            raise ValueError("ELF target mismatch")
        if ehsize != 64 or count > 4096 or (count and (entsize != 56 or offset < 64)):
            raise ValueError("Invalid ELF program header")
        if offset + count * entsize > size:
            raise ValueError("Truncated ELF program headers")
        interpreter = None
        for index in range(count):
            stream.seek(offset + index * entsize)
            entry = stream.read(56)
            if struct.unpack_from("<I", entry)[0] != 3:
                continue
            start, length = (
                struct.unpack_from("<Q", entry, 8)[0],
                struct.unpack_from("<Q", entry, 32)[0],
            )
            if interpreter is not None or not 2 <= length <= 256 or start + length > size:
                raise ValueError("Invalid ELF interpreter range")
            stream.seek(start)
            raw = stream.read(length)
            if not raw.endswith(b"\0") or b"\0" in raw[:-1]:
                raise ValueError("Invalid ELF interpreter")
            interpreter = raw[:-1].decode("ascii")
            if interpreter != TARGETS[target]["loader"]:
                raise ValueError("Foreign Linux interpreter")
    return {"target": target, "machine": machine, "elfClass": 64, "interpreter": interpreter}


def require_host(target):
    machines = {
        "x86_64": "linux-x86_64",
        "amd64": "linux-x86_64",
        "aarch64": "linux-aarch64",
        "arm64": "linux-aarch64",
    }
    if platform.system() != "Linux" or machines.get(platform.machine().lower()) != target:
        raise ValueError("Linux startup requires matching native host target")


def audit(binary, evidence, target="linux-x86_64"):
    binary, evidence = Path(binary), Path(evidence)
    if binary.is_symlink() or not binary.is_file():
        raise ValueError("ABI input must be a regular binary")
    with binary.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError("ABI input is not ELF")
    before = sha(binary)
    header = inspect_header(binary, target)
    result = subprocess.run(
        ["readelf", "--version-info", "--wide", str(binary)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={**os.environ, "LC_ALL": "C"},
    )
    evidence.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError("readelf failed; raw evidence retained")
    if not any(marker in result.stdout for marker in ("Version ", "No version information found")):
        raise ValueError("Unrecognized readelf output")
    if sha(binary) != before:
        raise ValueError("Binary changed during ABI inspection")
    values = requirements(result.stdout)
    check_versions(values)
    return {
        **header,
        "sha256": before,
        "requirements": values,
        "limits": LIMITS,
        "rawEvidence": {"path": evidence.name, "sha256": sha(evidence)},
    }


def toolchain():
    """Require the actual GCC 12 executables selected by our Linux CMake recipes."""
    result = {}
    for name, executable in (("c", CC), ("cxx", CXX)):
        version = subprocess.check_output(
            [executable, "-dumpfullversion"], text=True, timeout=30
        ).strip()
        if not re.fullmatch(r"12\.\d+(?:\.\d+)?", version):
            raise ValueError("Linux baseline requires actual GCC 12")
        result[name] = {
            "path": executable,
            "resolvedPath": str(Path(executable).resolve()),
            "sha256": sha(executable),
            "version": version,
        }
    return {
        "compilers": result,
        "libc": platform.libc_ver(),
        "kernel": platform.release(),
        "runnerImage": {
            "os": os.environ.get("ImageOS"),  # noqa: SIM112
            "version": os.environ.get("ImageVersion"),  # noqa: SIM112
        },
        "kernelBaselineTested": False,
    }


def unpack(archive, output):
    """Bounded local ZIP extraction for relocated checks; no links or overwrites."""
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 100000 or sum(i.file_size for i in entries) > 20 * 1024**3:
            raise ValueError("ZIP inspection resource limit")
        seen = set()
        for entry in entries:
            # ZipInfo normalizes backslashes on Windows and truncates NULs.
            # Inspect the original central-directory name before using its view.
            original = entry.orig_filename
            name = PurePosixPath(original)
            mode = entry.external_attr >> 16
            if (
                not original
                or original != entry.filename
                or "\\" in original
                or "\x00" in original
                or name.is_absolute()
                or ".." in name.parts
                or ":" in original
                or str(name) in seen
                or (mode & 0o170000) not in (0, 0o100000, 0o040000)
                or entry.file_size > 5 * 1024**3
            ):
                raise ValueError("Unsafe ZIP member")
            seen.add(str(name))
            destination = output / name
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            destination.chmod(mode & 0o777 or 0o644)


def startup(root, kind, output):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(output),
        "LC_ALL": "C.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    if kind == "cpu":
        commands = [
            ([root / "llama-server", "--version"], 0, "9dcf84e"),
            ([root / "llama-quantize", "--help"], 1, "--allow-requantize"),
        ]
    elif kind == "native":
        commands = [
            ([root / "bin/tesseract", "--version"], 0, "tesseract 5.5.3"),
            (
                [root / "bin/tesseract", "--tessdata-dir", root / "tessdata", "--list-langs"],
                0,
                "kor",
            ),
        ]
    else:
        root = root / "document-files"
        commands = [
            (
                [
                    root / "python/bin/python3",
                    "-I",
                    root / "launchers/run.py",
                    "cli",
                    "capabilities",
                ],
                0,
                '"ok": true',
            ),
            ([root / "rhwp/rhwp", "--version"], 0, "rhwp v0.8.6+pat.checkbox.1"),
        ]
    records = []
    for index, (command, expected_exit, marker) in enumerate(commands):
        result = subprocess.run(
            [str(x) for x in command],
            env=env,
            cwd=output,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        log = output / f"startup-{index}.txt"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        records.append(
            {
                "executableSha256": sha(command[0]),
                "exitCode": result.returncode,
                "log": {"path": log.name, "sha256": sha(log)},
            }
        )
        if result.returncode != expected_exit or marker not in result.stdout + result.stderr:
            raise ValueError("Relocated startup failed; raw evidence retained")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--tree", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=tuple(TARGETS), default="linux-x86_64")
    parser.add_argument("--startup", choices=("cpu", "native", "core"))
    parser.add_argument(
        "--startup-only", action="store_true", help="Consumer image needs no readelf"
    )
    args = parser.parse_args()
    if args.startup_only and not args.startup:
        parser.error("--startup-only requires --startup")
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output.resolve()
    receipt = {
        "schemaVersion": "document-files.linux-compatibility.v1",
        "passed": False,
        "target": args.target,
        "limits": LIMITS,
        "files": [],
        "finalImageQualified": False,
        "kernelBaselineTested": False,
        "libc": platform.libc_ver(),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="Document Files relocated Linux ") as temp:
            if args.startup:
                require_host(args.target)
            if args.startup_only:
                release = Path("/etc/os-release").read_text()
                if not re.search(r"^ID=debian$", release, re.M) or not re.search(
                    r"^VERSION_CODENAME=bookworm$", release, re.M
                ):
                    raise ValueError("Consumer startup requires actual Debian bookworm")
                if platform.libc_ver() != ("glibc", "2.36"):
                    raise ValueError("Consumer libc is not bookworm glibc 2.36")
                receipt["osReleaseSha256"] = sha(Path("/etc/os-release"))
            if args.archive:
                receipt["archiveSha256"] = sha(args.archive)
                unpack(args.archive, Path(temp))
                if sha(args.archive) != receipt["archiveSha256"]:
                    raise ValueError("Archive changed during inspection")
                root = Path(temp)
            else:
                if args.tree.is_symlink() or not args.tree.is_dir():
                    raise ValueError("Expected local regular tree")
                root = args.tree.resolve()
                if output.is_relative_to(root):
                    raise ValueError("Evidence output must be outside the source tree")
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Symlink in inspection tree")
                if path.is_file():
                    with path.open("rb") as stream:
                        elf = stream.read(4) == b"\x7fELF"
                    if elf:
                        entry = {"path": path.relative_to(root).as_posix(), "sha256": sha(path)}
                        inspect_header(path, args.target)
                        if not args.startup_only:
                            entry.update(
                                audit(
                                    path,
                                    output / f"elf-{len(receipt['files'])}.txt",
                                    target=args.target,
                                )
                            )
                        receipt["files"].append(entry)
            if not receipt["files"]:
                raise ValueError("No ELF inputs")
            if args.startup:
                receipt["startup"] = startup(root, args.startup, output)
            receipt["passed"] = True
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        receipt["error"] = str(exc)
    finally:
        (output / "compatibility.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
