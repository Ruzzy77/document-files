from __future__ import annotations

import json
from io import BytesIO

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
    wire_job = AnalysisJob.from_dict(json.loads(json.dumps(job.to_dict())))

    direct = LocalAnalyzerBackend().analyze(wire_job, SequentialBytes(content))
    restored = AnalysisResult.from_dict(
        json.loads(json.dumps(direct.to_dict())),
        expected_job=job,
    )

    assert restored.input == job.input
    assert restored.extraction.manifest_hash == direct.extraction.manifest_hash
    assert restored.to_dict() == direct.to_dict()


def test_local_backend_rejects_bytes_that_do_not_match_job_identity() -> None:
    content = b"declared"

    with pytest.raises(ExtractionError, match="do not match"):
        LocalAnalyzerBackend().analyze(_job(content), SequentialBytes(b"different"))
