"""Storage lifecycle and CLI/MCP safety regressions, without a remote model."""

import json
import os
import sqlite3
from contextlib import closing

import pytest

from document_files import api
from document_files.cli import _parser, _run
from document_files.engine import DocumentFilesError
from document_files.interpretation import workflow


class Model:
    identity = {"model": "test", "configurationId": "test-only"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = tmp_path / "document.txt"
    source.write_text("value: 123", encoding="utf-8")
    root = tmp_path / "private"
    monkeypatch.setenv("DOCUMENT_FILES_STORAGE_DIR", str(root))
    return source, root


def result(job):
    return {
        "jobId": job.job_id,
        "document": {"nodes": {}},
        "issues": [],
        "data": {"value": "123"},
        "extraction": {"status": "partial"},
    }


def test_checkpoint_read_resume_and_no_long_transaction(setup, monkeypatch):
    source, root = setup
    calls = []

    def engine(job, stream, *, checkpoint, restore=None, **kwargs):
        calls.append(restore)
        body = result(job)
        checkpoint({"result": body, "stage": "review"})
        assert workflow.get_extraction("one")["data"] == {"value": "123"}
        # A separate writer works while the model phase owns the job lock.
        with closing(sqlite3.connect(root / "one.sqlite3", timeout=0)) as db, db:
            db.execute("UPDATE execution SET status='running'")
        with pytest.raises(DocumentFilesError, match="already running"):
            workflow.delete_extraction("one")
        if restore is None:
            raise KeyboardInterrupt()
        return body

    monkeypatch.setattr(workflow, "extract_schema_from_stream", engine)
    with pytest.raises(KeyboardInterrupt):
        workflow.extract_schema(str(source), request_id="one", model_client=Model())
    assert workflow.get_extraction("one")["data"]
    resumed = workflow.resume_extraction("one", model_client=Model())
    assert resumed["data"] == {"value": "123"}
    assert calls[1]["stage"] == "review"
    assert workflow.extract_schema(str(source), request_id="one", model_client=Model()) == resumed
    assert len(calls) == 2
    assert workflow.delete_extraction("one")["deleted"] is True
    assert workflow.delete_extraction("one")["deleted"] is False
    assert source.read_text() == "value: 123"


def test_exact_resume_and_retention(setup, monkeypatch):
    source, root = setup
    monkeypatch.setattr(
        workflow, "extract_schema_from_stream", lambda job, stream, **kw: result(job)
    )
    workflow.extract_schema(str(source), request_id="transient", retain=False, model_client=Model())
    assert not root.exists()
    workflow.extract_schema(str(source), request_id="kept", model_client=Model())
    source.write_text("different", encoding="utf-8")
    with pytest.raises(DocumentFilesError, match="has changed"):
        workflow.resume_extraction("kept", model_client=Model())
    with pytest.raises(DocumentFilesError, match="different inputs"):
        workflow.extract_schema(str(source), request_id="kept", model_client=Model())
    if os.name != "nt":
        assert (root.stat().st_mode & 0o777) == 0o700
        assert ((root / "kept.sqlite3").stat().st_mode & 0o777) == 0o600


def test_legacy_result_untouched_and_safe_paths(setup):
    _, root = setup
    root.mkdir()
    with closing(sqlite3.connect(root / "legacy.sqlite3")) as db, db:
        db.execute("CREATE TABLE result(fingerprint TEXT, body TEXT)")
        db.execute("INSERT INTO result VALUES ('old',?)", (json.dumps({"data": [1]}),))
    before = (root / "legacy.sqlite3").read_bytes()
    assert workflow.get_extraction("legacy") == {"data": [1]}
    with pytest.raises(DocumentFilesError, match="Legacy"):
        workflow.resume_extraction("legacy")
    assert (root / "legacy.sqlite3").read_bytes() == before
    with pytest.raises(DocumentFilesError, match="identifier"):
        workflow.delete_extraction("../legacy")


def test_diagnostics_redacts_and_does_not_create_storage(setup, monkeypatch):
    _, root = setup
    monkeypatch.setenv("DOCUMENT_FILES_AI_ENDPOINT", "https://example.test/api")
    monkeypatch.setenv("DOCUMENT_FILES_AI_MODEL", "model")
    monkeypatch.setenv("DOCUMENT_FILES_AI_API_KEY", "secret-should-not-appear")
    response = _run(_parser().parse_args(["diagnose"]))
    encoded = json.dumps(response)
    assert "secret-should-not-appear" not in encoded
    assert "example.test" not in encoded
    assert not response["networkUsed"]
    assert not root.exists()
    assert api.AnalysisJob is not None


def test_symlink_database_refused(setup):
    source, root = setup
    root.mkdir()
    try:
        (root / "bad.sqlite3").symlink_to(source)
    except OSError:
        pytest.skip("Symlink creation is not available")
    with pytest.raises(DocumentFilesError, match="symlink"):
        workflow.delete_extraction("bad")
    assert source.is_file()


def test_safe_call_redacts_unexpected_error():
    from document_files.mcp_server import FlexibleResult, SchemaExtractionResponse, _safe_call

    def fail():
        raise ValueError("private document and secret")

    response = _safe_call(fail, SchemaExtractionResponse, FlexibleResult)
    assert "private document" not in response.model_dump_json()


def test_public_result_schema_matches_real_partial(setup, monkeypatch):
    from jsonschema import Draft202012Validator

    source, _ = setup
    monkeypatch.delenv("DOCUMENT_FILES_AI_ENDPOINT", raising=False)
    monkeypatch.delenv("DOCUMENT_FILES_AI_MODEL", raising=False)
    output = api.extract_schema(str(source), retain=False)
    schema = api.extraction_result_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(output)


def test_changed_model_and_prompt_resume_refused(setup, monkeypatch):
    source, _ = setup
    monkeypatch.setattr(
        workflow, "extract_schema_from_stream", lambda job, stream, **kw: result(job)
    )
    workflow.extract_schema(str(source), request_id="kept", model_client=Model())

    class Changed:
        identity = {"model": "different", "configurationId": "test-only"}

    with pytest.raises(DocumentFilesError, match="has changed"):
        workflow.resume_extraction("kept", model_client=Changed())
    monkeypatch.setattr(workflow, "PROMPT_VERSION", "different-version")
    with pytest.raises(DocumentFilesError, match="has changed"):
        workflow.resume_extraction("kept", model_client=Model())


def test_public_result_schema_matches_validated_output():
    from jsonschema import Draft202012Validator
    from test_schema_extraction import ScriptedModel, run

    output = run(ScriptedModel())
    assert output["validation"]["valid"]
    Draft202012Validator(api.extraction_result_schema()).validate(output)
    assert "Proposal" not in api.__all__
