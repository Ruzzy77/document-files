"""Synthetic runner/reviewer contracts only; no fixture here is release evidence."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

# Load the standalone tool as other script tests do; support pytest console scripts
# as well as python -m pytest without requiring the repository root on sys.path.
_spec = importlib.util.spec_from_file_location(
    "evaluation_review", Path(__file__).parents[1] / "evaluation/review.py"
)
review = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(review)
run = importlib.import_module("evaluation.run")


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return {"path": path.name, "sha256": run.sha256(path)}


@pytest.fixture
def case_files(tmp_path):
    (tmp_path / "source.txt").write_text("Observed text only.", encoding="utf-8")
    specification = write(
        tmp_path / "expected.json",
        {
            "schemaVersion": "document-files.review-specification.v1",
            "criteria": [
                {
                    "id": "value_source_bindings",
                    "description": "Check source values",
                    "expected": "SECRET_EXPECTED_NEVER_SENT",
                }
            ],
        },
    )
    recognition = write(
        tmp_path / "recognition.json",
        {
            "elapsedSeconds": 3.4,
            "note": "Synthetic receipt for contract testing only",
        },
    )
    case = {
        "id": "independent-case",
        "path": "source.txt",
        "languages": ["ko", "en"],
        "printed": True,
        "pdfKind": "not-applicable",
        "longDocument": False,
        "reviewSpecification": specification,
        "recognitionCostReceipt": recognition,
    }
    manifest = tmp_path / "cases.json"
    write(
        manifest,
        {
            "schemaVersion": "document-files.evaluation-cases.v2",
            "publicData": True,
            "cases": [case],
        },
    )
    return manifest, case


@pytest.fixture
def inference(tmp_path, case_files, monkeypatch):
    manifest, case = case_files
    captured = []

    def extract(path, **kwargs):
        captured.append((path, copy.deepcopy(kwargs)))
        return {
            "source": {"sha256": run.sha256(run.Path(path))},
            "extraction": {"status": "complete", "usage": {"elapsedSeconds": 0.5}},
            "validation": {"valid": True},
        }

    monkeypatch.setattr(run, "extract_schema", extract)
    monkeypatch.setattr(
        run.subprocess, "check_output", lambda cmd, **kw: "a" * 40 if "rev-parse" in cmd else b""
    )
    args = SimpleNamespace(
        output=tmp_path / "output",
        kind="local",
        options=None,
        holdout_only=True,
        holdout=manifest,
        case_format=None,
        artifact_inventory=None,
        artifact=[],
    )
    client = SimpleNamespace(identity={"model": "synthetic-test-only"})
    return args, client, captured


def execute(inference):
    args, client, captured = inference
    assert run.evaluate(args, client, None) == 0
    path = args.output / "local_model.json"
    return path, json.loads(path.read_text()), captured


def decisions_for(report_path, report):
    path = report_path.parent / "decisions.json"
    cases = [
        {
            "id": case["id"],
            "inputSha256": case["inputSha256"],
            "resultSha256": case["resultSha256"],
            "reviewSpecificationSha256": case["reviewSpecification"]["sha256"],
            "criteria": {"value_source_bindings": True},
            "findings": ["Synthetic fixture judgment, not actual qualification"],
        }
        for case in report["cases"]
    ]
    write(
        path,
        {
            "schemaVersion": "document-files.review-decisions.v1",
            "sourceReportSha256": run.sha256(report_path),
            "reviewer": "test-reviewer",
            "independent": True,
            "method": "human-ground-truth",
            "cases": cases,
        },
    )
    return path


def test_freezes_metadata_specification_input_and_cost_without_expected_leak(
    inference, monkeypatch
):
    monkeypatch.setenv("DOCUMENT_FILES_EXECUTION_RUN_ID", "run-test-only")
    report_path, report, captured = execute(inference)
    case = report["cases"][0]
    assert report["schemaVersion"] == "document-files.model-qualification.v2"
    assert report["passed"] is False
    assert report["executionRunId"] == "run-test-only"
    assert case["id"] == "independent-case"
    assert case["languages"] == ["ko", "en"]
    assert case["printed"] is True and case["longDocument"] is False
    assert case["pdfKind"] == "not-applicable"
    assert case["semanticReview"] == {"status": "pending"}
    assert case["options"]["maxModelCalls"] == 12
    assert case["options"]["completionSeconds"] == 900
    assert case["usage"] == {"elapsedSeconds": 0.5}
    assert case["elapsedSeconds"] >= 0
    assert report["elapsedSeconds"] >= case["elapsedSeconds"]
    assert case["recognitionCostStatus"] == "receipt-provided"
    spec = run.checked_reference(report_path.parent, case["reviewSpecification"])
    assert "SECRET_EXPECTED_NEVER_SENT" in spec.read_text()
    assert (
        json.loads(
            run.checked_reference(
                report_path.parent,
                case["recognitionCostReceipt"],
            ).read_text()
        )["elapsedSeconds"]
        == 3.4
    )
    path, kwargs = captured[0]
    assert set(kwargs) == {"model_client", "retain", "options", "observation_backend"}
    assert "SECRET_EXPECTED" not in json.dumps(kwargs["options"])
    assert run.Path(path).read_text() == "Observed text only."
    assert run.sha256(run.Path(path)) == case["inputSha256"]


def test_explicit_budget_override_is_validated_and_long_defaults():
    assert run.case_options({"longDocument": True}, {})["maxModelCalls"] == 64
    assert run.case_options({"longDocument": True}, {})["completionSeconds"] == 3600
    assert run.case_options({"longDocument": False}, {"maxModelCalls": 3})["maxModelCalls"] == 3
    with pytest.raises(ValueError):
        run.case_options({"longDocument": True}, {"completionSeconds": 0})


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../bad"},
        {"languages": []},
        {"printed": "true"},
        {"longDocument": True},
        {"pdfKind": "scan"},
        {"expected": "leak"},
    ],
)
def test_invalid_case_metadata_stops_before_inference(case_files, inference, change):
    manifest, case = case_files
    data = json.loads(manifest.read_text())
    data["cases"][0].update(change)
    write(manifest, data)
    args, client, captured = inference
    with pytest.raises(ValueError):
        run.evaluate(args, client, None)
    assert not captured


def test_changed_specification_rejected_before_inference(inference, case_files):
    manifest, _ = case_files
    (manifest.parent / "expected.json").write_text("{}")
    args, client, captured = inference
    with pytest.raises(ValueError, match="checksum"):
        run.evaluate(args, client, None)
    assert not captured


def test_safe_references_reject_traversal_and_symlink(tmp_path):
    write(tmp_path / "data.json", {})
    (tmp_path / "link.json").symlink_to(tmp_path / "data.json")
    for path in ("../data.json", "link.json", str(tmp_path / "data.json")):
        with pytest.raises(ValueError, match="Unsafe"):
            run.checked_reference(tmp_path, {"path": path, "sha256": "0" * 64})


def test_review_seals_receipt_without_changing_inference(inference):
    path, report, _ = execute(inference)
    original = path.read_bytes()
    decisions = decisions_for(path, report)
    receipt_path = path.parent / "review.json"
    receipt = review.seal_review(path, decisions, receipt_path)
    assert receipt["passed"] is True
    assert receipt["sourceReportSha256"] == run.sha256(path)
    assert receipt["cases"][0]["status"] == "passed"
    assert (
        receipt["cases"][0]["reviewSpecificationSha256"]
        == (report["cases"][0]["reviewSpecification"]["sha256"])
    )
    assert path.read_bytes() == original
    with pytest.raises(FileExistsError):
        review.seal_review(path, decisions, receipt_path)


@pytest.mark.parametrize("linked", ["resultPath", "inputPath", "reviewSpecification"])
def test_review_rejects_stale_file(inference, linked):
    path, report, _ = execute(inference)
    decisions = decisions_for(path, report)
    ref = report["cases"][0][linked]
    target = path.parent / (ref["path"] if isinstance(ref, dict) else ref)
    target.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        review.seal_review(path, decisions, path.parent / "review.json")


@pytest.mark.parametrize(
    "change",
    [
        {"reviewer": ""},
        {"independent": False},
        {"independent": "true"},
        {"sourceReportSha256": "0" * 64},
        {"cases": []},
    ],
)
def test_review_rejects_incomplete_or_stale_decisions(inference, change):
    path, report, _ = execute(inference)
    decisions = decisions_for(path, report)
    data = json.loads(decisions.read_text())
    data.update(change)
    write(decisions, data)
    with pytest.raises(ValueError):
        review.seal_review(path, decisions, path.parent / "review.json")


@pytest.mark.parametrize(
    "change",
    [
        {"criteria": {}},
        {"criteria": {"value_source_bindings": "true"}},
        {"criteria": {"value_source_bindings": 1}},
        {"findings": []},
        {"inputSha256": "0" * 64},
    ],
)
def test_review_rejects_false_pass_fields(inference, change):
    path, report, _ = execute(inference)
    decisions = decisions_for(path, report)
    data = json.loads(decisions.read_text())
    data["cases"][0].update(change)
    write(decisions, data)
    with pytest.raises(ValueError):
        review.seal_review(path, decisions, path.parent / "review.json")


@pytest.mark.parametrize("failure", ["partial", "invalid", "failed-criterion"])
def test_partial_invalid_or_failed_criterion_cannot_pass(inference, failure):
    path, report, _ = execute(inference)
    case = report["cases"][0]
    result_path = path.parent / case["resultPath"]
    result = json.loads(result_path.read_text())
    if failure == "partial":
        result["extraction"]["status"] = case["extractionStatus"] = "partial"
    elif failure == "invalid":
        result["validation"]["valid"] = case["structurallyValid"] = False
    write(result_path, result)
    case["resultSha256"] = run.sha256(result_path)
    write(path, report)
    decisions = decisions_for(path, report)
    if failure == "failed-criterion":
        data = json.loads(decisions.read_text())
        data["cases"][0]["criteria"]["value_source_bindings"] = False
        write(decisions, data)
    receipt = review.seal_review(path, decisions, path.parent / "review.json")
    assert receipt["passed"] is False
    assert receipt["cases"][0]["status"] == "failed"


def test_result_source_must_match_reviewed_input(inference):
    path, report, _ = execute(inference)
    case = report["cases"][0]
    result_path = path.parent / case["resultPath"]
    result = json.loads(result_path.read_text())
    result["source"]["sha256"] = "0" * 64
    write(result_path, result)
    case["resultSha256"] = run.sha256(result_path)
    write(path, report)
    decisions = decisions_for(path, report)
    with pytest.raises(ValueError, match="not linked"):
        review.seal_review(path, decisions, path.parent / "review.json")


def test_empty_evidence_directory_required(inference):
    execute(inference)
    args, client, _ = inference
    with pytest.raises(ValueError, match="empty"):
        run.evaluate(args, client, None)


def add_inventory(inference):
    args, _, _ = inference
    root = args.output.parent
    artifact = root / "core.whl"
    artifact.write_bytes(b"Synthetic packaging contract fixture only")
    args.artifact_inventory = root / "inventory.json"
    args.artifact = ["core-wheel"]
    args.evidence_root = root
    write(
        args.artifact_inventory,
        {
            "schemaVersion": "document-files.artifact-inventory.v2",
            "version": run.__version__,
            "sourceCommit": "a" * 40,
            "dirtySource": False,
            "artifacts": [
                {
                    "id": "core-wheel",
                    "path": artifact.name,
                    "sha256": run.sha256(artifact),
                    "kind": "core",
                }
            ],
        },
    )
    return artifact


def test_exact_artifact_inventory_is_linked(inference, monkeypatch):
    add_inventory(inference)
    # This unit covers report linkage only; byte verification has independent tests.
    monkeypatch.setattr(
        run,
        "verify_executed_source",
        lambda *args: ({"verification": "synthetic-linkage-fixture-not-qualification"}, "a" * 40),
    )
    path, report, _ = execute(inference)
    args, _, _ = inference
    assert report["artifacts"] == ["core-wheel"]
    assert run.checked_reference(args.evidence_root, report["artifactInventory"]) == (
        args.artifact_inventory
    )
    assert report["artifactInventory"]["sha256"] == run.sha256(args.artifact_inventory)


@pytest.mark.parametrize("fault", ["hash", "commit", "unknown", "duplicate", "dirty"])
def test_inventory_fault_stops_inference(inference, fault):
    artifact = add_inventory(inference)
    args, client, captured = inference
    inventory = json.loads(args.artifact_inventory.read_text())
    if fault == "hash":
        artifact.write_bytes(b"Different build")
    elif fault == "commit":
        inventory["sourceCommit"] = "b" * 40
    elif fault == "unknown":
        args.artifact = ["other-wheel"]
    elif fault == "duplicate":
        inventory["artifacts"].append(inventory["artifacts"][0])
    elif fault == "dirty":
        inventory["dirtySource"] = True
    write(args.artifact_inventory, inventory)
    with pytest.raises(ValueError):
        run.evaluate(args, client, None)
    assert not captured


def test_long_case_metadata_and_default_budget_persist(inference):
    args, _, _ = inference
    manifest = json.loads(args.holdout.read_text())
    manifest["cases"][0].update(longDocument=True, pageCount=4, regionCount=3)
    write(args.holdout, manifest)
    _, report, captured = execute(inference)
    case = report["cases"][0]
    assert case["longDocument"] is True
    assert case["pageCount"] == 4 and case["regionCount"] == 3
    assert case["options"]["maxModelCalls"] == 64
    assert case["options"]["completionSeconds"] == 3600
    assert captured[0][1]["options"] == case["options"]


def test_every_specification_is_snapshotted_before_first_inference(inference, monkeypatch):
    args, _, _ = inference
    manifest = json.loads(args.holdout.read_text())
    second = copy.deepcopy(manifest["cases"][0])
    second["id"] = "second-case"
    manifest["cases"].append(second)
    write(args.holdout, manifest)
    original_extract = run.extract_schema

    def extract(path, **kwargs):
        assert (args.output / "review-specifications/second-case.json").exists()
        # Later changes to original expected data cannot silently amend either case.
        (args.holdout.parent / "expected.json").write_text("Changed after start")
        return original_extract(path, **kwargs)

    monkeypatch.setattr(run, "extract_schema", extract)
    _, report, captured = execute(inference)
    assert len(captured) == 2
    assert (
        report["cases"][0]["reviewSpecification"]["sha256"]
        == (report["cases"][1]["reviewSpecification"]["sha256"])
    )


def test_input_mutation_during_extraction_is_rejected(inference, monkeypatch):
    args, client, _ = inference
    original_extract = run.extract_schema

    def extract(path, **kwargs):
        result = original_extract(path, **kwargs)
        run.Path(path).write_text("Mutated input")
        return result

    monkeypatch.setattr(run, "extract_schema", extract)
    with pytest.raises(ValueError, match="Input changed"):
        run.evaluate(args, client, None)
    assert not (args.output / "local_model.json").exists()


def test_duplicate_json_keys_cannot_hide_failed_review(inference):
    path, report, _ = execute(inference)
    decisions = decisions_for(path, report)
    decisions.write_text(
        decisions.read_text().replace(
            '"value_source_bindings": true',
            '"value_source_bindings": false, "value_source_bindings": true',
        )
    )
    with pytest.raises(ValueError, match="Duplicate JSON"):
        review.seal_review(path, decisions, path.parent / "review.json")
