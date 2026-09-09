"""Synthetic image/HTTP fixtures only: no model, runtime, OCR or network execution."""

import base64
import hashlib
import io
import json
import time
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

from document_files.interpretation import backends
from document_files.interpretation.backends import InferenceRequest, ManagedPackClient, ModelError


def image_data(fmt="PNG", mode="RGB", size=(2, 3), **kwargs):
    output = io.BytesIO()
    Image.new(mode, size, "white").save(output, format=fmt, **kwargs)
    return "data:image/" + fmt.lower() + ";base64," + base64.b64encode(output.getvalue()).decode()


def messages(url=None):
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "검토할 원본 영역"},
                {"type": "image_url", "image_url": {"url": url or image_data()}},
            ],
        }
    ]


@pytest.fixture
def setup(monkeypatch, tmp_path):
    events, calls = [], []
    runtime = SimpleNamespace(
        manifest={"kind": "llama-cpp-runtime", "version": "1"}, manifest_sha256="a" * 64
    )
    model = SimpleNamespace(
        manifest={
            "kind": "model",
            "model": {
                "name": "Qwen3.5-9B",
                "quantization": "Q4_K_M",
                "thinking": False,
                "file": "text.gguf",
                "vision": {"file": "vision.gguf", "minImageTokens": 1024, "maxImageTokens": 1536},
            },
            "files": [{"path": "vision.gguf", "sha256": "c" * 64, "size": 42, "executable": False}],
            "compatibleRuntimes": [{"id": "runtime", "version": "1", "manifestSha256": "a" * 64}],
        },
        manifest_sha256="b" * 64,
    )

    class Store:
        def __init__(self, root):
            pass

        def resolve(self, name):
            return runtime if name == "runtime" else model

    @contextmanager
    def endpoint(*args, **kwargs):
        events.append("start")
        try:
            yield SimpleNamespace(
                base_url="http://127.0.0.1:12345/v1",
                api_key="private",
                model="fixed",
                runtime_manifest_sha256="a" * 64,
                model_manifest_sha256="b" * 64,
            )
        finally:
            events.append("closed")

    state = {
        "tokens": 2048,
        "capabilities": ["completion", "multimodal"],
        "callback": None,
        "finish": "stop",
        "content": "{}",
    }

    class Opener:
        def open(self, request, *, timeout):
            path = request.full_url.split(":12345")[-1]
            body = request.data
            calls.append((path, body, timeout))
            if state["callback"]:
                state["callback"](path)
            if path == "/models":
                assert request.method == "GET" and body is None
                value = {"models": [{"model": "fixed", "capabilities": state["capabilities"]}]}
            elif path == "/apply-template":
                value = {"prompt": "source-bound image markers <think>\n"}
            elif path.endswith("/input_tokens"):
                value = {"input_tokens": state["tokens"]}
            elif path == "/v1/chat/completions":
                value = {
                    "model": "fixed",
                    "choices": [
                        {
                            "finish_reason": state["finish"],
                            "message": {
                                "content": state["content"],
                                "reasoning_content": "private",
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": state["tokens"], "completion_tokens": 10},
                }
            else:
                pytest.fail("visual path must not use string token approximation: " + path)
            return io.BytesIO(json.dumps(value).encode())

    monkeypatch.setattr("document_files.runtime_packs.PackStore", Store)
    monkeypatch.setattr("document_files.runtime_packs.managed_llama_endpoint", endpoint)
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    return SimpleNamespace(
        make=lambda **kw: ManagedPackClient(tmp_path, "runtime", "model", **kw),
        model=model,
        events=events,
        calls=calls,
        state=state,
    )


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
@pytest.mark.parametrize("thinking", [None, 1024])
def test_visual_exact_request_and_source_bytes_frozen(setup, fmt, thinking):
    client = setup.make(reasoning_budget_tokens=thinking)
    source = messages(image_data(fmt))
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    original = deepcopy(source)

    def mutate(path):
        if path == "/models":
            source[0]["content"][1]["image_url"]["url"] = "https://invalid/changed"
            schema["properties"]["unvalidated"] = {"type": "string"}

    setup.state["callback"] = mutate
    result = client.infer(InferenceRequest(source, output_schema=schema, timeout=5))
    assert result.text == "{}" and result.usage["prompt_tokens"] == 2048
    assert [x[0] for x in setup.calls] == [
        "/models",
        *(["/apply-template"] if thinking is not None else []),
        "/v1/chat/completions/input_tokens",
        "/v1/chat/completions",
    ]
    assert all(call[1] == setup.calls[-1][1] for call in setup.calls[1:])
    payload = json.loads(setup.calls[-1][1])
    assert payload["messages"] == original
    assert "unvalidated" not in payload["response_format"]["json_schema"]["schema"]["properties"]
    if thinking:
        assert payload["chat_template_kwargs"] == {"enable_thinking": True}
        assert payload["reasoning_budget_tokens"] == thinking
    assert client.identity["vision"]["minImageTokens"] == 1024
    assert client.identity["vision"]["maxImageTokens"] == 1536
    assert client.last_diagnostics["vision"]["inputPixels"] == 6
    assert (
        client.last_diagnostics["vision"]["images"][0]["sha256"]
        == hashlib.sha256(
            base64.b64decode(original[0]["content"][1]["image_url"]["url"].split(",")[1])
        ).hexdigest()
    )
    assert "private" not in json.dumps(client.last_diagnostics)
    client.close()
    assert setup.events == ["start", "closed"]


def test_text_pack_has_no_visual_identity_or_image_start(setup):
    del setup.model.manifest["model"]["vision"]
    client = setup.make()
    assert "vision" not in client.identity
    with pytest.raises(ModelError, match="ai_visual_unavailable"):
        client.infer(InferenceRequest(messages()))
    assert setup.events == setup.calls == []


def test_job_profile_pins_same_visual_policy_and_changed_policy_breaks_resume(setup, monkeypatch):
    from document_files.jobs import ModelProfile, resolve_profile_identity

    profile = ModelProfile(
        "cpu", "1", "local-pack", {"packRoot": ".", "runtimeId": "runtime", "modelId": "model"}
    )
    first = resolve_profile_identity(profile)
    assert first["vision"] == setup.make().identity["vision"]
    first["vision"]["imagePolicy"]["formats"].clear()
    assert resolve_profile_identity(profile)["vision"]["imagePolicy"]["formats"] == ["PNG", "JPEG"]
    first = resolve_profile_identity(profile)
    monkeypatch.setattr(backends, "MANAGED_VISION_VERSION", "document-files.managed-vision.v-next")
    assert resolve_profile_identity(profile) != first
    del setup.model.manifest["model"]["vision"]
    assert resolve_profile_identity(profile) == {"runtimeId": "a" * 64, "modelId": "b" * 64}
    assert setup.events == setup.calls == []


@pytest.mark.parametrize(
    "value",
    [
        "https://example.invalid/image.png",
        "file:///image.png",
        "/a.png",
        "data:image/gif;base64,R0lG",
        "data:image/png;base64,!!",
        "data:image/png;base64,eA==",
    ],
)
def test_non_inline_or_malformed_images_rejected_before_start(setup, value):
    with pytest.raises(ModelError, match="ai_visual_input_invalid"):
        setup.make().infer(InferenceRequest(messages(value)))
    assert setup.events == setup.calls == []


@pytest.mark.parametrize(
    "part",
    [
        {"type": "input_audio", "input_audio": {"data": "abc"}},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA==", "detail": "low"}},
        {"type": "input_video", "url": "https://invalid"},
    ],
)
def test_extra_media_not_forwarded(setup, part):
    with pytest.raises(ModelError, match="ai_visual_input_invalid"):
        setup.make().infer(InferenceRequest([{"role": "user", "content": [part]}]))
    assert not setup.events


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_concatenated_or_truncated_streams_rejected(setup, fmt):
    url = image_data(fmt)
    prefix, encoded = url.split(",")
    raw = base64.b64decode(encoded)
    for invalid in (raw + raw, raw[:-5]):
        with pytest.raises(ModelError, match="ai_visual_input_invalid"):
            setup.make().infer(
                InferenceRequest(messages(prefix + "," + base64.b64encode(invalid).decode()))
            )
    assert not setup.events


def test_animation_alpha_and_orientation_rejected(setup):
    out = io.BytesIO()
    Image.new("RGB", (2, 3), "white").save(
        out, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 3), "black")]
    )
    animated = "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    exif = Image.Exif()
    exif[274] = 6
    for url in (animated, image_data(mode="RGBA"), image_data("JPEG", exif=exif)):
        with pytest.raises(ModelError, match="ai_visual_input_invalid"):
            setup.make().infer(InferenceRequest(messages(url)))
    assert not setup.events


