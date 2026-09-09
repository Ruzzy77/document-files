"""Job lifecycle and authenticated transport; no real model or document downloads."""

import io
import json
import threading
import time

import pytest

from document_files.jobs import JobError, JobService, JobStore, ModelProfile
from document_files.server_worker import run_job


@pytest.fixture
def profile():
    return ModelProfile(
        "cpu", "r1", "chat-completions", {"endpoint": "http://127.0.0.1:1/v1", "model": "test"}
    )


def submit(store, profile, text=b"Public fixture", **kwargs):
    return store.submit(io.BytesIO(text), format_id="txt", profile=profile, **kwargs)


def eventually(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


class WaitingRunner:
    def __init__(self, store, record):
        self.stopped = threading.Event()

    def poll(self):
        return 1 if self.stopped.is_set() else None

    def stop(self):
        self.stopped.set()

    def close(self):
        self.stop()


def test_upload_identity_retention_and_conflict(tmp_path, profile):
    store = JobStore(tmp_path)
    first = submit(store, profile, idempotency_key="opaque-key")
    assert first["executionStatus"] == "queued"
    assert first["extractionStatus"] is None and first["resultRevision"] == 0
    assert first["expiresAt"] is None
    record = store.record(first["jobId"])
    assert json.loads(record["options"])["reconstructionContext"] is False
    snapshot = store.snapshot(record)
    assert snapshot.read_bytes() == b"Public fixture"
    assert submit(store, profile, idempotency_key="opaque-key")["jobId"] == first["jobId"]
    with pytest.raises(JobError, match="idempotency-conflict"):
        submit(store, profile, text=b"Different", idempotency_key="opaque-key")
    assert list(store.uploads.iterdir()) == [snapshot]
    assert "endpoint" not in json.dumps(first)
    assert store.delete(first["jobId"])["deleted"]
    assert not snapshot.exists()


def test_upload_limits_and_invalid_options_clean_up(tmp_path, profile):
    store = JobStore(tmp_path, max_upload_bytes=3)
    with pytest.raises(JobError, match="upload-too-large"):
        submit(store, profile)
    with pytest.raises(JobError, match="invalid-options"):
        submit(store, profile, text=b"a", options={"endpoint": "https://outside.invalid"})
    assert not list(store.uploads.iterdir())


def test_durable_recovery_does_not_replay(tmp_path, profile):
    from document_files.interpretation.workflow import _connect, _initialize, _store_result

    store = JobStore(tmp_path)
    job = submit(store, profile)
    store.claim()
    database = store.results / f"{job['jobId']}.sqlite3"
    _initialize(database)
    with _connect(database) as db:
        _store_result(
            db,
            "fixture",
            {
                "extraction": {"status": "partial"},
                "validation": {"valid": True},
                "data": {"kept": "0"},
            },
        )
    service = JobService(
        JobStore(tmp_path), profiles={profile.name: profile}, runner_factory=WaitingRunner
    )
    service.start()
    try:
        assert service.get(job["jobId"])["status"] == "interrupted"
        assert service.result(job["jobId"])["data"] == {"kept": "0"}
        service.resume(job["jobId"])
        eventually(lambda: service.get(job["jobId"])["status"] == "running")
        assert service.get(job["jobId"])["attempt"] == 2
        with pytest.raises(JobError, match="job-busy"):
            service.delete(job["jobId"])
        service.cancel(job["jobId"])
        eventually(lambda: service.get(job["jobId"])["status"] == "cancelled")
        assert service.get(job["jobId"])["extractionStatus"] == "partial"
        assert service.result(job["jobId"])["resultRevision"] == 1
        assert service.result(job["jobId"])["data"] == {"kept": "0"}
        service.delete(job["jobId"])
    finally:
        service.close()


def test_single_service_owner_and_shutdown(tmp_path, profile):
    first = JobService(
        JobStore(tmp_path), profiles={profile.name: profile}, runner_factory=WaitingRunner
    )
    second = JobService(
        JobStore(tmp_path), profiles={profile.name: profile}, runner_factory=WaitingRunner
    )
    first.start()
    try:
        with pytest.raises(JobError, match="service-busy"):
            second.start()
        job = first.submit(io.BytesIO(b"data"), format_id="txt", profile="cpu")
        eventually(lambda: first.get(job["jobId"])["status"] == "running")
    finally:
        first.close()
    assert first.get(job["jobId"])["status"] == "interrupted"
    second.start()
    second.close()


def test_resume_rejects_changed_profile(tmp_path, profile):
    store = JobStore(tmp_path)
    job = submit(store, profile)
    store.recover()
    changed = ModelProfile(profile.name, "r2", profile.kind, profile.settings)
    with pytest.raises(JobError, match="resume-profile-mismatch"):
        store.resume(job["jobId"], changed)


def test_retention_does_not_retroactively_expire_existing_jobs(tmp_path, profile):
    manual = JobStore(tmp_path)
    old = submit(manual, profile)
    ttl = JobStore(tmp_path, default_ttl_seconds=1)
    new = submit(ttl, profile)
    ttl.recover()
    with ttl.connect() as db:
        db.execute("UPDATE jobs SET expires=0 WHERE id=?", (new["jobId"],))
    assert ttl.purge_expired() == 1
    assert ttl.get(old["jobId"])["expiresAt"] is None


def test_worker_calls_workflow_and_closes_model(tmp_path, profile, monkeypatch):
    store = JobStore(tmp_path)
    job = submit(store, profile)
    store.claim()
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    def extraction(path, **kwargs):
        assert kwargs["options"]["reconstructionContext"] is False
        assert kwargs["request_id"] == job["jobId"]
        # A long model call must not own a SQLite transaction.
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        return {"extraction": {"status": "complete"}, "validation": {"valid": True}}

    monkeypatch.setattr("document_files.server_worker.extract_schema", extraction)
    assert run_job(store, job["jobId"], resolver=lambda _: Client()) == 0
    assert store.get(job["jobId"])["status"] == "complete"
    assert closed == [True]


def test_worker_rejects_changed_snapshot_and_missing_model(tmp_path, profile):
    store = JobStore(tmp_path)
    job = submit(store, profile)
    row = store.claim()
    path = store.snapshot(row)
    path.chmod(0o600)
    path.write_bytes(b"changed")
    assert run_job(store, job["jobId"], resolver=lambda _: None) == 1
    assert store.get(job["jobId"])["status"] == "failed"
    other = submit(store, profile)
    store.claim()
    assert run_job(store, other["jobId"], resolver=lambda _: None) == 1
    assert store.get(other["jobId"])["status"] == "failed"


def test_http_auth_bounded_upload_and_no_paths(tmp_path, profile):
    from fastapi.testclient import TestClient

    from document_files.http_server import create_app

    service = JobService(
        JobStore(tmp_path, max_upload_bytes=64),
        profiles={"cpu": profile},
        runner_factory=WaitingRunner,
    )
    token = "a" * 40
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "X-Document-Format": "txt",
        "X-Model-Profile": "cpu",
        "Idempotency-Key": "repeat",
    }
    with TestClient(create_app(service, token=token)) as client:
        assert client.get("/v1/capabilities").status_code == 401
        assert client.post("/v1/jobs", content=b"x", headers={}).status_code == 401
        assert client.post("/v1/jobs", content=b"x" * 65, headers=headers).status_code == 413
        invalid = {**headers, "X-Extraction-Options": json.dumps({"path": "/secret"})}
        assert client.post("/v1/jobs", content=b"x", headers=invalid).status_code == 400
        response = client.post("/v1/jobs", content=b"public", headers=headers)
        assert response.status_code == 202, response.text
        job_id = response.json()["jobId"]
        assert response.json()["executionStatus"] in {"queued", "running"}
        assert response.json()["extractionStatus"] is None
        assert response.json()["resultRevision"] == 0
        again = client.post("/v1/jobs", content=b"public", headers=headers)
        assert again.json()["jobId"] == job_id
        conflict = client.post("/v1/jobs", content=b"different", headers=headers)
        assert conflict.status_code == 409
        assert client.get(f"/v1/jobs/{job_id}/result", headers=headers).status_code == 409
        assert client.post(f"/v1/jobs/{job_id}/cancel", headers=headers).status_code == 202
        eventually(lambda: service.get(job_id)["status"] == "cancelled")
        assert (
            client.get(f"/v1/jobs/{job_id}", headers=headers).json()["executionStatus"]
            == "cancelled"
        )
        assert client.delete(f"/v1/jobs/{job_id}", headers=headers).status_code == 200
        assert client.get(f"/v1/jobs/{job_id}", headers=headers).status_code == 404


