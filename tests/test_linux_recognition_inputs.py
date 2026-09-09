"""Synthetic input acquisition only; no public downloads or recognition execution."""

import importlib.util
import io
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_linux_recognition_inputs.py"
spec = importlib.util.spec_from_file_location("linux_inputs", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def prepared(tmp_path):
    originals = tmp_path / "originals"
    originals.mkdir()

    def row(name, version, filename, url, role):
        raw = (name + " fixture bytes").encode()
        original = originals / filename
        original.write_bytes(raw)
        return {
            "name": name,
            "version": version,
            "filename": filename,
            "url": url,
            "size": len(raw),
            "sha256": helper.digest(raw),
            "role": role,
            "localOriginal": {
                "path": str(original),
                "size": len(raw),
                "sha256": helper.digest(raw),
            },
        }

    wheel = "demo-1.0-py3-none-any.whl"
    python = "cpython-3.12.14+20260901-aarch64-unknown-linux-gnu-install_only.tar.gz"
    antlr = "antlr4-python3-runtime-4.9.3.tar.gz"
    data = {
        "schemaVersion": helper.SCHEMA,
        "target": "linux-aarch64",
        "pythonVersion": "3.12.14",
        "wheels": [
            row("demo", "1.0", wheel, "https://files.pythonhosted.org/packages/a/" + wheel, "wheel")
        ],
        "pythonRuntime": row(
            "cpython",
            "3.12.14+20260901",
            python,
            "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/"
            + python,
            "python-runtime",
        ),
        "antlrSource": row(
            "antlr4-python3-runtime",
            "4.9.3",
            antlr,
            "https://files.pythonhosted.org/packages/b/" + antlr,
            "build-source",
        ),
    }
    args = SimpleNamespace(
        inventory=tmp_path / "inventory.json",
        inventory_sha256="",
        target="linux-aarch64",
        output=tmp_path / "new-output",
        acquire=False,
        download=False,
        max_total_bytes=10000,
        max_download_bytes=10000,
        max_file_bytes=1000,
        min_free_bytes=1,
        timeout_seconds=30,
        file_timeout_seconds=10,
    )

    def seal():
        raw = json.dumps(data).encode()
        args.inventory.write_bytes(raw)
        args.inventory_sha256 = helper.digest(raw)

    seal()
    return args, data, seal


def test_default_check_has_no_transfer_or_copy(prepared, monkeypatch):
    args, _, _ = prepared
    monkeypatch.setattr(helper, "supervise", lambda *a: pytest.fail("transfer in check mode"))
    result = helper.prepare(args)
    assert result["status"] == "ready-not-acquired"
    assert not (args.output / "files").exists()
    assert result["inventorySha256"] == result["observedInventorySha256"]
    assert result["stageApproved"] is False and result["releaseQualified"] is False


def test_actual_local_copy_worker_and_exact_output_hashes(prepared):
    args, data, _ = prepared
    args.acquire = True
    result = helper.prepare(args)
    assert result["status"] == "inputs-acquired-not-stage-approved"
    assert result["remaining"] == [] and len(result["inputs"]) == 3
    for row in [*data["wheels"], data["pythonRuntime"], data["antlrSource"]]:
        assert (
            helper.digest((args.output / "files" / row["filename"]).read_bytes()) == row["sha256"]
        )
    assert all(not r["cleanup"]["terminated"] for r in result["inputs"])
    for item in result["inputs"]:
        assert item["sourceRecheck"]["status"] == "verified"
        assert item["sourceRecheck"]["pathIdentityMatches"] is True
        assert item["sourceRecheck"]["bytesRead"] == item["expectedSize"]
        assert item["sourceRecheck"]["sha256"] == item["expectedSha256"]
    with pytest.raises(FileExistsError):
        helper.prepare(args)


@pytest.mark.parametrize(
    "change", ["hash", "duplicate", "traversal", "foreign", "metadata", "negative", "python"]
)
def test_invalid_inventory_fails_before_transfer(prepared, monkeypatch, change):
    args, data, seal = prepared
    row = data["wheels"][0]
    if change == "duplicate":
        data["wheels"].append(dict(row))
    if change == "traversal":
        row["filename"] = "../escape.whl"
    if change == "foreign":
        row["filename"] = "demo-1.0-cp312-cp312-manylinux_2_28_x86_64.whl"
        row["url"] = "https://files.pythonhosted.org/packages/a/" + row["filename"]
    if change == "metadata":
        row["metadata"] = {"path": "../escape", "sha256": "a" * 64}
    if change == "negative":
        row["size"] = -1
    if change == "python":
        data["pythonVersion"] = "3.11.14"
    seal()
    if change == "hash":
        args.inventory_sha256 = "0" * 64
    args.acquire = True
    monkeypatch.setattr(helper, "supervise", lambda *a: pytest.fail("unsafe transfer"))
    result = helper.prepare(args)
    assert result["status"] == "failed"
    assert (args.output / "acquisition.json").is_file()
    assert not (args.output / "files").exists()


def test_unresolved_tool_and_native_are_not_hidden(prepared, monkeypatch):
    args, data, seal = prepared
    data["buildTools"] = [{"name": "wheel", "version": "0.48.0", "url": None}]
    data["unresolved"] = [{"component": "ARM native candidate", "status": "unavailable"}]
    seal()
    args.acquire = True
    args.download = True
    monkeypatch.setattr(helper, "supervise", lambda *a: pytest.fail("incomplete inputs acquired"))
    result = helper.prepare(args)
    assert result["status"] == "not-ready"
    assert {r["component"] for r in result["missing"]} == {"wheel", "ARM native candidate"}


@pytest.mark.parametrize(
    "limit", ["max_total_bytes", "max_download_bytes", "max_file_bytes", "disk"]
)
def test_resource_preflight_precedes_any_transfer(prepared, monkeypatch, limit):
    args, data, seal = prepared
    args.acquire = True
    if limit == "max_download_bytes":
        for row in [*data["wheels"], data["pythonRuntime"], data["antlrSource"]]:
            row.pop("localOriginal")
        seal()
        args.download = True
    if limit == "disk":
        monkeypatch.setattr(helper.shutil, "disk_usage", lambda p: SimpleNamespace(free=0))
    else:
        setattr(args, limit, 1)
    monkeypatch.setattr(helper, "supervise", lambda *a: pytest.fail("over-budget transfer"))
    assert helper.prepare(args)["status"] == "failed"


@pytest.mark.parametrize(
    "url",
    [
        "http://files.pythonhosted.org/packages/a/demo.whl",
        "https://evil.test/demo.whl",
        "https://user:secret@files.pythonhosted.org/packages/a/demo.whl",
        "https://files.pythonhosted.org:444/packages/a/demo.whl",
        "https://files.pythonhosted.org/packages/../demo.whl",
        "https://files.pythonhosted.org/packages/a/demo.whl?token=x",
        "https://127.0.0.1/demo.whl",
    ],
)
def test_nonpublic_or_ambiguous_origins_are_rejected(url):
    with pytest.raises(ValueError):
        helper.official_url(url, "demo.whl")


def test_redirect_checked_before_request_and_signed_url_not_logged():
    original = "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/python.tar.gz"
    signed = "https://release-assets.githubusercontent.com/github-production-release-asset/123/a?jwt=SECRET"
    assert helper.official_url(signed, "python.tar.gz", initial=original) == signed
    handler = helper.Redirects(original, "python.tar.gz")
    with pytest.raises(ValueError):
        handler.redirect_request(None, None, 302, "", {}, "https://evil.test/a?jwt=SECRET")
    with pytest.raises(ValueError):
        helper.official_url(signed, "python.tar.gz")


@pytest.mark.parametrize("payload", [b"short", b"right-dataX", b"wrong-data"])
def test_stream_hash_size_and_partial_failure(tmp_path, payload):
    row = {"size": len(b"right-data"), "sha256": helper.digest(b"right-data")}
    destination = tmp_path / "partial"
    with pytest.raises(ValueError):
        helper.stream_bytes(io.BytesIO(payload), destination, row, time.monotonic() + 10)
    assert destination.stat().st_size <= row["size"] + 1


def test_copy_hash_failure_does_not_fallback_or_retry(prepared):
    args, data, _ = prepared
    row = data["wheels"][0]
    p = Path(row["localOriginal"]["path"])
    p.write_bytes(b"x" * row["size"])
    args.acquire = True
    args.download = True
    result = helper.prepare(args)
    assert result["status"] == "failed" and len(result["inputs"]) == 1
    assert result["inputs"][0]["method"] == "copy"
    assert result["inputs"][0]["partialBytes"] == row["size"]
    assert not (args.output / "files" / row["filename"]).exists()


@pytest.mark.parametrize("failure", ["timeout", "oserror", "cancel"])
def test_supervisor_kills_only_its_worker_on_wait_failure(tmp_path, monkeypatch, failure):
    class Process:
        pid = 432123
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                if failure == "oserror":
                    raise OSError("wait failed")
                if failure == "cancel":
                    raise KeyboardInterrupt()
                raise subprocess.TimeoutExpired("worker", timeout)
            return -9

        def kill(self):
            killed.append(self.pid)

    killed = []
    process = Process()
    monkeypatch.setattr(helper.subprocess, "Popen", lambda *a, **k: process)
    if helper.os.name == "posix":
        monkeypatch.setattr(helper.os, "killpg", lambda pid, sig: killed.append(pid))
    result = helper.supervise(tmp_path / "job.json", 0.01)
    assert killed == [process.pid]
    assert result["cleanup"]["terminated"] is True
    assert result["status"] == "failed"


def test_symlink_local_source_is_rejected(prepared, tmp_path):
    args, data, seal = prepared
    original = Path(data["wheels"][0]["localOriginal"]["path"])
    link = tmp_path / "link"
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip("host cannot create symbolic links")
    data["wheels"][0]["localOriginal"]["path"] = str(link)
    seal()
    assert helper.prepare(args)["status"] == "failed"


@pytest.mark.parametrize("mutation", [None, "status", "length", "encoding", "redirect", "hash"])
def test_synthetic_http_transfer_single_attempt_and_failures(tmp_path, monkeypatch, mutation):
    raw = b"public fixture"
    row = {
        "filename": "demo-1.0-py3-none-any.whl",
        "size": len(raw),
        "sha256": helper.digest(raw),
        "method": "download",
        "url": "https://files.pythonhosted.org/packages/a/demo-1.0-py3-none-any.whl",
    }
    calls = []

    class Response(io.BytesIO):
        status = 206 if mutation == "status" else 200
        url = "https://evil.test/?jwt=SECRET" if mutation == "redirect" else row["url"]
        headers = {"Content-Length": str(len(raw) + (1 if mutation == "length" else 0))}

    response = Response(b"x" * len(raw) if mutation == "hash" else raw)
    if mutation == "encoding":
        response.headers["Content-Encoding"] = "gzip"

    def open_request(request, timeout):
        calls.append(request.full_url)
        assert timeout <= 20
        return response

    monkeypatch.setattr(
        helper.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=open_request)
    )
    result = helper.transfer({"input": row, "directory": str(tmp_path), "timeoutSeconds": 10})
    assert len(calls) == 1
    assert "SECRET" not in json.dumps(result)
    if mutation is None:
        assert result["status"] == "verified"
        assert (tmp_path / row["filename"]).read_bytes() == raw
    else:
        assert result["status"] == "failed"
        assert not (tmp_path / row["filename"]).exists()
        if mutation == "hash":
            assert result["observedSize"] == len(raw)
            assert result["observedSha256"] == helper.digest(b"x" * len(raw))


