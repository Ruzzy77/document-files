"""Durable, explicitly managed single-worker extraction jobs.

Uploads and execution state belong to this service, not the caller's filesystem.
The extraction workflow remains the owner of interpretation checkpoints and v1 results.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .formats import FORMAT_SPECS
from .interpretation.contracts import ExtractionOptions
from .interpretation.workflow import _ownership, delete_extraction, get_extraction
from .portability import WindowsJob, kill_process_tree, process_options
from .private_fs import private_path


class JobError(Exception):
    """Only a fixed, non-sensitive error code crosses the service boundary."""

    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _execution_status(status: str) -> str:
    """A finished attempt is not a claim that document extraction is complete."""
    return "finished" if status in {"complete", "partial"} else status


@dataclass(frozen=True)
class ModelProfile:
    """Administrator-selected configuration; never construct from an HTTP request.

    settings may contain an admin endpoint or local model path. Secrets must be
    referenced by environment-variable name, never stored in this descriptor.
    """

    name: str
    revision: str
    kind: str
    settings: dict

    def descriptor(self) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.name) or not self.revision:
            raise JobError("invalid-profile")
        if any(k in self.settings for k in ("api_key", "token", "secret", "password")):
            raise JobError("inline-profile-secret-forbidden")
        return json.loads(
            _json(
                {
                    "name": self.name,
                    "revision": self.revision,
                    "kind": self.kind,
                    "settings": self.settings,
                }
            )
        )


def resolve_profile_identity(profile: ModelProfile) -> dict:
    """Resolve installed content only: never fetch packs or start inference."""
    if profile.kind != "local-pack" and not profile.settings.get("recognitionPackId"):
        return {}
    from .runtime_packs import PackError, PackStore

    try:
        store = PackStore(profile.settings["packRoot"])
        identity = {}
        for key, kind in (
            ("runtimeId", "llama-cpp-runtime"),
            ("modelId", "model"),
            ("recognitionPackId", "recognition"),
        ):
            if key in {"runtimeId", "modelId"} and profile.kind != "local-pack":
                continue
            if key == "recognitionPackId" and key not in profile.settings:
                continue
            pack = store.resolve(profile.settings[key])
            if pack.manifest["kind"] != kind:
                raise JobError("profile-pack-kind-mismatch", 409)
            identity[key] = pack.manifest_sha256
        if profile.kind == "local-pack" and "reasoningBudgetTokens" in profile.settings:
            from .interpretation.backends import ModelError, managed_reasoning_identity

            budget = profile.settings["reasoningBudgetTokens"]
            if budget is None:
                raise JobError("invalid-profile")
            try:
                identity["reasoning"] = managed_reasoning_identity(budget)
            except ModelError:
                raise JobError("invalid-profile") from None
        return identity
    except (PackError, OSError, KeyError):
        raise JobError("profile-pack-unavailable", 409) from None


def validate_budget_grant(value: dict | None) -> dict | None:
    if value is None:
        return None
    limits = {"maxModelCalls": 100, "completionSeconds": 3600}
    if not isinstance(value, dict) or not value or set(value) - set(limits):
        raise JobError("invalid-budget-grant")
    if any(type(v) is not int or not 0 <= v <= limits[k] for k, v in value.items()):
        raise JobError("invalid-budget-grant")
    if not any(value.values()):
        raise JobError("invalid-budget-grant")
    return dict(value)


class JobStore:
    def __init__(
        self,
        root: str | Path,
        *,
        max_upload_bytes: int = 32 * 1024 * 1024,
        default_ttl_seconds: int | None = None,
    ):
        if type(max_upload_bytes) is not int or not 1 <= max_upload_bytes <= 128 * 1024 * 1024:
            raise JobError("invalid-upload-limit")
        if default_ttl_seconds is not None and (
            type(default_ttl_seconds) is not int or default_ttl_seconds < 1
        ):
            raise JobError("invalid-retention")
        self.root = Path(root).expanduser().absolute()
        if self.root.is_symlink():
            raise JobError("unsafe-storage")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_path(self.root, directory=True)
        self._resolved_root = self.root.resolve()
        self.uploads = self.root / "uploads"
        self.results = self.root / "results"
        for path in (self.uploads, self.results):
            if path.is_symlink():
                raise JobError("unsafe-storage")
            path.mkdir(exist_ok=True, mode=0o700)
            private_path(path, directory=True)
        self.database = self.root / "jobs.sqlite3"
        if self.database.is_symlink():
            raise JobError("unsafe-storage")
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        private_path(self.database)
        self.max_upload_bytes = max_upload_bytes
        self.default_ttl_seconds = default_ttl_seconds
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, key_hash TEXT UNIQUE, fingerprint TEXT NOT NULL,
                format TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
                profile TEXT NOT NULL, options TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, expires REAL,
                attempt INTEGER NOT NULL DEFAULT 0, error TEXT, resume INTEGER NOT NULL DEFAULT 0
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, declaration in (
                ("pack_identity", "TEXT NOT NULL DEFAULT '{}'"),
                ("budget_grant", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                job_id TEXT NOT NULL, number INTEGER NOT NULL, created REAL NOT NULL,
                budget_grant TEXT, PRIMARY KEY(job_id,number))""")

    @contextmanager
    def connect(self):
        self.validate_storage()
        with closing(sqlite3.connect(self.database, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def validate_storage(self):
        """Reject replaced links before using stored paths, including HTTP spooling.

        The private directory is still an administrator-controlled trust boundary,
        not a sandbox against another process running as the same operating-system user.
        """
        for path in (self.root, self.uploads, self.results):
            expected = self._resolved_root if path == self.root else self._resolved_root / path.name
            if path.is_symlink() or not path.is_dir() or path.resolve() != expected:
                raise JobError("unsafe-storage")
        if self.database.is_symlink() or not self.database.is_file():
            raise JobError("unsafe-storage")

    def record(self, job_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise JobError("job-not-found", 404)
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError("job-not-found", 404)
        return dict(row)

    def snapshot(self, record: dict) -> Path:
        self.validate_storage()
        path = self.uploads / f"{record['id']}.{record['format']}"
        if path.is_symlink():
            raise JobError("unsafe-storage")
        return path

    def get(self, job_id: str) -> dict:
        row = self.record(job_id)
        profile = json.loads(row["profile"])
        result = {
            "jobId": row["id"],
            "status": row["status"],
            "executionStatus": _execution_status(row["status"]),
            "extractionStatus": None,
            "resultRevision": 0,
            "format": row["format"],
            "sha256": row["sha256"],
            "byteSize": row["size"],
            "profile": profile["name"],
            "profileRevision": profile["revision"],
            "createdAt": row["created"],
            "updatedAt": row["updated"],
            "expiresAt": row["expires"],
            "attempt": row["attempt"],
            "error": row["error"],
            "profileFingerprint": hashlib.sha256(
                (row["profile"] + row["pack_identity"]).encode()
            ).hexdigest(),
        }
        result["resultAvailable"] = False
        database = self.results / f"{job_id}.sqlite3"
        if database.is_file() and not database.is_symlink():
            with suppress(sqlite3.Error, ValueError):
                with closing(
                    sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=1)
                ) as db:
                    progress = db.execute(
                        "SELECT json_extract(body,'$.extraction.status'),"
                        "json_extract(body,'$.extraction.modelCalls'),"
                        "json_extract(body,'$.validation.valid'),"
                        "json_extract(body,'$.resultRevision') FROM result LIMIT 1"
                    ).fetchone()
                if progress:
                    result["resultAvailable"] = True
                    result["extractionStatus"] = progress[0]
                    result["resultRevision"] = progress[3] if type(progress[3]) is int else 0
                    result["progress"] = {
                        "extractionStatus": progress[0],
                        "modelCalls": progress[1],
                        "validated": bool(progress[2]),
                    }
        return result

    def submit(
        self,
        stream: BinaryIO,
        *,
        format_id: str,
        profile: ModelProfile,
        options: dict | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        self.validate_storage()
        if format_id not in FORMAT_SPECS or not re.fullmatch(r"[a-z0-9]+", format_id):
            raise JobError("unsupported-format", 415)
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256
        ):
            raise JobError("invalid-idempotency-key")
        try:
            selected = ExtractionOptions.model_validate(
                {"reconstructionContext": False, **(options or {})}
            )
        except (ValueError, TypeError):
            raise JobError("invalid-options") from None
        descriptor = profile.descriptor()
        pack_identity = resolve_profile_identity(profile)
        job_id = uuid.uuid4().hex
        path = self.uploads / f"{job_id}.{format_id}"
        count, digest = 0, hashlib.sha256()
        keep = False
        created = False
        try:
            with path.open("xb") as output:
                created = True
                private_path(path)
                while chunk := stream.read(64 * 1024):
                    count += len(chunk)
                    if count > min(self.max_upload_bytes, selected.maxInputBytes):
                        raise JobError("upload-too-large", 413)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            path.chmod(0o400)
            fingerprint = hashlib.sha256(
                _json(
                    [
                        format_id,
                        digest.hexdigest(),
                        selected.model_dump(),
                        descriptor,
                        pack_identity,
                    ]
                ).encode()
            ).hexdigest()
            key_hash = (
                hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
            )
            with self.connect() as db:
                old = db.execute(
                    "SELECT id,fingerprint FROM jobs WHERE key_hash=?", (key_hash,)
                ).fetchone()
                if old is not None:
                    if old["fingerprint"] != fingerprint:
                        raise JobError("idempotency-conflict", 409)
                    job_id = old["id"]
                else:
                    now = time.time()
                    expires = now + self.default_ttl_seconds if self.default_ttl_seconds else None
                    db.execute(
                        "INSERT INTO jobs (id,key_hash,fingerprint,format,sha256,size,profile,"
                        "options,status,created,updated,expires,pack_identity) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            job_id,
                            key_hash,
                            fingerprint,
                            format_id,
                            digest.hexdigest(),
                            count,
                            _json(descriptor),
                            _json(selected.model_dump()),
                            "queued",
                            now,
                            now,
                            expires,
                            _json(pack_identity),
                        ),
                    )
                    keep = True
            return self.get(job_id)
        finally:
            if created and not keep:
                self.validate_storage()
            if created and not keep and path.exists():
                if path.is_symlink():
                    raise JobError("unsafe-storage")
                path.chmod(0o600)
                path.unlink()

    def transition(self, job_id: str, states: tuple[str, ...], status: str, *, error=None) -> bool:
        placeholders = ",".join("?" for _ in states)
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE jobs SET status=?,updated=?,error=? WHERE id=? "
                f"AND status IN ({placeholders})",
                (status, time.time(), error, job_id, *states),
            )
            return cursor.rowcount == 1

    def recover(self):
        """Only call while holding both service and worker ownership locks."""
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status='interrupted',updated=?,error='service-interrupted' "
                "WHERE status IN ('running','queued','cancelling')",
                (time.time(),),
            )

    def claim(self) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY created,id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='running',attempt=attempt+1,updated=? WHERE id=?",
                (time.time(), row["id"]),
            )
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?)",
                (row["id"], row["attempt"] + 1, time.time(), row["budget_grant"]),
            )
        return self.record(row["id"])

    def resume(self, job_id: str, profile: ModelProfile, *, additional_budget=None) -> dict:
        grant = validate_budget_grant(additional_budget)
        row = self.record(job_id)
        if _json(profile.descriptor()) != row["profile"]:
            raise JobError("resume-profile-mismatch", 409)
        if _json(resolve_profile_identity(profile)) != row["pack_identity"]:
            raise JobError("profile-pack-changed", 409)
        if grant is not None and not (self.results / f"{job_id}.sqlite3").is_file():
            raise JobError("budget-not-started", 409)
        with self.connect() as db:
            changed = db.execute(
                "UPDATE jobs SET status='queued',resume=1,error=NULL,updated=?,budget_grant=? "
                "WHERE id=? AND status IN ('interrupted','cancelled','partial','failed')",
                (time.time(), _json(grant) if grant is not None else None, job_id),
            ).rowcount
        if not changed:
            raise JobError("job-not-resumable", 409)
        return self.get(job_id)

    def delete(self, job_id: str) -> dict:
        row = self.record(job_id)
        if not self.transition(
            job_id,
            ("queued", "interrupted", "cancelled", "partial", "failed", "complete", "deleting"),
            "deleting",
        ):
            raise JobError("job-busy", 409)
        delete_extraction(job_id, storage_dir=self.results)
        path = self.snapshot(row)
        if path.exists():
            path.chmod(0o600)
            path.unlink()
        with self.connect() as db:
            db.execute("DELETE FROM attempts WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM jobs WHERE id=? AND status='deleting'", (job_id,))
        return {"jobId": job_id, "deleted": True}

    def result(self, job_id: str, **pagination) -> dict:
        self.record(job_id)
        from .engine import DocumentFilesError

        try:
            result = get_extraction(job_id, storage_dir=self.results, **pagination)
            # Revision and extraction status must come from the same saved result
            # read, never a second lookup which might observe a newer checkpoint.
            result.setdefault("resultRevision", 0)
            result.setdefault("extractionStatus", result.get("extraction", {}).get("status"))
            result["executionStatus"] = _execution_status(self.record(job_id)["status"])
            return result
        except DocumentFilesError as exc:
            if exc.code in {"invalid-page", "invalid-section"}:
                raise JobError(exc.code) from None
            raise JobError("result-not-ready", 409) from None

    def purge_expired(self) -> int:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id FROM jobs WHERE expires IS NOT NULL AND expires<=? "
                "AND status IN ('interrupted','cancelled','partial','failed','complete')",
                (time.time(),),
            ).fetchall()
        count = 0
        for row in rows:
            with suppress(JobError):
                self.delete(row["id"])
                count += 1
        return count


