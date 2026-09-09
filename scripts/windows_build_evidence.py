"""Prepare pinned VS2022 Enterprise terms; never approve redistribution rights.

Only explicit administrator-selected terms are fetched (small, HTTPS, exact URL/hash).
The original and paragraph-text derivative are retained. A matching heading is not
proof of runner licensing, static CRT/SDK redistribution or final product compliance.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from windows_runtime_notice import validate_notice

TERMS_URL = (
    "https://visualstudio.microsoft.com/wp-content/uploads/2021/11/"
    "Visual-Studio-2022-Enterprise-Professional-License-EN.docx"
)
TERMS_PAGE = "https://visualstudio.microsoft.com/license-terms/vs2022-ga-proenterprise/"
MAX_BYTES = 1024 * 1024
BUILD_ENVIRONMENT_OVERRIDES = {"MSBUILDDISABLENODEREUSE": "1"}
# Official MSBuild guidance: documentation/wiki/MSBuild-Environment-Variables.md
# in https://github.com/dotnet/msbuild. Job cleanup is still verified independently.


def child_environment(parent=None):
    """Only the new builder's environment changes, never the supervisor or host."""
    environment = dict(os.environ if parent is None else parent)
    for key, value in BUILD_ENVIRONMENT_OVERRIDES.items():
        environment = {k: v for k, v in environment.items() if k.upper() != key}
        environment[key] = value
    return environment


def digest(data):
    return hashlib.sha256(data).hexdigest()