@pytest.mark.parametrize("limit", ["maxImages", "maxInputBytes", "maxInputPixels"])
def test_aggregate_image_limits_no_truncation(setup, monkeypatch, limit):
    monkeypatch.setitem(
        backends.MANAGED_IMAGE_POLICY,
        limit,
        {"maxImages": 1, "maxInputBytes": 100, "maxInputPixels": 10}[limit],
    )
    source = messages()
    source[0]["content"].append(deepcopy(source[0]["content"][1]))
    with pytest.raises(ModelError, match="ai_visual_budget_exceeded"):
        setup.make().infer(InferenceRequest(source))
    assert not setup.events


@pytest.mark.parametrize(
    "tokens,code",
    [
        (True, "ai_context_probe_invalid"),
        (-1, "ai_context_probe_invalid"),
        (1.2, "ai_context_probe_invalid"),
        ("5", "ai_context_probe_invalid"),
        (5089, "ai_context_exceeded"),
    ],
)
def test_visual_token_count_invalid_or_full_context_rejected(setup, tokens, code):
    setup.state["tokens"] = tokens
    with pytest.raises(ModelError, match=code):
        setup.make().infer(InferenceRequest(messages()))
    assert len(setup.calls) == 2


def test_capability_missing_never_sends_images_to_completion(setup):
    setup.state["capabilities"] = ["completion"]
    with pytest.raises(ModelError, match="ai_visual_capability_unavailable"):
        setup.make().infer(InferenceRequest(messages()))
    assert [x[0] for x in setup.calls] == ["/models"]


