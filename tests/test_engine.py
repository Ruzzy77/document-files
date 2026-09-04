from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from document_files.engine import (
    EDIT_PLAN_SCHEMA_VERSION,
    DocumentFilesError,
    create_hwpx,
    edit_hwpx,
    inspect_file,
    render_hwpx_preview,
    verify_hwpx,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan() -> dict:
    return {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "업무 계획",
        "metadata": {"organization": "예시 기관", "date": "2026-07-27"},
        "blocks": [
            {"type": "heading", "level": 1, "text": "개요"},
            {"type": "paragraph", "text": "초안 내용"},
            {
                "type": "table",
                "caption": "예산",
                "columns": [
                    {"key": "item", "label": "항목", "widthWeight": 2},
                    {"key": "amount", "label": "금액", "widthWeight": 1},
                ],
                "rows": [{"item": "장비", "amount": "1,000원"}],
            },
        ],
        "qualityGates": {
            "validatePackage": True,
            "validateDocument": True,
            "reopen": True,
            "requiredText": ["업무 계획", "초안 내용", "예산"],
            "visualReviewRequired": True,
        },
    }


def test_create_inspect_edit_verify_and_preview(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    created = create_hwpx(source, plan=_plan())
    assert created["ok"] is True
    before = _digest(source)

    inspected = inspect_file(source)
    assert inspected["format"] == "hwpx"
    assert "초안 내용" in inspected["text"]
    assert inspected["tableMap"]["tables"]

    edit_plan = {
        "schemaVersion": EDIT_PLAN_SCHEMA_VERSION,
        "textReplacements": [
            {
                "find": "초안 내용",
                "replace": "확정 내용",
                "expectedCount": 1,
            }
        ],
        "tableCells": [
            {
                "tableIndex": 1,
                "row": 1,
                "col": 1,
                "text": "2,000원",
                "expectedOldText": "1,000원",
            }
        ],
    }
    dry_run = edit_hwpx(source, plan=edit_plan, dry_run=True)
    assert dry_run["dryRun"] is True
    assert dry_run["output"] is None

    output = tmp_path / "output.hwpx"
    edited = edit_hwpx(
        source,
        plan=edit_plan,
        output_path=output,
        dry_run=False,
    )
    assert edited["ok"] is True
    assert output.exists()
    assert _digest(source) == before

    verified = verify_hwpx(
        output,
        reference_path=source,
        expected_text=["확정 내용", "2,000원"],
        forbidden_text=["초안 내용", "1,000원"],
    )
    assert verified["ok"] is True
    assert verified["comparison"]["tableGeometryPreserved"] is True

    preview_path = tmp_path / "preview.html"
    preview = render_hwpx_preview(output, preview_path)
    assert preview["ok"] is True
    assert preview["nativeRenderChecked"] is False
    assert "<html" in preview_path.read_text(encoding="utf-8")


def test_in_place_edit_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    create_hwpx(source, plan=_plan())
    with pytest.raises(DocumentFilesError) as exc_info:
        edit_hwpx(
            source,
            plan={
                "schemaVersion": EDIT_PLAN_SCHEMA_VERSION,
                "textReplacements": [
                    {
                        "find": "초안 내용",
                        "replace": "확정 내용",
                        "expectedCount": 1,
                    }
                ],
            },
            output_path=source,
            dry_run=False,
        )
    assert exc_info.value.code == "in-place-edit-refused"


def test_stale_table_value_is_refused_without_output(tmp_path: Path) -> None:
    source = tmp_path / "source.hwpx"
    output = tmp_path / "output.hwpx"
    create_hwpx(source, plan=_plan())
    with pytest.raises(DocumentFilesError) as exc_info:
        edit_hwpx(
            source,
            plan={
                "schemaVersion": EDIT_PLAN_SCHEMA_VERSION,
                "tableCells": [
                    {
                        "tableIndex": 1,
                        "row": 1,
                        "col": 1,
                        "text": "2,000원",
                        "expectedOldText": "다른 값",
                    }
                ],
            },
            output_path=output,
            dry_run=False,
        )
    assert exc_info.value.code == "stale-edit-plan"
    assert not output.exists()
