#!/usr/bin/env python3
"""Measure one bounded command inside an already isolated Linux cgroup-v2 container.

This recorder does not create isolation, install dependencies or qualify content.
It refuses missing limits instead of substituting process RSS for cgroup accounting.
Run it as the container's non-root user with only loopback networking and CPU packs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import signal
import subprocess
import time
import uuid
from pathlib import Path

CEILING = 16 * 1024**3


def read_int(path: Path) -> int:
    value = path.read_text().strip()
    if not value.isdecimal():
        raise ValueError(f"finite_measurement_required:{path.name}")
    return int(value)


def counters(path: Path, *, required=()) -> dict[str, int]:
    result = {}
    for line in path.read_text().splitlines():
        key, value = line.split()
        if key in result or not value.isdecimal():
            raise ValueError("invalid_measurement_counter")
        result[key] = int(value)
    if not result or not set(required) <= result.keys():
        raise ValueError("missing_measurement_counter")
    return result


def snapshot(cgroup: Path) -> dict:
    process_ids = [int(pid) for pid in (cgroup / "cgroup.procs").read_text().split()]
    if not process_ids or any(pid <= 0 for pid in process_ids):
        raise ValueError("invalid_cgroup_process_list")
    return {
        "elapsedMonotonic": time.monotonic(),
        "memoryCurrentBytes": read_int(cgroup / "memory.current"),
        "memoryPeakBytes": read_int(cgroup / "memory.peak"),
        "memorySwapCurrentBytes": read_int(cgroup / "memory.swap.current"),
        "memoryEvents": counters(cgroup / "memory.events", required=("oom", "oom_kill", "max")),
        "memoryStat": counters(
            cgroup / "memory.stat",
            required=("anon", "file", "kernel_stack", "pagetables", "sock", "slab"),
        ),
        "cpuStat": counters(
            cgroup / "cpu.stat", required=("usage_usec", "user_usec", "system_usec")
        ),
        "pidsCurrent": read_int(cgroup / "pids.current"),
        "pidsEvents": counters(cgroup / "pids.events", required=("max",)),
        "processIds": sorted(set(process_ids)),
    }


def preflight(
    cgroup: Path,
    *,
    net: Path = Path("/sys/class/net"),
    dev: Path = Path("/dev"),
    proc: Path = Path("/proc"),
) -> dict:
    if os.geteuid() == 0:
        raise ValueError("non_root_recorder_required")
    # A private cgroup namespace roots this process in the accounting directory.
    if (proc / "self/cgroup").read_text().strip() != "0::/":
        raise ValueError("private_cgroup_v2_namespace_required")
    if str(os.getpid()) not in (cgroup / "cgroup.procs").read_text().split():
        raise ValueError("recorder_outside_measured_cgroup")
    status = dict(line.split(":", 1) for line in (proc / "self/status").read_text().splitlines())
    if (
        any(int(status[key].strip(), 16) != 0 for key in ("CapEff", "CapPrm", "CapAmb"))
        or status["NoNewPrivs"].strip() != "1"
    ):
        raise ValueError("unprivileged_isolation_required")
    maximum = read_int(cgroup / "memory.max")
    swap = read_int(cgroup / "memory.swap.max")
    pids_max = read_int(cgroup / "pids.max")
    quota, period = (cgroup / "cpu.max").read_text().split()
    if not quota.isdecimal() or not period.isdecimal() or int(period) <= 0:
        raise ValueError("finite_cpu_quota_required")
    cpu_count = int(quota) / int(period)
    patterns = ("nvidia*", "dri", "kfd", "dxg", "accel", "vfio", "mali*", "galcore")
    devices = sorted({path.name for pattern in patterns for path in dev.glob(pattern)})
    interfaces = sorted(path.name for path in net.iterdir())
    if not 0 < maximum <= CEILING or swap != 0 or not 0 < cpu_count <= 4 or pids_max <= 0:
        raise ValueError("cpu16gb_limits_not_enforced")
    if interfaces != ["lo"] or devices:
        raise ValueError("offline_cpu_isolation_not_enforced")
    return {
        "memoryCeilingBytes": maximum,
        "memorySwapMaxBytes": swap,
        "cpuQuota": cpu_count,
        "pidsMax": pids_max,
        "networkInterfaces": interfaces,
        "gpuDeviceCount": len(devices),
        "effectiveCapabilities": 0,
        "noNewPrivileges": True,
        "privateCgroupNamespace": True,
    }


def validate_samples(samples):
    if len(samples) < 2:
        raise ValueError("incomplete_measurements")
    for previous, current in zip(samples, samples[1:], strict=False):
        if current["elapsedMonotonic"] < previous["elapsedMonotonic"]:
            raise ValueError("nonmonotonic_measurements")
        if current["memoryPeakBytes"] < previous["memoryPeakBytes"]:
            raise ValueError("cgroup_peak_reset_during_measurement")
        for name in ("memoryEvents", "cpuStat", "pidsEvents"):
            if any(current[name].get(key, -1) < value for key, value in previous[name].items()):
                raise ValueError("measurement_counters_regressed")
    for sample in samples:
        if sample["memoryPeakBytes"] < sample["memoryCurrentBytes"] or sample["pidsCurrent"] < 1:
            raise ValueError("inconsistent_measurement")


def execution(limits: dict, samples: list[dict], *, exit_code: int | None, timed_out: bool) -> dict:
    first, last = (samples[0], samples[-1]) if samples else ({}, {})
    return {
        "device": "cpu" if limits else None,
        "gpuUsed": False if limits.get("gpuDeviceCount") == 0 else None,
        "offline": True if limits.get("networkInterfaces") == ["lo"] else None,
        "networkBlocked": True if limits.get("networkInterfaces") == ["lo"] else None,
        "networkMode": "none" if limits else None,
        "memoryCeilingVerified": bool(limits),
        **limits,
        "cgroupMemoryPeakBytes": max((item["memoryPeakBytes"] for item in samples), default=None),
        "memorySwapPeakBytes": max(
            (item["memorySwapCurrentBytes"] for item in samples), default=None
        ),
        "oomEventsDelta": last["memoryEvents"]["oom"] - first["memoryEvents"]["oom"]
        if samples
        else None,
        "oomKillEventsDelta": last["memoryEvents"]["oom_kill"] - first["memoryEvents"]["oom_kill"]
        if samples
        else None,
        "exitCode": exit_code,
        "timedOut": timed_out,
        "elapsedSeconds": last["elapsedMonotonic"] - first["elapsedMonotonic"] if samples else None,
    }


def write_new(path: Path, value: dict) -> str:
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as output:
        output.write(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def safe_path(root: Path, relative: str, *, must_be_new=False) -> Path:
    path = Path(relative)
    target = root / path
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("unsafe_evidence_path")
    if not target.resolve().is_relative_to(root) or any(
        p.is_symlink() for p in (target, *target.parents) if p.is_relative_to(root)
    ):
        raise ValueError("unsafe_evidence_path")
    if must_be_new and target.exists():
        raise ValueError("evidence_path_exists")
    return target


def stop_child(child):
    # Signal the whole session group even if its leader already exited: an exited
    # launcher may leave model descendants alive. Never kill arbitrary cgroup PIDs.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, sig)
        if child.poll() is None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if sig == signal.SIGKILL:
                    raise
    child.wait(timeout=10)


def collect_reports(root, reports, run_id, errors):
    outputs = []
    for name in reports:
        try:
            path = safe_path(root, name)
            if not path.is_file():
                raise ValueError("missing_report")
            raw = path.read_bytes()
            report = json.loads(raw)
            if report.get("executionRunId") != run_id:
                raise ValueError("report_execution_id_mismatch")
            outputs.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        except (OSError, ValueError, TypeError, AttributeError) as error:
            errors.append({"stage": "report", "errorType": type(error).__name__})
    return outputs


def record_run(root, output, identity, command, timeout, reports, *, cgroup=Path("/sys/fs/cgroup")):
    """Always retain failed measurements after a valid writable output was reserved."""
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    run_id = str(uuid.uuid4())
    errors, samples = [], []
    limits, limits_after = {}, {}
    child, timed_out = None, False
    stage = "preflight"
    try:
        limits = preflight(cgroup)
        stage = "initial_sample"
        samples.append(snapshot(cgroup))
        stage = "launch"
        started = time.monotonic()
        child = subprocess.Popen(
            command,
            env={**os.environ, "DOCUMENT_FILES_EXECUTION_RUN_ID": run_id},
            start_new_session=True,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        stage = "sample"
        while child.poll() is None:
            if time.monotonic() - started >= timeout:
                timed_out = True
                break
            time.sleep(min(1, max(0, timeout - (time.monotonic() - started))))
            samples.append(snapshot(cgroup))
        # Conservatively refuse a run whose completion was observed after its
        # deadline, even if the launcher exited between polling opportunities.
        timed_out = timed_out or time.monotonic() - started >= timeout
    except (Exception, KeyboardInterrupt) as error:
        errors.append({"stage": stage, "errorType": type(error).__name__})
    finally:
        if child is not None:
            try:
                stop_child(child)
            except (Exception, KeyboardInterrupt) as error:
                errors.append({"stage": "cleanup", "errorType": type(error).__name__})
        try:
            samples.append(snapshot(cgroup))
            # SIGKILL is asynchronous; allow the namespace init to reap owned
            # descendants before calling a transient PID a leaked subprocess.
            if child is not None and len(samples) >= 2:
                initial_pids = set(samples[0]["processIds"])
                for _ in range(10):
                    if not set(samples[-1]["processIds"]) - initial_pids:
                        break
                    time.sleep(0.1)
                    samples.append(snapshot(cgroup))
        except (Exception, KeyboardInterrupt) as error:
            errors.append({"stage": "final_sample", "errorType": type(error).__name__})
        try:
            limits_after = preflight(cgroup)
        except (Exception, KeyboardInterrupt) as error:
            errors.append({"stage": "final_preflight", "errorType": type(error).__name__})
    try:
        validate_samples(samples)
    except (ValueError, KeyError, TypeError) as error:
        errors.append({"stage": "sample_validation", "errorType": type(error).__name__})
    residual = (
        sorted(set(samples[-1]["processIds"]) - set(samples[0]["processIds"]))
        if len(samples) >= 2
        else []
    )
    if residual:
        errors.append({"stage": "residual_processes", "errorType": "UnreapedCommandProcesses"})
    outputs = collect_reports(root, reports, run_id, errors)
    observed = execution(
        limits, samples, exit_code=child.returncode if child else None, timed_out=timed_out
    )
    observed.update(
        limitsUnchanged=bool(limits) and limits_after == limits,
        recorderErrors=errors,
        measurementComplete=not errors and bool(limits) and limits_after == limits,
        residualProcessIds=residual,
    )
    if not observed["limitsUnchanged"]:
        observed.update(
            memoryCeilingVerified=False, networkBlocked=False, offline=None, gpuUsed=None
        )
    raw_path = output / "measurements.json"
    measurement_link = None
    try:
        raw_digest = write_new(
            raw_path,
            {
                "schemaVersion": "document-files.cpu-measurements.v1",
                "executionRunId": run_id,
                "limitsBefore": limits,
                "limitsAfter": limits_after,
                "execution": observed,
                "samples": samples,
            },
        )
        measurement_link = {"path": raw_path.relative_to(root).as_posix(), "sha256": raw_digest}
    except OSError as error:
        errors.append({"stage": "measurement_output", "errorType": type(error).__name__})
        observed["measurementComplete"] = False
    # If the raw output could not be saved, a failure receipt can still explain
    # the missing link. Neither file is ever overwritten to manufacture success.
    write_new(
        output / "receipt.json",
        {
            "schemaVersion": "document-files.execution-receipt.v1",
            "recorderSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            **{
                key: identity[key]
                for key in ("version", "sourceCommit", "dirtySource", "artifacts")
            },
            "executionRunId": run_id,
            "outputs": outputs,
            "commandSha256": hashlib.sha256(json.dumps(command).encode()).hexdigest(),
            "execution": observed,
            "measurements": measurement_link,
        },
    )
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument(
        "--output", required=True, help="New relative directory beneath evidence root"
    )
    parser.add_argument(
        "--identity", required=True, type=Path, help="Clean candidate identity JSON"
    )
    parser.add_argument(
        "--timeout", required=True, type=int, help="Whole command seconds, <= 21600"
    )
    parser.add_argument(
        "--report", action="append", default=[], help="New report path relative to evidence root"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    root = args.evidence_root.resolve()
    try:
        output = safe_path(root, args.output, must_be_new=True)
        report_paths = [safe_path(root, name, must_be_new=True) for name in args.report]
        if len(set(report_paths)) != len(report_paths) or any(
            p.is_relative_to(output) for p in report_paths
        ):
            raise ValueError("reports_must_not_overlap_recorder_output")
    except ValueError as error:
        parser.error(str(error))
    if not command or not 0 < args.timeout <= 21600:
        parser.error("command and timeout between 1 and 21600 seconds required")
    if platform.system() != "Linux" or os.geteuid() == 0:
        parser.error("run as a non-root Linux container user")
    identity = json.loads(args.identity.read_text())
    if identity.get("dirtySource") is not False or not all(
        identity.get(key) for key in ("version", "sourceCommit", "artifacts")
    ):
        parser.error("clean exact candidate identity required")

    def interrupted(signum, frame):
        raise InterruptedError("recorder_interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    observed = record_run(
        root,
        output,
        identity,
        command,
        args.timeout,
        [p.relative_to(root).as_posix() for p in report_paths],
    )
    print(
        json.dumps(
            {
                "receipt": (output / "receipt.json").relative_to(root).as_posix(),
                "exitCode": observed["exitCode"],
                "timedOut": observed["timedOut"],
            }
        )
    )
    success = (
        observed["exitCode"] == 0 and not observed["timedOut"] and observed["measurementComplete"]
    )
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
