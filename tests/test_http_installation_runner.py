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


def test_simulated_lifecycle_records_observations_without_passing_raw_report(tmp_path, monkeypatch):
    """Drive saved-response paths only. No service/model is actually executed."""
    import hashlib

    content = b"public synthetic source"
    (tmp_path / "public.html").write_bytes(content)
    (tmp_path / "config.json").write_text(json.dumps({"profiles": {"cpu": {}}}))
    (tmp_path / "spec.json").write_text(
        json.dumps(
            {
                "schemaVersion": "document-files.review-specification.v1",
                "criteria": [{"id": "exact", "expected": "MUST_NOT_REACH_HTTP"}],
            }
        )
    )
    digest = hashlib.sha256(content).hexdigest()
    inventory = {"sourceCommit": "a" * 40, "version": "1.8.0"}
    packs = {"runtimeId": "b" * 64, "modelId": "c" * 64}
    monkeypatch.setattr(
        runner, "identity", lambda *_: (inventory, {"packManifestSha256": packs}, tmp_path, {})
    )
    monkeypatch.setattr(runner, "installed_tree", lambda *_: {})
    monkeypatch.setattr(runner.sys, "platform", "linux")
    old_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists", lambda p: p.as_posix() == "/proc/self/stat" or old_exists(p)
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    state = {"status": "partial", "attempt": 1, "resumes": 0, "revision": 1}
    upload = tmp_path / "run/service-state/uploads/job.html"
    database = tmp_path / "run/service-state/results/job.sqlite3"

    class Process:
        pid = 100

        def __init__(self, *_a, **_k):
            pass

        def poll(self):
            return None

        def terminate(self):
            if state["status"] == "running":
                state["status"], state["revision"] = "interrupted", 3

        def wait(self, **_k):
            return 0

    monkeypatch.setattr(runner.subprocess, "Popen", Process)
    child = {"pid": 200, "parent": 100, "startTicks": "321", "name": "llama-server"}
    monkeypatch.setattr(
        runner, "processes", lambda *_: [child] if state["status"] == "running" else []
    )
    monkeypatch.setattr(runner, "alive", lambda *_: state["status"] == "running")

    class HTTP:
        def __init__(self, *_):
            pass

        def wait(self, predicate, **_):
            result = predicate()
            assert result
            return result

        def request(self, method, path, body=b"", headers=None, authenticated=True):
            assert b"MUST_NOT_REACH_HTTP" not in body
            if path == "/v1/capabilities":
                return (200 if authenticated else 401), {}
            if path == "/v1/jobs" and method == "POST":
                if headers.get("Content-Type") == "application/json":
                    return 415, {}
                if any(
                    key in json.loads(headers["X-Extraction-Options"])
                    for key in ("path", "url", "endpoint")
                ):
                    return 400, {"error": {"code": "invalid-options"}}
                if body != content:
                    return 409, {"error": {"code": "idempotency-conflict"}}
                upload.parent.mkdir(parents=True, exist_ok=True)
                upload.write_bytes(content)
                database.parent.mkdir(parents=True, exist_ok=True)
                database.write_bytes(b"synthetic database")
                return 202, {"jobId": "job", "sha256": digest}
            if path.endswith("/resume"):
                state["resumes"] += 1
                state["attempt"] += 1
                state["status"] = "complete" if state["resumes"] == 3 else "running"
                if state["status"] == "complete":
                    state["revision"] = 4
                return 202, {}
            if path.endswith("/cancel"):
                state["status"], state["revision"] = "cancelled", 2
                return 202, {}
            if path.endswith("/result"):
                complete = state["status"] == "complete"
                return 200, {
                    "resultRevision": state["revision"],
                    "source": {"sha256": digest},
                    "extraction": {
                        "status": "complete" if complete else "partial",
                        "modelCalls": 4 if complete else 1,
                    },
                    "issues": [] if complete else [{"code": "model_call_budget_exceeded"}],
                    "validation": {"valid": True, "errors": []},
                    "provenance": {
                        "model": {
                            "adapter": "managed-llama-cpp.v1",
                            "runtimeManifestSha256": packs["runtimeId"],
                            "modelManifestSha256": packs["modelId"],
                        }
                    },
                }
            if method == "DELETE":
                state["status"] = "deleted"
                upload.unlink()
                database.unlink()
                return 200, {"deleted": True}
            if state["status"] == "deleted":
                return 404, {}
            return 200, {
                "status": state["status"],
                "executionStatus": "running" if state["status"] == "running" else "completed",
                "attempt": state["attempt"],
            }

    monkeypatch.setattr(runner, "HTTP", HTTP)
    assert (
        runner.main(arguments(tmp_path, **{"review-specification": str(tmp_path / "spec.json")}))
        == 0
    )
    report = json.loads((tmp_path / "run/report.json").read_bytes())
    assert report["passed"] is False and report["releaseQualification"] is False
    assert report["checksPassed"] is True
    assert report["observationProfile"] == "in-container-loopback.v1"
    assert report["notCovered"] == [
        "host-published-port-access",
        "independent-document-quality-suite",
    ]
    observations = json.loads((tmp_path / report["lifecycleEvidence"]["path"]).read_bytes())[
        "observations"
    ]
    assert set(observations) == set(report["plannedTests"])
    assert observations["cancel_tree"]["childrenBefore"] == [child]
    assert observations["cancel_tree"]["childrenAliveAfter"] == []
    assert observations["restart_interrupted"]["afterDelay"]["status"] == "interrupted"
    assert observations["delete_results"]["resultDatabaseExists"] is False
    assert report["reviewSpecification"]["sha256"] == runner.sha256(
        tmp_path / "run/review-specification.json"
    )
