"""Inference transport only. Document Files owns prompts, tools and completion."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ModelError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def safe_timings(value):
    """Whitelist numeric server metrics; never forward arbitrary response metadata."""
    allowed = {
        "cache_n",
        "prompt_n",
        "prompt_ms",
        "prompt_per_second",
        "predicted_n",
        "predicted_ms",
        "predicted_per_second",
    }
    return {
        key: number
        for key, number in (value.items() if isinstance(value, dict) else [])
        if key in allowed
        and type(number) in (int, float)
        and 0 <= number <= 2**53 - 1
        and math.isfinite(number)
    }


MANAGED_REASONING_VERSION = "document-files.managed-reasoning.v1"
MANAGED_MAX_OUTPUT_TOKENS = 3072


def managed_reasoning_identity(budget):
    """Explicit execution policy, not a mutation of the installed model manifest."""
    if budget is not None and (
        type(budget) is not int or not 0 <= budget < MANAGED_MAX_OUTPUT_TOKENS
    ):
        raise ModelError("ai_configuration_invalid")
    return {
        "version": MANAGED_REASONING_VERSION,
        "mode": "thinking" if budget is not None else "non-thinking",
        "budgetTokens": budget,
        "budgetScope": "per-block" if budget is not None else None,
    }


LOCAL_GRAMMAR_VERSION = "document-files.llama-grammar.v1"
SAMPLING_KEYS = {"temperature", "top_p", "top_k", "seed"}
# The internal interpreter decodes greedily: structural decisions must not vary
# between runs of the same input, contract and pack. The seed is recorded for
# any future non-greedy configuration; it does not change greedy output.
MANAGED_SAMPLING = {"temperature": 0.0, "seed": 1}


def _valid_sampling(sampling):
    if sampling is None:
        return True
    if not isinstance(sampling, dict) or set(sampling) - SAMPLING_KEYS:
        return False
    for key, value in sampling.items():
        numeric = not isinstance(value, bool) and isinstance(value, (int, float))
        if not numeric or not math.isfinite(value):
            return False
        if key in {"top_k", "seed"} and (type(value) is not int or value < 0):
            return False
    return 0 <= sampling.get("temperature", 0) <= 2 and 0 <= sampling.get("top_p", 1) <= 1


def _strict_wire_schema(schema):
    """Use the required/closed-object strict transport dialect, not a new result type.

    Original product schemas keep their optional fields and remain in the prompt.
    This projection requires those existing fields on the wire (nullable where
    already nullable); it never closes an unrestricted dictionary silently.
    """
    if isinstance(schema, list):
        return [_strict_wire_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: _strict_wire_schema(value) for key, value in schema.items() if key != "default"}
    if "const" in result:
        result["enum"] = [result.pop("const")]
    if result.get("type") == "object" or "properties" in result:
        if result.get("additionalProperties") is not False:
            raise ModelError("ai_output_contract_unsupported")
        result["properties"] = result.get("properties", {})
        result["required"] = list(result["properties"])
    return result


def _local_grammar_schema(schema):
    """Keep the wire shape while checking large cardinalities after generation.

    Pinned llama.cpp b10853 cannot parse the 2,000-item bounded expansion and
    already turns larger bounds into unbounded grammar repetitions. The complete
    original contract stays in the model prompt and product validation. Output
    token/byte budgets remain enforced; this is not a JSON-only grammar fallback.
    """
    if isinstance(schema, dict):
        return {
            key: _local_grammar_schema(value)
            for key, value in schema.items()
            if not (key == "maxItems" and type(value) is int and value >= 2000)
        }
    if isinstance(schema, list):
        return [_local_grammar_schema(value) for value in schema]
    return schema


class ModelClient(Protocol):
    @property
    def identity(self) -> dict[str, Any]: ...

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str: ...


@dataclass(frozen=True)
class InferenceRequest:
    messages: list[dict[str, Any]]
    output_schema: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    timeout: float = 300.0
    cancelled: Callable[[], bool] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (
            not isinstance(self.messages, list)
            or not all(isinstance(message, dict) for message in self.messages)
            or isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (float, int))
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or self.output_schema is not None
            and not isinstance(self.output_schema, dict)
            or self.cancelled is not None
            and not callable(self.cancelled)
            or self.max_output_tokens is not None
            and (
                type(self.max_output_tokens) is not int
                or not 1 <= self.max_output_tokens <= 1000000
            )
        ):
            raise ModelError("ai_request_invalid")

    def check_cancelled(self):
        if self.cancelled and self.cancelled():
            raise ModelError("ai_cancelled")


@dataclass(frozen=True)
class InferenceResponse:
    text: str
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    timings: dict[str, int | float] = field(default_factory=dict)


class InferenceModelClient(ModelClient, Protocol):
    """Optional richer protocol; complete-only model clients remain supported."""

    def infer(self, request: InferenceRequest) -> InferenceResponse: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ModelError("ai_redirect_rejected")


class ChatCompletionsClient:
    """Explicit OpenAI-compatible local or cloud endpoint; no credential discovery."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str = "",
        *,
        response_format: str = "json_object",
        max_output_tokens: int | None = None,
        strict_schema: bool = True,
        interpretation_protocol: str = "compact",
        sampling: dict[str, float | int] | None = None,
    ):
        try:
            url = urllib.parse.urlsplit(endpoint)
            _ = url.port
        except ValueError:
            raise ModelError("ai_configuration_invalid") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ModelError("ai_configuration_invalid")
        if (
            not model.strip()
            or response_format not in {"json_object", "json_schema", "none"}
            or type(strict_schema) is not bool
            or interpretation_protocol not in {"compact", "legacy"}
            or (
                max_output_tokens is not None
                and (
                    isinstance(max_output_tokens, bool)
                    or not isinstance(max_output_tokens, int)
                    or not 1 <= max_output_tokens <= 1000000
                )
            )
            or any(c in api_key for c in "\r\n")
            or not _valid_sampling(sampling)
        ):
            raise ModelError("ai_configuration_invalid")
        self.endpoint, self.model, self._key = endpoint, model, api_key
        self._actual_model: str | None = None
        self.response_format = response_format
        self.max_output_tokens = max_output_tokens
        self.strict_schema = strict_schema
        self.interpretation_protocol = interpretation_protocol
        self.sampling = dict(sampling or {})

    @classmethod
    def from_environment(cls) -> ChatCompletionsClient:
        endpoint = os.environ.get("DOCUMENT_FILES_AI_ENDPOINT", "")
        model = os.environ.get("DOCUMENT_FILES_AI_MODEL", "")
        if not endpoint or not model:
            raise ModelError("ai_unavailable")
        try:
            limit = os.environ.get("DOCUMENT_FILES_AI_MAX_OUTPUT_TOKENS", "")
            max_tokens = int(limit) if limit else None
        except ValueError:
            raise ModelError("ai_configuration_invalid") from None
        return cls(
            endpoint,
            model,
            os.environ.get("DOCUMENT_FILES_AI_API_KEY", ""),
            response_format=os.environ.get("DOCUMENT_FILES_AI_RESPONSE_FORMAT", "json_object"),
            max_output_tokens=max_tokens,
            interpretation_protocol=os.environ.get(
                "DOCUMENT_FILES_INTERPRETATION_PROTOCOL", "compact"
            ),
        )

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "adapter": "chat-completions.v1",
            "model": self.model,
            "returnedModel": self._actual_model,
            "sampling": dict(self.sampling),
            "configurationId": hashlib.sha256(
                json.dumps(
                    [
                        self.endpoint,
                        self.model,
                        self.response_format,
                        self.max_output_tokens,
                        self.interpretation_protocol,
                        self.strict_schema,
                        sorted(self.sampling.items()),
                    ]
                ).encode()
            ).hexdigest(),
        }

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str:
        result = self.infer(InferenceRequest(messages=messages, timeout=timeout))
        if result.finish_reason != "stop":
            raise ModelError("ai_response_incomplete")
        return result.text

    def _request_payload(self, request: InferenceRequest):
        payload = {"model": self.model, "messages": request.messages, **self.sampling}
        if self.response_format == "json_schema" and request.output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "document_files_step",
                    "strict": self.strict_schema,
                    "schema": _strict_wire_schema(request.output_schema)
                    if self.strict_schema
                    else request.output_schema,
                },
            }
        elif self.response_format != "none":
            payload["response_format"] = {"type": "json_object"}
        max_tokens = request.max_output_tokens or self.max_output_tokens
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    def _response_content(self, choice):
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise ModelError("ai_response_invalid")
        return content

    def infer(self, request: InferenceRequest) -> InferenceResponse:
        """One bounded HTTP exchange; process-owned jobs provide hard cancellation.

        The optional callback is checked before/after network IO. A synchronous
        blocked socket is bounded by timeout; it is not falsely reported as an
        immediately cancellable background request.
        """
        request.check_cancelled()
        payload = self._request_payload(request)
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        http_request = urllib.request.Request(
            self.endpoint, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
                http_request, timeout=request.timeout
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            request.check_cancelled()
            if len(raw) > 8 * 1024 * 1024:
                raise ModelError("ai_response_budget_exceeded")
            data = json.loads(raw)
            returned_model = data.get("model")
            self._actual_model = returned_model[:256] if isinstance(returned_model, str) else None
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason not in {
                "stop",
                "length",
                "content_filter",
                "tool_calls",
                "function_call",
            }:
                raise ModelError("ai_response_invalid")
            content = self._response_content(choice)
            usage = data.get("usage", {})
            usage = {
                key: value
                for key, value in (usage.items() if isinstance(usage, dict) else [])
                if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                and type(value) is int
                and value >= 0
            }
            return InferenceResponse(
                content, usage, finish_reason, safe_timings(data.get("timings"))
            )
        except urllib.error.HTTPError as exc:
            # Never expose HTTP response bodies, document content or bearer tokens.
            code = {
                401: "ai_authentication_failed",
                403: "ai_authorization_failed",
                408: "ai_timeout",
                429: "ai_rate_limited",
            }.get(exc.code, "ai_server_error" if exc.code >= 500 else "ai_request_rejected")
            exc.close()
            request.check_cancelled()
            raise ModelError(code) from None
        except TimeoutError:
            request.check_cancelled()
            raise ModelError("ai_timeout") from None
        except urllib.error.URLError as exc:
            request.check_cancelled()
            code = "ai_timeout" if isinstance(exc.reason, TimeoutError) else "ai_connection_failed"
            raise ModelError(code) from None
        except OSError:
            request.check_cancelled()
            raise ModelError("ai_connection_failed") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("ai_response_invalid") from None


class _ManagedLlamaTransport(ChatCompletionsClient):
    """Only instantiated for an owned, authenticated loopback llama.cpp server."""

    def __init__(self, *args, reasoning_budget_tokens=None, **kwargs):
        super().__init__(*args, **kwargs)
        managed_reasoning_identity(reasoning_budget_tokens)
        self.reasoning_budget_tokens = reasoning_budget_tokens

    def _request_payload(self, request):
        payload = super()._request_payload(request)
        if self.reasoning_budget_tokens is not None:
            output_tokens = request.max_output_tokens or self.max_output_tokens
            if output_tokens is None or self.reasoning_budget_tokens >= output_tokens:
                raise ModelError("ai_reasoning_budget_conflict")
            payload["chat_template_kwargs"] = {"enable_thinking": True}
            payload["reasoning_budget_tokens"] = self.reasoning_budget_tokens
            # Keep private reasoning separate even if a server environment default
            # would otherwise merge it into final content.
            payload["reasoning_format"] = "deepseek"
        return payload

    def _response_content(self, choice):
        # A bounded reasoning run can end before final content begins. Preserve
        # its finish reason and total usage, never substitute reasoning text.
        if (
            self.reasoning_budget_tokens is not None
            and choice.get("finish_reason") == "length"
            and choice["message"].get("content") is None
        ):
            return ""
        return super()._response_content(choice)


class ManagedPackClient:
    """Lazy CPU llama.cpp slot, pinned to verified installed manifest identities.

    ``threads`` (generation) and ``threads_batch`` (prompt processing) are explicit
    execution settings recorded in the identity. Unset keeps the pinned runtime's
    own defaults; the client never sizes them from the host. An explicitly set
    ``reasoning_budget_tokens`` enables thinking with a finite per-block budget;
    the total output cap is unchanged. None retains the non-thinking default.
    """

    def __init__(
        self,
        pack_root,
        runtime_id: str,
        model_id: str,
        *,
        parent_managed: bool = True,
        interpretation_protocol: str = "compact",
        threads: int | None = None,
        threads_batch: int | None = None,
        reasoning_budget_tokens: int | None = None,
    ):
        from ..runtime_packs import PackError, PackStore

        if interpretation_protocol not in {"compact", "legacy"} or any(
            value is not None and (type(value) is not int or not 1 <= value <= 1024)
            for value in (threads, threads_batch)
        ):
            raise ModelError("ai_configuration_invalid")
        self.reasoning = managed_reasoning_identity(reasoning_budget_tokens)
        self.reasoning_budget_tokens = reasoning_budget_tokens
        self.interpretation_protocol = interpretation_protocol
        self.parent_managed = parent_managed
        self.threads, self.threads_batch = threads, threads_batch
        self.context_tokens = 8192
        self.input_budget_chars = 16000
        self.max_output_tokens = MANAGED_MAX_OUTPUT_TOKENS
        self.runtime_id, self.model_id = runtime_id, model_id
        self._lock = threading.RLock()
        self._slot = threading.Lock()
        self._last_diagnostics = {}
        self._manager = None
        self._client = None
        self._closed = False
        try:
            self.store = PackStore(pack_root)
            runtime = self.store.resolve(runtime_id)
            model = self.store.resolve(model_id)
            if runtime.manifest["kind"] != "llama-cpp-runtime" or model.manifest["kind"] != "model":
                raise ModelError("ai_pack_kind_mismatch")
            compatibility = {
                "id": runtime_id,
                "version": runtime.manifest["version"],
                "manifestSha256": runtime.manifest_sha256,
            }
            if compatibility not in model.manifest["compatibleRuntimes"]:
                raise ModelError("ai_pack_incompatible")
            model_config = model.manifest.get("model", {})
            if (
                model_config.get("name") != "Qwen3.5-9B"
                or model_config.get("quantization") != "Q4_K_M"
            ):
                raise ModelError("ai_pack_model_unapproved")
            self._identity = {
                "adapter": "managed-llama-cpp.v1",
                "model": model_id,
                "runtimeManifestSha256": runtime.manifest_sha256,
                "modelManifestSha256": model.manifest_sha256,
                "interpretationProtocol": interpretation_protocol,
                "contextTokens": self.context_tokens,
                "inputBudgetChars": self.input_budget_chars,
                "maxOutputTokens": self.max_output_tokens,
                "grammarAdapter": LOCAL_GRAMMAR_VERSION,
                "threads": threads,
                "threadsBatch": threads_batch,
                "sampling": dict(MANAGED_SAMPLING),
            }
            if reasoning_budget_tokens is not None:
                self._identity["reasoning"] = dict(self.reasoning)
                self._identity["modelManifestThinking"] = model_config.get("thinking")
        except (PackError, OSError):
            raise ModelError("ai_pack_unavailable") from None

    @property
    def identity(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._identity))

    @property
    def last_diagnostics(self):
        # Snapshot only, not a log of prompts or a history of model responses.
        return json.loads(json.dumps(self._last_diagnostics))

    def _start(self, timeout):
        from ..runtime_packs import PackError, managed_llama_endpoint

        if self._closed:
            raise ModelError("ai_client_closed")
        if self._client is not None:
            return
        manager = managed_llama_endpoint(
            self.store,
            self.runtime_id,
            self.model_id,
            context_tokens=self.context_tokens,
            startup_timeout=min(timeout, 180),
            threads=self.threads,
            threads_batch=self.threads_batch,
            parent_managed=self.parent_managed,
        )
        try:
            endpoint = manager.__enter__()
        except (PackError, OSError):
            raise ModelError("ai_local_runtime_unavailable") from None
        try:
            if (
                endpoint.runtime_manifest_sha256 != self._identity["runtimeManifestSha256"]
                or endpoint.model_manifest_sha256 != self._identity["modelManifestSha256"]
            ):
                raise ModelError("ai_model_changed")
            self._client = _ManagedLlamaTransport(
                endpoint.base_url.rstrip("/") + "/chat/completions",
                endpoint.model,
                endpoint.api_key,
                response_format="json_schema",
                strict_schema=False,
                interpretation_protocol=self.interpretation_protocol,
                sampling=MANAGED_SAMPLING,
                reasoning_budget_tokens=self.reasoning_budget_tokens,
            )
            self._manager = manager
        except BaseException:
            manager.__exit__(*sys.exc_info())
            raise

    def _context_json(self, path: str, payload: dict, deadline: float, request: InferenceRequest):
        request.check_cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelError("ai_timeout")
        # This endpoint is the owned loopback server, never a caller-provided URL.
        origin = self._client.endpoint.removesuffix("/v1/chat/completions")
        call = urllib.request.Request(
            origin + path,
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
            {"Content-Type": "application/json", "Authorization": f"Bearer {self._client._key}"},
            method="POST",
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(
                call, timeout=remaining
            ) as response:
                body = response.read(8 * 1024 * 1024 + 1)
            request.check_cancelled()
            if len(body) > 8 * 1024 * 1024:
                raise ModelError("ai_context_probe_invalid")
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ModelError("ai_context_probe_invalid")
            return value
        except urllib.error.HTTPError as exc:
            exc.close()
            request.check_cancelled()
            raise ModelError("ai_context_probe_unsupported") from None
        except TimeoutError:
            request.check_cancelled()
            raise ModelError("ai_timeout") from None
        except urllib.error.URLError as exc:
            request.check_cancelled()
            raise ModelError(
                "ai_timeout"
                if isinstance(exc.reason, TimeoutError)
                else "ai_context_probe_unavailable"
            ) from None
        except (OSError, ValueError):
            request.check_cancelled()
            raise ModelError("ai_context_probe_invalid") from None

    def _check_context(self, request: InferenceRequest, deadline: float, output_tokens: int):
        payload = {"messages": request.messages}
        if self.reasoning_budget_tokens is not None:
            # Use the very same owned transport dialect, schema and mode during
            # template preparation; otherwise the context reservation can differ.
            payload = self._client._request_payload(
                InferenceRequest(
                    request.messages,
                    output_schema=_local_grammar_schema(request.output_schema),
                    max_output_tokens=output_tokens,
                    timeout=request.timeout,
                )
            )
        formatted = self._context_json("/apply-template", payload, deadline, request).get("prompt")
        if not isinstance(formatted, str):
            raise ModelError("ai_context_probe_invalid")
        if self.reasoning_budget_tokens is not None and not formatted.rstrip().endswith("<think>"):
            raise ModelError("ai_reasoning_mode_unsupported")
        tokens = self._context_json(
            "/tokenize",
            {"content": formatted, "add_special": True, "parse_special": True},
            deadline,
            request,
        ).get("tokens")
        if not isinstance(tokens, list) or any(type(token) is not int for token in tokens):
            raise ModelError("ai_context_probe_invalid")
        # Reserve a small guard for template/BOS differences. Never truncate input.
        if len(tokens) + output_tokens + 32 > self.context_tokens:
            raise ModelError("ai_context_exceeded")
        return len(tokens)

    def infer(self, request: InferenceRequest) -> InferenceResponse:
        request.check_cancelled()
        deadline = time.monotonic() + request.timeout
        while True:
            request.check_cancelled()
            if self._closed:
                raise ModelError("ai_client_closed")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError("ai_timeout")
            if self._slot.acquire(timeout=min(remaining, 0.1)):
                break
        started = time.monotonic()
        stage_started = started
        stage = "runtimePreparation"
        diagnostic = {
            "version": "document-files.inference-diagnostics.v1",
            "stages": {},
            "status": "interrupted",
            "reasoning": dict(self.reasoning),
        }

        def next_stage(name):
            nonlocal stage, stage_started
            now = time.monotonic()
            diagnostic["stages"][stage] = max(0.0, now - stage_started)
            stage, stage_started = name, now

        try:
            request.check_cancelled()
            output_tokens = request.max_output_tokens or self.max_output_tokens
            if output_tokens > self.max_output_tokens:
                raise ModelError("ai_context_exceeded")
            if (
                self.reasoning_budget_tokens is not None
                and self.reasoning_budget_tokens >= output_tokens
            ):
                raise ModelError("ai_reasoning_budget_conflict")
            with self._lock:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ModelError("ai_timeout")
                self._start(remaining)
                client = self._client
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError("ai_timeout")
            next_stage("contextCheck")
            diagnostic["inputTokens"] = self._check_context(request, deadline, output_tokens)
            diagnostic["maxOutputTokens"] = output_tokens
            next_stage("serverExchange")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelError("ai_timeout")
            result = client.infer(
                InferenceRequest(
                    request.messages,
                    output_schema=_local_grammar_schema(request.output_schema),
                    max_output_tokens=output_tokens,
                    timeout=remaining,
                    cancelled=lambda: (
                        self._closed or bool(request.cancelled and request.cancelled())
                    ),
                )
            )
            request.check_cancelled()
            diagnostic["serverTimings"] = safe_timings(result.timings)
            diagnostic["finishReason"] = result.finish_reason
            diagnostic["usage"] = dict(result.usage)
            diagnostic["finalContentPresent"] = bool(result.text)
            diagnostic["status"] = "response_received"
            return result
        except ModelError as exc:
            diagnostic["status"] = "failed"
            diagnostic["errorCode"] = exc.code
            diagnostic["failedStage"] = stage
            raise
        finally:
            diagnostic["stages"][stage] = max(0.0, time.monotonic() - stage_started)
            diagnostic["elapsedSeconds"] = max(0.0, time.monotonic() - started)
            self._last_diagnostics = diagnostic
            self._slot.release()

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str:
        result = self.infer(InferenceRequest(messages, timeout=timeout))
        if result.finish_reason != "stop":
            raise ModelError("ai_response_incomplete")
        return result.text

    def close(self):
        with self._lock:
            self._closed = True
            if self._manager is not None:
                manager, self._manager = self._manager, None
                self._client = None
                manager.__exit__(None, None, None)
