#!/usr/bin/env python3
"""Prepare one explicit, unqualified ARM CPU wheel derivative; never load native code.

Only the pinned torchvision input is accepted by the CLI. Five codec DT_NEEDED /
verneed loader names are restored together, then the now-unreferenced bundled
loader is omitted from a NEW wheel. Public package version/requirements stay fixed.
This is not upstream provenance, license, runtime or recognition-stage approval.
The byte/name checks do not identify arbitrarily renamed loaders in other wheels.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import struct
import time
import zipfile
from pathlib import Path, PurePosixPath

INPUT_NAME = "torchvision-0.29.0+cpu-cp312-cp312-manylinux_2_28_aarch64.whl"
INPUT_SHA256 = "8ac096fe796dfacbd174be86f7ab3595a39eabc341d2ee2e48557032ec1d57c4"
BUILD_TAG = "1dfarmloader1"
OUTPUT_NAME = INPUT_NAME.replace("-cp312-", f"-{BUILD_TAG}-cp312-", 1)
DIST_INFO = "torchvision-0.29.0+cpu.dist-info"
OLD = "ld-linux-aarch64.5cbc5e90.so.1"
SYSTEM = "ld-linux-aarch64.so.1"
LOADER = "torchvision.libs/" + OLD
LOADER_SHA256 = "88ec4a27977207469cbe73274d31afd41263439dcbd56ea257ad8bd4bb9a9303"
CODECS = {  # Exact original member hashes; not independently replaceable CLI inputs.
    "torchvision.libs/libpng16.151ef45e.so.16": (
        "351a322ba429d6011430236631b9b2f5383c7780b83cf91ec7ba359f1c62e68c"
    ),
    "torchvision.libs/libjpeg.58c83b75.so.8": (
        "085ec15383139074c46dc34bbd2e1dede3bf5d169d3c7aa1576a4708aae970a2"
    ),
    "torchvision.libs/libwebp.7dd92f04.so.7": (
        "8ed66376722907c7bebb020dc11eb494b490f943cd96c77b9c7813f5d8375f79"
    ),
    "torchvision.libs/libz.07f04cb6.so.1": (
        "594c7ae664e2045feb4a85b5937cb9dab0a63e023130ad8d8acacad1da25b4b3"
    ),
    "torchvision.libs/libsharpyuv.381ce5c4.so.0": (
        "770e515d0f342670a0bd8f6a2040e824eafb8840cede77c40b6835decf2c29a2"
    ),
}
EXTERNAL = {
    "libc10.so",
    "libtorch.so",
    "libtorch_cpu.so",
    "libtorch_python.so",
    "libstdc++.so.6",
    "libm.so.6",
    "libgcc_s.so.1",
    "libc.so.6",
    "libpthread.so.0",
    SYSTEM,
}
MAX_ARCHIVE = 8 * 1024**2
MAX_MEMBER = 16 * 1024**2
MAX_DECODED = 64 * 1024**2
MAX_MEMBERS = 2048
MAX_SECONDS = 60
RECIPE = "document-files.torchvision-arm-system-loader.v1"
SOURCES = {
    "wheel": "https://download.pytorch.org/whl/cpu/" + INPUT_NAME.replace("+", "%2B"),
    "upstreamRelocation": "https://github.com/pytorch/vision/blob/v0.29.0/packaging/wheel/relocate.py",
    "upstreamPostBuild": "https://github.com/pytorch/vision/blob/v0.29.0/packaging/post_build_script.sh",
}


class PreparationError(ValueError):
    """Input did not satisfy this exact, non-general transformation recipe."""


def require(condition, message):
    if not condition:
        raise PreparationError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def elf(data):
    """Bounded ELF64 LE dynamic string/symbol/version index inspection, no execution."""
    require(len(data) >= 64, "truncated ELF")
    h = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    require(h[0][:7] == b"\x7fELF\x02\x01\x01" and h[1:4] == (3, 183, 1), "not ARM DSO")
    require(h[8] == 64 and h[11] == 64 and 0 < h[12] <= 4096, "invalid section table")
    require(0 < h[10] <= 4096 and h[9] == 56, "invalid program table")

    def bounds(offset, size):
        require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset, "ELF bounds")
        return data[offset : offset + size]

    bounds(h[6], h[11] * h[12])
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, h[6] + i * 64) for i in range(h[12])]
    require(h[13] < len(sections), "section name index")

    def section_bytes(s):
        return bounds(s[4], s[5])

    def string(table, offset):
        require(0 <= offset < len(table), "string index")
        end = table.find(b"\0", offset)
        require(end >= 0, "unterminated string")
        try:
            return table[offset:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PreparationError("invalid ELF string") from exc

    names = section_bytes(sections[h[13]])
    named = {}
    for s in sections:
        name = string(names, s[0])
        if name:
            require(name not in named, "duplicate section")
            named[name] = s
        if s[1] != 8:
            bounds(s[4], s[5])
    require(all(n in named for n in (".dynamic", ".dynstr", ".dynsym")), "missing dynamic sections")
    ds, dy, sy = (named[n] for n in (".dynstr", ".dynamic", ".dynsym"))
    require(ds[1] == 3 and dy[1] == 6 and sy[1] == 11, "section type")
    require(dy[9] == 16 and sy[9] == 24 and dy[5] % 16 == sy[5] % 24 == 0, "dynamic entry size")
    require(
        dy[6] < len(sections)
        and sections[dy[6]] == ds
        and sy[6] < len(sections)
        and sections[sy[6]] == ds,
        "dynstr linkage",
    )
    bounds(h[5], 56 * h[10])
    headers = [struct.unpack_from("<IIQQQQQQ", data, h[5] + i * 56) for i in range(h[10])]
    require(not any(p[0] == 3 for p in headers), "unexpected DSO interpreter")
    require(
        [(p[2], p[3], p[5]) for p in headers if p[0] == 2] == [(dy[4], dy[3], dy[5])],
        "dynamic section/program mismatch",
    )
    require(
        any(
            p[0] == 1
            and p[2] <= ds[4]
            and ds[4] + ds[5] <= p[2] + p[5]
            and ds[3] - p[3] == ds[4] - p[2]
            for p in headers
        ),
        "unmapped dynstr",
    )
    handled_string_sections = {".dynamic", ".dynsym", ".gnu.version_r", ".gnu.version_d"}
    for name, s in named.items():
        if s[6] and s[6] < len(sections) and sections[s[6]] == ds:
            require(name in handled_string_sections, "unhandled dynstr consumer section")
    protected = [(0, 64), (h[5], h[10] * 56), (h[6], h[12] * 64)]
    protected += [(s[4], s[5]) for s in sections if s != ds and s[1] != 8 and s[5]]
    require(
        not any(start < ds[4] + ds[5] and ds[4] < start + size for start, size in protected),
        "dynstr overlaps another ELF structure",
    )
    strings = section_bytes(ds)
    refs = []

    def ref(offset, role):
        value = string(strings, offset)
        refs.append({"offset": offset, "role": role, "text": value})
        return value

    dynamics, needed, soname = {}, [], []
    terminated = False
    for i in range(dy[5] // 16):
        tag, value = struct.unpack_from("<qQ", data, dy[4] + i * 16)
        if tag == 0:
            terminated = True
            continue
        require(not terminated, "dynamic data after terminator")
        dynamics.setdefault(tag, []).append(value)
        if tag in (
            1,
            14,
            15,
            29,
            0x7FFFFFFD,
            0x7FFFFFFE,
            0x7FFFFFFF,
            0x6FFFFEFA,
            0x6FFFFEFB,
            0x6FFFFEFC,
        ):
            text = ref(value, "needed" if tag == 1 else "dynamic")
            if tag == 1:
                needed.append(text)
            elif tag == 14:
                soname.append(text)
    require(terminated and len(soname) <= 1, "invalid dynamic terminator/SONAME")
    for tag, expected in ((5, ds[3]), (10, ds[5]), (6, sy[3]), (11, 24)):
        require(dynamics.get(tag) == [expected], "dynamic address/size mismatch")
    versions, needs = {}, []
    if ".gnu.version_r" in named:
        s = named[".gnu.version_r"]
        require(s[6] < len(sections) and sections[s[6]] == ds, "verneed string linkage")
        require(
            dynamics.get(0x6FFFFFFE) == [s[3]] and dynamics.get(0x6FFFFFFF) == [s[7]],
            "verneed dynamic linkage",
        )
        x, offset, visited = section_bytes(s), 0, set()
        require(0 < s[7] <= 4096, "verneed count")
        for index in range(s[7]):
            require(offset not in visited and offset + 16 <= len(x), "verneed bounds/cycle")
            visited.add(offset)
            version, count, filename, aux, nxt = struct.unpack_from("<HHIII", x, offset)
            require(version == 1 and 0 < count <= 4096, "verneed header")
            provider = ref(filename, "verneed-provider")
            require(provider in needed, "verneed provider not DT_NEEDED")
            position, aux_seen = offset + aux, set()
            for ai in range(count):
                require(position not in aux_seen and position + 16 <= len(x), "vernaux bounds")
                aux_seen.add(position)
                _, flags, vi, name, anext = struct.unpack_from("<IHHII", x, position)
                require(vi >= 2 and vi < 0x8000 and vi not in versions, "verneed index collision")
                row = {
                    "provider": provider,
                    "version": ref(name, "version-name"),
                    "versionIndex": vi,
                    "flags": flags,
                }
                versions[vi] = row
                needs.append(row)
                require(bool(anext) == (ai + 1 < count), "vernaux count mismatch")
                position += anext
            require(bool(nxt) == (index + 1 < s[7]), "verneed count mismatch")
            offset += nxt
    else:
        require(0x6FFFFFFE not in dynamics and 0x6FFFFFFF not in dynamics, "hidden verneed")
    definitions = set()
    if ".gnu.version_d" in named:
        s = named[".gnu.version_d"]
        require(s[6] < len(sections) and sections[s[6]] == ds, "verdef string linkage")
        require(
            dynamics.get(0x6FFFFFFC) == [s[3]] and dynamics.get(0x6FFFFFFD) == [s[7]],
            "verdef dynamic linkage",
        )
        x, offset, visited = section_bytes(s), 0, set()
        require(0 < s[7] <= 4096, "verdef count")
        for index in range(s[7]):
            require(offset not in visited and offset + 20 <= len(x), "verdef bounds")
            visited.add(offset)
            version, _, vi, count, _, aux, nxt = struct.unpack_from("<HHHHIII", x, offset)
            require(
                version == 1 and 0 < count <= 4096 and vi not in versions and vi not in definitions,
                "verdef index/count",
            )
            definitions.add(vi)
            position, aux_seen = offset + aux, set()
            for ai in range(count):
                require(position not in aux_seen and position + 8 <= len(x), "verdaux bounds")
                aux_seen.add(position)
                name, anext = struct.unpack_from("<II", x, position)
                ref(name, "version-definition")
                require(bool(anext) == (ai + 1 < count), "verdaux count mismatch")
                position += anext
            require(bool(nxt) == (index + 1 < s[7]), "verdef count mismatch")
            offset += nxt
    count = sy[5] // 24
    require(count <= 200000, "symbol budget")
    vs = named.get(".gnu.version")
    if vs:
        require(
            vs[5] == count * 2
            and vs[6] < len(sections)
            and sections[vs[6]] == sy
            and dynamics.get(0x6FFFFFF0) == [vs[3]],
            "versym linkage/count",
        )
    else:
        require(not versions and not definitions and 0x6FFFFFF0 not in dynamics, "missing versym")
    imports = []
    for i in range(count):
        name, _, _, index, _, _ = struct.unpack_from("<IBBHQQ", data, sy[4] + 24 * i)
        text = ref(name, "symbol")
        vi = struct.unpack_from("<H", data, vs[4] + i * 2)[0] & 0x7FFF if vs else 0
        require(vi <= 1 or vi in versions or vi in definitions, "unknown symbol version index")
        if index == 0 and vi > 1:
            require(vi in versions, "undefined symbol uses definition index")
            imports.append({"symbol": text, "symbolIndex": i, **versions[vi]})
    return {
        "needed": needed,
        "soname": soname,
        "versionNeeds": needs,
        "imports": imports,
        "references": refs,
        "dynstrOffset": ds[4],
        "dynstrSize": ds[5],
    }


def restore_loader(data):
    before = elf(data)
    require(
        before["needed"].count(OLD) == 1 and SYSTEM not in before["needed"],
        "expected one renamed loader",
    )
    imports = [x for x in before["imports"] if x["provider"] == OLD]
    require(
        len(imports) == 1
        and imports[0]["symbol"] == "__stack_chk_guard"
        and imports[0]["version"] == "GLIBC_2.17",
        "unexpected loader symbol/ABI",
    )
    requirements = [x for x in before["versionNeeds"] if x["provider"] == OLD]
    require(
        len(requirements) == 1 and requirements[0]["version"] == "GLIBC_2.17",
        "unexpected loader version requirements",
    )
    refs = before["references"]
    selected = [r for r in refs if r["text"] == OLD]
    require(
        {r["role"] for r in selected} == {"needed", "verneed-provider"},
        "loader name has non-provider reference",
    )
    offsets = sorted({r["offset"] for r in selected})
    for offset in offsets:
        require(
            not any(
                r["offset"] != offset
                and r["offset"] < offset + len(OLD) + 1
                and offset < r["offset"] + len(r["text"].encode()) + 1
                for r in refs
            ),
            "loader string suffix sharing",
        )
    table = data[before["dynstrOffset"] : before["dynstrOffset"] + before["dynstrSize"]]
    occurrences = [m.start() for m in re.finditer(re.escape(OLD.encode() + b"\0"), table)]
    require(occurrences == offsets, "unaccounted loader string")
    output, patches = bytearray(data), []
    replacement = SYSTEM.encode() + b"\0" * (len(OLD) + 1 - len(SYSTEM))
    for offset in offsets:
        start = before["dynstrOffset"] + offset
        output[start : start + len(replacement)] = replacement
        patches.append(
            {"offset": start, "length": len(replacement), "before": OLD, "after": SYSTEM}
        )
    result = bytes(output)
    after = elf(result)
    # All parsed indices, sections, symbols and references must remain identical
    # except these two filename roles. All other bytes are untouched by construction.
    expected = json.loads(json.dumps(before).replace('"' + OLD + '"', '"' + SYSTEM + '"'))
    require(after == expected and len(result) == len(data), "ELF transformation invariant")
    return result, {
        "patches": patches,
        "importsBefore": imports,
        "importsAfter": [x for x in after["imports"] if x["provider"] == SYSTEM],
    }


def record_bytes(files):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name in sorted(files):
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
        )
        writer.writerow((name, "sha256=" + digest, len(files[name])))
    writer.writerow((DIST_INFO + "/RECORD", "", ""))
    return stream.getvalue().encode()


def check_record(files):
    record = DIST_INFO + "/RECORD"
    require(record in files, "missing RECORD")
    rows = list(csv.reader(io.StringIO(files[record].decode())))
    require(all(len(r) == 3 for r in rows), "invalid RECORD row")
    require(len(rows) == len(files) and len({r[0] for r in rows}) == len(rows), "RECORD coverage")
    for name, digest, size in rows:
        require(name in files, "unknown RECORD member")
        if name == record:
            require(digest == size == "", "RECORD self hash")
        else:
            actual = (
                base64.urlsafe_b64encode(hashlib.sha256(files[name]).digest()).rstrip(b"=").decode()
            )
            require(
                digest == "sha256=" + actual and size == str(len(files[name])), "RECORD mismatch"
            )


def closure(parsed, removed=False):
    basenames = {PurePosixPath(n).name: n for n in parsed}
    require(len(basenames) == len(parsed), "duplicate native basename")
    sonames = {}
    external = []
    for name, info in parsed.items():
        for soname in info["soname"]:
            require(
                soname not in sonames and (soname not in basenames or basenames[soname] == name),
                "SONAME collision",
            )
            sonames[soname] = name
        for needed in info["needed"]:
            require("/" not in needed and "$" not in needed, "external dependency path")
            require(not removed or needed != OLD, "remaining bundled loader reference")
            if needed not in basenames:
                require(needed in EXTERNAL, "unrecognized external dependency")
                external.append(
                    {
                        "member": name,
                        "name": needed,
                        "role": "system-loader" if needed == SYSTEM else "unverified-external",
                    }
                )
    return external


def prepare(source: Path, output_directory: Path):
    started = time.monotonic()

    def deadline():
        require(time.monotonic() - started <= MAX_SECONDS, "preparation time budget")

    require(
        not source.is_symlink() and source.is_file() and source.name == INPUT_NAME,
        "input name/type",
    )
    require(source.stat().st_size <= MAX_ARCHIVE, "input byte budget")
    original = source.read_bytes()
    require(sha(original) == INPUT_SHA256, "input SHA mismatch")
    require(not output_directory.exists(), "output already exists")
    files, total = {}, 0
    with zipfile.ZipFile(io.BytesIO(original)) as archive:
        infos = archive.infolist()
        require(
            len(infos) <= MAX_MEMBERS and len({i.filename for i in infos}) == len(infos),
            "ZIP member count/duplicate",
        )
        for info in infos:
            deadline()
            path = PurePosixPath(info.filename)
            require(
                not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in info.filename
                and str(path) == info.filename.rstrip("/"),
                "unsafe ZIP path",
            )
            mode = stat.S_IFMT(info.external_attr >> 16)
            require(
                mode in (0, stat.S_IFDIR, stat.S_IFREG) and not info.flag_bits & 1,
                "ZIP special/encrypted member",
            )
            require(0 <= info.file_size <= MAX_MEMBER, "member byte budget")
            total += info.file_size
            require(total <= MAX_DECODED, "decoded byte budget")
            if info.is_dir():
                require(info.file_size == 0, "nonempty directory")
                continue
            files[info.filename] = archive.read(info)
    require(
        not any(n.endswith(("/RECORD.jws", "/RECORD.p7s")) for n in files),
        "signed wheel unsupported",
    )
    check_record(files)
    require(sha(files.get(LOADER, b"")) == LOADER_SHA256, "loader SHA mismatch")
    require(
        len(CODECS) == 5 and all(sha(files.get(n, b"")) == digest for n, digest in CODECS.items()),
        "five codec SHA mismatch",
    )
    require(files.get(DIST_INFO + "/LICENSE", b"").strip(), "missing upstream LICENSE")
    require(
        DIST_INFO + "/METADATA" in files and DIST_INFO + "/WHEEL" in files, "missing wheel metadata"
    )
    metadata = files[DIST_INFO + "/METADATA"].decode()
    require(
        re.findall(r"^Name: (.+)$", metadata, re.M) == ["torchvision"]
        and re.findall(r"^Version: (.+)$", metadata, re.M) == ["0.29.0+cpu"],
        "package identity",
    )
    wheel_path = DIST_INFO + "/WHEEL"
    wheel = files[wheel_path].decode()
    require(
        not re.search(r"^Build:", wheel, re.M)
        and re.findall(r"^Tag: (.+)$", wheel, re.M) == ["cp312-cp312-manylinux_2_28_aarch64"],
        "wheel build/tag mismatch",
    )
    parsed = {n: elf(data) for n, data in files.items() if data.startswith(b"\x7fELF")}
    require(
        {n for n, info in parsed.items() if OLD in info["needed"]} == set(CODECS),
        "unexpected loader consumer set",
    )
    require(
        parsed[LOADER]["soname"] == [SYSTEM] and parsed[LOADER]["needed"] == [], "loader identity"
    )
    before_external = closure(parsed)
    changes = []
    original_files = dict(files)
    for name in sorted(CODECS):
        deadline()
        files[name], change = restore_loader(files[name])
        changes.append(
            {
                "member": name,
                "beforeSHA256": CODECS[name],
                "afterSHA256": sha(files[name]),
                **change,
            }
        )
    del files[LOADER]
    after = {n: elf(data) for n, data in files.items() if data.startswith(b"\x7fELF")}
    after_external = closure(after, removed=True)
    require(
        not any(OLD in r["text"] for info in after.values() for r in info["references"]),
        "remaining dynamic loader text",
    )
    files[wheel_path] = (wheel.rstrip("\n") + f"\nBuild: {BUILD_TAG}\n").encode()
    notice_path = DIST_INFO + "/DOCUMENT_FILES_DERIVATION_NOTICE.txt"
    require(notice_path not in files, "derivation notice collision")
    notice = (
        f"Document Files explicit derived candidate, recipe {RECIPE}.\n"
        f"Original wheel: {INPUT_NAME}\nSHA256: {INPUT_SHA256}\n"
        f"Build tag: {BUILD_TAG}; upstream version/requirements unchanged.\n"
        "Five codec dependency/provider names restored to the system ARM glibc loader.\n"
        f"Bundled loader omitted only in this derivative: {LOADER}\nSHA256: {LOADER_SHA256}\n"
        "Original license files retained byte-for-byte; this notice does not replace them.\n"
        "Upstream source references (not reproduction receipts):\n"
        + "\n".join(SOURCES.values())
        + "\n"
        "Pending: exact codec corresponding sources, component notices and redistribution review.\n"
        "No runtime/ABI-load, codec, security, license or recognition-stage approval.\n"
    )
    files[notice_path] = notice.encode()
    record = DIST_INFO + "/RECORD"
    files.pop(record)
    files[record] = record_bytes(files)
    check_record(files)
    unchanged = {
        n: sha(data)
        for n, data in original_files.items()
        if n not in {*CODECS, LOADER, wheel_path, record}
    }
    require(
        all(sha(files[n]) == digest for n, digest in unchanged.items()), "unrelated member changed"
    )
    licenses = {
        n: digest
        for n, digest in unchanged.items()
        if any(word in PurePosixPath(n).name.upper() for word in ("LICENSE", "COPYING", "NOTICE"))
    }
    require(sha(source.read_bytes()) == INPUT_SHA256, "source changed during preparation")
    deadline()
    report = {
        "schemaVersion": "document-files.derived-torchvision-wheel.v1",
        "recipe": RECIPE,
        "recipeSHA256": sha(Path(__file__).read_bytes()),
        "sourceReferences": SOURCES,
        "input": {"name": INPUT_NAME, "sha256": INPUT_SHA256, "size": len(original)},
        "buildTag": BUILD_TAG,
        "changes": changes,
        "removed": {"member": LOADER, "sha256": LOADER_SHA256},
        "unchangedMembers": unchanged,
        "preservedLicenseMembers": licenses,
        "externalDependenciesBefore": before_external,
        "externalDependenciesAfter": after_external,
        "licenseReview": "pending-component-corresponding-sources-and-notices",
        "targetExecution": "not-performed",
        "stageApproved": False,
        "releaseQualified": False,
        "limits": {
            "archiveBytes": MAX_ARCHIVE,
            "memberBytes": MAX_MEMBER,
            "decodedBytes": MAX_DECODED,
            "members": MAX_MEMBERS,
            "seconds": MAX_SECONDS,
            "deadlineEnforcement": "between-bounded-IO-operations",
            "retries": 0,
        },
        "usage": {"decodedBytes": total, "members": len(files)},
    }
    output_directory.mkdir(parents=False)
    output = output_directory / OUTPUT_NAME
    receipt = output_directory / "derivation.json"
    try:
        with zipfile.ZipFile(
            output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name in sorted(files):
                deadline()
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, files[name])
        deadline()
        require(output.stat().st_size <= MAX_ARCHIVE, "output byte budget")
        report["output"] = {
            "name": OUTPUT_NAME,
            "size": output.stat().st_size,
            "sha256": sha(output.read_bytes()),
        }
        report["usage"]["elapsedSeconds"] = round(time.monotonic() - started, 6)
        receipt.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    except BaseException:
        output.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)
        output_directory.rmdir()
        raise
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source, args.output_directory)
    print(json.dumps({"output": result["output"], "stageApproved": False}, sort_keys=True))


if __name__ == "__main__":
    main()