def test_post_worker_same_size_tampering_is_not_accepted(prepared, monkeypatch):
    args, _, _ = prepared
    args.acquire = True
    original = helper.supervise

    def run(job, timeout):
        result = original(job, timeout)
        spec = json.loads(job.read_text())
        row = spec["input"]
        (Path(spec["directory"]) / row["filename"]).write_bytes(b"x" * row["size"])
        return result

    monkeypatch.setattr(helper, "supervise", run)
    result = helper.prepare(args)
    assert result["status"] == "failed" and "final bytes changed" in result["error"]
    assert len(result["inputs"]) == 1


@pytest.mark.parametrize("restore_timestamp", [False, True])
def test_local_mutation_during_copy_is_recorded(tmp_path, monkeypatch, restore_timestamp):
    original = tmp_path / "original"
    raw = b"source bytes"
    original.write_bytes(raw)
    output = tmp_path / "output"
    output.mkdir()
    row = {
        "method": "copy",
        "localPath": str(original),
        "filename": "source.bin",
        "size": len(raw),
        "sha256": helper.digest(raw),
    }
    stream = helper.stream_bytes

    before = original.stat()

    def changed(*args):
        result = stream(*args)
        original.write_bytes(b"x" * len(raw))
        if restore_timestamp:
            os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert original.stat().st_mtime_ns == before.st_mtime_ns
        return result

    monkeypatch.setattr(helper, "stream_bytes", changed)
    result = helper.transfer({"input": row, "directory": str(output), "timeoutSeconds": 10})
    assert result["status"] == "failed" and result["partialBytes"] == len(raw)
    assert not (output / "source.bin").exists()
    assert result["copiedBytes"] == len(raw)
    assert result["copiedSha256"] == helper.digest(raw)
    assert result["sourceRecheck"]["bytesRead"] == len(raw)
    assert result["sourceRecheck"]["sha256"] == helper.digest(b"x" * len(raw))
    assert result["sourceRecheck"]["status"] == "incomplete"


