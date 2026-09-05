from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path

import pytest

import document_files.engine as engine
from document_files.engine import (
    DocumentFilesError,
    capabilities,
    convert_file,
    create_hwpx,
    extract_file,
    extract_structure,
    inspect_file,
    render_file,
)
from document_files.mcp_server import create_server
from document_files.rhwp_backend import backend_status


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> dict:
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "백그라운드 문서",
        "blocks": [
            {"type": "heading", "level": 1, "text": "개요"},
            {"type": "paragraph", "text": "화면을 열지 않고 처리합니다."},
            {
                "type": "table",
                "columns": [
                    {"key": "item", "label": "항목"},
                    {"key": "value", "label": "값"},
                ],
                "rows": [{"item": "처리 방식", "value": "headless"}],
            },
        ],
    }


def _require_rhwp() -> dict:
    status = backend_status()
    if not status.get("available"):
        pytest.skip("pinned rhwp backend is not provisioned")
    return status


def test_capabilities_are_explicitly_headless() -> None:
    result = capabilities()
    assert result["pluginVersion"] == "1.6.0"
    assert result["headless"] is True
    assert result["nativeAppAutomation"] is False
    assert result["runtimeNetworkUsed"] is False
    assert result["nativeRenderChecked"] is False
    assert result["artifactFormats"]["hwp"]["edit"] is False
    assert result["artifactFormats"]["hwpx"]["convertToHwp"] is False
    assert result["extraction"]["coverageReported"] is True
    assert result["extraction"]["structuredSchemaVersion"] == (
        "document-files.structured-extraction.v1"
    )
    assert result["extraction"]["sourceDeclaredSemanticsOnly"] is True
    assert result["extraction"]["pathIndependentInput"] is True
    assert result["extraction"]["replaceableBackend"] is True
    assert result["extraction"]["singlePassBoundedAnalysis"] is True
    assert set(result["extraction"]["formats"]) == {
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
    assert result["backends"]["pythonHwpx"] == {
        "available": True,
        "expectedVersion": "6.3.0",
        "version": "6.3.0",
        "expectedAutomationVersion": "7.0.3",
        "automationVersion": "7.0.3",
        "reason": None,
    }
    assert result["outputPolicy"]["dryRunDefaultForEdits"] is False
    assert result["outputPolicy"]["preflightAndReopenInWrite"] is True
    assert result["outputPolicy"]["dependencyMismatchFailsClosed"] is True


def test_hwpx_version_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_version = engine._package_version

    def mismatched_version(distribution: str) -> str | None:
        if distribution == "python-hwpx":
            return "0.0.0"
        return package_version(distribution)

    monkeypatch.setattr(engine, "_package_version", mismatched_version)
    status = capabilities()
    assert status["backends"]["pythonHwpx"]["available"] is False
    assert status["artifactFormats"]["hwpx"]["create"] is False

    with pytest.raises(DocumentFilesError) as exc_info:
        create_hwpx(tmp_path / "blocked.hwpx", plan=_plan())
    assert exc_info.value.code == "backend-version-mismatch"
    assert not (tmp_path / "blocked.hwpx").exists()


def test_mcp_surface_is_small_and_headless(tmp_path: Path) -> None:
    server = create_server()
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "document_capabilities",
        "document_inspect_file",
        "document_extract_file",
        "document_extract_structure",
        "document_convert_file",
        "document_create_hwpx",
        "document_edit_hwpx",
        "document_verify_hwpx",
        "document_render_file",
    }
    assert by_name["document_capabilities"].annotations.read_only_hint is True
    assert by_name["document_extract_file"].annotations.read_only_hint is True
    assert by_name["document_extract_structure"].annotations.read_only_hint is True
    assert "computer control" in by_name["document_render_file"].description
    for tool in by_name.values():
        schema = tool.output_schema
        assert schema.get("additionalProperties") is not True
        assert {"ok", "result", "error"}.issubset(schema["properties"])
        assert "$defs" in schema

    source = tmp_path / "source.hwpx"
    create_hwpx(source, plan=_plan())
    response = asyncio.run(server.call_tool("document_inspect_file", {"path": str(source)}))
    assert response.is_error is False
    assert response.structured_content["ok"] is True
    inspected = response.structured_content["result"]
    assert inspected["schemaVersion"] == "document-files.inspect.v1"
    assert inspected["ok"] is True
    assert inspected["source"] == inspected["file"]
    assert "화면을 열지 않고 처리합니다." in inspected["text"]


