#!/usr/bin/env python3
"""Assemble a fresh, unapproved Linux recognition stage from explicit offline inputs.

This is neither a pack verifier nor a license, network-isolation or runtime certificate.
Run inside separately enforced isolation. --check-only never executes supplied code.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import platform
import re
import selectors
import signal
import stat
import struct
import subprocess
import tarfile
import time
import unicodedata
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

SCHEMA = "document-files.linux-recognition-assembly.v1"
LIMITS = (
    "maxInputBytes",
    "maxArchiveMembers",
    "maxExpandedBytes",
    "maxOutputFiles",
    "maxOutputBytes",
    "maxSeconds",
    "maxLogBytes",
)


class AssemblyError(ValueError):
    pass


def require(value, code):
    if not value:
        raise AssemblyError(code)


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value, "invalid_relative_path")
    require(not value.startswith("/") and "\x00" not in value, "invalid_relative_path")
    parts = value.split("/")
    require(all(p not in ("", ".", "..") for p in parts), "invalid_relative_path")
    require(not re.match(r"^[A-Za-z]:", value), "invalid_relative_path")
    return value


def regular(root, name):
    path = root / relative(name)
    require(not any(p.is_symlink() for p in (path, *path.parents)), "input_symlink")
    require(path.is_file(), "input_not_regular")
    return path


def digest_file(path, budget=None, *, staged=False):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(1024 * 1024):
            if budget:
                budget.tick()
                if staged:
                    budget.verification_read += len(chunk)
                    require(
                        budget.verification_read <= budget.limits["maxOutputBytes"] * 4,
                        "verification_read_budget",
                    )
                else:
                    budget.source_read += len(chunk)
                    require(
                        budget.source_read <= budget.limits["maxInputBytes"] * 3,
                        "source_read_budget",
                    )
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        # Reopen the path to detect replacement, but compare handle metadata with
        # handle metadata. Windows stat(path) and fstat(fd) need not expose identical
        # timestamp/identity representations. No field of the mutation guard is dropped.
        with path.open("rb") as current_stream:
            current = os.fstat(current_stream.fileno())

        def signature(st):
            return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

        require(
            signature(before) == signature(after) == signature(current),
            "input_changed_while_hashing",
        )
    return digest.hexdigest()


def load(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "duplicate_json_key")
            value[key] = item
        return value

    require(path.stat().st_size <= 16 * 1024**2, "manifest_size_budget")
    return json.loads(path.read_text(), object_pairs_hook=unique)


class Budget:
    def __init__(self, limits):
        require(isinstance(limits, dict) and set(limits) == set(LIMITS), "invalid_limits")
        require(all(type(v) is int and v > 0 for v in limits.values()), "invalid_limits")
        self.limits = limits
        self.started = time.monotonic()
        self.members = self.expanded = self.output_bytes = self.output_files = self.source_read = 0
        self.verification_read = 0

    def tick(self):
        require(time.monotonic() - self.started < self.limits["maxSeconds"], "deadline_exceeded")

    def member(self):
        self.tick()
        self.members += 1
        require(self.members <= self.limits["maxArchiveMembers"], "member_budget")

    def decoded(self, size):
        self.tick()
        self.expanded += size
        require(self.expanded <= self.limits["maxExpandedBytes"], "expanded_byte_budget")

    def output(self, size):
        self.tick()
        self.output_files += 1
        self.output_bytes += size
        require(self.output_files <= self.limits["maxOutputFiles"], "output_file_budget")
        require(self.output_bytes <= self.limits["maxOutputBytes"], "output_byte_budget")

    def usage(self):
        return {
            "sourceReadBytes": self.source_read,
            "verificationReadBytes": self.verification_read,
            "decodedBytes": self.expanded,
            "archiveMembers": self.members,
            "stageFiles": self.output_files,
            "stageBytes": self.output_bytes,
            "elapsedSeconds": time.monotonic() - self.started,
        }


def copy_stream(stream, *, budget, destination=None):
    digest, size, prefix = hashlib.sha256(), 0, b""
    out = destination.open("xb") if destination else None
    try:
        while chunk := stream.read(1024 * 1024):
            budget.decoded(len(chunk))
            digest.update(chunk)
            size += len(chunk)
            prefix = (prefix + chunk)[:256]
            if out:
                out.write(chunk)
    finally:
        if out:
            out.close()
    return size, digest.hexdigest(), prefix


def absolute_script(prefix, mode):
    return bool(mode & 0o111 and prefix.startswith(b"#!/"))


def source_shebang(prefix, mode):
    """Describe an immutable upstream script, not an assembler-selected entrypoint.

    Its original interpreter path is not an installer-generated portability promise.
    Selected PBS/asset executables are validated separately; wheel .data/scripts
    are preserved separately without advertising a runnable CLI. Never rewrite fixed
    source bytes merely because a shebang exists.
    """
    if not prefix.startswith(b"#!"):
        return None
    return {
        "disposition": "not-selected-entrypoint",
        "standalonePortability": "unverified",
        "firstLinePrefixHex": prefix.split(b"\n", 1)[0].hex(),
        "prefixTruncated": b"\n" not in prefix,
        "originalExecutableBits": mode & 0o111,
        "sourceBytesAndModePreserved": True,
    }


def omitted(name, omissions):
    for rule in omissions:
        if name == rule["path"] or rule["recursive"] and name.startswith(rule["path"] + "/"):
            return rule["reason"]
    return None


def resolve_link(name, members):
    chain, seen = [], set()
    while members[name]["kind"] in ("symlink", "hardlink"):
        require(name not in seen and len(chain) < 64, "archive_link_cycle")
        seen.add(name)
        row = members[name]
        target = row["link"]
        require(
            target and not target.startswith("/") and "\\" not in target, "archive_link_absolute"
        )
        parts = name.split("/")[:-1] if row["kind"] == "symlink" else []
        for part in target.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                require(parts, "archive_link_escape")
                parts.pop()
            else:
                require("\x00" not in part and ":" not in part, "archive_link_invalid")
                parts.append(part)
        resolved = "/".join(parts)
        require(resolved in members, "archive_link_dangling")
        chain.append({"member": name, "target": target, "resolvedMember": resolved})
        name = resolved
    require(members[name]["kind"] == "file", "archive_link_not_regular")
    return name, chain


def tar_inventory(path, omissions, budget):
    members = {}
    with tarfile.open(path, "r|*") as archive:
        for entry in archive:
            budget.member()
            name = relative(entry.name.rstrip("/"))
            require(name not in members, "duplicate_archive_member")
            require(name == "python" or name.startswith("python/"), "pbs_archive_root")
            kind = (
                "file"
                if entry.isfile()
                else "dir"
                if entry.isdir()
                else "symlink"
                if entry.issym()
                else "hardlink"
                if entry.islnk()
                else "special"
            )
            require(kind != "special", "archive_special_file")
            row = {
                "kind": kind,
                "size": entry.size,
                "mode": entry.mode & 0o777,
                "link": entry.linkname,
                "omission": omitted(name, omissions),
            }
            require(not entry.mode & 0o7000, "archive_privileged_mode")
            if kind == "file":
                size, sha, prefix = copy_stream(archive.extractfile(entry), budget=budget)
                require(size == entry.size, "truncated_archive_member")

                row.update(sha256=sha)
                preserved = source_shebang(prefix, row["mode"])
                if preserved:
                    row["originalShebang"] = preserved
            if name == "python/bin/python3.12" and kind == "file":
                row["elfPrefix"] = prefix[:20].hex()
            members[name] = row
    for name, row in members.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in members:
                require(members[str(parent)]["kind"] == "dir", "archive_parent_not_directory")
        if row["kind"] in ("symlink", "hardlink"):
            target, chain = resolve_link(name, members)
            row.update(
                resolvedMember=target,
                linkChain=chain,
                resolvedSha256=members[target]["sha256"],
                resolvedSize=members[target]["size"],
            )
            require(row["omission"] or not members[target]["omission"], "link_to_omitted_member")
    for rule in omissions:
        require(any(omitted(n, [rule]) for n in members), "unused_python_omission")
    return members


def package_name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value), "invalid_package")
    return re.sub(r"[-_.]+", "-", value).lower()


def wheel_destination(name, distribution):
    first, *rest = name.split("/")
    if first.endswith(".data"):
        require(len(rest) >= 2, "unsupported_wheel_data")
        require(
            first == distribution.removesuffix(".dist-info") + ".data",
            "wheel_data_distribution_mismatch",
        )
        if rest[0] == "scripts":
            require(
                first == distribution.removesuffix(".dist-info") + ".data",
                "wheel_script_distribution_mismatch",
            )
            return relative(
                ".document-files-wheel-scripts/" + distribution + "/" + "/".join(rest[1:])
            )
        require(rest[0] in {"purelib", "platlib", "data"}, "unsupported_wheel_data")
        return relative("/".join(rest[1:]))
    return name


def wheel_inventory(path, wheel, budget):
    with zipfile.ZipFile(path) as archive:
        infos, rows = {}, {}
        for entry in archive.infolist():
            budget.member()
            name = relative(entry.filename.rstrip("/"))
            require(name not in infos, "duplicate_wheel_member")
            mode = entry.external_attr >> 16
            require(
                not mode & 0o7000 and stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                "wheel_nonregular_member",
            )
            infos[name] = entry
        files = {n for n, e in infos.items() if not e.is_dir()}
        records = [n for n in files if len(n.split("/")) == 2 and n.endswith(".dist-info/RECORD")]
        metas = [n for n in files if len(n.split("/")) == 2 and n.endswith(".dist-info/METADATA")]
        require(len(records) == len(metas) == 1, "wheel_metadata_count")
        record, meta = records[0], metas[0]
        require(
            record.rsplit("/", 1)[0] == meta.rsplit("/", 1)[0]
            and record.rsplit("/", 1)[0] + "/WHEEL" in files,
            "wheel_metadata_root",
        )
        require(
            infos[record].file_size <= 16 * 1024**2 and infos[meta].file_size <= 2 * 1024**2,
            "wheel_metadata_budget",
        )
        raw = archive.read(record)
        budget.decoded(len(raw))
        for row in csv.reader(io.StringIO(raw.decode())):
            require(len(row) == 3, "invalid_wheel_record")
            name = relative(row[0])
            require(name not in rows, "duplicate_wheel_record")
            rows[name] = row[1:]
        require(set(rows) == files, "incomplete_wheel_record")
        data = archive.read(meta)
        budget.decoded(len(data))
        metadata = Parser().parsestr(data.decode())
        require(
            package_name(metadata["Name"]) == package_name(wheel["name"])
            and metadata["Version"] == wheel["version"],
            "wheel_identity_mismatch",
        )
        expected = {}
        for name in sorted(files):
            size, sha, prefix = copy_stream(archive.open(infos[name]), budget=budget)
            require(size == infos[name].file_size, "truncated_wheel_member")
            if name == record:
                require(rows[name] == ["", ""], "invalid_record_self_entry")
            else:
                encoded = base64.urlsafe_b64encode(bytes.fromhex(sha)).rstrip(b"=").decode()
                require(rows[name] == ["sha256=" + encoded, str(size)], "wheel_record_mismatch")
            distribution = record.rsplit("/", 1)[0]
            destination = wheel_destination(name, distribution)
            mode = (infos[name].external_attr >> 16) & 0o777 or 0o644

            require(destination not in expected, "wheel_destination_collision")
            if destination is not None:
                expected[destination] = {
                    "member": name,
                    "sha256": sha,
                    "size": size,
                    "sourceSha256": wheel["sha256"],
                    "mode": mode,
                    "record": name == record,
                }
                if name.startswith(distribution.removesuffix(".dist-info") + ".data/scripts/"):
                    expected[destination]["providedScript"] = name.split(".data/scripts/", 1)[1]
                    expected[destination]["standalonePortability"] = "unverified"
                    expected[destination]["selectedEntrypoint"] = False
                if name.startswith(distribution.removesuffix(".dist-info") + ".data/data/"):
                    expected[destination].update(
                        dataScheme=True,
                        installedRecordPath="../../" + destination,
                        stageDestination="python/" + destination,
                        finalRecordPath="../../../" + destination,
                        installationRelativePath=destination,
                    )
                preserved = source_shebang(prefix, mode)
                if preserved:
                    expected[destination]["originalShebang"] = preserved
        return expected, record.rsplit("/", 1)[0]


def preflight(manifest, root, budget):
    require(manifest.get("schemaVersion") == SCHEMA, "invalid_schema")
    require(manifest.get("platform") in {"linux-x86_64", "linux-aarch64"}, "invalid_platform")
    require(manifest.get("installedScriptPolicy") == "omit-generated-bin", "script_policy_required")
    omissions = manifest.get("pythonOmissions", [])
    for rule in omissions:
        relative(rule["path"])
        require(
            type(rule["recursive"]) is bool
            and isinstance(rule["reason"], str)
            and rule["reason"].strip(),
            "invalid_python_omission",
        )
    inputs = [manifest["pythonRuntime"], *manifest["wheels"], *manifest["assets"]]
    require(inputs and len(inputs) <= budget.limits["maxOutputFiles"], "input_file_budget")
    require(
        sum(i["size"] for i in inputs if type(i.get("size")) is int)
        <= budget.limits["maxInputBytes"],
        "input_byte_budget",
    )
    seen = set()
    for item in inputs:
        require(
            type(item.get("size")) is int
            and item["size"] >= 0
            and re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")),
            "invalid_input_identity",
        )
        require(item["path"] not in seen, "duplicate_input_path")
        seen.add(item["path"])
        path = regular(root, item["path"])
        require(
            path.stat().st_size == item["size"] and digest_file(path, budget) == item["sha256"],
            "input_identity_mismatch",
        )
    runtime = manifest["pythonRuntime"]
    members = tar_inventory(regular(root, runtime["path"]), omissions, budget)
    executable = relative(runtime["executable"])
    require(executable == "python/bin/python3.12", "unsupported_python_layout")
    require(
        executable in members
        and members[executable]["kind"] == "file"
        and members[executable]["mode"] & 0o111
        and not members[executable]["omission"],
        "missing_python_executable",
    )
    elf = bytes.fromhex(members[executable].get("elfPrefix", ""))
    require(
        len(elf) == 20
        and elf[:7] == b"\x7fELF\x02\x01\x01"
        and struct.unpack_from("<H", elf, 18)[0]
        == (183 if manifest["platform"] == "linux-aarch64" else 62),
        "python_elf_target_mismatch",
    )
    expected, distributions, wheel_names = {}, {}, set()
    for wheel in manifest["wheels"]:
        name = package_name(wheel["name"])
        require(
            name not in wheel_names and re.fullmatch(r"[A-Za-z0-9.+!_-]+", wheel["version"]),
            "duplicate_or_invalid_package",
        )
        wheel_names.add(name)
        rows, dist = wheel_inventory(regular(root, wheel["path"]), wheel, budget)
        require(not set(expected) & set(rows), "wheel_file_collision")
        expected.update(rows)
        distributions[dist] = wheel["sha256"]
    script_names = [row["providedScript"] for row in expected.values() if "providedScript" in row]
    require(len(script_names) == len(set(script_names)), "wheel_provided_script_collision")
    require(
        not any("bin/" + name in expected for name in script_names),
        "wheel_script_installation_collision",
    )
    for name, row in expected.items():
        require(
            not name.startswith(".document-files-wheel-scripts/") or "providedScript" in row,
            "reserved_script_source_namespace",
        )
    pip = [
        w
        for w in manifest["wheels"]
        if w["sha256"] == manifest.get("pipWheelSha256") and package_name(w["name"]) == "pip"
    ]
    require(len(pip) == 1, "missing_pinned_pip")
    # Installing into this location must not retain any bundled distribution residue.
    site = "python/lib/python3.12/site-packages"
    require(
        all(row["omission"] for n, row in members.items() if n == site or n.startswith(site + "/")),
        "pbs_site_packages_not_omitted",
    )
    destinations = {n for n, row in members.items() if row["kind"] != "dir" and not row["omission"]}
    wheel_destinations = [
        row.get("stageDestination", site + "/" + n) for n, row in expected.items()
    ]
    require(
        len(set(wheel_destinations)) == len(wheel_destinations)
        and not destinations & set(wheel_destinations),
        "wheel_pbs_destination_collision",
    )
    destinations.update(wheel_destinations)
    data_roots = {n.split("/", 1)[0] for n, row in expected.items() if row.get("dataScheme")}
    library_roots = {n.split("/", 1)[0] for n, row in expected.items() if not row.get("dataScheme")}
    require(not data_roots & library_roots, "wheel_data_target_directory_collision")
    require("lib" not in data_roots, "pip_target_skips_library_data_directory")
    for asset in manifest["assets"]:
        dest = relative(asset["destination"])
        require(type(asset.get("executable")) is bool, "asset_executable_required")
        if asset["executable"]:
            require(asset.get("role") == "native", "unsupported_executable_asset_role")
            with regular(root, asset["path"]).open("rb") as stream:
                prefix = stream.read(256)
            budget.decoded(len(prefix))
            require(not absolute_script(prefix, 0o755), "executable_asset_absolute_shebang")
            require(
                len(prefix) >= 20
                and prefix[:7] == b"\x7fELF\x02\x01\x01"
                and struct.unpack_from("<H", prefix, 18)[0]
                == (183 if manifest["platform"] == "linux-aarch64" else 62),
                "native_asset_elf_target_mismatch",
            )
        require(
            asset.get("role") and isinstance(asset.get("provenance"), dict) and asset["provenance"],
            "asset_provenance_required",
        )
        require(dest not in destinations, "asset_collision")
        destinations.add(dest)
    require(
        len({unicodedata.normalize("NFC", name).casefold() for name in destinations})
        == len(destinations),
        "pack_destination_casefold_collision",
    )
    for dest in destinations:
        require(
            not any(str(parent) in destinations for parent in PurePosixPath(dest).parents),
            "destination_parent_collision",
        )
    return members, expected, distributions, site, pip[0]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def put(stage, name, source, row, budget, origins):
    destination = stage / relative(name)
    require(not destination.exists() and not destination.is_symlink(), "stage_collision")
    budget.output(row["size"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream:
        size, sha, _ = copy_stream(stream, budget=budget, destination=destination)
    require(size == row["size"] and sha == row["sha256"], "copy_source_changed")
    destination.chmod(row.get("mode", 0o644) & 0o777)
    origins[name] = {**row, "size": size, "sha256": sha}


def extract_python(path, members, stage, budget, origins, source_sha):
    seen = set()
    with tarfile.open(path, "r|*") as archive:
        for entry in archive:
            budget.tick()
            name = entry.name.rstrip("/")
            require(name in members and name not in seen, "python_archive_members_changed")
            seen.add(name)
            row = members[name]
            if row["kind"] != "file" or row["omission"]:
                continue
            budget.output(row["size"])
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            size, sha, _ = copy_stream(
                archive.extractfile(entry), budget=budget, destination=target
            )
            require(size == row["size"] and sha == row["sha256"], "python_member_changed")
            target.chmod(row["mode"])
            origins[name] = {
                "size": size,
                "sha256": sha,
                "mode": row["mode"],
                "sourceSha256": source_sha,
                "member": name,
            }
            if "originalShebang" in row:
                origins[name]["originalShebang"] = row["originalShebang"]
    require(seen == set(members), "python_archive_members_changed")
    for name, row in members.items():
        if row["kind"] in ("symlink", "hardlink") and not row["omission"]:
            target = row["resolvedMember"]
            origin = {
                **origins[target],
                "member": name,
                "linkChain": row["linkChain"],
                "resolvedMember": target,
                "transformation": "regular-copy-of-archive-link",
            }
            put(stage, name, stage / target, origin, budget, origins)


def run_pip(command, env, log, budget):
    """One child group; bounded draining prevents pipe deadlock and unbounded log files."""
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
    )
    total = 0
    started = time.monotonic()
    original_error = original_traceback = result = None
    try:
        with selectors.DefaultSelector() as selector, log.open("xb") as output:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                budget.tick()
                # Observed growth bounds supplement, not replace, container filesystem quotas.
                observed_bytes = observed_files = 0
                for folder in (Path(env["TMPDIR"]), Path(env["TMPDIR"]).parent / "installed"):
                    for candidate in folder.rglob("*"):
                        budget.tick()
                        require(not candidate.is_symlink(), "pip_output_symlink")
                        if candidate.is_file():
                            observed_files += 1
                            try:
                                observed_bytes += candidate.stat().st_size
                            except FileNotFoundError:
                                continue
                            require(
                                observed_files <= budget.limits["maxOutputFiles"]
                                and observed_bytes <= budget.limits["maxOutputBytes"],
                                "pip_output_budget",
                            )
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = budget.limits["maxLogBytes"] - total
                    output.write(chunk[: max(0, remaining)])
                    total += len(chunk)
                    require(total <= budget.limits["maxLogBytes"], "pip_log_budget")
            code = process.wait(
                timeout=max(0.01, budget.limits["maxSeconds"] - (time.monotonic() - budget.started))
            )
            require(code == 0, "pip_failed:" + str(code))
            result = {"exitCode": code, "logBytes": total, "attempts": 1}
    except BaseException as exc:
        original_error, original_traceback = exc, exc.__traceback__

    # Each cleanup boundary is independent: denied group signalling must not hide
    # the original failure, skip reaping/closing, or manufacture group absence.
    cleanup_errors = []
    cleanup = {
        "groupKill": "not_attempted",
        "ownedChildPoll": "not_needed",
        "ownedChildKill": "not_needed",
        "wait": "not_attempted",
        "stdoutClose": "not_attempted",
        "groupProbe": "not_attempted",
    }

    def failed(stage, error):
        cleanup[stage] = "failed"
        cleanup_errors.append(
            {"stage": stage, "type": type(error).__name__, "errno": getattr(error, "errno", None)}
        )

    try:
        os.killpg(process.pid, signal.SIGKILL)
        cleanup["groupKill"] = "sent"
    except ProcessLookupError:
        cleanup["groupKill"] = "absent"
    except (Exception, KeyboardInterrupt) as exc:
        failed("groupKill", exc)
        # This Popen child is the only fallback target. Its exit never proves
        # that the original process group (which may own descendants) is absent.
        try:
            alive = process.poll() is None
            cleanup["ownedChildPoll"] = "alive" if alive else "exited"
            if alive:
                try:
                    process.kill()
                    cleanup["ownedChildKill"] = "sent"
                except ProcessLookupError:
                    cleanup["ownedChildKill"] = "absent"
                except (Exception, KeyboardInterrupt) as child_error:
                    failed("ownedChildKill", child_error)
        except (Exception, KeyboardInterrupt) as poll_error:
            failed("ownedChildPoll", poll_error)
    try:
        process.wait(timeout=5)
        cleanup["wait"] = "completed"
    except (Exception, KeyboardInterrupt) as exc:
        failed("wait", exc)
    try:
        process.stdout.close()
        cleanup["stdoutClose"] = "closed"
    except (Exception, KeyboardInterrupt) as exc:
        failed("stdoutClose", exc)
    try:
        os.killpg(process.pid, 0)
        cleanup["groupProbe"] = "present"
    except ProcessLookupError:
        cleanup["groupProbe"] = "absent"
    except (Exception, KeyboardInterrupt) as exc:
        failed("groupProbe", exc)
    confirmed = (
        not cleanup_errors
        and cleanup["wait"] == "completed"
        and cleanup["stdoutClose"] == "closed"
        and cleanup["groupProbe"] == "absent"
    )
    receipt = {
        "attempts": 1,
        "pid": process.pid,
        "exitCode": process.returncode,
        "elapsedSeconds": time.monotonic() - started,
        "logBytesObserved": total,
        "logBytesPreserved": min(total, budget.limits["maxLogBytes"]),
        "processGroupKillSent": cleanup["groupKill"] == "sent",
        "processGroupAbsent": cleanup["groupProbe"] == "absent",
        "cleanupWaitSecondsMaximum": 5,
        "cleanupConfirmed": confirmed,
        "cleanupSteps": cleanup,
        "cleanupErrors": cleanup_errors,
        "originalError": (
            {
                "type": type(original_error).__name__,
                "code": str(original_error) if isinstance(original_error, AssemblyError) else None,
            }
            if original_error is not None
            else None
        ),
        "networkIsolationVerified": False,
    }
    receipt_error = None
    try:
        write_json(log.with_suffix(".process.json"), receipt)
    except (Exception, KeyboardInterrupt) as exc:
        receipt_error = exc
    if original_error is not None:
        if not confirmed or receipt_error is not None:
            original_error.add_note(
                "pip cleanup evidence: "
                + json.dumps(
                    {
                        "cleanupConfirmed": confirmed,
                        "cleanupErrors": cleanup_errors,
                        "receiptWriteError": type(receipt_error).__name__
                        if receipt_error
                        else None,
                    },
                    sort_keys=True,
                )
            )
        raise original_error.with_traceback(original_traceback)
    if receipt_error is not None:
        raise AssemblyError("pip_cleanup_receipt_failed") from receipt_error
    require(confirmed, "pip_cleanup_unconfirmed")
    return result


def preserve_wheel_scripts(target, expected, wheelhouse, wheels, budget):
    """Keep original CLI sources separately; never retain the temporary pip shebang."""
    for wheel in wheels:
        rows = {
            n: r
            for n, r in expected.items()
            if r["sourceSha256"] == wheel["sha256"] and "providedScript" in r
        }
        if not rows:
            continue
        with zipfile.ZipFile(wheelhouse / Path(wheel["path"]).name) as archive:
            for name, row in rows.items():
                dest = target / relative(name)
                require(not dest.exists() and not dest.is_symlink(), "preserved_script_collision")
                dest.parent.mkdir(parents=True, exist_ok=True)
                size, sha, _ = copy_stream(
                    archive.open(row["member"]), budget=budget, destination=dest
                )
                require(size == row["size"] and sha == row["sha256"], "original_script_changed")
                dest.chmod(row["mode"])


def script_installation_matches(original, installed, python, budget):
    """Only the wheel-standard #!python[ w] first-line rewrite is accepted."""
    with original.open("rb") as source, installed.open("rb") as output:
        first = source.readline(4097)
        require(len(first) <= 4096, "script_shebang_budget")
        wanted = (
            b"#!" + os.fsencode(python) + b"\n"
            if first.rstrip(b"\r\n") in (b"#!python", b"#!pythonw")
            else first
        )
        observed = output.read(len(wanted))
        budget.decoded(len(first) + len(observed))
        if observed != wanted:
            return False
        while True:
            left, right = source.read(1024 * 1024), output.read(1024 * 1024)
            budget.decoded(len(left) + len(right))
            if left != right:
                return False
            if not left:
                return True