def test_deadline_stops_stream_without_writing(tmp_path):
    p = tmp_path / "partial"
    with pytest.raises(TimeoutError):
        helper.stream_bytes(
            io.BytesIO(b"x"), p, {"size": 1, "sha256": helper.digest(b"x")}, time.monotonic() - 1
        )
    assert p.stat().st_size == 0


def test_check_rejects_same_size_changed_local_original(prepared):
    args, data, _ = prepared
    row = data["wheels"][0]
    Path(row["localOriginal"]["path"]).write_bytes(b"x" * row["size"])
    result = helper.prepare(args)
    assert result["status"] == "failed"
    assert "SHA256 mismatch" in result["error"]
    assert not (args.output / "files").exists()


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_never_drains_body_and_has_finite_count(code):
    original = "https://download.pytorch.org/whl/cpu/demo.whl"
    handler = helper.Redirects(original, "demo.whl")
    request = helper.urllib.request.Request(original)
    request.timeout = 2
    calls = []
    handler.parent = SimpleNamespace(open=lambda req, timeout: calls.append(req.full_url))

    class Body:
        closed = False

        def read(self, *args):
            pytest.fail("redirect body must not be consumed")

        def close(self):
            self.closed = True

    for _ in range(3):
        body = Body()
        getattr(handler, f"http_error_{code}")(request, body, code, "", {"location": original})
        assert body.closed
    body = Body()
    with pytest.raises(ValueError, match="redirect count"):
        handler.http_error_302(request, body, 302, "", {"location": original})
    assert body.closed and len(calls) == 3


