"""Behavioral checks for the product loop; scripted responses do not assess AI quality."""

from __future__ import annotations

import io
import json

import pytest

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.engine import DocumentFilesError
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.validation import check_schema
from document_files.interpretation.workflow import extract_schema, get_extraction


class ScriptedModel:
    identity = {"adapter": "scripted-test", "model": "not-a-real-model"}

    def __init__(self, *, invalid_first=False, uncertain=False):
        self.calls = 0
        self.invalid_first = invalid_first
        self.uncertain = uncertain

    def complete(self, messages, *, timeout):
        self.calls += 1
        if self.invalid_first and self.calls == 1:
            return '{"action":"finish","proposal":{}}'
        payload = json.loads(messages[-1]["content"])
        if "sourceNodes" in payload:
            return '{"issues": []}'
        nodes = payload["nodeIds"]
        target = {"space": "data", "path": "/label"}
        schema_target = {"space": "dataSchema", "path": "/properties/label"}
        schema = {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        }
        return json.dumps(
            {
                "action": "finish",
                "proposal": {
                    "documentSchema": {"type": "object"},
                    "dataSchema": schema,
                    "data": {"label": "12 mm"},
                    "semantics": [
                        {
                            "id": "measurement",
                            "kind": "measurement",
                            "description": "Source measurement",
                            "targets": [target],
                            "scope": [target],
                            "sourceRefs": nodes,
                            "basis": "ai_interpreted",
                            "status": "uncertain" if self.uncertain else "interpreted",
                        }
                    ],
                    "schemaEvidence": [
                        {
                            "target": schema_target,
                            "sourceRefs": nodes,
                            "semanticIds": ["measurement"],
                            "raw": "12 mm",
                            "status": "present",
                            "transformation": "field discovery",
                        }
                    ],
                    "valueEvidence": [
                        {
                            "target": target,
                            "sourceRefs": nodes,
                            "semanticIds": ["measurement"],
                            "raw": "12 mm",
                            "status": "present",
                            "transformation": "verbatim",
                        }
                    ],
                    "accounting": [
                        {
                            "sourceRef": n,
                            "disposition": "represented",
                            "explanation": "Measurement",
                            "semanticIds": ["measurement"],
                        }
                        for n in nodes
                    ],
                    "issues": [],
                },
            }
        )


def run(model=None, content=b"12 mm\n", **options):
    job = AnalysisJob(job_id="test", input=AnalysisInput.from_bytes(content, format_id="txt"))
    return extract_schema_from_stream(
        job, io.BytesIO(content), model_client=model, options=ExtractionOptions(**options)
    )


def test_product_repairs_invalid_response_and_keeps_exact_source():
    model = ScriptedModel(invalid_first=True)
    result = run(model, content=b"12 mm\n\n  ")
    assert model.calls == 3
    assert result["validation"]["valid"]
    assert result["data"] == {"label": "12 mm"}
    assert result["reconstructionContext"]["parts"][0]["text"] == "12 mm\n\n  "
    assert result["provenance"]["model"]["adapter"] == "scripted-test"


def test_model_absence_never_claims_completed_ai(monkeypatch):
    monkeypatch.delenv("DOCUMENT_FILES_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("DOCUMENT_FILES_AI_MODEL", raising=False)
    result = run()
    assert result["extraction"]["status"] == "partial"
    assert {"code": "ai_unavailable"} in result["issues"]
    assert result["dataSchema"] is None


def test_uncertain_semantics_never_complete():
    assert run(ScriptedModel(uncertain=True))["extraction"]["status"] == "partial"


def test_bad_source_refs_and_wrong_values_exhaust_budget():
    result = run(ScriptedModel(), content=b"13 cm", maxModelCalls=2)
    assert result["extraction"]["status"] == "partial"
    assert not result["validation"]["valid"]
    assert result["data"] is None
    assert {"code": "model_call_budget_exceeded"} in result["issues"]


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.com/schema"},
        {"$ref": "#/foo", "foo": {"$ref": "#/foo"}},
        {"type": "string", "pattern": "(a+)+$"},
    ],
)
def test_schema_resolution_is_bounded_and_offline(schema):
    with pytest.raises(ValueError):
        check_schema(schema)