def original_console_definitions(actual, expected, distributions, budget):
    """Definitions come only from byte-verified, top-level wheel entry_points.txt."""
    definitions = {}
    for distribution, digest in distributions.items():
        name = distribution + "/entry_points.txt"
        if name not in expected:
            continue
        source = expected[name]
        require(source["sourceSha256"] == digest and name in actual, "entrypoint_source_mismatch")
        require(actual[name].stat().st_size <= 2 * 1024**2, "entrypoint_metadata_budget")
        require(
            digest_file(actual[name], budget, staged=True) == source["sha256"],
            "entrypoint_source_mismatch",
        )
        parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
        parser.optionxform = str
        parser.read_string(actual[name].read_text())
        require(not parser.defaults(), "entrypoint_defaults_rejected")
        if not parser.has_section("console_scripts"):
            continue
        for script, target in parser.items("console_scripts"):
            require(
                re.fullmatch(r"[A-Za-z0-9_.-]+", script)
                and script not in (".", "..")
                and target.strip()
                and "\n" not in target,
                "invalid_console_definition",
            )
            definitions[(distribution, "bin/" + script)] = {
                "group": "console_scripts",
                "target": target.strip(),
                "sourceMember": source["member"],
                "sourceMemberSha256": source["sha256"],
                "sourceWheelSha256": digest,
            }
    return definitions


