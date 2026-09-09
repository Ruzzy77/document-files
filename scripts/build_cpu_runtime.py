#!/usr/bin/env python3
"""Build a pinned, CPU-only llama.cpp pack on its matching release host.

Only the initial explicit source checkout uses the network. CMake dependency
fetching, HTTPS support, embedded UI downloads and GPU backends are disabled.
This is a build command, never a document-processing installation step. x64
packs require AVX2/FMA/F16C/BMI2; they are not generic pre-AVX x64 binaries.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

from build_runtime_pack import build_pack, sha256_file
from windows_runtime_notice import validate_notice

from document_files.runtime_packs import PackError, current_target

REVISION = "9dcf84e5ae2718947188b539aab8b9c2b15d3ba1"
SOURCE_URL = "https://github.com/ggml-org/llama.cpp.git"
TARGETS = {"macos-aarch64", "macos-x86_64", "windows-x86_64", "linux-x86_64", "linux-aarch64"}
DISABLED = (
    "BUILD_SHARED_LIBS",
    "GGML_BACKEND_DL",
    "GGML_CPU_ALL_VARIANTS",
    "GGML_NATIVE",
    "GGML_METAL",
    "GGML_CUDA",
    "GGML_HIP",
    "GGML_MUSA",
    "GGML_VULKAN",
    "GGML_WEBGPU",
    "GGML_SYCL",
    "GGML_OPENCL",
    "GGML_HEXAGON",
    "GGML_ZENDNN",
    "GGML_RPC",
    "GGML_CANN",
    "GGML_BLAS",
    "GGML_ACCELERATE",
    "GGML_OPENMP",
    "GGML_OPENMP_FETCH",
    "GGML_CPU_KLEIDIAI",
    "GGML_CPU_HBM",
    "GGML_CCACHE",
    "GGML_LLAMAFILE",
    "LLAMA_OPENSSL",
    "LLAMA_SUBPROCESS",
    "LLAMA_LLGUIDANCE",
    "LLAMA_USE_SYSTEM_GGML",
    "LLAMA_BUILD_TESTS",
    "LLAMA_BUILD_EXAMPLES",
    "LLAMA_BUILD_APP",
    "LLAMA_BUILD_UI",
    "LLAMA_USE_PREBUILT_UI",
    "GGML_AVX512",
    "GGML_AVX512_VBMI",
    "GGML_AVX512_VNNI",
    "GGML_AVX512_BF16",
    "GGML_AVX_VNNI",
    "GGML_AMX_TILE",
    "GGML_AMX_INT8",
    "GGML_AMX_BF16",
)
X64_FEATURES = ("SSE42", "AVX", "AVX2", "BMI2", "FMA", "F16C")
SYSTEM_DLLS = {
    "kernel32.dll",
    "ntdll.dll",
    "advapi32.dll",
    "ws2_32.dll",
    "mswsock.dll",
    "user32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "bcrypt.dll",
    "crypt32.dll",
    "secur32.dll",
    "normaliz.dll",
    "winmm.dll",
    "shlwapi.dll",
    "version.dll",
    "powrprof.dll",
    "setupapi.dll",
    "cfgmgr32.dll",
    "uuid.dll",
    "gdi32.dll",
    "comdlg32.dll",
    "comctl32.dll",
    "iphlpapi.dll",
}


def run(*args, cwd=None, env=None):
    return subprocess.check_output([str(a) for a in args], cwd=cwd, env=env, text=True)


def smoke_binary(binary, *, quantize=False):
    # Pinned llama-quantize has no --version and deliberately exits 1 on --help.
    option = "--help" if quantize else "--version"
    process = subprocess.run(
        [str(binary), option],
        capture_output=True,
        text=True,
        timeout=60,
    )
    text = process.stdout + process.stderr
    if quantize:
        valid = process.returncode == 1 and "usage:" in text and "--allow-requantize" in text
    else:
        valid = process.returncode == 0 and REVISION[:7] in text
    if not valid:
        raise PackError("cpu_build_binary_identity_or_smoke_failed")
    return {"argument": option, "exitCode": process.returncode, "output": text}


def cmake_options(target):
    if target not in TARGETS:
        raise PackError("unsupported_cpu_target")
    options = {key: "OFF" for key in DISABLED}
    options.update(
        {
            "CMAKE_BUILD_TYPE": "Release",
            "GGML_CPU": "ON",
            "LLAMA_BUILD_COMMON": "ON",
            "LLAMA_BUILD_TOOLS": "ON",
            "LLAMA_BUILD_SERVER": "ON",
            "FETCHCONTENT_FULLY_DISCONNECTED": "ON",
            "FETCHCONTENT_UPDATES_DISCONNECTED": "ON",
            "LLAMA_BUILD_NUMBER": "10853",
            "LLAMA_BUILD_COMMIT": REVISION[:7],
        }
    )
    for feature in X64_FEATURES:
        options[f"GGML_{feature}"] = "ON" if target.endswith("x86_64") else "OFF"
    if target.startswith("macos"):
        options["CMAKE_OSX_ARCHITECTURES"] = "arm64" if target.endswith("aarch64") else "x86_64"
        options["CMAKE_OSX_DEPLOYMENT_TARGET"] = "13.3"
    if target.endswith("aarch64"):
        options["GGML_CPU_ARM_ARCH"] = "armv8.2-a+fp16+dotprod"
    if target.startswith("linux"):
        from linux_abi import CC, CXX

        options.update(CMAKE_C_COMPILER=CC, CMAKE_CXX_COMPILER=CXX)
    if target.startswith("windows"):
        options["CMAKE_MSVC_RUNTIME_LIBRARY"] = "MultiThreaded"
    return options


def validate_cache(cache, requested):
    values = {}
    for line in cache.splitlines():
        if not line.startswith(("#", "//")) and ":" in line and "=" in line:
            key, value = line.split("=", 1)
            values[key.split(":", 1)[0]] = value
    for key, expected in requested.items():
        if values.get(key) != expected:
            raise PackError(f"cpu_build_configuration_mismatch:{key}")


def audit_dependencies(target, output):
    """Reject non-system dependencies rather than silently bundling host libraries."""
    if target.startswith("macos"):
        libraries = [
            line.strip().split(" (", 1)[0] for line in output.splitlines()[1:] if line.strip()
        ]
        if not libraries or any(
            not lib.startswith(("/usr/lib/", "/System/Library/Frameworks/")) for lib in libraries
        ):
            raise PackError("cpu_pack_non_system_dependency")
        return libraries
    if target.startswith("windows"):
        libraries = [name.lower() for name in re.findall(r"(?im)^\s*([\w.-]+\.dll)\s*$", output)]
        if not libraries or any(
            name not in SYSTEM_DLLS and not name.startswith(("api-ms-win-", "ext-ms-win-"))
            for name in libraries
        ):
            raise PackError("cpu_pack_non_system_dependency")
        return libraries
    allowed = {
        "libc.so.6",
        "libm.so.6",
        "libstdc++.so.6",
        "libgcc_s.so.1",
        "libpthread.so.0",
        "libdl.so.2",
        "librt.so.1",
    }
    libraries = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("linux-vdso"):
            continue
        if "=>" in line:
            name, destination = (part.strip() for part in line.split("=>", 1))
            path = destination.split(" ", 1)[0]
            if name not in allowed or not path.startswith(
                ("/lib/", "/lib64/", "/usr/lib/", "/usr/lib64/")
            ):
                raise PackError("cpu_pack_non_system_dependency")
            libraries.append(name)
        elif re.match(r"/(?:lib|usr/lib)[^ ]*/ld-linux[^ ]*", line):
            libraries.append(line.split(" ", 1)[0])
        else:
            raise PackError("cpu_pack_non_system_dependency")
    if not libraries:
        raise PackError("cpu_pack_dependency_audit_missing")
    return libraries


def required_glibc(symbol_versions):
    values = re.findall(r"\bGLIBC_(\d+(?:\.\d+)+)\b", symbol_versions)
    if not values:
        raise PackError("cpu_pack_glibc_version_missing")
    return max(values, key=lambda v: tuple(map(int, v.split("."))))


def stage_notices(source, stage, windows_license=None):
    # Preserve full upstream texts where the license is embedded in a header.
    # This also retains miniaudio's embedded decoder notices, not just its footer.
    entries = [
        ("llama.cpp", "MIT", "LICENSE"),
        ("cpp-httplib", "MIT", "vendor/cpp-httplib/LICENSE"),
        ("nlohmann-json", "MIT", "licenses/LICENSE-jsonhpp"),
        ("stb-image", "MIT OR Unlicense", "vendor/stb/stb_image.h"),
        ("miniaudio", "MIT-0 OR Unlicense", "vendor/miniaudio/miniaudio.h"),
        ("xxHash", "BSD-2-Clause", "vendor/hash/xxhash/xxhash.h"),
        ("sha1", "LicenseRef-Public-Domain-SHA1", "vendor/hash/sha1/sha1.c"),
        ("sha256", "LicenseRef-Public-Domain-SHA256", "vendor/hash/sha256/sha256.c"),
    ]
    licenses, components, file_licenses = [], [], {}
    notices = stage / "licenses"
    notices.mkdir()
    for name, spdx, original in entries:
        destination = f"licenses/{name}.txt"
        shutil.copyfile(source / original, stage / destination)
        licenses.append({"id": name, "spdx": spdx, "path": destination})
        file_licenses[destination] = name
        components.append(
            {
                "type": "library",
                "name": name,
                "version": f"vendored-at-{REVISION}",
                "licenses": [{"expression": spdx}],
            }
        )
    if windows_license:
        try:
            validate_notice(windows_license)
        except ValueError as exc:
            raise PackError(str(exc)) from None
        destination = "licenses/microsoft-visual-cpp-runtime.txt"
        shutil.copyfile(windows_license, stage / destination)
        name, spdx = "microsoft-runtime", "LicenseRef-Microsoft-Visual-Cpp-Runtime"
        licenses.append({"id": name, "spdx": spdx, "path": destination})
        file_licenses[destination] = name
        components.append(
            {
                "type": "library",
                "name": "Microsoft Visual C++ static runtime",
                "licenses": [{"expression": spdx}],
            }
        )
    expression = " AND ".join(f"({item['spdx']})" for item in licenses)
    (stage / "THIRD_PARTY_NOTICES.txt").write_text(
        "This pack statically links the components listed below. Their original notices\n"
        "are retained under licenses/, including embedded source-header notices.\n\n"
        + "\n".join(f"{c['name']}: {c['licenses'][0]['expression']}" for c in components)
        + "\n",
        encoding="utf-8",
    )
    licenses.append({"id": "bundled", "spdx": expression, "path": "THIRD_PARTY_NOTICES.txt"})
    return licenses, components, file_licenses


def build(target, work, output, version, *, jobs=2, windows_runtime_license=None):
    if target not in TARGETS or current_target() != target:
        raise PackError("cpu_build_requires_matching_host")
    if not 1 <= jobs <= 64 or not re.fullmatch(r"b10853-cpu\.[1-9][0-9]*", version):
        raise PackError("invalid_cpu_build_options")
    if work.exists() or work.is_symlink() or output.exists():
        raise PackError("cpu_build_requires_fresh_paths")
    if target.startswith("windows") and (
        windows_runtime_license is None
        or not windows_runtime_license.is_file()
        or windows_runtime_license.is_symlink()
    ):
        raise PackError("windows_runtime_license_required")
    for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "CMAKE_ARGS", "CMAKE_TOOLCHAIN_FILE"):
        if os.environ.get(key):
            raise PackError(f"cpu_build_ambient_flags_forbidden:{key}")
    work.mkdir(parents=True)
    linux_toolchain = None
    if target.startswith("linux"):
        from linux_abi import toolchain

        linux_toolchain = toolchain()
        (work / "linux-toolchain.json").write_text(json.dumps(linux_toolchain, indent=2) + "\n")
    source, build_dir, stage = work / "source", work / "build", work / "stage"
    run("git", "clone", "--filter=blob:none", "--no-checkout", SOURCE_URL, source)
    run("git", "checkout", "--detach", REVISION, cwd=source)
    if run("git", "rev-parse", "HEAD", cwd=source).strip() != REVISION:
        raise PackError("cpu_build_revision_mismatch")
    artifact = work / "llama-source.tar"
    run("git", "archive", "--format=tar", f"--output={artifact}", REVISION, cwd=source)
    source_sha = sha256_file(artifact)
    options = cmake_options(target)
    configure = ["cmake", "-S", str(source), "-B", str(build_dir)]
    if target.startswith("windows"):
        configure += ["-A", "x64"]
    configure += [f"-D{key}={value}" for key, value in options.items()]
    configure_log = run(*configure)
    validate_cache((build_dir / "CMakeCache.txt").read_text(), options)
    build_log = run(
        "cmake",
        "--build",
        build_dir,
        "--config",
        "Release",
        "--target",
        "llama-server",
        "llama-quantize",
        "--parallel",
        jobs,
    )
    (work / "configure.log").write_text(configure_log)
    (work / "build.log").write_text(build_log)
    stage.mkdir()
    suffix = ".exe" if target.startswith("windows") else ""
    dependencies, versions, symbols, linux_abi = {}, {}, "", {}
    names = [f"llama-server{suffix}", f"llama-quantize{suffix}"]
    for name in names:
        matches = [p for p in (build_dir / "bin").rglob(name) if p.is_file() and not p.is_symlink()]
        if len(matches) != 1:
            raise PackError("cpu_build_binary_missing_or_ambiguous")
        shutil.copyfile(matches[0], stage / name)
        (stage / name).chmod(0o755)
        if target.startswith("linux"):
            from linux_abi import inspect_header

            # Reject a foreign executable before invoking ldd or the startup probe.
            inspect_header(stage / name, target)
        command = (
            ("otool", "-L")
            if target.startswith("macos")
            else ("dumpbin", "/DEPENDENTS")
            if target.startswith("windows")
            else ("ldd",)
        )
        dependencies[name] = audit_dependencies(target, run(*command, stage / name))
        if target.startswith("linux"):
            from linux_abi import audit

            linux_abi[name] = audit(stage / name, stage / f"{name}-abi.txt", target=target)
            symbols += (stage / f"{name}-abi.txt").read_text()
        versions[name] = smoke_binary(stage / name, quantize=name.startswith("llama-quantize"))
    licenses, components, file_licenses = stage_notices(source, stage, windows_runtime_license)
    cpu = "armv8.2-a+fp16+dotprod" if target.endswith("aarch64") else "x86_64+avx2+fma+f16c+bmi2"
    declaration = {
        "schemaVersion": "document-files.pack.v1",
        "id": "llama-cpp-cpu",
        "version": version,
        "kind": "llama-cpp-runtime",
        "platform": target,
        "minimumOS": {
            "name": target.split("-")[0],
            "version": "13.3"
            if target.startswith("macos")
            else "10.0"
            if target.startswith("windows")
            else "5.15",
        },
        "cpuRequirements": cpu,
        "defaultLicense": "bundled",
        "licenses": licenses,
        "components": components,
        "fileLicenses": file_licenses,
        "executables": names,
        "entrypoints": {"server": names[0], "quantize": names[1]},
        "compatibleRuntimes": [],
        "provenance": {
            "sources": [{"uri": SOURCE_URL, "revision": REVISION, "sha256": source_sha}]
        },
    }
    if target.startswith("linux"):
        declaration["minimumGlibc"] = required_glibc(symbols)
    receipt = {
        "schemaVersion": "document-files.cpu-runtime-build.v1",
        "revision": REVISION,
        "sourceArchiveSha256": source_sha,
        "target": target,
        "cpuRequirements": cpu,
        "cmakeOptions": options,
        "linuxToolchain": linux_toolchain,
        "linuxAbi": linux_abi,
        "dependencies": dependencies,
        "binaryVersions": versions,
        "buildHost": platform.platform(),
        "cmakeVersion": run("cmake", "--version"),
        "executionQualification": False,
        "windowsRuntimeLicenseSha256": sha256_file(windows_runtime_license)
        if windows_runtime_license
        else None,
        "requiredSystemSymbolVersions": sorted(
            set(re.findall(r"\b(?:GLIBCXX|CXXABI)_\d+(?:\.\d+)+", symbols))
        ),
    }
    (stage / "build.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (work / "declaration.json").write_text(json.dumps(declaration, indent=2) + "\n")
    return build_pack(stage, declaration, output, {source_sha: artifact})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--windows-runtime-license", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.target,
                args.work.absolute(),
                args.output.absolute(),
                args.version,
                jobs=args.jobs,
                windows_runtime_license=args.windows_runtime_license,
            )
        )
    )


if __name__ == "__main__":
    main()
