#!/usr/bin/env python3
"""Fail closed on release evidence, not on packaging or self-reported partial success.

Evidence is independently collected and reviewed; this checker verifies identity,
coverage, linked result hashes and explicit measured acceptance criteria. It cannot
establish that a fabricated measurement really happened. Never use unit fixtures
as release evidence. Cloud may be explicitly not-qualified; CPU support may not.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"macos-aarch64", "macos-x86_64", "windows-x86_64", "linux-x86_64"}
CLIENTS = {"codex", "claude_code", "claude_desktop", "chatgpt"}
REQUIRED = frozenset(
    {"local_model", "http_service", "container_internal"}
    | {f"packaged_{p}" for p in PLATFORMS}
    | {f"client_{c}" for c in CLIENTS}
)
FORMATS = {"txt", "md", "html", "docx", "hwp", "hwpx", "xlsx", "pptx", "pdf"}
CRITERIA = {
    "repeated_rows",
    "merged_headers",
    "common_units",
    "footnotes",
    "conditions",
    "empty_missing_uncertain",
    "number_precision",
    "formulas_cached_values",
    "field_source_bindings",
    "value_source_bindings",
    "cross_page_tables",
    "cross_page_notes",
}
HTTP_TESTS = {
    "authentication",
    "idempotency_same",
    "idempotency_conflict",
    "cancel_tree",
    "resume_checkpoint",
    "explicit_budget",
    "input_snapshot",
    "no_path_or_url_input",
}
CONTAINER_TESTS = {
    "offline_extraction",
    "internal_network_only",
    "authentication",
    "non_root",
    "read_only_root",
    "persistent_results",
    "restart_interrupted",
}
PLATFORM_TESTS = {
    "install_without_python",
    "document_processing",
    "update",
    "rollback",
    "hwp_checkbox_preservation",
}
CLIENT_TESTS = {"install", "tool_discovery", "document_processing", "extraction_result"}


def _file(root: Path, relative, digest: str) -> Path:
    path = Path(relative)
    resolved = root / path
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or not resolved.resolve().is_relative_to(root.resolve())
        or any(p.is_symlink() for p in [resolved, *resolved.parents])
    ):
        raise ValueError("Unsafe evidence path")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid SHA256 identity")
    with resolved.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("Evidence checksum mismatch")
    return resolved


def _linked(root: Path, relative, digest: str) -> tuple[Path, dict]:
    resolved = _file(root, relative, digest)
    result = json.loads(resolved.read_bytes())
    if not isinstance(result, dict):
        raise ValueError("Evidence must be an object")
    return resolved, result


def _actual_model(report: dict, name: str, path: Path, evidence_root: Path):
    model = report.get("model", {})
    adapter = model.get("adapter", "")
    if (
        report.get("schemaVersion") != "document-files.model-qualification.v2"
        or report.get("dirtySource") is not False
        or report.get("passed") is not True
        or report.get("executionKind") != "actual-model"
        or not model.get("model")
        or not adapter
        or any(marker in adapter.lower() for marker in ("script", "fake", "mock"))
        or report.get("endpointKind") != name.removesuffix("_model")
    ):
        raise ValueError(f"Actual model evidence required: {name}")
    if name == "local_model":
        execution = report.get("execution", {})
        ceiling, peak = execution.get("memoryCeilingBytes"), execution.get("cgroupMemoryPeakBytes")
        if (
            adapter != "managed-llama-cpp.v1"
            or execution.get("device") != "cpu"
            or execution.get("gpuUsed") is not False
            or execution.get("offline") is not True
            or execution.get("networkBlocked") is not True
            or execution.get("memoryCeilingVerified") is not True
            or type(ceiling) is not int
            or not 0 < ceiling <= 16 * 1024**3
            or type(peak) is not int
            or not 0 < peak <= ceiling
            or any(
                type(execution.get(key)) is not int or execution[key] != 0
                for key in (
                    "memorySwapMaxBytes",
                    "memorySwapPeakBytes",
                    "oomEventsDelta",
                    "oomKillEventsDelta",
                    "gpuDeviceCount",
                )
            )
            or execution.get("networkMode") != "none"
            or type(execution.get("exitCode")) is not int
            or execution["exitCode"] != 0
            or execution.get("timedOut") is not False
            or execution.get("measurementComplete") is not True
            or execution.get("limitsUnchanged") is not True
            or execution.get("recorderErrors") != []
            or not model.get("runtimeManifestSha256")
            or not model.get("modelManifestSha256")
        ):
            raise ValueError("Measured CPU 16GB offline execution required")
    cases = report.get("cases", [])
    holdouts = [c for c in cases if c.get("holdout") is True and c.get("printed") is True]
    if (
        not {c.get("format") for c in holdouts} >= FORMATS
        or not {"ko", "en"} <= {lang for c in holdouts for lang in c.get("languages", [])}
        or not {"text", "scan"} <= {c.get("pdfKind") for c in holdouts if c.get("format") == "pdf"}
        or not any(
            c.get("longDocument") is True
            and c.get("regionCount", 0) >= 3
            and c.get("pageCount", 0) >= 3
            for c in holdouts
        )
    ):
        raise ValueError(f"Independent printed full-document holdout coverage required: {name}")
    covered = set()
    for case in cases:
        review = case.get("semanticReview", {})
        criteria = review.get("criteria", {})
        if (
            case.get("extractionStatus") != "complete"
            or case.get("structurallyValid") is not True
            or review.get("status") != "passed"
            or not review.get("reviewer")
            or review.get("independent") is not True
            or review.get("method") not in {"human-ground-truth", "independent-ground-truth"}
            or not review.get("findings")
            or not criteria
            or any(value is not True for value in criteria.values())
        ):
            raise ValueError(f"Incomplete independent semantic review: {name}")
        if case in holdouts:
            covered.update(criteria)
        relative = path.parent.relative_to(evidence_root) / case["resultPath"]
        _, result = _linked(evidence_root, relative, case["resultSha256"])
        observed = result.get("provenance", {}).get("model", {})
        identity_keys = (
            "adapter",
            "model",
            "configurationId",
            "runtimeManifestSha256",
            "modelManifestSha256",
            "interpretationProtocol",
            "contextTokens",
            "sampling",
            "threads",
            "threadsBatch",
        )
        if (
            result.get("extraction", {}).get("status") != "complete"
            or result.get("validation", {}).get("valid") is not True
            or result.get("source", {}).get("sha256") != case.get("inputSha256")
            or not case.get("inputSha256")
            or any(observed.get(k) != model.get(k) for k in identity_keys)
            or result.get("validation", {}).get("errors")
        ):
            raise ValueError(f"Report does not match actual model result: {name}")
    if not covered >= CRITERIA:
        raise ValueError(f"Missing independent value/relation criteria: {name}")


def _operational(report: dict, name: str, root: Path, assets: dict):
    unsupported = (
        name == "client_chatgpt"
        and report.get("capabilities", {}).get("aiExtraction") == "not-supported"
    )
    if unsupported:
        if report.get("aiResult") is not None:
            raise ValueError("Unsupported AI must not attach an AI success result")
    else:
        ref = report.get("aiResult")
        if not isinstance(ref, dict):
            raise ValueError("Actual successful installed AI result required")
        _, result = _linked(root, ref["path"], ref["sha256"])
        observed = result.get("provenance", {}).get("model", {})
        if (
            result.get("extraction", {}).get("status") != "complete"
            or result.get("validation", {}).get("valid") is not True
            or result.get("validation", {}).get("errors")
            or not result.get("source", {}).get("sha256")
        ):
            raise ValueError("Actual successful installed AI result required")
        for kind, key in (("runtime", "runtimeManifestSha256"), ("model", "modelManifestSha256")):
            if not any(
                assets[a]["kind"] == kind and assets[a].get("manifestSha256") == observed.get(key)
                for a in report["artifacts"]
            ):
                raise ValueError("Installed AI result differs from qualified pack identity")
    tests = report.get("tests", [])
    by_id = {test["id"]: test for test in tests}
    if (
        report.get("passed") is not True
        or report.get("dirtySource") is not False
        or report.get("executionKind") != "actual-installed"
        or not tests
        or len(by_id) != len(tests)
        or any(test.get("passed") is not True for test in tests)
    ):
        raise ValueError(f"Actual installed evidence required: {name}")
    expected = (
        HTTP_TESTS
        if name == "http_service"
        else CONTAINER_TESTS
        if name == "container_internal"
        else PLATFORM_TESTS
        if name.startswith("packaged_")
        else CLIENT_TESTS
    )
    if (
        name == "client_chatgpt"
        and report.get("capabilities", {}).get("aiExtraction") == "not-supported"
    ):
        expected = CLIENT_TESTS - {"extraction_result"} | {"unsupported_ai_is_explicit"}
        if (
            "extraction_result" in by_id
            or report["capabilities"].get("nativeDocuments") != "qualified"
        ):
            raise ValueError("Unsupported ChatGPT AI must not be reported as qualified")
    elif name.startswith("client_") and report.get("capabilities") != {
        "nativeDocuments": "qualified",
        "aiExtraction": "qualified",
    }:
        raise ValueError("Actual AI capability qualification required")
    if not expected <= by_id.keys():
        raise ValueError(f"Missing operational criteria: {name}")
    if name.startswith("packaged_"):
        target = name.removeprefix("packaged_")
        if report.get("target") != target or not report.get("artifactSha256"):
            raise ValueError(f"Packaged target identity required: {name}")
        route = "linux-cpu-container" if target == "macos-x86_64" else "native-cpu"
        if report.get("fullExtractionRoute") != route:
            raise ValueError(f"Full extraction route required: {name}")
    if name.startswith("client_") and (
        report.get("client") != name.removeprefix("client_")
        or not report.get("clientVersion")
        or not report.get("runtimeRoute")
    ):
        raise ValueError(f"Actual client version/runtime required: {name}")


def _identity(document: dict, commit: str, version: str) -> None:
    if (
        document.get("sourceCommit") != commit
        or document.get("version") != version
        or document.get("dirtySource") is not False
    ):
        raise ValueError("Clean release source identity required")


def artifact_inventory(document: dict, root: Path, commit: str, version: str) -> dict:
    ref = document["artifactInventory"]
    path, inventory = _linked(root, ref["path"], ref["sha256"])
    _identity(inventory, commit, version)
    if inventory.get("schemaVersion") != "document-files.artifact-inventory.v2":
        raise ValueError("Release artifact inventory v2 required")
    assets = {}
    paths = set()
    for asset in inventory["artifacts"]:
        identifier = asset["id"]
        if not identifier or identifier in assets:
            raise ValueError("Duplicate or empty artifact ID")
        relative = path.parent.relative_to(root) / asset["path"]
        actual = _file(root, relative, asset["sha256"])
        if actual in paths:
            raise ValueError("Duplicate artifact path")
        paths.add(actual)
        kind = asset["kind"]
        if kind == "core":
            receipt = asset["buildReceipt"]
            _, build = _linked(root, receipt["path"], receipt["sha256"])
            _identity(build, commit, version)
            if (
                build.get("schemaVersion") != "document-files.build-inventory.v2"
                or build.get("candidateMode") != "stable"
                or build.get("artifacts", {}).get(actual.name) != asset["sha256"]
                or build.get("target") != asset.get("target")
            ):
                raise ValueError("Core artifact does not match stable build receipt")
        elif kind in {"runtime", "model", "recognition"}:
            with zipfile.ZipFile(actual) as archive:
                raw = archive.read("manifest.json")
            if hashlib.sha256(raw).hexdigest() != asset.get("manifestSha256"):
                raise ValueError("Pack manifest digest mismatch")
            pack = json.loads(raw)
            build = pack["provenance"]["build"]
            if build.get("sourceCommit") != commit or build.get("dirtySource") is not False:
                raise ValueError("Pack build source identity mismatch")
            if pack.get("kind") != {"runtime": "llama-cpp-runtime"}.get(kind, kind):
                raise ValueError("Pack kind mismatch")
            if pack.get("platform") != asset.get("target"):
                raise ValueError("Pack target mismatch")
        elif kind == "image":
            receipt = asset["buildReceipt"]
            _, build = _linked(root, receipt["path"], receipt["sha256"])
            _identity(build, commit, version)
            if (
                build.get("schemaVersion") != "document-files.image-build.v1"
                or build.get("archiveSha256") != asset["sha256"]
                or (
                    asset.get("imageDigest") is not None
                    and not re.fullmatch(r"sha256:[a-f0-9]{64}", asset["imageDigest"])
                )
                or build.get("imageDigest") != asset.get("imageDigest")
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("imageId", ""))
                or build.get("imageId") != asset["imageId"]
            ):
                raise ValueError("Image artifact identity mismatch")
        elif kind != "metadata":
            raise ValueError("Unknown release artifact kind")
        assets[identifier] = {**asset, "verifiedPath": actual}
    if not assets:
        raise ValueError("Empty release artifact inventory")
    return assets


def _bindings(report: dict, name: str, assets: dict) -> None:
    ids = report.get("artifacts", [])
    if not ids or len(set(ids)) != len(ids) or any(key not in assets for key in ids):
        raise ValueError(f"Exact artifact bindings required: {name}")
    chosen = [assets[key] for key in ids]
    kinds = {asset["kind"] for asset in chosen}
    unsupported = (
        name == "client_chatgpt"
        and report.get("capabilities", {}).get("aiExtraction") == "not-supported"
    )
    required = (
        {"core"}
        if unsupported or name == "cloud_model"
        else {"core", "runtime", "model", "recognition"}
    )
    container = (
        name in {"local_model", "http_service", "container_internal"}
        or report.get("fullExtractionRoute") == "linux-cpu-container"
        or report.get("runtimeRoute") == "linux-cpu-container"
    )
    if container:
        required.add("image")
    if not kinds >= required:
        raise ValueError(f"Incomplete pipeline artifact bindings: {name}")
    if name.startswith("packaged_"):
        target = name.removeprefix("packaged_")
        if not any(
            a["kind"] == "core"
            and a.get("target") == target
            and a["sha256"] == report.get("artifactSha256")
            for a in chosen
        ):
            raise ValueError("Installed artifact differs from release asset")
        runtime_target = "linux-x86_64" if container else target
        for kind in ("runtime", "recognition"):
            if not any(a["kind"] == kind and a.get("target") == runtime_target for a in chosen):
                raise ValueError("Installed pack target differs from runtime route")
    if name == "local_model":
        model = report["model"]
        observation = report.get("observation") or {}
        recognition_sha = observation.get("packManifestSha256", observation.get("manifestSha256"))
        if not any(
            a["kind"] == "recognition" and a.get("manifestSha256") == recognition_sha
            for a in chosen
        ):
            raise ValueError("Recognition observation differs from release pack identity")
        for kind, key in (("runtime", "runtimeManifestSha256"), ("model", "modelManifestSha256")):
            if not any(
                a["kind"] == kind and a.get("manifestSha256") == model.get(key) for a in chosen
            ):
                raise ValueError("Model result differs from release pack identity")


def _core_execution(report: dict, assets: dict) -> None:
    spec = importlib.util.spec_from_file_location(
        "qualification_execution_identity", ROOT / "evaluation/execution_identity.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    actual = report.get("coreExecution", {})
    core_id, source_id = actual.get("coreArtifactId"), actual.get("evaluatorArtifactId")
    if (
        core_id not in report["artifacts"]
        or source_id not in report["artifacts"]
        or core_id == source_id
    ):
        raise ValueError("Measured executed core and evaluator artifacts required")
    core, source = assets[core_id], assets[source_id]
    if (
        core["kind"] != "core"
        or source["kind"] != "core"
        or not core["verifiedPath"].name.endswith(".whl")
        or not source["verifiedPath"].name.endswith(".tar.gz")
    ):
        raise ValueError("Executed core wheel and evaluator source artifact required")
    package = helper.wheel_package(core["verifiedPath"])
    evaluator = helper.sdist_evaluator(source["verifiedPath"])
    expected = {
        "verification": "wheel-record-and-source-exact.v1",
        "coreArtifactId": core_id,
        "coreArtifactSha256": core["sha256"],
        "packageTreeSha256": helper.tree_digest(package),
        "packageFileCount": len(package),
        "evaluatorArtifactId": source_id,
        "evaluatorArtifactSha256": source["sha256"],
        "evaluatorFilesSha256": helper.tree_digest(evaluator),
        "recorderSha256": helper.recorder_digest(source["verifiedPath"]),
    }
    if actual != expected:
        raise ValueError("Executed package/evaluator identity differs from release artifacts")


def _reviewed(report: dict, item: dict, root: Path, report_path: Path) -> dict:
    if report.get("passed") is not False:
        raise ValueError("Immutable inference report must remain unqualified")
    ref = item["review"]
    _, review = _linked(root, ref["path"], ref["sha256"])
    if (
        review.get("schemaVersion") != "document-files.semantic-review.v1"
        or review.get("sourceReportSha256") != item["evidence"]["sha256"]
        or review.get("passed") is not True
        or review.get("independent") is not True
        or not review.get("reviewer")
        or review.get("method") not in {"human-ground-truth", "independent-ground-truth"}
    ):
        raise ValueError("Independent immutable report review required")
    decisions = {case["id"]: case for case in review["cases"]}
    cases = report["cases"]
    if (
        len(decisions) != len(review["cases"])
        or len(cases) != len(decisions)
        or {case["id"] for case in cases} != decisions.keys()
    ):
        raise ValueError("Review case coverage mismatch")
    result = {**report, "passed": True, "cases": []}
    for case in cases:
        decision = decisions[case["id"]]
        spec = case["reviewSpecification"]
        _, specification = _linked(
            root, report_path.parent.relative_to(root) / spec["path"], spec["sha256"]
        )
        _file(root, report_path.parent.relative_to(root) / case["inputPath"], case["inputSha256"])
        criteria = {criterion["id"] for criterion in specification["criteria"]}
        if (
            specification.get("schemaVersion") != "document-files.review-specification.v1"
            or not criteria
            or len(criteria) != len(specification["criteria"])
        ):
            raise ValueError("Frozen review specification required")
        if (
            decision.get("inputSha256") != case["inputSha256"]
            or decision.get("resultSha256") != case["resultSha256"]
            or decision.get("reviewSpecificationSha256") != spec["sha256"]
            or set(decision.get("criteria", {})) != criteria
        ):
            raise ValueError("Review does not match frozen input/result/specification")
        result["cases"].append(
            {
                **case,
                "semanticReview": {
                    **decision,
                    "reviewer": review["reviewer"],
                    "independent": True,
                    "method": review["method"],
                },
            }
        )
    return result


def _execution(
    report: dict,
    root: Path,
    commit: str,
    version: str,
    report_path: Path,
    report_sha: str,
    ref: dict,
) -> dict:
    _, receipt = _linked(root, ref["path"], ref["sha256"])
    _identity(receipt, commit, version)
    if receipt.get("schemaVersion") != "document-files.execution-receipt.v1" or receipt.get(
        "artifacts"
    ) != report.get("artifacts"):
        raise ValueError("Execution receipt artifact identity mismatch")
    if (
        not report.get("executionRunId")
        or report["executionRunId"] != receipt.get("executionRunId")
        or {"path": report_path.relative_to(root).as_posix(), "sha256": report_sha}
        not in receipt.get("outputs", [])
    ):
        raise ValueError("Execution receipt does not bind this model run and report")
    if receipt.get("recorderSha256") != report["coreExecution"]["recorderSha256"]:
        raise ValueError("Executed resource recorder differs from selected source artifact")
    measurement = receipt["measurements"]
    _, measured = _linked(root, measurement["path"], measurement["sha256"])
    if measured.get("execution") != receipt.get("execution"):
        raise ValueError("Execution receipt differs from measured resource result")
    return {**report, "execution": receipt["execution"]}


def _container_identity(report: dict, item: dict, root: Path, assets: dict) -> None:
    ref = item.get("containerIdentityReceipt")
    if not isinstance(ref, dict):
        raise ValueError("Independent host Docker identity receipt required")
    _, host = _linked(root, ref["path"], ref["sha256"])
    _identity(host, report["sourceCommit"], report["version"])
    image = assets.get(host.get("imageArtifactId"), {})
    actual = host.get("dockerInspect", {})
    if (
        host.get("schemaVersion") != "document-files.container-identity.v1"
        or host.get("collectorSha256")
        != hashlib.sha256((ROOT / "scripts/capture_container_identity.py").read_bytes()).hexdigest()
        or host.get("artifacts") != report["artifacts"]
        or host.get("artifactInventory") != report["artifactInventory"]
        or host.get("executionRunId") != report["executionRunId"]
        or host.get("executionReceipt") != item["executionReceipt"]
        or host.get("containerReceiptSha256") != item["executionReceipt"]["sha256"]
        or host.get("imageArtifactId") not in report["artifacts"]
        or image.get("kind") != "image"
        or host.get("imageArtifactSha256") != image.get("sha256")
        or host.get("imageId") != image.get("imageId")
        or actual.get("imageId") != image.get("imageId")
        or host.get("imageInspect", {}).get("imageId") != image.get("imageId")
        or not re.fullmatch(r"[a-f0-9]{64}", host.get("containerId", ""))
        or actual.get("containerId") != host["containerId"]
    ):
        raise ValueError("Host receipt does not bind this actual container/image/run")
    execution = report["execution"]
    if (
        actual.get("status") not in {"running", "exited"}
        or not actual.get("startedAt")
        or actual["startedAt"].startswith("0001-")
        or actual.get("networkMode") != "none"
        or actual.get("readOnlyRoot") is not True
        or actual.get("privileged") is not False
        or actual.get("memory") != execution["memoryCeilingBytes"]
        or actual.get("memorySwap") != execution["memoryCeilingBytes"]
        or type(actual.get("deviceCount")) is not int
        or actual["deviceCount"] != 0
        or type(actual.get("deviceRequestCount")) is not int
        or actual["deviceRequestCount"] != 0
    ):
        raise ValueError("Actual host container isolation differs from measured CPU run")
    for mount in actual.get("mounts", []):
        destination = mount["destination"]
        if destination == "/" or any(
            destination == prefix or destination.startswith(prefix + "/")
            for prefix in ("/usr", "/opt", "/lib", "/bin", "/sbin")
        ):
            raise ValueError("Container mount overrides image executable content")


def check(manifest: Path, evidence_root: Path, source_commit: str, version: str) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        document.get("schemaVersion") != "document-files.qualification.v2"
        or document.get("version") != version
        or document.get("sourceCommit") != source_commit
    ):
        raise ValueError("Qualification identity does not match this release source")
    _identity(document, source_commit, version)
    assets = artifact_inventory(document, evidence_root, source_commit, version)
    checks = document.get("checks", [])
    if len({c["id"] for c in checks}) != len(checks):
        raise ValueError("Duplicate qualification checks")
    by_id = {c["id"]: c for c in checks}
    names = set(REQUIRED)
    cloud = document.get("support", {}).get("cloud_model")
    if cloud == "qualified" or "cloud_model" in by_id:
        names.add("cloud_model")
    # Model evidence first so generic mocked test reports can never qualify a model.
    for name in sorted(names, key=lambda n: (not n.endswith("_model"), n)):
        item = by_id.get(name, {})
        if item.get("passed") is not True:
            raise ValueError(f"Missing successful qualification: {name}")
        evidence = item.get("evidence", {})
        path, report = _linked(evidence_root, evidence["path"], evidence["sha256"])
        if report.get("sourceCommit") != source_commit or report.get("version") != version:
            raise ValueError(f"Stale evidence: {name}")
        if report.get("artifactInventory") != document["artifactInventory"]:
            raise ValueError(f"Report artifact inventory identity mismatch: {name}")
        _bindings(report, name, assets)
        if name.endswith("_model"):
            _core_execution(report, assets)
            report = _reviewed(report, item, evidence_root, path)
            if name == "local_model":
                report = _execution(
                    report,
                    evidence_root,
                    source_commit,
                    version,
                    path,
                    item["evidence"]["sha256"],
                    item["executionReceipt"],
                )
                _container_identity(report, item, evidence_root, assets)
            _actual_model(report, name, path, evidence_root)
        else:
            _operational(report, name, evidence_root, assets)
    if cloud not in {"qualified", "not-qualified"}:
        raise ValueError("Cloud support must be explicitly qualified or not-qualified")
    if document.get("scope") != "printed-ko-en-cpu16gb-full-document.v1":
        raise ValueError("Approved full-document scope declaration required")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    try:
        if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True):
            raise ValueError("Stable qualification requires a clean checkout")
        check(args.manifest, args.evidence_root, commit, version)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"Release NOT qualified: {exc}") from exc
    print(f"Release qualified: {version} ({commit})")


if __name__ == "__main__":
    main()
