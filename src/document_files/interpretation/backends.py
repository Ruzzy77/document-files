"""Inference transport only. Document Files owns prompts, tools and completion."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol


class ModelError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ModelClient(Protocol):
    @property
    def identity(self) -> dict[str, Any]: ...

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str: ...


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
            or response_format not in {"json_object", "none"}
            or (
                max_output_tokens is not None
                and (
                    isinstance(max_output_tokens, bool)
                    or not isinstance(max_output_tokens, int)
                    or not 1 <= max_output_tokens <= 1000000
                )
            )
            or any(c in api_key for c in "\r\n")
        ):
            raise ModelError("ai_configuration_invalid")
        self.endpoint, self.model, self._key = endpoint, model, api_key
        self._actual_model: str | None = None
        self.response_format = response_format
        self.max_output_tokens = max_output_tokens

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
        )

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "adapter": "chat-completions.v1",
            "model": self.model,
            "returnedModel": self._actual_model,
            "configurationId": hashlib.sha256(
                json.dumps(
                    [
                        self.endpoint,
                        self.model,
                        self.response_format,
                        self.max_output_tokens,
                    ]
                ).encode()
            ).hexdigest(),
        }

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str:
        payload = {"model": self.model, "messages": messages}
        if self.response_format != "none":
            payload["response_format"] = {"type": self.response_format}
        if self.max_output_tokens is not None:
            payload["max_tokens"] = self.max_output_tokens
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=timeout
            ) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ModelError("ai_response_budget_exceeded")
            data = json.loads(raw)
            returned_model = data.get("model")
            self._actual_model = returned_model[:256] if isinstance(returned_model, str) else None
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ModelError("ai_response_incomplete")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ModelError("ai_response_invalid")
            return content
        except urllib.error.HTTPError as exc:
            # Never expose HTTP response bodies, document content or bearer tokens.
            code = {
                401: "ai_authentication_failed",
                403: "ai_authorization_failed",
                408: "ai_timeout",
                429: "ai_rate_limited",
            }.get(exc.code, "ai_server_error" if exc.code >= 500 else "ai_request_rejected")
            exc.close()
            raise ModelError(code) from None
        except TimeoutError:
            raise ModelError("ai_timeout") from None
        except urllib.error.URLError as exc:
            code = "ai_timeout" if isinstance(exc.reason, TimeoutError) else "ai_connection_failed"
            raise ModelError(code) from None
        except OSError:
            raise ModelError("ai_connection_failed") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("ai_response_invalid") from None
