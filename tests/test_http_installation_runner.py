"""Runner contracts only: no actual HTTP/model qualification is fabricated."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_http_installation_check.py"
spec = importlib.util.spec_from_file_location("http_installation_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def arguments(tmp_path, **changes):
    values = {
        "evidence-root": str(tmp_path),
        "output": "run",
        "inventory": "inventory.json",
        "inventory-sha256": "a" * 64,
        "core-artifact": "wheel",
        "source-artifact": "source",
        "image-artifact": "image",
        "profile": "cpu",
        "config": str(tmp_path / "config.json"),
        "input": str(tmp_path / "public.html"),
        "format": "html",
        "max-calls": "6",
        "seconds": "900",
        "timeout": "1200",
    }
    values.update(changes)
    return [v for key, value in values.items() for v in ("--" + key, value)] + [
        "--artifact",
        "wheel",
        "--artifact",
        "source",
        "--artifact",
        "image",
    ]


def test_failed_run_keeps_raw_failure_without_secret_error_text(tmp_path, monkeypatch):
    def fail(*args):
        raise ValueError("credential-or-source-content-must-not-appear")

    monkeypatch.setattr(runner, "run_sequence", fail)
    assert runner.main(arguments(tmp_path)) == 1
    raw = (tmp_path / "run/report.json").read_text()
    report = json.loads(raw)
    assert report["failure"] == "ValueError"
    assert "credential-or-source" not in raw
    assert report["passed"] is False and report["releaseQualification"] is False
    assert report["checksPassed"] is False


def test_even_simulated_success_cannot_be_mistaken_for_release_evidence(tmp_path, monkeypatch):
    def simulated(args, out, report):
        report["checksPassed"] = True

    monkeypatch.setattr(runner, "run_sequence", simulated)
    assert runner.main(arguments(tmp_path)) == 0
    report = json.loads((tmp_path / "run/report.json").read_bytes())
    assert report["passed"] is False and report["releaseQualification"] is False
    assert "independent-image-and-isolation-receipts-required" in report["qualificationBlockers"]
    with pytest.raises(FileExistsError):
        runner.main(arguments(tmp_path))


@pytest.mark.parametrize(
    "change",
    [
        {"output": "../outside"},
        {"output": "."},
        {"max-calls": "3"},
        {"seconds": "0"},
        {"timeout": "900"},
        {"timeout": "999999"},
    ],
)
def test_invalid_destination_or_unbounded_run_rejected_before_execution(
    tmp_path, monkeypatch, change
):
    monkeypatch.setattr(runner, "run_sequence", lambda *args: pytest.fail("must not execute"))
    with pytest.raises(runner.CheckFailure):
        runner.main(arguments(tmp_path, **change))
    assert not (tmp_path / "run").exists()


def proc_stat(root, pid, parent, ticks, name="worker", state="S"):
    path = root / str(pid)
    path.mkdir(exist_ok=True)
    # Fields after comm: state, ppid, ..., starttime (field22/index19).
    fields = [state, str(parent)] + ["0"] * 17 + [str(ticks)]
    (path / "stat").write_text(f"{pid} ({name}) " + " ".join(fields))


def test_child_evidence_follows_descendants_and_rejects_pid_reuse(tmp_path):
    proc_stat(tmp_path, 10, 1, 100, "server")
    proc_stat(tmp_path, 11, 10, 101, "python")
    proc_stat(tmp_path, 12, 11, 102, "llama-server")
    proc_stat(tmp_path, 99, 1, 199, "unrelated")
    children = runner.processes(10, tmp_path)
    assert [c["pid"] for c in children] == [11, 12]
    assert runner.alive(children[1], tmp_path)
    proc_stat(tmp_path, 12, 1, 999, "reused")
    assert not runner.alive(children[1], tmp_path)
    proc_stat(tmp_path, 11, 10, 101, state="Z")
    assert not runner.alive(children[0], tmp_path)


def test_http_client_never_accepts_an_external_endpoint(monkeypatch):
    client = runner.HTTP(8765, "secret", float("inf"))
    monkeypatch.setattr(
        runner.http.client, "HTTPConnection", lambda *args, **kwargs: pytest.fail("no connection")
    )
    for path in ("https://example.invalid", "//outside/v1/jobs", "/other"):
        with pytest.raises(runner.CheckFailure):
            client.request("GET", path)


def test_missing_real_inventory_produces_failed_report_without_launch(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text('{"profiles":{}}')
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch")
    )
    assert runner.main(arguments(tmp_path)) == 1
    report = json.loads((tmp_path / "run/report.json").read_bytes())
    assert not report["checksPassed"] and report["tests"] == []
    assert "aiResult" not in report


def test_interrupt_preserves_failure_and_cannot_leave_success_status(tmp_path, monkeypatch):
    def interrupted(args, out, report):
        report["checksPassed"] = True
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "run_sequence", interrupted)
    assert runner.main(arguments(tmp_path)) == 1
    report = json.loads((tmp_path / "run/report.json").read_bytes())
    assert report["checksPassed"] is False and report["failure"] == "KeyboardInterrupt"
