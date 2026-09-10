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
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {"macos-aarch64", "macos-x86_64", "windows-x86_64", "linux-x86_64", "linux-aarch64"}
LINUX_ARCHITECTURES = {"linux-x86_64": "amd64", "linux-aarch64": "arm64"}
LINUX_CHECKS = {
    "local_model": ("local_model", "linux-x86_64"),
    "http_service": ("http_service", "linux-x86_64"),
    "container_internal": ("container_internal", "linux-x86_64"),
    "local_arm64_model": ("local_model", "linux-aarch64"),
    "http_service_arm64": ("http_service", "linux-aarch64"),
    "container_internal_arm64": ("container_internal", "linux-aarch64"),
}
CLIENTS = {"codex", "claude_code", "claude_desktop", "chatgpt"}
REQUIRED = frozenset(
    set(LINUX_CHECKS) | {f"packaged_{p}" for p in PLATFORMS} | {f"client_{c}" for c in CLIENTS}
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
    "persistent_results",
    "restart_interrupted",
    "actual_ai_complete",
    "delete_results",
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
        if name == "http_service" and observed.get("adapter") != "managed-llama-cpp.v1":
            raise ValueError("Actual installed local HTTP model required")
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


def _http_execution(report, item, root, path, digest, assets):
    """Verify installed HTTP runner + wheel + packs + measured loopback container."""
    import tarfile

    _identity(report, report["sourceCommit"], report["version"])
    _bindings(report, "container_pipeline", assets)
    if report.get("observationProfile") != "in-container-loopback.v1" or report.get(
        "notCovered"
    ) != ["host-published-port-access", "independent-document-quality-suite"]:
        raise ValueError("HTTP review requires explicit internal loopback-only coverage")
    actual = report.get("coreExecution", {})
    core = assets.get(actual.get("coreArtifactId"), {})
    source = assets.get(actual.get("sourceArtifactId"), {})
    if (
        actual.get("verification") != "installed-wheel-and-runner-source-exact.v1"
        or actual.get("coreArtifactId") not in report["artifacts"]
        or actual.get("sourceArtifactId") not in report["artifacts"]
        or core.get("kind") != "core"
        or source.get("kind") != "core"
        or not str(core.get("verifiedPath", "")).endswith(".whl")
        or not str(source.get("verifiedPath", "")).endswith(".tar.gz")
        or actual.get("coreArtifactSha256") != core["sha256"]
        or actual.get("sourceArtifactSha256") != source["sha256"]
    ):
        raise ValueError("Installed HTTP core/source artifacts required")
    spec = importlib.util.spec_from_file_location(
        "http_execution_identity", ROOT / "evaluation/execution_identity.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if actual.get("packageTreeSha256") != helper.tree_digest(
        helper.wheel_package(core["verifiedPath"])
    ):
        raise ValueError("Installed HTTP package differs from selected wheel")
    with tarfile.open(source["verifiedPath"], "r:gz") as archive:

        def member_sha(member):
            matches = [
                m
                for m in archive.getmembers()
                if Path(m.name).parts[1:] == tuple(member.split("/"))
            ]
            if len(matches) != 1 or not matches[0].isfile() or ".." in Path(matches[0].name).parts:
                raise ValueError("HTTP runner source member missing or ambiguous")
            return hashlib.sha256(archive.extractfile(matches[0]).read()).hexdigest()

        runner = member_sha("scripts/run_http_installation_check.py")
        recorder = member_sha("scripts/measure_cpu_execution.py")
        if runner != actual.get("runnerSha256") or recorder != actual.get("recorderSha256"):
            raise ValueError("HTTP executable identity differs from source artifact")
        # The current validator interprets observations emitted by this exact runner contract.
        if (
            runner
            != hashlib.sha256(
                (ROOT / "scripts/run_http_installation_check.py").read_bytes()
            ).hexdigest()
        ):
            raise ValueError("HTTP observations require the matching reviewed runner")
    for kind, key in (
        ("runtime", "runtimeId"),
        ("model", "modelId"),
        ("recognition", "recognitionPackId"),
    ):
        if not any(
            assets[a]["kind"] == kind
            and assets[a].get("manifestSha256") == actual.get("packManifestSha256", {}).get(key)
            for a in report["artifacts"]
        ):
            raise ValueError("HTTP configured pack differs from selected artifact")
    measured = _execution(
        report,
        root,
        report["sourceCommit"],
        report["version"],
        path,
        digest,
        item["executionReceipt"],
    )
    _container_identity(measured, item, root, assets)
    execution = measured["execution"]
    ceiling, peak = execution.get("memoryCeilingBytes"), execution.get("cgroupMemoryPeakBytes")
    if (
        execution.get("device") != "cpu"
        or execution.get("networkMode") != "none"
        or any(
            execution.get(k) is not True
            for k in (
                "offline",
                "networkBlocked",
                "memoryCeilingVerified",
                "measurementComplete",
                "limitsUnchanged",
            )
        )
        or execution.get("gpuUsed") is not False
        or execution.get("timedOut") is not False
        or execution.get("recorderErrors") != []
        or type(ceiling) is not int
        or not 0 < ceiling <= 16 * 1024**3
        or type(peak) is not int
        or not 0 < peak <= ceiling
        or any(
            type(execution.get(k)) is not int or execution[k] != 0
            for k in (
                "memorySwapMaxBytes",
                "memorySwapPeakBytes",
                "oomEventsDelta",
                "oomKillEventsDelta",
                "gpuDeviceCount",
                "exitCode",
            )
        )
    ):
        raise ValueError("Measured HTTP CPU16GB execution required")
    _, receipt = _linked(root, item["executionReceipt"]["path"], item["executionReceipt"]["sha256"])
    _, measurements = _linked(
        root, receipt["measurements"]["path"], receipt["measurements"]["sha256"]
    )
    limits = measurements.get("limitsBefore", {})
    cpu = limits.get("cpuQuota")
    if (
        type(cpu) not in {int, float}
        or not 0 < cpu <= 4
        or limits != measurements.get("limitsAfter")
    ):
        raise ValueError("Measured stable HTTP CPU quota required")
    if actual.get("selectedImageArtifactId") not in report["artifacts"]:
        raise ValueError("Selected HTTP image missing")
    _, host = _linked(
        root, item["containerIdentityReceipt"]["path"], item["containerIdentityReceipt"]["sha256"]
    )
    if host.get("imageArtifactId") != actual["selectedImageArtifactId"]:
        raise ValueError("HTTP selected image differs from actual image")
    return measured


def _reviewed_operational(report, item, root, path, assets):
    spec = importlib.util.spec_from_file_location(
        "operational_review", ROOT / "scripts/review_operational.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    _, review = _linked(root, item["review"]["path"], item["review"]["sha256"])
    if review.get("schemaVersion") != "document-files.operational-review.v1":
        raise ValueError("Independent operational review receipt required")
    decisions_ref = review["reviewDecisions"]
    _, decisions = _linked(root, decisions_ref["path"], decisions_ref["sha256"])
    for key in ("executionReceipt", "containerIdentityReceipt"):
        if item.get(key) != decisions.get(key):
            raise ValueError("Operational review uses different execution proofs")
    assessed = helper.assess(
        root,
        report,
        decisions,
        item["evidence"],
        sys.modules[__name__] if __name__ in sys.modules else helper.gate_module(),
    )
    if (
        any(review.get(key) != value for key, value in assessed.items())
        or assessed["passed"] is not True
    ):
        raise ValueError("Operational review is stale, incomplete or lacks actual observations")
    measured = _http_execution(report, item, root, path, item["evidence"]["sha256"], assets)
    return {**measured, "passed": True}


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
    images = []
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
                build.get("schemaVersion") != "document-files.image-build.v2"
                or asset.get("target") not in LINUX_ARCHITECTURES
                or build.get("target") != asset.get("target")
                or build.get("imageInspect", {}).get("os") != "linux"
                or build.get("imageInspect", {}).get("architecture")
                != LINUX_ARCHITECTURES.get(asset.get("target"))
                or build.get("imageInspect", {}).get("imageId") != asset.get("imageId")
                or build.get("status") != "built-unqualified"
                or build.get("stage") != "complete"
                or build.get("installedNativeVerification") != "selected-core-rhwp-exact"
                or build.get("installedCoreVerification") != "selected-wheel-exact"
                or build.get("probeCleanup", {}).get("status") != "removed"
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
            images.append((asset, build))
        elif kind != "metadata":
            raise ValueError("Unknown release artifact kind")
        assets[identifier] = {**asset, "verifiedPath": actual}
    if not assets:
        raise ValueError("Empty release artifact inventory")
    if images:
        spec = importlib.util.spec_from_file_location(
            "qualification_image_inputs", ROOT / "scripts/build_release_image.py"
        )
        image_builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(image_builder)
        for image, build in images:
            image_builder.export_identity(
                assets[image["id"]]["verifiedPath"], image["imageId"], target=image["target"]
            )
            inputs = build.get("inputs", {})
            cores = [
                a
                for a in assets.values()
                if a["kind"] == "core"
                and a.get("target") == image["target"]
                and a["sha256"] == inputs.get("coreArchiveSha256")
                and a["verifiedPath"].suffix == ".zip"
            ]
            if len(cores) != 1 or cores[0]["buildReceipt"]["sha256"] != inputs.get(
                "coreReceiptSha256"
            ):
                raise ValueError("Image requires its exact Linux portable core archive")
            _, core_build = _linked(
                root, cores[0]["buildReceipt"]["path"], cores[0]["buildReceipt"]["sha256"]
            )
            selected = {}
            for suffix, key in ((".whl", "wheelSha256"), (".tar.gz", "sourceArchiveSha256")):
                if not any(
                    n.endswith(suffix) and h == inputs.get(key)
                    for n, h in core_build["artifacts"].items()
                ):
                    raise ValueError("Image wheel/source not in the same core build")
                matches = [
                    a
                    for a in assets.values()
                    if a["sha256"] == inputs.get(key) and a["verifiedPath"].name.endswith(suffix)
                ]
                if len(matches) != 1:
                    raise ValueError("Image requires exact wheel and source inventory entries")
                selected[key] = matches[0]["verifiedPath"]
            patch_sha = image_builder.source_files(selected["sourceArchiveSha256"]).get(
                "patches/rhwp/checkbox-preservation.patch"
            )
            if patch_sha is None:
                raise ValueError("Image source must include the selected rhwp patch")
            _, native = image_builder.portable_rhwp(
                cores[0]["verifiedPath"],
                commit,
                version,
                inputs["wheelSha256"],
                patch_sha,
                target=image["target"],
            )
            if build.get("rhwp") != native:
                raise ValueError("Image rhwp does not match its portable core")
            build_path = root / image["buildReceipt"]["path"]
            proof = build["installedNativeEvidence"]
            proof_name = Path(proof["path"])
            if proof_name.is_absolute() or ".." in proof_name.parts or "\\" in proof["path"]:
                raise ValueError("Unsafe image installed-native evidence path")
            proof_path = build_path.parent / proof_name
            _, observed = _linked(root, str(proof_path.relative_to(root)), proof["sha256"])
            if observed.get("core") != image_builder.wheel_tree(selected["wheelSha256"]):
                raise ValueError("Image installed core proof differs from selected wheel")
            if observed.get("rhwp") != {k: native[k] for k in ("path", "version", "files")}:
                raise ValueError("Image installed rhwp proof differs from portable core")
    return assets


def _bindings(report: dict, name: str, assets: dict) -> None:
    role, required_target = LINUX_CHECKS.get(name, (name, None))
    name = role
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
        name in {"local_model", "http_service", "container_internal", "container_pipeline"}
        or report.get("fullExtractionRoute") == "linux-cpu-container"
        or report.get("runtimeRoute") == "linux-cpu-container"
    )
    if container:
        required.add("image")
    if not kinds >= required:
        raise ValueError(f"Incomplete pipeline artifact bindings: {name}")
    pipeline_target = None
    if container:
        images = [a for a in chosen if a["kind"] == "image"]
        if len(images) != 1 or images[0].get("target") not in LINUX_ARCHITECTURES:
            raise ValueError("One exact Linux image target required per execution")
        pipeline_target = images[0]["target"]
        if required_target is not None and pipeline_target != required_target:
            raise ValueError("Linux qualification cannot substitute another architecture")
        if required_target is not None:
            portable = [
                a
                for a in chosen
                if a["kind"] == "core" and str(a.get("verifiedPath", "")).endswith(".zip")
            ]
            if not portable or any(a.get("target") != required_target for a in portable):
                raise ValueError("Linux execution requires matching portable core target")
        for kind in ("runtime", "recognition"):
            selected = [a for a in chosen if a["kind"] == kind]
            if not unsupported and (
                len(selected) != 1 or selected[0].get("target") != pipeline_target
            ):
                raise ValueError("Container image and selected pack architectures differ")
    if name.startswith("packaged_"):
        target = name.removeprefix("packaged_")
        if not any(
            a["kind"] == "core"
            and a.get("target") == target
            and a["sha256"] == report.get("artifactSha256")
            for a in chosen
        ):
            raise ValueError("Installed artifact differs from release asset")
        runtime_target = "linux-x86_64" if target == "macos-x86_64" else target
        if container and pipeline_target != runtime_target:
            raise ValueError("Installed platform and container architecture differ")
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
    before, after = measured.get("limitsBefore", {}), measured.get("limitsAfter", {})
    cpu = before.get("cpuQuota")
    if (
        type(cpu) not in (int, float)
        or not 0 < cpu <= 4
        or after.get("cpuQuota") != cpu
        or receipt["execution"].get("cpuQuota") != cpu
    ):
        raise ValueError("Measured unchanged CPU quota of at most four required")
    return {**report, "execution": receipt["execution"]}


def _measured_cpu(execution):
    ceiling, peak = execution.get("memoryCeilingBytes"), execution.get("cgroupMemoryPeakBytes")
    if (
        type(ceiling) is not int
        or not 0 < ceiling <= 16 * 1024**3
        or type(peak) is not int
        or not 0 < peak <= ceiling
        or execution.get("device") != "cpu"
        or execution.get("gpuUsed") is not False
        or execution.get("networkMode") != "none"
        or any(
            execution.get(k) is not True
            for k in (
                "offline",
                "networkBlocked",
                "memoryCeilingVerified",
                "measurementComplete",
                "limitsUnchanged",
            )
        )
        or any(
            type(execution.get(k)) is not int or execution[k] != 0
            for k in (
                "memorySwapMaxBytes",
                "memorySwapPeakBytes",
                "oomEventsDelta",
                "oomKillEventsDelta",
                "gpuDeviceCount",
                "exitCode",
            )
        )
        or execution.get("timedOut") is not False
        or execution.get("recorderErrors") != []
    ):
        raise ValueError("Measured CPU 16GB offline execution required")


def _container_identity(report: dict, item: dict, root: Path, assets: dict) -> None:
    ref = item.get("containerIdentityReceipt")
    if not isinstance(ref, dict):
        raise ValueError("Independent host Docker identity receipt required")
    _, host = _linked(root, ref["path"], ref["sha256"])
    _identity(host, report["sourceCommit"], report["version"])
    image = assets.get(host.get("imageArtifactId"), {})
    actual = host.get("dockerInspect", {})
    if (
        host.get("schemaVersion") != "document-files.container-identity.v2"
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
        or image.get("target") not in LINUX_ARCHITECTURES
        or host.get("imageInspect", {}).get("os") != "linux"
        or host.get("imageInspect", {}).get("architecture")
        != LINUX_ARCHITECTURES.get(image.get("target"))
        or not re.fullmatch(r"[a-f0-9]{64}", host.get("containerId", ""))
        or actual.get("containerId") != host["containerId"]
    ):
        raise ValueError("Host receipt does not bind this actual container/image/run")
    execution = report["execution"]
    _measured_cpu(execution)
    nano, quota, period = (actual.get(k) for k in ("nanoCpus", "cpuQuota", "cpuPeriod"))
    if any(type(v) is not int or v < 0 for v in (nano, quota, period)):
        raise ValueError("Actual Docker CPU quota required")
    if nano:
        host_cpu = nano / 1_000_000_000
        if quota or period:
            raise ValueError("Ambiguous Docker CPU quota")
    elif quota and period:
        host_cpu = quota / period
    else:
        raise ValueError("Actual Docker CPU quota required")
    if not 0 < host_cpu <= 4 or host_cpu != execution.get("cpuQuota"):
        raise ValueError("Actual Docker CPU quota differs from measured run")
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


def redistribution_review(document: dict, root: Path, assets: dict) -> set[str]:
    """Bind human redistribution decisions to bytes; never infer license approval."""
    reference = document.get("redistributionReview")
    if not isinstance(reference, dict):
        raise ValueError("Redistribution review required")
    path = _file(root, reference["path"], reference["sha256"])
    if path.stat().st_size > 4 * 1024**2:
        raise ValueError("Redistribution review size limit")
    review = json.loads(path.read_bytes())
    if not isinstance(review, dict):
        raise ValueError("Redistribution review must be an object")
    _identity(review, document["sourceCommit"], document["version"])
    if (
        review.get("schemaVersion") != "document-files.redistribution-review.v1"
        or review.get("artifactInventory") != document["artifactInventory"]
    ):
        raise ValueError("Redistribution review candidate mismatch")
    rows = review.get("artifacts")
    expected = {key for key, asset in assets.items() if asset["kind"] != "metadata"}
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Redistribution artifact reviews required")
    ids = [row.get("artifactId") for row in rows]
    if any(not isinstance(key, str) for key in ids) or len(set(ids)) != len(ids):
        raise ValueError("Duplicate or invalid redistribution artifact review")
    if set(ids) != expected:
        raise ValueError("Redistribution review must cover every candidate exactly once")

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    public = set()
    for row in rows:
        asset = assets[row["artifactId"]]
        if (
            row.get("sha256") != asset["sha256"]
            or row.get("status") != "approved"
            or not text(row.get("reviewedBy"))
            or not isinstance(row.get("findings"), list)
            or not row["findings"]
            or not all(text(finding) for finding in row["findings"])
            or row.get("openIssues") != []
        ):
            raise ValueError("Redistribution review incomplete or stale")
        required = row.get("requiredPublicArtifacts")
        if not isinstance(required, list):
            raise ValueError("Redistribution public artifact list required")
        if not required and not text(row.get("noAdditionalPublicArtifactsReason")):
            raise ValueError("Redistribution absence of public artifacts needs review rationale")
        seen = set()
        for item in required:
            if not isinstance(item, dict):
                raise ValueError("Invalid redistribution public artifact")
            key = item.get("artifactId")
            if (
                not isinstance(key, str)
                or key not in assets
                or key in seen
                or item.get("sha256") != assets[key]["sha256"]
                or item.get("role") not in {"source", "recipe", "notice"}
                or not text(item.get("reason"))
            ):
                raise ValueError("Redistribution public artifact identity mismatch")
            seen.add(key)
            public.add(key)
        notices = row.get("embeddedNotices", [])
        if not isinstance(notices, list):
            raise ValueError("Invalid embedded notice list")
        if notices:
            _redistribution_zip_notices(asset["verifiedPath"], notices)
    return public


def _redistribution_zip_notices(path: Path, notices: list) -> None:
    """Read selected ZIP notices only, with bounded members and no extraction."""
    import stat
    import unicodedata

    def safe(name):
        if (
            not isinstance(name, str)
            or not name
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("Unsafe embedded notice path")
        return unicodedata.normalize("NFC", name).casefold()

    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 100000 or len(notices) > 4096:
            raise ValueError("Embedded notice member limit")
        members, folded = {}, set()
        for info in infos:
            raw = info.orig_filename
            name = raw[:-1] if info.is_dir() else raw
            key = safe(name)
            mode = info.external_attr >> 16
            if (
                raw != info.filename
                or key in folded
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                or (not info.is_dir() and stat.S_IFMT(mode) == stat.S_IFDIR)
                or (info.is_dir() and stat.S_IFMT(mode) == stat.S_IFREG)
                or info.flag_bits & 1
            ):
                raise ValueError("Unsafe or duplicate ZIP notice member")
            folded.add(key)
            if not info.is_dir():
                members[name] = info
        seen, total = set(), 0
        for item in notices:
            if not isinstance(item, dict):
                raise ValueError("Invalid embedded notice")
            name = item.get("path")
            key = safe(name)
            digest = item.get("sha256")
            if (
                key in seen
                or name not in members
                or not isinstance(digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                raise ValueError("Missing or duplicate embedded notice")
            seen.add(key)
            member = members[name]
            total += member.file_size
            if member.file_size > 16 * 1024**2 or total > 64 * 1024**2:
                raise ValueError("Embedded notice byte limit")
            h, size = hashlib.sha256(), 0
            with archive.open(member) as stream:
                while chunk := stream.read(min(65536, member.file_size - size + 1)):
                    size += len(chunk)
                    if size > member.file_size:
                        raise ValueError("Embedded notice size mismatch")
                    h.update(chunk)
            if size != member.file_size or h.hexdigest() != digest:
                raise ValueError("Embedded notice checksum mismatch")


def check(manifest: Path, evidence_root: Path, source_commit: str, version: str) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        document.get("schemaVersion") != "document-files.qualification.v4"
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
    for name in sorted(
        names, key=lambda n: (not LINUX_CHECKS.get(n, (n, None))[0].endswith("_model"), n)
    ):
        role = LINUX_CHECKS.get(name, (name, None))[0]
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
        if role.endswith("_model"):
            _core_execution(report, assets)
            report = _reviewed(report, item, evidence_root, path)
            if role == "local_model":
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
            _actual_model(report, role, path, evidence_root)
        else:
            if role == "http_service":
                report = _reviewed_operational(report, item, evidence_root, path, assets)
            if role == "container_internal":
                _core_execution(report, assets)
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
            _operational(report, role, evidence_root, assets)
    redistribution_review(document, evidence_root, assets)
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
