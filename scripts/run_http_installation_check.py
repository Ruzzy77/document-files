#!/usr/bin/env python3
"""One bounded Linux installed-HTTP lifecycle check; never a release qualification.

Run with the final image's installed Python, selected local assets/packs and a
public multi-stage input. A dedicated loopback service/state is created; existing
services and administrator state are untouched. Raw failures/results are retained.
Use measure_cpu_execution.py around this command and capture_container_identity.py
on the host afterwards. Self-reported image IDs never become qualification proof.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import suppress
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
from execution_identity import checked_path, installed_tree, sha256, tree_digest, wheel_package


class CheckFailure(ValueError):
    """A fixed diagnostic code, never a response, credential or source path."""


def need(condition, code):
    if not condition:
        raise CheckFailure(code)


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def source_member(archive, member):
    with tarfile.open(archive, "r:gz") as source:
        matches = [m for m in source if Path(m.name).parts[1:] == tuple(member.split("/"))]
        need(
            len(matches) == 1
            and matches[0].isfile()
            and not Path(matches[0].name).is_absolute()
            and ".." not in Path(matches[0].name).parts,
            "source_member_invalid",
        )
        return hashlib.sha256(source.extractfile(matches[0]).read()).hexdigest()


def identity(args, config):
    import document_files
    from document_files.jobs import ModelProfile, resolve_profile_identity

    inventory = json.loads(
        checked_path(args.evidence_root, args.inventory, args.inventory_sha256).read_bytes()
    )
    need(
        inventory.get("schemaVersion") == "document-files.artifact-inventory.v2",
        "inventory_version",
    )
    assets = {a["id"]: a for a in inventory["artifacts"]}
    need(len(assets) == len(inventory["artifacts"]), "duplicate_artifacts")
    need(len(set(args.artifact)) == len(args.artifact), "duplicate_selection")
    selected = [assets[a] for a in args.artifact]
    for key in (args.core_artifact, args.source_artifact, args.image_artifact):
        need(key in args.artifact, "execution_artifact_not_selected")
    core, source, image = (
        assets[a] for a in (args.core_artifact, args.source_artifact, args.image_artifact)
    )
    need(
        core["kind"] == source["kind"] == "core" and image["kind"] == "image",
        "execution_artifact_kind",
    )
    base = (args.evidence_root / args.inventory).parent
    paths = {}
    for asset in selected:
        paths[asset["id"]] = checked_path(base, asset["path"], asset["sha256"])
    for asset in (core, source):
        ref = asset["buildReceipt"]
        receipt = json.loads(
            checked_path(args.evidence_root, ref["path"], ref["sha256"]).read_bytes()
        )
        need(
            receipt.get("schemaVersion") == "document-files.build-inventory.v2"
            and receipt.get("candidateMode") == "stable"
            and receipt.get("dirtySource") is False
            and receipt.get("sourceCommit") == inventory["sourceCommit"]
            and receipt.get("version") == inventory["version"]
            and receipt.get("artifacts", {}).get(paths[asset["id"]].name) == asset["sha256"],
            "clean_build_receipt_required",
        )
    package_root = Path(document_files.__file__).resolve().parent
    files = wheel_package(paths[core["id"]])
    need(installed_tree(package_root) == files, "installed_core_differs_from_wheel")
    for member, path in (
        ("scripts/run_http_installation_check.py", Path(__file__).resolve()),
        (
            "evaluation/execution_identity.py",
            Path(__file__).resolve().parents[1] / "evaluation/execution_identity.py",
        ),
    ):
        need(
            source_member(paths[source["id"]], member) == sha256(path),
            "runner_differs_from_source_artifact",
        )
    profile = config["profiles"][args.profile]
    need(profile["type"] == "local-pack", "actual_local_pack_required")
    settings = {k: v for k, v in profile.items() if k not in {"type", "revision"}}
    packs = resolve_profile_identity(
        ModelProfile(args.profile, profile["revision"], "local-pack", settings)
    )
    for field, kind in (
        ("runtimeId", "runtime"),
        ("modelId", "model"),
        ("recognitionPackId", "recognition"),
    ):
        need(
            packs.get(field)
            and any(
                a["kind"] == kind and a.get("manifestSha256") == packs[field] for a in selected
            ),
            "installed_pack_selection_mismatch",
        )
    return (
        inventory,
        {
            "verification": "installed-wheel-and-runner-source-exact.v1",
            "coreArtifactId": core["id"],
            "coreArtifactSha256": core["sha256"],
            "sourceArtifactId": source["id"],
            "sourceArtifactSha256": source["sha256"],
            "recorderSha256": source_member(
                paths[source["id"]], "scripts/measure_cpu_execution.py"
            ),
            "packageTreeSha256": tree_digest(files),
            "runnerSha256": sha256(Path(__file__)),
            "selectedImageArtifactId": image["id"],
            "actualImageIdentityVerified": False,
            "packManifestSha256": packs,
        },
        package_root,
        files,
    )


def processes(root_pid, proc=Path("/proc")):
    """PID+start ticks prevent PID reuse being mistaken for residual children."""
    records = {}
    for path in proc.glob("[0-9]*/stat"):
        try:
            text = path.read_text()
            prefix, rest = text.rsplit(")", 1)
            fields = rest.split()
            pid = int(path.parent.name)
            records[pid] = {
                "pid": pid,
                "parent": int(fields[1]),
                "startTicks": fields[19],
                "name": prefix.split("(", 1)[1],
                "state": fields[0],
            }
        except (OSError, ValueError, IndexError):
            continue
    owned = {root_pid}
    while True:
        expanded = owned | {pid for pid, r in records.items() if r["parent"] in owned}
        if expanded == owned:
            break
        owned = expanded
    return [records[pid] for pid in sorted(owned - {root_pid}) if pid in records]


def alive(record, proc=Path("/proc")):
    try:
        fields = (proc / str(record["pid"]) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] == record["startTicks"] and fields[0] != "Z"
    except (OSError, IndexError):
        return False


class HTTP:
    def __init__(self, port, token, deadline):
        self.port, self.token, self.deadline = port, token, deadline

    def request(self, method, path, body=b"", headers=None, authenticated=True):
        # Fixed loopback only: no redirect, proxy, arbitrary endpoint or URL fetch.
        need(path.startswith("/v1/") and not path.startswith("//"), "invalid_internal_request_path")
        remaining = self.deadline - time.monotonic()
        need(remaining > 0, "runner_deadline_exceeded")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=min(10, remaining))
        selected = dict(headers or {})
        if authenticated:
            selected["Authorization"] = "Bearer " + self.token
        try:
            connection.request(method, path, body=body, headers=selected)
            response = connection.getresponse()
            data = response.read(64 * 1024 * 1024 + 1)
            need(len(data) <= 64 * 1024 * 1024, "response_too_large")
            return response.status, json.loads(data)
        finally:
            connection.close()

    def wait(self, predicate, *, seconds=None):
        end = min(self.deadline, time.monotonic() + seconds) if seconds else self.deadline
        while time.monotonic() < end:
            result = predicate()
            if result:
                return result
            time.sleep(0.2)
        raise CheckFailure("runner_wait_timeout")


def run_sequence(args, out, report):
    config = json.loads(args.config.read_bytes())
    inventory, execution, package_root, files = identity(args, config)
    report.update(
        sourceCommit=inventory["sourceCommit"],
        version=inventory["version"],
        dirtySource=False,
        executionKind="actual-installed",
        coreExecution=execution,
    )
    need(
        sys.platform == "linux" and Path("/proc/self/stat").exists(),
        "linux_process_evidence_required",
    )
    need(args.input.is_file() and not args.input.is_symlink(), "input_must_be_regular_file")
    data = args.input.read_bytes()
    need(0 < len(data) <= 32 * 1024 * 1024, "input_size_limit")
    input_path = out / ("input." + args.format)
    with input_path.open("xb") as stream:
        stream.write(data)
    input_path.chmod(0o600)
    digest = sha256(input_path)
    report["input"] = {
        "path": input_path.relative_to(args.evidence_root).as_posix(),
        "sha256": digest,
    }
    if args.review_specification is not None:
        # Frozen before the service/model starts; never passed to its request.
        specification = json.loads(args.review_specification.read_bytes())
        need(
            specification.get("schemaVersion") == "document-files.review-specification.v1",
            "review_specification_version",
        )
        specification_path = out / "review-specification.json"
        write_json(specification_path, specification)
        report["reviewSpecification"] = {
            "path": specification_path.relative_to(args.evidence_root).as_posix(),
            "sha256": sha256(specification_path),
        }
    state_root = out / "service-state"
    token = secrets.token_urlsafe(48)
    deadline = time.monotonic() + args.timeout
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = HTTP(port, token, deadline)
    service = None
    observed_children = []

    observations = {}

    def check(name, condition, observed=None):
        if observed is not None:
            observations[name] = observed
        report["tests"].append({"id": name, "passed": bool(condition)})
        need(condition, name + "_failed")

    def capture(name, value):
        path = out / (name + ".json")
        write_json(path, value)
        return {"path": path.relative_to(args.evidence_root).as_posix(), "sha256": sha256(path)}

    def status(job):
        code, value = client.request("GET", "/v1/jobs/" + job)
        need(code == 200, "job_status_failed")
        return value

    def terminal(job):
        value = status(job)
        return value if value["executionStatus"] not in {"queued", "running"} else None

    def stop():
        nonlocal service
        if service is not None:
            service.terminate()
            try:
                service.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(service.pid, signal.SIGKILL)
                service.wait(timeout=5)
            service = None

    with tempfile.TemporaryDirectory(prefix="df-http-install-") as temp:
        private_config = Path(temp) / "server.json"
        write_json(
            private_config,
            {
                "storageRoot": str(state_root),
                "profiles": {args.profile: config["profiles"][args.profile]},
            },
        )

        def start():
            nonlocal service
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"}
            }
            env["DOCUMENT_FILES_SERVER_TOKEN"] = token
            service = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "document_files.cli",
                    "serve",
                    "--config",
                    str(private_config),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            def ready():
                need(service.poll() is None, "service_start_failed")
                try:
                    return client.request("GET", "/v1/capabilities")[0] == 200
                except (OSError, http.client.HTTPException):
                    return False

            client.wait(ready, seconds=30)

        try:
            start()
            anonymous_status = client.request("GET", "/v1/capabilities", authenticated=False)[0]
            invalid_status = client.request(
                "GET",
                "/v1/capabilities",
                headers={"Authorization": "Bearer invalid"},
                authenticated=False,
            )[0]
            check(
                "authentication",
                anonymous_status == invalid_status == 401,
                {"anonymousStatus": anonymous_status, "invalidTokenStatus": invalid_status},
            )
            rejected_bodies, rejected_options = [], []
            for bad in (
                {"path": "/etc/passwd"},
                {"url": "https://example.invalid/source.pdf"},
                {"endpoint": "http://127.0.0.1:1"},
            ):
                code, _ = client.request(
                    "POST",
                    "/v1/jobs",
                    json.dumps(bad).encode(),
                    {"Content-Type": "application/json"},
                )
                rejected_bodies.append(code)
                need(code == 415, "non_byte_input_accepted")
            for bad in (
                {"path": "/etc/passwd"},
                {"url": "https://example.invalid/source.pdf"},
                {"endpoint": "http://127.0.0.1:1"},
            ):
                code, value = client.request(
                    "POST",
                    "/v1/jobs",
                    data,
                    {
                        "Content-Type": "application/octet-stream",
                        "X-Document-Format": args.format,
                        "X-Model-Profile": args.profile,
                        "X-Extraction-Options": json.dumps(bad),
                    },
                )
                rejected_options.append(
                    {"status": code, "error": value.get("error", {}).get("code")}
                )
                need(
                    code == 400 and value.get("error", {}).get("code") == "invalid-options",
                    "caller_path_url_or_endpoint_option_accepted",
                )
            check(
                "no_path_or_url_input",
                True,
                {"bodyStatuses": rejected_bodies, "optionErrors": rejected_options},
            )
            headers = {
                "Content-Type": "application/octet-stream",
                "X-Document-Format": args.format,
                "X-Model-Profile": args.profile,
                "Idempotency-Key": secrets.token_hex(16),
                "X-Extraction-Options": json.dumps(
                    {
                        "maxModelCalls": 1,
                        "completionSeconds": args.seconds,
                        "reconstructionContext": False,
                    }
                ),
            }
            code, first = client.request("POST", "/v1/jobs", data, headers)
            need(code == 202, "initial_submit_failed")
            job = first["jobId"]
            report["jobId"] = job
            code, same = client.request("POST", "/v1/jobs", data, headers)
            check(
                "idempotency_same",
                code == 202 and same.get("jobId") == job,
                {"status": code, "initialJobId": job, "repeatedJobId": same.get("jobId")},
            )
            code, conflict = client.request("POST", "/v1/jobs", data + b" ", headers)
            check(
                "idempotency_conflict",
                code == 409 and conflict.get("error", {}).get("code") == "idempotency-conflict",
                {"status": code, "error": conflict.get("error", {}).get("code")},
            )
            snapshot = state_root / "uploads" / f"{job}.{args.format}"
            check(
                "input_snapshot",
                sha256(snapshot) == digest and first["sha256"] == digest,
                {"snapshotSha256": sha256(snapshot), "submittedSha256": first["sha256"]},
            )
            done = client.wait(lambda: terminal(job))
            code, partial = client.request("GET", f"/v1/jobs/{job}/result")
            report["partialResult"] = capture("initial-partial", partial)
            check(
                "explicit_budget",
                code == 200
                and done["status"] == "partial"
                and partial.get("extraction", {}).get("modelCalls") == 1
                and any(
                    i.get("code") == "model_call_budget_exceeded" for i in partial.get("issues", [])
                ),
                {
                    "httpStatus": code,
                    "jobStatus": done["status"],
                    "partialResult": report["partialResult"],
                },
            )
            grant = {"additionalBudget": {"maxModelCalls": args.max_calls - 1}}
            report["budget"] = {
                "maxModelCalls": args.max_calls,
                "completionSeconds": args.seconds,
                "runnerTimeoutSeconds": args.timeout,
            }
            code, _ = client.request("POST", f"/v1/jobs/{job}/resume", json.dumps(grant).encode())
            need(code == 202, "budget_resume_failed")

            def actual_child():
                nonlocal observed_children
                need(
                    status(job)["executionStatus"] in {"queued", "running"},
                    "job_finished_before_cancel_window",
                )
                observed_children = processes(service.pid)
                return any("llama-server" in child["name"] for child in observed_children)

            client.wait(actual_child)
            report["cancelChildren"] = capture("cancel-children", observed_children)
            need(
                client.request("POST", f"/v1/jobs/{job}/cancel")[0] == 202, "cancel_request_failed"
            )
            cancelled = client.wait(lambda: terminal(job), seconds=30)
            client.wait(lambda: not any(alive(p) for p in observed_children), seconds=30)
            check(
                "cancel_tree",
                cancelled["status"] == "cancelled" and bool(observed_children),
                {
                    "jobStatus": cancelled["status"],
                    "childrenBefore": observed_children,
                    "childrenAliveAfter": [p for p in observed_children if alive(p)],
                },
            )
            code, kept = client.request("GET", f"/v1/jobs/{job}/result")
            need(code == 200 and kept.get("resultRevision", 0) > 0, "cancel_checkpoint_missing")
            report["cancelledResult"] = capture("cancelled-result", kept)
            stop()
            start()
            code, persisted = client.request("GET", f"/v1/jobs/{job}/result")
            check(
                "persistent_results",
                code == 200 and persisted == kept and sha256(snapshot) == digest,
                {
                    "httpStatus": code,
                    "before": report["cancelledResult"],
                    "after": capture("persisted-result", persisted),
                    "snapshotSha256": sha256(snapshot),
                },
            )
            # No second grant: cancellation cannot silently enlarge total budget.
            need(
                client.request("POST", f"/v1/jobs/{job}/resume", b"{}")[0] == 202,
                "checkpoint_resume_failed",
            )
            # Interrupt an actually running child through service shutdown, then
            # prove restart does not automatically replay the durable job.
            client.wait(actual_child)
            restarting_children = list(observed_children)
            before_restart = status(job)
            stop()
            need(not any(alive(p) for p in restarting_children), "restart_children_survived")
            start()
            restarted = status(job)
            check(
                "restart_interrupted",
                restarted["status"] == "interrupted"
                and restarted["attempt"] == before_restart["attempt"],
                {
                    "before": before_restart,
                    "after": restarted,
                    "childrenBefore": restarting_children,
                    "childrenAliveAfterStop": [p for p in restarting_children if alive(p)],
                },
            )
            code, restart_result = client.request("GET", f"/v1/jobs/{job}/result")
            report["restartResult"] = capture("restart-result", restart_result)
            need(
                code == 200 and restart_result.get("resultRevision", 0) >= kept["resultRevision"],
                "restart_checkpoint_missing",
            )
            time.sleep(0.5)
            after_delay = status(job)
            observations["restart_interrupted"]["afterDelay"] = after_delay
            need(
                after_delay["attempt"] == restarted["attempt"]
                and after_delay["status"] == "interrupted",
                "restart_automatically_replayed",
            )
            need(
                client.request("POST", f"/v1/jobs/{job}/resume", b"{}")[0] == 202,
                "interrupted_resume_failed",
            )
            final_status = client.wait(lambda: terminal(job))
            code, result = client.request("GET", f"/v1/jobs/{job}/result")
            report["aiResult"] = capture("final-result", result)
            check(
                "resume_checkpoint",
                code == 200
                and final_status["attempt"] > cancelled["attempt"]
                and result.get("resultRevision", 0) >= kept["resultRevision"]
                and result.get("extraction", {}).get("modelCalls", args.max_calls + 1)
                <= args.max_calls,
                {
                    "httpStatus": code,
                    "finalStatus": final_status,
                    "cancelledAttempt": cancelled["attempt"],
                    "result": report["aiResult"],
                    "cancelledResult": report["cancelledResult"],
                },
            )
            model = result.get("provenance", {}).get("model") or {}
            check(
                "actual_ai_complete",
                final_status["status"] == "complete"
                and result.get("extraction", {}).get("status") == "complete"
                and result.get("validation", {}).get("valid") is True
                and not result["validation"].get("errors")
                and result.get("source", {}).get("sha256") == digest
                and model.get("adapter") == "managed-llama-cpp.v1"
                and model.get("runtimeManifestSha256")
                == execution["packManifestSha256"]["runtimeId"]
                and model.get("modelManifestSha256") == execution["packManifestSha256"]["modelId"],
                {"jobStatus": final_status["status"], "result": report["aiResult"]},
            )
            deleted = client.request("DELETE", "/v1/jobs/" + job)
            deleted_status = client.request("GET", "/v1/jobs/" + job)[0]
            upload_exists = snapshot.exists()
            result_exists = (state_root / "results" / f"{job}.sqlite3").exists()
            check(
                "delete_results",
                deleted[0] == 200
                and deleted[1].get("deleted") is True
                and deleted_status == 404
                and not upload_exists
                and not result_exists,
                {
                    "deleteStatus": deleted[0],
                    "deleted": deleted[1].get("deleted"),
                    "lookupStatus": deleted_status,
                    "uploadExists": upload_exists,
                    "resultDatabaseExists": result_exists,
                },
            )
            need(
                installed_tree(package_root) == files and sha256(args.input) == digest,
                "execution_or_input_changed",
            )
            report["checksPassed"] = True
        finally:
            if service is not None:
                report["remainingChildrenBeforeCleanup"] = processes(service.pid)
            cleanup_children = report.get("remainingChildrenBeforeCleanup", [])
            stop()
            forced = [p for p in cleanup_children if alive(p)]
            report["cleanupForcedChildren"] = forced
            # A failed service must not leave this runner's model children alive.
            # Never signal reused PIDs or unrelated processes.
            for child in forced:
                if alive(child):
                    with suppress(ProcessLookupError):
                        os.kill(child["pid"], signal.SIGKILL)
            if forced:
                report["checksPassed"] = False
            residual = [p for p in observed_children if alive(p)]
            report["residualCancelChildren"] = residual
            if residual:
                report["checksPassed"] = False
            report["lifecycleEvidence"] = capture(
                "lifecycle-evidence",
                {
                    "schemaVersion": "document-files.http-lifecycle-observations.v1",
                    "executionRunId": report["executionRunId"],
                    "jobId": report.get("jobId"),
                    "observations": observations,
                },
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", required=True, help="New child directory below evidence-root")
    parser.add_argument("--inventory", required=True, help="Evidence-root relative inventory path")
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--artifact", action="append", required=True)
    for name in ("core-artifact", "source-artifact", "image-artifact", "profile"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--review-specification", type=Path)
    parser.add_argument(
        "--format",
        required=True,
        choices=["txt", "md", "html", "pdf", "docx", "xlsx", "pptx", "hwp", "hwpx"],
    )
    parser.add_argument("--max-calls", type=int, required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args(argv)
    need(
        4 <= args.max_calls <= 100
        and 1 <= args.seconds <= 3600
        and args.seconds < args.timeout <= 7200,
        "invalid_finite_budget",
    )
    args.evidence_root = args.evidence_root.resolve()
    need(
        Path(args.output).name == args.output and args.output not in {".", ".."},
        "invalid_output_name",
    )
    out = args.evidence_root / args.output
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    report = {
        "schemaVersion": "document-files.http-installation-run.v1",
        "observationProfile": "in-container-loopback.v1",
        "notCovered": ["host-published-port-access", "independent-document-quality-suite"],
        "passed": False,
        "checksPassed": False,
        "releaseQualification": False,
        "executionRunId": os.environ.get("DOCUMENT_FILES_EXECUTION_RUN_ID"),
        "artifactInventory": {"path": args.inventory, "sha256": args.inventory_sha256},
        "artifacts": args.artifact,
        "plannedTests": [
            "authentication",
            "no_path_or_url_input",
            "idempotency_same",
            "idempotency_conflict",
            "input_snapshot",
            "explicit_budget",
            "cancel_tree",
            "persistent_results",
            "restart_interrupted",
            "resume_checkpoint",
            "actual_ai_complete",
            "delete_results",
        ],
        "tests": [],
        "qualificationBlockers": [
            "independent-image-and-isolation-receipts-required",
            "independent-content-review-required",
        ],
    }

    def terminated(_signal, _frame):
        raise CheckFailure("runner_cancelled")

    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        run_sequence(args, out, report)
    except (Exception, KeyboardInterrupt) as exc:
        report["checksPassed"] = False
        report["failure"] = str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__
    finally:
        signal.signal(signal.SIGTERM, previous)
        write_json(out / "report.json", report)
    return 0 if report["checksPassed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