@pytest.mark.parametrize("where", ["before", "/models", "/v1/chat/completions/input_tokens"])
def test_visual_cancellation_and_no_retry(setup, where):
    cancelled = [where == "before"]
    setup.state["callback"] = lambda path: cancelled.__setitem__(0, path == where)
    with pytest.raises(ModelError, match="ai_cancelled"):
        setup.make().infer(InferenceRequest(messages(), cancelled=lambda: cancelled[0]))
    assert not any(x[0] == "/v1/chat/completions" for x in setup.calls)
    if where == "before":
        assert not setup.events


def test_validation_uses_original_deadline(setup, monkeypatch):
    original = backends._inline_image
    clock = [time.monotonic()]
    monkeypatch.setattr(backends.time, "monotonic", lambda: clock[0])

    def delayed(*args):
        result = original(*args)
        clock[0] += 2
        return result

    monkeypatch.setattr(backends, "_inline_image", delayed)
    with pytest.raises(ModelError, match="ai_timeout"):
        setup.make().infer(InferenceRequest(messages(), timeout=1))
    assert not setup.events


def test_visual_length_preserves_usage_not_reasoning_as_final(setup):
    setup.state.update(finish="length", content=None)
    client = setup.make(reasoning_budget_tokens=1024)
    result = client.infer(InferenceRequest(messages()))
    assert result.finish_reason == "length" and result.text == ""
    assert result.usage["completion_tokens"] == 10
    with pytest.raises(ModelError, match="ai_response_incomplete"):
        client.complete(messages(), timeout=2)
    assert client.last_diagnostics["finalContentPresent"] is False


