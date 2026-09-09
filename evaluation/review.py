#!/usr/bin/env python3
"""Seal an independent semantic review without modifying inference evidence.

This validates the review's completeness and immutable bindings, not its truth.
A qualified reviewer must compare the actual inputs, specifications and results.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.run import checked_reference, read_json, read_specification, sha256, write_json


def seal_review(report_path: Path, decisions_path: Path, output: Path) -> dict:
    report_hash = sha256(report_path)
    report = read_json(report_path)
    decisions = read_json(decisions_path)
    decisions_hash = sha256(decisions_path)
    if (
        not isinstance(report, dict)
        or not isinstance(decisions, dict)
        or report.get("schemaVersion") != "document-files.model-qualification.v2"
        or report.get("passed") is not False
        or decisions.get("schemaVersion") != "document-files.review-decisions.v1"
        or decisions.get("sourceReportSha256") != report_hash
    ):
        raise ValueError("Review must bind an unchanged pending v2 inference report")
    reviewer = decisions.get("reviewer")
    method = decisions.get("method")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or decisions.get("independent") is not True
        or method not in ("human-ground-truth", "independent-ground-truth")
    ):
        raise ValueError("An identified independent ground-truth reviewer is required")
    report_cases, decision_cases = report.get("cases"), decisions.get("cases")
    if (
        not isinstance(report_cases, list)
        or not report_cases
        or not isinstance(decision_cases, list)
    ):
        raise ValueError("Nonempty case review required")
    case_ids = [case["id"] for case in report_cases]
    decision_ids = [case["id"] for case in decision_cases]
    if (
        len(set(case_ids)) != len(case_ids)
        or len(set(decision_ids)) != len(decision_ids)
        or set(case_ids) != set(decision_ids)
    ):
        raise ValueError("Review must cover each exact case ID once")
    by_id = {case["id"]: case for case in decision_cases}
    reviewed = []
    all_passed = True
    for case in report_cases:
        if case.get("semanticReview") != {"status": "pending"}:
            raise ValueError("Inference report must remain pending and unmodified")
        # The runner freezes source bytes before inference, so the reviewer checks
        # the same document even if the external manifest's input later changes.
        checked_reference(
            report_path.parent,
            {
                "path": case["inputPath"],
                "sha256": case["inputSha256"],
            },
        )
        result_path = checked_reference(
            report_path.parent,
            {
                "path": case["resultPath"],
                "sha256": case["resultSha256"],
            },
        )
        result = read_json(result_path)
        specification = case.get("reviewSpecification")
        if specification is None:
            raise ValueError("Each reviewed case needs a pre-inference review specification")
        spec_path = checked_reference(report_path.parent, specification)
        spec = read_specification(spec_path)
        if "recognitionCostReceipt" in case:
            checked_reference(report_path.parent, case["recognitionCostReceipt"])
        decision = by_id[case["id"]]
        bindings = {
            "inputSha256": case["inputSha256"],
            "resultSha256": case["resultSha256"],
            "reviewSpecificationSha256": specification["sha256"],
        }
        if any(decision.get(key) != value for key, value in bindings.items()):
            raise ValueError("Case decision binds stale input, result or specification")
        criteria = decision.get("criteria")
        findings = decision.get("findings")
        if (
            not isinstance(criteria, dict)
            or set(criteria) != {criterion["id"] for criterion in spec["criteria"]}
            or any(type(value) is not bool for value in criteria.values())
            or not isinstance(findings, list)
            or not findings
            or any(not isinstance(value, str) or not value.strip() for value in findings)
        ):
            raise ValueError(
                "Every specified criterion needs a boolean judgment and written findings"
            )
        if result.get("source", {}).get("sha256") != case["inputSha256"]:
            raise ValueError("Result is not linked to the reviewed input")
        result_complete = (
            case.get("extractionStatus") == "complete"
            and case.get("structurallyValid") is True
            and result.get("extraction", {}).get("status") == "complete"
            and result.get("validation", {}).get("valid") is True
        )
        passed = result_complete and all(criteria.values())
        all_passed = all_passed and passed
        reviewed.append(
            {
                "id": case["id"],
                **bindings,
                "status": "passed" if passed else "failed",
                "criteria": criteria,
                "findings": findings,
            }
        )
    # Recheck after validation, before sealing. Existing report/result/spec files
    # are never rewritten; amendments need another independent receipt.
    if sha256(report_path) != report_hash or sha256(decisions_path) != decisions_hash:
        raise ValueError("Report changed while reviewing")
    receipt = {
        "schemaVersion": "document-files.semantic-review.v1",
        "sourceReportSha256": report_hash,
        "reviewDecisionsSha256": decisions_hash,
        "reviewedAt": datetime.now(UTC).isoformat(),
        "reviewer": reviewer.strip(),
        "independent": True,
        "method": method,
        "passed": all_passed,
        "cases": reviewed,
    }
    write_json(output, receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = seal_review(args.report, args.decisions, args.output)
    print(f"Review receipt written: {args.output}; passed={receipt['passed']}")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
