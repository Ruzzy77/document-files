"""Inference contract and managed lifetime checks; no real models or servers."""

import io
import json
import urllib.error
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from document_files.interpretation.backends import (
    ChatCompletionsClient,
    InferenceRequest,
    InferenceResponse,
    ManagedPackClient,
    ModelError,
)


def response_transport(monkeypatch, *, finish="stop", callback=None):
    payloads = []

    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, *, timeout):
            if request.full_url.endswith("/apply-template"):
                return Response(json.dumps({"prompt": "formatted-public-fixture"}).encode())
            if request.full_url.endswith("/tokenize"):
                return Response(json.dumps({"tokens": [1, 2, 3]}).encode())
            payloads.append(json.loads(request.data))
            if callback:
                callback()
            return Response(
                json.dumps(
                    {
                        "model": "test",
                        "choices": [{"finish_reason": finish, "message": {"content": "{}"}}],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "raw_secret": "never returned",
                        },
                    }
                ).encode()
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    return payloads


@pytest.mark.parametrize("mode", ["json_schema", "json_object", "none"])
def test_per_call_schema_only_when_opted_in(monkeypatch, mode):
    calls = response_transport(monkeypatch)
    client = ChatCompletionsClient(
        "http://127.0.0.1/v1/chat/completions", "test", response_format=mode
    )
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    result = client.infer(
        InferenceRequest([], output_schema=schema, max_output_tokens=25, timeout=4)
    )
    assert result.text == "{}"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 2}
    assert result.finish_reason == "stop"
    assert calls[0]["max_tokens"] == 25
    if mode == "json_schema":
        assert calls[0]["response_format"]["json_schema"]["schema"] == {**schema, "required": []}
    elif mode == "json_object":
        assert calls[0]["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in calls[0]


def test_strict_schema_wire_projection_keeps_product_optional_contract(monkeypatch):
    from copy import deepcopy

    from document_files.interpretation.semantic_types import RegionInterpretation

    calls = response_transport(monkeypatch)
    schema = RegionInterpretation.model_json_schema()
    before = deepcopy(schema)
    client = ChatCompletionsClient(
        "https://example.invalid/v1/chat/completions", "test", response_format="json_schema"
    )
    client.infer(InferenceRequest([], output_schema=schema))
    wire = calls[0]["response_format"]["json_schema"]["schema"]
    assert set(wire["required"]) == set(schema["properties"])
    assert set(wire["$defs"]["FieldLink"]["required"]) == set(
        schema["$defs"]["FieldLink"]["properties"]
    )
    assert "default" not in wire["$defs"]["FieldLink"]["properties"]["groupId"]
    assert wire["$defs"]["FieldLink"]["properties"]["groupId"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert schema == before


def test_strict_transport_never_silently_closes_a_free_dictionary(monkeypatch):
    calls = response_transport(monkeypatch)
    client = ChatCompletionsClient(
        "https://example.invalid/v1/chat/completions", "test", response_format="json_schema"
    )
    with pytest.raises(ModelError, match="ai_output_contract_unsupported"):
        client.infer(InferenceRequest([], output_schema={"type": "object"}))
    assert not calls
    loose = ChatCompletionsClient(
        "https://example.invalid/v1/chat/completions",
        "test",
        response_format="json_schema",
        strict_schema=False,
    )
    loose.infer(InferenceRequest([], output_schema={"type": "object"}))
    assert calls[0]["response_format"]["json_schema"] == {
        "name": "document_files_step",
        "strict": False,
        "schema": {"type": "object"},
    }
    assert loose.identity["configurationId"] != client.identity["configurationId"]


def test_complete_keeps_incomplete_response_failure(monkeypatch):
    response_transport(monkeypatch, finish="length")
    client = ChatCompletionsClient(
        "http://127.0.0.1/v1/chat/completions", "test", interpretation_protocol="legacy"
    )
    assert client.interpretation_protocol == "legacy"
    assert client.infer(InferenceRequest([])).finish_reason == "length"
    with pytest.raises(ModelError, match="ai_response_incomplete"):
        client.complete([], timeout=1)


def test_cancellation_before_and_after_io(monkeypatch):
    cancelled = [True]
    calls = response_transport(monkeypatch, callback=lambda: cancelled.__setitem__(0, True))
    client = ChatCompletionsClient("http://127.0.0.1/v1/chat/completions", "test")
    request = InferenceRequest([], cancelled=lambda: cancelled[0])
    with pytest.raises(ModelError, match="ai_cancelled"):
        client.infer(request)
    assert calls == []
    cancelled[0] = False
    with pytest.raises(ModelError, match="ai_cancelled"):
        client.infer(request)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [(401, "ai_authentication_failed"), (429, "ai_rate_limited"), (503, "ai_server_error")],
)
def test_error_payload_is_never_exposed(monkeypatch, status, code):
    class Opener:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url, status, "private detail", {}, io.BytesIO(b"secret document")
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    client = ChatCompletionsClient("https://example.invalid/v1", "test", "secret key")
    with pytest.raises(ModelError) as caught:
        client.infer(InferenceRequest([]))
    assert str(caught.value) == code


def fake_packs(monkeypatch, *, changed=False):
    runtime = SimpleNamespace(
        manifest={"kind": "llama-cpp-runtime", "version": "v1"}, manifest_sha256="a" * 64
    )
    model = SimpleNamespace(
        manifest={
            "kind": "model",
            "model": {"name": "Qwen3.5-9B", "quantization": "Q4_K_M"},
            "compatibleRuntimes": [
                {"id": "runtime", "version": "v1", "manifestSha256": "a" * 64},
            ],
        },
        manifest_sha256="b" * 64,
    )

    class Store:
        def __init__(self, root):
            pass

        def resolve(self, identifier):
            return runtime if identifier == "runtime" else model

    events = []

    @contextmanager
    def endpoint(store, runtime_id, model_id, **kwargs):
        events.append(("start", kwargs))
        try:
            yield SimpleNamespace(
                base_url="http://127.0.0.1:45789/v1",
                api_key="ephemeral-secret",
                model="model",
                runtime_manifest_sha256="a" * 64,
                model_manifest_sha256=("c" if changed else "b") * 64,
            )
        finally:
            events.append(("close", None))

    monkeypatch.setattr("document_files.runtime_packs.PackStore", Store)
    monkeypatch.setattr("document_files.runtime_packs.managed_llama_endpoint", endpoint)
    return events


def test_managed_pack_identity_lazy_start_schema_and_close(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch)
    requests = response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    identity = client.identity
    assert events == []
    assert "45789" not in json.dumps(identity) and "ephemeral-secret" not in json.dumps(identity)
    schema = {"type": "object"}
    assert client.infer(InferenceRequest([], output_schema=schema, timeout=8)).text == "{}"
    assert events[0][0] == "start" and events[0][1]["parent_managed"] is True
    assert client.complete([], timeout=4) == "{}"
    assert [e[0] for e in events] == ["start"]
    assert requests[0]["response_format"]["json_schema"]["schema"] == schema
    assert requests[0]["temperature"] == 0.0 and requests[0]["seed"] == 1
    assert identity["sampling"] == {"temperature": 0.0, "seed": 1}
    assert client.identity == identity
    client.close()
    client.close()
    assert [e[0] for e in events] == ["start", "close"]
    with pytest.raises(ModelError, match="ai_client_closed"):
        client.infer(InferenceRequest([]))


def test_managed_pack_thread_settings_are_identity_and_launch_arguments(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch)
    response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model", threads=4, threads_batch=10)
    default = ManagedPackClient(tmp_path, "runtime", "model")
    assert client.identity["threads"] == 4 and client.identity["threadsBatch"] == 10
    assert default.identity["threads"] is None and default.identity["threadsBatch"] is None
    assert client.identity != default.identity
    assert client.infer(InferenceRequest([], timeout=5, max_output_tokens=8)).text == "{}"
    assert events[0][1]["threads"] == 4 and events[0][1]["threads_batch"] == 10
    client.close()
    default.close()
    invalid_settings = (
        {"threads": 0},
        {"threads": True},
        {"threads_batch": 1025},
        {"threads_batch": "4"},
    )
    for invalid in invalid_settings:
        with pytest.raises(ModelError, match="ai_configuration_invalid"):
            ManagedPackClient(tmp_path, "runtime", "model", **invalid)


def test_managed_pack_refuses_identity_change_before_launch(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch, changed=True)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    with pytest.raises(ModelError, match="ai_model_changed"):
        client.infer(InferenceRequest([]))
    assert [e[0] for e in events] == ["start", "close"]


def test_managed_pack_single_slot_wait_is_in_request_budget(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    client._slot.acquire()
    try:
        with pytest.raises(ModelError, match="ai_timeout"):
            client.infer(InferenceRequest([], timeout=0.01))
    finally:
        client._slot.release()
        client.close()
    assert events == []


def test_usage_defaults_and_invalid_request():
    assert InferenceResponse("{}").usage == {}
    with pytest.raises(ModelError, match="ai_request_invalid"):
        InferenceRequest([], timeout=float("nan"))


def test_managed_context_counts_formatted_tokens_and_rejects_overflow(monkeypatch, tmp_path):
    fake_packs(monkeypatch)
    completion_calls = response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    probes = []

    def probe(path, payload, deadline, request):
        probes.append((path, payload))
        return (
            {"prompt": "public formatted prompt"}
            if path == "/apply-template"
            else {"tokens": [1] * 6000}
        )

    monkeypatch.setattr(client, "_context_json", probe)
    try:
        with pytest.raises(ModelError, match="ai_context_exceeded"):
            client.infer(InferenceRequest([{"role": "user", "content": "public"}]))
        assert not completion_calls
        assert probes[1][1] == {
            "content": "public formatted prompt",
            "add_special": True,
            "parse_special": True,
        }
        assert client.identity["contextTokens"] == 8192
        assert client.identity["maxOutputTokens"] == client.max_output_tokens == 3072
        assert client.input_budget_chars == 16000
        with pytest.raises(ModelError, match="ai_context_exceeded"):
            client.infer(InferenceRequest([], max_output_tokens=8192))
    finally:
        client.close()


def test_managed_context_probe_does_not_fallback_to_unbounded_inference(monkeypatch, tmp_path):
    fake_packs(monkeypatch)

    class Opener:
        def open(self, request, **kwargs):
            raise urllib.error.HTTPError(
                request.full_url, 404, "secret", {}, io.BytesIO(b"private")
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    client = ManagedPackClient(tmp_path, "runtime", "model")
    try:
        with pytest.raises(ModelError, match="^ai_context_probe_unsupported$"):
            client.infer(InferenceRequest([]))
    finally:
        client.close()


def test_local_grammar_avoids_upstream_large_repeat_failure_without_relaxing_product_contract(
    monkeypatch, tmp_path
):
    import copy

    from document_files.interpretation.semantic_types import RegionInterpretation

    fake_packs(monkeypatch)
    calls = response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    schema = RegionInterpretation.model_json_schema()
    original = copy.deepcopy(schema)
    try:
        client.infer(InferenceRequest([], output_schema=schema))
    finally:
        client.close()
    grammar = calls[0]["response_format"]["json_schema"]["schema"]
    assert schema == original
    assert "maxItems" not in grammar["properties"]["excludedBindings"]
    assert "maxItems" not in grammar["properties"]["dispositions"]
    assert grammar["properties"]["fields"] == schema["properties"]["fields"]
    assert grammar["$defs"] == schema["$defs"]
    assert grammar["additionalProperties"] is False
    with pytest.raises(ValueError):
        RegionInterpretation.model_validate(
            {
                "regionId": "r",
                "excludedBindings": [{"bindingId": "b", "role": "label", "explanation": "Label"}]
                * 2001,
            }
        )


def test_numeric_timings_filter_does_not_forward_response_content():
    from document_files.interpretation.backends import safe_timings

    assert safe_timings(
        {
            "prompt_ms": 12.5,
            "predicted_n": 32,
            "cache_n": True,
            "predicted_ms": float("inf"),
            "prompt_n": -1,
            "predicted_per_second": 10**400,
            "prompt": "private source",
            "api_key": "private key",
        }
    ) == {"prompt_ms": 12.5, "predicted_n": 32}


def test_managed_diagnostics_keep_completed_and_failed_stages(monkeypatch, tmp_path):
    fake_packs(monkeypatch)
    response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    response = client.infer(InferenceRequest([], timeout=5, max_output_tokens=32))
    assert response.text == "{}"
    first = client.last_diagnostics
    assert first["status"] == "response_received"
    assert first["inputTokens"] == 3 and first["maxOutputTokens"] == 32
    assert set(first["stages"]) == {"runtimePreparation", "contextCheck", "serverExchange"}
    assert all(v >= 0 for v in first["stages"].values())
    first["stages"]["runtimePreparation"] = -1
    assert client.last_diagnostics["stages"]["runtimePreparation"] >= 0

    def fail(_):
        raise ModelError("ai_timeout")

    monkeypatch.setattr(client._client, "infer", fail)
    with pytest.raises(ModelError):
        client.infer(InferenceRequest([], timeout=5, max_output_tokens=32))
    assert client.last_diagnostics["failedStage"] == "serverExchange"
    assert "serverTimings" not in client.last_diagnostics
    assert "ephemeral-secret" not in json.dumps(client.last_diagnostics)
    client.close()


def test_timeout_diagnostics_are_checkpointed_without_source_or_keys(monkeypatch, tmp_path):
    from document_files.analysis import AnalysisInput, AnalysisJob
    from document_files.interpretation.contracts import ExtractionOptions
    from document_files.interpretation.engine import extract_schema_from_stream

    fake_packs(monkeypatch)

    def timeout():
        raise TimeoutError()

    response_transport(monkeypatch, callback=timeout)
    client = ManagedPackClient(tmp_path, "runtime", "model")
    source = b"private document: value\n"
    states = []
    result = extract_schema_from_stream(
        AnalysisJob(job_id="timing", input=AnalysisInput.from_bytes(source, format_id="txt")),
        io.BytesIO(source),
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=1),
        model_client=client,
        checkpoint=states.append,
    )
    details = result["extraction"]["lastInferenceDiagnostics"]
    assert details["failedStage"] == "serverExchange"
    assert details == states[-1]["result"]["extraction"]["lastInferenceDiagnostics"]
    assert result["extraction"]["usage"]["unreportedUsageCalls"] == 1
    assert "private document" not in json.dumps(details)
    assert "ephemeral-secret" not in json.dumps(details)
    client.close()


def test_cloud_sampling_is_explicit_recorded_and_validated(monkeypatch):
    requests = response_transport(monkeypatch)
    default = ChatCompletionsClient("http://127.0.0.1:1/v1", "m")
    explicit = ChatCompletionsClient(
        "http://127.0.0.1:1/v1", "m", sampling={"temperature": 0.2, "seed": 3}
    )
    assert default.identity["sampling"] == {}
    assert explicit.identity["sampling"] == {"temperature": 0.2, "seed": 3}
    assert default.identity["configurationId"] != explicit.identity["configurationId"]
    default.infer(InferenceRequest([], timeout=5))
    explicit.infer(InferenceRequest([], timeout=5))
    assert "temperature" not in requests[0] and "seed" not in requests[0]
    assert requests[1]["temperature"] == 0.2 and requests[1]["seed"] == 3
    for invalid in (
        {"temperature": 3},
        {"top_k": -1},
        {"seed": 1.5},
        {"nucleus": 0.5},
        {"temperature": True},
        {"top_p": float("nan")},
    ):
        with pytest.raises(ModelError, match="ai_configuration_invalid"):
            ChatCompletionsClient("http://127.0.0.1:1/v1", "m", sampling=invalid)