def test_real_subprocess_missing_credentials_fails_without_inference(tmp_path, monkeypatch):
    profile = ModelProfile(
        "missing",
        "r1",
        "chat-completions",
        {
            "endpoint": "http://127.0.0.1:1/v1",
            "model": "test",
            "apiKeyEnv": "DF_TEST_MISSING_SECRET",
        },
    )
    monkeypatch.delenv("DF_TEST_MISSING_SECRET", raising=False)
    service = JobService(JobStore(tmp_path), profiles={profile.name: profile})
    service.start()
    try:
        job = service.submit(io.BytesIO(b"Public fixture"), format_id="txt", profile="missing")
        eventually(lambda: service.get(job["jobId"])["status"] == "failed")
        assert service.get(job["jobId"])["error"] == "ai_authentication_missing"
        eventually(lambda: service._active is None)
    finally:
        service.close()


def test_result_passthrough_pagination_and_checkpoint_progress(tmp_path, profile):
    from document_files.interpretation.workflow import _connect, _initialize, _store_result

    store = JobStore(tmp_path)
    job = submit(store, profile)
    database = store.results / f"{job['jobId']}.sqlite3"
    _initialize(database)
    result = {
        "jobId": job["jobId"],
        "schemaVersion": "document-files.schema-extraction-result.v1",
        "extraction": {"status": "partial", "modelCalls": 2},
        "validation": {"valid": True},
        "document": {"nodes": {"n1": {"text": "0"}}},
        "valueEvidence": [{"raw": "0"}],
        "futureExtension": {"keep": True},
        "resultRevision": 4,
    }
    with _connect(database) as db:
        db.execute("INSERT INTO result VALUES (?,?)", ("fingerprint", json.dumps(result)))
    assert store.get(job["jobId"])["progress"]["modelCalls"] == 2
    assert store.get(job["jobId"])["resultRevision"] == 4
    assert store.get(job["jobId"])["extractionStatus"] == "partial"
    assert store.result(job["jobId"])["futureExtension"] == {"keep": True}
    assert store.result(job["jobId"])["resultRevision"] == 4
    assert store.result(job["jobId"])["extractionStatus"] == "partial"
    store.transition(job["jobId"], ("queued",), "partial")
    assert store.get(job["jobId"])["executionStatus"] == "finished"
    assert store.get(job["jobId"])["status"] == "partial"  # legacy field is unchanged
    assert store.result(job["jobId"])["executionStatus"] == "finished"
    assert store.result(job["jobId"], section="nodes")["items"] == [{"id": "n1", "text": "0"}]
    page = store.result(job["jobId"], section="nodes")
    assert page["resultRevision"] == 4 and page["extractionStatus"] == "partial"
    with pytest.raises(JobError, match="invalid-section"):
        store.result(job["jobId"], section="unknown")
    # New committed versions are visible through status and full-result reads.
    result["extraction"]["status"] = "complete"
    with _connect(database) as db:
        _store_result(db, "fingerprint", result)
    assert result["resultRevision"] == 5 and result["extractionStatus"] == "complete"
    store.transition(job["jobId"], ("partial",), "complete")
    assert store.get(job["jobId"])["executionStatus"] == "finished"
    assert store.get(job["jobId"])["extractionStatus"] == "complete"
    assert (
        store.get(job["jobId"])["resultRevision"]
        == store.result(job["jobId"])["resultRevision"]
        == 5
    )
    page = store.result(job["jobId"], section="nodes")
    assert page["resultRevision"] == 5 and page["extractionStatus"] == "complete"
    # An older body remains readable without rewriting it or inventing a revision.
    result.pop("resultRevision")
    result.pop("extractionStatus")
    with _connect(database) as db:
        db.execute("UPDATE result SET body=?", (json.dumps(result),))
    assert store.get(job["jobId"])["resultRevision"] == 0
    assert store.result(job["jobId"])["resultRevision"] == 0
    assert store.result(job["jobId"], section="nodes")["resultRevision"] == 0