def test_result_retrieval_idempotency_and_input_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENT_FILES_RUNTIME_ROOT", str(tmp_path / "runtime"))
    source = tmp_path / "sample.txt"
    source.write_text("12 mm")
    model = ScriptedModel()
    first = extract_schema(str(source), request_id="stable", model_client=model)
    assert extract_schema(str(source), request_id="stable", model_client=model) == first
    assert model.calls == 2
    assert get_extraction("stable") == first
    assert get_extraction("stable", section="nodes")["total"] == 1
    source.write_text("different")
    with pytest.raises(DocumentFilesError, match="different inputs"):
        extract_schema(str(source), request_id="stable", model_client=model)


def test_identity_mismatch_stops_before_model():
    job = AnalysisJob(job_id="test", input=AnalysisInput.from_bytes(b"a", format_id="txt"))
    model = ScriptedModel()
    with pytest.raises(ValueError, match="identity"):
        extract_schema_from_stream(job, io.BytesIO(b"b"), model_client=model)
    assert model.calls == 0


def test_review_rejection_is_repaired_inside_product():
    class ReviewRejector(ScriptedModel):
        rejected = False

        def complete(self, messages, *, timeout):
            payload = json.loads(messages[-1]["content"])
            if "sourceNodes" in payload and not self.rejected:
                self.rejected = True
                self.calls += 1
                return '{"issues": ["Check the unit scope against the source."]}'
            return super().complete(messages, timeout=timeout)

    model = ReviewRejector()
    result = run(model)
    assert model.calls == 4
    assert result["validation"]["semanticAccuracy"] == "ai_reviewed"


