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


def response_transport(monkeypatch, *, finish="stop", callback=None, content="{}", reasoning=None):
    payloads = []

    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, *, timeout):
            if request.full_url.endswith("/apply-template"):
                mode = (
                    json.loads(request.data).get("chat_template_kwargs", {}).get("enable_thinking")
                )
                suffix = "<think>\n" if mode else ""
                return Response(
                    json.dumps({"prompt": "formatted-public-fixture" + suffix}).encode()
                )
            if request.full_url.endswith("/tokenize"):
                return Response(json.dumps({"tokens": [1, 2, 3]}).encode())
            payloads.append(json.loads(request.data))
            if callback:
                callback()
            return Response(
                json.dumps(
                    {
                        "model": "test",
                        "choices": [
                            {
                                "finish_reason": finish,
                                "message": {"content": content, "reasoning_content": reasoning},
                            }
                        ],
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
            "model": {"name": "Qwen3.5-9B", "quantization": "Q4_K_M", "thinking": False},
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


@pytest.mark.parametrize("budget", [-1, True, False, "1024", 1.5, 3072, 4096])
def test_managed_reasoning_rejects_invalid_budgets(monkeypatch, tmp_path, budget):
    events = fake_packs(monkeypatch)
    with pytest.raises(ModelError, match="ai_configuration_invalid"):
        ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=budget)
    assert not events


@pytest.mark.parametrize("budget", [0, 1024, 3071])
def test_managed_reasoning_context_transport_identity_and_diagnostics(
    monkeypatch, tmp_path, budget
):
    events = fake_packs(monkeypatch)
    calls = response_transport(monkeypatch, reasoning="private model reasoning")
    client = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=budget)
    default = ManagedPackClient(tmp_path, "runtime", "model")
    assert "reasoning" not in default.identity  # Old default checkpoint identity is unchanged.
    identity = client.identity
    assert identity["reasoning"] == {
        "version": "document-files.managed-reasoning.v1",
        "mode": "thinking",
        "budgetTokens": budget,
        "budgetScope": "per-block",
    }
    assert identity["modelManifestThinking"] is False  # Declaration is not overwritten.
    probes = []
    original = client._context_json

    def probe(path, payload, deadline, request):
        probes.append((path, payload))
        return original(path, payload, deadline, request)

    monkeypatch.setattr(client, "_context_json", probe)
    result = client.infer(InferenceRequest([], output_schema={"type": "object"}, timeout=5))
    assert probes[0][1] == calls[0]
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert calls[0]["reasoning_budget_tokens"] == budget
    assert calls[0]["reasoning_format"] == "deepseek"
    assert calls[0]["max_tokens"] == 3072
    assert result.text == "{}" and "private model reasoning" not in str(result)
    assert client.last_diagnostics["reasoning"] == identity["reasoning"]
    assert client.last_diagnostics["usage"] == result.usage
    assert client.last_diagnostics["finalContentPresent"] is True
    assert "private model reasoning" not in json.dumps(client.last_diagnostics)
    assert client.identity == identity
    exposed = client.identity
    exposed["reasoning"]["budgetTokens"] = 9999
    assert client.identity == identity
    client.close()
    default.close()
    assert [event[0] for event in events] == ["start", "close"]


def test_managed_reasoning_conflict_fails_before_start_without_adjustment(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch)
    calls = response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=1024)
    for output in (64, 1024):
        with pytest.raises(ModelError, match="ai_reasoning_budget_conflict"):
            client.infer(InferenceRequest([], max_output_tokens=output))
        assert client.last_diagnostics["errorCode"] == "ai_reasoning_budget_conflict"
    assert not events and not calls
    assert client.reasoning_budget_tokens == 1024
    client.close()


@pytest.mark.parametrize("content", [None, ""])
def test_managed_reasoning_length_keeps_usage_without_exposing_reasoning(
    monkeypatch, tmp_path, content
):
    fake_packs(monkeypatch)
    response_transport(monkeypatch, finish="length", content=content, reasoning="hidden reasoning")
    client = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=1024)
    result = client.infer(InferenceRequest([]))
    assert result.text == "" and result.finish_reason == "length"
    assert result.usage == {"prompt_tokens": 10, "completion_tokens": 2}
    assert client.last_diagnostics["finalContentPresent"] is False
    assert client.last_diagnostics["finishReason"] == "length"
    assert "hidden reasoning" not in json.dumps(client.last_diagnostics)
    with pytest.raises(ModelError, match="ai_response_incomplete"):
        client.complete([], timeout=5)
    client.close()


def test_default_managed_null_content_keeps_existing_invalid_response(monkeypatch, tmp_path):
    fake_packs(monkeypatch)
    response_transport(monkeypatch, finish="length", content=None, reasoning="hidden reasoning")
    client = ManagedPackClient(tmp_path, "runtime", "model")
    with pytest.raises(ModelError, match="ai_response_invalid"):
        client.infer(InferenceRequest([]))
    client.close()