def test_linux_x64_inventory_is_supported_without_foreign_wheels(prepared):
    args, data, seal = prepared
    args.target = data["target"] = "linux-x86_64"
    row = data["pythonRuntime"]
    row["filename"] = row["filename"].replace("aarch64", "x86_64")
    row["url"] = row["url"].replace("aarch64", "x86_64")
    old = Path(row["localOriginal"]["path"])
    new = old.with_name(row["filename"])
    old.rename(new)
    row["localOriginal"]["path"] = str(new)
    seal()
    assert helper.prepare(args)["status"] == "ready-not-acquired"


def test_sigterm_enters_failure_receipt_path_and_restores_handler(prepared, monkeypatch):
    args, _, _ = prepared
    handlers = []
    previous = object()

    def install(signum, handler):
        assert signum == helper.signal.SIGTERM
        handlers.append(handler)
        return previous

    def interrupted(*args):
        handlers[0](helper.signal.SIGTERM, None)

    monkeypatch.setattr(helper.signal, "signal", install)
    monkeypatch.setattr(helper, "inventory_plan", interrupted)
    result = helper.run_preparation(args)
    assert handlers[-1] is previous
    assert result["status"] == "failed" and result["errorType"] == "KeyboardInterrupt"
    assert (args.output / "acquisition.json").is_file()