def installed_files(target, expected, distributions, budget, python=None):
    actual, generated, omitted_scripts = {}, {}, []
    total_files = total_bytes = 0
    for path in target.rglob("*"):
        budget.tick()
        require(not path.is_symlink() and (path.is_dir() or path.is_file()), "installed_nonregular")
        if not path.is_file():
            continue
        name = path.relative_to(target).as_posix()
        total_files += 1
        total_bytes += path.stat().st_size
        require(
            total_files <= budget.limits["maxOutputFiles"]
            and total_bytes <= budget.limits["maxOutputBytes"],
            "installed_output_budget",
        )
        require(path.stat().st_size <= budget.limits["maxOutputBytes"], "installed_size_budget")
        if name.startswith("bin/") and name not in expected:
            omitted_scripts.append(
                {
                    "path": name,
                    "size": path.stat().st_size,
                    "sha256": digest_file(path, budget, staged=True),
                    "reason": "omit-generated-bin",
                }
            )
            continue
        actual[name] = path
        require(len(actual) <= budget.limits["maxOutputFiles"], "installed_file_budget")
    allowed = set(expected)
    for dist in distributions:
        for suffix in ("INSTALLER", "REQUESTED"):
            name = dist + "/" + suffix
            if name not in expected and name in actual:
                require(
                    actual[name].stat().st_size == (4 if suffix == "INSTALLER" else 0)
                    and actual[name].read_bytes() == (b"pip\n" if suffix == "INSTALLER" else b""),
                    "unexpected_generated_metadata",
                )
                generated[name] = distributions[dist]
                allowed.add(name)
    require(set(actual) == allowed, "installed_inventory_mismatch")
    for name, row in expected.items():
        if not row["record"]:
            require(
                actual[name].stat().st_size == row["size"]
                and digest_file(actual[name], budget, staged=True) == row["sha256"],
                "installed_wheel_bytes_changed",
            )
    # Every installed RECORD row must describe an actual file owned by this wheel,
    # or a deliberately omitted generated console script. No arbitrary ../../ paths.
    definitions = original_console_definitions(actual, expected, distributions, budget)
    rewritten, script_owners = {}, {}
    provided = {}
    for name, value in expected.items():
        if "providedScript" in value:
            script = "bin/" + relative(value["providedScript"])
            require(script not in provided, "wheel_provided_script_collision")
            provided[script] = name
    for dist, source_sha in distributions.items():
        record = actual[dist + "/RECORD"]
        require(record.stat().st_size <= 16 * 1024**2, "installed_record_budget")
        seen, kept, removed, relocated = set(), [], [], []
        owned = {n for n, row in expected.items() if row["sourceSha256"] == source_sha}
        owned.update(n for n, sha in generated.items() if sha == source_sha)
        for row in csv.reader(io.StringIO(record.read_text())):
            require(len(row) == 3 and row[0] not in seen, "invalid_installed_record")
            seen.add(row[0])
            # pip 26.2.1 install.py:604-656 moves home scheme data into --target
            # without rewriting wheel.py's lib_dir-relative RECORD. In CPython
            # 3.12 posix_home, lib_dir=<home>/lib/python and data=<home>.
            data_matches = [
                (n, item)
                for n, item in expected.items()
                if item.get("dataScheme") and item["installedRecordPath"] == row[0]
            ]
            if data_matches:
                require(len(data_matches) == 1, "ambiguous_installed_data")
                name, item = data_matches[0]
                require(name in owned and name not in seen, "installed_data_foreign_file")
                seen.add(name)
                encoded = (
                    base64.urlsafe_b64encode(bytes.fromhex(item["sha256"])).rstrip(b"=").decode()
                )
                require(
                    row[1:] == ["sha256=" + encoded, str(item["size"])],
                    "installed_data_record_mutation",
                )
                require(
                    actual[name].stat().st_size == item["size"]
                    and digest_file(actual[name], budget, staged=True) == item["sha256"],
                    "installed_data_bytes_changed",
                )
                final_row = [item["finalRecordPath"], row[1], row[2]]
                kept.append(final_row)
                relocated.append(
                    {
                        "originalRow": row,
                        "finalRow": final_row,
                        "installationRelativePath": name,
                        "stageDestination": item["stageDestination"],
                    }
                )
                continue
            if row[0].startswith("../../bin/"):
                script = relative(row[0][6:])
                previous = script_owners.get(script, [])
                definition = definitions.get((dist, script))
                if previous:
                    require(script not in provided, "duplicate_provided_script_owner")
                    require(
                        definition is not None
                        and all(
                            owner["definition"] is not None
                            and owner["definition"]["group"] == definition["group"]
                            and owner["definition"]["target"] == definition["target"]
                            for owner in previous
                        ),
                        "shared_script_definition_mismatch",
                    )
                item = next((x for x in omitted_scripts if x["path"] == script), None)
                require(item is not None, "unknown_installed_script")
                encoded = (
                    base64.urlsafe_b64encode(bytes.fromhex(item["sha256"])).rstrip(b"=").decode()
                )
                require(
                    row[1:] == ["sha256=" + encoded, str(item["size"])],
                    "installed_script_record_mutation",
                )
                ownership = {
                    "distribution": dist,
                    "sourceWheelSha256": source_sha,
                    "recordRow": list(row),
                    "definition": definition,
                }
                script_owners.setdefault(script, []).append(ownership)
                item["owners"] = script_owners[script]
                item["sharedGeneratedConsoleScript"] = len(script_owners[script]) > 1
                if script in provided:
                    preserved = provided[script]
                    require(preserved in owned and python is not None, "script_owner_mismatch")
                    require(
                        script_installation_matches(
                            actual[preserved], target / script, python, budget
                        ),
                        "installed_script_changed",
                    )
                    original = expected[preserved]
                    digest = (
                        base64.urlsafe_b64encode(bytes.fromhex(original["sha256"]))
                        .rstrip(b"=")
                        .decode()
                    )
                    kept.append([preserved, "sha256=" + digest, str(original["size"])])
                    seen.add(preserved)
                    item.update(
                        reason="installer-script-replaced-by-preserved-upstream-source",
                        preservedSource=preserved,
                        selectedEntrypoint=False,
                        standalonePortability="unverified",
                    )
                removed.append(row)
                continue
            kept.append(row)
            name = relative(row[0])
            require(name in owned, "installed_record_foreign_file")
            require(
                not expected.get(name, {}).get("dataScheme"),
                "unexpected_installed_data_record_path",
            )
            if name == dist + "/RECORD":
                require(row[1:] == ["", ""], "installed_record_self_changed")
            else:
                size = actual[name].stat().st_size
                sha = digest_file(actual[name], budget, staged=True)
                encoded = base64.urlsafe_b64encode(bytes.fromhex(sha)).rstrip(b"=").decode()
                require(row[1:] == ["sha256=" + encoded, str(size)], "installed_record_mutation")
        require(owned <= seen, "installed_record_incomplete")
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="\n").writerows(kept)
        rewritten[dist + "/RECORD"] = {
            "bytes": buffer.getvalue().encode(),
            "originalInstalledRecordSha256": digest_file(record, budget, staged=True),
            "removedRows": removed,
            "relocatedDataRows": relocated,
        }
    require(set(script_owners) == {x["path"] for x in omitted_scripts}, "unowned_installed_script")
    return actual, generated, omitted_scripts, rewritten


