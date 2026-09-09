#!/usr/bin/env python3
"""Seal independent HTTP operational review; never alter raw run evidence.

All paths are evidence-root relative. Decisions bind the raw report SHA, exact
execution/container receipts, per-test findings and a pre-run content specification.
The gate repeats this assessment; reviewer booleans alone cannot approve a run.
This verifies completeness and identities, not the truth of fabricated observations.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

TESTS = {
    "authentication",
    "idempotency_same",
    "idempotency_conflict",
    "cancel_tree",
    "resume_checkpoint",
    "explicit_budget",
    "input_snapshot",
    "no_path_or_url_input",
    "persistent_results",
    "restart_interrupted",
    "actual_ai_complete",
    "delete_results",
}


def gate_module():
    spec = importlib.util.spec_from_file_location(
        "operational_gate", Path(__file__).with_name("check_release_qualification.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def findings(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(x, str) and x.strip() for x in value)
    )


def assess(root, report, decisions, raw_ref, gate):
    if (
        report.get("schemaVersion") != "document-files.http-installation-run.v1"
        or report.get("passed") is not False
        or report.get("releaseQualification") is not False
        or decisions.get("schemaVersion") != "document-files.operational-review-decisions.v1"
        or decisions.get("sourceReportSha256") != raw_ref["sha256"]
        or not isinstance(decisions.get("reviewer"), str)
        or not decisions["reviewer"].strip()
        or decisions.get("independent") is not True
        or decisions.get("method") not in {"human-ground-truth", "independent-ground-truth"}
    ):
        raise ValueError("Independent review of immutable HTTP raw evidence required")

    def linked(ref):
        return gate._linked(root, ref["path"], ref["sha256"])[1]

    judgments = decisions.get("criteria", {})
    if set(judgments) != TESTS or any(
        not isinstance(v, dict)
        or type(v.get("passed")) is not bool
        or not findings(v.get("findings"))
        for v in judgments.values()
    ):
        raise ValueError("Every HTTP criterion needs an independent judgment and findings")
    missing = []
    raw_tests = report.get("tests", [])
    by_id = {v["id"]: v for v in raw_tests}
    if len(by_id) != len(raw_tests):
        raise ValueError("Duplicate HTTP run tests")
    lifecycle = linked(report["lifecycleEvidence"]) if report.get("lifecycleEvidence") else {}
    if (
        lifecycle.get("schemaVersion") != "document-files.http-lifecycle-observations.v1"
        or lifecycle.get("executionRunId") != report.get("executionRunId")
        or lifecycle.get("jobId") != report.get("jobId")
        or not report.get("jobId")
    ):
        missing.append("lifecycle-run-identity")
    observations = lifecycle.get("observations", {})

    def result(ref):
        return linked(ref) if isinstance(ref, dict) else {}

    def children(value):
        return (
            isinstance(value, list)
            and bool(value)
            and any("llama-server" in p.get("name", "") for p in value)
            and all(
                type(p.get("pid")) is int
                and p["pid"] > 0
                and str(p.get("startTicks", "")).isdigit()
                for p in value
            )
        )

    actual = {}
    for key in TESTS:
        d = observations.get(key, {})
        ok = False
        if key == "authentication":
            ok = d.get("anonymousStatus") == d.get("invalidTokenStatus") == 401
        elif key == "no_path_or_url_input":
            ok = (
                d.get("bodyStatuses") == [415] * 3
                and d.get("optionErrors") == [{"status": 400, "error": "invalid-options"}] * 3
            )
        elif key == "idempotency_same":
            ok = (
                d.get("status") == 202
                and d.get("initialJobId") == d.get("repeatedJobId") == report["jobId"]
            )
        elif key == "idempotency_conflict":
            ok = d.get("status") == 409 and d.get("error") == "idempotency-conflict"
        elif key == "input_snapshot":
            ok = d.get("snapshotSha256") == d.get("submittedSha256") == report.get("input", {}).get(
                "sha256"
            ) and bool(report.get("input"))
        elif key == "explicit_budget":
            p = result(d.get("partialResult"))
            ok = (
                d.get("httpStatus") == 200
                and d.get("jobStatus") == "partial"
                and d.get("partialResult") == report.get("partialResult")
                and p.get("extraction", {}).get("modelCalls") == 1
                and any(i.get("code") == "model_call_budget_exceeded" for i in p.get("issues", []))
            )
        elif key == "cancel_tree":
            ok = (
                d.get("jobStatus") == "cancelled"
                and children(d.get("childrenBefore"))
                and d.get("childrenAliveAfter") == []
                and report.get("residualCancelChildren") == []
                and report.get("cleanupForcedChildren") == []
            )
        elif key == "persistent_results":
            before, after = result(d.get("before")), result(d.get("after"))
            ok = (
                d.get("httpStatus") == 200
                and bool(before)
                and before == after
                and d.get("before") == report.get("cancelledResult")
                and d.get("snapshotSha256") == report.get("input", {}).get("sha256")
            )
        elif key == "restart_interrupted":
            before, after, later = (d.get(k, {}) for k in ("before", "after", "afterDelay"))
            ok = (
                children(d.get("childrenBefore"))
                and d.get("childrenAliveAfterStop") == []
                and before.get("executionStatus") in {"queued", "running"}
                and after.get("status") == later.get("status") == "interrupted"
                and type(before.get("attempt")) is int
                and before["attempt"] == after.get("attempt") == later.get("attempt")
            )
        elif key == "resume_checkpoint":
            final, cancelled = result(d.get("result")), result(d.get("cancelledResult"))
            attempt = d.get("finalStatus", {}).get("attempt")
            calls = final.get("extraction", {}).get("modelCalls")
            ok = (
                d.get("httpStatus") == 200
                and d.get("result") == report.get("aiResult")
                and d.get("cancelledResult") == report.get("cancelledResult")
                and type(attempt) is int
                and type(d.get("cancelledAttempt")) is int
                and attempt > d["cancelledAttempt"]
                and type(calls) is int
                and calls <= report.get("budget", {}).get("maxModelCalls", -1)
                and final.get("resultRevision", -1) >= cancelled.get("resultRevision", 0) > 0
            )
        elif key == "actual_ai_complete":
            final = result(d.get("result"))
            ok = (
                d.get("jobStatus") == "complete"
                and d.get("result") == report.get("aiResult")
                and final.get("extraction", {}).get("status") == "complete"
                and final.get("validation", {}).get("valid") is True
                and not final.get("validation", {}).get("errors")
            )
        elif key == "delete_results":
            ok = (
                d.get("deleteStatus") == 200
                and d.get("deleted") is True
                and d.get("lookupStatus") == 404
                and d.get("uploadExists") is False
                and d.get("resultDatabaseExists") is False
            )
        actual[key] = bool(
            ok and by_id.get(key, {}).get("passed") is True and judgments[key]["passed"]
        )
        if not actual[key]:
            missing.append(key)
    semantic = decisions.get("semanticReview", {})
    if (
        not report.get("reviewSpecification")
        or not report.get("input")
        or not report.get("aiResult")
    ):
        missing.append("input-result-prerun-specification")
    else:
        spec = linked(report["reviewSpecification"])
        ids = [v["id"] for v in spec.get("criteria", [])]
        if (
            spec.get("schemaVersion") != "document-files.review-specification.v1"
            or not ids
            or len(ids) != len(set(ids))
        ):
            raise ValueError("Frozen independent content criteria required")
        gate._file(root, report["input"]["path"], report["input"]["sha256"])
        final = linked(report["aiResult"])
        if final.get("source", {}).get("sha256") != report["input"]["sha256"]:
            raise ValueError("Operational result/source mismatch")
        if (
            set(semantic.get("criteria", {})) != set(ids)
            or not findings(semantic.get("findings"))
            or any(type(v) is not bool for v in semantic.get("criteria", {}).values())
        ):
            raise ValueError("Independent content comparison required")
        budget = report.get("budget", {})
        long_document = spec.get("longDocument") is True
        maximum_calls, maximum_seconds = (64, 3600) if long_document else (12, 900)
        if (
            type(budget.get("maxModelCalls")) is not int
            or not 0 < budget["maxModelCalls"] <= maximum_calls
            or type(budget.get("completionSeconds")) is not int
            or not 0 < budget["completionSeconds"] <= maximum_seconds
        ):
            missing.append("document-budget")
        if not all(semantic["criteria"].values()):
            missing.append("independent-content-review")
    return {
        "sourceReportSha256": raw_ref["sha256"],
        "reviewer": decisions["reviewer"].strip(),
        "independent": True,
        "method": decisions["method"],
        "bindings": {
            k: report.get(k)
            for k in (
                "artifactInventory",
                "artifacts",
                "executionRunId",
                "observationProfile",
                "notCovered",
                "input",
                "aiResult",
                "reviewSpecification",
                "lifecycleEvidence",
            )
        }
        | {k: decisions[k] for k in ("executionReceipt", "containerIdentityReceipt")},
        "criteria": {k: {**judgments[k], "verified": actual[k]} for k in sorted(TESTS)},
        "semanticReview": semantic,
        "missingEvidence": sorted(set(missing)),
        "passed": not missing and report.get("checksPassed") is True and not report.get("failure"),
    }


def seal(root, report_path, decisions_path, output):
    gate = gate_module()
    root = root.resolve()

    def reference(path):
        return {
            "path": path.resolve().relative_to(root).as_posix(),
            "sha256": gate.hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    raw_ref, decision_ref = reference(report_path), reference(decisions_path)
    _, report = gate._linked(root, **{"relative": raw_ref["path"], "digest": raw_ref["sha256"]})
    decisions = gate._linked(root, decision_ref["path"], decision_ref["sha256"])[1]
    assets = gate.artifact_inventory(report, root, report["sourceCommit"], report["version"])
    gate._http_execution(report, decisions, root, report_path.resolve(), raw_ref["sha256"], assets)
    assessed = assess(root, report, decisions, raw_ref, gate)
    if assessed["passed"]:
        gate._operational({**report, "passed": True}, "http_service", root, assets)
    gate._file(root, raw_ref["path"], raw_ref["sha256"])
    gate._file(root, decision_ref["path"], decision_ref["sha256"])
    receipt = {
        "schemaVersion": "document-files.operational-review.v1",
        "reviewDecisions": decision_ref,
        "reviewedAt": datetime.now(UTC).isoformat(),
        **assessed,
    }
    with output.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("evidence-root", "report", "decisions", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    args = parser.parse_args()
    receipt = seal(args.evidence_root, args.report, args.decisions, args.output)
    print(
        "Review passed" if receipt["passed"] else "Review incomplete; original evidence unchanged"
    )


if __name__ == "__main__":
    main()