def test_result_revision_is_not_replaced_by_a_newer_status_read(tmp_path, profile, monkeypatch):
    store = JobStore(tmp_path)
    job_id = submit(store, profile)["jobId"]
    monkeypatch.setattr(
        "document_files.jobs.get_extraction",
        lambda *a, **k: {
            "jobId": job_id,
            "section": "nodes",
            "items": [],
            "resultRevision": 7,
            "extractionStatus": "partial",
        },
    )
    page = store.result(job_id, section="nodes")
    assert page["resultRevision"] == 7 and page["extractionStatus"] == "partial"
    assert page["executionStatus"] == "queued"


def test_worker_resume_uses_retained_checkpoint(tmp_path, profile, monkeypatch):
    store = JobStore(tmp_path)
    job = submit(store, profile)
    store.recover()
    store.resume(job["jobId"], profile)
    store.claim()
    (store.results / f"{job['jobId']}.sqlite3").touch()
    calls = []

    def resumed(job_id, **kwargs):
        calls.append(job_id)
        return {"extraction": {"status": "partial"}, "validation": {"valid": False}}

    monkeypatch.setattr("document_files.server_worker.resume_extraction", resumed)
    assert run_job(store, job["jobId"], resolver=lambda _: object()) == 0
    assert calls == [job["jobId"]]
    assert store.get(job["jobId"])["status"] == "partial"


