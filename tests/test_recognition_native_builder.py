"""Offline recipe/security contracts, never native execution qualification."""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "recognition_native_builder", ROOT / "scripts/build_recognition_native.py"
)
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


def archive(path, entries):
    with tarfile.open(path, "w:gz") as bundle:
        for name, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = 1 if kind == tarfile.REGTYPE else 0
            member.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else ""
            bundle.addfile(member, io.BytesIO(b"x") if member.size else None)
    return path


def test_public_pins_have_all_sources_and_exact_notices():
    pins = native.load_pins(native.PINS)
    assert [s["id"] for s in pins["sources"]] == native.ORDER
    assert len(pins["tessdata"]) == 4
    assert "private/" not in native.PINS.read_text()
    tiff = next(s for s in pins["sources"] if s["id"] == "tiff")
    assert tiff["url"].endswith(".tar.gz")
    assert pins["sources"][0]["version"] == "1.3.2"


@pytest.mark.parametrize(
    "field,value",
    [("sha256", ""), ("url", "http://example.org/a"), ("license", ""), ("notices", [])],
)
def test_missing_hash_or_license_is_rejected(tmp_path, field, value):
    pins = json.loads(native.PINS.read_text())
    pins["sources"][0][field] = value
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(pins))
    with pytest.raises(native.BuildError):
        native.load_pins(path)


def test_source_hash_always_checked_without_network(tmp_path, monkeypatch):
    item = {"sha256": "a" * 64, "url": "https://example.org/a"}
    monkeypatch.setattr(
        native.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("network forbidden")
    )
    with pytest.raises(native.BuildError, match="missing pinned input"):
        native.acquire(item, tmp_path, False)
    (tmp_path / item["sha256"]).write_bytes(b"wrong")
    with pytest.raises(native.BuildError, match="source hash mismatch"):
        native.acquire(item, tmp_path, False)


@pytest.mark.parametrize(
    "entries",
    [
        [("../escape", tarfile.REGTYPE)],
        [("/escape", tarfile.REGTYPE)],
        [("root/link", tarfile.SYMTYPE)],
        [("root/link", tarfile.LNKTYPE)],
        [("root/a", tarfile.REGTYPE), ("root/a", tarfile.REGTYPE)],
        [("one/a", tarfile.REGTYPE), ("two/a", tarfile.REGTYPE)],
        [("root/C:bad", tarfile.REGTYPE)],
        [("root/back\\bad", tarfile.REGTYPE)],
    ],
)
def test_unsafe_archives_rejected(tmp_path, entries):
    with pytest.raises(native.BuildError):
        native.unpack(archive(tmp_path / "source.tar.gz", entries), tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_regular_archive_and_existing_output(tmp_path):
    source = archive(tmp_path / "source.tar.gz", [("root/a", tarfile.REGTYPE)])
    out = native.unpack(source, tmp_path / "out")
    assert (out / "a").read_bytes() == b"x"
    with pytest.raises(FileExistsError):
        native.unpack(source, tmp_path / "out")


@pytest.mark.parametrize("target", ["linux-x86_64", "windows-x86_64"])
def test_recipe_cpu_static_and_optional_network_disabled(tmp_path, target):
    opts = native.options("tesseract", tmp_path, target)
    for flag in [
        "-DOPENMP_BUILD=OFF",
        "-DENABLE_NATIVE=OFF",
        "-DDISABLE_CURL=ON",
        "-DDISABLE_ARCHIVE=ON",
    ]:
        assert flag in opts
    assert "-DWITH_SIMD=OFF" in native.options("jpeg", tmp_path, target)
    assert "-DENABLE_TIFF=ON" in native.options("leptonica", tmp_path, target)
    assert not any(
        "SDK" in flag or ".tbd" in flag
        for item in native.ORDER
        for flag in native.options(item, tmp_path, target)
    )


@pytest.mark.parametrize("target", ["linux-x86_64", "windows-x86_64"])
def test_binary_target_headers(tmp_path, target):
    data = bytearray(128)
    if target.startswith("linux"):
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 18, 62)
    else:
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 80)
        data[80:86] = b"PE\0\0\x64\x86"
    path = tmp_path / "binary"
    path.write_bytes(data)
    native.check_binary(path, target)
    with pytest.raises(native.BuildError):
        native.check_binary(
            path, "windows-x86_64" if target.startswith("linux") else "linux-x86_64"
        )


def test_linux_dynamic_dependency_and_rpath_guard():
    raw = "0x (NEEDED) Shared library: [libc.so.6]\n0x (NEEDED) Shared library: [libm.so.6]"
    assert native.parse_linkage(raw, "linux-x86_64") == ["libc.so.6", "libm.so.6"]
    for extra in [
        "\n0x (NEEDED) Shared library: [libstdc++.so.6]",
        "\n0x (RUNPATH) Library runpath: [/tmp/prefix]",
    ]:
        with pytest.raises(native.BuildError):
            native.parse_linkage(raw + extra, "linux-x86_64")


def test_windows_requires_system_only_imports():
    assert native.parse_linkage(
        "    KERNEL32.dll\n    api-ms-win-crt-runtime-l1-1-0.dll\n", "windows-x86_64"
    )
    for dll in ["VCRUNTIME140.dll", "MSVCP140.dll", "libpng16.dll", "cuda.dll"]:
        with pytest.raises(native.BuildError):
            native.parse_linkage(f"    KERNEL32.dll\n    {dll}\n", "windows-x86_64")


