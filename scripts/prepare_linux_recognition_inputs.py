#!/usr/bin/env python3
"""Check or acquire approved Linux recognition inputs, never install or execute them.

Default mode is offline inspection. --acquire copies exact local originals;
--download additionally allows a single bounded request chain per missing file.
Incomplete inventories fail before acquisition. Receipts never approve a stage,
license, model quality or release. Each transfer runs in a finite-lived worker.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

SCHEMA = "document-files.recognition-preparation-inventory.v1"
TARGETS = {"linux-aarch64", "linux-x86_64"}
MAX_JSON = 5 * 1024**2
CHUNK = 64 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def regular(path, *, directory=False):
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError("symlink path rejected")
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("nonregular input rejected")
    return path


def read_json(path, expected=None):
    path = regular(path)
    if path.stat().st_size > MAX_JSON:
        raise ValueError("JSON input exceeds limit")
    data = path.read_bytes()
    if expected is not None and digest(data) != expected:
        raise ValueError("input SHA256 mismatch")
    return json.loads(data, object_pairs_hook=unique), data


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def filename(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+%-]*", value):
        raise ValueError("unsafe filename")
    if value.endswith((".", " ")) or value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ValueError("reserved filename")
    return value


def canonical(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def official_url(value, name, *, initial=None):
    """Validate BEFORE redirects; signed GitHub CDN queries are never recorded."""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value) or "\\" in value:
        raise ValueError("unsafe public URL")
    p = urlsplit(value)
    if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise ValueError("HTTPS public origin required")
    path = unquote(p.path)
    if any(c in path for c in "\\\x00") or ".." in PurePosixPath(path).parts:
        raise ValueError("unsafe URL path")
    if initial:
        origin = urlsplit(initial)
        if (
            p.hostname == "release-assets.githubusercontent.com"
            and origin.hostname == "github.com"
            and path.startswith(
                ("/github-production-release-asset/", "/github-production-release-asset-2e65be/")
            )
        ):
            return value
        if origin.hostname == "github.com" or p.path != origin.path:
            raise ValueError("redirect leaves approved artifact")
        allowed = {origin.hostname}
        if origin.hostname in {"download.pytorch.org", "download-r2.pytorch.org"}:
            allowed = {"download.pytorch.org", "download-r2.pytorch.org"}
        if p.hostname not in allowed:
            raise ValueError("redirect leaves approved origin")
    if p.query or Path(path).name != name:
        raise ValueError("URL filename or query mismatch")
    if p.hostname == "files.pythonhosted.org" and path.startswith("/packages/"):
        return value
    if (
        p.hostname in {"download.pytorch.org", "download-r2.pytorch.org"}
        and path == "/whl/cpu/" + name
    ):
        return value
    if p.hostname == "github.com" and re.fullmatch(
        r"/astral-sh/python-build-standalone/releases/download/[0-9]{8}/[^/]+", path
    ):
        return value
    raise ValueError("unapproved acquisition origin")


def wheel_target(row, target):
    name = row["filename"]
    if not name.endswith(".whl"):
        raise ValueError("wheel filename required")
    stem, python, abi, platforms = name[:-4].rsplit("-", 3)
    prefix = row["name"].replace("-", "_") + "-" + row["version"]
    if canonical(stem) != canonical(prefix):
        raise ValueError("wheel name/version mismatch")
    valid_python = {"py3", "py2", "py312", "cp312"}
    if abi == "abi3":
        valid_python |= {f"cp3{i}" for i in range(2, 13)}
    if (
        not set(python.split(".")) <= valid_python
        or python == "py2"
        or abi not in {"none", "abi3", "cp312"}
    ):
        raise ValueError("wheel is not CPython 3.12 compatible")
    arch = target.removeprefix("linux-")
    for platform in platforms.split("."):
        if platform == "any" and abi == "none":
            continue
        if not re.fullmatch(rf"(?:manylinux(?:_[0-9]+_[0-9]+|2014)|linux)_{arch}", platform):
            raise ValueError("foreign wheel platform")
    if re.search(r"nvidia|cuda|rocm|triton", row["name"], re.I):
        raise ValueError("GPU distribution rejected")
    if canonical(row["name"]) in {"torch", "torchvision"} and not urlsplit(
        row["url"]
    ).path.startswith("/whl/cpu/"):
        raise ValueError("official CPU wheel required")


def inventory_plan(args, deadline):
    data, raw = read_json(args.inventory, args.inventory_sha256)
    if data.get("schemaVersion") != SCHEMA or data.get("target") != args.target:
        raise ValueError("inventory target/schema mismatch")
    if not re.fullmatch(r"3\.12\.[0-9]+", data.get("pythonVersion", "")):
        raise ValueError("CPython 3.12 inventory required")
    base = args.inventory.absolute().parent
    checked_refs = set()
    metadata_bytes = 0

    def evidence(ref):
        nonlocal metadata_bytes
        if time.monotonic() >= deadline:
            raise TimeoutError("preflight deadline")
        if not isinstance(ref, dict) or "path" not in ref or "sha256" not in ref:
            raise ValueError("invalid evidence reference")
        name = ref["path"]
        p = PurePosixPath(name)
        if not name or p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
            raise ValueError("escaping evidence reference")
        key = (name, ref["sha256"])
        if key not in checked_refs:
            path = regular(base / name)
            if path.stat().st_size > MAX_JSON or digest(path.read_bytes()) != ref["sha256"]:
                raise ValueError("metadata evidence SHA256 mismatch")
            metadata_bytes += path.stat().st_size
            if metadata_bytes > 100 * 1024**2:
                raise ValueError("metadata input budget exceeded")
            checked_refs.add(key)

    for ref in data.get("sourceInputs", {}).values():
        evidence(ref)
    for key in ("dependencyCheck", "collection", "modelInputs"):
        if data.get(key):
            evidence(data[key])
    missing = list(data.get("collectionIssues", [])) + list(data.get("unresolved", []))
    if data.get("dependencyCheck"):
        dependency, _ = read_json(base / data["dependencyCheck"]["path"])
        if dependency.get("passed") is not True:
            missing.append({"component": "dependencyCheck", "reason": "not passed"})
    rows = list(data.get("wheels", []))
    for key in ("pythonRuntime", "antlrSource"):
        if isinstance(data.get(key), dict):
            rows.append(data[key])
        else:
            missing.append({"component": key, "reason": "missing explicit input"})
    wheel_refs = {(r.get("name"), r.get("version")): r for r in data.get("wheels", [])}
    for row in data.get("buildTools", []):
        if "filename" not in row and (row.get("name"), row.get("version")) in wheel_refs:
            continue  # Exact existing wheel reference, not a second acquisition.
        rows.append(row)
    if data.get("modelInputs"):
        missing.append(
            {"component": "modelInputs", "reason": "reference is not a normalized acquisition list"}
        )
    if len(rows) > 512:
        raise ValueError("input count limit")
    planned, seen, packages = [], set(), set()
    for original in rows:
        if time.monotonic() >= deadline:
            raise TimeoutError("preflight deadline")
        row = dict(original)
        if not all(
            row.get(k) is not None for k in ("name", "version", "filename", "url", "sha256", "size")
        ):
            missing.append(
                {"component": row.get("name", "unnamed"), "reason": "incomplete explicit input"}
            )
            continue
        name = filename(row["filename"])
        if name.casefold() in seen:
            raise ValueError("duplicate output filename")
        seen.add(name.casefold())
        if (
            not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
            or type(row["size"]) is not int
            or row["size"] <= 0
        ):
            raise ValueError("exact positive size and SHA256 required")
        official_url(row["url"], name)
        if name.endswith(".whl"):
            wheel_target(row, args.target)
            package = canonical(row["name"])
            if package in packages:
                raise ValueError("duplicate wheel distribution")
            packages.add(package)
        elif row.get("role") == "python-runtime":
            arch = args.target.removeprefix("linux-")
            if not re.fullmatch(
                rf"cpython-3\.12\.[0-9]+\+[0-9]{{8}}-{arch}-unknown-linux-gnu-install_only\.tar\.gz",
                name,
            ):
                raise ValueError("foreign PBS filename")
            if (
                not name.startswith("cpython-" + data["pythonVersion"] + "+")
                or not name.startswith("cpython-" + row["version"] + "-")
                or urlsplit(row["url"]).hostname != "github.com"
            ):
                raise ValueError("PBS identity mismatch")
        elif (
            row.get("role") != "build-source"
            or row["name"] != "antlr4-python3-runtime"
            or name != f"antlr4-python3-runtime-{row['version']}.tar.gz"
        ):
            raise ValueError("unrecognized source input")
        for key in ("metadata", "indexEvidence"):
            if row.get(key):
                evidence(row[key])
        source = row.get("localOriginal")
        if source:
            if source.get("sha256") != row["sha256"] or source.get("size") != row["size"]:
                raise ValueError("local original identity mismatch")
            path = Path(source["path"])
            if not path.is_absolute():
                raise ValueError("explicit absolute local original required")
            if path.exists() or path.is_symlink():
                regular(path)
                if path.stat().st_size != row["size"]:
                    raise ValueError("local original size mismatch")
                row["localPath"] = str(path)
        row["method"] = "copy" if row.get("localPath") else "download"
        if row["method"] == "download" and not args.download:
            missing.append(
                {"component": row["name"], "reason": "local input missing and download disabled"}
            )
        planned.append(row)
    if not planned:
        missing.append({"component": "inputs", "reason": "empty input list"})
    return raw, planned, missing


class Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def __init__(self, original, name):
        self.original, self.name = original, name
        self.redirect_count = 0

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib's default redirect handler drains fp.read() without a bound.
        # Close instead: neither an approved nor a rejected redirect body is an input.
        try:
            location = headers.get("location", headers.get("uri"))
            if not location:
                raise ValueError("redirect lacks location")
            newurl = urljoin(req.full_url, location)
            official_url(newurl, self.name, initial=self.original)
            self.redirect_count += 1
            if self.redirect_count > self.max_redirections:
                raise ValueError("redirect count limit")
            request = self.redirect_request(req, fp, code, msg, headers, newurl)
            if request is None:
                raise ValueError("redirect method rejected")
        finally:
            fp.close()
        return self.parent.open(request, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl, self.name, initial=self.original)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class TransferError(ValueError):
    def __init__(self, message, size, sha256):
        super().__init__(message)
        self.size, self.sha256 = size, sha256


def stream_bytes(source, destination, row, deadline):
    total, hashed = 0, hashlib.sha256()
    with destination.open("xb") as output:
        os.chmod(destination, 0o600)
        read = getattr(source, "read1", source.read)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("transfer deadline")
            chunk = read(min(CHUNK, row["size"] - total + 1))
            if not chunk:
                break
            output.write(chunk)
            total += len(chunk)
            hashed.update(chunk)
            if total > row["size"]:
                raise TransferError("received excess bytes", total, hashed.hexdigest())
    if total != row["size"] or hashed.hexdigest() != row["sha256"]:
        raise TransferError("received size/SHA256 mismatch", total, hashed.hexdigest())
    return total, hashed.hexdigest()


def transfer(job):
    row = job["input"]
    directory = regular(Path(job["directory"]), directory=True)
    name = filename(row["filename"])
    partial, final = directory / (name + ".partial"), directory / name
    deadline = time.monotonic() + job["timeoutSeconds"]
    result = {"status": "failed", "filename": name, "method": row["method"]}
    try:
        if final.exists() or final.is_symlink():
            raise ValueError("output already exists")
        if row["method"] == "copy":
            path = regular(row["localPath"])
            before = path.stat()

            def identity(s):
                return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns

            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source:
                opened = os.fstat(source.fileno())
                if identity(before) != identity(opened):
                    raise ValueError("local original changed before copy")
                total, hashed = stream_bytes(source, partial, row, deadline)
                after = os.fstat(source.fileno())
            current = regular(path).stat()
            if not identity(before) == identity(opened) == identity(after) == identity(current):
                raise ValueError("local original changed during copy")
        else:
            official_url(row["url"], name)
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), Redirects(row["url"], name)
            )
            request = urllib.request.Request(row["url"], headers={"Accept-Encoding": "identity"})
            with opener.open(request, timeout=min(20, job["timeoutSeconds"])) as response:
                if response.url != row["url"]:
                    official_url(response.url, name, initial=row["url"])
                if response.status != 200 or response.headers.get("Content-Range"):
                    raise ValueError("full HTTP 200 required")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("encoded body rejected")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != row["size"]:
                    raise ValueError("HTTP size mismatch")
                total, hashed = stream_bytes(response, partial, row, deadline)
        if time.monotonic() >= deadline:
            raise TimeoutError("transfer deadline")
        os.link(partial, final)  # Atomic create-only commit; never replace existing output.
        partial.unlink()
        result.update(status="verified", size=total, sha256=hashed)
    except Exception as exc:
        # Do not leak redirected signed URLs or arbitrary server exception text.
        result["errorType"] = type(exc).__name__
        if isinstance(exc, TransferError):
            result.update(observedSize=exc.size, observedSha256=exc.sha256)
    result["partialBytes"] = partial.stat().st_size if partial.is_file() else 0
    return result


def supervise(job_path, timeout):
    process = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--transfer-job", str(job_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    cleanup = {"pid": process.pid, "terminated": False}
    try:
        code = process.wait(timeout=timeout)
    except (Exception, KeyboardInterrupt) as wait_error:
        try:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    process.kill()
            else:
                process.kill()
            process.wait(timeout=5)
            cleanup["terminated"] = True
        except (OSError, subprocess.SubprocessError) as exc:
            cleanup["errorType"] = type(exc).__name__
        return {
            "status": "failed",
            "errorType": type(wait_error).__name__,
            "cleanup": cleanup,
        }
    result_path = job_path.with_suffix(".result.json")
    if code != 0 or not result_path.is_file():
        return {
            "status": "failed",
            "errorType": "TransferWorkerFailed",
            "exitCode": code,
            "cleanup": cleanup,
        }
    result, _ = read_json(result_path)
    return {**result, "cleanup": cleanup}


def prepare(args):
    started = time.monotonic()
    output = args.output.absolute()
    regular(output.parent, directory=True)
    output.mkdir(mode=0o700, exist_ok=False)
    receipt = {
        "schemaVersion": "document-files.recognition-input-acquisition.v1",
        "createdAt": datetime.datetime.now(datetime.UTC).isoformat(),
        "preparerSha256": digest(Path(__file__).read_bytes()),
        "inventorySha256": args.inventory_sha256,
        "target": args.target,
        "mode": "acquire" if args.acquire else "check",
        "status": "failed",
        "stageApproved": False,
        "releaseQualified": False,
        "inputs": [],
        "missing": [],
        "remaining": None,
    }
    try:
        values = [
            args.max_total_bytes,
            args.max_download_bytes,
            args.max_file_bytes,
            args.min_free_bytes,
            args.timeout_seconds,
            args.file_timeout_seconds,
        ]
        if any(type(v) is not int or v <= 0 for v in values):
            raise ValueError("explicit positive resource limits required")
        if args.target not in TARGETS or not re.fullmatch(r"[a-f0-9]{64}", args.inventory_sha256):
            raise ValueError("target and approved inventory SHA256 required")
        deadline = started + args.timeout_seconds
        original = regular(args.inventory)
        if original.stat().st_size > MAX_JSON:
            raise ValueError("inventory byte limit")
        observed = original.read_bytes()
        receipt["observedInventorySha256"] = digest(observed)
        with (output / "received-inventory.json").open("xb") as copy:
            copy.write(observed)
        raw, rows, missing = inventory_plan(args, deadline)
        (output / "approved-inventory.json").write_bytes(raw)
        receipt["missing"] = missing
        totals = {
            "outputBytes": sum(r["size"] for r in rows),
            "downloadBytes": sum(r["size"] for r in rows if r["method"] == "download"),
            "diskFreeBytes": shutil.disk_usage(output).free,
        }
        receipt["preflight"] = totals
        receipt["limits"] = dict(
            zip(
                [
                    "maxTotalBytes",
                    "maxDownloadBytes",
                    "maxFileBytes",
                    "minFreeBytes",
                    "timeoutSeconds",
                    "fileTimeoutSeconds",
                ],
                values,
                strict=True,
            )
        )
        if (
            totals["outputBytes"] > args.max_total_bytes
            or totals["downloadBytes"] > args.max_download_bytes
            or any(r["size"] > args.max_file_bytes for r in rows)
        ):
            raise ValueError("input byte budget exceeded")
        if totals["diskFreeBytes"] < totals["outputBytes"] + args.min_free_bytes:
            raise ValueError("insufficient free disk")
        if digest(regular(args.inventory).read_bytes()) != args.inventory_sha256:
            raise ValueError("inventory changed during preflight")
        receipt["remaining"] = [r["filename"] for r in rows]
        if missing:
            receipt["status"] = "not-ready"
        elif not args.acquire:
            receipt["checkedLocalOriginals"] = []
            for row in rows:
                if row["method"] != "copy":
                    continue
                path = regular(row["localPath"])
                before = path.stat()
                hashed, size = hashlib.sha256(), 0
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    while True:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("local inspection deadline")
                        chunk = stream.read(min(CHUNK, row["size"] - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        hashed.update(chunk)
                        if size > row["size"]:
                            raise ValueError("local original exceeds approved size")
                    after = os.fstat(stream.fileno())

                def identity(st):
                    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns

                if (
                    size != row["size"]
                    or hashed.hexdigest() != row["sha256"]
                    or not identity(before)
                    == identity(opened)
                    == identity(after)
                    == identity(regular(path).stat())
                ):
                    raise ValueError("local original bytes changed or SHA256 mismatch")
                receipt["checkedLocalOriginals"].append(
                    {"filename": row["filename"], "size": size, "sha256": hashed.hexdigest()}
                )
            receipt["remoteBytesVerified"] = False
            receipt["status"] = "ready-not-acquired"
        else:
            if args.timeout_seconds <= 5:
                raise ValueError("acquisition requires a five-second cleanup reserve")
            receipt["cleanupReserveSeconds"] = 5
            (output / "files").mkdir()
            (output / "jobs").mkdir()
            for index, row in enumerate(rows):
                remaining = deadline - time.monotonic() - 5
                if remaining <= 0:
                    raise TimeoutError("overall preparation deadline")
                if shutil.disk_usage(output).free < row["size"] + args.min_free_bytes:
                    raise ValueError("free disk fell below reserve")
                timeout = min(remaining, args.file_timeout_seconds)
                job = {"input": row, "directory": str(output / "files"), "timeoutSeconds": timeout}
                job_path = output / "jobs" / f"{index:04}.json"
                write_json(job_path, job)
                result = supervise(job_path, timeout)
                result.update(
                    filename=row["filename"],
                    expectedSha256=row["sha256"],
                    expectedSize=row["size"],
                    jobPath="jobs/" + job_path.name,
                )
                partial = output / "files" / (row["filename"] + ".partial")
                result["partialBytes"] = partial.stat().st_size if partial.is_file() else 0
                receipt["inputs"].append(result)
                if result["status"] != "verified":
                    raise ValueError("input transfer failed; no retry")
                final = regular(output / "files" / row["filename"])
                if final.stat().st_size != row["size"] or result.get("sha256") != row["sha256"]:
                    raise ValueError("transfer result identity mismatch")
                hashed = hashlib.sha256()
                with final.open("rb") as stream:
                    before = os.fstat(stream.fileno())
                    while chunk := stream.read(CHUNK):
                        if time.monotonic() >= deadline - 5:
                            raise TimeoutError("final verification deadline")
                        hashed.update(chunk)
                    after = os.fstat(stream.fileno())
                if (
                    hashed.hexdigest() != row["sha256"]
                    or (before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_ino, after.st_size, after.st_mtime_ns)
                    or (final.stat().st_ino, final.stat().st_size, final.stat().st_mtime_ns)
                    != (after.st_ino, after.st_size, after.st_mtime_ns)
                ):
                    raise ValueError("final bytes changed after transfer")
                result["verifiedPath"] = "files/" + row["filename"]
                receipt["remaining"].remove(row["filename"])
            receipt["status"] = "inputs-acquired-not-stage-approved"
    except (Exception, KeyboardInterrupt) as exc:
        receipt["errorType"] = type(exc).__name__
        receipt["error"] = (
            str(exc)
            if isinstance(exc, (ValueError, TimeoutError))
            else "preparation interrupted or failed"
        )
    finally:
        receipt["elapsedSeconds"] = round(time.monotonic() - started, 3)
        write_json(output / "acquisition.json", receipt)
    return receipt


def run_preparation(args):
    # Convert cooperative process termination into the same receipt/worker cleanup
    # path as Ctrl-C. SIGKILL and host loss cannot be intercepted.
    def terminate(signum, frame):
        raise KeyboardInterrupt()

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        return prepare(args)
    finally:
        signal.signal(signal.SIGTERM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transfer-job", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inventory-sha256")
    parser.add_argument("--target", choices=sorted(TARGETS))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--acquire", action="store_true")
    parser.add_argument("--download", action="store_true")
    for name in (
        "max-total-bytes",
        "max-download-bytes",
        "max-file-bytes",
        "min-free-bytes",
        "timeout-seconds",
        "file-timeout-seconds",
    ):
        parser.add_argument("--" + name, type=int)
    args = parser.parse_args()
    if args.transfer_job:
        job, _ = read_json(args.transfer_job)
        write_json(args.transfer_job.with_suffix(".result.json"), transfer(job))
        return
    required = (
        "inventory",
        "inventory_sha256",
        "target",
        "output",
        "max_total_bytes",
        "max_download_bytes",
        "max_file_bytes",
        "min_free_bytes",
        "timeout_seconds",
        "file_timeout_seconds",
    )
    if any(getattr(args, n) is None for n in required):
        parser.error(
            "inventory, approved hash, target, new output and all resource limits required"
        )
    result = run_preparation(args)
    print(json.dumps(result))
    if result["status"] not in {"ready-not-acquired", "inputs-acquired-not-stage-approved"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