def test_admin_config_and_capability_do_not_expose_credentials(tmp_path):
    from document_files.http_server import build_service_from_config

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "storageRoot": str(tmp_path / "state"),
                "profiles": {
                    "cloud": {
                        "type": "chat-completions",
                        "revision": "1",
                        "endpoint": "https://model.invalid/v1",
                        "model": "test",
                        "apiKeyEnv": "PRIVATE_API_ENV",
                    },
                },
            }
        )
    )
    service = build_service_from_config(config)
    text = json.dumps(service.capabilities())
    assert "PRIVATE_API_ENV" not in text and "model.invalid" not in text
    assert service.capabilities()["profiles"][0]["availability"] == "not-probed"
    entry = json.loads(config.read_text())
    entry["profiles"]["cloud"]["api_key"] = "do-not-persist"
    config.write_text(json.dumps(entry))
    with pytest.raises(JobError, match="invalid-server-config"):
        build_service_from_config(config)


def test_resume_budget_is_explicit_persisted_and_not_replayed(tmp_path, profile, monkeypatch):
    from document_files.jobs import validate_budget_grant

    for invalid in (
        {},
        {"maxModelCalls": True},
        {"completionSeconds": 3601},
        {"maxModelCalls": 0},
        {"unknown": 1},
        [],
    ):
        with pytest.raises(JobError, match="invalid-budget-grant"):
            validate_budget_grant(invalid)
    store = JobStore(tmp_path)
    job_id = submit(store, profile)["jobId"]
    store.recover()
    grant = {"maxModelCalls": 2, "completionSeconds": 60}
    with pytest.raises(JobError, match="budget-not-started"):
        store.resume(job_id, profile, additional_budget=grant)
    (store.results / f"{job_id}.sqlite3").touch()
    store.resume(job_id, profile, additional_budget=grant)
    record = store.claim()
    assert json.loads(record["budget_grant"]) == grant
    seen = []

    def resumed(*args, **kwargs):
        seen.append(kwargs["additional_budget"])
        return {"extraction": {"status": "partial"}}

    monkeypatch.setattr("document_files.server_worker.resume_extraction", resumed)
    assert run_job(store, job_id, resolver=lambda _: object()) == 0
    assert seen == [grant]
    store.resume(job_id, profile)
    assert store.claim()["budget_grant"] is None
    with store.connect() as db:
        rows = db.execute("SELECT budget_grant FROM attempts ORDER BY number").fetchall()
    assert [json.loads(r[0]) if r[0] else None for r in rows] == [grant, None]


