"""Scripted synthetic ELF/wheel regressions, never target execution or release evidence."""

import importlib.util
import json
import struct
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location(
        "tv_derivative", Path(__file__).parents[1] / "scripts/prepare_torchvision_cpu_wheel.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native(tool, *, soname="libcodec.so.1", loader=False, version="GLIBC_2.17", suffix=False):
    """Build inert ELF tables with a single versioned import, no executable instructions."""
    names = ["", ".shstrtab", ".dynstr", ".dynsym", ".dynamic", ".gnu.version", ".gnu.version_r"]
    names_blob = b"\0"
    name_offsets = {"": 0}
    for name in names[1:]:
        name_offsets[name] = len(names_blob)
        names_blob += name.encode() + b"\0"
    strings, offsets = b"\0prefix" if suffix == "prefix" else b"\0", {}
    for value in (tool.OLD, "__stack_chk_guard", version, soname):
        if value not in offsets:
            offsets[value] = len(strings)
            strings += value.encode() + b"\0"
    symbols = bytes(24)
    if not loader:
        symbols += struct.pack("<IBBHQQ", offsets["__stack_chk_guard"], 0x11, 0, 0, 0, 8)
    if suffix:
        symbols += struct.pack(
            "<IBBHQQ", 1 if suffix == "prefix" else offsets[tool.OLD] + 3, 0x11, 0, 1, 0, 1
        )
    versym = struct.pack(
        "<" + "H" * (len(symbols) // 24), *([0] + ([] if loader else [2]) + ([0] if suffix else []))
    )
    need = struct.pack("<HHIII", 1, 1, offsets[tool.OLD], 16, 0)
    need += struct.pack("<IHHII", 0, 0, 2, offsets[version], 0)
    contents = {
        ".shstrtab": names_blob,
        ".dynstr": strings,
        ".dynsym": symbols,
        ".dynamic": bytes(16 * (7 if loader else 10)),
        ".gnu.version": versym,
        ".gnu.version_r": b"" if loader else need,
    }
    positions = {}
    cursor = 176
    for name in names[1:]:
        positions[name] = cursor
        cursor += (len(contents[name]) + 7) // 8 * 8
    shoff = cursor
    address = lambda name: 0x10000 + positions[name]  # noqa: E731
    entries = [
        (5, address(".dynstr")),
        (10, len(strings)),
        (6, address(".dynsym")),
        (11, 24),
        (14, offsets[soname]),
        (0x6FFFFFF0, address(".gnu.version")),
    ]
    if not loader:
        entries += [
            (1, offsets[tool.OLD]),
            (0x6FFFFFFE, address(".gnu.version_r")),
            (0x6FFFFFFF, 1),
        ]
    entries += [(0, 0)]
    contents[".dynamic"] = b"".join(struct.pack("<qQ", *e) for e in entries)
    data = bytearray(shoff + len(names) * 64)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into(
        "<16sHHIQQQIHHHHHH",
        data,
        0,
        ident,
        3,
        183,
        1,
        0,
        64,
        shoff,
        0,
        64,
        56,
        2,
        64,
        len(names),
        1,
    )
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 4, 0, 0x10000, 0, shoff, shoff, 4096)
    struct.pack_into(
        "<IIQQQQQQ",
        data,
        120,
        2,
        4,
        positions[".dynamic"],
        address(".dynamic"),
        0,
        len(contents[".dynamic"]),
        len(contents[".dynamic"]),
        8,
    )
    for i, name in enumerate(names[1:], 1):
        content = contents[name]
        data[positions[name] : positions[name] + len(content)] = content
        kind = {
            ".shstrtab": 3,
            ".dynstr": 3,
            ".dynsym": 11,
            ".dynamic": 6,
            ".gnu.version": 0x6FFFFFFF,
            ".gnu.version_r": 0x6FFFFFFE,
        }[name]
        link = 2 if name in (".dynsym", ".dynamic", ".gnu.version_r") else 0
        if name == ".gnu.version":
            link = 3
        info = int(name == ".gnu.version_r" and not loader)
        size = {".dynsym": 24, ".dynamic": 16, ".gnu.version": 2}.get(name, 0)
        struct.pack_into(
            "<IIQQQQIIQQ",
            data,
            shoff + i * 64,
            name_offsets[name],
            kind,
            0,
            address(name),
            positions[name],
            len(content),
            link,
            info,
            8,
            size,
        )
    if loader:
        # No version needs section or tag on the synthetic loader.
        struct.pack_into("<I", data, shoff + 6 * 64, 0)
    return bytes(data), positions


@pytest.fixture
def wheel(tool, tmp_path, monkeypatch):
    def create(mutate=None):
        codecs = {}
        files = {}
        for i, name in enumerate(tool.CODECS):
            files[name], _ = native(tool, soname=f"libcodec{i}.so.1")
            codecs[name] = tool.sha(files[name])
        files[tool.LOADER], _ = native(tool, soname=tool.SYSTEM, loader=True)
        files[tool.DIST_INFO + "/LICENSE"] = b"Synthetic upstream license bytes\r\n"
        files[tool.DIST_INFO + "/METADATA"] = (
            b"Name: torchvision\nVersion: 0.29.0+cpu\nRequires-Dist: torch==2.14.0\n"
        )
        files[tool.DIST_INFO + "/WHEEL"] = (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp312-cp312-manylinux_2_28_aarch64\n"
        )
        files["torchvision/version.py"] = b"__version__ = '0.29.0+cpu'\n"
        if mutate:
            mutate(files)
        codecs = {n: tool.sha(files.get(n, b"")) for n in codecs}
        monkeypatch.setattr(tool, "CODECS", codecs)
        monkeypatch.setattr(tool, "LOADER_SHA256", tool.sha(files.get(tool.LOADER, b"")))
        files[tool.DIST_INFO + "/RECORD"] = tool.record_bytes(files)
        source = tmp_path / tool.INPUT_NAME
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in files.items():
                archive.writestr(name, value)
        monkeypatch.setattr(tool, "INPUT_SHA256", tool.sha(source.read_bytes()))
        return source, files

    return create


def test_exact_restoration_preserves_other_bytes_and_indices(tool):
    original, _ = native(tool)
    result, detail = tool.restore_loader(original)
    assert len(result) == len(original)
    allowed = {i for p in detail["patches"] for i in range(p["offset"], p["offset"] + p["length"])}
    assert all(
        a == b for i, (a, b) in enumerate(zip(original, result, strict=True)) if i not in allowed
    )
    assert tool.elf(result)["needed"] == [tool.SYSTEM]
    assert detail["importsBefore"][0]["symbolIndex"] == detail["importsAfter"][0]["symbolIndex"]
    assert detail["importsBefore"][0]["versionIndex"] == detail["importsAfter"][0]["versionIndex"]
    assert tool.elf(result)["soname"] == tool.elf(original)["soname"]


@pytest.mark.parametrize(
    "fault", ["suffix", "prefix", "private", "wrong_symbol", "versym", "verneed", "machine"]
)
def test_unsafe_elf_rejected(tool, fault):
    data, pos = native(
        tool,
        suffix="prefix" if fault == "prefix" else fault == "suffix",
        version="GLIBC_PRIVATE" if fault == "private" else "GLIBC_2.17",
    )
    data = bytearray(data)
    if fault == "wrong_symbol":
        data[pos[".dynstr"] + data[pos[".dynstr"] :].index(b"__stack_chk_guard")] = ord("x")
    elif fault == "versym":
        struct.pack_into("<H", data, pos[".gnu.version"] + 2, 77)
    elif fault == "verneed":
        struct.pack_into("<H", data, pos[".gnu.version_r"] + 22, 1)
    elif fault == "machine":
        struct.pack_into("<H", data, 18, 62)
    with pytest.raises(tool.PreparationError):
        tool.restore_loader(bytes(data))


def test_new_wheel_record_license_and_input_immutability(tool, wheel, tmp_path):
    source, files = wheel()
    before = source.read_bytes()
    output = tmp_path / "candidate"
    report = tool.prepare(source, output)
    assert source.read_bytes() == before
    assert report["stageApproved"] is report["releaseQualified"] is False
    assert report["targetExecution"] == "not-performed"
    assert len(report["changes"]) == 5
    assert report["licenseReview"].startswith("pending")
    assert tool.OUTPUT_NAME != tool.INPUT_NAME
    with zipfile.ZipFile(output / tool.OUTPUT_NAME) as archive:
        changed = {n: archive.read(n) for n in archive.namelist()}
    tool.check_record(changed)
    assert tool.LOADER not in changed
    for name in (
        tool.DIST_INFO + "/LICENSE",
        tool.DIST_INFO + "/METADATA",
        "torchvision/version.py",
    ):
        assert changed[name] == files[name]
    assert b"Build: 1dfarmloader1\n" in changed[tool.DIST_INFO + "/WHEEL"]
    assert json.loads((output / "derivation.json").read_text())["output"] == report["output"]
    assert {x["name"] for x in report["externalDependenciesAfter"]} == {tool.SYSTEM}
    with pytest.raises(tool.PreparationError, match="output already exists"):
        tool.prepare(source, output)


@pytest.mark.parametrize("fault", ["input_sha", "codec_sha", "loader_sha", "budget", "time"])
def test_pin_and_budget_rejections_leave_no_output(tool, wheel, tmp_path, monkeypatch, fault):
    source, _ = wheel()
    if fault == "input_sha":
        monkeypatch.setattr(tool, "INPUT_SHA256", "0" * 64)
    elif fault == "codec_sha":
        monkeypatch.setattr(tool, "CODECS", {n: "0" * 64 for n in tool.CODECS})
    elif fault == "loader_sha":
        monkeypatch.setattr(tool, "LOADER_SHA256", "0" * 64)
    elif fault == "budget":
        monkeypatch.setattr(tool, "MAX_DECODED", 1)
    else:
        monkeypatch.setattr(tool, "MAX_SECONDS", -1)
    with pytest.raises(tool.PreparationError):
        tool.prepare(source, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize(
    "fault", ["extra_consumer", "soname", "external", "signed", "unsafe", "wrong_tag"]
)
def test_wheel_boundaries_rejected(tool, wheel, tmp_path, fault):
    def mutate(files):
        if fault == "extra_consumer":
            files["torchvision/extra.so"] = native(tool, soname="libextra.so")[0]
        elif fault == "soname":
            keys = list(tool.CODECS)
            files[keys[1]] = files[keys[0]]
        elif fault == "external":
            # Same byte length, but never accepted as a dependency path.
            key = next(iter(tool.CODECS))
            files[key] = files[key].replace(tool.OLD.encode(), b"/" + tool.OLD.encode()[1:])
        elif fault == "signed":
            files[tool.DIST_INFO + "/RECORD.jws"] = b"not-a-valid-signature"
        elif fault == "unsafe":
            files["../outside"] = b"bad"
        else:
            files[tool.DIST_INFO + "/WHEEL"] += b"Tag: cp312-cp312-linux_x86_64\n"

    source, _ = wheel(mutate)
    with pytest.raises(tool.PreparationError):
        tool.prepare(source, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


def test_record_tampering_rejected(tool):
    files = {"a": b"correct"}
    files[tool.DIST_INFO + "/RECORD"] = tool.record_bytes(files)
    files["a"] = b"wrong"
    with pytest.raises(tool.PreparationError, match="RECORD mismatch"):
        tool.check_record(files)


def test_duplicate_record_row_rejected(tool):
    files = {"a": b"correct"}
    record = tool.record_bytes(files)
    files[tool.DIST_INFO + "/RECORD"] = record + record.splitlines(keepends=True)[0]
    with pytest.raises(tool.PreparationError, match="RECORD coverage"):
        tool.check_record(files)


def test_output_failure_cleans_only_new_directory(tool, wheel, tmp_path, monkeypatch):
    source, _ = wheel()
    before = source.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("synthetic output failure")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail)
    with pytest.raises(OSError, match="synthetic output failure"):
        tool.prepare(source, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()
    assert source.read_bytes() == before


def test_output_bytes_deterministic_without_claiming_runtime_reproducibility(tool, wheel, tmp_path):
    source, _ = wheel()
    first = tool.prepare(source, tmp_path / "one")
    second = tool.prepare(source, tmp_path / "two")
    assert first["output"] == second["output"]
    assert first["stageApproved"] is second["stageApproved"] is False
