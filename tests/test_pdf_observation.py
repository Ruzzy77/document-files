"""PDF geometry and recognition import contracts; no model quality claim."""

from copy import deepcopy
from pathlib import Path

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


def test_recognition_worker_scope_releases_process_and_does_not_inherit_model_key(
    tmp_path, monkeypatch
):
    import json

    import document_files.document_model.docling_adapter as adapter

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
    monkeypatch.setattr(adapter.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(adapter, "WindowsJob", FakeJob)
    monkeypatch.setattr(adapter, "kill_process_tree", lambda process: closed.append("tree"))
    config = RecognitionConfig(
        str(artifacts), str(executable), str(tessdata), python=str(executable)
    )
    result = DoclingRecognition(config).observe(b"public synthetic PDF")
    assert result["status"] == "complete"
    assert closed == ["tree", "wait", "job"]
    assert "DOCUMENT_FILES_AI_API_KEY" not in seen["env"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1"
    assert seen["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert seen["env"]["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in seen["env"]
    assert seen["command"][1:3] == ["-I", "-B"]
    assert seen["command"][3].endswith("recognition_bootstrap.py")


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


def test_table_repair_stage_preserves_conflicting_original_and_applies_call_budget():
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
    model.orientation = 90
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