def test_pack_pin_binds_idempotency_resume_and_worker(tmp_path, profile, monkeypatch):
    active = {"modelId": "hash-one", "runtimeId": "runtime", "recognitionPackId": "ocr"}
    monkeypatch.setattr("document_files.jobs.resolve_profile_identity", lambda _: dict(active))
    monkeypatch.setattr(
        "document_files.server_worker.resolve_profile_identity", lambda _: dict(active)
    )
    store = JobStore(tmp_path)
    job_id = submit(store, profile, idempotency_key="key")["jobId"]
    assert json.loads(store.record(job_id)["pack_identity"]) == active
    store.claim()
    active["recognitionPackId"] = "changed-ocr"
    with pytest.raises(JobError, match="idempotency-conflict"):
        submit(store, profile, idempotency_key="key")
    called = []
    assert run_job(store, job_id, resolver=lambda _: called.append(True)) == 1
    assert not called
    assert store.get(job_id)["error"] == "profile-pack-changed"
    with pytest.raises(JobError, match="profile-pack-changed"):
        store.resume(job_id, profile)


def test_http_resume_grant_is_bounded_and_forwarded(tmp_path, profile, monkeypatch):
    from fastapi.testclient import TestClient

    from document_files.http_server import create_app

    service = JobService(
        JobStore(tmp_path), profiles={"cpu": profile}, runner_factory=WaitingRunner
    )
    seen = []
    monkeypatch.setattr(
        service, "resume", lambda job_id, **kwargs: seen.append(kwargs) or {"jobId": job_id}
    )
    with TestClient(create_app(service, token="x" * 32)) as client:
        headers = {"Authorization": "Bearer " + "x" * 32}
        url = "/v1/jobs/" + "a" * 32 + "/resume"
        grant = {"maxModelCalls": 1}
        assert (
            client.post(url, headers=headers, json={"additionalBudget": grant}).status_code == 202
        )
        assert seen == [{"additional_budget": grant}]
        assert client.post(url, headers=headers).status_code == 202
        assert seen[-1] == {"additional_budget": None}
        assert client.post(url, headers=headers, json={"endpoint": "forbidden"}).status_code == 400
        assert client.post(url, headers=headers, content="x" * 1025).status_code == 400