@pytest.fixture
def prepared_v2(prepared, tmp_path):
    args, data, seal = prepared
    wheel = data["wheels"][0]
    original = dict(wheel)
    source = tmp_path / "source-wheel.json"
    source.write_text(json.dumps([original]), encoding="utf-8")
    wheel["sourceRef"] = {
        "path": source.name,
        "sha256": helper.digest(source.read_bytes()),
        "rowIndex": 0,
        "rowSha256": helper.canonical_row_sha256(original),
    }
    wheel.update(id="runtime-demo", component="runtime-wheels")
    data.clear()
    data.update(
        schemaVersion=helper.SCHEMA_V2,
        target="linux-aarch64",
        pythonVersion="3.12.14",
        components={
            "runtime-wheels": {"requiredInputs": ["runtime-demo"]},
            "build-inputs": {"requiredInputs": ["build-wheel"]},
        },
        inputs=[
            wheel,
            {"id": "build-wheel", "component": "build-inputs", "name": "wheel", "url": None},
        ],
        stageRequirements=[{"component": "ARM native", "status": "unavailable"}],
        unresolved=[{"component": "full stage", "status": "not-audited"}],
    )
    args.component = ["runtime-wheels"]
    args.reuse_root = None
    seal()
    return args, data, seal


def test_v2_selected_runtime_can_acquire_without_stage_or_unselected_build_inputs(prepared_v2):
    args, data, _ = prepared_v2
    args.acquire = True
    result = helper.prepare(args)
    assert result["status"] == "inputs-acquired-not-stage-approved"
    assert result["selectedInputsReady"] is True
    assert result["stageApproved"] is False and result["releaseQualified"] is False
    assert result["stageRequirements"] == data["stageRequirements"]
    assert result["unselectedInputs"] == [data["inputs"][1]]
    assert result["unselectedRequirements"]["unresolved"] == data["unresolved"]
    assert result["remaining"] == []
    assert result["inputs"][0]["verifiedPath"] == "files/runtime-demo/demo-1.0-py3-none-any.whl"


@pytest.mark.parametrize(
    "change",
    [
        "no-selection",
        "unknown-selection",
        "duplicate-selection",
        "missing-component",
        "no-required",
        "empty-required",
        "empty-inputs",
        "duplicate-id",
        "uppercase-id",
        "escaping-id",
        "unlisted-id",
        "wrong-component",
        "unknown-component",
    ],
)
def test_v2_rejects_ambiguous_or_missing_selection_contract(prepared_v2, change):
    args, data, seal = prepared_v2
    if change == "no-selection":
        args.component = None
    if change == "unknown-selection":
        args.component = ["native"]
    if change == "duplicate-selection":
        args.component *= 2
    if change == "missing-component":
        args.component = ["python-runtime"]
    if change == "no-required":
        data["components"]["runtime-wheels"] = {}
    if change == "empty-required":
        data["components"]["runtime-wheels"]["requiredInputs"] = []
    if change == "empty-inputs":
        data["inputs"] = []
    if change == "duplicate-id":
        data["inputs"].append(dict(data["inputs"][0]))
    if change == "uppercase-id":
        data["inputs"][0]["id"] = "Runtime-demo"
    if change == "escaping-id":
        data["inputs"][0]["id"] = "../escape"
    if change == "unlisted-id":
        data["inputs"][0]["id"] = "not-required"
    if change == "wrong-component":
        data["inputs"][0]["component"] = "build-inputs"
    if change == "unknown-component":
        data["components"]["unknown"] = {"requiredInputs": ["unknown"]}
    seal()
    result = helper.prepare(args)
    assert result["status"] == "failed"
    assert not (args.output / "files").exists()


def test_v2_required_missing_input_is_not_ready(prepared_v2):
    args, data, seal = prepared_v2
    data["components"]["runtime-wheels"]["requiredInputs"].append("runtime-missing")
    seal()
    result = helper.prepare(args)
    assert result["status"] == "not-ready" and result["selectedInputsReady"] is False
    assert result["missing"][0]["id"] == "runtime-missing"
    assert "runtime-missing" in result["remainingSelectedInputs"]


def test_v2_selected_incomplete_build_input_is_not_ready(prepared_v2):
    args, _, _ = prepared_v2
    args.component = ["build-inputs"]
    assert helper.prepare(args)["status"] == "not-ready"


def test_v1_cannot_opt_into_component_bypass(prepared):
    args, _, _ = prepared
    args.component = ["runtime-wheels"]
    assert helper.prepare(args)["status"] == "failed"