def test_cloud_cannot_configure_managed_reasoning(monkeypatch):
    for settings in (
        {"reasoning_budget_tokens": 1024},
        {"chat_template_kwargs": {"enable_thinking": True}},
    ):
        with pytest.raises(ModelError, match="ai_configuration_invalid"):
            ChatCompletionsClient("https://example.invalid/v1", "m", sampling=settings)
    calls = response_transport(monkeypatch)
    ChatCompletionsClient("https://example.invalid/v1", "m").infer(InferenceRequest([]))
    assert "reasoning_budget_tokens" not in calls[0]
    assert "chat_template_kwargs" not in calls[0]


def test_profile_reasoning_version_and_mode_are_part_of_pinned_identity(monkeypatch, tmp_path):
    from document_files.jobs import ModelProfile, resolve_profile_identity

    fake_packs(monkeypatch)
    settings = {"packRoot": str(tmp_path), "runtimeId": "runtime", "modelId": "model"}
    default = ModelProfile("cpu", "1", "local-pack", settings)
    enabled = ModelProfile("cpu", "1", "local-pack", {**settings, "reasoningBudgetTokens": 1024})
    smaller = ModelProfile("cpu", "1", "local-pack", {**settings, "reasoningBudgetTokens": 512})
    assert resolve_profile_identity(default) == {"runtimeId": "a" * 64, "modelId": "b" * 64}
    first = resolve_profile_identity(enabled)
    assert first["reasoning"]["version"] == "document-files.managed-reasoning.v1"
    assert first["reasoning"]["budgetTokens"] == 1024
    assert first != resolve_profile_identity(smaller) != resolve_profile_identity(default)
    assert enabled.descriptor() != default.descriptor()


def test_managed_reasoning_refuses_ignored_template_mode(monkeypatch, tmp_path):
    events = fake_packs(monkeypatch)
    calls = response_transport(monkeypatch)
    client = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=1024)
    monkeypatch.setattr(client, "_context_json", lambda *args: {"prompt": "<think>\n</think>\n"})
    with pytest.raises(ModelError, match="ai_reasoning_mode_unsupported"):
        client.infer(InferenceRequest([]))
    assert not calls
    client.close()
    assert [e[0] for e in events] == ["start", "close"]


def test_reasoning_length_usage_checkpoint_and_mode_resume_mismatch(monkeypatch, tmp_path):
    from document_files.analysis import AnalysisInput, AnalysisJob
    from document_files.interpretation.contracts import ExtractionOptions
    from document_files.interpretation.engine import extract_schema_from_stream

    fake_packs(monkeypatch)
    calls = response_transport(
        monkeypatch, finish="length", content=None, reasoning="private reasoning"
    )
    client = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=1024)
    source = b"Public fixture: 1\n"
    job = AnalysisJob(job_id="reasoning", input=AnalysisInput.from_bytes(source, format_id="txt"))
    options = ExtractionOptions(reconstructionContext=False, maxModelCalls=1)
    states = []
    result = extract_schema_from_stream(
        job, io.BytesIO(source), options=options, model_client=client, checkpoint=states.append
    )
    assert result["extraction"]["status"] != "complete"
    assert result["extraction"]["usage"]["completionTokens"] == 2
    assert result["extraction"]["usage"]["modelCalls"] == 1
    assert result["extraction"]["lastInferenceDiagnostics"]["finishReason"] == "length"
    assert "private reasoning" not in json.dumps(states)
    other = ManagedPackClient(tmp_path, "runtime", "model", reasoning_budget_tokens=512)
    with pytest.raises(ValueError, match="incompatible"):
        extract_schema_from_stream(
            job, io.BytesIO(source), options=options, model_client=other, restore=states[-1]
        )
    assert len(calls) == 1
    client.close()
    other.close()


def test_profile_reasoning_contract_version_invalidates_job_fingerprint(monkeypatch, tmp_path):
    from document_files.jobs import JobError, JobStore, ModelProfile

    fake_packs(monkeypatch)
    settings = {
        "packRoot": str(tmp_path),
        "runtimeId": "runtime",
        "modelId": "model",
        "reasoningBudgetTokens": 1024,
    }
    profile = ModelProfile("cpu", "1", "local-pack", settings)
    store = JobStore(tmp_path / "jobs")
    job = store.submit(
        io.BytesIO(b"Public"), format_id="txt", profile=profile, idempotency_key="key"
    )
    monkeypatch.setattr(
        "document_files.interpretation.backends.MANAGED_REASONING_VERSION", "future-version"
    )
    with pytest.raises(JobError, match="idempotency-conflict"):
        store.submit(io.BytesIO(b"Public"), format_id="txt", profile=profile, idempotency_key="key")
    with pytest.raises(JobError, match="profile-pack-changed"):
        store.resume(job["jobId"], profile)
