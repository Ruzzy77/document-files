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