def installed_identity(host):
    """Project only needed vswhere fields; never preserve full environment/catalog."""
    if (
        host.get("productId") != "Microsoft.VisualStudio.Product.Enterprise"
        or not re.fullmatch(r"17\.\d+(?:\.\d+){1,2}", host.get("installationVersion", ""))
        or host.get("isPrerelease") is not False
        or host.get("isComplete") is not True
        or host.get("isLaunchable") is not True
        or not host.get("installationPath")
    ):
        raise ValueError("installed_vs2022_enterprise_identity_required")
    return {
        key: host[key]
        for key in (
            "productId",
            "installationVersion",
            "installationPath",
            "isPrerelease",
            "isComplete",
            "isLaunchable",
        )
    } | {"edition": "Enterprise", "majorVersion": "2022"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("terms_redirect_not_approved")


def fetch(url):
    with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as response:
        raw = response.read(MAX_BYTES + 1)
    return raw


def document_text(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError("terms_too_large")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("terms_duplicate_members")
        item = archive.getinfo("word/document.xml")
        if item.file_size > MAX_BYTES * 2:
            raise ValueError("terms_xml_too_large")
        xml = archive.read(item)
    # No external entities, DTD or lossy decoding in a retained legal text.
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("terms_unsafe_xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    text = "\n".join(
        "".join(node.text or "" for node in paragraph.findall(".//w:t", ns))
        for paragraph in root.findall(".//w:p", ns)
    )
    if not re.search(r"MICROSOFT VISUAL STUDIO ENTERPRISE 2022", " ".join(text.split())[:700]):
        raise ValueError("terms_enterprise_edition_missing")
    return text + "\n"


def prepare(host, output, url, expected_sha256, *, source=None, retrieved_at=None):
    identity = installed_identity(host)
    if url != TERMS_URL or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ValueError("explicit_official_terms_identity_required")
    output.mkdir(parents=False, exist_ok=False)
    if source is None:
        raw = fetch(url)
        retrieved_at = datetime.now(UTC).isoformat()
    else:
        if not source.is_file() or source.is_symlink() or not retrieved_at:
            raise ValueError("local_terms_source_and_retrieval_time_required")
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("terms_too_large")
        raw = source.read_bytes()
    timestamp = datetime.fromisoformat(retrieved_at)
    if timestamp.tzinfo is None or timestamp > datetime.now(UTC):
        raise ValueError("terms_retrieval_time_invalid")
    (output / "retrieval.json").write_text(
        json.dumps(
            {
                "url": url,
                "retrievedAt": retrieved_at,
                "expectedSha256": expected_sha256,
                "observedSha256": digest(raw),
                "observedBytes": len(raw),
                "complete": len(raw) <= MAX_BYTES,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if len(raw) > MAX_BYTES or digest(raw) != expected_sha256:
        raise ValueError("terms_checksum_mismatch")
    original = output / "vs2022-enterprise-original.docx"
    notice = output / "vs2022-enterprise-text.txt"
    original.write_bytes(raw)
    text = document_text(raw)
    notice.write_text(text, encoding="utf-8")
    validate_notice(notice)
    receipt = {
        "schemaVersion": "document-files.windows-terms.v1",
        "installedProduct": identity,
        "source": {
            "url": url,
            "licensePage": TERMS_PAGE,
            "retrievedAt": retrieved_at,
            "path": original.name,
            "sha256": digest(raw),
        },
        "textDerivative": {
            "path": notice.name,
            "sha256": digest(notice.read_bytes()),
            "method": "word/document.xml paragraph text, original retained",
        },
        "independentRedistributionReview": "pending",
        "installedLicenseEntitlement": "not-assessed",
        "staticCrtAndSdkReview": "not-assessed",
        "releaseQualification": False,
    }
    (output / "terms-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


class WindowsBuildJob:
    """One suspended child assigned before it can spawn; no breakaway permission.

    Standard compiler subprocesses inherit this unnamed job. Kill-on-close also
    applies if the supervisor is terminated before it can write a final receipt.
    This is process cleanup, not a sandbox for hostile WMI/service process launch.
    """

    def __init__(self, command, log):
        if os.name != "nt":
            raise OSError("windows_build_host_required")
        import ctypes as c
        import msvcrt
        from ctypes import wintypes as w

        class Limits(c.Structure):
            _fields_ = [
                ("processTime", c.c_int64),
                ("jobTime", c.c_int64),
                ("flags", w.DWORD),
                ("minWorkingSet", c.c_size_t),
                ("maxWorkingSet", c.c_size_t),
                ("activeLimit", w.DWORD),
                ("affinity", c.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class Extended(c.Structure):
            _fields_ = [
                ("basic", Limits),
                ("io", c.c_uint64 * 6),
                ("processMemory", c.c_size_t),
                ("jobMemory", c.c_size_t),
                ("peakProcessMemory", c.c_size_t),
                ("peakJobMemory", c.c_size_t),
            ]

        class Startup(c.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("reserved", w.LPWSTR),
                ("desktop", w.LPWSTR),
                ("title", w.LPWSTR),
                ("x", w.DWORD),
                ("y", w.DWORD),
                ("xSize", w.DWORD),
                ("ySize", w.DWORD),
                ("xChars", w.DWORD),
                ("yChars", w.DWORD),
                ("fill", w.DWORD),
                ("flags", w.DWORD),
                ("show", w.WORD),
                ("reservedSize", w.WORD),
                ("reservedBytes", c.c_void_p),
                ("stdin", w.HANDLE),
                ("stdout", w.HANDLE),
                ("stderr", w.HANDLE),
            ]

        class Process(c.Structure):
            _fields_ = [
                ("process", w.HANDLE),
                ("thread", w.HANDLE),
                ("pid", w.DWORD),
                ("tid", w.DWORD),
            ]

        class Counts(c.Structure):
            _fields_ = [
                ("user", c.c_int64),
                ("kernel", c.c_int64),
                ("periodUser", c.c_int64),
                ("periodKernel", c.c_int64),
                ("pageFaults", w.DWORD),
                ("total", w.DWORD),
                ("active", w.DWORD),
                ("terminated", w.DWORD),
            ]

        self.c, self.w, self.Counts = c, w, Counts
        self.kernel = k = c.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    c.c_void_p,
                    c.c_void_p,
                    w.BOOL,
                    w.DWORD,
                    c.c_void_p,
                    w.LPCWSTR,
                    c.POINTER(Startup),
                    c.POINTER(Process),
                ],
                w.BOOL,
            ),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, c.POINTER(w.DWORD)], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p],
                w.BOOL,
            ),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            getattr(k, name).argtypes, getattr(k, name).restype = args, result
        self.job, self.process, self.output, self.input = None, None, None, None
        self.files = contextlib.ExitStack()
        child = Process()
        try:
            self.job = k.CreateJobObjectW(None, None)  # unnamed, non-inheritable
            if not self.job:
                raise c.WinError(c.get_last_error())
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, no breakaway
            if not k.SetInformationJobObject(self.job, 9, c.byref(limits), c.sizeof(limits)):
                raise c.WinError(c.get_last_error())
            self.output = self.files.enter_context(log.open("xb"))
            self.input = self.files.enter_context(Path(os.devnull).open("rb"))  # noqa: SIM115
            for stream in (self.input, self.output):
                os.set_handle_inheritable(msvcrt.get_osfhandle(stream.fileno()), True)
            info = Startup()
            info.cb, info.flags = c.sizeof(info), 0x100  # STARTF_USESTDHANDLES
            info.stdin = msvcrt.get_osfhandle(self.input.fileno())
            info.stdout = info.stderr = msvcrt.get_osfhandle(self.output.fileno())
            line = c.create_unicode_buffer(subprocess.list2cmdline(command))
            environment = c.create_unicode_buffer(
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        child_environment().items(), key=lambda item: item[0].upper()
                    )
                )
                + "\0\0"
            )
            # SUSPENDED | NEW_PROCESS_GROUP | UNICODE_ENVIRONMENT; no shell/PID search.
            if not k.CreateProcessW(
                command[0],
                line,
                None,
                None,
                True,
                0x604,
                environment,
                None,
                c.byref(info),
                c.byref(child),
            ):
                raise c.WinError(c.get_last_error())
            self.process, self.pid = child.process, child.pid
            if not k.AssignProcessToJobObject(self.job, self.process):
                raise c.WinError(c.get_last_error())
            if k.ResumeThread(child.thread) != 1:
                raise OSError("suspended_builder_resume_failed")
        except BaseException:
            if self.process:
                k.TerminateProcess(self.process, 1)  # still our exact process handle
                k.WaitForSingleObject(self.process, 30000)
            self.close()
            raise
        finally:
            if child.thread:
                k.CloseHandle(child.thread)
            for stream in (self.input, self.output):
                if stream and not stream.closed:
                    os.set_handle_inheritable(msvcrt.get_osfhandle(stream.fileno()), False)

    def poll(self):
        result = self.kernel.WaitForSingleObject(self.process, 0)
        if result == 258:
            return None
        if result != 0:
            raise OSError("builder_wait_failed")
        code = self.w.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process, self.c.byref(code)):
            raise OSError("builder_exit_code_unavailable")
        return code.value

    def counts(self):
        value = self.Counts()
        if not self.kernel.QueryInformationJobObject(
            self.job, 1, self.c.byref(value), self.c.sizeof(value), None
        ):
            raise OSError("build_job_process_count_unavailable")
        return {"active": value.active, "total": value.total, "terminated": value.terminated}

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.job, 1):
            raise OSError("build_job_termination_failed")

    def close(self):
        for name in ("job", "process"):
            handle = getattr(self, name)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)
        self.files.close()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def supervise(
    command,
    evidence,
    run_id,
    timeout,
    *,
    launch=WindowsBuildJob,
    clock=time.monotonic,
    sleep=time.sleep,
    cleanup_seconds=30,
):
    """Persist incomplete state before launch; only an empty job proves cleanup."""
    path = evidence / "build-execution.json"
    if path.exists():
        raise ValueError("build_execution_evidence_exists")
    state = {
        "schemaVersion": "document-files.windows-build-execution.v1",
        "executionRunId": run_id,
        "status": "started",
        "timeoutSeconds": timeout,
        "cleanupTimeoutSeconds": cleanup_seconds,
        "timedOut": False,
        "interrupted": False,
        "exitCode": None,
        "cleanupVerified": False,
        "errors": [],
        "containment": "unnamed_job_kill_on_close_no_breakaway",
        "childEnvironmentOverrides": dict(BUILD_ENVIRONMENT_OVERRIDES),
        "scope": "builder_and_inherited_job_descendants_not_external_services",
        "startedAt": datetime.now(UTC).isoformat(),
        "endedAt": None,
    }
    write_json(path, state)
    job = None
    started = clock()
    try:
        if not 1 <= timeout <= 7200 or not 1 <= cleanup_seconds <= 60:
            raise ValueError("finite_build_limits_required")
        job = launch(command, evidence / "build-console.log")
        state["builderPid"] = job.pid
        state["initialJobCounts"] = job.counts()
        write_json(path, state)
        while (code := job.poll()) is None:
            if clock() - started >= timeout:
                state["timedOut"] = True
                break
            sleep(min(0.2, max(0, timeout - (clock() - started))))
        state["exitCode"] = code
        if clock() - started > timeout:
            state["timedOut"] = True
    except BaseException as exc:
        state["interrupted"] = isinstance(exc, (KeyboardInterrupt, SystemExit))
        state["errors"].append(
            "build_interrupted" if state["interrupted"] else "build_supervision_failed"
        )
    finally:
        if job is not None:
            try:
                before = job.counts()
                state["beforeCleanupJobCounts"] = before
                if before["active"]:
                    state["terminationRequested"] = True
                    # Even exit 0 with surviving children is not a completed build.
                    if state["exitCode"] == 0 and not state["timedOut"]:
                        state["errors"].append("builder_left_running_descendants")
                    job.terminate()
                deadline = clock() + cleanup_seconds
                while True:
                    state["finalJobCounts"] = job.counts()
                    if state["finalJobCounts"]["active"] == 0:
                        state["cleanupVerified"] = True
                        break
                    if clock() >= deadline:
                        state["errors"].append("build_cleanup_deadline_exceeded")
                        break
                    sleep(0.1)
            except BaseException:
                state["errors"].append("build_cleanup_unverified")
            finally:
                try:
                    job.close()  # Kernel kill-on-close remains a fallback, not proof of empty job.
                except BaseException:
                    state["cleanupVerified"] = False
                    state["errors"].append("build_job_close_failed")
        state["status"] = (
            "completed"
            if (
                state["exitCode"] == 0
                and not state["timedOut"]
                and not state["interrupted"]
                and state["cleanupVerified"]
                and not state["errors"]
            )
            else "failed"
        )
        state["endedAt"] = datetime.now(UTC).isoformat()
        write_json(path, state)
    return state


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_record(path):
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_file():
        raise ValueError("build_artifact_not_regular")
    return {"path": str(path.absolute()), "sha256": sha_file(path), "size": path.stat().st_size}


