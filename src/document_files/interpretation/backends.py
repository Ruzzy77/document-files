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

    def __init__(self, endpoint: str, model: str, api_key: str = ""):
        url = urllib.parse.urlsplit(endpoint)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ModelError("ai_configuration_invalid")
        if not model.strip():
            raise ModelError("ai_configuration_invalid")
        self.endpoint, self.model, self._key = endpoint, model, api_key
        self._actual_model: str | None = None

    @classmethod
    def from_environment(cls) -> ChatCompletionsClient:
        endpoint = os.environ.get("DOCUMENT_FILES_AI_ENDPOINT", "")
        model = os.environ.get("DOCUMENT_FILES_AI_MODEL", "")
        if not endpoint or not model:
            raise ModelError("ai_unavailable")
        return cls(endpoint, model, os.environ.get("DOCUMENT_FILES_AI_API_KEY", ""))

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "adapter": "chat-completions.v1",
            "model": self.model,
            "returnedModel": self._actual_model,
            "configurationId": hashlib.sha256(
                json.dumps([self.endpoint, self.model]).encode()
            ).hexdigest(),
        }

    def complete(self, messages: list[dict[str, Any]], *, timeout: float) -> str:
        body = json.dumps(
            {"model": self.model, "messages": messages, "response_format": {"type": "json_object"}},
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
            self._actual_model = data.get("model")
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ModelError("ai_response_incomplete")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ModelError("ai_response_invalid")
            return content
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Never expose HTTP response bodies, document content or bearer tokens.
            raise ModelError("ai_connection_failed") from exc
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("ai_response_invalid") from exc
