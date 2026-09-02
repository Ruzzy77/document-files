from __future__ import annotations

import json
from io import BytesIO
from typing import BinaryIO

import pytest

from document_files.analysis import (
    AnalysisInput,
    AnalysisJob,
    AnalysisResult,
    LocalAnalyzerBackend,
)
from document_files.engine import extract_structure_from_stream
from document_files.extraction_errors import ExtractionError


class SequentialBytes:
    """A read-only stream without seek, fileno, or a local path."""

    def __init__(self, content: bytes) -> None:
        self._stream = BytesIO(content)

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)


class SerializedBackend:
    """Remote-like test backend that crosses the public JSON boundary."""

    def __init__(self) -> None:
        self.job_payload: dict | None = None
        self.result_manifest_hash: str | None = None

    def analyze(self, job: AnalysisJob, source: BinaryIO) -> AnalysisResult:
        self.job_payload = json.loads(json.dumps(job.to_dict()))
        wire_job = AnalysisJob.from_dict(self.job_payload)
        direct = LocalAnalyzerBackend().analyze(wire_job, SequentialBytes(source.read()))
        self.result_manifest_hash = direct.extraction.manifest_hash
        return AnalysisResult.from_dict(
            json.loads(json.dumps(direct.to_dict())),
            expected_job=wire_job,
        )


def _job(content: bytes) -> AnalysisJob:
    return AnalysisJob(
        job_id="portable:test-1",
        input=AnalysisInput.from_bytes(content, format_id="md"),
    )


def test_stream_api_has_no_public_path_dependency() -> None:
    content = "# 제목\n\n본문".encode()

    result = extract_structure_from_stream(_job(content), SequentialBytes(content))

    assert [unit["text"] for unit in result["units"]] == ["제목", "본문"]
    assert result["analysis"]["input"]["sha256"] == _job(content).input.sha256
    assert "source" not in result
    assert "path" not in json.dumps(result["analysis"], ensure_ascii=False)


def test_analysis_job_and_result_survive_a_serialized_backend_boundary() -> None:
    content = b"first\n\nsecond"
    job = AnalysisJob(
        job_id="portable:test-2",
        input=AnalysisInput.from_bytes(content, format_id="txt"),
    )
    backend = SerializedBackend()

    result = extract_structure_from_stream(
        job,
        SequentialBytes(content),
        backend=backend,
    )

    assert backend.job_payload == job.to_dict()
    assert "path" not in json.dumps(backend.job_payload)
    assert result["analysis"]["jobId"] == job.job_id
    assert result["manifestHash"] == backend.result_manifest_hash
    assert [unit["text"] for unit in result["units"]] == ["first", "second"]


def test_local_backend_rejects_bytes_that_do_not_match_job_identity() -> None:
    content = b"declared"

    with pytest.raises(ExtractionError, match="do not match"):
        LocalAnalyzerBackend().analyze(_job(content), SequentialBytes(b"different"))