def test_empty_linkage_fails_closed():
    with pytest.raises(native.BuildError):
        native.parse_linkage("not tool output", "linux-x86_64")


def test_wrong_host_fails_before_creating_work(tmp_path, monkeypatch):
    monkeypatch.setattr(native, "current_target", lambda: "linux-x86_64")
    with pytest.raises(native.BuildError):
        native.run_build(SimpleNamespace(target="windows-x86_64", work=tmp_path / "new"))
    assert not (tmp_path / "new").exists()


def test_export_fix_only_local_generated_export(tmp_path):
    export = tmp_path / "lib/cmake/leptonica/LeptonicaTargets.cmake"
    export.parent.mkdir(parents=True)
    export.write_text("ZLIB::ZLIB;JPEG::JPEG")
    with pytest.raises(native.BuildError, match="static dependency"):
        native.rewrite_export(tmp_path, "linux-x86_64")
    for name in ["libz.a", "libjpeg.a"]:
        (tmp_path / "lib" / name).write_bytes(b"lib")
    native.rewrite_export(tmp_path, "linux-x86_64")
    assert "::" not in export.read_text()
    assert str(tmp_path / "lib/libz.a") in export.read_text()


def test_existing_partial_download_is_preserved(tmp_path):
    item = {"sha256": "a" * 64, "url": "https://example.org/a"}
    partial = tmp_path / (item["sha256"] + ".partial")
    partial.write_bytes(b"other run")
    with pytest.raises(FileExistsError):
        native.acquire(item, tmp_path, True)
    assert partial.read_bytes() == b"other run"


@pytest.mark.parametrize("matches", [True, False])
def test_download_bytes_verified_before_use(tmp_path, monkeypatch, matches):
    data = b"pinned source"
    digest = native.hashlib.sha256(data).hexdigest() if matches else "a" * 64
    item = {"sha256": digest, "url": "https://example.org/a"}
    response = io.BytesIO(data)
    response.url = item["url"]
    monkeypatch.setattr(native.urllib.request, "urlopen", lambda *_a, **_k: response)
    if matches:
        assert native.acquire(item, tmp_path, True).read_bytes() == data
    else:
        with pytest.raises(native.BuildError, match="hash mismatch"):
            native.acquire(item, tmp_path, True)
        assert not (tmp_path / digest).exists()
    assert not (tmp_path / (digest + ".partial")).exists()


def test_simulated_build_receipt_never_claims_pack_or_quality(tmp_path, monkeypatch):
    """Mock tool calls exercise receipt flow only; never evidence for a release."""
    pins = native.load_pins(native.PINS)
    payload = b"x"
    digest = native.hashlib.sha256(payload).hexdigest()
    archive_file = archive(tmp_path / "source.tar.gz", [("root/LICENSE", tarfile.REGTYPE)])
    for item in pins["sources"]:
        item["notices"] = [{"path": "LICENSE", "sha256": digest}]
    monkeypatch.setattr(native, "load_pins", lambda _path: pins)
    monkeypatch.setattr(native, "acquire", lambda *_a: archive_file)
    monkeypatch.setattr(native, "current_target", lambda: "linux-x86_64")
    monkeypatch.setattr(
        native.subprocess,
        "check_output",
        lambda argv, **_kw: "" if "status" in argv else "commit\n",
    )
    work = tmp_path / "work"

    def mock_run(argv, **kwargs):
        output = "tool version\n"
        if "--install" in argv:
            component = Path(argv[2]).name
            lib = work / "prefix/lib"
            lib.mkdir(parents=True, exist_ok=True)
            if component in {"zlib", "jpeg"}:
                (lib / ("libz.a" if component == "zlib" else "libjpeg.a")).write_bytes(b"lib")
            if component == "leptonica":
                export = lib / "cmake/leptonica/LeptonicaTargets.cmake"
                export.parent.mkdir(parents=True)
                export.write_text("ZLIB::ZLIB;JPEG::JPEG")
            if component == "tesseract":
                binary = work / "prefix/bin/tesseract"
                binary.parent.mkdir()
                data = bytearray(128)
                data[:6] = b"\x7fELF\x02\x01"
                struct.pack_into("<H", data, 18, 62)
                binary.write_bytes(data)
        if "-d" in argv:
            output = "0x (NEEDED) Shared library: [libc.so.6]\n"
        if argv[0].endswith("/candidate/bin/tesseract"):
            assert not (work / "prefix").exists()
            assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
            output = (
                "List of available languages (0):\n"
                if "--list-langs" in argv
                else (
                    "tesseract 5.5.3 leptonica-1.87.0 libpng 1.6.58 "
                    "libjpeg-turbo 3.2.0 libtiff 4.7.2 zlib 1.3.2\n"
                )
            )
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(native.subprocess, "run", mock_run)
    result = native.run_build(
        SimpleNamespace(
            target="linux-x86_64",
            work=work,
            pins=native.PINS,
            cache=tmp_path / "cache",
            download=False,
            jobs=4,
            with_tessdata=False,
        )
    )
    assert result["nativeExecutionVerified"] is True  # simulated command success only
    assert result["fullRecognitionQualified"] is False
    assert result["releaseReady"] is False
    assert result["languageDiscoveryVerified"] is False
    assert result["sourceCommit"] == "commit"
    assert result["files"]
    assert (work / "native-candidate.json").is_file()
