"""Builder policy tests, not installed-host or inference qualification evidence."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("cpu_builder", SCRIPTS / "build_cpu_runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.mark.parametrize("target", sorted(builder.TARGETS))
def test_all_platforms_pin_cpu_only_options_and_explicit_arch_baseline(target):
    options = builder.cmake_options(target)
    assert all(options[name] == "OFF" for name in builder.DISABLED)
    assert options["GGML_CPU"] == "ON"
    assert options["FETCHCONTENT_FULLY_DISCONNECTED"] == "ON"
    assert options["LLAMA_BUILD_COMMIT"] == builder.REVISION[:7]
    if target.endswith("x86_64"):
        assert all(options[f"GGML_{feature}"] == "ON" for feature in builder.X64_FEATURES)
    else:
        assert options["GGML_CPU_ARM_ARCH"] == "armv8.2-a+fp16+dotprod"
    if target.startswith("linux"):
        assert options["CMAKE_C_COMPILER"] == "/usr/bin/gcc-12"
        assert options["CMAKE_CXX_COMPILER"] == "/usr/bin/g++-12"
    if target.startswith("windows"):
        assert options["CMAKE_MSVC_RUNTIME_LIBRARY"] == "MultiThreaded"
    cache = "\n".join(f"{key}:STRING={value}" for key, value in options.items())
    builder.validate_cache(cache, options)
    with pytest.raises(builder.PackError, match="configuration_mismatch"):
        builder.validate_cache(
            cache.replace("GGML_CUDA:STRING=OFF", "GGML_CUDA:STRING=ON"), options
        )


@pytest.mark.parametrize(
    "target,good,bad",
    [
        (
            "macos-aarch64",
            "bin:\n\t/usr/lib/libc++.1.dylib (compatibility version 1)\n",
            "bin:\n\t/opt/homebrew/lib/libomp.dylib (compatibility version 1)\n",
        ),
        (
            "linux-x86_64",
            "libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x01)\n"
            "/lib64/ld-linux-x86-64.so.2 (0x02)",
            "libstdc++.so.6 => /opt/compiler/libstdc++.so.6 (0x01)",
        ),
        (
            "linux-aarch64",
            "libc.so.6 => /lib/aarch64-linux-gnu/libc.so.6 (0x01)\n"
            "/lib/ld-linux-aarch64.so.1 (0x02)",
            "libcudart.so => /usr/lib/aarch64-linux-gnu/libcudart.so (0x01)",
        ),
        (
            "windows-x86_64",
            "Image has the following dependencies:\n    KERNEL32.dll\n    WS2_32.dll\n",
            "    VCRUNTIME140.dll\n",
        ),
    ],
)
def test_dependency_audit_rejects_non_system_runtime_leaks(target, good, bad):
    assert builder.audit_dependencies(target, good)
    with pytest.raises(builder.PackError, match="dependency"):
        builder.audit_dependencies(target, bad)
    with pytest.raises(builder.PackError, match="dependency"):
        builder.audit_dependencies(target, "")


def test_glibc_baseline_is_measured_not_assumed_from_runner_label():
    assert builder.required_glibc("GLIBC_2.9 GLIBC_2.17 GLIBC_2.38 GLIBCXX_3.4.32") == "2.38"
    with pytest.raises(builder.PackError, match="glibc_version_missing"):
        builder.required_glibc("No version metadata")


def test_notices_preserve_component_specific_licenses_and_embedded_notices(tmp_path):
    source, stage = tmp_path / "source", tmp_path / "stage"
    source.mkdir()
    stage.mkdir()
    for original in [
        "LICENSE",
        "vendor/cpp-httplib/LICENSE",
        "licenses/LICENSE-jsonhpp",
        "vendor/stb/stb_image.h",
        "vendor/miniaudio/miniaudio.h",
        "vendor/hash/xxhash/xxhash.h",
        "vendor/hash/sha1/sha1.c",
        "vendor/hash/sha256/sha256.c",
    ]:
        path = source / original
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"Entire upstream notice: {original}\nEmbedded third-party notice\n")
    microsoft = tmp_path / "runtime-license.txt"
    microsoft.write_text("MICROSOFT VISUAL STUDIO 2022 test fixture. Distributable code.")
    licenses, components, files = builder.stage_notices(source, stage, microsoft)
    by_name = {component["name"]: component for component in components}
    assert by_name["xxHash"]["licenses"] == [{"expression": "BSD-2-Clause"}]
    assert by_name["miniaudio"]["licenses"] == [{"expression": "MIT-0 OR Unlicense"}]
    assert "Microsoft Visual C++ static runtime" in by_name
    assert (stage / "licenses/miniaudio.txt").read_bytes() == (
        source / "vendor/miniaudio/miniaudio.h"
    ).read_bytes()
    assert files["licenses/microsoft-visual-cpp-runtime.txt"] == "microsoft-runtime"
    assert all((stage / item["path"]).is_file() for item in licenses)
    assert "LicenseRef-Microsoft-Visual-Cpp-Runtime" in licenses[-1]["spdx"]


def test_build_rejects_wrong_host_and_existing_work_before_any_command(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(builder, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(builder, "current_target", lambda: "linux-x86_64")
    with pytest.raises(builder.PackError, match="matching_host"):
        builder.build("windows-x86_64", tmp_path / "work", tmp_path / "pack.zip", "b10853-cpu.2")
    existing = tmp_path / "work"
    existing.mkdir()
    with pytest.raises(builder.PackError, match="fresh_paths"):
        builder.build("linux-x86_64", existing, tmp_path / "pack.zip", "b10853-cpu.2")
    assert calls == []


@pytest.mark.parametrize(
    "host,target", [("linux-x86_64", "linux-aarch64"), ("linux-aarch64", "linux-x86_64")]
)
def test_linux_cpu_cross_arch_build_is_rejected_before_creating_output(
    tmp_path, monkeypatch, host, target
):
    monkeypatch.setattr(builder, "current_target", lambda: host)
    monkeypatch.setattr(builder, "run", lambda *a, **k: pytest.fail("cross-architecture build"))
    with pytest.raises(builder.PackError, match="matching_host"):
        builder.build(target, tmp_path / "work", tmp_path / "pack.zip", "b10853-cpu.4")
    assert not (tmp_path / "work").exists()


@pytest.mark.parametrize("target,foreign_machine", [("linux-aarch64", 62), ("linux-x86_64", 183)])
def test_cpu_foreign_elf_is_rejected_before_ldd_or_startup(
    tmp_path, monkeypatch, target, foreign_machine
):
    import linux_abi
    from test_linux_abi import elf

    work = tmp_path / "work"
    monkeypatch.setattr(builder, "current_target", lambda: target)
    monkeypatch.setattr(linux_abi, "toolchain", lambda: {"fixture": "gcc12"})
    commands = []

    def run(*args, **kwargs):
        commands.append(args)
        if args[:3] == ("git", "rev-parse", "HEAD"):
            return builder.REVISION
        if args[:2] == ("git", "archive"):
            (work / "llama-source.tar").write_bytes(b"fixture source")
        if args[:2] == ("cmake", "-S"):
            directory = work / "build"
            directory.mkdir()
            options = builder.cmake_options(target)
            (directory / "CMakeCache.txt").write_text(
                "\n".join(f"{key}:STRING={value}" for key, value in options.items())
            )
        if args[:2] == ("cmake", "--build"):
            directory = work / "build/bin"
            directory.mkdir()
            (directory / "llama-server").write_bytes(elf(foreign_machine))
        if args[0] == "ldd":
            pytest.fail("foreign ELF passed to ldd")
        return ""

    monkeypatch.setattr(builder, "run", run)
    monkeypatch.setattr(
        builder, "smoke_binary", lambda *a, **k: pytest.fail("executed foreign ELF")
    )
    with pytest.raises(ValueError, match="ELF target mismatch"):
        builder.build(target, work, tmp_path / "pack.zip", "b10853-cpu.4")
    assert not (tmp_path / "pack.zip").exists()
    assert all(command[0] != "ldd" for command in commands)


def test_windows_static_runtime_requires_separate_license_input(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "current_target", lambda: "windows-x86_64")
    with pytest.raises(builder.PackError, match="windows_runtime_license_required"):
        builder.build("windows-x86_64", tmp_path / "work", tmp_path / "pack.zip", "b10853-cpu.2")
    assert not (tmp_path / "work").exists()


def test_ambient_compiler_flags_cannot_silently_override_cpu_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "current_target", lambda: "linux-x86_64")
    monkeypatch.setenv("CXXFLAGS", "-march=native")
    with pytest.raises(builder.PackError, match="ambient_flags_forbidden"):
        builder.build("linux-x86_64", tmp_path / "work", tmp_path / "pack.zip", "b10853-cpu.2")
    assert not (tmp_path / "work").exists()


def test_quantizer_help_is_an_expected_nonzero_smoke_not_a_fake_version(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    monkeypatch.setattr(
        builder.subprocess,
        "run",
        lambda *a, **kw: CompletedProcess(
            a, 1, "usage: llama-quantize --help --allow-requantize", ""
        ),
    )
    result = builder.smoke_binary(tmp_path / "quantize", quantize=True)
    assert result["exitCode"] == 1 and result["argument"] == "--help"
    with pytest.raises(builder.PackError, match="identity_or_smoke_failed"):
        builder.smoke_binary(tmp_path / "server")


def test_new_ci_pack_version_is_explicit_and_valid_without_reusing_old_default(
    tmp_path, monkeypatch
):
    workflow = (SCRIPTS.parent / ".github/workflows/cpu-runtime.yml").read_text()
    assert "default: 'b10853-cpu.4'" in workflow
    assert "inputs.pack_version || 'b10853-cpu.4'" in workflow
    assert '--version "$PACK_VERSION"' in workflow
    monkeypatch.setattr(builder, "current_target", lambda: "linux-x86_64")
    monkeypatch.setattr(builder, "run", lambda *a, **k: pytest.fail("unexpected build"))
    with pytest.raises(builder.PackError, match="fresh_paths"):
        builder.build("linux-x86_64", tmp_path, tmp_path / "pack.zip", "b10853-cpu.4")
    with pytest.raises(builder.PackError, match="invalid_cpu_build_options"):
        builder.build("linux-x86_64", tmp_path / "new", tmp_path / "pack.zip", "b10853-cpu.0")
