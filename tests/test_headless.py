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
    assert result["pluginVersion"] == "1.1.2"
    assert result["headless"] is True
    assert result["nativeAppAutomation"] is False
    assert result["runtimeNetworkUsed"] is False
    assert result["nativeRenderChecked"] is False
    assert result["artifactFormats"]["hwp"]["edit"] is False
    assert result["artifactFormats"]["hwpx"]["convertToHwp"] is False
    assert result["extraction"]["coverageReported"] is True
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


def test_mcp_surface_is_small_and_headless() -> None:
    tools = asyncio.run(create_server().list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "document_capabilities",
        "document_inspect_file",
        "document_extract_file",
        "document_convert_file",
        "document_create_hwpx",
        "document_edit_hwpx",
        "document_verify_hwpx",
        "document_render_file",
    }
    assert by_name["document_capabilities"].annotations.readOnlyHint is True
    assert by_name["document_extract_file"].annotations.readOnlyHint is True
    assert "computer control" in by_name["document_render_file"].description


def test_headless_hwp_read_convert_and_render(tmp_path: Path) -> None:
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

    extracted = extract_file(binary_hwp, output_format="markdown")
    assert extracted["engine"] == {"name": "rhwp", "version": "0.8.2"}
    assert "headless" in extracted["content"]

    converted_path = tmp_path / "converted.hwpx"
    converted = convert_file(binary_hwp, converted_path)
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