def assemble(inputs_path, inputs_sha256, input_root, output=None, *, check_only=False):
    inputs_path, root = Path(inputs_path), Path(input_root).absolute()
    require(
        not any(p.is_symlink() for p in (inputs_path, *inputs_path.parents)), "manifest_symlink"
    )
    require(inputs_path.stat().st_size <= 16 * 1024**2, "manifest_size_budget")
    require(digest_file(inputs_path) == inputs_sha256, "manifest_hash_mismatch")
    manifest = load(inputs_path)
    budget = Budget(manifest["limits"])
    report = {
        "schemaVersion": "document-files.linux-recognition-assembly-receipt.v1",
        "inputsSha256": inputs_sha256,
        "platform": manifest.get("platform"),
        "status": "failed",
        "stageApproved": False,
        "licenseApproved": False,
        "releaseQualified": False,
        "networkIsolationVerified": False,
        "pipAttempts": 0,
    }
    origins = {}
    owned_output = None
    try:
        if not check_only:
            targets = {
                "aarch64": "linux-aarch64",
                "arm64": "linux-aarch64",
                "x86_64": "linux-x86_64",
                "amd64": "linux-x86_64",
            }
            require(
                platform.system() == "Linux"
                and targets.get(platform.machine().lower()) == manifest["platform"],
                "execution_host_mismatch",
            )
            require(output is not None, "output_required")
            out = Path(output).absolute()
            require(not any(p.is_symlink() for p in (out, *out.parents)), "output_symlink")
            require(
                not out.exists() and not out.is_relative_to(root) and not root.is_relative_to(out),
                "output_not_fresh_or_overlaps_inputs",
            )
            out.mkdir()
            owned_output = out
        members, expected, distributions, site, pip = preflight(manifest, root, budget)
        if check_only:
            for item in [manifest["pythonRuntime"], *manifest["wheels"], *manifest["assets"]]:
                require(
                    digest_file(regular(root, item["path"]), budget) == item["sha256"],
                    "original_input_changed",
                )
            require(digest_file(inputs_path) == inputs_sha256, "manifest_changed")
            report.update(status="inputs-checked-not-executed", usage=budget.usage())
            return report
        stage = out / "stage"
        stage.mkdir()
        for folder in ("home", "tmp", "wheelhouse", "installed"):
            (out / folder).mkdir()
        runtime = manifest["pythonRuntime"]
        extract_python(
            regular(root, runtime["path"]), members, stage, budget, origins, runtime["sha256"]
        )
        wheel_paths = []
        for wheel in manifest["wheels"]:
            dest = out / "wheelhouse" / Path(wheel["path"]).name
            require(not dest.exists(), "wheel_filename_collision")
            with regular(root, wheel["path"]).open("rb") as src:
                size, sha, _ = copy_stream(src, budget=budget, destination=dest)
            require(size == wheel["size"] and sha == wheel["sha256"], "wheel_copy_changed")
            wheel_paths.append(dest)
        lock = out / "requirements.lock"
        lock.write_text(
            "".join(
                f"{w['name']}=={w['version']} --hash=sha256:{w['sha256']}\n"
                for w in manifest["wheels"]
            )
        )
        # Isolated bootstrap imports only the exact pinned pip wheel plus PBS stdlib.
        bootstrap = (
            "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));"
            "runpy.run_module('pip',run_name='__main__')"
        )
        command = [
            str(stage / runtime["executable"]),
            "-I",
            "-S",
            "-B",
            "-c",
            bootstrap,
            str(out / "wheelhouse" / Path(pip["path"]).name),
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-compile",
            "--no-cache-dir",
            "--ignore-installed",
            "--find-links",
            str(out / "wheelhouse"),
            "--target",
            str(out / "installed"),
            "-r",
            str(lock),
        ]
        env = {
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "HOME": str(out / "home"),
            "TMPDIR": str(out / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        report["pipAttempts"] = 1
        report["pip"] = run_pip(command, env, out / "pip.log", budget)
        preserve_wheel_scripts(
            out / "installed", expected, out / "wheelhouse", manifest["wheels"], budget
        )
        actual, generated, scripts, rewritten = installed_files(
            out / "installed", expected, distributions, budget, stage / runtime["executable"]
        )
        for name, path in actual.items():
            row = expected.get(name)
            if row is not None and row["record"]:
                transformed = out / "rewritten-records" / name
                transformed.parent.mkdir(parents=True, exist_ok=True)
                transformed.write_bytes(rewritten[name]["bytes"])
                path = transformed
            if row is None or row["record"]:
                row = {
                    "sourceSha256": generated.get(name) or row["sourceSha256"],
                    "transformation": "pip-generated-metadata",
                    "size": path.stat().st_size,
                    "sha256": digest_file(path, budget, staged=True),
                    "mode": 0o644,
                }
                if name in rewritten:
                    row.update({k: v for k, v in rewritten[name].items() if k != "bytes"})
                    row["transformation"] = "pip-record-with-script-and-data-relocations"
            put(stage, row.get("stageDestination", site + "/" + name), path, row, budget, origins)
        for asset in manifest["assets"]:
            put(
                stage,
                asset["destination"],
                regular(root, asset["path"]),
                {
                    **asset,
                    "sourceSha256": asset["sha256"],
                    "mode": 0o755 if asset["executable"] else 0o644,
                },
                budget,
                origins,
            )
        report["omittedInstalledScripts"] = scripts
        report["preservedUpstreamCLIs"] = [
            {
                "path": site + "/" + name,
                "member": row["member"],
                "sha256": row["sha256"],
                "sourceSha256": row["sourceSha256"],
                "selectedEntrypoint": False,
                "standalonePortability": "unverified",
                "executionSupport": "not-provided",
            }
            for name, row in expected.items()
            if "providedScript" in row
        ]
        report["generatedBinsOmitted"] = [x for x in scripts if "preservedSource" not in x]
        report["relocatedWheelData"] = [
            {
                k: row[k]
                for k in (
                    "member",
                    "sourceSha256",
                    "sha256",
                    "size",
                    "installationRelativePath",
                    "installedRecordPath",
                    "stageDestination",
                    "finalRecordPath",
                )
            }
            for row in expected.values()
            if row.get("dataScheme")
        ]
        report["pythonOmissions"] = [
            {"member": n, **row} for n, row in members.items() if row["omission"]
        ]
        # Input mutation invalidates the entire candidate, even if copied bytes still match.
        for item in [runtime, *manifest["wheels"], *manifest["assets"]]:
            path = regular(root, item["path"])
            require(
                path.stat().st_size == item["size"] and digest_file(path, budget) == item["sha256"],
                "original_input_changed",
            )
        require(digest_file(inputs_path) == inputs_sha256, "manifest_changed")
        actual_stage = set()
        for path in stage.rglob("*"):
            budget.tick()
            require(
                not path.is_symlink() and (path.is_dir() or path.is_file()),
                "final_stage_nonregular",
            )
            if path.is_file():
                name = path.relative_to(stage).as_posix()
                require(name in origins, "unrecorded_stage_file")
                row = origins[name]
                require(
                    path.stat().st_size == row["size"]
                    and digest_file(path, budget, staged=True) == row["sha256"]
                    and stat.S_IMODE(path.stat().st_mode) == row["mode"],
                    "final_stage_changed",
                )
                actual_stage.add(name)
        require(actual_stage == set(origins), "final_stage_inventory_mismatch")
        report.update(
            status="assembled-unapproved",
            originalInputsUnchanged=True,
            finalStageFilesVerified=len(actual_stage),
            pipExpandedBytesEstimated=sum(x["size"] for x in expected.values()),
            filesystemQuotaVerified=False,
        )
        return report
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        report["usage"] = budget.usage()
        if owned_output:
            process_receipt = owned_output / "pip.process.json"
            if process_receipt.is_file():
                report["pipProcessEvidence"] = {
                    "path": process_receipt.name,
                    "sha256": digest_file(process_receipt),
                }
            write_json(owned_output / "file-origins.json", origins)
            write_json(owned_output / "receipt.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            assemble(
                args.inputs,
                args.inputs_sha256,
                args.input_root,
                args.output,
                check_only=args.check_only,
            )
        )
    )


if __name__ == "__main__":
    main()
