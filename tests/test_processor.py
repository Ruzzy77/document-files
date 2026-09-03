from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from document_files.processor import (
    DESCRIPTOR_SCHEMA_VERSION,
    _materialized_input,
    describe_all,
    extract_complete,
)


def test_descriptor_reports_one_public_route_per_supported_format() -> None:
    described = describe_all()
    assert described["schema_version"] == DESCRIPTOR_SCHEMA_VERSION
    assert set(described["formats"]) == {
        "docx",
        "htm",
        "html",
        "hwp",
        "hwpx",
        "markdown",
        "md",
        "pdf",
        "pptx",
        "txt",
        "xlsx",
    }
    for format_id, route in described["formats"].items():
        assert route["reanalysis_generation"] == 1
        assert route["descriptor"]["adapter_id"] == f"document-files.process.{format_id}"
        assert route["descriptor"]["adapter_version"].startswith("1.0.0+process.")
        assert route["descriptor"]["config_hash"]
        assert len(route["config"]["processor_implementation_sha256"]) == 64
        assert route["config"]["route"]["adapter_id"].startswith("document-files.")


def test_text_extraction_returns_structural_coverage(tmp_path: Path) -> None:
    source = tmp_path / "sample.txt"
    source.write_text("첫 문단\n\n둘째 문단", encoding="utf-8")
    result = extract_complete(source, format_id="txt")
    assert result.completeness == "complete"
    assert [unit.content for unit in result.units] == ["첫 문단", "둘째 문단"]
    assert result.descriptor.adapter_id.startswith("document-files.")


def test_process_jsonl_uses_read_only_descriptor(tmp_path: Path) -> None:
    source = tmp_path / "sample.md"
    source.write_text("# 제목\n\n본문", encoding="utf-8")
    route = describe_all()["formats"]["md"]
    descriptor = route["descriptor"]
    fd = os.open(source, os.O_RDONLY)
    try:
        request = {
            "schema_version": "document-files.extraction-request.v2",
            "operation": "extract",
            "adapter": {
                "adapter_id": descriptor["adapter_id"],
                "adapter_version": descriptor["adapter_version"],
                "config_hash": descriptor["config_hash"],
            },
            "input": {
                "kind": "read_only_file_descriptor",
                "file_descriptor": fd,
                "path": f"/dev/fd/{fd}",
                "format_id": "md",
            },
            "config": route["config"],
            "budgets": {},
        }
        completed = subprocess.run(
            [
                str(Path(__file__).parents[1] / "launchers" / "document-files"),
                "process",
            ],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n",
            capture_output=True,
            check=True,
            pass_fds=(fd,),
        )
    finally:
        os.close(fd)
    result = json.loads(completed.stdout)
    assert result["schema_version"] == "document-files.extraction-result.v2"
    assert result["completeness"] == "complete"
    assert result["coverage"] == {
        "text_content": "complete",
        "structure": "complete",
        "visual_content": "not_applicable",
        "reading_order": "complete",
    }
    assert [unit["unit_type"] for unit in result["units"]] == ["heading", "paragraph"]


def test_process_materializes_a_stable_reopenable_input(tmp_path: Path) -> None:
    source = tmp_path / "source.hwp"
    source.write_bytes(b"0123456789" * 100)
    descriptor = os.open(source, os.O_RDONLY)
    try:
        os.lseek(descriptor, 700, os.SEEK_SET)
        with _materialized_input(descriptor, "hwp") as private_path:
            assert private_path.suffix == ".hwp"
            assert private_path.read_bytes() == source.read_bytes()
            with private_path.open("rb") as first, private_path.open("rb") as second:
                assert first.read(8) == b"01234567"
                assert second.read(8) == b"01234567"
        assert not private_path.exists()
    finally:
        os.close(descriptor)


def test_plugin_contains_no_removed_plugin_identifiers() -> None:
    root = Path(__file__).parents[1]
    forbidden = (
        "hancom" + "-files",
        "hancom" + "_files",
        "hancom" + "_",
        "Legacy" + " alias",
    )
    offenders: list[str] = []
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or ".venv" in path.parts
            or "__pycache__" in path.parts
            or path.suffix.lower() in {".png", ".svg", ".lock"}
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(value in text for value in forbidden):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