def test_cloud_recognition_pin_and_observation_pass_through(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from document_files.jobs import resolve_profile_identity

    profile = ModelProfile(
        "cloud",
        "1",
        "chat-completions",
        {
            "endpoint": "https://model.invalid/v1/chat/completions",
            "model": "test",
            "packRoot": str(tmp_path / "packs"),
            "recognitionPackId": "ocr",
        },
    )

    class Store:
        def __init__(self, root):
            pass

        def resolve(self, identifier):
            assert identifier == "ocr"
            return SimpleNamespace(manifest={"kind": "recognition"}, manifest_sha256="f" * 64)

    monkeypatch.setattr("document_files.runtime_packs.PackStore", Store)
    assert resolve_profile_identity(profile) == {"recognitionPackId": "f" * 64}
    store = JobStore(tmp_path / "state")
    job_id = submit(store, profile)["jobId"]
    store.claim()
    observation = SimpleNamespace(identity={"packManifestSha256": "f" * 64})
    called = []

    def extract(*args, **kwargs):
        called.append(kwargs["observation_backend"])
        return {"extraction": {"status": "partial"}}

    monkeypatch.setattr("document_files.server_worker.extract_schema", extract)
    assert (
        run_job(
            store,
            job_id,
            resolver=lambda _: object(),
            observation_resolver=lambda _, **kwargs: observation,
        )
        == 0
    )
    assert called == [observation]


def test_upload_collision_does_not_delete_an_existing_file(tmp_path, profile, monkeypatch):
    from types import SimpleNamespace

    store = JobStore(tmp_path)
    identifier = "b" * 32
    original = store.uploads / f"{identifier}.txt"
    original.write_bytes(b"Existing unrelated file")
    monkeypatch.setattr("document_files.jobs.uuid.uuid4", lambda: SimpleNamespace(hex=identifier))
    with pytest.raises(FileExistsError):
        submit(store, profile)
    assert original.read_bytes() == b"Existing unrelated file"


def test_replaced_upload_directory_is_rejected_before_write(tmp_path, profile):
    store = JobStore(tmp_path / "state")
    outside = tmp_path / "outside"
    outside.mkdir()
    store.uploads.rmdir()
    try:
        store.uploads.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require privileges on this host")
    with pytest.raises(JobError, match="unsafe-storage"):
        submit(store, profile)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"responseFormat": "json_schema", "maxOutputTokens": 2048, "strictSchema": True},
        {"responseFormat": "none", "maxOutputTokens": None, "strictSchema": False},
        {"sampling": {"temperature": 0.0, "seed": 1}},
    ],
)
def test_admin_response_settings_reach_worker_without_requests(tmp_path, monkeypatch, settings):
    from document_files.http_server import build_service_from_config
    from document_files.server_worker import build_model_client

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "storageRoot": str(tmp_path / "state"),
                "profiles": {
                    "cloud": {
                        "type": "chat-completions",
                        "revision": "1",
                        "endpoint": "https://model.invalid/v1",
                        "model": "test",
                        **settings,
                    }
                },
            }
        )
    )
    captured = []
    monkeypatch.setattr(
        "document_files.server_worker.ChatCompletionsClient",
        lambda *args, **kwargs: captured.append(kwargs),
    )
    service = build_service_from_config(path)
    build_model_client(service.profile("cloud"))
    assert captured == [
        {
            "response_format": settings.get("responseFormat", "json_object"),
            "max_output_tokens": settings.get("maxOutputTokens"),
            "strict_schema": settings.get("strictSchema", True),
            "sampling": settings.get("sampling"),
        }
    ]


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"threads": 4},
        {"threads": 4, "threadsBatch": 10},
        {"reasoningBudgetTokens": 1024},
        {"reasoningBudgetTokens": 0},
    ],
)
def test_admin_thread_settings_reach_the_local_pack_client(tmp_path, monkeypatch, settings):
    from document_files.http_server import build_service_from_config
    from document_files.server_worker import build_model_client

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "storageRoot": str(tmp_path / "state"),
                "profiles": {
                    "cpu": {
                        "type": "local-pack",
                        "revision": "1",
                        "packRoot": str(tmp_path / "packs"),
                        "runtimeId": "llama-cpp-cpu",
                        "modelId": "qwen",
                        **settings,
                    }
                },
            }
        )
    )
    captured = []
    monkeypatch.setattr(
        "document_files.interpretation.backends.ManagedPackClient",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    service = build_service_from_config(path)
    build_model_client(service.profile("cpu"))
    assert captured == [
        (
            (str(tmp_path / "packs"), "llama-cpp-cpu", "qwen"),
            {
                "threads": settings.get("threads"),
                "threads_batch": settings.get("threadsBatch"),
                "reasoning_budget_tokens": settings.get("reasoningBudgetTokens"),
            },
        )
    ]
    assert service.profile("cpu").descriptor()["settings"].get("threads") == settings.get("threads")


@pytest.mark.parametrize(
    "setting",
    [
        {"threads": 0},
        {"threads": True},
        {"threads": None},
        {"threads": "4"},
        {"threadsBatch": 1025},
        {"threadsBatch": 1.5},
        *[{"reasoningBudgetTokens": value} for value in (-1, True, None, "1024", 1.5, 3072, 99999)],
        {"sampling": {"temperature": 0.0}},
    ],
)
def test_admin_thread_settings_fail_closed(tmp_path, setting):
    from document_files.http_server import build_service_from_config

    path = tmp_path / "config.json"
    cloud = {"type": "chat-completions", "endpoint": "https://model.invalid/v1", "model": "m"}
    for profile in (
        {"type": "local-pack", "packRoot": "/packs", "runtimeId": "r", "modelId": "m", **setting},
        {**cloud, "threads": 4},
        {**cloud, "reasoningBudgetTokens": 1024},
    ):
        path.write_text(
            json.dumps(
                {
                    "storageRoot": str(tmp_path / "state"),
                    "profiles": {"cpu": {"revision": "1", **profile}},
                }
            )
        )
        with pytest.raises(JobError, match="invalid-server-config"):
            build_service_from_config(path)


@pytest.mark.parametrize(
    "setting",
    [
        {"responseFormat": "unsupported"},
        {"responseFormat": None},
        {"maxOutputTokens": True},
        {"maxOutputTokens": 0},
        {"maxOutputTokens": 1_000_001},
        {"maxOutputTokens": 1.5},
        {"strictSchema": "true"},
        {"strictSchema": 1},
        {"sampling": None},
        {"sampling": {"temperature": 3}},
        {"sampling": {"nucleus": 0.5}},
        {"sampling": [0.0]},
    ],
)
def test_admin_response_settings_fail_closed(tmp_path, setting):
    from document_files.http_server import build_service_from_config

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "storageRoot": str(tmp_path / "state"),
                "profiles": {
                    "cloud": {
                        "type": "chat-completions",
                        "revision": "1",
                        "endpoint": "https://model.invalid/v1",
                        "model": "test",
                        **setting,
                    }
                },
            }
        )
    )
    with pytest.raises(JobError, match="invalid-server-config"):
        build_service_from_config(path)