def test_headless_hwp_read_convert_and_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _require_rhwp()
    source_hwpx = tmp_path / "source.hwpx"
    binary_hwp = tmp_path / "source.hwp"
    create_hwpx(source_hwpx, plan=_plan())

    subprocess.run(  # noqa: S603
        [status["executable"], "convert", str(source_hwpx), str(binary_hwp)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        text=True,
    )
    source_digest = _digest(binary_hwp)

    inspected = inspect_file(binary_hwp)
    assert inspected["format"] == "hwp5"
    assert inspected["contentAccess"]["ok"] is True
    assert "화면을 열지 않고 처리합니다." in inspected["text"]
    assert inspected["tableMap"]["tables"]

    server = create_server()
    response = asyncio.run(server.call_tool("document_inspect_file", {"path": str(binary_hwp)}))
    assert response.is_error is False
    assert response.structured_content["ok"] is True
    assert response.structured_content["result"]["source"] == inspected["file"]
    assert response.structured_content["result"]["engine"] == inspected["engine"]
    with monkeypatch.context() as patch:
        patch.setattr(engine, "backend_status", lambda: {"available": False})
        patch.setattr(engine, "HwpxDocument", None)
        patch.setattr(engine, "TextExtractor", None)
        patch.setattr(
            "document_files.extraction_rhwp.RhwpPageTextAdapter.extract",
            lambda *_a, **_k: pytest.fail("Unexpected rhwp fallback"),
        )
        response = asyncio.run(server.call_tool("document_inspect_file", {"path": str(binary_hwp)}))
        extracted = extract_file(binary_hwp, output_format="markdown")
        structured = extract_structure(binary_hwp)
        assert response.structured_content["ok"] is True
        without_optional_backends = response.structured_content["result"]
        assert without_optional_backends["source"] == inspected["file"]
        assert without_optional_backends["contentAccess"]["ok"] is True
        assert without_optional_backends["engine"] == inspected["engine"]
        assert without_optional_backends["text"] == inspected["text"]
        assert extracted["manifestHash"] == structured["manifestHash"] == inspected["manifestHash"]
        assert extracted["coverageProfile"] == structured["coverage"]
        assert extracted["coverage"] == structured["completeness"]
        assert "headless" in extracted["content"]
        assert "### Table" in extracted["content"]
        assert extracted["representation"] == "source-structure-markdown"
        assert extracted["layoutPreserved"] is False

    converted_path = tmp_path / "converted.hwpx"
    with pytest.raises(DocumentFilesError) as exc_info:
        convert_file(binary_hwp, converted_path)
    assert exc_info.value.code == "conversion-loss-detected"
    converted = convert_file(binary_hwp, converted_path, allow_lossy=True)
    assert converted["validation"]["packageAndReopen"]["ok"] is True
    assert converted["validation"]["pageCountPreserved"] is True
    assert converted_path.exists()

    pdf_path = tmp_path / "rendered.pdf"
    pdf = render_file(binary_hwp, pdf_path, output_format="pdf")
    assert pdf["nativeRenderChecked"] is False
    assert pdf_path.read_bytes().startswith(b"%PDF-")

    svg_dir = tmp_path / "svg-pages"
    svg = render_file(binary_hwp, svg_dir, output_format="svg", page=0)
    assert svg["output"]["fileCount"] == 1
    assert b"<svg" in next(svg_dir.glob("*.svg")).read_bytes()[:4_096]
    assert _digest(binary_hwp) == source_digest


def test_svg_render_refuses_existing_directory(tmp_path: Path) -> None:
    _require_rhwp()
    source = tmp_path / "source.hwpx"
    create_hwpx(source, plan=_plan())
    existing = tmp_path / "svg-pages"
    existing.mkdir()
    with pytest.raises(DocumentFilesError) as exc_info:
        render_file(source, existing, output_format="svg")
    assert exc_info.value.code == "output-exists"
