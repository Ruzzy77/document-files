"""PDF geometry and recognition import contracts; no model quality claim."""

from copy import deepcopy
from pathlib import Path

import pytest

from document_files.document_model.docling_adapter import (
    DoclingRecognition,
    RecognitionConfig,
    import_docling,
)
from document_files.document_model.model import ObservationDocument
from document_files.document_model.observe import observe_document


def exported():
    return {
        "pages": {"1": {"size": {"width": 600, "height": 800}}},
        "texts": [],
        "tables": [
            {
                "self_ref": "#/tables/0",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 10,
                            "t": 790,
                            "r": 300,
                            "b": 700,
                            "coord_origin": "BOTTOMLEFT",
                        },
                    }
                ],
                "data": {
                    "table_cells": [
                        {
                            "text": "Volume",
                            "start_row_offset_idx": 0,
                            "end_row_offset_idx": 1,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 2,
                            "row_span": 1,
                            "col_span": 2,
                            "column_header": True,
                        },
                        {
                            "text": "",
                            "start_row_offset_idx": 1,
                            "end_row_offset_idx": 2,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 1,
                        },
                    ],
                    "grid": [[{"text": "MUST NOT IMPORT"}]],
                },
            }
        ],
    }


def test_docling_import_preserves_cells_spans_and_never_reads_grid():
    doc = ObservationDocument()
    source = exported()
    before = deepcopy(source)
    ids = import_docling(doc, source)
    assert source == before
    assert len(ids) == 2
    cells = doc.tables["docling:table:0"]["cells"]
    assert len(cells) == 2 and cells[0]["colSpan"] == 2
    assert doc.nodes[ids[1]]["text"] == ""
    bbox = doc.tables["docling:table:0"]["locator"]["bbox"]
    assert doc.nodes[ids[0]]["sourceStructure"]["bbox"] is None
    assert bbox["top"] == 10 and bbox["bottom"] == 100 and bbox["origin"] == "TOPLEFT"
    assert not any("MUST NOT IMPORT" in n["text"] for n in doc.nodes.values())


def test_docling_missing_cells_and_bad_span_are_honest_gaps():
    source = exported()
    source["tables"][0]["data"]["table_cells"][0]["col_span"] = 9
    doc = ObservationDocument()
    import_docling(doc, source)
    assert any(i["code"] == "recognition_table_cell_invalid" for i in doc.issues)
    source["tables"][0]["data"].pop("table_cells")
    doc = ObservationDocument()
    assert import_docling(doc, source) == []
    assert doc.issues[0]["code"] == "recognition_table_cells_missing"


def test_missing_recognition_paths_do_not_spawn_or_download(monkeypatch):
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned"))
    )
    result = DoclingRecognition(
        RecognitionConfig(
            artifacts_path="/definitely-not-installed",
            tesseract_cmd="/none",
            tessdata_path="/none",
        )
    ).observe(b"PDF")
    assert result["status"] == "unavailable"
    assert result["issues"][0]["code"] == "recognition_runtime_unavailable"


