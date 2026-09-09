"""Recorder contract tests; synthetic measurements never qualify a release."""

import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path

import pytest


@pytest.fixture
def recorder():
    path = Path(__file__).parents[1] / "scripts/measure_cpu_execution.py"
    spec = importlib.util.spec_from_file_location("cpu_recorder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 10001, raising=False)
    group, net, dev = (tmp_path / name for name in ("cgroup", "net", "dev"))
    for directory in (group, net, dev):
        directory.mkdir()
    (net / "lo").mkdir()
    for name, value in {
        "memory.max": 16 * 1024**3,
        "memory.swap.max": 0,
        "cpu.max": "400000 100000",
        "pids.max": 256,
        "cgroup.procs": os.getpid(),
        "memory.current": 100,
        "memory.peak": 200,
        "memory.swap.current": 0,
        "memory.events": "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n",
        "memory.stat": "anon 50\nfile 50\nkernel_stack 2\npagetables 3\nsock 0\nslab 5\n",
        "cpu.stat": "usage_usec 100\nuser_usec 50\nsystem_usec 50\n",
        "pids.current": 1,
        "pids.events": "max 0\n",
    }.items():
        (group / name).write_text(str(value))
    proc = tmp_path / "proc" / "self"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text("0::/\n")
    (proc / "status").write_text("CapEff:\t0\nCapPrm:\t0\nCapAmb:\t0\nNoNewPrivs:\t1\n")
    return group, net, dev


def test_cgroup_limits_and_no_gpu(recorder, isolated):
    group, net, dev = isolated
    result = recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc")
    assert result["cpuQuota"] == 4
    assert result["memoryCeilingBytes"] == 16 * 1024**3
    (dev / "dri").mkdir()
    with pytest.raises(ValueError, match="isolation"):
        recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc")


@pytest.mark.parametrize(
    "name,value",
    [
        ("memory.max", "max"),
        ("memory.swap.max", "1"),
        ("cpu.max", "max 100000"),
        ("cpu.max", "800000 100000"),
    ],
)
def test_missing_or_invalid_bound_refused(recorder, isolated, name, value):
    group, net, dev = isolated
    (group / name).write_text(value)
    with pytest.raises(ValueError):
        recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc")


def test_egress_interface_refused(recorder, isolated):
    group, net, dev = isolated
    (net / "eth0").mkdir()
    with pytest.raises(ValueError, match="isolation"):
        recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc")


def test_peak_and_oom_are_retained_not_passed(recorder, isolated):
    group, net, dev = isolated
    sample = {
        "elapsedMonotonic": 1,
        "memoryPeakBytes": 100,
        "memorySwapCurrentBytes": 0,
        "memoryEvents": {"oom": 1, "oom_kill": 1},
    }
    later = {
        **sample,
        "elapsedMonotonic": 4,
        "memoryPeakBytes": 300,
        "memoryEvents": {"oom": 2, "oom_kill": 2},
    }
    result = recorder.execution(
        recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc"),
        [sample, later],
        exit_code=137,
        timed_out=False,
    )
    assert result["oomKillEventsDelta"] == result["oomEventsDelta"] == 1
    assert result["cgroupMemoryPeakBytes"] == 300
    assert result["exitCode"] == 137
    assert "passed" not in result


def test_evidence_never_overwritten(recorder, tmp_path):
    output = tmp_path / "receipt.json"
    recorder.write_new(output, {"value": 1})
    with pytest.raises(FileExistsError):
        recorder.write_new(output, {"value": 2})


@pytest.mark.parametrize("field,value", [("CapEff", "1"), ("CapPrm", "1"), ("NoNewPrivs", "0")])
def test_capabilities_and_privilege_escalation_are_actually_checked(
    recorder, isolated, field, value
):
    group, net, dev = isolated
    status = group.parent / "proc/self/status"
    text = status.read_text()
    text = text.replace(
        f"{field}:\t" + ("1" if field == "NoNewPrivs" else "0"), f"{field}:\t{value}"
    )
    status.write_text(text)
    with pytest.raises(ValueError, match="unprivileged"):
        recorder.preflight(group, net=net, dev=dev, proc=group.parent / "proc")


def test_snapshot_includes_memory_stat_pids_swap_and_required_counters(recorder, isolated):
    group, _, _ = isolated
    sample = recorder.snapshot(group)
    assert sample["memoryStat"]["anon"] == 50
    assert sample["pidsEvents"] == {"max": 0}
    assert sample["processIds"] == [os.getpid()]
    assert sample["memorySwapCurrentBytes"] == 0
    (group / "memory.events").write_text("max 0\noom 0\n")
    with pytest.raises(ValueError, match="missing_measurement_counter"):
        recorder.snapshot(group)


def test_monotonic_and_cumulative_measurements_cannot_regress(recorder, isolated):
    group, _, _ = isolated
    first = recorder.snapshot(group)
    last = {**first, "elapsedMonotonic": first["elapsedMonotonic"] - 1}
    with pytest.raises(ValueError, match="nonmonotonic"):
        recorder.validate_samples([first, last])
    last = {**first, "cpuStat": {**first["cpuStat"], "usage_usec": 0}}
    with pytest.raises(ValueError, match="regressed"):
        recorder.validate_samples([first, last])


class FakeChild:
    pid = 999999

    def __init__(self, code=0, running=False):
        self.returncode = None if running else code
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.returncode is None:
            self.returncode = -signal.SIGTERM
        return self.returncode


def setup_run(recorder, isolated, monkeypatch, *, child=None):
    group, net, dev = isolated
    preflight = recorder.preflight
    monkeypatch.setattr(
        recorder,
        "preflight",
        lambda cgroup: preflight(cgroup, net=net, dev=dev, proc=group.parent / "proc"),
    )
    child = child or FakeChild()
    monkeypatch.setattr(recorder.subprocess, "Popen", lambda *args, **kwargs: child)
    signals = []
    monkeypatch.setattr(
        recorder.os, "killpg", lambda pid, sig: signals.append((pid, sig)), raising=False
    )
    identity = {
        "version": "test",
        "sourceCommit": "a" * 40,
        "dirtySource": False,
        "artifacts": [{"id": "test"}],
    }
    return group, identity, child, signals


def assert_receipt(recorder, root, output):
    receipt = json.loads((output / "receipt.json").read_bytes())
    raw_path = root / receipt["measurements"]["path"]
    raw = raw_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt["measurements"]["sha256"]
    measurements = json.loads(raw)
    assert receipt["execution"] == measurements["execution"]
    assert receipt["executionRunId"] == measurements["executionRunId"]
    return receipt


@pytest.mark.parametrize("code", [0, 7, -11])
def test_command_success_or_crash_preserves_raw_and_receipt(
    recorder, isolated, monkeypatch, tmp_path, code
):
    group, identity, child, signals = setup_run(
        recorder, isolated, monkeypatch, child=FakeChild(code)
    )
    output = tmp_path / "result"
    observed = recorder.record_run(
        tmp_path, output, identity, ["synthetic-test"], 10, [], cgroup=group
    )
    receipt = assert_receipt(recorder, tmp_path, output)
    assert receipt["execution"] == observed
    assert observed["exitCode"] == code
    assert observed["measurementComplete"] is True and observed["recorderErrors"] == []
    assert signals == [(child.pid, signal.SIGTERM), (child.pid, signal.SIGKILL)]


def test_timeout_kills_owned_process_group_and_retains_failure(
    recorder, isolated, monkeypatch, tmp_path
):
    group, identity, child, signals = setup_run(
        recorder, isolated, monkeypatch, child=FakeChild(running=True)
    )
    # A zero test timeout makes the bounded loop time out immediately, without sleeping.
    output = tmp_path / "result"
    observed = recorder.record_run(
        tmp_path, output, identity, ["synthetic-test"], 0, [], cgroup=group
    )
    assert_receipt(recorder, tmp_path, output)
    assert observed["timedOut"] and observed["exitCode"] == -signal.SIGTERM
    assert child.waits and signals[-1] == (child.pid, signal.SIGKILL)


@pytest.mark.parametrize(
    "failure",
    ["launch", "initial_sample", "final_sample", "preflight", "final_preflight", "cleanup"],
)
def test_measurement_and_lifecycle_exceptions_still_leave_failed_receipt(
    recorder, isolated, monkeypatch, tmp_path, failure
):
    group, identity, child, _ = setup_run(recorder, isolated, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("raw private command must not appear in receipt")

    if failure == "launch":
        monkeypatch.setattr(recorder.subprocess, "Popen", fail)
    elif failure == "cleanup":
        monkeypatch.setattr(recorder, "stop_child", fail)
    else:
        name = "snapshot" if "sample" in failure else "preflight"
        original = getattr(recorder, name)
        calls = []

        def sometimes(*args, **kwargs):
            calls.append(1)
            if len(calls) == (2 if failure.startswith("final") else 1):
                fail()
            return original(*args, **kwargs)

        monkeypatch.setattr(recorder, name, sometimes)
    output = tmp_path / "result"
    observed = recorder.record_run(
        tmp_path, output, identity, ["synthetic-test"], 10, [], cgroup=group
    )
    receipt = assert_receipt(recorder, tmp_path, output)
    assert observed["measurementComplete"] is False
    assert observed["recorderErrors"]
    assert "raw private" not in json.dumps(receipt)


def test_report_hash_is_one_way_and_run_id_must_match(recorder, isolated, monkeypatch, tmp_path):
    group, identity, child, _ = setup_run(recorder, isolated, monkeypatch)
    original = recorder.subprocess.Popen

    def launch(*args, **kwargs):
        (tmp_path / "model.json").write_text(
            json.dumps(
                {
                    "executionRunId": kwargs["env"]["DOCUMENT_FILES_EXECUTION_RUN_ID"],
                    "passed": False,
                }
            )
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(recorder.subprocess, "Popen", launch)
    output = tmp_path / "result"
    recorder.record_run(
        tmp_path, output, identity, ["synthetic-test"], 10, ["model.json"], cgroup=group
    )
    receipt = assert_receipt(recorder, tmp_path, output)
    raw = (tmp_path / "model.json").read_bytes()
    assert receipt["outputs"] == [{"path": "model.json", "sha256": hashlib.sha256(raw).hexdigest()}]
    assert "receipt" not in json.loads(raw)
    errors = []
    assert recorder.collect_reports(tmp_path, ["model.json"], "different-run", errors) == []
    assert errors


def test_missing_or_symlink_report_cannot_escape_or_hide_failure(recorder, tmp_path):
    errors = []
    (tmp_path / "report.json").symlink_to(tmp_path.parent / "elsewhere.json")
    assert recorder.collect_reports(tmp_path, ["report.json", "missing.json"], "run", errors) == []
    assert len(errors) == 2
    with pytest.raises(ValueError):
        recorder.safe_path(tmp_path, "../outside")


def test_descendants_in_changed_process_group_are_reported_not_arbitrarily_killed(
    recorder, isolated, monkeypatch, tmp_path
):
    group, identity, child, _ = setup_run(recorder, isolated, monkeypatch)
    original = recorder.snapshot
    calls = []

    def sample(*args):
        result = original(*args)
        calls.append(1)
        if len(calls) >= 2:
            result["processIds"].append(12345)
        return result

    monkeypatch.setattr(recorder, "snapshot", sample)
    monkeypatch.setattr(recorder.time, "sleep", lambda seconds: None)
    observed = recorder.record_run(
        tmp_path, tmp_path / "result", identity, ["synthetic-test"], 10, [], cgroup=group
    )
    assert observed["residualProcessIds"] == [12345]
    assert observed["measurementComplete"] is False


def test_raw_measurement_write_failure_still_preserves_failure_receipt(
    recorder, isolated, monkeypatch, tmp_path
):
    group, identity, _, _ = setup_run(recorder, isolated, monkeypatch)
    original = recorder.write_new

    def write(path, value):
        if path.name == "measurements.json":
            raise OSError("sample file cannot be written")
        return original(path, value)

    monkeypatch.setattr(recorder, "write_new", write)
    observed = recorder.record_run(
        tmp_path, tmp_path / "result", identity, ["synthetic-test"], 10, [], cgroup=group
    )
    receipt = json.loads((tmp_path / "result/receipt.json").read_text())
    assert receipt["measurements"] is None
    assert observed["measurementComplete"] is False
    assert observed["recorderErrors"][-1]["stage"] == "measurement_output"


def test_cleanup_escalates_to_kill_after_term_timeout(recorder, monkeypatch):
    import subprocess

    child = FakeChild(running=True)
    calls = []

    def wait(timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("synthetic", timeout)
        child.returncode = -signal.SIGKILL
        return child.returncode

    child.wait = wait
    signals = []
    monkeypatch.setattr(recorder.os, "killpg", lambda pid, sig: signals.append(sig), raising=False)
    recorder.stop_child(child)
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert child.returncode == -signal.SIGKILL
