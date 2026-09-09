"""Authenticated raw-byte HTTP transport for the durable job service.

POST /v1/jobs accepts application/octet-stream with X-Document-Format,
X-Model-Profile, optional X-Extraction-Options (JSON object), and Idempotency-Key.
It never accepts document URLs, caller-selected server paths or model endpoints.
"""

import hmac
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from .jobs import JobError, JobService, JobStore, ModelProfile


def build_service_from_config(path: str | Path) -> JobService:
    """Read a local administrator configuration, never an HTTP request body."""
    try:
        config = json.loads(Path(path).read_text())
        if set(config) - {"storageRoot", "maxUploadBytes", "defaultTtlSeconds", "profiles"}:
            raise ValueError
        profiles = {}
        for name, entry in config["profiles"].items():
            kind = entry["type"]
            fields = {
                "chat-completions": {
                    "endpoint",
                    "model",
                    "apiKeyEnv",
                    "packRoot",
                    "recognitionPackId",
                    "responseFormat",
                    "maxOutputTokens",
                    "strictSchema",
                    "sampling",
                },
                "local-pack": {
                    "packRoot",
                    "runtimeId",
                    "modelId",
                    "recognitionPackId",
                    "threads",
                    "threadsBatch",
                },
            }[kind]
            if set(entry) - fields - {"type", "revision"}:
                raise ValueError
            settings = {key: entry[key] for key in fields if key in entry}
            required = (
                {"endpoint", "model"}
                if kind == "chat-completions"
                else {"packRoot", "runtimeId", "modelId"}
            )
            if not required <= settings.keys() or any(
                not isinstance(value, str) or not value
                for key, value in settings.items()
                if key
                not in {"maxOutputTokens", "strictSchema", "threads", "threadsBatch", "sampling"}
            ):
                raise ValueError
            if kind == "chat-completions" and "sampling" in settings:
                from .interpretation.backends import _valid_sampling

                if settings["sampling"] is None or not _valid_sampling(settings["sampling"]):
                    raise ValueError
            if kind == "local-pack" and any(
                type(settings[key]) is not int or not 1 <= settings[key] <= 1024
                for key in ("threads", "threadsBatch")
                if key in settings
            ):
                raise ValueError
            if kind == "chat-completions":
                maximum = settings.get("maxOutputTokens")
                if (
                    settings.get("responseFormat", "json_object")
                    not in {"json_object", "json_schema", "none"}
                    or type(settings.get("strictSchema", True)) is not bool
                    or maximum is not None
                    and (type(maximum) is not int or not 1 <= maximum <= 1_000_000)
                ):
                    raise ValueError
            if kind == "chat-completions" and ("packRoot" in settings) != (
                "recognitionPackId" in settings
            ):
                raise ValueError
            profiles[name] = ModelProfile(name, entry["revision"], kind, settings)
        return JobService(
            JobStore(
                config["storageRoot"],
                max_upload_bytes=config.get("maxUploadBytes", 32 * 1024 * 1024),
                default_ttl_seconds=config.get("defaultTtlSeconds"),
            ),
            profiles=profiles,
        )
    except (ValueError, KeyError, TypeError, AttributeError, OSError):
        raise JobError("invalid-server-config") from None


def create_app(service: JobService, *, token: str, profile_probe=None):
    """Build one authenticated app; lifespan explicitly owns the worker service."""
    from fastapi import Depends, FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool

    if (
        not isinstance(token, str)
        or len(token) < 32
        or not token.isascii()
        or any(c.isspace() for c in token)
    ):
        raise JobError("invalid-server-token")

    @asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(service.start)
        try:
            yield
        finally:
            await run_in_threadpool(service.close)

    async def authenticated(request: Request):
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise JobError("unauthorized", 401)

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(authenticated)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(JobError)
    async def job_error(_request, exc):
        return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code}})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return JSONResponse(status_code=422, content={"error": {"code": "invalid-request"}})

    @app.middleware("http")
    async def safe_errors(request, call_next):
        try:
            return await call_next(request)
        except Exception:
            # Avoid framework traceback logging request data or model exceptions.
            return JSONResponse(status_code=500, content={"error": {"code": "internal-error"}})

    @app.get("/v1/capabilities")
    def capabilities():
        return service.capabilities(probe=profile_probe)

    @app.post("/v1/jobs", status_code=202)
    async def submit(request: Request):
        if (
            request.headers.get("content-type", "").split(";")[0].strip()
            != "application/octet-stream"
        ):
            raise JobError("unsupported-content-type", 415)
        if request.headers.get("content-encoding", "identity") != "identity":
            raise JobError("unsupported-content-encoding", 415)
        profile = request.headers.get("x-model-profile", "")
        service.profile(profile)
        try:
            header = request.headers.get("x-extraction-options", "{}")
            if len(header) > 16384:
                raise ValueError
            options = json.loads(header)
            if not isinstance(options, dict):
                raise ValueError
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > service.store.max_upload_bytes:
                raise JobError("upload-too-large", 413)
        except (ValueError, TypeError):
            raise JobError("invalid-request") from None
        count = 0
        service.store.validate_storage()
        with tempfile.SpooledTemporaryFile(
            max_size=1024 * 1024, dir=service.store.uploads
        ) as stream:
            async for chunk in request.stream():
                count += len(chunk)
                if count > service.store.max_upload_bytes:
                    raise JobError("upload-too-large", 413)
                stream.write(chunk)
            stream.seek(0)
            result = await run_in_threadpool(
                service.submit,
                stream,
                profile=profile,
                format_id=request.headers.get("x-document-format", ""),
                options=options,
                idempotency_key=request.headers.get("idempotency-key"),
            )
        return result

    @app.get("/v1/jobs/{job_id}")
    def status(job_id: str):
        return service.get(job_id)

    @app.get("/v1/jobs/{job_id}/result")
    def result(job_id: str, section: str | None = None, offset: int = 0, limit: int = 100):
        if offset < 0 or not 1 <= limit <= 1000:
            raise JobError("invalid-page")
        return service.result(job_id, section=section, offset=offset, limit=limit)

    @app.post("/v1/jobs/{job_id}/cancel", status_code=202)
    def cancel(job_id: str):
        return service.cancel(job_id)

    @app.post("/v1/jobs/{job_id}/resume", status_code=202)
    async def resume(job_id: str, request: Request):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 1024:
                raise JobError("invalid-budget-grant")
        try:
            body = json.loads(data) if data else {}
            if not isinstance(body, dict) or set(body) - {"additionalBudget"}:
                raise ValueError
        except (ValueError, TypeError):
            raise JobError("invalid-budget-grant") from None
        return await run_in_threadpool(
            service.resume, job_id, additional_budget=body.get("additionalBudget")
        )

    @app.delete("/v1/jobs/{job_id}")
    def delete(job_id: str):
        return service.delete(job_id)

    return app


def serve(
    config_path: str | Path, *, token_env="DOCUMENT_FILES_SERVER_TOKEN", host="127.0.0.1", port=8765
):
    """Explicit foreground service; external binding is an administrator choice."""
    import uvicorn

    from .profiles import probe_profile

    service = build_service_from_config(config_path)
    app = create_app(service, token=os.environ.get(token_env, ""), profile_probe=probe_profile)
    uvicorn.run(app, host=host, port=port, access_log=False, log_level="critical")