def test_pdf_native_geometry_and_fake_recognition_conflict(tmp_path: Path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "text.pdf"
    page = canvas.Canvas(str(path), pagesize=(600, 800))
    page.drawString(20, 760, "Credit: 19")
    page.save()

    class FakeRecognition:
        def observe(self, content):
            assert content.startswith(b"%PDF")
            return {
                "status": "complete",
                "document": {
                    "pages": {"1": {"size": {"width": 600, "height": 800}}},
                    "tables": [],
                    "texts": [
                        {
                            "text": "Credit: 79",
                            "label": "text",
                            "prov": [
                                {
                                    "page_no": 1,
                                    "bbox": {
                                        "l": 0,
                                        "t": 0,
                                        "r": 500,
                                        "b": 100,
                                        "coord_origin": "TOPLEFT",
                                    },
                                }
                            ],
                        }
                    ],
                },
                "provenance": {"testDouble": True},
            }

    doc = observe_document(path.read_bytes(), "pdf", {}, recognition=FakeRecognition())
    assert doc.nodes["pdf:page:1"]["sourceStructure"]["rotation"] == 0
    assert any(n["text"] == "Credit: 19" for n in doc.nodes.values())
    assert any(n["text"] == "Credit: 79" for n in doc.nodes.values())
    assert any(i["code"] == "native_recognition_text_conflict" for i in doc.issues)
    assert doc.coverage["status"] == "partial"


def test_pdf_without_recognition_retains_native_text_and_reports_gap(tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "text.pdf"
    page = canvas.Canvas(str(path))
    page.drawString(20, 760, "Native only")
    page.save()
    doc = observe_document(path.read_bytes(), "pdf", {})
    assert any(n["text"] == "Native only" for n in doc.nodes.values())
    assert any(i["code"] == "pdf_layout_recognition_unavailable" for i in doc.issues)
    assert doc.coverage["status"] == "partial"


@pytest.mark.parametrize("native_paths", [False, True])
def test_recognition_worker_scope_releases_process_and_does_not_inherit_model_key(
    tmp_path, monkeypatch, native_paths
):
    import json
    import os

    import document_files.document_model.docling_adapter as adapter

    if native_paths and os.name == "nt":
        pytest.skip("Linux loader paths use POSIX filesystem paths")
    artifacts = tmp_path / "models"
    tessdata = tmp_path / "tessdata"
    artifacts.mkdir()
    tessdata.mkdir()
    for language in ("kor", "eng", "osd"):
        (tessdata / f"{language}.traineddata").write_bytes(b"fixture")
    (tessdata / "configs").mkdir()
    (tessdata / "configs" / "tsv").write_text("fixture")
    executable = tmp_path / "python"
    executable.write_text("fixture")
    seen = {}
    closed = []

    class FakeProcess:
        returncode = 0

        def __init__(self, command, **kwargs):
            seen["command"] = command
            seen["env"] = kwargs["env"]
            self.output = kwargs["stdout"]

        def communicate(self, content, *, timeout):
            assert content == b"public synthetic PDF"
            seen["timeout"] = timeout
            self.output.write(json.dumps({"status": "complete", "document": {}}).encode())

        def wait(self):
            closed.append("wait")

    class FakeJob:
        def __init__(self, process):
            pass

        def close(self):
            closed.append("job")

    monkeypatch.setenv("DOCUMENT_FILES_AI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/host/untrusted::/another/host/path")
    monkeypatch.setenv("LD_PRELOAD", "/host/untrusted.so")
    monkeypatch.setattr(adapter.sys, "platform", "linux")
    monkeypatch.setattr(adapter.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(adapter, "WindowsJob", FakeJob)
    monkeypatch.setattr(adapter, "kill_process_tree", lambda process: closed.append("tree"))
    libraries = tmp_path / "libraries"
    libraries.mkdir(parents=True)
    config = RecognitionConfig(
        str(artifacts),
        str(executable),
        str(tessdata),
        python=str(executable),
        native_library_directories=[str(libraries.resolve())] if native_paths else [],
    )
    result = DoclingRecognition(config).observe(b"public synthetic PDF")
    assert result["status"] == "complete"
    assert closed == ["tree", "wait", "job"]
    assert "DOCUMENT_FILES_AI_API_KEY" not in seen["env"]
    assert "LD_PRELOAD" not in seen["env"]
    if native_paths:
        assert seen["env"]["LD_LIBRARY_PATH"] == str(libraries.resolve())
    else:
        assert "LD_LIBRARY_PATH" not in seen["env"]
    raw_config = json.loads(seen["command"][seen["command"].index("--config") + 1])
    assert RecognitionConfig(**raw_config) == config
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["env"]["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in seen["env"]
    assert seen["command"][1:3] == ["-I", "-B"]
    assert seen["command"][3].endswith("recognition_bootstrap.py")


def test_recognition_native_directories_are_frozen_and_bound_to_identity(tmp_path):
    directories = [str(tmp_path.resolve())]
    config = RecognitionConfig(
        "missing", "missing", "missing", native_library_directories=directories
    )
    backend = DoclingRecognition(config)
    directories.clear()
    assert config.native_library_directories == (str(tmp_path.resolve()),)
    assert (
        backend.identity["configuration"]["native_library_directories"]
        == config.native_library_directories
    )
    assert (
        backend.identity
        != DoclingRecognition(RecognitionConfig("missing", "missing", "missing")).identity
    )
    assert backend.identity["adapterVersion"] == "28"


@pytest.mark.parametrize("value", [None, "python/lib", {"path": "/lib"}, [None], [[]]])
def test_recognition_native_directories_reject_non_path_arrays(value):
    with pytest.raises(ValueError, match="native library directories invalid"):
        RecognitionConfig("missing", "missing", "missing", native_library_directories=value)


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "relative",
        "parent",
        "colon",
        "semicolon",
        "token",
        "nul",
        "missing",
        "duplicate",
        "too_many",
        "symlink",
        "non_linux",
    ],
)
def test_invalid_native_directories_do_not_spawn(tmp_path, monkeypatch, case):
    import os

    import document_files.document_model.docling_adapter as adapter

    root = tmp_path.resolve()
    paths = [str(root)]
    monkeypatch.setattr(adapter.sys, "platform", "linux")
    if case == "empty":
        paths = [""]
    elif case == "relative":
        paths = ["python/lib"]
    elif case == "parent":
        paths = [str(root / "..")]
    elif case in {"colon", "semicolon", "token", "nul"}:
        paths = [
            str(root)
            + {"colon": ":/lib", "semicolon": ";/lib", "token": "/$ORIGIN", "nul": "\0"}[case]
        ]
    elif case == "missing":
        paths = [str(root / "missing")]
    elif case == "duplicate":
        paths *= 2
    elif case == "too_many":
        paths = [str(root / str(index)) for index in range(9)]
    elif case == "symlink":
        if os.name == "nt":
            pytest.skip("directory symlinks require Windows privileges")
        link = root / "linked"
        link.symlink_to(root, target_is_directory=True)
        paths = [str(link)]
    elif case == "non_linux":
        monkeypatch.setattr(adapter.sys, "platform", "darwin")
    config = RecognitionConfig("missing", "missing", "missing", native_library_directories=paths)
    with pytest.raises(ValueError, match="recognition native library director"):
        config.validate()
    monkeypatch.setattr(adapter.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    assert DoclingRecognition(config).observe(b"PDF")["status"] == "unavailable"


def test_recognition_bootstrap_does_not_import_core_siblings_or_write_bytecode(tmp_path):
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from document_files.document_model import recognition_bootstrap

    core = tmp_path / "core-site-packages"
    product = core / "document_files"
    modules = product / "document_model"
    modules.mkdir(parents=True)
    (product / "__init__.py").write_text("raise AssertionError('public API imported')")
    (modules / "__init__.py").write_text("")
    (core / "json.py").write_text("raise AssertionError('core dependency imported')")
    bootstrap = modules / "recognition_bootstrap.py"
    shutil.copyfile(recognition_bootstrap.__file__, bootstrap)
    (modules / "recognition_worker.py").write_text(
        "import json, sys\n"
        "print(json.dumps({'path': sys.path, 'isolated': sys.flags.isolated, "
        "'noUserSite': sys.flags.no_user_site, 'noBytecode': sys.dont_write_bytecode}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(bootstrap)],
        cwd=core,
        env={**os.environ, "PYTHONPATH": str(core), "PYTHONUSERBASE": str(core)},
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    observed = json.loads(result.stdout)
    assert str(core) not in observed["path"]
    assert observed["isolated"] == observed["noUserSite"] == 1
    assert observed["noBytecode"] is True
    assert not list(Path(core).rglob("*.pyc"))


def test_worker_network_guard_rejects_network_before_any_request():
    import pytest

    from document_files.document_model.recognition_worker import _offline_network_guard

    for event in ("socket.connect", "socket.getaddrinfo", "urllib.Request"):
        with pytest.raises(PermissionError, match="disabled"):
            _offline_network_guard(event, ())


def test_recognition_identity_is_stable_copied_and_does_not_pin_paths():
    config = RecognitionConfig("/models", "/bin/tesseract", "/tessdata")
    backend = DoclingRecognition(config)
    assert backend.identity["modelPinning"] == "unverified"
    identity = {"packManifestSha256": "a" * 64, "pack": {"id": "recognition"}}
    pinned = DoclingRecognition(config, identity=identity)
    identity["pack"]["id"] = "changed"
    snapshot = pinned.identity
    snapshot["configuration"]["artifacts_path"] = "/other"
    snapshot["pack"]["id"] = "changed again"
    assert pinned.identity["configuration"]["artifacts_path"] == "/models"
    assert pinned.identity["pack"]["id"] == "recognition"
    assert pinned.identity["modelPinning"] == "caller_supplied_manifest"
    assert (
        DoclingRecognition(config, identity={"manifestSha256": "/models"}).identity["modelPinning"]
        == "unverified"
    )


def test_parent_managed_recognition_inherits_group_and_never_kills_callers_group(monkeypatch):
    import subprocess

    import document_files.document_model.docling_adapter as adapter

    events = []
    monkeypatch.setattr(RecognitionConfig, "validate", lambda self: None)

    class FakeProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            assert "start_new_session" not in kwargs and "creationflags" not in kwargs

        def communicate(self, content, *, timeout):
            raise subprocess.TimeoutExpired("worker", timeout)

        def poll(self):
            return None

        def kill(self):
            events.append("child_killed")

        def wait(self):
            events.append("child_reaped")

    def forbidden(*args, **kwargs):
        raise AssertionError("must not create or kill a private process group")

    monkeypatch.setattr(adapter.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(adapter, "WindowsJob", forbidden)
    monkeypatch.setattr(adapter, "process_options", forbidden)
    monkeypatch.setattr(adapter, "kill_process_tree", forbidden)
    result = DoclingRecognition(
        RecognitionConfig("/models", "/bin/tesseract", "/tessdata"),
        parent_managed=True,
    ).observe(b"public synthetic PDF")
    assert result["issues"] == [{"code": "recognition_timeout"}]
    assert events == ["child_killed", "child_reaped"]


def _geometry_observation(text, recognized, bounds):
    doc = ObservationDocument()
    chars = [
        {"text": c, "x0": i * 10, "x1": (i + 1) * 10, "top": 0, "bottom": 10, "upright": True}
        for i, c in enumerate(text)
    ]
    doc.node(
        "native",
        text,
        locator={
            "page": 1,
            "characters": chars,
            "bbox": {
                "left": 0,
                "right": len(text) * 10,
                "top": 0,
                "bottom": 10,
                "origin": "TOPLEFT",
            },
        },
    )
    doc.node(
        "recognized",
        recognized,
        locator={
            "page": 1,
            "bbox": {
                "left": bounds[0],
                "right": bounds[1],
                "top": 0,
                "bottom": 10,
                "origin": "TOPLEFT",
            },
        },
    )
    return doc


def test_pdf_cell_conflict_uses_covered_characters_not_entire_native_row():
    from document_files.document_model.pdf import _conflicts

    doc = _geometry_observation("A 19 B 27", "19", (20, 40))
    before = deepcopy(doc.nodes)
    _conflicts(doc, ["native"], ["recognized"])
    assert doc.nodes == before
    assert doc.issues == []
    doc.nodes["recognized"]["text"] = "79"
    _conflicts(doc, ["native"], ["recognized"])
    assert doc.issues[0]["code"] == "native_recognition_text_conflict"
    relation = doc.relations[-1]
    assert [(s["start"], s["end"]) for s in relation["sourceSegments"]] == [(2, 3), (3, 4)]


def test_pdf_partial_glyph_overlap_is_explicit_and_spaces_not_erased():
    from document_files.document_model.pdf import _conflicts

    doc = _geometry_observation("A19", "19", (15, 30))
    _conflicts(doc, ["native"], ["recognized"])
    assert doc.issues[0]["code"] == "native_recognition_geometry_ambiguous"
    doc = _geometry_observation("1 9", "19", (0, 30))
    _conflicts(doc, ["native"], ["recognized"])
    assert doc.issues[0]["code"] == "native_recognition_text_conflict"


def test_page_batches_release_framework_scope_and_remap_original_pages(tmp_path):
    import hashlib
    import io

    import pypdfium2 as pdfium
    from reportlab.pdfgen import canvas

    from document_files.document_model.recognition_worker import page_batches

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for i in range(3):
        pdf.drawString(20, 760, f"Page {i + 1}")
        pdf.showPage()
    pdf.save()
    content = stream.getvalue()
    calls, frames = [], []

    def convert_page(single_page):
        check = pdfium.PdfDocument(single_page)
        try:
            assert len(check) == 1
        finally:
            check.close()
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("synthetic failure without private content")
        return (
            {
                "pages": {"1": {"size": {"width": 600, "height": 800}}},
                "texts": [{"text": "public", "prov": [{"page_no": 1}]}],
            },
            "complete",
            {
                "version": "document-files.recognition-source-observations.v1",
                "cells": [{"page_no": 1, "text": "observed"}],
            },
        )

    config = RecognitionConfig("/models", "/bin/tesseract", "/tessdata")
    result = page_batches(content, config, convert_page, on_page=frames.append)
    assert result["status"] == "partial" and result["completedPages"] == [1, 2]
    assert result["issues"] == [{"code": "recognition_page_failed", "page": 3}]
    assert result["pageResults"] == []
    assert frames[1]["document"]["texts"][0]["prov"][0] == {"page_no": 2, "batch_page_no": 1}
    assert list(frames[1]["document"]["pages"]) == ["2"]
    assert frames[1]["pageRender"]["page_no"] == 2
    assert frames[1]["pageRender"]["sourceSha256"] == hashlib.sha256(content).hexdigest()
    assert frames[1]["pageRender"]["status"] == "captured"
    assert frames[1]["coordinateEvidence"]["mapping"]["status"] == "verified"
    assert frames[1]["coordinateEvidence"]["mapping"]["originalPageNumber"] == 2
    assert frames[1]["coordinateEvidence"]["mapping"]["subsetPageNumber"] == 1
    assert frames[1]["coordinateEvidence"]["mapping"]["subsetRender"]["page_no"] == 1
    assert frames[1]["sourceObservations"]["cells"][0] == {
        "page_no": 2,
        "batch_page_no": 1,
        "text": "observed",
    }
    assert frames[0]["sourceSha256"] == hashlib.sha256(content).hexdigest()


def test_worker_timeout_retains_only_complete_page_frames():
    import io
    import json

    from document_files.document_model.docling_adapter import _worker_result

    page = {"page": 1, "status": "complete", "document": {}}
    payload = json.dumps({"type": "page", "pageResult": page}).encode() + b'\n{"type":"page"'
    result = _worker_result(io.BytesIO(payload), 10000, interrupted="recognition_timeout")
    assert result["status"] == "partial"
    assert result["pageResults"] == [page] and result["completedPages"] == [1]
    assert result["issues"] == [{"code": "recognition_timeout"}]


def test_pdf_partial_page_result_uses_original_page_and_stable_page_node_ids():
    import hashlib
    import io

    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for i in range(2):
        pdf.drawString(20, 760, f"Page {i + 1}")
        pdf.showPage()
    pdf.save()
    content = stream.getvalue()

    class PartialPages:
        def observe(self, content):
            return {
                "status": "partial",
                "issues": [{"code": "recognition_timeout"}],
                "completedPages": [2],
                "pageCount": 2,
                "pageResults": [
                    {
                        "page": 2,
                        "status": "complete",
                        "sourceSha256": hashlib.sha256(content).hexdigest(),
                        "document": {
                            "pages": {"2": {"size": {"height": 800}}},
                            "texts": [{"text": "Page 2", "prov": [{"page_no": 2}]}],
                        },
                    }
                ],
            }

    doc = observe_document(content, "pdf", {}, recognition=PartialPages())
    assert doc.nodes["docling:page:2:text:0"]["sourceStructure"]["page"] == 2
    assert doc.nodes["docling:page:2:text:0"]["text"] == "Page 2"
    assert doc.provenance["recognitionPages"]["completedPages"] == [2]
    assert doc.coverage["status"] == "partial"


def test_recognition_progress_checkpoints_before_worker_exit_and_resume_skips_complete(monkeypatch):
    import hashlib
    import json
    import threading

    import document_files.document_model.docling_adapter as adapter

    content = b"public synthetic input"
    source_hash = hashlib.sha256(content).hexdigest()
    snapshots, skips, offsets, timeouts = [], [], [], []
    progressed = threading.Event()
    config = RecognitionConfig("/models", "/bin/tesseract", "/tessdata")
    backend = DoclingRecognition(config)
    monkeypatch.setattr(RecognitionConfig, "validate", lambda self: None)

    def frame(number, status="complete"):
        return {
            "page": number,
            "sourceSha256": source_hash,
            "status": status,
            "document": {"texts": [{"text": f"public page {number}"}]},
        }

    class FakeProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            self.output = kwargs["stdout"]
            self.skipped = json.loads(command[command.index("--completed-pages") + 1])
            skips.append(self.skipped)

        def communicate(self, data, *, timeout):
            assert data == content
            timeouts.append(timeout)
            page = frame(2 if self.skipped else 1)
            payload = json.dumps({"type": "page", "pageResult": page}).encode() + b"\n"
            self.output.write(payload)
            assert progressed.wait(2), "callback must run while worker is still active"
            # Reader progress must never move the descriptor owned by the writer.
            offsets.append(self.output.tell() == len(payload))
            self.output.write(json.dumps({"status": "complete", "pageCount": 2}).encode() + b"\n")
            self.returncode = 0

        def wait(self):
            return 0

    class NoJob:
        def __init__(self, process):
            pass

        def close(self):
            pass

    monkeypatch.setattr(adapter.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(adapter, "WindowsJob", NoJob)
    monkeypatch.setattr(adapter, "kill_process_tree", lambda process: None)

    def callback(state):
        assert state["sourceSha256"] == source_hash
        assert state["backendIdentity"] == backend.identity
        snapshots.append(state)
        progressed.set()

    first = backend.observe(content, checkpoint=callback)
    assert first["completedPages"] == [1]
    restore = snapshots[-1]
    restore["pageResults"].append(frame(2, "partial"))
    progressed.clear()
    second = backend.observe(content, restore=restore, checkpoint=callback, timeout_seconds=0.125)
    assert skips == [[], [1]]
    assert second["completedPages"] == [1, 2]
    assert second["pageResults"][1]["status"] == "complete"
    assert offsets == [True, True]
    assert timeouts == [600, 0.125]
    assert not any(t.name == "recognition-stdin" for t in threading.enumerate())


def test_recognition_restore_rejects_changed_input_backend_and_false_completed_pages(monkeypatch):
    import hashlib

    import pytest

    backend = DoclingRecognition(RecognitionConfig("/models", "/bin/tesseract", "/tessdata"))
    state = {
        "version": "document-files.recognition-checkpoint.v1",
        "sourceSha256": hashlib.sha256(b"public").hexdigest(),
        "backendIdentity": backend.identity,
        "pageResults": [],
        "completedPages": [],
    }
    monkeypatch.setattr(
        "subprocess.Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawned"))
    )
    for changed in (
        {**state, "sourceSha256": "0" * 64},
        {**state, "backendIdentity": {}},
        {**state, "completedPages": [1]},
    ):
        with pytest.raises(ValueError, match="checkpoint does not match"):
            backend.observe(b"public", restore=changed)


def test_page_batches_never_convert_explicit_completed_pages():
    import io

    from reportlab.pdfgen import canvas

    from document_files.document_model.recognition_worker import page_batches

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for _ in range(2):
        pdf.drawString(20, 700, "Public")
        pdf.showPage()
    pdf.save()
    calls = []

    def convert(page):
        calls.append(page)
        return {"pages": {"1": {}}, "texts": []}, "complete"

    result = page_batches(
        stream.getvalue(), RecognitionConfig("/m", "/t", "/d"), convert, completed_pages=[1]
    )
    assert len(calls) == 1
    assert result["completedPages"] == [1, 2]
    assert [p["page"] for p in result["pageResults"]] == [2]


def test_checkpoint_callback_failure_reaps_communication_thread(monkeypatch):
    import hashlib
    import json
    import threading

    import pytest

    import document_files.document_model.docling_adapter as adapter

    stopped = threading.Event()
    content = b"public"
    monkeypatch.setattr(RecognitionConfig, "validate", lambda self: None)

    class FakeProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            self.output = kwargs["stdout"]

        def communicate(self, data, *, timeout):
            page = {
                "page": 1,
                "status": "complete",
                "document": {},
                "sourceSha256": hashlib.sha256(content).hexdigest(),
            }
            self.output.write(json.dumps({"type": "page", "pageResult": page}).encode() + b"\n")
            assert stopped.wait(2)
            self.returncode = -9

        def wait(self):
            return -9

    class NoJob:
        def __init__(self, process):
            pass

        def close(self):
            pass

    monkeypatch.setattr(adapter.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(adapter, "WindowsJob", NoJob)
    monkeypatch.setattr(adapter, "kill_process_tree", lambda process: stopped.set())

    def cannot_save(state):
        raise ValueError("synthetic checkpoint failure")

    with pytest.raises(adapter.RecognitionCheckpointError, match="checkpoint persistence failed"):
        DoclingRecognition(RecognitionConfig("/m", "/t", "/d")).observe(
            content, checkpoint=cannot_save
        )
    assert stopped.is_set()
    assert not any(t.name == "recognition-stdin" for t in threading.enumerate())


def test_pdf_does_not_downgrade_checkpoint_storage_failure(monkeypatch):
    import pytest

    from document_files.document_model.docling_adapter import RecognitionCheckpointError
    from document_files.document_model.pdf import observe_pdf

    monkeypatch.setattr("document_files.document_model.pdf._native_pdf", lambda doc, data: [])

    class BrokenStorage:
        def observe(self, content):
            raise RecognitionCheckpointError()

    doc = ObservationDocument()
    with pytest.raises(RecognitionCheckpointError, match="checkpoint persistence failed"):
        observe_pdf(doc, b"public", recognition=BrokenStorage())
    assert not any(issue["code"] == "pdf_layout_recognition_failed" for issue in doc.issues)


def _project_geometry(doc, recognized_ids=("recognized",), *, table_cell=None):
    from document_files.document_model.native import bind_spans
    from document_files.document_model.observe import _regions
    from document_files.document_model.pdf import _conflicts, _semantic_candidates

    if table_cell:
        doc.tables["table"] = {
            "id": "table",
            "basis": "docling_table_cells",
            "cells": [{"sourceRef": table_cell, "row": 0, "col": 0, "rowSpan": 1, "colSpan": 1}],
            "contextNodeIds": [],
        }
    for ref in list(doc.nodes):
        bind_spans(doc, ref)
    comparisons = _conflicts(doc, ["native"], list(recognized_ids))
    primary = _semantic_candidates(doc, ["native"], list(recognized_ids), comparisons)
    _regions(doc, primary)
    return primary


def test_equal_native_and_recognition_have_one_semantic_representative_but_both_sources():
    doc = _geometry_observation("Count: 19", "Count: 19", (0, 90))
    raw = deepcopy(doc.nodes["native"]["sourceStructure"]["characters"])
    primary = _project_geometry(doc)
    assert primary == ["recognized"]
    assert doc.nodes["native"]["text"] == doc.nodes["recognized"]["text"] == "Count: 19"
    assert doc.nodes["native"]["sourceStructure"]["characters"] == raw
    assert doc.regions[0]["nodeIds"] == ["recognized"]
    assert any(r["kind"] == "observationEquivalence" for r in doc.relations)
    assert doc.provenance["pdfSemanticProjection"]["sourceObservationsPreserved"] is True


def test_table_cell_representative_preserves_uncovered_native_remainders():
    doc = _geometry_observation("A19B27", "19", (10, 30))
    doc.node(
        "paragraph",
        "A19B27",
        locator={
            "page": 1,
            "bbox": {"left": 0, "right": 60, "top": 0, "bottom": 10, "origin": "TOPLEFT"},
        },
    )
    primary = _project_geometry(doc, ("paragraph", "recognized"), table_cell="recognized")
    assert "paragraph" not in primary and "native" not in primary
    assert [doc.nodes[ref]["text"] for ref in primary] == ["A", "B27", "19"]
    assert doc.nodes["native"]["text"] == "A19B27"
    assert doc.nodes["paragraph"]["text"] == "A19B27"
    remainder = doc.nodes["native/remainder/3:6"]
    assert remainder["sourceSegments"] == [
        {"sourceRef": "native", "sourceStart": 3, "sourceEnd": 6, "start": 0, "end": 3}
    ]


def test_native_ocr_disagreement_is_context_alternative_and_blocks_present_binding():
    doc = _geometry_observation("A19B", "79", (10, 30))
    primary = _project_geometry(doc, table_cell="recognized")
    assert "native" not in primary
    table_region = next(r for r in doc.regions if r.get("tableRef"))
    alternatives = [
        ref
        for ref in table_region["contextNodeIds"]
        if doc.nodes[ref].get("semanticInput", {}).get("role") == "conflict_alternative"
    ]
    assert len(alternatives) == 1 and doc.nodes[alternatives[0]]["text"] == "19"
    assert doc.nodes["recognized"]["text"] == "79"
    assert all(
        b["candidateStatus"] == "unresolved_conflict"
        for b in doc.bindings.values()
        if b["sourceRef"] in ["recognized", *alternatives]
    )
    assert any(
        r["kind"] == "observationConflict"
        and r.get("sourceRef") == alternatives[0]
        and r["targetRef"] == "recognized"
        for r in doc.relations
    )


def test_equal_text_without_geometry_is_not_assumed_to_be_the_same_observation():
    doc = _geometry_observation("19", "19", (0, 20))
    doc.nodes["recognized"]["sourceStructure"]["bbox"] = None
    primary = _project_geometry(doc)
    assert primary == ["native", "recognized"]
    assert any(i["code"] == "native_recognition_alignment_unresolved" for i in doc.issues)
    assert not any(r["kind"] == "observationEquivalence" for r in doc.relations)


def test_recognition_normalized_text_cannot_hide_original_text_conflict():
    doc = _geometry_observation("19", "19", (0, 20))
    doc.nodes["recognized"]["originalRecognitionText"] = "79"
    _project_geometry(doc)
    assert any(i["code"] == "native_recognition_text_conflict" for i in doc.issues)
    assert doc.nodes["recognized"]["semanticInput"]["role"] == "unresolved_conflict"


def test_typed_source_export_preserves_ocr_provenance_not_page_membership():
    from types import SimpleNamespace

    from document_files.document_model.recognition_worker import export_source_observations

    class Geometry:
        def model_dump(self, **kwargs):
            return {"l": 10, "t": 20, "r": 30, "b": 40, "coord_origin": "TOPLEFT"}

    def cell(text, from_ocr):
        return SimpleNamespace(
            text=text,
            orig=text,
            from_ocr=from_ocr,
            index=3,
            confidence=0.75,
            rect=Geometry(),
            to_bounding_box=Geometry,
        )

    page = SimpleNamespace(
        page_no=1,
        parsed_page=SimpleNamespace(
            textline_cells=[
                cell("OCR 0", True),
                cell("native 7", False),
                cell("unknown", None),
            ]
        ),
    )
    result = export_source_observations([page])
    assert [c["sourceKind"] for c in result["cells"]] == ["ocr", "pdf_text"]
    assert result["cells"][0]["raw"] == "OCR 0"
    assert result["cells"][0]["confidence"] == 0.75
    assert result["unavailablePages"] == [1]
    assert result["coverage"] == "post_merge_cells_only_not_all_raw_ocr_detections"


def test_declared_table_holes_are_not_imported_as_blank_evidence():
    source = exported()
    source["tables"][0]["data"].update(num_rows=3, num_cols=2)
    doc = ObservationDocument()
    import_docling(doc, source)
    table = doc.tables["docling:table:0"]
    assert len(table["cells"]) == 2
    assert table["unobservedCellCount"] == 3
    issue = next(i for i in doc.issues if i["code"] == "recognition_table_cells_unobserved")
    assert issue["meaning"] == "not_observed_not_proven_blank"
    assert not any(n["text"] == "MUST NOT IMPORT" for n in doc.nodes.values())


def test_unassigned_ocr_sources_survive_structure_without_duplicate_matching_fields():
    from document_files.document_model.recognition_sources import import_source_observations

    source = exported()
    cell = source["tables"][0]["data"]["table_cells"][0]
    cell.update(
        text="Item 0", bbox={"l": 10, "t": 20, "r": 100, "b": 30, "coord_origin": "TOPLEFT"}
    )
    doc = ObservationDocument()
    ids = import_docling(doc, source)

    def token(text, y, *, from_ocr=True):
        return {
            "page_no": 1,
            "text": text,
            "raw": text,
            "fromOcr": from_ocr,
            "sourceKind": "ocr" if from_ocr else "pdf_text",
            "confidence": 0.9,
            "bbox": {"l": 10, "t": y, "r": 40, "b": y + 10, "coord_origin": "TOPLEFT"},
        }

    payload = {
        "version": "document-files.recognition-source-observations.v1",
        "stage": "docling_post_ocr_merge",
        "cells": [
            token("Item", 20),
            token("lost row", 50),
            token("native", 80, from_ocr=False),
        ],
    }
    primary = import_source_observations(doc, payload, source, ids, prefix="docling")
    assert primary == ["docling:source:1"]
    assert doc.nodes["docling:source:1"]["originalRecognitionText"] == "lost row"
    assert doc.nodes["docling:source:2"]["sourceStructure"]["sourceKind"] == "pdf_text"
    assert not doc.nodes["docling:source:2"]["recognizedText"]
    support = next(r for r in doc.relations if r["kind"] == "recognitionSourceSupport")
    assert support["sourceRef"] == "docling:source:0" and not support["truthVerified"]
    assert any(i["code"] == "recognition_unassigned_content" for i in doc.issues)
    assert "docling:source:1" in doc.tables["docling:table:0"]["contextNodeIds"]
    # Observed source text is not a new invented table cell.
    assert len(doc.tables["docling:table:0"]["cells"]) == 2


def test_ocr_source_alignment_disagreement_blocks_silent_structural_value_choice():
    from document_files.document_model.recognition_sources import import_source_observations

    source = exported()
    source["tables"][0]["data"]["table_cells"][0].update(
        text="17",
        bbox={"l": 10, "t": 20, "r": 50, "b": 30, "coord_origin": "TOPLEFT"},
    )
    doc = ObservationDocument()
    ids = import_docling(doc, source)
    payload = {
        "version": "document-files.recognition-source-observations.v1",
        "cells": [
            {
                "page_no": 1,
                "text": "19",
                "raw": "19",
                "fromOcr": True,
                "sourceKind": "ocr",
                "bbox": {"l": 10, "t": 20, "r": 50, "b": 30, "coord_origin": "TOPLEFT"},
            }
        ],
    }
    primary = import_source_observations(doc, payload, source, ids, prefix="docling")
    assert primary == ["docling:source:0"]
    assert doc.nodes[ids[0]]["text"] == "17" and doc.nodes[primary[0]]["text"] == "19"
    assert all(
        b.get("candidateStatus") == "unresolved_conflict"
        for b in doc.bindings.values()
        if b["sourceRef"] in {ids[0], primary[0]}
    )
    assert any(r["kind"] == "observationConflict" for r in doc.relations)


def test_framework_success_is_not_a_verified_content_coverage_claim(monkeypatch):
    from document_files.document_model import pdf

    monkeypatch.setattr(pdf, "_native_pdf", lambda *_: [])

    class Recognition:
        def observe(self, content):
            return {
                "status": "complete",
                "document": exported(),
                "sourceObservations": {
                    "version": "document-files.recognition-source-observations.v1",
                    "cells": [],
                    "coverage": "post_merge_cells_only_not_all_raw_ocr_detections",
                },
            }

    doc = ObservationDocument()
    pdf.observe_pdf(doc, b"synthetic", recognition=Recognition())
    assert doc.coverage["recognitionConversion"] == "complete"
    assert doc.coverage["recognitionContentCompleteness"] == "unverified"
    assert any(i["code"] == "recognition_content_completeness_unverified" for i in doc.issues)


def test_ocr_tsv_parser_keeps_exact_numeric_and_null_like_lexemes():
    from document_files.document_model.table_ocr_repair import parse_tsv

    header = "left\ttop\twidth\theight\tconf\ttext\n"
    values = ["00001234567890123456789", "0.1234567890123456789", "NA", "null", "0"]
    rows = parse_tsv((header + "".join(f"1\t2\t3\t4\t99\t{v}\n" for v in values)).encode())
    assert [row["text"] for row in rows] == values


def test_table_repair_requires_closed_grid_and_preserves_original_pixels():
    import pytest

    pytest.importorskip("cv2")
    import numpy as np
    from PIL import Image, ImageDraw

    from document_files.document_model.table_ocr_repair import remove_grid

    original = Image.new("RGB", (300, 180), "white")
    draw = ImageDraw.Draw(original)
    for x in (10, 100, 200, 290):
        draw.line((x, 10, x, 170), fill="black", width=2)
    for y in (10, 90, 170):
        draw.line((10, y, 290, y), fill="black", width=2)
    draw.rectangle((30, 35, 45, 50), fill="black")
    before = original.tobytes()
    derived, info = remove_grid(original, max_pixels=100000)
    assert derived is not None and original.tobytes() == before
    assert derived.getpixel((35, 40)) == original.getpixel((35, 40))
    mask = np.zeros((180, 300), dtype=bool)
    for y, start, end in info["maskRuns"]:
        mask[y, start:end] = True
    assert not np.any(np.any(np.asarray(original) != np.asarray(derived), axis=2) & ~mask)
    assert remove_grid(original, max_pixels=100)[0] is None
    no_grid = Image.new("RGB", (300, 180), "white")
    ImageDraw.Draw(no_grid).line((10, 90, 290, 90), fill="black", width=2)
    assert remove_grid(no_grid, max_pixels=100000)[0] is None


def test_table_repair_stage_preserves_conflicting_original_and_applies_call_budget(monkeypatch):
    import pytest

    pytest.importorskip("docling")
    pytest.importorskip("cv2")
    from types import SimpleNamespace

    import pandas as pd
    from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel
    from docling_core.types.doc.page import BoundingRectangle, TextCell
    from PIL import Image, ImageDraw

    from document_files.document_model.docling_pipeline import pipeline_class

    config = RecognitionConfig(
        "/models", "/tesseract", "/tessdata", table_ocr_repair="ruled_tables_v1", repair_max_calls=1
    )
    source_orientation = {"status": "verified_upright", "sourcePassId": "fixture"}
    monkeypatch.setattr(
        "document_files.document_model.docling_pipeline.table_orientation_evidence",
        lambda *_args, **_kwargs: dict(source_orientation),
    )
    restored = {}
    cls = pipeline_class(config, {}, restored)._product_ocr_type
    model = cls.__new__(cls)
    model.scale, model.orientation = 3, 0
    image = Image.new("RGB", (300, 180), "white")
    draw = ImageDraw.Draw(image)
    for x in (0, 99, 199, 299):
        draw.line((x, 0, x, 179), fill="black", width=2)
    for y in (0, 89, 179):
        draw.line((0, y, 299, y), fill="black", width=2)
    bbox = BoundingBox(l=0, t=0, r=100, b=60, coord_origin=CoordOrigin.TOPLEFT)
    label = TextCell(
        index=0,
        text="Qty",
        orig="Qty",
        from_ocr=True,
        rect=BoundingRectangle.from_bounding_box(
            BoundingBox(l=10, t=10, r=20, b=20, coord_origin=CoordOrigin.TOPLEFT)
        ),
    )
    page = SimpleNamespace(
        size=SimpleNamespace(width=100, height=60),
        page_no=1,
        predictions=SimpleNamespace(
            layout=SimpleNamespace(
                clusters=[SimpleNamespace(label=DocItemLabel.TABLE, bbox=bbox, id=1)]
            )
        ),
        parsed_page=SimpleNamespace(textline_cells=[label]),
        get_image=lambda **_: image,
    )
    calls = []

    def ocr(*args):
        calls.append(1)
        return pd.DataFrame(
            [
                {"text": "067", "left": 30, "top": 30, "width": 30, "height": 30, "conf": 80},
                {
                    "text": "000000123456789012345",
                    "left": 120,
                    "top": 120,
                    "width": 30,
                    "height": 30,
                    "conf": 90,
                },
            ]
        )

    model._run_tesseract = ocr
    snapshot = {"original": [], "supplemental": [], "repairs": [], "issues": []}
    model.repair(page, [label], snapshot)
    assert calls == [1]
    assert [c.orig for c in page.parsed_page.textline_cells] == ["Qty", "000000123456789012345"]
    assert snapshot["supplemental"][0]["candidateStatus"] == "unresolved_conflict"
    model.repair(page, [label], snapshot)
    assert calls == [1]
    assert any(i["code"] == "table_ocr_repair_budget_exceeded" for i in snapshot["issues"])
    restored.update(
        {
            "originalOCRFingerprint": "same-original",
            "tableRepairs": deepcopy(snapshot["repairs"]),
            "cells": deepcopy(snapshot["supplemental"]),
        }
    )
    snapshot = {
        "original": [],
        "supplemental": [],
        "repairs": [],
        "issues": [],
        "originalOCRFingerprint": "same-original",
    }
    page.parsed_page.textline_cells = [label]
    model.repair(page, [label], snapshot)
    assert calls == [1]  # Completed table is reused despite exhausted attempt budget.
    assert snapshot["repairs"][0]["reusedFromCheckpoint"]
    assert [c.orig for c in page.parsed_page.textline_cells] == ["Qty", "000000123456789012345"]
    source_orientation["status"] = "unverified"
    snapshot = {"original": [], "supplemental": [], "repairs": [], "issues": []}
    model.repair(page, [label], snapshot)
    assert calls == [1]
    assert snapshot["issues"][0]["code"] == "table_ocr_repair_orientation_unresolved"
    label.from_ocr = False
    snapshot = {"original": [], "supplemental": [], "repairs": [], "issues": []}
    model.repair(page, [label], snapshot)
    assert calls == [1] and snapshot["repairs"][0]["status"] == "native_text_table_skipped"


def test_repair_budget_is_resumable_but_quality_conflicts_are_not_automatic_retries():
    from document_files.document_model.recognition_worker import conversion_page_status

    for code in ("table_ocr_repair_budget_exceeded", "table_ocr_repair_timeout"):
        assert conversion_page_status("success", {"issues": [{"code": code}]}) == "partial"
    for code in ("table_ocr_repair_text_conflict", "table_ocr_repair_region_too_large"):
        assert conversion_page_status("success", {"issues": [{"code": code}]}) == "complete"


def test_page_batches_pause_at_unfinished_repair_without_revisiting_completed_pages():
    import io

    from reportlab.pdfgen import canvas

    from document_files.document_model.recognition_worker import page_batches

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    for _ in range(3):
        pdf.drawString(10, 10, "public")
        pdf.showPage()
    pdf.save()
    calls, frames = [], []

    def convert(data):
        calls.append(1)
        return (
            {"pages": {"1": {}}},
            "partial",
            {"issues": [{"code": "table_ocr_repair_budget_exceeded"}]},
        )

    result = page_batches(
        stream.getvalue(),
        RecognitionConfig("/models", "/bin/ocr", "/tessdata"),
        convert,
        completed_pages=[1],
        on_page=frames.append,
    )
    assert calls == [1] and frames[0]["page"] == 2
    assert result["completedPages"] == [1] and result["processedPages"] == [1, 2]


def test_ocr_spooled_stdout_enforces_byte_limit_and_timeout():
    import subprocess
    import sys

    import pytest

    from document_files.document_model.table_ocr_repair import bounded_tsv, parse_tsv

    assert (
        bounded_tsv(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'public\\n')"],
            timeout=3,
            max_bytes=100,
        )
        == b"public\n"
    )
    with pytest.raises(ValueError, match="output budget"):
        bounded_tsv(
            [sys.executable, "-c", "import sys; sys.stdout.write('x'*10000)"],
            timeout=3,
            max_bytes=100,
        )
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_tsv(
            [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05, max_bytes=100
        )
    with pytest.raises(ValueError, match="token budget"):
        parse_tsv(b"left\ttop\twidth\theight\tconf\ttext\n1\t2\t3\t4\t90\t0\n", max_rows=0)


def test_ruling_candidate_preserves_ocr_and_blocks_present_binding():
    from document_files.document_model.recognition_sources import import_source_observations

    doc = ObservationDocument()
    selection = {
        "selection": "ruling_line_excluded",
        "rulingLineEvidence": {
            "basis": "all_observed_ink_within_closed_grid_long_stroke_mask",
            "inkPixels": 20,
            "nonRulingInkPixels": 0,
            "originalTextPreserved": True,
            "conflictStatus": "unresolved_ocr_text_vs_ruling_geometry",
        },
    }
    payload = {
        "version": "document-files.recognition-source-observations.v1",
        "cells": [
            {
                "text": "arbitrary lexeme",
                "raw": "arbitrary lexeme",
                "page_no": 1,
                "fromOcr": True,
                "sourceKind": "ocr",
                "structureView": selection,
                "bbox": {"l": 10, "t": 10, "r": 15, "b": 30, "coord_origin": "TOPLEFT"},
            }
        ],
    }
    assert import_source_observations(doc, payload, exported(), [], prefix="docling") == []
    node = doc.nodes["docling:source:0"]
    assert node["text"] == "arbitrary lexeme"
    assert node["structureViewSelection"] == selection
    assert node["semanticInput"]["role"] == "context_only"
    assert all(b["candidateStatus"] == "unresolved_conflict" for b in doc.bindings.values())


def raw_ledger_fixture(text="001.20"):
    """Synthetic contract evidence only; this is never OCR quality evidence."""
    import csv
    import hashlib
    import io

    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    tsv = "left\ttop\twidth\theight\tconf\ttext\n10\t20\t30\t10\t90\t" + text + "\n"
    raw_row = next(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
    bounds = {"l": 10, "t": 20, "r": 40, "b": 30, "coord_origin": "TOPLEFT"}
    capture = {
        "passId": "pass-0",
        "page_no": 1,
        "sourcePass": "page_ocr",
        "status": "complete",
        "tsv": tsv,
        "tsvSha256": hashlib.sha256(tsv.encode()).hexdigest(),
        "image": {"sha256": "1" * 64, "size": [600, 800], "mode": "RGB"},
        "transform": {
            "scale": 1,
            "orientation": 0,
            "crop": {"l": 0, "t": 0, "r": 600, "b": 800, "coord_origin": "TOPLEFT"},
        },
        "detections": [
            {
                "ordinal": 0,
                "text": text,
                "raw": raw_row,
                "accepted": True,
                "confidence": 90.0,
                "imageBBox": {"left": 10, "top": 20, "width": 30, "height": 10},
                "pageBBox": bounds,
                "mappingBasis": "pinned_upstream_pre_merge_order_text_confidence",
            }
        ],
    }
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload = {
        "version": "document-files.recognition-source-observations.v1",
        "cells": [],
        "rawCaptureVersion": "document-files.raw-ocr.v1",
        "rawOCRPasses": [capture],
        "rawCapturePages": [
            {"page_no": 1, "captureAvailable": True, "passFingerprints": [capture["fingerprint"]]}
        ],
    }
    doc = ObservationDocument()
    target = doc.node(
        "structural",
        text,
        role="recognized_text",
        locator={
            "page": 1,
            "bbox": {"left": 10, "top": 20, "right": 40, "bottom": 30, "origin": "TOPLEFT"},
        },
    )
    return doc, payload, {"pages": {"1": {"size": {"height": 800}}}}, [target]


def test_raw_detection_ledger_conserves_lexemes_without_claiming_ocr_truth():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, source, ids = raw_ledger_fixture()
    before = deepcopy(payload)
    import_source_observations(doc, payload, source, ids, prefix="page1")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert payload == before
    assert ledger["allRawOCRDetectionsPreserved"] is True
    assert ledger["observedProcessingCoverage"] == "complete"
    assert ledger["ocrTruthVerified"] is False
    assert ledger["pageContentCompletenessVerified"] is False
    assert ledger["rawOCRPasses"][0]["detections"][0]["text"] == "001.20"
    assert ledger["entries"][0]["targetRefs"] == ids
    assert "recognitionContentCompleteness" not in doc.coverage
    payload["rawOCRPasses"][0]["detections"][0]["text"] = "changed"
    assert ledger["rawOCRPasses"][0]["detections"][0]["text"] == "001.20"


def test_raw_processing_coverage_does_not_depend_on_other_pages_or_aggregate_issues():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, source, ids = raw_ledger_fixture()
    preserved = [
        {"code": "pdf_page_has_no_native_text", "page": 1},
        {"code": "reading_order_unverified"},
        {"code": "recognition_content_completeness_unverified"},
        {"code": "recognition_observed_processing_partial", "recognitionBatch": "page1"},
        {"code": "recognition_table_cells_unobserved", "tableRef": "page2:table:0", "page": 2},
        {"code": "recognition_source_cell_invalid", "recognitionBatch": "page2"},
        {"code": "recognition_text_invalid"},
    ]
    doc.issues.extend(deepcopy(preserved))
    import_source_observations(doc, payload, source, ids, prefix="page1")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["version"] == "document-files.observed-processing-ledger.v5"
    assert ledger["observedProcessingCoverage"] == "complete"
    assert ledger["processingDependencies"]["issues"] == []
    assert ledger["processingDependencies"]["pages"] == [1]
    assert ledger["pageContentCompletenessVerified"] is False
    assert ledger["ocrTruthVerified"] is False
    assert doc.issues == preserved


def test_raw_processing_coverage_keeps_local_structural_failures():
    from document_files.document_model.recognition_sources import import_source_observations

    for failure in ("missing_cells", "overlap", "source_invalid", "text_invalid", "empty_table"):
        doc, payload, source, ids = raw_ledger_fixture()
        if failure in {"missing_cells", "overlap", "empty_table"}:
            table_ref = "page1:table:0"
            doc.tables[table_ref] = {
                "id": table_ref,
                "cells": [] if failure == "empty_table" else [{"sourceRef": ids[0]}],
                "declaredRowCount": 1,
                "declaredColCount": 2,
            }
            code = (
                "recognition_table_cells_overlap"
                if failure == "overlap"
                else "recognition_table_cells_unobserved"
            )
            doc.issue(code, tableRef=table_ref)
        elif failure == "source_invalid":
            payload["cells"] = [{"text": 1}]
        else:
            source["texts"] = [{"text": 1}]
            doc.issue("recognition_text_invalid")
        before = deepcopy(doc.issues)
        import_source_observations(doc, payload, source, ids, prefix="page1")
        ledger = doc.provenance["recognitionProcessingLedgers"][0]
        assert ledger["allRawOCRDetectionsPreserved"] is True
        assert ledger["observedProcessingCoverage"] == "partial"
        assert ledger["processingDependencies"]["issues"]
        assert doc.issues[: len(before)] == before


def test_raw_ledger_loss_tampering_and_missing_pass_are_not_verified():
    from document_files.document_model.recognition_sources import import_source_observations

    mutations = [
        lambda p: p["rawOCRPasses"][0]["detections"].clear(),
        lambda p: p["rawOCRPasses"].clear(),
        lambda p: p["rawOCRPasses"].append(deepcopy(p["rawOCRPasses"][0])),
        lambda p: p["rawOCRPasses"][0].update(tsvSha256="0" * 64),
        lambda p: p["rawCapturePages"].clear(),
        lambda p: p.update(issues=[{"code": "recognition_raw_capture_failed"}]),
    ]
    for mutate in mutations:
        doc, payload, source, ids = raw_ledger_fixture()
        mutate(payload)
        import_source_observations(doc, payload, source, ids, prefix="page1")
        ledger = doc.provenance["recognitionProcessingLedgers"][0]
        assert ledger["allRawOCRDetectionsPreserved"] is False
        assert ledger["observedProcessingCoverage"] == "partial"
        assert any(i["code"] == "recognition_observed_processing_partial" for i in doc.issues)


def test_raw_ledger_missing_or_ambiguous_structure_remains_partial():
    from document_files.document_model.recognition_sources import import_source_observations

    for change in ("missing", "ambiguous", "unsupported_suffix", "budget"):
        doc, payload, source, ids = raw_ledger_fixture()
        if change == "missing":
            doc.nodes[ids[0]]["text"] = "different"
        elif change == "ambiguous":
            doc.nodes["other"] = {**deepcopy(doc.nodes[ids[0]]), "id": "other"}
            ids.append("other")
        elif change == "unsupported_suffix":
            doc.nodes[ids[0]]["text"] += " invented"
        else:
            payload["issues"] = [{"code": "table_ocr_repair_budget_exceeded"}]
        import_source_observations(doc, payload, source, ids, prefix="page1")
        ledger = doc.provenance["recognitionProcessingLedgers"][0]
        assert ledger["allRawOCRDetectionsPreserved"] is True
        assert ledger["observedProcessingCoverage"] == "partial"
        if change == "unsupported_suffix":
            assert ledger["unsupportedStructuralText"][0]["unaccountedCharacterCount"] == 8


def test_raw_capture_fingerprint_survives_original_page_remapping():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, source, ids = raw_ledger_fixture()
    payload["rawOCRPasses"][0].update(page_no=2, batch_page_no=1)
    payload["rawCapturePages"][0].update(page_no=2, batch_page_no=1)
    source["pages"]["2"] = source["pages"].pop("1")
    doc.nodes[ids[0]]["sourceStructure"]["page"] = 2
    import_source_observations(doc, payload, source, ids, prefix="page2")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["allRawOCRDetectionsPreserved"] is True
    assert ledger["entries"][0]["status"] == "structural_observation"


def test_raw_ledger_missing_transform_and_table_holes_are_not_complete():
    from document_files.document_model.recognition_sources import (
        import_source_observations,
        raw_pass_fingerprint,
    )

    doc, payload, source, ids = raw_ledger_fixture()
    capture = payload["rawOCRPasses"][0]
    del capture["detections"][0]["pageBBox"]
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload["rawCapturePages"][0]["passFingerprints"] = [capture["fingerprint"]]
    import_source_observations(doc, payload, source, ids, prefix="page1")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["allRawOCRDetectionsPreserved"] is True
    assert ledger["entries"][0]["reason"] == "page_transform_unresolved"
    assert ledger["observedProcessingCoverage"] == "partial"
    doc, payload, source, ids = raw_ledger_fixture()
    doc.tables["table"] = {"id": "table", "cells": [{"sourceRef": ids[0]}]}
    import_source_observations(doc, payload, source, ids, prefix="page1")
    assert doc.provenance["recognitionProcessingLedgers"][0]["unverifiedTableExtents"] == ["table"]
    assert (
        doc.provenance["recognitionProcessingLedgers"][0]["observedProcessingCoverage"] == "partial"
    )


def test_docling_overlapping_cells_are_not_silently_set_deduplicated():
    source = exported()
    source["tables"][0]["data"].update(num_rows=2, num_cols=2)
    source["tables"][0]["data"]["table_cells"].append(
        deepcopy(source["tables"][0]["data"]["table_cells"][0])
    )
    doc = ObservationDocument()
    import_docling(doc, source)
    assert any(i["code"] == "recognition_table_cells_overlap" for i in doc.issues)
    assert any(i["code"] == "recognition_table_cells_unobserved" for i in doc.issues)


def test_raw_tsv_capture_precedes_dataframe_and_merge_with_exact_pixel_transform(
    tmp_path, monkeypatch
):
    import hashlib
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("docling")
    from docling_core.types.doc import BoundingBox, CoordOrigin
    from docling_core.types.doc.page import BoundingRectangle, TextCell
    from PIL import Image

    from document_files.document_model import docling_pipeline

    cls = docling_pipeline.pipeline_class(
        RecognitionConfig("/models", "/ocr", "/data"), {}
    )._product_ocr_type
    model = cls.__new__(cls)
    model.options = SimpleNamespace(lang=["kor", "eng"], psm=3)
    model._safe_tesseract_cmd, model._safe_tessdata_path = "/ocr", "/data"
    model.scale, model.orientation = 3, None
    model.raw_rectangles = [{"l": 100, "t": 200, "r": 300, "b": 400, "coord_origin": "TOPLEFT"}]
    path = tmp_path / "crop.png"
    Image.new("RGB", (60, 30), "white").save(path)
    raw = (
        b"left\ttop\twidth\theight\tconf\ttext\n3\t6\t30\t9\t90\t001.20\n4\t7\t0\t9\t70\tINVALID\n"
    )
    monkeypatch.setattr(docling_pipeline, "bounded_tsv", lambda *a, **k: raw)
    result = model._run_tesseract(str(path), None)
    assert list(result["text"]) == ["001.20"]
    capture = model.raw_passes[0]
    assert capture["transform"]["orientation"] == 0
    assert capture["transform"]["orientationObservation"] is None
    assert capture["transform"]["orientationBasis"] == "upstream_no_rotation_fallback"
    assert model.orientation is None  # Repair policy still sees an unknown orientation.
    assert capture["tsv"].encode() == raw
    assert capture["tsvSha256"] == hashlib.sha256(raw).hexdigest()
    assert [d["text"] for d in capture["detections"]] == ["001.20", "INVALID"]
    assert capture["detections"][1]["accepted"] is False
    cell = TextCell(
        index=0,
        text="001.20",
        orig="001.20",
        from_ocr=True,
        confidence=0.9,
        rect=BoundingRectangle.from_bounding_box(
            BoundingBox(l=101, t=202, r=111, b=205, coord_origin=CoordOrigin.TOPLEFT)
        ),
    )
    monkeypatch.setattr(cls.__bases__[0], "post_process_cells", lambda *a: None)
    model.post_process_cells([cell], None, None)
    assert capture["detections"][0]["pageBBox"]["l"] == 101
    model.raw_repair_transform = {
        "pixelOrigin": [300, 600],
        "scale": [3, 3],
        "cellUnitIndex": 0,
        "repairIndex": 0,
    }
    model._run_tesseract(str(path), None)
    assert model.raw_passes[1]["detections"][0]["pageBBox"]["t"] == 202
    assert model.raw_passes[1]["sourcePass"] == "table_repair"


def test_empty_raw_capture_is_not_page_content_completeness():
    import hashlib

    from document_files.document_model.recognition_sources import (
        import_source_observations,
        raw_pass_fingerprint,
    )

    doc, payload, source, ids = raw_ledger_fixture()
    capture = payload["rawOCRPasses"][0]
    capture["tsv"] = capture["tsv"].splitlines()[0] + "\n"
    capture["tsvSha256"] = hashlib.sha256(capture["tsv"].encode()).hexdigest()
    capture["detections"] = []
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    payload["rawCapturePages"][0]["passFingerprints"] = [capture["fingerprint"]]
    import_source_observations(doc, payload, source, ids, prefix="page1")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["allRawOCRDetectionsPreserved"] is True
    assert ledger["unsupportedStructuralText"]
    assert ledger["observedProcessingCoverage"] == "partial"
    assert ledger["pageContentCompletenessVerified"] is False


def test_raw_ledger_checks_rotation_and_rejects_inconsistent_page_transform():
    from document_files.document_model.recognition_sources import (
        import_source_observations,
        raw_pass_fingerprint,
    )

    for wrong in (False, True):
        doc, payload, source, ids = raw_ledger_fixture()
        capture = payload["rawOCRPasses"][0]
        capture["transform"]["orientation"] = 90
        bounds = {"l": 20, "t": 560, "r": 30, "b": 590, "coord_origin": "TOPLEFT"}
        capture["detections"][0]["pageBBox"] = bounds
        doc.nodes[ids[0]]["sourceStructure"]["bbox"] = {
            "left": 20,
            "top": 560,
            "right": 30,
            "bottom": 590,
            "origin": "TOPLEFT",
        }
        if wrong:
            capture["transform"]["scale"] = 2
        capture["fingerprint"] = raw_pass_fingerprint(capture)
        payload["rawCapturePages"][0]["passFingerprints"] = [capture["fingerprint"]]
        import_source_observations(doc, payload, source, ids, prefix="page1")
        ledger = doc.provenance["recognitionProcessingLedgers"][0]
        assert ledger["observedProcessingCoverage"] == ("partial" if wrong else "complete")


def test_raw_explicit_exclusion_keeps_token_and_never_proves_blank():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, source, _ = raw_ledger_fixture("|")
    payload["cells"] = [
        {
            "page_no": 1,
            "text": "|",
            "raw": "|",
            "fromOcr": True,
            "sourceKind": "ocr",
            "bbox": deepcopy(payload["rawOCRPasses"][0]["detections"][0]["pageBBox"]),
            "structureView": {
                "selection": "ruling_line_excluded",
                "rulingLineEvidence": {
                    "basis": "all_observed_ink_within_closed_grid_long_stroke_mask",
                    "nonRulingInkPixels": 0,
                    "originalTextPreserved": True,
                },
            },
        }
    ]
    import_source_observations(doc, payload, source, [], prefix="page1")
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["entries"][0]["status"] == "explicit_geometric_exclusion"
    assert ledger["entries"][0]["truthVerified"] is False
    assert doc.nodes["page1:source:0"]["originalRecognitionText"] == "|"
    assert ledger["rawOCRPasses"][0]["detections"][0]["text"] == "|"
    assert all(n["text"] for n in doc.nodes.values())


def test_raw_tsv_failed_call_remains_an_incomplete_capture(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("docling")
    from PIL import Image

    from document_files.document_model import docling_pipeline

    cls = docling_pipeline.pipeline_class(
        RecognitionConfig("/models", "/ocr", "/data"), {}
    )._product_ocr_type
    model = cls.__new__(cls)
    model.options = SimpleNamespace(lang=["kor", "eng"], psm=3)
    model._safe_tesseract_cmd, model._safe_tessdata_path = "/ocr", "/data"
    model.scale, model.orientation = 3, 0
    path = tmp_path / "crop.png"
    Image.new("RGB", (60, 30), "white").save(path)

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("fixed OCR", 1)

    monkeypatch.setattr(docling_pipeline, "bounded_tsv", fail)
    with pytest.raises(subprocess.TimeoutExpired):
        model._run_tesseract(str(path), None)
    assert model.raw_passes[0]["status"] == "failed"
    assert model.raw_capture_issues == [{"code": "recognition_raw_capture_failed"}]


def test_digital_text_support_is_separate_from_ocr_and_requires_exact_alignment():
    import hashlib

    from document_files.document_model.recognition_sources import (
        import_source_observations,
        raw_pass_fingerprint,
    )

    for variation in ("digital", "ocr_label", "wrong_geometry", "wrong_text"):
        doc, payload, source, ids = raw_ledger_fixture()
        capture = payload["rawOCRPasses"][0]
        capture["tsv"] = capture["tsv"].splitlines()[0] + "\n"
        capture["tsvSha256"] = hashlib.sha256(capture["tsv"].encode()).hexdigest()
        capture["detections"] = []
        capture["fingerprint"] = raw_pass_fingerprint(capture)
        payload["rawCapturePages"][0]["passFingerprints"] = [capture["fingerprint"]]
        cell = {
            "page_no": 1,
            "text": "001.20",
            "raw": "001.20",
            "fromOcr": False,
            "sourceKind": "pdf_text",
            "bbox": {"l": 10, "t": 20, "r": 40, "b": 30, "coord_origin": "TOPLEFT"},
        }
        if variation == "ocr_label":
            cell.update(fromOcr=True, sourceKind="ocr")
        elif variation == "wrong_geometry":
            cell["bbox"].update(l=100, r=130)
        elif variation == "wrong_text":
            cell.update(text="001.21", raw="001.21")
        payload["cells"] = [cell]
        import_source_observations(doc, payload, source, ids, prefix="page1")
        ledger = doc.provenance["recognitionProcessingLedgers"][0]
        assert ledger["allRawOCRDetectionsPreserved"] is True
        assert ledger["entries"] == []
        if variation == "digital":
            assert ledger["unsupportedStructuralText"] == []
            assert ledger["digitalTextSupport"] == [
                {
                    "sourceRef": "page1:source:0",
                    "targetRef": ids[0],
                    "targetStart": 0,
                    "targetEnd": 6,
                    "basis": "exact_pdf_text_observation_and_geometry",
                    "ocrEvidence": False,
                }
            ]
            assert ledger["observedProcessingCoverage"] == "complete"
        else:
            assert ledger["unsupportedStructuralText"]
            assert ledger["digitalTextSupport"] == []
            assert ledger["observedProcessingCoverage"] == "partial"
        assert ledger["ocrTruthVerified"] is False


def full_page_render_fixture(*, rotation=0):
    import hashlib
    import io

    import pypdfium2 as pdfium
    from reportlab.pdfgen import canvas

    from document_files.document_model.recognition_worker import capture_full_page_render

    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(120, 80))
    pdf.setPageRotation(rotation)
    pdf.drawString(10, 20, "Visible")
    pdf.save()
    content = stream.getvalue()
    source_hash = hashlib.sha256(content).hexdigest()
    document = pdfium.PdfDocument(content)
    try:
        document.init_forms()
        record = capture_full_page_render(document, 0, source_hash)
    finally:
        document.close()
    return content, record


def test_whole_page_capture_hashes_actual_full_render_and_keeps_rotation():
    import hashlib

    import pypdfium2 as pdfium

    for rotation in (0, 90, 180, 270):
        content, record = full_page_render_fixture(rotation=rotation)
        assert record["status"] == "captured"
        assert record["sourceSha256"] == hashlib.sha256(content).hexdigest()
        assert record["page_no"] == 1 and record["intrinsicRotation"] == rotation
        document = pdfium.PdfDocument(content)
        try:
            document.init_forms()
            page = document[0]
            bitmap = page.render(
                scale=3,
                rotation=0,
                crop=(0, 0, 0, 0),
                fill_color=(255, 255, 255, 255),
                draw_annots=True,
                may_draw_forms=True,
            )
            image = bitmap.to_pil().convert("RGB")
            try:
                assert record["pixelSha256"] == hashlib.sha256(image.tobytes()).hexdigest()
                assert record["processedPixelBounds"] == [0, 0, *image.size]
            finally:
                image.close()
                bitmap.close()
                page.close()
        finally:
            document.close()
        assert record["visualContentCoverage"] == "not_assessed"
        assert record["coordinateAlignmentToRecognition"] == "not_verified"
        assert record["ocrTruthVerified"] is False
        coordinates = record["renderCoordinates"]
        assert coordinates["status"] == "verified"
        assert coordinates["scope"] == "full_render_to_source_page_only"
        assert coordinates["recognitionAlignmentVerified"] is False
        assert coordinates["pageContentCompletenessVerified"] is False
        assert coordinates["ocrTruthVerified"] is False
        assert len(coordinates["samples"]) == 9
        assert all(s["pixel"] == s["returnedPixel"] for s in coordinates["samples"])


def test_render_coordinates_use_actual_cropped_page_origin_and_pixel_rounding():
    import hashlib
    import io

    import pypdfium2 as pdfium

    from document_files.document_model.recognition_worker import capture_full_page_render

    for rotation in (0, 90, 180, 270):
        document = pdfium.PdfDocument.new()
        page = document.new_page(120, 80)
        page.set_cropbox(10.25, 15.5, 99.75, 70.25)
        page.set_rotation(rotation)
        page.close()
        stream = io.BytesIO()
        document.save(stream)
        document.close()
        content = stream.getvalue()
        document = pdfium.PdfDocument(content)
        try:
            document.init_forms()
            record = capture_full_page_render(
                document, 0, hashlib.sha256(content).hexdigest(), scale=1.37
            )
        finally:
            document.close()
        assert record["status"] == "captured"
        coordinates = record["renderCoordinates"]
        assert coordinates["status"] == "verified"
        assert all(
            abs(actual - expected) < 0.001
            for actual, expected in zip(
                coordinates["mappedPageBounds"], [10.25, 15.5, 99.75, 70.25], strict=True
            )
        )
        a, b, c, d, e, f = coordinates["pageToPixelAffine"]
        for sample in coordinates["samples"]:
            x, y = sample["page"]
            projected = [a * x + c * y + e, b * x + d * y + f]
            assert all(
                abs(p - actual) <= coordinates["pixelAffineTolerance"]
                for p, actual in zip(projected, sample["pixel"], strict=True)
            )


def test_render_coordinate_failure_keeps_pixels_but_does_not_verify_capture(monkeypatch):
    import pypdfium2 as pdfium

    original = pdfium.PdfBitmap.get_posconv

    class WrongRoundTrip:
        def __init__(self, converter):
            self.converter = converter
            self.pos_args = converter.pos_args

        def to_page(self, x, y):
            return self.converter.to_page(x, y)

        def to_bitmap(self, x, y):
            px, py = self.converter.to_bitmap(x, y)
            return px + 1, py

    monkeypatch.setattr(
        pdfium.PdfBitmap, "get_posconv", lambda self, page: WrongRoundTrip(original(self, page))
    )
    _, record = full_page_render_fixture()
    assert record["status"] == "failed"
    assert record["pixelSha256"] and record["processedPixelBounds"]
    assert record["renderCoordinates"]["status"] == "failed"
    assert record["issues"] == [{"code": "recognition_render_coordinates_unverified"}]
    assert record["coordinateAlignmentToRecognition"] == "not_verified"


def test_render_coordinate_failure_prevents_completed_page_checkpoint(monkeypatch):
    from document_files.document_model import recognition_worker

    content, _ = full_page_render_fixture()
    monkeypatch.setattr(
        recognition_worker,
        "_render_coordinate_evidence",
        lambda page, bitmap: {"status": "failed", "failure": "synthetic_conversion_failure"},
    )
    frames = []
    result = recognition_worker.page_batches(
        content,
        RecognitionConfig("/m", "/t", "/d"),
        lambda page: ({"pages": {"1": {}}, "texts": []}, "complete"),
        on_page=frames.append,
    )
    assert result["status"] == "partial"
    assert result["completedPages"] == []
    assert any(i["code"] == "recognition_render_coordinates_unverified" for i in result["issues"])
    assert frames and frames[0]["status"] == "partial"


def test_whole_page_capture_budget_failure_is_preserved_without_allocating_pixels():
    import hashlib

    import pypdfium2 as pdfium

    from document_files.document_model.recognition_worker import capture_full_page_render

    content, _ = full_page_render_fixture()
    document = pdfium.PdfDocument(content)
    try:
        document.init_forms()
        record = capture_full_page_render(
            document, 0, hashlib.sha256(content).hexdigest(), max_pixels=1
        )
    finally:
        document.close()
    assert record["status"] == "failed"
    assert record["issues"] == [{"code": "recognition_page_render_pixel_budget_exceeded"}]
    assert "pixelSha256" not in record and "processedPixelBounds" not in record
    assert record["visualContentCoverage"] == "not_assessed"


def test_page_capture_import_rejects_mismatch_and_keeps_original_evidence():
    from document_files.document_model.pdf import import_page_render
    from document_files.document_model.recognition_sources import page_render_fingerprint

    _, capture = full_page_render_fixture()
    for variation in (
        "valid",
        "wrong_page",
        "wrong_source",
        "partial_bounds",
        "tampered",
        "coordinate_tampered",
    ):
        record = deepcopy(capture)
        if variation == "wrong_page":
            record["page_no"] = 2
        elif variation == "wrong_source":
            record["sourceSha256"] = "0" * 64
        elif variation == "partial_bounds":
            record["processedPixelBounds"][0] = 10
        if variation == "coordinate_tampered":
            record["renderCoordinates"]["pixelToPageAffine"][4] += 10
        elif variation != "tampered":
            record["fingerprint"] = page_render_fingerprint(record)
        else:
            record["pixelSha256"] = "0" * 64
        before = deepcopy(record)
        doc = ObservationDocument()
        import_page_render(doc, record, source_hash=capture["sourceSha256"], page=1)
        evidence = doc.provenance["pdfPageRenderCaptures"][0]
        assert record == before and evidence["capture"] == before
        assert evidence["bindingStatus"] == (
            "source_page_matched" if variation == "valid" else "invalid"
        )
        assert evidence["contentCoverageVerified"] is False
        if variation != "valid":
            assert doc.issues[0]["code"] == "recognition_page_render_unverified"


def test_full_page_render_does_not_resolve_native_or_content_coverage_issues(monkeypatch):
    from document_files.document_model import pdf

    content, record = full_page_render_fixture()

    def native(doc, content):
        doc.provenance["pdfium"] = {"pageCount": 1}
        doc.issue("pdf_page_has_no_native_text", page=1)
        return []

    monkeypatch.setattr(pdf, "_native_pdf", native)

    class Recognition:
        def observe(self, content):
            return {
                "status": "complete",
                "pageResults": [
                    {
                        "page": 1,
                        "sourceSha256": record["sourceSha256"],
                        "status": "complete",
                        "pageRender": record,
                        "document": {"pages": {"1": {}}, "texts": [], "tables": []},
                    }
                ],
            }

    doc = ObservationDocument()
    pdf.observe_pdf(doc, content, recognition=Recognition())
    evidence = doc.provenance["pdfObservationChannels"]
    assert evidence["fullDisplayedPageCapture"] == "captured"
    assert evidence["visualContentCoverage"] == "not_assessed"
    dispositions = evidence["issueDispositions"]
    original = next(d for d in dispositions if d["issue"]["code"] == "pdf_page_has_no_native_text")
    assert original["channel"] == "native_pdf" and original["status"] == "unresolved"
    assert original["resolutionEvidence"] == []
    assert all(d["issue"] in doc.issues for d in dispositions)
    assert any(i["code"] == "recognition_content_completeness_unverified" for i in doc.issues)
    assert doc.coverage["recognitionContentCompleteness"] == "unverified"


def test_render_budget_failure_cannot_be_checkpointed_as_complete_conversion(monkeypatch):
    from document_files.document_model import recognition_worker

    content, _ = full_page_render_fixture()
    capture = recognition_worker.capture_full_page_render
    monkeypatch.setattr(
        recognition_worker, "capture_full_page_render", lambda *a: capture(*a, max_pixels=1)
    )
    result = recognition_worker.page_batches(
        content,
        RecognitionConfig("/models", "/ocr", "/data"),
        lambda data: ({"pages": {"1": {}}, "texts": []}, "complete"),
    )
    assert result["status"] == "partial" and result["completedPages"] == []
    page = result["pageResults"][0]
    assert page["status"] == "partial" and page["pageRender"]["status"] == "failed"
    assert page["pageRender"]["visualContentCoverage"] == "not_assessed"
    assert any(
        i["code"] == "recognition_page_render_pixel_budget_exceeded" for i in result["issues"]
    )


def coordinate_parser_fixture(rotation=0, *, cropped=True):
    """Real pinned parsers over synthetic geometry; not OCR quality evidence."""
    import hashlib
    import io

    import pypdfium2 as pdfium

    parser_module = pytest.importorskip("docling_parse.pdf_parser")
    from reportlab.pdfgen import canvas

    from document_files.document_model.recognition_coordinates import subset_mapping
    from document_files.document_model.recognition_worker import capture_full_page_render

    drawing = io.BytesIO()
    pdf = canvas.Canvas(drawing, pagesize=(120, 80))
    pdf.rect(30, 55, 4, 6, stroke=0, fill=1)
    pdf.rect(60, 42, 8, 3, stroke=0, fill=1)
    pdf.save()
    original = pdfium.PdfDocument(drawing.getvalue())
    page = original[0]
    if cropped:
        page.set_cropbox(10.25, 15.5, 99.75, 70.25)
    page.set_rotation(rotation)
    page.close()
    content = io.BytesIO()
    original.save(content)
    original.init_forms()
    source = capture_full_page_render(original, 0, hashlib.sha256(content.getvalue()).hexdigest())
    subset = pdfium.PdfDocument.new()
    subset.import_pages(original, pages=[0])
    serialized = io.BytesIO()
    subset.save(serialized)
    subset.close()
    original.close()
    data = serialized.getvalue()
    with pdfium.PdfDocument(data) as reopened:
        reopened.init_forms()
        captured = capture_full_page_render(reopened, 0, hashlib.sha256(data).hexdigest())
    render_config = parser_module.RenderConfig()
    render_config.scale = 2.0
    parser = parser_module.DoclingThreadedPdfParser(
        parser_config=parser_module.ThreadedPdfParserConfig(render_config=render_config)
    )
    parser.load(io.BytesIO(data))
    results = list(parser.iterate_results())
    assert len(results) == 1
    return parser, results[0], source, subset_mapping(source, captured)


def coordinate_ocr_capture(result, mapping, *, orientation=0, origin="TOPLEFT", scale=1.37):
    from types import SimpleNamespace

    from docling_core.types.doc import BoundingBox, CoordOrigin

    from document_files.document_model.recognition_coordinates import (
        bind_ocr_frame,
        capture_framework_crops,
        image_identity,
    )
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    class Observer:
        raw_coordinate_image = None
        raw_pixel_frame = None

        def _release_coordinate_image(self):
            if self.raw_coordinate_image is not None:
                self.raw_coordinate_image.close()
            self.raw_coordinate_image = self.raw_pixel_frame = None

    observer = Observer()
    page = SimpleNamespace(_backend=SimpleNamespace(_result=result))
    request = BoundingBox(l=1.17, t=2.31, r=31.49, b=27.18, coord_origin=CoordOrigin.TOPLEFT)
    if origin == "BOTTOMLEFT":
        request = request.to_bottom_left_origin(page_height=result.page_height)
    with capture_framework_crops(page, observer):
        cropped = result.get_image(scale=scale, cropbox=request).convert("RGB")
        transformed = cropped.rotate(-orientation, expand=True)
        identity = image_identity(transformed)
        frame = bind_ocr_frame(
            observer.raw_pixel_frame, observer.raw_coordinate_image, identity, orientation
        )
        transformed.close()
        cropped.close()
    assert observer.raw_coordinate_image is None
    assert "_crop_image" not in result.__dict__
    capture = {
        "passId": "pass-0",
        "page_no": mapping["originalPageNumber"],
        "batch_page_no": 1,
        "sourcePass": "page_ocr",
        "pixelFrame": frame,
        "image": identity,
        "transform": {"orientation": orientation},
        "detections": [],
    }
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    return capture


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("orientation", [0, 90, 180, 270])
def test_actual_framework_crop_and_rotated_input_link_to_original_pdf(rotation, orientation):
    from document_files.document_model.recognition_coordinates import coordinate_links

    parser, result, source, mapping = coordinate_parser_fixture(rotation)
    assert mapping["status"] == "verified"
    capture = coordinate_ocr_capture(result, mapping, orientation=orientation, origin="BOTTOMLEFT")
    frame = capture["pixelFrame"]
    link = coordinate_links(mapping, [capture])[0]
    assert link["status"] == "verified"
    assert link["ocrTruthVerified"] is False and link["contentCoverageVerified"] is False
    # Check measured source matrix independently at an interior input point. The
    # requested crop origin is NOT used: its actual rounded pixel origin is.
    px, py = 3, 4
    width, height = frame["cropImage"]["size"]
    unrotated = {
        0: (px, py),
        90: (py, height - px),
        180: (width - px, height - py),
        270: (width - py, px),
    }[orientation]
    x0, y0, _, _ = frame["cropPixelBounds"]
    cw, ch = frame["canvas"]["size"]
    x = (x0 + unrotated[0]) * source["pixelSize"][0] / cw
    y = (y0 + unrotated[1]) * source["pixelSize"][1] / ch
    a, b, c, d, e, f = source["renderCoordinates"]["pixelToPageAffine"]
    expected = [a * x + c * y + e, b * x + d * y + f]
    a, b, c, d, e, f = link["inputPixelToOriginalPageAffine"]
    assert [a * px + c * py + e, b * px + d * py + f] == pytest.approx(expected)
    assert frame["cropPixelBounds"][0] != frame["requestedCropTopLeft"][0] * cw / result.page_width
    del parser


def test_coordinate_import_rejects_page_source_fingerprint_and_crop_mismatch():
    from copy import deepcopy

    from document_files.document_model.recognition_coordinates import coordinate_links
    from document_files.document_model.recognition_sources import (
        import_coordinate_evidence,
        raw_pass_fingerprint,
    )

    parser, result, source, mapping = coordinate_parser_fixture()
    capture = coordinate_ocr_capture(result, mapping)
    evidence = {"mapping": mapping, "rawPassLinks": coordinate_links(mapping, [capture])}
    for change in ("none", "source", "page", "fingerprint", "bbox", "origin", "size", "pixels"):
        item = deepcopy(capture)
        if change == "source":
            item["pixelFrame"]["documentKey"] = "key=" + "0" * 64
        if change == "page":
            item["pixelFrame"]["localPageNumber"] = 2
        if change == "fingerprint":
            item["fingerprint"] = "0" * 64
        if change == "bbox":
            item["pixelFrame"]["cropPixelBounds"][0] += 1
        if change == "origin":
            item["pixelFrame"]["normalizedCropBox"][0] += 1
        if change == "size":
            item["pixelFrame"]["canvas"]["size"][0] += 1
        if change == "pixels":
            item["image"]["sha256"] = "0" * 64
        if change != "fingerprint":
            item["fingerprint"] = raw_pass_fingerprint(item)
        link = coordinate_links(mapping, [item])[0]
        assert (link["status"] == "verified") == (change == "none")
        doc = ObservationDocument()
        import_coordinate_evidence(
            doc,
            evidence,
            source,
            {"rawOCRPasses": [item]},
            source_hash=source["sourceSha256"],
            page=1,
        )
        assert (doc.provenance["recognitionCoordinateEvidence"][0]["status"] == "verified") == (
            change == "none"
        )
    del parser


def test_cell_crop_padding_transform_requires_exact_repair_unit_membership():
    from PIL import ImageOps

    from document_files.document_model.recognition_coordinates import (
        coordinate_links,
        framework_frame,
        image_identity,
    )
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    parser, result, _, mapping = coordinate_parser_fixture(90)
    canvas = result.get_image(scale=4.17).convert("RGB")
    frame = framework_frame(result, canvas)
    tx, ty, tr, tb = 10, 11, canvas.width - 12, canvas.height - 13
    crop = canvas.crop((tx, ty, tr, tb))
    cell_box = [15, 16, 55, 59]
    ink_box = [2, 3, 19, 27]
    padding = 10
    offset = [cell_box[0] + ink_box[0] - padding, cell_box[1] + ink_box[1] - padding]
    unit_image = ImageOps.expand(crop.crop(cell_box).crop(ink_box), border=padding, fill="white")
    # Geometry-contract fixture, not a claim that ink/blank classification is true.
    unit = {
        "cellPixelBox": cell_box,
        "inkCrop": ink_box,
        "padding": padding,
        "pixelOffset": offset,
        "fingerprint": "unit-fixture",
    }
    repair = {
        "policy": "ruled_cells_v2",
        "page_no": 1,
        "units": [unit],
        "sourcePixelsSha256": image_identity(crop)["sha256"],
        "transform": {"cropPixelOrigin": [tx, ty], "canvasPixels": list(canvas.size)},
    }
    frame.update(
        status="input_pixels_matched",
        inputImage=image_identity(unit_image),
        processing="ruled_cell_crop_and_white_padding",
        tableCropPixelBounds=[tx, ty, tr, tb],
        tableCropImage=image_identity(crop),
        cellPixelBox=cell_box,
        unitPixelOffset=offset,
        unitPixelOrigin=[tx + offset[0], ty + offset[1]],
        unitFingerprint=unit["fingerprint"],
        appliedClockwiseRotation=0,
    )
    capture = {
        "passId": "pass-0",
        "page_no": 1,
        "batch_page_no": 1,
        "sourcePass": "table_repair",
        "pixelFrame": frame,
        "image": image_identity(unit_image),
        "transform": {
            "repairIndex": 0,
            "cellUnitIndex": 0,
            "pixelOrigin": frame["unitPixelOrigin"],
        },
    }
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    link = coordinate_links(mapping, [capture], [repair])[0]
    assert link["status"] == "verified"
    assert link["sourceSupportedInputPixelBounds"] == [10, 10, 27, 34]
    assert link["syntheticPaddingIsSourceContent"] is False
    for field in ("unit", "origin", "pixels", "page", "missing"):
        changed = deepcopy(repair)
        if field == "unit":
            changed["units"][0]["fingerprint"] = "wrong"
        if field == "origin":
            changed["transform"]["cropPixelOrigin"][0] += 1
        if field == "pixels":
            changed["sourcePixelsSha256"] = "0" * 64
        if field == "page":
            changed["page_no"] = 2
        assert (
            coordinate_links(mapping, [capture], [] if field == "missing" else [changed])[0][
                "status"
            ]
            == "unverified"
        )
    # Explicit input page 2 is not PDF subset page 2. Independent image
    # membership retains its own crop origin and original PDF transform.
    import hashlib

    from document_files.document_model.recognition_batches import BATCH_VERSION, run_fingerprint

    second_unit = deepcopy(unit)
    second_unit["cellPixelBox"] = [v + 60 if i in (0, 2) else v for i, v in enumerate(cell_box)]
    second_unit["pixelOffset"][0] += 60
    second_unit["fingerprint"] = "unit-fixture-second"
    second_image = ImageOps.expand(
        crop.crop(second_unit["cellPixelBox"]).crop(ink_box), border=padding, fill="white"
    )
    repair["units"].append(second_unit)
    captures = [deepcopy(capture), deepcopy(capture)]
    for index, current in enumerate(captures):
        current.update(
            passId=f"pass-{index}",
            status="complete",
            tsvInputPageNumber=index + 1,
            unitFingerprint=repair["units"][index]["fingerprint"],
            detections=[],
        )
    second = captures[1]
    second["image"] = image_identity(second_image)
    second["transform"]["cellUnitIndex"] = 1
    second["transform"]["pixelOrigin"][0] += 60
    second["pixelFrame"].update(
        inputImage=second["image"],
        cellPixelBox=second_unit["cellPixelBox"],
        unitPixelOffset=second_unit["pixelOffset"],
        unitPixelOrigin=second["transform"]["pixelOrigin"],
        unitFingerprint=second_unit["fingerprint"],
    )
    raw = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "1\t1\t0\t0\t0\t0\t0\t0\t37\t44\t-1\t\n"
        "1\t2\t0\t0\t0\t0\t0\t0\t37\t44\t-1\t\n"
    )
    run = {
        "version": BATCH_VERSION,
        "page_no": 1,
        "status": "complete",
        "exitCode": 0,
        "timedOut": False,
        "processStarted": True,
        "outputTruncated": False,
        "psm": 7,
        "languages": ["kor", "eng"],
        "tsv": raw,
        "tsvSha256": hashlib.sha256(raw.encode()).hexdigest(),
        "inputs": [
            {
                k: deepcopy(c[k])
                for k in (
                    "tsvInputPageNumber",
                    "image",
                    "transform",
                    "pixelFrame",
                    "unitFingerprint",
                )
            }
            for c in captures
        ],
    }
    for item in run["inputs"]:
        item["localPdfPageNumber"] = 1
    run["fingerprint"] = run_fingerprint(run)
    for current in captures:
        current["runFingerprint"] = run["fingerprint"]
        current["fingerprint"] = raw_pass_fingerprint(current)
    linked = coordinate_links(mapping, captures, [repair], [run])
    assert [item["status"] for item in linked] == ["verified", "verified"]
    assert (
        linked[0]["inputPixelToOriginalPageAffine"] != linked[1]["inputPixelToOriginalPageAffine"]
    )
    assert second["batch_page_no"] == 1 and second["tsvInputPageNumber"] == 2
    assert all(
        item["status"] == "unverified" for item in coordinate_links(mapping, captures, [repair])
    )
    second["tsvInputPageNumber"] = 1
    second["fingerprint"] = raw_pass_fingerprint(second)
    assert all(
        item["status"] == "unverified"
        for item in coordinate_links(mapping, captures, [repair], [run])
    )
    second_image.close()
    for image in (unit_image, crop, canvas):
        image.close()
    del parser


def test_crop_observer_restores_instance_and_releases_pixels_after_exception():
    from types import SimpleNamespace

    from document_files.document_model.recognition_coordinates import capture_framework_crops

    parser, result, _, _ = coordinate_parser_fixture()
    released = []
    observer = SimpleNamespace(_release_coordinate_image=lambda: released.append(True))
    page = SimpleNamespace(_backend=SimpleNamespace(_result=result))
    with pytest.raises(RuntimeError, match="cancelled"), capture_framework_crops(page, observer):
        assert "_crop_image" in result.__dict__
        raise RuntimeError("cancelled")
    assert "_crop_image" not in result.__dict__ and released == [True]
    del parser


def test_invalid_single_page_locator_is_preserved_not_silently_remapped():
    from document_files.document_model.recognition_worker import page_batches

    content, _ = full_page_render_fixture()
    invalid = {"pages": {"1": {"page_no": 1}, "2": {"page_no": 2}}, "texts": [], "tables": []}
    result = page_batches(
        content,
        RecognitionConfig("/unused", "/unused", "/unused"),
        lambda _: (deepcopy(invalid), "complete"),
    )
    page = result["pageResults"][0]
    assert page["document"] == invalid
    assert page["localPageMappingValid"] is False
    assert page["coordinateEvidence"]["mapping"]["status"] == "unverified"
    assert page["status"] == "partial" and result["completedPages"] == []
    assert any(i["code"] == "recognition_local_page_mapping_invalid" for i in result["issues"])


def malformed_coordinate_dimensions_fixture():
    """Malformed JSON contract only; no OCR or parser execution is involved."""
    from document_files.document_model.recognition_coordinates import VERSION, subset_mapping
    from document_files.document_model.recognition_sources import (
        page_render_fingerprint,
        raw_pass_fingerprint,
    )

    render = {
        "sourceSha256": "a" * 64,
        "page_no": 1,
        "status": "captured",
        "profile": {},
        "pixelSize": [120, 80],
        "pixelSha256": "b" * 64,
        "intrinsicRotation": 0,
        "pageBoxes": {
            "mediaDeclared": [0, 0, 120, 80],
            "cropDeclared": [0, 0, 120, 80],
            "effective": [0, 0, 120, 80],
        },
        "pageSizeCanvasUnits": [0, 80],
        "renderCoordinates": {"pixelToPageAffine": [1, 0, 0, -1, 0, 80]},
    }
    render["fingerprint"] = page_render_fingerprint(render)
    subset = deepcopy(render)
    subset["sourceSha256"] = "c" * 64
    subset["fingerprint"] = page_render_fingerprint(subset)
    mapping = subset_mapping(render, subset)
    identity = {"sha256": "d" * 64, "size": [1, 1], "mode": "RGB"}
    frame = {
        "version": VERSION,
        "backend": "ThreadedDoclingParsePageBackend",
        "documentKey": "key=" + subset["sourceSha256"],
        "localPageNumber": 1,
        "boundaryType": "crop_box",
        "normalizedAngle": 0,
        "normalizedMediaBox": [0, 0, 120, 80],
        "normalizedCropBox": [0, 0, 120, 80],
        "pageSize": [0, 80],
        "canvas": {"size": [120, 80]},
        "pixelCoordinateOrigin": "TOPLEFT",
        "requestedCropTopLeft": [1, 1, 2, 2],
        "cropPixelBounds": [1, 1, 2, 2],
        "rounding": "python_round_then_clamp",
        "status": "input_pixels_matched",
        "inputImage": identity,
    }
    capture = {"page_no": 1, "pixelFrame": frame, "image": identity}
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    return render, {"mapping": mapping, "rawPassLinks": []}, {"rawOCRPasses": [capture]}


def test_malformed_zero_page_dimension_preserves_unverified_evidence_and_existing_issue():
    from document_files.document_model.recognition_sources import import_coordinate_evidence

    render, evidence, payload = malformed_coordinate_dimensions_fixture()
    supplied = deepcopy((render, evidence, payload))
    doc = ObservationDocument()
    doc.issue("recognition_content_completeness_unverified")
    original_issue = deepcopy(doc.issues[0])
    # Before the guard this escaped as ZeroDivisionError, with no saved evidence.
    import_coordinate_evidence(doc, evidence, render, payload, source_hash="a" * 64, page=1)
    saved = doc.provenance["recognitionCoordinateEvidence"][0]
    assert saved["status"] == "unverified" and saved["evidence"] == evidence
    assert saved["validationErrorType"] == "ValueError"
    assert doc.issues[0] == original_issue
    assert doc.issues[-1]["code"] == "recognition_source_coordinates_unverified"
    assert (render, evidence, payload) == supplied


def test_coordinate_crop_denominators_require_finite_positive_page_dimensions():
    from document_files.document_model.recognition_coordinates import _frame_to_page

    _, evidence, payload = malformed_coordinate_dimensions_fixture()
    for axis in (0, 1):
        for value in (0, -1, True, float("inf"), float("nan")):
            frame = deepcopy(payload["rawOCRPasses"][0]["pixelFrame"])
            frame["pageSize"] = [120, 80]
            frame["pageSize"][axis] = value
            with pytest.raises(ValueError, match="invalid framework page dimensions"):
                _frame_to_page(frame, evidence["mapping"])


def test_coordinate_import_boundary_preserves_unexpected_division_failure(monkeypatch):
    from document_files.document_model import recognition_coordinates
    from document_files.document_model.recognition_sources import import_coordinate_evidence

    render, evidence, payload = malformed_coordinate_dimensions_fixture()

    def fail(*_args):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setattr(recognition_coordinates, "coordinate_links", fail)
    doc = ObservationDocument()
    import_coordinate_evidence(doc, evidence, render, payload, source_hash="a" * 64, page=1)
    saved = doc.provenance["recognitionCoordinateEvidence"][0]
    assert saved["status"] == "unverified" and saved["evidence"] == evidence
    assert saved["validationErrorType"] == "ZeroDivisionError"
    assert doc.issues[-1]["code"] == "recognition_source_coordinates_unverified"


def visual_fixture(image, **limits):
    pytest.importorskip("cv2")
    from document_files.document_model.recognition_visual import observe_rgb, visual_profile

    return observe_rgb(
        image,
        source_sha="a" * 64,
        page=1,
        render_profile={"visualObservation": visual_profile(**limits)},
    )


def test_full_visual_inventory_keeps_faint_margin_pixels_figures_and_edge_contact():
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (40, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 8, 29, 22), fill=(10, 20, 30))  # unclassified figure-sized area
    draw.rectangle((0, 3, 2, 6), fill="black")  # boundary contact: possible clipping
    draw.point((36, 27), fill=(255, 254, 255))  # faint, outside the figure/OCR-like area
    draw.point((35, 1), fill="black")  # a margin mark cannot be cropped away
    record = visual_fixture(image)
    assert record["status"] == "captured" and record["foregroundPixelCount"] == 314
    assert record["lowContrastPixelCount"] == 1 and record["foregroundPixelsDiscarded"] == 0
    components = {tuple(c["pixelBounds"]): c for c in record["components"]}
    assert components[(36, 27, 37, 28)]["lowContrastPixelCount"] == 1
    assert components[(0, 3, 3, 7)]["touchesPageEdges"] == ["left"]
    assert components[(10, 8, 30, 23)]["largeExtentOrDenseRegion"] is True
    assert components[(35, 1, 36, 2)]["contentKind"] == "unclassified"
    assert record["ocrBBoxesUsed"] is False and record["contentCoverageVerified"] is False
    assert record["readingOrderVerified"] is False and record["blankValueProven"] is False
    image.close()


def test_visual_background_uncertainty_and_white_pixels_never_prove_blank():
    from PIL import Image

    for color, count in (((250, 250, 250), 100), ("white", 0)):
        image = Image.new("RGB", (10, 10), color)
        record = visual_fixture(image)
        assert record["foregroundPixelCount"] == record["lowContrastPixelCount"] == count
        assert record["backgroundClassification"] == "unverified"
        assert record["blankValueProven"] is False and record["contentCoverageVerified"] is False
        if count:
            assert record["components"][0]["pixelBounds"] == [0, 0, 10, 10]
            assert record["components"][0]["largeExtentOrDenseRegion"] is True
        image.close()


def test_visual_pixel_run_and_component_budgets_are_explicit_not_empty_success():
    from PIL import Image

    image = Image.new("RGB", (10, 10), "white")
    for y in (1, 4, 7):
        for x in (1, 4, 7):
            image.putpixel((x, y), (0, 0, 0))
    record = visual_fixture(image, max_components=2)
    assert record["status"] == "truncated" and record["foregroundPixelCount"] == 9
    assert len(record["components"]) == 2 and record["omittedComponentCount"] == 7
    assert record["truncationReasons"] == ["component_budget_exceeded"]
    record = visual_fixture(image, max_runs=2)
    assert record["componentAnalyzedPixelBounds"] == [0, 0, 10, 1]
    assert record["foregroundPixelCount"] == 9 and record["components"] == []
    assert record["truncationReasons"] == ["foreground_run_budget_exceeded"]
    record = visual_fixture(image, max_pixels=50)
    assert record["pixelInventoryStatus"] == "not_run" and record["status"] == "truncated"
    assert record["truncationReasons"] == ["pixel_budget_exceeded"]
    assert "foregroundPixelCount" not in record and record["blankValueProven"] is False
    image.close()
    image = Image.new("RGB", (4, 6), "white")
    for y in range(6):
        image.putpixel((2, y), (0, 0, 0))
    record = visual_fixture(image, max_runs=2)
    assert record["foregroundPixelCount"] == 6 and record["analyzedForegroundPixelCount"] == 2
    assert record["components"][0]["touchesUnprocessedBoundary"] is True
    assert "bottom" not in record["components"][0]["touchesPageEdges"]
    image.close()


def test_visual_import_checks_source_page_render_hash_without_changing_existing_issues():
    pytest.importorskip("cv2")
    from document_files.document_model.pdf import import_page_render
    from document_files.document_model.recognition_sources import page_render_fingerprint
    from document_files.document_model.recognition_visual import digest

    _, render = full_page_render_fixture()
    for change in ("none", "source", "page", "rgb", "profile", "geometry", "counts"):
        candidate = deepcopy(render)
        visual = candidate["visualObservation"]
        if change == "source":
            visual["sourceSha256"] = "0" * 64
        if change == "page":
            visual["page"] = 2
        if change == "rgb":
            visual["rgbSha256"] = "0" * 64
        if change == "profile":
            visual["renderProfileSha256"] = "0" * 64
        if change == "geometry":
            visual["components"][0]["pixelBounds"][0] = -1
        if change == "counts":
            visual["foregroundPixelCount"] += 1
        visual["fingerprint"] = digest({k: v for k, v in visual.items() if k != "fingerprint"})
        candidate["fingerprint"] = page_render_fingerprint(candidate)
        doc = ObservationDocument()
        doc.issue("recognition_content_completeness_unverified")
        before = deepcopy(doc.issues)
        import_page_render(doc, candidate, source_hash=render["sourceSha256"], page=1)
        imported = doc.provenance["pdfPageVisualObservations"][0]
        assert (imported["bindingStatus"] == "source_page_render_matched") == (change == "none")
        assert imported["observation"] == visual and imported["contentCoverageVerified"] is False
        assert doc.issues == before and doc.coverage == {}


def visual_correspondence_fixture():
    """Actual rendered pixels plus synthetic source geometry, not OCR quality."""
    pytest.importorskip("cv2")
    from document_files.document_model.pdf import import_page_render

    _, render = full_page_render_fixture()
    doc = ObservationDocument()
    doc.provenance["sourceSha256"] = render["sourceSha256"]
    import_page_render(doc, render, source_hash=render["sourceSha256"], page=1)
    width, height = render["pageSizeCanvasUnits"]
    doc.nodes = {
        "pdf:page:1": {
            "text": "",
            "sourceStructure": {
                "page": 1,
                "width": width,
                "height": height,
                "rotation": 0,
                "bbox": [0, 0, width, height],
                "coordinateOrigin": "TOPLEFT",
            },
        },
        "native": {
            "text": "AB",
            "observationBasis": "native_pdf",
            "sourceStructure": {
                "page": 1,
                "characters": [
                    {
                        "text": char,
                        "x0": 0,
                        "top": 0,
                        "x1": width,
                        "bottom": height,
                        "upright": True,
                    }
                    for char in "AB"
                ],
            },
        },
        "structure": {
            "text": "AB",
            "observationBasis": "recognition",
            "sourceStructure": {"page": 1, "bbox": [0, 0, width, height]},
        },
    }
    doc.relations = [
        {
            "kind": "observationEquivalence",
            "targetRef": "structure",
            "comparison": "exact_characters",
            "resolution": "equivalent",
            "basis": "native_character_geometry",
            "sourceSegments": [
                {"sourceRef": "native", "characterIndex": i, "start": i, "end": i + 1}
                for i in range(2)
            ],
        }
    ]
    doc.issue("recognition_content_completeness_unverified")
    return doc, render


def test_visual_correspondences_are_two_way_candidates_not_assignments_or_model_payload():
    from document_files.document_model.recognition_visual import append_visual_correspondences
    from document_files.interpretation.regions import region_payload

    doc, _ = visual_correspondence_fixture()
    region = {"id": "region-1", "nodeIds": list(doc.nodes), "bindingIds": []}
    payload = region_payload(doc, region)
    unchanged = deepcopy((doc.nodes, doc.relations, doc.issues, doc.coverage, doc.provenance))
    result = append_visual_correspondences(doc, recognition_identity={"adapterVersion": "14"})[0]
    assert result["status"] == "captured_candidates"
    assert len(result["components"]) > 1
    assert all(c["status"] == "multiple_overlap_candidates" for c in result["components"])
    assert all(o["status"] == "multiple_component_candidates" for o in result["observations"])
    for edge in result["overlaps"]:
        ci, oi = edge["componentIndex"], edge["observationIndex"]
        assert ci in result["observations"][oi]["candidateComponentIndices"]
        assert oi in result["components"][ci]["candidateObservationIndices"]
    structure = result["structures"][0]
    assert structure["sourceProxyRangeStatus"] == "full_text_range"
    assert structure["reportedStructureBBoxUsed"] is False
    assert structure["visualAssignmentVerified"] is False
    assert all("structure" in c["candidateStructuralRefs"] for c in result["components"])
    for key in (
        "contentCoverageVerified",
        "ocrTruthVerified",
        "blankValueProven",
        "readingOrderVerified",
        "componentPixelIntersectionMeasured",
    ):
        assert result[key] is False
    assert (doc.nodes, doc.relations, doc.issues, doc.coverage) == unchanged[:4]
    assert {k: v for k, v in doc.provenance.items() if k != "visualCorrespondences"} == unchanged[4]
    assert region_payload(doc, region) == payload
    assert append_visual_correspondences(doc, recognition_identity={"adapterVersion": "14"}) == [
        result
    ]


def test_visual_correspondence_partial_overlap_unknown_geometry_and_proxy_mismatch():
    from document_files.document_model.recognition_visual import append_visual_correspondences

    doc, render = visual_correspondence_fixture()
    component = render["visualObservation"]["components"][0]["pixelBounds"]
    from document_files.document_model.recognition_visual import _project_box

    # Convert an actual half-component pixel rectangle into native TOPLEFT units.
    box = _project_box(
        [component[0], component[1], (component[0] + component[2]) / 2, component[3]],
        render["renderCoordinates"]["pixelToPageAffine"],
    )
    height = render["pageSizeCanvasUnits"][1]
    chars = doc.nodes["native"]["sourceStructure"]["characters"]
    chars[0].update(x0=box[0], x1=box[2], top=height - box[3], bottom=height - box[1])
    chars[1]["upright"] = False
    doc.nodes["structure"]["text"] = "not AB"
    result = append_visual_correspondences(doc)[0]
    assert result["status"] == "partial_candidates"
    assert result["geometryUnavailableObservationCount"] == 1
    assert any(edge["kind"] == "partial_bbox_overlap_candidate" for edge in result["overlaps"])
    assert result["observations"][1]["status"] == "geometry_unavailable"
    assert result["structures"][0]["sourceProxies"] == []
    assert result["structures"][0]["candidateComponentIndices"] == []
    assert all(c["comparisonIncomplete"] for c in result["components"])


@pytest.mark.parametrize(
    "limit", ["sourceItems", "observationsPerKind", "bboxComparisons", "overlapCandidates"]
)
def test_visual_correspondence_limits_keep_unexamined_candidates_explicit(limit):
    from document_files.document_model.recognition_visual import (
        CORRESPONDENCE_LIMITS,
        append_visual_correspondences,
    )

    doc, _ = visual_correspondence_fixture()
    limits = {**CORRESPONDENCE_LIMITS, limit: 1}
    result = append_visual_correspondences(doc, limits=limits)[0]
    assert result["status"] == "partial_candidates"
    assert result["truncationReasons"]
    assert all(c["comparisonIncomplete"] for c in result["components"])
    assert result["sourceItemsExamined"] <= limits["sourceItems"]
    assert result["bboxComparisons"] <= limits["bboxComparisons"]
    assert len(result["overlaps"]) <= limits["overlapCandidates"]
    assert result["contentCoverageVerified"] is False


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_visual_correspondence_raw_source_binding_and_target_range_rechecked(rotation):
    pytest.importorskip("cv2")
    from document_files.document_model.pdf import import_page_render
    from document_files.document_model.recognition_coordinates import coordinate_links
    from document_files.document_model.recognition_sources import raw_pass_fingerprint
    from document_files.document_model.recognition_visual import append_visual_correspondences

    parser, result, render, mapping = coordinate_parser_fixture(rotation)
    capture = coordinate_ocr_capture(result, mapping)
    width, height = capture["image"]["size"]
    capture["detections"] = [
        {
            "text": "A",
            "accepted": True,
            "imageBBox": {
                "left": 0,
                "top": 0,
                "width": width,
                "height": height,
            },
        }
    ]
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    links = coordinate_links(mapping, [capture])
    assert links[0]["status"] == "verified"
    doc = ObservationDocument()
    doc.provenance["sourceSha256"] = render["sourceSha256"]
    import_page_render(doc, render, source_hash=render["sourceSha256"], page=1)
    doc.provenance["recognitionCoordinateEvidence"] = [
        {
            "page": 1,
            "sourceSha256": render["sourceSha256"],
            "status": "verified",
            "evidence": {"mapping": mapping, "rawPassLinks": links},
        }
    ]
    ledger = {
        "batch": "docling:page:1",
        "allRawOCRDetectionsPreserved": True,
        "processingDependencies": {"pages": [1]},
        "rawOCRPasses": [capture],
        "entries": [
            {
                "rawRef": "docling:page:1:raw:0:0",
                "status": "structural_observation",
                "targetRefs": ["cell"],
                "targetStart": 0,
                "targetEnd": 1,
            }
        ],
    }
    doc.provenance["recognitionProcessingLedgers"] = [ledger]
    doc.nodes["cell"] = {
        "text": "AB",
        "observationBasis": "recognition",
        "sourceStructure": {"page": 1},
    }
    result = append_visual_correspondences(doc)[0]
    assert result["observations"][0]["geometryStatus"] == "available"
    assert result["observations"][0]["sourceSupportedBBoxFraction"] == 1
    assert result["structures"][0]["sourceProxyRangeStatus"] == "partial_text_range"
    assert result["coordinateRenderEquivalences"]
    original = deepcopy(doc)
    ledger["allRawOCRDetectionsPreserved"] = False
    partial_inventory = append_visual_correspondences(doc)[0]
    assert partial_inventory["observations"][0]["geometryStatus"] == "available"
    assert partial_inventory["observations"][0]["rawInventoryVerified"] is False
    assert partial_inventory["status"] == "partial_candidates"
    for change in ("page", "fingerprint", "unverified", "raw", "target"):
        changed = deepcopy(original)
        evidence = changed.provenance["recognitionCoordinateEvidence"][0]
        if change == "page":
            evidence["page"] = 2
        if change == "fingerprint":
            evidence["evidence"]["mapping"]["originalRenderFingerprint"] = "0" * 64
        if change == "unverified":
            evidence["status"] = "unverified"
        if change == "raw":
            changed.provenance["recognitionProcessingLedgers"][0]["rawOCRPasses"][0]["image"][
                "size"
            ][0] += 1
        if change == "target":
            changed.nodes["cell"]["text"] = "BC"
        result = append_visual_correspondences(changed)[0]
        if change == "target":
            assert result["structures"][0]["sourceProxies"] == []
        else:
            assert result["observations"][0]["geometryStatus"] == "unavailable"
            assert result["status"] == "partial_candidates"
        assert result["contentCoverageVerified"] is False


def ordered_source_fixture(variant="valid"):
    """Real parser coordinate capture with synthetic TSV; never quality evidence."""
    import hashlib

    from document_files.document_model.pdf import import_page_render
    from document_files.document_model.recognition_coordinates import coordinate_links
    from document_files.document_model.recognition_sources import raw_pass_fingerprint

    parser, parsed, render, mapping = coordinate_parser_fixture(cropped=False)
    capture = coordinate_ocr_capture(parsed, mapping, scale=3)
    cx, cy, cr, cb = capture["pixelFrame"]["cropPixelBounds"]
    capture["transform"].update(
        scale=3,
        crop={"l": cx / 3, "t": cy / 3, "r": cr / 3, "b": cb / 3, "coord_origin": "TOPLEFT"},
    )
    words = ["A", "이", "0", "이", "Z"]
    if variant in {"two_lines", "two_lines_normalized"}:
        words *= 2
    capture.update(status="complete", detections=[])
    cells = []
    columns = [
        "level",
        "page_num",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    ]
    rows = []
    for index, word in enumerate(words):
        left, top = 5 + index % 5 * 12, 10 + index // 5 * 20
        line_no, word_no = 1 + index // 5, index % 5 + 1
        if variant == "cross_line" and index >= 3:
            line_no, word_no = 2, index - 2
        if variant == "overlap" and index == 2:
            left = 19
        if variant == "vertical_disjoint" and index == 2:
            top = 35
        if variant == "unknown_word_order" and index == 2:
            word_no = 1
        row = dict(
            zip(
                columns,
                map(str, [5, 1, 1, 1, line_no, word_no, left, top, 6, 9, 90, word]),
                strict=True,
            )
        )
        rows.append("\t".join(row[k] for k in columns))
        pb = {
            "l": (cx + left) / 3,
            "t": (cy + top) / 3,
            "r": (cx + left + 6) / 3,
            "b": (cy + top + 9) / 3,
            "coord_origin": "TOPLEFT",
        }
        capture["detections"].append(
            {
                "ordinal": index,
                "text": word,
                "raw": row,
                "accepted": True,
                "confidence": 90.0,
                "imageBBox": {"left": left, "top": top, "width": 6, "height": 9},
                "pageBBox": pb,
                "mappingBasis": "synthetic_exact_coordinate_contract",
            }
        )
        cells.append(
            {
                "text": word,
                "raw": word,
                "sourceKind": "ocr",
                "fromOcr": True,
                "page_no": 1,
                "bbox": deepcopy(pb),
                "backendCellIndex": index,
            }
        )
    capture["tsv"] = "\t".join(columns) + "\n" + "\n".join(rows) + "\n"
    capture["tsvSha256"] = hashlib.sha256(capture["tsv"].encode()).hexdigest()
    capture["fingerprint"] = raw_pass_fingerprint(capture)
    if variant == "normalized_source":
        cells[1]["text"] = "I"
    if variant == "two_lines_normalized":
        cells[5]["text"] = "normalized"
    if variant == "source_conflict":
        cells[1]["candidateStatus"] = "unresolved_conflict"
    payload = {
        "version": "document-files.recognition-source-observations.v1",
        "cells": cells,
        "rawCaptureVersion": "document-files.raw-ocr.v1",
        "rawOCRPasses": [capture],
        "rawCapturePages": [
            {"page_no": 1, "captureAvailable": True, "passFingerprints": [capture["fingerprint"]]}
        ],
    }
    doc = ObservationDocument()
    doc.provenance["sourceSha256"] = render["sourceSha256"]
    import_page_render(doc, render, source_hash=render["sourceSha256"], page=1)
    links = coordinate_links(mapping, [capture])
    assert links[0]["status"] == "verified"
    doc.provenance["recognitionCoordinateEvidence"] = [
        {
            "page": 1,
            "sourceSha256": render["sourceSha256"],
            "status": "verified",
            "evidence": {"mapping": mapping, "rawPassLinks": links},
        }
    ]
    if variant == "unverified_coordinates":
        doc.provenance["recognitionCoordinateEvidence"][0]["status"] = "unverified"
    if variant == "wrong_source":
        doc.provenance["recognitionCoordinateEvidence"][0]["sourceSha256"] = "0" * 64
    bounds = {
        "left": (cx + 5) / 3,
        "top": (cy + 10) / 3,
        "right": (cx + 59) / 3,
        "bottom": (cy + (39 if variant in {"two_lines", "two_lines_normalized"} else 19)) / 3,
        "origin": "TOPLEFT",
    }
    if variant == "partial_containment":
        bounds["left"] += 0.1
    text = "A 이0 이 Z" if variant == "no_separator" else "A 이 0 이 Z"
    target = doc.node(
        "target",
        text,
        role="recognized_text",
        locator={"page": 1, "bbox": bounds},
        observationBasis="recognition",
        originalRecognitionText=text,
    )
    ids = [target]
    if variant == "normalized_target":
        doc.nodes[target]["text"] = "A I 0 I Z"
    if variant == "duplicate_target":
        ids.append(
            doc.node(
                "other",
                text,
                role="recognized_text",
                locator={"page": 1, "bbox": deepcopy(bounds)},
                observationBasis="recognition",
                originalRecognitionText=text,
            )
        )
    if variant == "table":
        doc.tables["table"] = {
            "id": "table",
            "cells": [{"sourceRef": target}],
            "page": 1,
            "locator": {"bbox": bounds},
            "declaredRowCount": 1,
            "declaredColCount": 1,
        }
    doc.issue("recognition_content_completeness_unverified")
    doc.issue("recognition_table_cells_unobserved", page=1, tableRef="unrelated_missing")
    return doc, payload, {"pages": {"1": {"size": {"height": 80}}}}, ids


def test_exact_single_raw_line_resolves_repeated_tokens_without_rewriting_or_global_completion():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, exported, ids = ordered_source_fixture()
    original = deepcopy((payload, doc.nodes, doc.issues, doc.coverage))
    assert import_source_observations(doc, payload, exported, ids, prefix="page1") == []
    alignment = doc.provenance["recognitionOrderedSourceAlignments"][0]
    assert [
        (e["sourceCellIndex"], e["targetStart"], e["targetEnd"]) for e in alignment["alignments"]
    ] == [(1, 2, 3), (3, 6, 7)]
    assert alignment["readingOrderVerified"] is False
    for index in (1, 3):
        assert (
            doc.nodes[f"page1:source:{index}"]["semanticInput"]["role"]
            == "source_overlap_not_independent"
        )
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["version"] == "document-files.observed-processing-ledger.v5"
    assert ledger["unsupportedStructuralText"] == []
    assert all(e["status"] == "structural_observation" for e in ledger["entries"])
    assert ledger["observedProcessingCoverage"] == "partial"  # actual other local failure remains
    assert (
        ledger["pageContentCompletenessVerified"] is False and ledger["ocrTruthVerified"] is False
    )
    assert payload == original[0] and doc.nodes["target"] == original[1]["target"]
    assert doc.issues[: len(original[2])] == original[2] and doc.coverage == original[3]
    assert not any(i["code"] == "recognition_unassigned_content" for i in doc.issues)


@pytest.mark.parametrize(
    "variant",
    [
        "two_lines",
        "two_lines_normalized",
        "no_separator",
        "cross_line",
        "overlap",
        "vertical_disjoint",
        "unknown_word_order",
        "normalized_source",
        "normalized_target",
        "source_conflict",
        "unverified_coordinates",
        "wrong_source",
        "partial_containment",
        "duplicate_target",
        "table",
    ],
)
def test_ordered_source_matching_never_guesses_ambiguous_partial_or_cross_cell_alignment(variant):
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, exported, ids = ordered_source_fixture(variant)
    before = deepcopy(payload)
    import_source_observations(doc, payload, exported, ids, prefix="page1")
    alignment = doc.provenance["recognitionOrderedSourceAlignments"][0]
    assert alignment["alignments"] == []
    if variant == "two_lines_normalized":
        assert alignment["ambiguousIncompleteLineTargets"] == ["target"]
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert ledger["observedProcessingCoverage"] == "partial"
    assert any(e["status"] == "unresolved" for e in ledger["entries"])
    assert payload == before and doc.coverage == {}


def test_ordered_source_alignment_budget_preserves_unresolved_candidates():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, exported, ids = ordered_source_fixture()
    for index in range(10):
        ref = f"long-target-{index}"
        doc.nodes[ref] = {
            **deepcopy(doc.nodes["target"]),
            "text": "x" * 8192,
            "originalRecognitionText": "x" * 8192,
        }
        ids.append(ref)
    before = deepcopy(payload)
    import_source_observations(doc, payload, exported, ids, prefix="page1")
    alignment = doc.provenance["recognitionOrderedSourceAlignments"][0]
    assert alignment["truncated"] is True and alignment["alignments"] == []
    assert alignment["comparisons"] <= alignment["maxComparisons"]
    assert payload == before
    assert (
        doc.provenance["recognitionProcessingLedgers"][0]["observedProcessingCoverage"] == "partial"
    )


def ordered_source_cross_table_fixture():
    """A non-cell target extends beyond a table and spans several of its cells."""
    doc, payload, exported, ids = ordered_source_fixture()
    first, last = payload["cells"][1]["bbox"], payload["cells"][3]["bbox"]
    bounds = {
        "left": first["l"],
        "top": first["t"],
        "right": first["r"],
        "bottom": first["b"],
        "origin": "TOPLEFT",
    }
    ids.append(doc.node("table-cell", "이", locator={"page": 1, "bbox": bounds}))
    doc.tables["table"] = {
        "id": "table",
        "cells": [{"sourceRef": "table-cell"}],
        "page": 1,
        "locator": {"bbox": {**bounds, "right": last["r"]}},
        "declaredRowCount": 1,
        "declaredColCount": 2,
    }
    return doc, payload, exported, ids


def test_ordered_noncell_target_overlapping_table_does_not_connect_across_cells():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, exported, ids = ordered_source_cross_table_fixture()
    import_source_observations(doc, payload, exported, ids, prefix="page1")
    alignment = doc.provenance["recognitionOrderedSourceAlignments"][0]
    assert alignment["version"] == "document-files.ordered-ocr-source.v2"
    assert alignment["alignments"] == []
    supports = [
        r
        for r in doc.relations
        if r.get("kind") == "recognitionSourceSupport" and r.get("sourceRef") == "page1:source:1"
    ]
    assert len(supports) == 1 and supports[0]["targetRef"] == "table-cell"
    assert supports[0]["basis"] == "exact_unique_text_and_geometry"
    assert "orderedAlignmentFingerprint" not in supports[0]
    assert doc.nodes["page1:source:3"]["semanticInput"]["role"] == "unresolved_conflict"


def test_table_preference_cannot_label_another_target_with_ordered_support(monkeypatch):
    from document_files.document_model import recognition_sources

    doc, payload, exported, ids = ordered_source_fixture()
    prior = recognition_sources._ordered_source_alignments(doc, payload, ids, prefix="page1")
    assert len(prior["alignments"]) == 2
    doc, payload, exported, ids = ordered_source_cross_table_fixture()
    # Exercise the downstream preference independently, with a precomputed
    # candidate from before the table was known. This is not source evidence.
    monkeypatch.setattr(
        recognition_sources, "_ordered_source_alignments", lambda *a, **kw: deepcopy(prior)
    )
    recognition_sources.import_source_observations(doc, payload, exported, ids, prefix="page1")
    support = next(
        r
        for r in doc.relations
        if r.get("kind") == "recognitionSourceSupport" and r.get("sourceRef") == "page1:source:1"
    )
    assert support["targetRef"] == "table-cell"
    assert support["basis"] == "exact_unique_text_and_geometry"
    assert "orderedAlignmentFingerprint" not in support and "rawRef" not in support
    ledger = doc.provenance["recognitionProcessingLedgers"][0]
    assert "orderedAlignmentFingerprint" not in ledger["entries"][1]


def test_ordered_target_touching_but_not_overlapping_table_keeps_line_alignment():
    from document_files.document_model.recognition_sources import import_source_observations

    doc, payload, exported, ids = ordered_source_fixture()
    bounds = deepcopy(doc.nodes["target"]["sourceStructure"]["bbox"])
    bounds["left"], bounds["right"] = bounds["right"], bounds["right"] + 10
    doc.tables["table"] = {
        "id": "table",
        "cells": [],
        "page": 1,
        "locator": {"bbox": bounds},
        "declaredRowCount": 1,
        "declaredColCount": 1,
    }
    import_source_observations(doc, payload, exported, ids, prefix="page1")
    assert len(doc.provenance["recognitionOrderedSourceAlignments"][0]["alignments"]) == 2


def cell_pixel_import_fixture(*, max_pixels=16000000):
    """Synthetic pixel/frame contracts only; no PDF rendering, OCR or quality claim."""
    from PIL import Image

    from document_files.document_model.recognition_cell_observations import observe_table_cells
    from document_files.document_model.recognition_coordinates import (
        fingerprint,
        image_identity,
        subset_mapping,
    )

    pytest.importorskip("numpy")
    image = Image.new("RGB", (60, 40), "white")
    image.putpixel((1, 1), (237, 237, 237))
    render = {
        "sourceSha256": "a" * 64,
        "page_no": 1,
        "status": "captured",
        "profile": {"scale": 1},
        "pixelSize": [60, 40],
        "pixelSha256": "c" * 64,
        "intrinsicRotation": 0,
        "pageBoxes": {
            "mediaDeclared": [0, 0, 60, 40],
            "cropDeclared": None,
            "effective": [0, 0, 60, 40],
        },
        "pageSizeCanvasUnits": [60, 40],
        "renderCoordinates": {"pixelToPageAffine": [1, 0, 0, -1, 0, 40]},
    }
    render["fingerprint"] = fingerprint(render)
    subset = deepcopy(render)
    subset["sourceSha256"] = "b" * 64
    subset["fingerprint"] = fingerprint(subset)
    mapping = subset_mapping(render, subset)
    frame = {
        "version": "document-files.recognition-coordinates.v1",
        "backend": "ThreadedDoclingParsePageBackend",
        "documentKey": "key=" + "b" * 64,
        "localPageNumber": 1,
        "boundaryType": "crop_box",
        "pageSize": [60, 40],
        "normalizedMediaBox": [0, 0, 60, 40],
        "normalizedCropBox": [0, 0, 60, 40],
        "normalizedAngle": 0,
        "canvas": image_identity(image),
        "requestedCropTopLeft": None,
        "cropPixelBounds": [0, 0, 60, 40],
        "rounding": "python_round_then_clamp",
        "pixelCoordinateOrigin": "TOPLEFT",
        "ocrTruthVerified": False,
    }
    grid = {
        "horizontalLines": [[0, y, 60, 1, 60] for y in (0, 19, 39)],
        "verticalLines": [[x, 0, 1, 40, 40] for x in (0, 29, 59)],
    }
    record = observe_table_cells(
        image,
        source_frame=frame,
        table_crop_bounds=[0, 0, 60, 40],
        cluster_id=0,
        local_page_number=1,
        grid=grid,
        max_pixels=max_pixels,
    )
    doc = ObservationDocument()
    doc.nodes["value"] = {
        "text": "12.50",
        "sourceStructure": {
            "page": 1,
            "bbox": {"left": 5, "top": 5, "right": 12, "bottom": 12, "origin": "TOPLEFT"},
        },
    }
    doc.tables["docling:page:1:table:0"] = {
        "id": "docling:page:1:table:0",
        "page": 1,
        "declaredRowCount": 2,
        "declaredColCount": 2,
        "locator": {"bbox": {"left": 0, "top": 0, "right": 60, "bottom": 40, "origin": "TOPLEFT"}},
        "cells": [{"sourceRef": "value", "row": 0, "col": 0, "rowSpan": 1, "colSpan": 1}],
        "unobservedCellCount": 3,
        "contentCompleteness": "unverified",
    }
    doc.issue("recognition_table_cells_unobserved", count=3)
    doc.issue("recognition_content_completeness_unverified")
    doc.coverage = {"status": "partial", "recognitionContentCompleteness": "unverified"}
    doc.provenance["pdfPageRenderCaptures"] = [
        {
            "page": 1,
            "sourceSha256": "a" * 64,
            "bindingStatus": "source_page_matched",
            "capture": deepcopy(render),
        }
    ]
    return doc, record, render, {"mapping": mapping, "rawPassLinks": []}


def import_cell_pixel_fixture(doc, records, render, evidence, **kwargs):
    from document_files.document_model.recognition_sources import import_cell_pixel_observations

    import_cell_pixel_observations(
        doc,
        records,
        render,
        evidence,
        source_hash="a" * 64,
        page=1,
        prefix="docling:page:1",
        **kwargs,
    )
    return doc.provenance["recognitionCellPixelObservations"][-1]


def test_cell_pixel_import_without_ocr_keeps_missing_values_and_partial_status():
    doc, record, render, evidence = cell_pixel_import_fixture()
    before = deepcopy(doc.to_dict())
    original = deepcopy(record)
    result = import_cell_pixel_fixture(doc, [record], render, evidence)
    entry = result["observations"][0]
    assert entry["observationStatus"] == entry["sourceCoordinateStatus"] == "verified"
    association = entry["structureAssociation"]
    assert association["status"] == "unique_geometry_correspondence"
    assert len(association["slots"]) == 4 and association["valueObserved"] is False
    assert result["rawPixelsRecomputed"] is result["blankValueProven"] is False
    assert result["ocrTruthVerified"] is result["contentCoverageVerified"] is False
    for key in ("nodes", "tables", "bindings", "regions", "relations", "issues", "coverage"):
        assert doc.to_dict()[key] == before[key]
    assert record == original
    assert entry["observation"]["slots"] == original["slots"]


@pytest.mark.parametrize(
    "change", ["source", "page", "subset", "render", "frame", "canvas", "statistics", "slot"]
)
@pytest.mark.parametrize("rehash", [False, True])
def test_cell_pixel_import_rejects_changed_observation_or_source_chain(change, rehash):
    doc, record, render, evidence = cell_pixel_import_fixture()
    if change == "source":
        render["sourceSha256"] = "0" * 64
    elif change == "page":
        render["page_no"] = 2
    elif change == "subset":
        evidence["mapping"]["subsetSha256"] = "0" * 64
    elif change == "render":
        doc.provenance["pdfPageRenderCaptures"][0]["bindingStatus"] = "unverified"
    elif change == "frame":
        record["sourceFrame"]["localPageNumber"] = 2
    elif change == "canvas":
        record["canvas"]["size"] = [61, 40]
    elif change == "statistics":
        record["slots"][0]["regions"]["interior"]["pixelCount"] += 1
    else:
        record["slots"][0]["row"] = 1
    if rehash:
        from document_files.document_model.recognition_coordinates import fingerprint

        record["sourceFrameFingerprint"] = fingerprint(record["sourceFrame"])
        record["fingerprint"] = fingerprint(record)
    before = deepcopy(doc.issues)
    result = import_cell_pixel_fixture(doc, [record], render, evidence)
    entry = result["observations"][0]
    assert entry["observationStatus"] == "unverified"
    assert entry["structureAssociation"]["status"] == "unlinked"
    assert doc.issues == before
    assert entry["observation"] == record


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "two_tables",
        "missing_bbox",
        "wrong_row",
        "merged",
        "wrong_page",
        "shifted_table",
        "dimensions",
    ],
)
def test_cell_pixel_observation_does_not_guess_structure_slot(change):
    doc, record, render, evidence = cell_pixel_import_fixture()
    table = next(iter(doc.tables.values()))
    records = [record]
    if change == "duplicate":
        records.append(deepcopy(record))
    elif change == "two_tables":
        other = deepcopy(table)
        other["id"] = "docling:page:1:table:1"
        doc.tables[other["id"]] = other
    elif change == "missing_bbox":
        doc.nodes["value"]["sourceStructure"]["bbox"] = None
    elif change == "wrong_row":
        table["cells"][0]["row"] = 1
    elif change == "merged":
        table["cells"][0]["colSpan"] = 2
    elif change == "wrong_page":
        table["page"] = 2
    elif change == "shifted_table":
        table["locator"]["bbox"]["left"] = 5
    else:
        table["declaredColCount"] = 3
    before = deepcopy(doc.to_dict())
    result = import_cell_pixel_fixture(doc, records, render, evidence)
    for entry in result["observations"]:
        assert entry["observationStatus"] == "verified"
        assert entry["structureAssociation"]["status"] == "unlinked"
    assert doc.nodes == before["nodes"] and doc.tables == before["tables"]
    assert doc.issues == before["issues"] and doc.coverage == before["coverage"]


def test_cell_pixel_absent_legacy_evidence_does_not_change_observation():
    from document_files.document_model.recognition_sources import import_cell_pixel_observations

    doc = ObservationDocument()
    before = deepcopy(doc.to_dict())
    import_cell_pixel_observations(doc, None, None, None, source_hash="a" * 64, page=1, prefix="p")
    assert doc.to_dict() == before


def test_pdf_projects_cell_pixels_after_structure_before_source_import(monkeypatch):
    import hashlib

    from document_files.document_model import pdf, recognition_sources

    calls = []
    marker = {"version": "synthetic-marker"}
    monkeypatch.setattr(pdf, "_native_pdf", lambda *a: [])
    monkeypatch.setattr(pdf, "import_page_render", lambda *a, **kw: None)
    monkeypatch.setattr(recognition_sources, "import_coordinate_evidence", lambda *a, **kw: None)

    def structure(doc, exported, **kwargs):
        calls.append("structure")
        return []

    def pixels(doc, records, render, coordinate_evidence, **kwargs):
        assert calls == ["structure"] and records == [marker]
        calls.append("pixels")

    def sources(*args, **kwargs):
        assert calls == ["structure", "pixels"]
        calls.append("sources")
        return []

    monkeypatch.setattr(pdf, "import_docling", structure)
    monkeypatch.setattr(pdf, "import_cell_pixel_observations", pixels)
    monkeypatch.setattr(pdf, "import_source_observations", sources)
    content = b"synthetic receiver ordering only"

    class Recognition:
        def observe(self, content):
            return {
                "status": "complete",
                "pageResults": [
                    {
                        "page": 1,
                        "sourceSha256": hashlib.sha256(content).hexdigest(),
                        "document": {},
                        "sourceObservations": {"cellObservations": [marker]},
                    }
                ],
            }

    doc = ObservationDocument()
    pdf.observe_pdf(doc, content, recognition=Recognition())
    assert calls == ["structure", "pixels", "sources"]
    assert doc.coverage["recognitionContentCompleteness"] == "unverified"
    assert any(i["code"] == "recognition_content_completeness_unverified" for i in doc.issues)


def test_cell_pixel_budget_partial_preserves_measurements_without_slot_approval():
    doc, record, render, evidence = cell_pixel_import_fixture(max_pixels=5000)
    assert any(s["measurementStatus"] != "measured" for s in record["slots"])
    before = deepcopy(doc.to_dict())
    result = import_cell_pixel_fixture(doc, [record], render, evidence)
    entry = result["observations"][0]
    assert entry["observationStatus"] == "verified"
    assert entry["pixelMeasurementStatus"] == record["status"]
    assert entry["structureAssociation"]["status"] == "unlinked"
    assert entry["observation"] == record
    assert doc.tables == before["tables"] and doc.issues == before["issues"]


def cell_pixel_link_fixture():
    """Synthetic link claims, deliberately not real TSV/execution evidence."""
    from document_files.document_model.recognition_cell_observations import cell_ocr_links
    from document_files.document_model.recognition_coordinates import fingerprint

    doc, record, render, evidence = cell_pixel_import_fixture()
    record["slots"][0]["ocrUnit"] = {"unitIndex": 0, "unitFingerprint": "d" * 64}
    record["fingerprint"] = fingerprint(record)
    capture = {
        "passId": "p0",
        "sourcePass": "table_repair",
        "status": "complete",
        "page_no": 1,
        "image": {"size": [10, 10], "mode": "RGB", "sha256": "e" * 64},
        "unitFingerprint": "d" * 64,
        "transform": {"repairIndex": 0, "cellUnitIndex": 0},
        "pixelFrame": {
            "tableCropPixelBounds": record["tableCrop"]["pixelBounds"],
            "documentKey": record["sourceFrame"]["documentKey"],
            "localPageNumber": 1,
        },
    }
    payload = {
        "rawOCRPasses": [capture],
        "rawOCRRuns": [],
        "tableRepairs": [
            {
                "page_no": 1,
                "units": [
                    {
                        "row": 0,
                        "col": 0,
                        "status": "ready",
                        "fingerprint": "d" * 64,
                    }
                ],
            }
        ],
    }
    links = cell_ocr_links([record], payload["rawOCRPasses"])
    assert len(links) == 1
    return doc, record, render, evidence, payload, links


@pytest.mark.parametrize(
    "change",
    [
        "single",
        "claimed_batch",
        "failed",
        "no_ink",
        "unit",
        "page",
        "capture",
        "duplicate",
        "mapping",
    ],
)
def test_cell_pixel_raw_ocr_links_are_preserved_never_counted_as_execution_success(change):
    doc, record, render, evidence, payload, links = cell_pixel_link_fixture()
    capture = payload["rawOCRPasses"][0]
    if change == "claimed_batch":
        capture["runFingerprint"] = links[0]["runFingerprint"] = "f" * 64
        capture["tsvInputPageNumber"] = links[0]["tsvInputPageNumber"] = 1
    elif change == "failed":
        capture["status"] = links[0]["status"] = "failed"
    elif change == "no_ink":
        payload["tableRepairs"][0]["units"][0]["status"] = "no_ink_observed"
    elif change == "unit":
        links[0]["unitIndex"] = 1
    elif change == "page":
        capture["page_no"] = 2
    elif change == "capture":
        payload["rawOCRPasses"] = []
    elif change == "duplicate":
        links.append(deepcopy(links[0]))
    elif change == "mapping":
        evidence["mapping"]["sourceSha256"] = "0" * 64
    before = deepcopy(doc.to_dict())
    original = deepcopy(links)
    result = import_cell_pixel_fixture(
        doc, [record], render, evidence, ocr_links=links, source_payload=payload
    )
    saved = result["ocrLinkEvidence"]
    assert saved["rawLinks"] == links == original
    assert saved["verificationStatus"] == "unverified"
    assert saved["executionSuccessInferred"] is False
    assert saved["ocrTruthVerified"] is saved["contentCoverageVerified"] is False
    assert all(c["verificationStatus"] == "unverified" for c in saved["checks"])
    assert saved["checks"][0]["referenceConsistency"] == (
        "matched" if change in ("single", "claimed_batch") else "unmatched"
    )
    for key in ("nodes", "tables", "bindings", "regions", "relations", "issues", "coverage"):
        assert doc.to_dict()[key] == before[key]


def test_cell_pixel_link_absence_does_not_invalidate_an_ocr_unit_or_no_ink_observation():
    doc, record, render, evidence, payload, _ = cell_pixel_link_fixture()
    result = import_cell_pixel_fixture(doc, [record], render, evidence, source_payload=payload)
    assert result["observations"][0]["observationStatus"] == "verified"
    assert result["ocrLinkEvidence"]["status"] == "not_provided"
    assert result["ocrLinkEvidence"]["rawLinks"] is None


def test_cell_pixel_orphan_links_survive_without_cell_observation_records():
    doc, _, render, evidence, payload, links = cell_pixel_link_fixture()
    result = import_cell_pixel_fixture(
        doc, None, render, evidence, ocr_links=links, source_payload=payload
    )
    assert result["observations"] == []
    assert result["ocrLinkEvidence"]["rawLinks"] == links
    assert result["ocrLinkEvidence"]["checks"][0]["referenceConsistency"] == "unmatched"


def test_actual_ocr_input_seam_retains_bounded_rgb_without_changing_tsv(tmp_path, monkeypatch):
    """Synthetic OCR process result; exercise producer, not actual OCR quality."""
    from types import SimpleNamespace

    pytest.importorskip("docling")
    from PIL import Image

    from document_files.document_model import docling_pipeline
    from document_files.document_model.recognition_coordinates import image_identity
    from document_files.document_model.recognition_ruling_pixels import validate_ruling_windows

    image = Image.new("RGB", (100, 100), "white")
    for y in range(100):
        image.putpixel((49, y), (0, 0, 0))
        image.putpixel((50, y), (0, 0, 0))
    image.putpixel((48, 45), (254, 254, 254))
    path = tmp_path / "input.png"
    image.save(path)
    identity = image_identity(image)
    cls = docling_pipeline.pipeline_class(
        RecognitionConfig("/models", "/ocr", "/data"), {}
    )._product_ocr_type
    model = cls.__new__(cls)
    model.options = SimpleNamespace(lang=["kor", "eng"], psm=3)
    model._safe_tesseract_cmd, model._safe_tessdata_path = "/ocr", "/data"
    model.scale, model.orientation = 1, None
    raw = b"left\ttop\twidth\theight\tconf\ttext\n48\t40\t4\t20\t90\tI\n"
    monkeypatch.setattr(docling_pipeline, "bounded_tsv", lambda *a, **k: raw)
    monkeypatch.setattr(docling_pipeline, "bind_ocr_frame", lambda *a: {"inputImage": identity})
    frame = model._run_tesseract(str(path), None)
    capture = model.raw_passes[0]
    assert capture["tsv"].encode() == raw
    assert frame["text"].tolist() == ["I"]
    record = capture["rulingPixelObservation"]
    _, pixels = validate_ruling_windows(record, capture)[0]
    assert b"\xfe\xfe\xfe" in pixels
    assert model.cell_observation_pixels == 4 * 52
    assert model.ruling_observation_windows == 1
    model.cell_observation_pixels = 16000000
    model._run_tesseract(str(path), None)
    limited = model.raw_passes[1]["rulingPixelObservation"]
    assert limited["usage"]["examinedPixels"] == 0
    assert model.cell_observation_pixels == 16000000
    assert model.raw_passes[1]["status"] == "complete"  # Existing raw processing only.