def test_two_opaque_images_and_exact_context_boundary(setup):
    source = messages(image_data(mode="L"))
    source[0]["content"].append(messages(image_data("JPEG"))[0]["content"][1])
    setup.state["tokens"] = 8192 - 3072 - 32
    client = setup.make()
    client.infer(InferenceRequest(source))
    assert len(client.last_diagnostics["vision"]["images"]) == 2
    assert client.last_diagnostics["vision"]["inputPixels"] == 12


def test_png_crc_mutation_rejected_before_start(setup):
    url = image_data()
    raw = bytearray(base64.b64decode(url.split(",")[1]))
    raw[29] ^= 1  # IHDR CRC, not source content transformed by this API.
    with pytest.raises(ModelError, match="ai_visual_input_invalid"):
        setup.make().infer(
            InferenceRequest(messages("data:image/png;base64," + base64.b64encode(raw).decode()))
        )
    assert not setup.events


def test_dimension_limit_checked_before_decode(setup, monkeypatch):
    import struct
    import zlib

    raw = bytearray(base64.b64decode(image_data().split(",")[1]))
    raw[16:24] = struct.pack(">II", 4001, 4000)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]))
    monkeypatch.setattr(Image.Image, "load", lambda *_: pytest.fail("oversized decode forbidden"))
    with pytest.raises(ModelError, match="ai_visual_budget_exceeded"):
        setup.make().infer(
            InferenceRequest(messages("data:image/png;base64," + base64.b64encode(raw).decode()))
        )
    assert not setup.events


def test_visual_refuses_ignored_reasoning_mode(setup, monkeypatch):
    client = setup.make(reasoning_budget_tokens=1024)
    original = client._context_json

    def probe(path, *args):
        if path == "/apply-template":
            return {"prompt": "image marker <think>\n</think>\n"}
        return original(path, *args)

    monkeypatch.setattr(client, "_context_json", probe)
    with pytest.raises(ModelError, match="ai_reasoning_mode_unsupported"):
        client.infer(InferenceRequest(messages()))
    assert not any(x[0].endswith("input_tokens") for x in setup.calls)


def test_visual_probe_consumes_same_request_deadline(setup, monkeypatch):
    clock = [time.monotonic()]
    monkeypatch.setattr(backends.time, "monotonic", lambda: clock[0])

    def delayed(path):
        if path.endswith("input_tokens"):
            clock[0] += 2

    setup.state["callback"] = delayed
    with pytest.raises(ModelError, match="ai_timeout"):
        setup.make().infer(InferenceRequest(messages(), timeout=1))
    assert [x[0] for x in setup.calls] == ["/models", "/v1/chat/completions/input_tokens"]


def test_permissive_decoder_setting_is_not_silently_used(setup, monkeypatch):
    from PIL import ImageFile

    monkeypatch.setattr(ImageFile, "LOAD_TRUNCATED_IMAGES", True)
    with pytest.raises(ModelError, match="ai_visual_decoder_unsupported"):
        setup.make().infer(InferenceRequest(messages(image_data("JPEG"))))
    assert not setup.events


def test_rgb16_png_is_not_silently_downconverted(setup):
    import struct
    import zlib

    raw = bytearray(base64.b64decode(image_data().split(",")[1]))
    raw[24] = 16
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]))
    with pytest.raises(ModelError, match="ai_visual_input_invalid"):
        setup.make().infer(
            InferenceRequest(messages("data:image/png;base64," + base64.b64encode(raw).decode()))
        )
    assert not setup.events