@pytest.fixture
def prepared_model(prepared_v2, tmp_path):
    args, data, seal = prepared_v2
    args.reuse_root = tmp_path / "reuse"
    args.reuse_root.mkdir()
    rows, models = [], []
    for index, (repo, revision) in enumerate(
        [("docling-layout-heron", "a" * 40), ("docling-models", "b" * 40)]
    ):
        raw = f"model fixture {index}".encode()
        path = f"models/docling/docling-project--{repo}/README.md"
        source = args.reuse_root / path
        source.parent.mkdir(parents=True)
        source.write_bytes(raw)
        row = {
            "url": f"https://huggingface.co/docling-project/{repo}/resolve/{revision}/README.md",
            "path": path,
            "size": len(raw),
            "sha256": helper.digest(raw),
        }
        rows.append(row)
        models.append(
            {
                "id": f"model-{index}",
                "component": "model-originals",
                "name": f"docling-project/{repo}",
                "version": revision,
                "filename": "README.md",
                "sourceUrl": row["url"],
                "assemblyPath": path,
                "size": len(raw),
                "sha256": row["sha256"],
                "localCopyOnly": True,
            }
        )
    manifest = tmp_path / "source-models.json"
    manifest.write_text(json.dumps(rows), encoding="utf-8")
    for i, model in enumerate(models):
        model["sourceRef"] = {
            "path": manifest.name,
            "sha256": helper.digest(manifest.read_bytes()),
            "rowIndex": i,
            "rowSha256": helper.canonical_row_sha256(rows[i]),
        }
    data["components"]["model-originals"] = {"requiredInputs": [r["id"] for r in models]}
    data["inputs"].extend(models)
    args.component = ["model-originals"]
    seal()
    return args, data, seal, models


def test_v2_models_same_basename_acquired_into_unique_ids_without_network(prepared_model):
    args, _, _, models = prepared_model
    args.acquire = args.download = True
    args.max_download_bytes = 1
    result = helper.prepare(args)
    assert result["status"] == "inputs-acquired-not-stage-approved"
    assert result["selectedInputsReady"] is True
    assert all(r["method"] == "copy" for r in result["inputs"])
    for row in models:
        assert (
            helper.digest((args.output / "files" / row["id"] / "README.md").read_bytes())
            == row["sha256"]
        )
    assert not (args.output / models[0]["assemblyPath"]).exists()


@pytest.mark.parametrize(
    "change",
    [
        "manifest-sha",
        "row-sha",
        "row-index",
        "assembly-path",
        "filename",
        "source-url",
        "download-url",
        "copy-policy",
        "reuse-root",
        "escaping-source-ref",
        "source-version",
    ],
)
def test_v2_model_source_binding_is_exact(prepared_model, change):
    args, _, seal, models = prepared_model
    row = models[0]
    if change == "manifest-sha":
        row["sourceRef"]["sha256"] = "0" * 64
    if change == "row-sha":
        row["sourceRef"]["rowSha256"] = "0" * 64
    if change == "row-index":
        row["sourceRef"]["rowIndex"] = True
    if change == "assembly-path":
        row["assemblyPath"] = "models/elsewhere"
    if change == "filename":
        row["filename"] = "other.md"
    if change == "source-url":
        row["sourceUrl"] = "https://evil.test/README.md"
    if change == "download-url":
        row["url"] = row["sourceUrl"]
    if change == "copy-policy":
        row["localCopyOnly"] = False
    if change == "reuse-root":
        args.reuse_root = Path("relative-root")
    if change == "escaping-source-ref":
        row["sourceRef"]["path"] = "../source-models.json"
    if change == "source-version":
        row["version"] = "incorrect"
    seal()
    assert helper.prepare(args)["status"] == "failed"


def test_v2_missing_model_never_falls_back_to_download(prepared_model, monkeypatch):
    args, _, _, models = prepared_model
    (args.reuse_root / models[0]["assemblyPath"]).unlink()
    args.acquire = args.download = True
    args.max_download_bytes = 1
    monkeypatch.setattr(
        helper, "supervise", lambda *a: pytest.fail("missing model triggered transfer")
    )
    receipt = helper.prepare(args)
    assert receipt["status"] == "not-ready"
    assert receipt["preflight"]["downloadBytes"] == 0