class ProcessRunner:
    """One supervised subprocess. Its stdin is a parent-lifetime pipe."""

    def __init__(self, store: JobStore, record: dict):
        self._lock = threading.RLock()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "document_files.server_worker", str(store.root), record["id"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **process_options(supervised=True),
        )
        self.guard = WindowsJob(self.process)

    def poll(self):
        return self.process.poll()

    def stop(self):
        with self._lock:
            if os.name == "posix" and self.process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
                with suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=5)
            kill_process_tree(self.process)
            self.process.wait(timeout=15)

    def close(self):
        # Also reap descendants left behind by a worker which exited prematurely.
        with self._lock:
            if os.name == "posix":
                kill_process_tree(self.process)
            self.guard.close()
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait(timeout=15)


class JobService:
    def __init__(
        self, store: JobStore, *, profiles: dict[str, ModelProfile], runner_factory=ProcessRunner
    ):
        self.store = store
        self.profiles = {
            name: ModelProfile(**profile.descriptor()) for name, profile in profiles.items()
        }
        if any(name != profile.name for name, profile in self.profiles.items()):
            raise JobError("invalid-profile")
        self.runner_factory = runner_factory
        self._mutex = threading.RLock()
        self._wake = threading.Event()
        self._closing = threading.Event()
        self._thread = None
        self._ownership_context = None
        self._active = None

    def start(self):
        with self._mutex:
            if self._thread is not None:
                raise JobError("service-already-started", 409)
            ownership = _ownership(self.store.root / "service.sqlite3")
            try:
                ownership.__enter__()
                with _ownership(self.store.root / "worker.sqlite3"):
                    self.store.recover()
            except Exception:
                ownership.__exit__(*sys.exc_info())
                raise JobError("service-busy", 409) from None
            self._ownership_context = ownership
            self._closing.clear()
            self._thread = threading.Thread(
                target=self._loop, name="document-files-jobs", daemon=False
            )
            self._thread.start()

    def close(self):
        with self._mutex:
            if self._thread is None:
                return
            self._closing.set()
            self._wake.set()
            thread = self._thread
        thread.join(timeout=30)
        if thread.is_alive():
            raise JobError("worker-shutdown-incomplete", 503)
        with self._mutex:
            self._thread = None
            self._ownership_context.__exit__(None, None, None)
            self._ownership_context = None

    def _loop(self):
        next_cleanup = time.monotonic()
        while not self._closing.is_set():
            with self._mutex:
                if time.monotonic() >= next_cleanup:
                    self.store.purge_expired()
                    next_cleanup = time.monotonic() + 60
                record = self.store.claim()
                if record:
                    try:
                        runner = self.runner_factory(self.store, record)
                        self._active = (record["id"], runner)
                    except Exception:
                        self.store.transition(
                            record["id"], ("running",), "failed", error="worker-unavailable"
                        )
                        continue
            if not record:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            try:
                while runner.poll() is None:
                    if self._closing.wait(0.1):
                        runner.stop()
                        break
                if self._closing.is_set():
                    self.store.transition(
                        record["id"], ("running",), "interrupted", error="service-stopped"
                    )
                else:
                    self.store.transition(
                        record["id"], ("running",), "interrupted", error="worker-interrupted"
                    )
                self.store.transition(record["id"], ("cancelling",), "cancelled")
            finally:
                runner.close()
                with self._mutex:
                    self._active = None

    def profile(self, name: str) -> ModelProfile:
        if name not in self.profiles:
            raise JobError("profile-not-found", 404)
        return self.profiles[name]

    def submit(self, stream: BinaryIO, *, profile: str, **kwargs) -> dict:
        with self._mutex:
            if self._thread is None or not self._thread.is_alive() or self._closing.is_set():
                raise JobError("service-not-running", 503)
            result = self.store.submit(stream, profile=self.profile(profile), **kwargs)
            self._wake.set()
            return result

    def cancel(self, job_id: str) -> dict:
        with self._mutex:
            self.store.record(job_id)
            if self.store.transition(job_id, ("queued",), "cancelled"):
                return self.store.get(job_id)
            if self.store.transition(job_id, ("running",), "cancelling"):
                if self._active and self._active[0] == job_id:
                    self._active[1].stop()
                self._wake.set()
                return self.store.get(job_id)
            if self.store.record(job_id)["status"] not in {"cancelled", "cancelling"}:
                raise JobError("job-not-cancellable", 409)
            return self.store.get(job_id)

    def resume(self, job_id: str, *, additional_budget=None) -> dict:
        with self._mutex:
            if self._thread is None or not self._thread.is_alive() or self._closing.is_set():
                raise JobError("service-not-running", 503)
            row = self.store.record(job_id)
            result = self.store.resume(
                job_id,
                self.profile(json.loads(row["profile"])["name"]),
                additional_budget=additional_budget,
            )
            self._wake.set()
            return result

    def delete(self, job_id: str) -> dict:
        with self._mutex:
            return self.store.delete(job_id)

    def get(self, job_id: str) -> dict:
        return self.store.get(job_id)

    def result(self, job_id: str, **pagination) -> dict:
        return self.store.result(job_id, **pagination)

    def capabilities(self, probe: Callable | None = None) -> dict:
        return {
            "jobs": True,
            "maxUploadBytes": self.store.max_upload_bytes,
            "workerConcurrency": 1,
            "retention": "manual" if self.store.default_ttl_seconds is None else "ttl",
            "profiles": [
                {
                    "name": p.name,
                    "revision": p.revision,
                    "kind": p.kind,
                    "availability": probe(p) if probe else "not-probed",
                }
                for p in self.profiles.values()
            ],
        }