def test_http_transport_runs_both_product_passes(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from document_files.interpretation.backends import ChatCompletionsClient

    scripted = ScriptedModel()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            answer = scripted.complete(payload["messages"], timeout=10)
            response = json.dumps(
                {
                    "model": "test-returned-model",
                    "choices": [{"finish_reason": "stop", "message": {"content": answer}}],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = ChatCompletionsClient(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
            "configured-test-model",
            "secret-for-test",
        )
        result = run(client)
        assert len(requests) == 2
        assert requests[0]["model"] == "configured-test-model"
        assert result["provenance"]["model"]["returnedModel"] == "test-returned-model"
        assert "secret-for-test" not in json.dumps(result)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_native_parts_preserve_styles_and_binary_assets():
    import zipfile

    from document_files.document_model.capture import capture

    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as package:
        package.writestr("word/styles.xml", '<styles name="한글"/>')
        package.writestr("word/media/image.bin", b"\x00\xff")
    result = capture(content.getvalue(), "docx", max_expanded_bytes=1000)
    assert result["nativeCaptureComplete"]
    assert result["parts"][0]["text"] == '<styles name="한글"/>'
    assert result["parts"][1]["data"] == "AP8="
    assert not result["recipientReconstructionVerified"]


def test_context_budget_does_not_silently_truncate_source():
    result = run(ScriptedModel(), content=b"a" * 20000, contextChars=8000)
    assert result["extraction"]["status"] == "partial"
    assert result["data"] is None
    assert result["coverage"]["readNodes"] == 0


def test_source_binding_materializes_instead_of_copying_model_value():
    class WrongValue(ScriptedModel):
        def complete(self, messages, *, timeout):
            answer = json.loads(super().complete(messages, timeout=timeout))
            if "proposal" in answer:
                answer["proposal"]["data"]["label"] = "rewritten by model"
            return json.dumps(answer)

    result = run(WrongValue())
    assert result["data"] == {"label": "12 mm"}
    assert result["valueEvidence"][0]["binding"]["sourceRef"] == "n1"
    assert result["valueEvidence"][0]["raw"] == "12 mm"


def test_optional_schema_fields_require_own_evidence():
    class MissingFieldEvidence(ScriptedModel):
        def complete(self, messages, *, timeout):
            answer = json.loads(super().complete(messages, timeout=timeout))
            if "proposal" in answer:
                answer["proposal"]["dataSchema"]["properties"]["optional"] = {"type": "string"}
            return json.dumps(answer)

    result = run(MissingFieldEvidence(), maxModelCalls=1)
    assert not result["validation"]["valid"]
    assert any("/properties/optional" in error for error in result["validation"]["errors"])


def test_valid_candidate_survives_budget_and_resumes_at_review():
    snapshots = []
    content = b"12 mm"
    job = AnalysisJob(job_id="resume", input=AnalysisInput.from_bytes(content, format_id="txt"))
    model = ScriptedModel()
    options = ExtractionOptions(maxModelCalls=1)
    first = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=model,
        options=options,
        checkpoint=snapshots.append,
    )
    assert first["data"] == {"label": "12 mm"}
    assert first["validation"]["valid"]
    assert first["validation"]["semanticAccuracy"] == "unverified"
    assert first["extraction"]["status"] == "partial"
    assert snapshots[-1]["reviewPending"]
    final = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=model,
        options=options,
        restore=snapshots[-1],
    )
    assert final["extraction"]["status"] == "complete"
    assert final["extraction"]["modelCalls"] == 2
    assert model.calls == 2
    assert {"code": "review_budget_exceeded"} not in final["issues"]
    with pytest.raises(ValueError, match="checkpoint"):
        extract_schema_from_stream(
            job,
            io.BytesIO(content),
            model_client=model,
            options=ExtractionOptions(maxModelCalls=2),
            restore=snapshots[-1],
        )


def test_valid_candidate_survives_model_failure():
    from document_files.interpretation.backends import ModelError

    class BrokenReviewer(ScriptedModel):
        def complete(self, messages, *, timeout):
            if "sourceNodes" in json.loads(messages[-1]["content"]):
                raise ModelError("ai_rate_limited")
            return super().complete(messages, timeout=timeout)

    result = run(BrokenReviewer())
    assert result["data"] == {"label": "12 mm"}
    assert result["validation"]["valid"]
    assert result["extraction"]["status"] == "partial"
    assert {"code": "ai_rate_limited"} in result["issues"]


def test_bindings_preserve_precision_formula_and_cached_native_values():
    from document_files.interpretation.bindings import resolve
    from document_files.interpretation.contracts import SourceBinding

    nodes = {
        "n1": {
            "text": "0.12345678901234567890123456789",
            "semantic": {"value": {"formula": "=SUM(A1:A2)", "cachedValue": {"value": "123.4500"}}},
        }
    }
    exact = nodes["n1"]["text"]
    assert resolve(SourceBinding(sourceRef="n1"), nodes) == (exact, exact)
    with pytest.raises(ValueError, match="precision"):
        resolve(SourceBinding(sourceRef="n1", representation="number"), nodes)
    assert resolve(SourceBinding(sourceRef="n1", path="/semantic/value/formula"), nodes) == (
        "=SUM(A1:A2)",
        "=SUM(A1:A2)",
    )
    assert resolve(
        SourceBinding(
            sourceRef="n1",
            path="/semantic/value/cachedValue/value",
            representation="native",
        ),
        nodes,
    ) == ("123.4500", "123.4500")
    with pytest.raises(ValueError, match="outside"):
        resolve(SourceBinding(sourceRef="n1", start=0, end=1000), nodes)


@pytest.mark.parametrize("status", ["absent", "blank", "unreadable", "uncertain"])
def test_nonpresent_values_cannot_smuggle_invented_data(status):
    class Missing(ScriptedModel):
        def complete(self, messages, *, timeout):
            answer = json.loads(super().complete(messages, timeout=timeout))
            if "proposal" in answer:
                answer["proposal"]["valueEvidence"][0]["status"] = status
            return json.dumps(answer)

    result = run(Missing(), maxModelCalls=1)
    assert not result["validation"]["valid"]
    assert result["data"] is None


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "ai_authentication_failed"),
        (403, "ai_authorization_failed"),
        (408, "ai_timeout"),
        (429, "ai_rate_limited"),
        (503, "ai_server_error"),
        (400, "ai_request_rejected"),
    ],
)
def test_model_transport_errors_are_distinct_and_do_not_expose_body(monkeypatch, status, code):
    import urllib.error

    from document_files.interpretation.backends import ChatCompletionsClient, ModelError

    class Opener:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                status,
                "private response",
                {},
                io.BytesIO(b"secret document"),
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    client = ChatCompletionsClient("http://127.0.0.1/v1/chat/completions", "model", "secret key")
    with pytest.raises(ModelError) as error:
        client.complete([], timeout=1)
    assert str(error.value) == code
    assert error.value.__suppress_context__