def test_v2_model_worker_rejects_download_even_if_job_is_wrong(tmp_path, monkeypatch):
    row = {
        "filename": "README.md",
        "method": "download",
        "localCopyOnly": True,
        "url": "https://files.pythonhosted.org/packages/a/README.md",
    }
    monkeypatch.setattr(
        helper.urllib.request, "build_opener", lambda *a: pytest.fail("model network request")
    )
    assert (
        helper.transfer({"directory": str(tmp_path), "input": row, "timeoutSeconds": 2})["status"]
        == "failed"
    )


def test_v2_runtime_source_ref_cannot_be_invented(prepared_v2):
    args, data, seal = prepared_v2
    data["inputs"][0]["sourceRef"]["rowSha256"] = "0" * 64
    seal()
    assert helper.prepare(args)["status"] == "failed"


def test_v2_python_runtime_component_cannot_contain_a_wheel(prepared_v2):
    args, data, seal = prepared_v2
    row = data["inputs"][0]
    row["component"] = "python-runtime"
    row["role"] = "python-runtime"
    data["components"]["python-runtime"] = data["components"].pop("runtime-wheels")
    args.component = ["python-runtime"]
    seal()
    assert helper.prepare(args)["status"] == "failed"


def test_v2_model_symlink_below_reuse_root_is_rejected(prepared_model, tmp_path):
    args, _, _, models = prepared_model
    source = args.reuse_root / models[0]["assemblyPath"]
    target = tmp_path / "outside"
    source.rename(target)
    try:
        source.symlink_to(target)
    except OSError:
        pytest.skip("host cannot create symbolic links")
    assert helper.prepare(args)["status"] == "failed"


def test_source_recheck_stops_at_size_plus_one_and_records_actual_reads():
    evidence = {}
    with pytest.raises(ValueError, match="exceeds approved size"):
        helper.recheck_local_source(
            io.BytesIO(b"abcdef"),
            {"size": 3, "sha256": helper.digest(b"abc")},
            time.monotonic() + 10,
            evidence,
        )
    assert evidence["bytesRead"] == evidence["maxBytes"] == 4
    assert evidence["sha256"] == helper.digest(b"abcd")
    assert evidence["status"] == "incomplete"


def test_source_recheck_keeps_partial_reads_on_original_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(helper.time, "monotonic", lambda: clock[0])

    class Source(io.BytesIO):
        def read1(self, n):
            value = super().read1(min(n, 2))
            clock[0] = 2.0
            return value

    evidence = {}
    with pytest.raises(TimeoutError, match="source recheck deadline"):
        helper.recheck_local_source(
            Source(b"abcd"), {"size": 4, "sha256": helper.digest(b"abcd")}, 1.0, evidence
        )
    assert evidence["bytesRead"] == 2
    assert evidence["sha256"] == helper.digest(b"ab")
    assert evidence["status"] == "incomplete"


def test_source_recheck_read_failure_retains_observed_prefix():
    class Source(io.BytesIO):
        def read1(self, n):
            if self.tell():
                raise OSError("synthetic read failure")
            return super().read1(min(n, 2))

    evidence = {}
    with pytest.raises(OSError):
        helper.recheck_local_source(
            Source(b"abcd"),
            {"size": 4, "sha256": helper.digest(b"abcd")},
            time.monotonic() + 10,
            evidence,
        )
    assert evidence["bytesRead"] == 2 and evidence["sha256"] == helper.digest(b"ab")
    assert evidence["status"] == "incomplete"


def test_failed_recheck_keeps_partial_file_and_does_not_publish(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"abc")
    output = tmp_path / "out"
    output.mkdir()
    row = {
        "method": "copy",
        "localPath": str(source),
        "filename": "copied.bin",
        "size": 3,
        "sha256": helper.digest(b"abc"),
    }

    def failed(stream, row, deadline, evidence):
        evidence.update(status="incomplete", bytesRead=1, sha256=helper.digest(b"a"))
        raise TimeoutError("source recheck deadline")

    monkeypatch.setattr(helper, "recheck_local_source", failed)
    result = helper.transfer({"input": row, "directory": str(output), "timeoutSeconds": 10})
    assert result["status"] == "failed" and result["errorType"] == "TimeoutError"
    assert result["partialBytes"] == 3 and result["sourceRecheck"]["bytesRead"] == 1
    assert (output / "copied.bin.partial").read_bytes() == b"abc"
    assert not (output / "copied.bin").exists()