def safe_artifact(root, name):
    relative = PurePosixPath(name)
    if (
        not name
        or relative.is_absolute()
        or ".." in relative.parts
        or "\\" in name
        or ":" in name
        or relative.as_posix() != name
    ):
        raise ValueError("build_artifact_path_invalid")
    path = root / name
    for parent in (root, *path.relative_to(root).parents):
        candidate = parent if parent == root else root / parent
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise ValueError("build_artifact_link_rejected")
    return path


def linked_outputs(
    kind, work, output, version, commit, builder_sha, license_sha, *, metadata_output=None
):
    """Bind fresh output to the builder's own inventory; never grant redistribution."""
    if work.is_symlink() or getattr(work, "is_junction", lambda: False)():
        raise ValueError("build_work_link_rejected")
    if kind == "recognition":
        receipt_path = safe_artifact(work, "native-candidate.json")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt.get("schemaVersion") != "document-files.recognition-native-candidate.v1"
            or receipt.get("target") != "windows-x86_64"
            or receipt.get("sourceCommit") != commit
            or receipt.get("dirtySource") is not False
            or receipt.get("builderSha256") != builder_sha
            or receipt.get("releaseReady") is not False
            or receipt.get("fullRecognitionQualified") is not False
        ):
            raise ValueError("native_build_identity_mismatch")
        files = receipt.get("files", [])
        if not files or len({item["path"] for item in files}) != len(files):
            raise ValueError("native_build_inventory_invalid")
        actual = []
        for item in files:
            if not item["path"].startswith("candidate/"):
                raise ValueError("native_build_inventory_outside_candidate")
            entry = file_record(safe_artifact(work, item["path"]))
            if entry["sha256"] != item["sha256"] or entry["size"] != item["size"]:
                raise ValueError("native_build_artifact_mismatch")
            actual.append(entry)
        present = {
            p.relative_to(work).as_posix()
            for p in (work / "candidate").rglob("*")
            if p.is_file() or p.is_symlink()
        }
        if present != {item["path"] for item in files}:
            raise ValueError("native_build_inventory_incomplete")
        binary = receipt.get("binary", {})
        if binary.get("path") != "candidate/bin/tesseract.exe" or not any(
            item["path"] == binary["path"] and item["sha256"] == binary.get("sha256")
            for item in files
        ):
            raise ValueError("native_build_binary_not_in_inventory")
        notices = receipt.get("runtimeNotices", {}).get("notices", [])
        if not any(
            item.get("sha256") == license_sha
            and any(
                f["path"] == "candidate/licenses/" + item["path"] and f["sha256"] == license_sha
                for f in files
            )
            for item in notices
        ):
            raise ValueError("native_build_terms_not_linked")
        return {"internalReceipt": file_record(receipt_path), "artifacts": actual}

    if output.is_symlink() or getattr(output, "is_junction", lambda: False)():
        raise ValueError("build_output_link_rejected")
    archive_path = output / f"llama-cpp-cpu-{version}-windows-x86_64.pack.zip"
    archive_record = file_record(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or len(names) > 10000:
            raise ValueError("cpu_build_archive_inventory_invalid")
        if any(
            archive.getinfo(name).file_size > 2 * MAX_BYTES
            for name in ("manifest.json", "build.json")
        ):
            raise ValueError("cpu_build_metadata_too_large")
        manifest_bytes, build_bytes = archive.read("manifest.json"), archive.read("build.json")
        manifest, receipt = json.loads(manifest_bytes), json.loads(build_bytes)
        build = manifest.get("provenance", {}).get("build", {})
        if (
            manifest.get("id") != "llama-cpp-cpu"
            or manifest.get("version") != version
            or manifest.get("platform") != "windows-x86_64"
            or manifest.get("kind") != "llama-cpp-runtime"
            or build.get("sourceCommit") != commit
            or build.get("dirtySource") is not False
            or receipt.get("schemaVersion") != "document-files.cpu-runtime-build.v1"
            or receipt.get("target") != "windows-x86_64"
            or receipt.get("windowsRuntimeLicenseSha256") != license_sha
            or receipt.get("executionQualification") is not False
        ):
            raise ValueError("cpu_build_identity_mismatch")
        inventory = manifest.get("files", [])
        if len({item["path"] for item in inventory}) != len(inventory) or set(names) != {
            "manifest.json",
            *[item["path"] for item in inventory],
        }:
            raise ValueError("cpu_build_archive_inventory_mismatch")
        if not {"build.json", "llama-server.exe", "llama-quantize.exe"} <= set(names):
            raise ValueError("cpu_build_required_artifacts_missing")
        for item in inventory:
            stage_file = file_record(safe_artifact(work / "stage", item["path"]))
            with archive.open(item["path"]) as stream:
                observed = hashlib.file_digest(stream, "sha256").hexdigest()
            if (
                observed != item["sha256"]
                or archive.getinfo(item["path"]).file_size != item["size"]
                or stage_file["sha256"] != item["sha256"]
                or stage_file["size"] != item["size"]
            ):
                raise ValueError("cpu_build_archive_hash_mismatch")
    internal = file_record(work / "stage/build.json")
    if internal["sha256"] != digest(build_bytes):
        raise ValueError("cpu_build_receipt_not_from_fresh_stage")
    sidecar = archive_path.with_suffix(archive_path.suffix + ".manifest.json")
    if sidecar.read_bytes() != manifest_bytes:
        raise ValueError("cpu_build_manifest_sidecar_mismatch")
    checksum = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if checksum.read_text().strip() != f"{archive_record['sha256']}  {archive_path.name}":
        raise ValueError("cpu_build_checksum_sidecar_mismatch")
    review_metadata = []
    if metadata_output is not None:
        for name, raw in (("cpu-build.json", build_bytes), ("cpu-manifest.json", manifest_bytes)):
            path = metadata_output / name
            with path.open("xb") as stream:
                stream.write(raw)
            review_metadata.append(file_record(path))
    return {
        "internalReceipt": internal,
        "packedReceipt": {
            "archivePath": str(archive_path),
            "member": "build.json",
            "sha256": digest(build_bytes),
        },
        "reviewMetadata": review_metadata,
        "artifacts": [
            archive_record,
            file_record(sidecar),
            file_record(checksum),
            file_record(archive_path.with_suffix(archive_path.suffix + ".cdx.json")),
        ],
    }


def run_candidate(
    kind,
    python,
    evidence,
    work,
    output,
    version,
    cache,
    license_path,
    commit,
    run_id,
    timeout=2400,
    *,
    supervisor=supervise,
):
    """Own fresh paths and one execution identity before dispatching a fixed builder."""
    result_path = evidence / "build-result.json"
    if result_path.exists():
        raise ValueError("build_result_evidence_exists")
    result = {
        "schemaVersion": "document-files.windows-build-result.v1",
        "executionRunId": run_id,
        "status": "failed",
        "buildKind": kind,
        "sourceCommit": commit,
        "independentRedistributionReview": "pending",
        "installedLicenseEntitlement": "not-assessed",
        "staticCrtAndSdkReview": "not-assessed",
        "releaseQualification": False,
        "outputs": None,
    }
    write_json(result_path, result)
    try:
        if (
            str(uuid.UUID(run_id)) != run_id
            or kind not in {"cpu", "recognition"}
            or not re.fullmatch(r"[a-f0-9]{40}", commit)
        ):
            raise ValueError("build_request_identity_invalid")
        if (
            work.exists()
            or work.is_symlink()
            or (kind == "cpu" and (output.exists() or output.is_symlink()))
        ):
            raise ValueError("build_requires_fresh_paths")
        work, output, evidence = work.resolve(), output.resolve(), evidence.resolve()
        if work in evidence.parents or evidence in work.parents or work == evidence:
            raise ValueError("build_evidence_must_be_separate")
        if kind == "cpu" and (
            work == output
            or work in output.parents
            or output in work.parents
            or output == evidence
            or output in evidence.parents
            or evidence in output.parents
        ):
            raise ValueError("build_output_must_be_separate")
        repository = Path(__file__).resolve().parents[1]
        builder = (
            repository
            / "scripts"
            / ("build_cpu_runtime.py" if kind == "cpu" else "build_recognition_native.py")
        )
        builder_sha = sha_file(builder)
        license_sha = file_record(license_path)["sha256"]
        command = [
            str(python),
            str(builder),
            "--target",
            "windows-x86_64",
            "--work",
            str(work),
            "--windows-runtime-license",
            str(license_path.absolute()),
        ]
        if kind == "cpu":
            if not re.fullmatch(r"b10853-cpu\.[1-9][0-9]*", version):
                raise ValueError("cpu_build_version_invalid")
            output.mkdir(parents=True, exist_ok=False)
            command += [
                "--output",
                str(output / f"llama-cpp-cpu-{version}-windows-x86_64.pack.zip"),
                "--version",
                version,
                "--jobs",
                "2",
            ]
        else:
            command += [
                "--cache",
                str(cache.absolute()),
                "--download",
                "--with-tessdata",
                "--jobs",
                "4",
            ]
        result["invocation"] = {
            "builder": file_record(builder),
            "supervisor": file_record(Path(__file__)),
            "python": file_record(python.resolve(strict=True)),
            "work": str(work),
            "output": str(output) if kind == "cpu" else str(work / "candidate"),
            "workExistedBeforeLaunch": False,
            "licenseSha256": license_sha,
            "commandSha256": digest(json.dumps(command).encode()),
        }
        write_json(result_path, result)
        execution = supervisor(command, evidence, run_id, timeout)
        result["executionReceipt"] = file_record(evidence / "build-execution.json")
        recorded = json.loads((evidence / "build-execution.json").read_text(encoding="utf-8"))
        if (
            recorded != execution
            or execution.get("executionRunId") != run_id
            or execution.get("status") != "completed"
            or execution.get("exitCode") != 0
            or execution.get("timedOut") is not False
            or execution.get("cleanupVerified") is not True
            or execution.get("interrupted") is not False
            or execution.get("errors") != []
        ):
            raise ValueError("build_execution_not_complete")
        if sha_file(builder) != builder_sha or sha_file(license_path) != license_sha:
            raise ValueError("build_input_changed")
        result["outputs"] = linked_outputs(
            kind,
            work,
            output,
            version,
            commit,
            builder_sha,
            license_sha,
            metadata_output=evidence,
        )
        result["status"] = "built-unreviewed"
    except (Exception, KeyboardInterrupt) as exc:
        result["failureCode"] = (
            str(exc) if isinstance(exc, ValueError) else "candidate_build_failed"
        )
    finally:
        result["endedAt"] = datetime.now(UTC).isoformat()
        write_json(result_path, result)
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        parser = argparse.ArgumentParser(description="Supervise one fresh Windows build candidate")
        parser.add_argument("--kind", choices=["cpu", "recognition"], required=True)
        for name in ("python", "evidence", "work", "output", "license"):
            parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--version", default="")
        parser.add_argument("--cache", type=Path, default=Path("unused"))
        parser.add_argument("--commit", required=True)
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--timeout", type=int, default=2400)
        args = parser.parse_args(sys.argv[2:])

        def interrupted(signum, frame):
            raise KeyboardInterrupt

        for name in ("SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), interrupted)
        result = run_candidate(
            args.kind,
            args.python,
            args.evidence,
            args.work,
            args.output,
            args.version,
            args.cache,
            args.license,
            args.commit,
            args.run_id,
            args.timeout,
        )
        return 0 if result["status"] == "built-unreviewed" else 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--terms-url", required=True)
    parser.add_argument("--terms-sha256", required=True)
    parser.add_argument("--terms-file", type=Path)
    parser.add_argument("--retrieved-at")
    args = parser.parse_args()
    prepare(
        json.loads(args.host.read_text(encoding="utf-8-sig")),
        args.output,
        args.terms_url,
        args.terms_sha256,
        source=args.terms_file,
        retrieved_at=args.retrieved_at,
    )


if __name__ == "__main__":
    raise SystemExit(main())