def test_model_can_disable_response_format_without_automatic_retry(monkeypatch):
    from document_files.interpretation.backends import ChatCompletionsClient

    captured = []

    class Opener:
        def open(self, request, *, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(
                json.dumps(
                    {
                        "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                    }
                ).encode()
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    client = ChatCompletionsClient(
        "http://127.0.0.1/v1/chat/completions",
        "model",
        response_format="none",
        max_output_tokens=99,
    )
    assert client.complete([], timeout=1) == "{}"
    assert captured == [{"model": "model", "messages": [], "max_tokens": 99}]


def test_package_images_cannot_be_declared_semantically_read():
    import zipfile

    from document_files.interpretation.engine import _has_unread_visuals

    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as package:
        package.writestr("word/media/image1.png", b"image")
    assert _has_unread_visuals(content.getvalue(), "docx", {})
    assert _has_unread_visuals(b'<p>Text</p><img src="private.png">', "html", {})
    assert not _has_unread_visuals(b"<p>Text</p>", "html", {})


def test_native_numeric_binding_refuses_already_rounded_parser_value():
    from document_files.interpretation.bindings import resolve
    from document_files.interpretation.contracts import SourceBinding

    raw = "0.123456789012345678901"
    nodes = {
        "n1": {
            "semantic": {
                "value": {
                    "kind": "number",
                    "value": float(raw),
                    "raw": raw,
                    "rawType": "n",
                }
            }
        }
    }
    with pytest.raises(ValueError, match="precision"):
        resolve(SourceBinding(sourceRef="n1", path="/semantic/value/value"), nodes)
    assert resolve(SourceBinding(sourceRef="n1", path="/semantic/value/raw"), nodes) == (raw, raw)


@pytest.mark.parametrize(
    "status,raw,data",
    [
        ("present", "null", None),
        ("blank", "", None),
        ("absent", "", None),
    ],
)
def test_explicit_null_blank_and_absent_remain_distinct(status, raw, data):
    class NullModel(ScriptedModel):
        def complete(self, messages, *, timeout):
            answer = json.loads(super().complete(messages, timeout=timeout))
            if "proposal" in answer:
                proposal = answer["proposal"]
                proposal["dataSchema"]["properties"]["label"] = {"type": "null"}
                proposal["data"]["label"] = data
                proposal["valueEvidence"][0].update({"status": status, "raw": raw})
            return json.dumps(answer)

    result = run(NullModel(), content=b"value: null")
    assert result["extraction"]["status"] == "complete"
    assert result["data"] == {"label": None}
    evidence = result["valueEvidence"][0]
    assert evidence["status"] == status
    if status == "present":
        assert evidence["binding"]["representation"] == "null"
        assert evidence["raw"] == "null"
    else:
        assert evidence["binding"] is None


def test_native_null_binding_never_confuses_missing_formula_cache_with_null():
    from document_files.interpretation.bindings import resolve
    from document_files.interpretation.contracts import SourceBinding

    nodes = {
        "n1": {"semantic": {"value": {"kind": "null", "value": None}}},
        "n2": {"semantic": {"value": {"kind": "blank", "value": None}}},
        "n3": {"semantic": {"value": {"kind": "formula", "cachedValue": None}}},
        "n4": {"text": "absent"},
    }
    assert resolve(
        SourceBinding(
            sourceRef="n1",
            path="/semantic/value/value",
            representation="native",
        ),
        nodes,
    ) == (None, "null")
    with pytest.raises(ValueError, match="explicitly null"):
        resolve(SourceBinding(sourceRef="n2", path="/semantic/value/value"), nodes)
    with pytest.raises(ValueError, match="explicitly null"):
        resolve(SourceBinding(sourceRef="n3", path="/semantic/value/cachedValue"), nodes)
    with pytest.raises(ValueError, match="null binding requires"):
        resolve(SourceBinding(sourceRef="n4", representation="null"), nodes)


@pytest.mark.parametrize("damage", ["seen", "selected", "history", "candidate", "modelCalls"])
def test_malformed_checkpoint_rejected_without_source_content(damage):
    import copy

    snapshots = []
    content = b"12 mm"
    job = AnalysisJob(job_id="restore", input=AnalysisInput.from_bytes(content, format_id="txt"))
    options = ExtractionOptions(maxModelCalls=1)
    model = ScriptedModel()
    extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=model,
        options=options,
        checkpoint=snapshots.append,
    )
    damaged = copy.deepcopy(snapshots[-1])
    if damage == "seen":
        damaged["seen"] = [{}]
    elif damage == "selected":
        damaged["selected"] = ["unknown-source-private-text"]
    elif damage == "history":
        damaged["history"] = [{"role": "system", "content": "private source text"}]
    elif damage == "candidate":
        damaged["candidate"] = {"private source text": "not a proposal"}
    else:
        damaged["result"]["extraction"]["modelCalls"] = "private source text"
    with pytest.raises(ValueError, match="checkpoint") as error:
        extract_schema_from_stream(
            job,
            io.BytesIO(content),
            model_client=model,
            options=options,
            restore=damaged,
        )
    assert "private" not in str(error.value)
    assert model.calls == 1


@pytest.mark.parametrize(
    "durations, expected_status, expected_data",
    [
        ([150, 10], "complete", {"label": "12 mm"}),
        ([181], "partial", None),
        ([150, 31], "partial", {"label": "12 mm"}),
    ],
)
def test_model_calls_share_total_budget_without_hidden_request_cap(
    monkeypatch,
    durations,
    expected_status,
    expected_data,
):
    now = [1000.0]
    monkeypatch.setattr("document_files.interpretation.engine.time.monotonic", lambda: now[0])

    class TimedModel(ScriptedModel):
        def __init__(self):
            super().__init__()
            self.timeouts = []

        def complete(self, messages, *, timeout):
            self.timeouts.append(timeout)
            answer = super().complete(messages, timeout=timeout)
            now[0] += durations[self.calls - 1]
            return answer

    model = TimedModel()
    result = run(model, completionSeconds=180)
    assert model.timeouts == [180] + ([30] if len(durations) == 2 else [])
    assert result["extraction"]["status"] == expected_status
    assert result["data"] == expected_data
    if expected_status == "partial":
        assert {"code": "completion_budget_exceeded"} in result["issues"]
        assert result["validation"]["semanticAccuracy"] == "unverified"
        assert result["validation"]["valid"] == (expected_data is not None)


@pytest.mark.parametrize("expire_before_call", [1, 2])
def test_budget_is_rechecked_after_request_preparation(monkeypatch, expire_before_call):
    now = [1000.0]
    monkeypatch.setattr("document_files.interpretation.engine.time.monotonic", lambda: now[0])
    content = b"12 mm"
    job = AnalysisJob(
        job_id="preparation-budget", input=AnalysisInput.from_bytes(content, format_id="txt")
    )
    model = ScriptedModel()

    def checkpoint(state):
        # Request preparation/persistence can use the remaining wall-clock budget.
        if state["result"]["extraction"]["modelCalls"] == expire_before_call:
            now[0] = 1180.0

    result = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        model_client=model,
        options=ExtractionOptions(completionSeconds=180),
        checkpoint=checkpoint,
    )
    assert model.calls == expire_before_call - 1
    assert result["extraction"]["modelCalls"] == model.calls
    assert result["extraction"]["status"] == "partial"
    assert {"code": "completion_budget_exceeded"} in result["issues"]
    assert result["data"] == ({"label": "12 mm"} if expire_before_call == 2 else None)
