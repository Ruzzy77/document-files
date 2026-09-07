"""Local adapters and private synchronous result storage; no detached worker claims."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import suppress
from pathlib import Path

from ..analysis import AnalysisInput, AnalysisJob, runtime_root
from ..engine import DocumentFilesError
from .backends import ChatCompletionsClient, ModelClient, ModelError
from .contracts import ExtractionOptions
from .engine import encode, extract_schema_from_stream
from .prompts import PROMPT_VERSION


def _database(job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise DocumentFilesError("invalid-job-id", "Invalid extraction job identifier.")
    root = runtime_root() / "schema-extractions"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    return root / f"{job_id}.sqlite3"


def extract_schema(
    path: str,
    *,
    options: dict | None = None,
    request_id: str | None = None,
    model_client: ModelClient | None = None,
) -> dict:
    selected = ExtractionOptions.model_validate(options or {})
    source = Path(path).expanduser().resolve(strict=True)
    identity = AnalysisInput.from_path(source, format_id=source.suffix.lower().lstrip("."))
    job_id = request_id or uuid.uuid4().hex
    job = AnalysisJob(job_id=job_id, input=identity)
    client = model_client
    if client is None:
        with suppress(ModelError):
            client = ChatCompletionsClient.from_environment()
    model_identity = dict(client.identity) if client else {"available": False}
    model_identity.pop("returnedModel", None)
    fingerprint = hashlib.sha256(
        encode([identity.to_dict(), selected.model_dump(), model_identity, PROMPT_VERSION]).encode()
    ).hexdigest()
    database = _database(job_id)
    fd = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(database, timeout=1) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS result (fingerprint TEXT, body TEXT)")
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT fingerprint, body FROM result").fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise DocumentFilesError("request-mismatch", "Request ID has different inputs.")
                return json.loads(previous[1])
            with source.open("rb") as stream:
                result = extract_schema_from_stream(
                    job, stream, options=selected, model_client=client
                )
            connection.execute("INSERT INTO result VALUES (?, ?)", (fingerprint, encode(result)))
            return result
    except sqlite3.OperationalError as exc:
        raise DocumentFilesError(
            "extraction-busy", "Extraction store is busy or unavailable."
        ) from exc


def get_extraction(
    job_id: str, *, section: str | None = None, offset: int = 0, limit: int = 100
) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise DocumentFilesError("invalid-page", "Invalid extraction page bounds.")
    database = _database(job_id)
    if not database.is_file():
        raise DocumentFilesError("extraction-not-found", "No retained extraction with this ID.")
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1) as connection:
        try:
            record = connection.execute("SELECT body FROM result").fetchone()
        except sqlite3.OperationalError as exc:
            raise DocumentFilesError(
                "extraction-not-ready", "No committed result is available."
            ) from exc
    if record is None:
        raise DocumentFilesError("extraction-not-ready", "No committed result is available.")
    result = json.loads(record[0])
    if section is None:
        return result
    if section == "nodes":
        items = [{"id": k, **v} for k, v in result["document"]["nodes"].items()]
    elif section in {"semantics", "schemaEvidence", "valueEvidence", "issues"}:
        items = result[section]
    else:
        raise DocumentFilesError("invalid-section", "Unsupported extraction section.")
    end = min(len(items), offset + limit)
    return {
        "jobId": job_id,
        "section": section,
        "items": items[offset:end],
        "total": len(items),
        "nextOffset": end if end < len(items) else None,
    }
