"""CLI/MCP client for an explicitly running, authenticated Document Files service."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .engine import DocumentFilesError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DocumentFilesError("server-redirect-rejected", "Server redirects are not allowed.")


class JobClient:
    def __init__(self, endpoint: str, token: str, *, timeout=60):
        try:
            url = urllib.parse.urlsplit(endpoint)
            _ = url.port
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise ValueError
            if len(token) < 32 or any(c.isspace() for c in token):
                raise ValueError
        except ValueError:
            raise DocumentFilesError(
                "server-configuration-invalid", "Invalid Document Files server configuration."
            ) from None
        self.endpoint, self._token, self.timeout = endpoint.rstrip("/"), token, timeout

    @classmethod
    def from_environment(cls):
        token = os.environ.get("DOCUMENT_FILES_SERVER_TOKEN", "")
        if not token:
            raise DocumentFilesError(
                "server-unconfigured",
                "Start and configure an authenticated Document Files server first.",
            )
        return cls(os.environ.get("DOCUMENT_FILES_SERVER_URL", "http://127.0.0.1:8765"), token)

    def _request(self, method, path, *, data=None, headers=None):
        headers = {"Authorization": f"Bearer {self._token}", **(headers or {})}
        request = urllib.request.Request(
            self.endpoint + path, method=method, data=data, headers=headers
        )
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=self.timeout
            ) as response:
                raw = response.read(128 * 1024 * 1024 + 1)
            if len(raw) > 128 * 1024 * 1024:
                raise DocumentFilesError(
                    "server-result-too-large", "Retrieve a smaller result section."
                )
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            # Return fixed codes only, never server bodies or uploaded content.
            code = {
                401: "server-unauthorized",
                404: "job-not-found",
                409: "job-conflict",
                413: "upload-too-large",
            }.get(exc.code, "server-request-failed")
            exc.close()
            raise DocumentFilesError(code, "Document Files server rejected the request.") from None
        except (urllib.error.URLError, OSError, ValueError):
            raise DocumentFilesError(
                "server-unavailable",
                "Document Files server is unavailable or returned an invalid response.",
            ) from None

    def capabilities(self):
        return self._request("GET", "/v1/capabilities")

    def start(self, path, *, profile, options=None, idempotency_key=None):
        source = Path(path).expanduser().resolve(strict=True)
        from .interpretation.contracts import ExtractionOptions

        selected = ExtractionOptions.model_validate(
            {"reconstructionContext": False, **(options or {})}
        )
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Document-Format": source.suffix.lstrip(".").lower(),
            "X-Model-Profile": profile,
            "X-Extraction-Options": json.dumps(
                selected.model_dump(), separators=(",", ":"), ensure_ascii=True
            ),
        }
        if any("\r" in value or "\n" in value for value in headers.values()):
            raise DocumentFilesError("invalid-profile", "Invalid extraction profile.")
        if idempotency_key:
            if not re.fullmatch(r"[\x21-\x7e]{1,256}", idempotency_key):
                raise DocumentFilesError("invalid-idempotency-key", "Invalid retry identifier.")
            headers["Idempotency-Key"] = idempotency_key
        # A bounded immutable upload snapshot, never an arbitrary server file path.
        with source.open("rb") as stream:
            data = stream.read(selected.maxInputBytes + 1)
        if len(data) > selected.maxInputBytes:
            raise DocumentFilesError(
                "upload-too-large", "Document exceeds the configured input budget."
            )
        return self._request("POST", "/v1/jobs", data=data, headers=headers)

    def job(
        self, job_id, action="status", *, section=None, offset=0, limit=100, additional_budget=None
    ):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise DocumentFilesError("invalid-job-id", "Invalid managed job identifier.")
        path = "/v1/jobs/" + job_id
        if action == "status":
            return self._request("GET", path)
        if action == "result":
            query = {"offset": offset, "limit": limit}
            if section is not None:
                query["section"] = section
            return self._request("GET", path + "/result?" + urllib.parse.urlencode(query))
        if action == "delete":
            return self._request("DELETE", path)
        if action in {"cancel", "resume"}:
            data = (
                json.dumps({"additionalBudget": additional_budget}).encode()
                if additional_budget is not None
                else b""
            )
            return self._request(
                "POST", path + "/" + action, data=data, headers={"Content-Type": "application/json"}
            )
        raise DocumentFilesError("invalid-job-action", "Unsupported managed job action.")
