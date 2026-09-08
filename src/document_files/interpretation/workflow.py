"""Synchronous extraction with private checkpoints and explicit resume/delete.

An OS lock owns each job during execution; SQLite transactions only persist state.
A killed process releases ownership automatically. Existing v1 result rows remain readable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager, suppress
from pathlib import Path

from ..analysis import AnalysisInput, AnalysisJob, runtime_root
from ..engine import DocumentFilesError
from ..private_fs import private_path
from .backends import ChatCompletionsClient, ModelClient, ModelError
from .contracts import ExtractionOptions
from .engine import encode, extract_schema_from_stream
from .prompts import PROMPT_VERSION


def _private(path: Path, *, directory: bool = False) -> None:
    try:
        private_path(path, directory=directory)
    except OSError as exc:
        raise DocumentFilesError(
            "storage-permission-failed", "Cannot establish private extraction storage."
        ) from exc


def storage_root(storage_dir: str | Path | None = None) -> Path:
    configured = storage_dir or os.environ.get("DOCUMENT_FILES_STORAGE_DIR")
    return (
        Path(configured).expanduser() if configured else runtime_root() / "schema-extractions"
    ).absolute()


def _database(job_id: str, storage_dir: str | Path | None = None, *, create=False) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise DocumentFilesError("invalid-job-id", "Invalid extraction job identifier.")
    root = storage_root(storage_dir)
    if root.is_symlink():
        raise DocumentFilesError("unsafe-storage", "Extraction storage cannot be a symlink.")
    if create:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private(root, directory=True)
    path = root / f"{job_id}.sqlite3"
    if path.is_symlink():
        raise DocumentFilesError("unsafe-storage", "Extraction database cannot be a symlink.")
    return path


@contextmanager
def _ownership(database: Path):
    lock = database.with_suffix(".lock")
    if lock.is_symlink():
        raise DocumentFilesError("unsafe-storage", "Extraction lock cannot be a symlink.")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _private(lock)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DocumentFilesError("extraction-busy", "Extraction is already running.") from exc
        yield
    finally:
        os.close(descriptor)
    # Keep the empty lock inode: unlinking it could split ownership between waiters.


@contextmanager
def _connect(database: Path, *, create=False):
    try:
        if create:
            fd = os.open(database, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
            _private(database)
        with (
            closing(
                sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True, timeout=1)
            ) as connection,
            connection,
        ):
            yield connection
    except sqlite3.OperationalError as exc:
        raise DocumentFilesError(
            "extraction-store-unavailable", "Extraction store unavailable."
        ) from exc


def _client(model_client):
    if model_client is not None:
        return model_client
    with suppress(ModelError):
        return ChatCompletionsClient.from_environment()
    return None


def _fingerprint(identity, options, client):
    model_identity = dict(client.identity) if client else {"available": False}
    model_identity.pop("returnedModel", None)
    return hashlib.sha256(
        encode(
            [
                identity.to_dict(),
                options.model_dump(),
                model_identity,
                PROMPT_VERSION,
            ]
        ).encode()
    ).hexdigest()


def _initialize(database):
    with _connect(database, create=True) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS result (fingerprint TEXT, body TEXT)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS execution "
            "(id INTEGER PRIMARY KEY CHECK(id=1), metadata TEXT, checkpoint TEXT, "
            "status TEXT)"
        )


def _execute(source, job_id, selected, client, database, fingerprint, *, restore=None):
    job = AnalysisJob(
        job_id=job_id,
        input=AnalysisInput.from_path(source, format_id=source.suffix.lower().lstrip(".")),
    )

    if _fingerprint(job.input, selected, client) != fingerprint:
        raise DocumentFilesError("request-mismatch", "Input changed before extraction began.")

    def save(snapshot):
        with _connect(database) as connection:
            connection.execute(
                "UPDATE execution SET checkpoint=?, status='running' WHERE id=1",
                (encode(snapshot),),
            )
            if isinstance(snapshot.get("result"), dict):
                connection.execute("DELETE FROM result")
                connection.execute(
                    "INSERT INTO result VALUES (?,?)", (fingerprint, encode(snapshot["result"]))
                )

    try:
        with source.open("rb") as stream:
            result = extract_schema_from_stream(
                job, stream, options=selected, model_client=client, restore=restore, checkpoint=save
            )
        with _connect(database) as connection:
            connection.execute("DELETE FROM result")
            connection.execute("INSERT INTO result VALUES (?,?)", (fingerprint, encode(result)))
            connection.execute("UPDATE execution SET status='finished' WHERE id=1")
        return result
    except BaseException:
        with _connect(database) as connection:
            connection.execute("UPDATE execution SET status='interrupted' WHERE id=1")
        raise


def extract_schema(
    path: str,
    *,
    options: dict | None = None,
    request_id: str | None = None,
    model_client: ModelClient | None = None,
    storage_dir: str | Path | None = None,
    retain: bool = True,
) -> dict:
    selected = ExtractionOptions.model_validate(options or {})
    source = Path(path).expanduser().resolve(strict=True)
    identity = AnalysisInput.from_path(source, format_id=source.suffix.lower().lstrip("."))
    job_id = request_id or uuid.uuid4().hex
    client = _client(model_client)
    if not retain:
        with source.open("rb") as stream:
            return extract_schema_from_stream(
                AnalysisJob(job_id=job_id, input=identity),
                stream,
                options=selected,
                model_client=client,
            )
    fingerprint = _fingerprint(identity, selected, client)
    database = _database(job_id, storage_dir, create=True)
    with _ownership(database):
        _initialize(database)
        with _connect(database) as connection:
            previous = connection.execute("SELECT fingerprint, body FROM result").fetchone()
            execution = connection.execute("SELECT metadata FROM execution WHERE id=1").fetchone()
            if previous or execution:
                prior_fp = previous[0] if previous else json.loads(execution[0])["fingerprint"]
                if prior_fp != fingerprint:
                    raise DocumentFilesError("request-mismatch", "Request ID has different inputs.")
                if previous:
                    return json.loads(previous[1])
                raise DocumentFilesError(
                    "extraction-interrupted", "Explicitly resume this extraction."
                )
            metadata = {
                "path": str(source),
                "options": selected.model_dump(),
                "fingerprint": fingerprint,
            }
            connection.execute(
                "INSERT INTO execution VALUES (1,?,NULL,'running')", (encode(metadata),)
            )
        return _execute(source, job_id, selected, client, database, fingerprint)


def resume_extraction(
    job_id: str,
    *,
    path: str | None = None,
    model_client: ModelClient | None = None,
    storage_dir: str | Path | None = None,
) -> dict:
    database = _database(job_id, storage_dir)
    if not database.is_file():
        raise DocumentFilesError("extraction-not-found", "No retained extraction with this ID.")
    with _ownership(database):
        with _connect(database) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='execution'"
            ).fetchone()
            row = (
                connection.execute(
                    "SELECT metadata, checkpoint FROM execution WHERE id=1"
                ).fetchone()
                if exists
                else None
            )
        if not row:
            raise DocumentFilesError(
                "extraction-not-resumable", "Legacy result has no resume metadata."
            )
        metadata = json.loads(row[0])
        source = Path(path or metadata["path"]).expanduser().resolve(strict=True)
        selected = ExtractionOptions.model_validate(metadata["options"])
        client = _client(model_client)
        identity = AnalysisInput.from_path(source, format_id=source.suffix.lower().lstrip("."))
        fingerprint = _fingerprint(identity, selected, client)
        if fingerprint != metadata["fingerprint"]:
            raise DocumentFilesError(
                "resume-mismatch", "Input, options, model or prompt has changed."
            )
        with _connect(database) as connection:
            stored = connection.execute("SELECT body FROM result").fetchone()
        if stored:
            completed = json.loads(stored[0])
            if completed.get("extraction", {}).get("status") == "complete":
                return completed
        snapshot = json.loads(row[1]) if row[1] else None
        with _connect(database) as connection:
            connection.execute("UPDATE execution SET status='running' WHERE id=1")
        return _execute(source, job_id, selected, client, database, fingerprint, restore=snapshot)


def delete_extraction(job_id: str, *, storage_dir: str | Path | None = None) -> dict:
    database = _database(job_id, storage_dir)
    if not database.is_file():
        return {"jobId": job_id, "deleted": False}
    with _ownership(database):
        database.unlink(missing_ok=True)
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
    return {"jobId": job_id, "deleted": True}


def get_extraction(
    job_id: str,
    *,
    section: str | None = None,
    offset: int = 0,
    limit: int = 100,
    storage_dir: str | Path | None = None,
) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise DocumentFilesError("invalid-page", "Invalid extraction page bounds.")
    database = _database(job_id, storage_dir)
    if not database.is_file():
        raise DocumentFilesError("extraction-not-found", "No retained extraction with this ID.")
    try:
        with closing(
            sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1)
        ) as connection:
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