def test_response_profile_settings_change_job_identity_not_upload_options(tmp_path, profile):
    store = JobStore(tmp_path)
    original = submit(store, profile, idempotency_key="response-settings")
    changed = ModelProfile(
        profile.name,
        profile.revision,
        profile.kind,
        {**profile.settings, "responseFormat": "json_schema", "strictSchema": False},
    )
    with pytest.raises(JobError, match="idempotency-conflict"):
        submit(store, changed, idempotency_key="response-settings")
    replacement = submit(store, changed)
    assert original["profileFingerprint"] != replacement["profileFingerprint"]
    store.recover()
    with pytest.raises(JobError, match="resume-profile-mismatch"):
        store.resume(original["jobId"], changed)
    for options in ({"responseFormat": "none"}, {"maxOutputTokens": 100}, {"strictSchema": False}):
        with pytest.raises(JobError, match="invalid-options"):
            submit(store, profile, options=options)


def test_worker_transport_identity_includes_admin_response_settings(profile):
    from document_files.server_worker import build_model_client

    baseline = build_model_client(profile)
    assert baseline.response_format == "json_object"
    assert baseline.strict_schema is True and baseline.max_output_tokens is None
    seen = {baseline.identity["configurationId"]}
    for settings in (
        {"responseFormat": "json_schema"},
        {"maxOutputTokens": 1024},
        {"strictSchema": False},
    ):
        changed = ModelProfile(
            profile.name, profile.revision, profile.kind, {**profile.settings, **settings}
        )
        identity = build_model_client(changed).identity["configurationId"]
        assert identity not in seen
        seen.add(identity)


def test_worker_refuses_runtime_reasoning_mode_different_from_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace

    expected = {
        "version": "document-files.managed-reasoning.v1",
        "mode": "thinking",
        "budgetTokens": 1024,
        "budgetScope": "per-block",
    }
    pinned = {"runtimeId": "runtime-sha", "modelId": "model-sha", "reasoning": expected}
    monkeypatch.setattr("document_files.jobs.resolve_profile_identity", lambda _: pinned)
    monkeypatch.setattr("document_files.server_worker.resolve_profile_identity", lambda _: pinned)
    profile = ModelProfile("cpu", "1", "local-pack", {"reasoningBudgetTokens": 1024})
    store = JobStore(tmp_path)
    job_id = submit(store, profile)["jobId"]
    store.claim()
    closed = []
    client = SimpleNamespace(
        identity={"runtimeManifestSha256": "runtime-sha", "modelManifestSha256": "model-sha"},
        close=lambda: closed.append(True),
    )
    assert run_job(store, job_id, resolver=lambda _: client) == 1
    assert store.get(job_id)["error"] == "profile-pack-changed"
    assert closed == [True]
