#!/usr/bin/env python3
"""Build pinned native Tesseract candidates on Linux x64 / Windows x64 only.

Requires an existing CMake + native compiler toolchain (MSVC developer shell on
Windows). Never installs packages. --download permits only hash-pinned inputs;
otherwise --cache must already contain files named by SHA256. Uses a fresh work
folder, isolated install prefix, static image libraries and a relocated binary.
--with-tessdata checks pinned eng/kor/osd discovery, not OCR or model quality.

Output is NOT a recognition pack: Python/wheels, Docling models, OCR quality,
platform deployment, compiler-runtime redistribution and release qualification
remain separate gates. Build-host toolchain versions are recorded, not pinned
by this helper. Linux glibc floor is measured, not asserted as an older baseline.
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
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

PINS = Path(__file__).with_name("recognition-native-sources.json")
ORDER = ["zlib", "png", "jpeg", "tiff", "leptonica", "tesseract"]
LINUX_SYSTEM = {
    "libc.so.6",
    "libm.so.6",
    "libdl.so.2",
    "libpthread.so.0",
    "librt.so.1",
    "ld-linux-x86-64.so.2",
}
WINDOWS_SYSTEM = {
    "kernel32.dll",
    "user32.dll",
    "gdi32.dll",
    "advapi32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "ws2_32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "ntdll.dll",
    "ucrtbase.dll",
}


class BuildError(ValueError):
    pass


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value == "."
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise BuildError("unsafe relative path")
    return value


def load_pins(path: Path) -> dict:
    pins = json.loads(path.read_text(encoding="utf-8"))
    if (
        pins.get("schemaVersion") != "document-files.recognition-native-sources.v1"
        or [s["id"] for s in pins["sources"]] != ORDER
    ):
        raise BuildError("invalid native source pins")
    for source in [*pins["sources"], *pins["tessdata"]]:
        if (
            not re.fullmatch("[a-f0-9]{64}", source["sha256"])
            or not source["url"].startswith("https://")
            or not source["license"]
        ):
            raise BuildError("missing upstream hash, HTTPS URL or license")
        for notice in source.get("notices", []):
            relative(notice["path"])
            if not re.fullmatch("[a-f0-9]{64}", notice["sha256"]):
                raise BuildError("missing notice hash")
        if "id" in source and not source.get("notices"):
            raise BuildError("missing source notices")
        if "path" in source:
            relative(source["path"])
    return pins


def acquire(source: dict, cache: Path, download: bool) -> Path:
    destination = cache / source["sha256"]
    if destination.is_symlink():
        raise BuildError("symlink in source cache")
    if not destination.exists():
        if not download:
            raise BuildError(f"missing pinned input: {source['sha256']}")
        cache.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(".partial")
        request = urllib.request.Request(
            source["url"], headers={"User-Agent": "Document-Files-native-builder"}
        )
        created = False
        try:
            with partial.open("xb") as out:
                created = True
                with urllib.request.urlopen(request, timeout=60) as response:
                    if not response.url.startswith("https://"):
                        raise BuildError("non-HTTPS redirect")
                    count = 0
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > 64 * 1024 * 1024:
                            raise BuildError("source download size limit")
                        out.write(chunk)
            if sha(partial) != source["sha256"]:
                raise BuildError("download hash mismatch")
            partial.rename(destination)
        finally:
            if created:
                partial.unlink(missing_ok=True)
    if not destination.is_file() or sha(destination) != source["sha256"]:
        raise BuildError("source hash mismatch")
    return destination


def unpack(archive: Path, destination: Path) -> Path:
    """Extract regular files only, never archive links or device nodes."""
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        if len(members) > 20000 or sum(m.size for m in members) > 256 * 1024 * 1024:
            raise BuildError("source archive size limit")
        roots = set()
        seen = set()
        for member in members:
            name = relative(member.name.rstrip("/"))
            if name in seen or not (member.isfile() or member.isdir()):
                raise BuildError("duplicate or nonregular archive member")
            seen.add(name)
            roots.add(PurePosixPath(name).parts[0])
        if len(roots) != 1:
            raise BuildError("source archive must have one root")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, target.open("xb") as out:
                    shutil.copyfileobj(source, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return destination / roots.pop()


def current_target() -> str:
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        raise BuildError("native x64 host required; Intel Mac recognition uses Linux")
    return {"Linux": "linux-x86_64", "Windows": "windows-x86_64"}.get(platform.system(), "")


def options(component: str, prefix: Path, target: str) -> list[str]:
    windows = target == "windows-x86_64"
    lib = prefix / "lib"

    def dependency(name, filename, include_name=None):
        return [
            f"-D{name}_LIBRARY={(lib / filename).as_posix()}",
            f"-D{include_name or name + '_INCLUDE_DIR'}={(prefix / 'include').as_posix()}",
        ]

    zlib = dependency("ZLIB", "zs.lib" if windows else "libz.a")
    jpeg = dependency("JPEG", "jpeg-static.lib" if windows else "libjpeg.a")
    png = dependency(
        "PNG", "libpng16_static.lib" if windows else "libpng16.a", "PNG_PNG_INCLUDE_DIR"
    )
    tiff = dependency("TIFF", "tiff.lib" if windows else "libtiff.a")
    choices = {
        "zlib": ["-DZLIB_BUILD_SHARED=OFF", "-DZLIB_BUILD_STATIC=ON", "-DZLIB_BUILD_TESTING=OFF"],
        "png": ["-DPNG_SHARED=OFF", "-DPNG_STATIC=ON", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF", *zlib],
        "jpeg": [
            "-DENABLE_SHARED=OFF",
            "-DENABLE_STATIC=ON",
            "-DWITH_TURBOJPEG=OFF",
            "-DWITH_SIMD=OFF",
            "-DWITH_CRT_DLL=OFF",
        ],
        "tiff": [
            "-Dtiff-tools=OFF",
            "-Dtiff-tests=OFF",
            "-Dtiff-contrib=OFF",
            "-Dtiff-docs=OFF",
            "-Dtiff-cxx=OFF",
            "-Dzlib=ON",
            "-Djpeg=ON",
            "-Djpeg-prefer-standard=ON",
            "-Dlibdeflate=OFF",
            "-Dold-jpeg=OFF",
            "-Djbig=OFF",
            "-Dlerc=OFF",
            "-Dlzma=OFF",
            "-Dwebp=OFF",
            "-Dzstd=OFF",
            *zlib,
            *jpeg,
        ],
        "leptonica": [
            "-DSW_BUILD=OFF",
            "-DBUILD_PROG=OFF",
            "-DSTRICT_CONF=ON",
            "-DENABLE_ZLIB=ON",
            "-DENABLE_PNG=ON",
            "-DENABLE_JPEG=ON",
            "-DENABLE_TIFF=ON",
            "-DENABLE_GIF=OFF",
            "-DENABLE_WEBP=OFF",
            "-DENABLE_OPENJPEG=OFF",
            *zlib,
            *jpeg,
            *png,
            *tiff,
        ],
        "tesseract": [
            "-DBUILD_TRAINING_TOOLS=OFF",
            "-DBUILD_TESTS=OFF",
            "-DDISABLE_TIFF=ON",
            "-DDISABLE_ARCHIVE=ON",
            "-DDISABLE_CURL=ON",
            "-DGRAPHICS_DISABLED=ON",
            "-DOPENMP_BUILD=OFF",
            "-DENABLE_NATIVE=OFF",
            "-DWIN32_MT_BUILD=ON",
            f"-DLeptonica_DIR={(prefix / 'lib/cmake/leptonica').as_posix()}",
        ],
    }
    return choices[component]


def rewrite_export(prefix: Path, target: str) -> None:
    """Resolve two imported aliases in generated export for Tesseract try_run.

    No upstream source changes: Leptonica's private imported ZLIB/JPEG aliases
    are not defined in CMake's nested try_compile project. Bind only our static
    libraries, fail rather than fall back to a host library.
    """
    export = prefix / "lib/cmake/leptonica/LeptonicaTargets.cmake"
    value = export.read_text(encoding="utf-8")
    windows = target == "windows-x86_64"
    for name, filename in [
        ("ZLIB::ZLIB", "zs.lib" if windows else "libz.a"),
        ("JPEG::JPEG", "jpeg-static.lib" if windows else "libjpeg.a"),
    ]:
        library = prefix / "lib" / filename
        if not library.is_file():
            raise BuildError(f"missing private static dependency: {filename}")
        value = value.replace(name, library.as_posix())
    export.write_text(value, encoding="utf-8")


def collect_runtime_notices(target, notices, run, windows_license=None, compiler="c++"):
    """Preserve real installed toolchain notices; never substitute a made-up text.

    Windows follows the CPU runtime builder's installed Visual Studio License.rtf
    route. Linux binds the GCC-major package copyright plus full GPL-3 text and
    the exact static runtime archives selected by the same compiler. This is a
    collection/consistency check, not an independent redistribution approval.
    """
    records = []
    runtimes = []

    def copy_notice(original, filename, required):
        if not original.is_file() or original.is_symlink():
            raise BuildError(f"installed compiler notice missing: {original}")
        content = original.read_bytes()
        encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        text = content.decode(encoding, errors="replace")
        plain = " ".join(re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]", " ", text).split())
        if any(not re.search(pattern, plain, re.IGNORECASE) for pattern in required):
            raise BuildError(f"installed compiler notice lacks required terms: {original}")
        destination = notices / filename
        shutil.copyfile(original, destination)
        records.append(
            {
                "sourcePath": str(original),
                "path": destination.name,
                "sha256": sha(destination),
                "size": destination.stat().st_size,
            }
        )

    if target == "windows-x86_64":
        if windows_license is None:
            raise BuildError("actual installed Visual Studio license is required")
        from windows_runtime_notice import validate_notice

        try:
            validate_notice(windows_license)
        except ValueError as exc:
            raise BuildError(str(exc)) from None
        copy_notice(
            windows_license,
            "microsoft-visual-cpp-runtime.rtf",
            [r"MICROSOFT.{0,250}VISUAL\s+STUDIO", r"distributable.{0,30}code|redistribut"],
        )
        root = os.environ.get("VCTOOLSINSTALLDIR")
        if not root:
            raise BuildError("installed Visual Studio compiler path is required")
        for filename in ("libcmt.lib", "libcpmt.lib", "libvcruntime.lib"):
            library = Path(root) / "lib/x64" / filename
            if not library.is_file() or library.is_symlink():
                raise BuildError(f"installed static compiler runtime missing: {filename}")
            runtimes.append({"path": str(library), "sha256": sha(library)})
    else:
        version = run([compiler, "-dumpfullversion"], "gcc-full-version").strip()
        if not re.fullmatch(r"\d+(?:\.\d+)+", version):
            raise BuildError("GCC version unavailable for installed notice selection")
        copyright = Path(f"/usr/share/doc/gcc-{version.split('.')[0]}-base/copyright")
        copy_notice(
            copyright,
            "gcc-runtime-copyright.txt",
            [r"GCC RUNTIME LIBRARY EXCEPTION", r"Version 3\.1", r"GNU GENERAL PUBLIC LICENSE"],
        )
        copy_notice(
            Path("/usr/share/common-licenses/GPL-3"),
            "gcc-GPL-3.txt",
            [r"GNU GENERAL PUBLIC LICENSE", r"Version 3"],
        )
        for name in ("libstdc++.a", "libgcc.a", "libgcc_eh.a"):
            library = Path(
                run([compiler, f"-print-file-name={name}"], f"gcc-{name}-location").strip()
            )
            if not library.is_absolute() or not library.is_file():
                raise BuildError(f"installed static compiler runtime missing: {name}")
            runtimes.append(
                {
                    "path": str(library),
                    "resolvedPath": str(library.resolve()),
                    "sha256": sha(library),
                }
            )
    return {
        "collected": True,
        "independentRedistributionReview": "pending",
        "notices": records,
        "staticRuntimes": runtimes,
    }


def write_attributions(pins, candidate):
    """Ship explicit acknowledgments alongside the unmodified license texts."""
    lines = [
        "Document Files native recognition dependencies",
        "",
        "This software is based in part on the work of the Independent JPEG Group.",
        "This product includes software developed by the University of California, "
        "Berkeley and its contributors.",
        "",
        "Full upstream license and compiler runtime notices are retained in licenses/.",
        "This notice is not an independent redistribution approval.",
    ]
    for source in pins["sources"]:
        lines += ["", f"{source['id']} {source['version']}: {source['license']}"]
        lines += [
            f"  licenses/{source['id']}-{Path(item['path']).name}" for item in source["notices"]
        ]
    (candidate / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_binary(path: Path, target: str) -> None:
    head = path.read_bytes()[:4096]
    if target == "linux-x86_64":
        valid = (
            len(head) >= 20
            and head[:6] == b"\x7fELF\x02\x01"
            and struct.unpack_from("<H", head, 18)[0] == 62
        )
    else:
        offset = struct.unpack_from("<I", head, 60)[0] if len(head) >= 64 else 0
        valid = head[:2] == b"MZ" and head[offset : offset + 6] == b"PE\0\0\x64\x86"
    if not valid:
        raise BuildError("native binary target mismatch")


def parse_linkage(raw: str, target: str) -> list[str]:
    if target == "linux-x86_64":
        deps = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", raw)
        allowed = LINUX_SYSTEM
        if re.search(r"\((?:RPATH|RUNPATH)\)", raw):
            raise BuildError("unexpected dynamic search path in static-library candidate")
    else:
        deps = re.findall(r"^\s+([\w.-]+\.dll)\s*$", raw, re.MULTILINE | re.IGNORECASE)
        allowed = WINDOWS_SYSTEM
    if not deps:
        raise BuildError("missing dynamic linkage evidence")
    for name in deps:
        candidate = name.lower() if target == "windows-x86_64" else name
        if candidate not in allowed and not (
            target == "windows-x86_64" and candidate.startswith("api-ms-win-")
        ):
            raise BuildError(f"non-system dynamic dependency: {name}")
    return sorted(set(deps))


def run_build(args) -> dict:
    target = current_target()
    if not target or target != args.target:
        raise BuildError("must execute on requested native Linux/Windows x64 host")
    pins = load_pins(args.pins)
    repository = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=normal"], cwd=repository, text=True
        ).strip()
    )
    args.work.mkdir(parents=True, exist_ok=False)
    work = args.work.resolve()
    prefix = work / "prefix"
    logs = work / "logs"
    logs.mkdir()
    env = os.environ.copy()
    for key in (
        "CC",
        "CXX",
        "CFLAGS",
        "CXXFLAGS",
        "LDFLAGS",
        "CPATH",
        "LIBRARY_PATH",
        "CMAKE_PREFIX_PATH",
        "PKG_CONFIG_PATH",
        "LD_LIBRARY_PATH",
        "TESSDATA_PREFIX",
    ):
        env.pop(key, None)
    env["PKG_CONFIG_LIBDIR"] = str(prefix / "lib/pkgconfig")
    commands = []

    def run(command, label, runtime=False):
        execution_env = env.copy()
        if runtime:
            execution_env["PATH"] = (
                str(Path(os.environ["SYSTEMROOT"]) / "System32")
                if target == "windows-x86_64"
                else "/usr/bin:/bin"
            )
        result = subprocess.run(
            [str(c) for c in command],
            cwd=work,
            env=execution_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        log = logs / f"{label}.txt"
        log.write_text(result.stdout, encoding="utf-8")
        commands.append(
            {
                "argv": [str(c) for c in command],
                "exitCode": result.returncode,
                "log": str(log.relative_to(work)),
                "sha256": sha(log),
            }
        )
        (work / "commands.json").write_text(json.dumps(commands, indent=2), encoding="utf-8")
        if result.returncode:
            raise BuildError(f"{label} failed; see {log}")
        return result.stdout

    toolchain = {"cmake": run(["cmake", "--version"], "cmake-version")}
    if target.startswith("linux"):
        from linux_abi import CC, CXX
        from linux_abi import toolchain as linux_toolchain

        toolchain["linux"] = linux_toolchain()
        toolchain["compiler"] = run([CXX, "--version"], "compiler-version")
    else:
        toolchain["compiler"] = run(["cmd", "/c", "cl 2>&1 & exit /b 0"], "compiler-version")
    notices = work / "candidate/licenses"
    notices.mkdir(parents=True)
    runtime_notices = collect_runtime_notices(
        target,
        notices,
        run,
        getattr(args, "windows_runtime_license", None),
        *([CXX] if target.startswith("linux") else []),
    )
    (work / "runtime-notices.json").write_text(
        json.dumps(runtime_notices, indent=2) + "\n", encoding="utf-8"
    )
    for source in pins["sources"]:
        archive = acquire(source, args.cache.resolve(), args.download)
        source_root = unpack(archive, work / "sources" / source["id"])
        for notice in source["notices"]:
            original = source_root / notice["path"]
            if sha(original) != notice["sha256"]:
                raise BuildError("source notice hash mismatch")
            shutil.copyfile(original, notices / f"{source['id']}-{Path(notice['path']).name}")
        directory = work / "build" / source["id"]
        flags = [
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCMAKE_INSTALL_PREFIX={prefix.as_posix()}",
            f"-DCMAKE_PREFIX_PATH={prefix.as_posix()}",
            "-DCMAKE_INSTALL_LIBDIR=lib",
            "-DBUILD_SHARED_LIBS=OFF",
            "-DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF",
            "-DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY=OFF",
            "-DCMAKE_SKIP_RPATH=ON",
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
            "-DFETCHCONTENT_FULLY_DISCONNECTED=ON",
        ]
        if target.startswith("windows"):
            flags += [
                "-G",
                "Visual Studio 17 2022",
                "-A",
                "x64",
                "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
                "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
            ]
        else:
            flags += [
                f"-DCMAKE_C_COMPILER={CC}",
                f"-DCMAKE_CXX_COMPILER={CXX}",
                "-DCMAKE_EXE_LINKER_FLAGS=-static-libstdc++ -static-libgcc",
            ]
        run(
            [
                "cmake",
                "-S",
                source_root,
                "-B",
                directory,
                *flags,
                *options(source["id"], prefix, target),
            ],
            f"{source['id']}-configure",
        )
        run(
            ["cmake", "--build", directory, "--config", "Release", "--parallel", args.jobs],
            f"{source['id']}-build",
        )
        run(["cmake", "--install", directory, "--config", "Release"], f"{source['id']}-install")
        if source["id"] == "leptonica":
            rewrite_export(prefix, target)
    write_attributions(pins, work / "candidate")
    binary_name = "tesseract.exe" if target.startswith("windows") else "tesseract"
    relocated = work / "candidate/bin" / binary_name
    relocated.parent.mkdir()
    shutil.copy2(prefix / "bin" / binary_name, relocated)
    check_binary(relocated, target)
    command = (
        ["dumpbin", "/dependents", relocated]
        if target.startswith("windows")
        else ["readelf", "-d", relocated]
    )
    raw = run(command, "native-dependencies")
    dependencies = parse_linkage(raw, target)
    linux_abi = None
    if target.startswith("linux"):
        from linux_abi import audit

        linux_abi = audit(relocated, logs / "glibc-versions.txt")
        linux_abi["rawEvidence"]["path"] = "logs/glibc-versions.txt"
    # Hide the entire install prefix: relocation must not silently use build products.
    prefix.rename(work / "prefix-not-on-runtime-path")
    version = run([relocated, "--version"], "relocated-version", runtime=True)
    if f"tesseract {pins['sources'][-1]['version']}" not in version:
        raise BuildError("unexpected Tesseract version")
    for component in pins["sources"]:
        if component["id"] != "tesseract" and component["version"] not in version:
            raise BuildError(f"missing compiled codec version: {component['id']}")
    tessdata = work / "candidate/tessdata"
    tessdata.mkdir()
    expected_languages = []
    if args.with_tessdata:
        for item in pins["tessdata"]:
            shutil.copyfile(
                acquire(item, args.cache.resolve(), args.download), tessdata / item["path"]
            )
            if item["path"].endswith(".traineddata"):
                expected_languages.append(item["path"].removesuffix(".traineddata"))
    languages = run(
        [relocated, "--tessdata-dir", tessdata, "--list-langs"], "relocated-languages", runtime=True
    )
    actual_languages = [
        line.strip() for line in languages.splitlines() if line.strip() in {"eng", "kor", "osd"}
    ]
    if sorted(actual_languages) != sorted(expected_languages):
        raise BuildError("language discovery mismatch")
    receipt = {
        "schemaVersion": "document-files.recognition-native-candidate.v1",
        "target": target,
        "sourcePinsSha256": sha(args.pins),
        "sourceCommit": commit,
        "dirtySource": dirty,
        "builderSha256": sha(Path(__file__)),
        # GitHub runner variables are intentionally mixed case on Linux.
        "runnerImage": {  # noqa: SIM112
            "os": os.environ.get("ImageOS"),  # noqa: SIM112
            "version": os.environ.get("ImageVersion"),  # noqa: SIM112
        },
        "files": [
            {
                "path": file.relative_to(work).as_posix(),
                "sha256": sha(file),
                "size": file.stat().st_size,
            }
            for file in sorted((work / "candidate").rglob("*"))
            if file.is_file()
        ],
        "sources": pins["sources"],
        "toolchain": toolchain,
        "linuxAbi": linux_abi,
        "runtimeNotices": runtime_notices,
        "binary": {"path": relocated.relative_to(work).as_posix(), "sha256": sha(relocated)},
        "dependencies": dependencies,
        "nativeExecutionVerified": True,
        "languageDiscoveryVerified": bool(args.with_tessdata),
        "languages": actual_languages,
        "tessdata": pins["tessdata"] if args.with_tessdata else [],
        "fullRecognitionQualified": False,
        "releaseReady": False,
        "remaining": [
            "Independent compiler runtime redistribution review of collected notices",
            "Target baseline installation and complete recognition pack audit",
            "Real OCR, Docling and independent quality / memory qualification",
        ],
        "commands": commands,
    }
    (work / "native-candidate.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["linux-x86_64", "windows-x86_64"])
    parser.add_argument(
        "--work", required=True, type=Path, help="New, nonexistent output directory"
    )
    parser.add_argument(
        "--cache", required=True, type=Path, help="Input cache, files named by SHA256"
    )
    parser.add_argument("--pins", type=Path, default=PINS)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--windows-runtime-license",
        type=Path,
        help="Actual installed Visual Studio License.rtf with redistribution terms",
    )
    parser.add_argument(
        "--with-tessdata",
        action="store_true",
        help="Discover pinned eng/kor/osd; does not load OCR models",
    )
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 32:
        parser.error("--jobs must be between 1 and 32")
    try:
        run_build(args)
    except (BuildError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
