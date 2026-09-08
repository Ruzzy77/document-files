#!/usr/bin/env python3
"""Run explicitly configured real-model qualification on public synthetic documents.

This produces evidence for review, not an automatic semantic-accuracy certificate.
No model endpoint, credentials, or private document paths enter the evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from document_files import __version__
from document_files.api import ChatCompletionsClient, extract_schema
from document_files.engine import create_hwpx

ROOT = Path(__file__).resolve().parents[1]
LINES = [
    "Reference measurements / 참조 측정 기록",
    "All lengths are in mm. The note applies to sample B only.",
    "Sample A: length 0012.40; count 0; enabled false.",
    "Sample B: length 15.60; count blank; enabled true.",
    "Note B: exclude from the total if the inspection status is pending.",
    "Inspection status B: pending. No status is supplied for sample A.",
]


def fixtures(root: Path) -> list[Path]:
    from docx import Document
    from openpyxl import Workbook

    text = "\n".join(LINES) + "\n"
    for suffix in ("txt", "md"):
        (root / f"measurements.{suffix}").write_text(text, encoding="utf-8")
    (root / "measurements.html").write_text(
        "<!doctype html><html><body>"
        + "".join(f"<p>{line}</p>" for line in LINES)
        + "</body></html>",
        encoding="utf-8",
    )
    doc = Document()
    for line in LINES:
        doc.add_paragraph(line)
    doc.save(root / "measurements.docx")
    book = Workbook()
    sheet = book.active
    for line in LINES:
        sheet.append([line])
    sheet.append(["Native decimal", 12.4, "=B7*2"])
    sheet["B7"].number_format = "0000.00"
    book.save(root / "measurements.xlsx")
    create_hwpx(
        root / "measurements.hwpx",
        plan={
            "schemaVersion": "hwpx.document_plan.v1",
            "title": "Reference measurements",
            "blocks": [{"type": "paragraph", "text": line} for line in LINES],
        },
    )
    return sorted(root.glob("measurements.*"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", required=True, choices=("local", "cloud"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--holdout",
        type=Path,
        help="Independent public case manifest; never use private work documents",
    )
    args = parser.parse_args()
    client = (
        ChatCompletionsClient.from_environment()
    )  # Stop before producing evidence if not configured.
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT)
    )
    records = []
    with tempfile.TemporaryDirectory(prefix="document-files-qualification-") as directory:
        inputs = fixtures(Path(directory))
        cases = [{"path": path, "id": path.suffix[1:], "holdout": False} for path in inputs]
        if args.holdout:
            manifest = json.loads(args.holdout.read_text(encoding="utf-8"))
            if manifest.get("publicData") is not True:
                raise ValueError("Holdout cases must be explicitly declared public data")
            for case in manifest["cases"]:
                cases.append(
                    {
                        "path": (args.holdout.parent / case["path"]).resolve(),
                        "id": "holdout-" + str(len(cases)),
                        "holdout": True,
                    }
                )
        for case in cases:
            result = extract_schema(
                str(case["path"]),
                model_client=client,
                retain=False,
                options={"reconstructionContext": False},
            )
            result_name = f"{args.kind}-{case['id']}.json"
            serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            (output / result_name).write_text(serialized, encoding="utf-8")
            records.append(
                {
                    "id": case["id"],
                    "format": case["path"].suffix.lstrip("."),
                    "holdout": case["holdout"],
                    "inputSha256": hashlib.sha256(case["path"].read_bytes()).hexdigest(),
                    "resultPath": result_name,
                    "resultSha256": hashlib.sha256(serialized.encode()).hexdigest(),
                    "extractionStatus": result["extraction"]["status"],
                    "structurallyValid": result["validation"]["valid"],
                    "semanticReview": {
                        "status": "pending",
                        "checks": [
                            "Both samples and their values are present without reassignment",
                            "Common mm unit applies to both lengths; Note B only to sample B",
                            "Zero, false, blank and absent status remain distinct",
                            "0012.40 retains its source spelling; "
                            "no invented precision or formula cache",
                        ],
                    },
                }
            )
    report = {
        "schemaVersion": "document-files.model-qualification.v1",
        "version": __version__,
        "sourceCommit": source_commit,
        "dirtySource": dirty,
        "endpointKind": args.kind,
        "model": client.identity,
        "passed": False,
        "cases": records,
        "reason": (
            "Independent semantic review and holdout coverage "
            "must be completed before qualification."
        ),
    }
    name = output / f"{args.kind}_model.json"
    name.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence written: {name}; semantic qualification remains pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
