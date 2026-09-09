"""Synthetic contract fixtures only; these are not release qualification evidence."""

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from test_evaluation_execution_identity import make_execution_artifacts

ROOT = Path(__file__).parents[1]


def gate_module():
    spec = importlib.util.spec_from_file_location(
        "gate", ROOT / "scripts/check_release_qualification.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evidence(tmp_path):
    gate = gate_module()
    identity = {"version": "1.8.0", "sourceCommit": "a" * 40, "dirtySource": False}
    model = {
        "adapter": "managed-llama-cpp.v1",
        "model": "qwen",
        "runtimeManifestSha256": "1" * 64,
        "modelManifestSha256": "2" * 64,
    }

    def write(name, value):
        path = tmp_path / name
        path.write_text(json.dumps(value))
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    assets = []
    for target in sorted(gate.PLATFORMS):
        core = tmp_path / f"core-{target}.zip"
        core.write_bytes(b"synthetic core fixture")
        core_sha = hashlib.sha256(core.read_bytes()).hexdigest()
        build = write(
            f"build-{target}.json",
            {
                **identity,
                "schemaVersion": "document-files.build-inventory.v2",
                "candidateMode": "stable",
                "target": target,
                "artifacts": {core.name: core_sha},
            },
        )
        assets.append(
            {
                "id": f"core-{target}",
                "kind": "core",
                "target": target,
                "path": core.name,
                "sha256": core_sha,
                "buildReceipt": build,
            }
        )
        for kind in ("runtime", "recognition"):
            pack = {
                "kind": "llama-cpp-runtime" if kind == "runtime" else kind,
                "platform": target,
                "provenance": {"build": identity},
            }
            raw = json.dumps(pack).encode()
            path = tmp_path / f"{kind}-{target}.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", raw)
            assets.append(
                {
                    "id": f"{kind}-{target}",
                    "kind": kind,
                    "target": target,
                    "path": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "manifestSha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    raw = json.dumps(
        {"kind": "model", "platform": "any", "provenance": {"build": identity}}
    ).encode()
    path = tmp_path / "model.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", raw)
    assets.append(
        {
            "id": "model",
            "kind": "model",
            "target": "any",
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "manifestSha256": hashlib.sha256(raw).hexdigest(),
        }
    )
    image = tmp_path / "image.tar"
    image.write_bytes(b"synthetic image fixture")
    image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    image_receipt = write(
        "image-build.json",
        {
            **identity,
            "schemaVersion": "document-files.image-build.v1",
            "imageDigest": "sha256:" + "5" * 64,
            "imageId": "sha256:" + "6" * 64,
            "archiveSha256": image_sha,
        },
    )
    assets.append(
        {
            "id": "image",
            "kind": "image",
            "target": "linux-x86_64",
            "path": image.name,
            "sha256": image_sha,
            "imageDigest": "sha256:" + "5" * 64,
            "imageId": "sha256:" + "6" * 64,
            "buildReceipt": image_receipt,
        }
    )
    inventory = {
        **identity,
        "schemaVersion": "document-files.artifact-inventory.v2",
        "artifacts": assets,
    }
    extra_assets, core_execution, _, _ = make_execution_artifacts(tmp_path, identity)
    assets.extend(extra_assets)
    inventory_ref = write("artifacts.json", inventory)
    model["runtimeManifestSha256"] = next(
        a["manifestSha256"] for a in assets if a["id"] == "runtime-linux-x86_64"
    )
    model["modelManifestSha256"] = next(a["manifestSha256"] for a in assets if a["id"] == "model")
    cases = []
    for index, format_id in enumerate([*sorted(gate.FORMATS), "pdf"]):
        result = {
            "source": {"sha256": "3" * 64},
            "extraction": {"status": "complete"},
            "validation": {"valid": True},
            "provenance": {"model": model},
        }
        path = tmp_path / f"result-{index}.json"
        path.write_text(json.dumps(result))
        cases.append(
            {
                "id": f"case-{index}",
                "inputPath": write(f"input-{index}.json", {"synthetic": index})["path"],
                "reviewSpecification": write(
                    f"spec-{index}.json",
                    {
                        "schemaVersion": "document-files.review-specification.v1",
                        "criteria": [
                            {"id": key, "description": key, "expected": True}
                            for key in gate.CRITERIA
                        ],
                    },
                ),
                "format": format_id,
                "printed": True,
                "holdout": True,
                "languages": ["ko", "en"],
                "pdfKind": "scan" if index == 9 else "text",
                "longDocument": True,
                "pageCount": 4,
                "regionCount": 4,
                "extractionStatus": "complete",
                "structurallyValid": True,
                "inputSha256": "3" * 64,
                "resultPath": path.name,
                "resultSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "semanticReview": {
                    "status": "passed",
                    "reviewer": "contract-fixture",
                    "independent": True,
                    "method": "human-ground-truth",
                    "findings": ["Synthetic fixture, not actual evaluation"],
                    "criteria": {key: True for key in gate.CRITERIA},
                },
            }
        )
    reports = {
        "local_model": {
            **identity,
            "schemaVersion": "document-files.model-qualification.v2",
            "passed": False,
            "executionKind": "actual-model",
            "artifactInventory": inventory_ref,
            "observation": {
                "packManifestSha256": next(
                    a["manifestSha256"] for a in assets if a["id"] == "recognition-linux-x86_64"
                )
            },
            "executionRunId": "synthetic-run",
            "coreExecution": core_execution,
            "artifacts": [
                "core-linux-x86_64",
                "executed-wheel",
                "evaluator-source",
                "runtime-linux-x86_64",
                "recognition-linux-x86_64",
                "model",
                "image",
            ],
            "model": model,
            "endpointKind": "local",
            "cases": cases,
            "execution": {
                "device": "cpu",
                "gpuUsed": False,
                "offline": True,
                "networkBlocked": True,
                "memoryCeilingVerified": True,
                "memoryCeilingBytes": 16 * 1024**3,
                "cgroupMemoryPeakBytes": 10 * 1024**3,
                "memorySwapMaxBytes": 0,
                "memorySwapPeakBytes": 0,
                "oomEventsDelta": 0,
                "oomKillEventsDelta": 0,
                "networkMode": "none",
                "exitCode": 0,
                "timedOut": False,
                "measurementComplete": True,
                "limitsUnchanged": True,
                "recorderErrors": [],
                "gpuDeviceCount": 0,
            },
        }
    }
    for name in gate.REQUIRED - {"local_model"}:
        required = (
            gate.HTTP_TESTS
            if name == "http_service"
            else gate.CONTAINER_TESTS
            if name == "container_internal"
            else gate.PLATFORM_TESTS
            if name.startswith("packaged_")
            else gate.CLIENT_TESTS
        )
        report = {
            **identity,
            "passed": True,
            "executionKind": "actual-installed",
            "tests": [{"id": key, "passed": True} for key in required],
        }
        if name.startswith("packaged_"):
            target = name.removeprefix("packaged_")
            report.update(
                target=target,
                artifactSha256="4" * 64,
                fullExtractionRoute="linux-cpu-container"
                if target == "macos-x86_64"
                else "native-cpu",
            )
        elif name.startswith("client_"):
            report.update(
                client=name.removeprefix("client_"), clientVersion="fixture", runtimeRoute="fixture"
            )
        runtime_target = (
            "linux-x86_64"
            if report.get("target") == "macos-x86_64"
            else report.get("target", "linux-x86_64")
        )
        core_target = report.get("target", "linux-x86_64")
        report["artifacts"] = [
            f"core-{core_target}",
            f"runtime-{runtime_target}",
            f"recognition-{runtime_target}",
            "model",
            "image",
        ]
        if name.startswith("packaged_"):
            report["artifactSha256"] = next(
                a["sha256"] for a in assets if a["id"] == f"core-{core_target}"
            )
        if name.startswith("client_"):
            report["capabilities"] = {"nativeDocuments": "qualified", "aiExtraction": "qualified"}
        observed = {
            **model,
            "runtimeManifestSha256": next(
                a["manifestSha256"] for a in assets if a["id"] == f"runtime-{runtime_target}"
            ),
        }
        report["aiResult"] = write(
            f"installed-{name}.json",
            {
                "source": {"sha256": "3" * 64},
                "extraction": {"status": "complete"},
                "validation": {"valid": True},
                "provenance": {"model": observed},
            },
        )
        report["artifactInventory"] = inventory_ref
        reports[name] = report
    document = {
        **identity,
        "schemaVersion": "document-files.qualification.v2",
        "artifactInventory": inventory_ref,
        "releaseAssets": [a["id"] for a in assets],
        "publishArtifactInventory": True,
        "scope": "printed-ko-en-cpu16gb-full-document.v1",
        "support": {"cloud_model": "not-qualified"},
    }

    # Snapshot input hashes in the stored source-linked results before any test mutations.
    for case in cases:
        case["inputSha256"] = hashlib.sha256(
            (tmp_path / case["inputPath"]).read_bytes()
        ).hexdigest()
        result_path = tmp_path / case["resultPath"]
        result = json.loads(result_path.read_text())
        result["source"]["sha256"] = case["inputSha256"]
        result_path.write_text(json.dumps(result))
        case["resultSha256"] = hashlib.sha256(result_path.read_bytes()).hexdigest()

    def run():
        checks = []
        for name, report in reports.items():
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(report))
            review_ref = None
            execution_ref = None
            container_ref = None
            if name.endswith("_model"):
                review_ref = write(
                    f"review-{name}.json",
                    {
                        "schemaVersion": "document-files.semantic-review.v1",
                        "sourceReportSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "passed": True,
                        "reviewer": "synthetic",
                        "independent": True,
                        "method": "human-ground-truth",
                        "cases": [
                            {
                                **case["semanticReview"],
                                "id": case["id"],
                                "inputSha256": case["inputSha256"],
                                "resultSha256": case["resultSha256"],
                                "reviewSpecificationSha256": case["reviewSpecification"]["sha256"],
                            }
                            for case in report["cases"]
                        ],
                    },
                )
                # Preserve independence mutation as a top-level reviewed-evidence condition.
                if any(not case["semanticReview"].get("independent") for case in report["cases"]):
                    broken = json.loads((tmp_path / review_ref["path"]).read_text())
                    broken["independent"] = False
                    review_ref = write(review_ref["path"], broken)
                measurements = write(
                    f"measurements-{name}.json", {"execution": report["execution"]}
                )
                execution_ref = write(
                    f"execution-{name}.json",
                    {
                        **identity,
                        "schemaVersion": "document-files.execution-receipt.v1",
                        "executionRunId": report["executionRunId"],
                        "recorderSha256": core_execution["recorderSha256"],
                        "artifacts": report["artifacts"],
                        "execution": report["execution"],
                        "measurements": measurements,
                        "outputs": [
                            {
                                "path": path.name,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            }
                        ],
                    },
                )
                container_ref = write(
                    f"container-{name}.json",
                    {
                        **identity,
                        "schemaVersion": "document-files.container-identity.v1",
                        "collectorSha256": hashlib.sha256(
                            (ROOT / "scripts/capture_container_identity.py").read_bytes()
                        ).hexdigest(),
                        "artifacts": report["artifacts"],
                        "artifactInventory": inventory_ref,
                        "executionRunId": report["executionRunId"],
                        "executionReceipt": execution_ref,
                        "containerReceiptSha256": execution_ref["sha256"],
                        "imageArtifactId": "image",
                        "imageArtifactSha256": image_sha,
                        "imageId": "sha256:" + "6" * 64,
                        "containerId": "c" * 64,
                        "imageInspect": {
                            "imageId": "sha256:" + "6" * 64,
                            "repoDigests": ["registry/image@sha256:" + "5" * 64],
                        },
                        "dockerInspect": {
                            "containerId": "c" * 64,
                            "imageId": "sha256:" + "6" * 64,
                            "running": False,
                            "status": "exited",
                            "startedAt": "2026-09-09T00:00:00Z",
                            "finishedAt": "2026-09-09T00:01:00Z",
                            "user": "10001:10001",
                            "memory": 16 * 1024**3,
                            "memorySwap": 16 * 1024**3,
                            "networkMode": "none",
                            "readOnlyRoot": True,
                            "privileged": False,
                            "deviceCount": 0,
                            "deviceRequestCount": 0,
                            "mounts": [],
                        },
                    },
                )
            checks.append(
                {
                    "id": name,
                    "passed": True,
                    "review": review_ref,
                    "executionReceipt": execution_ref,
                    "containerIdentityReceipt": container_ref,
                    "evidence": {
                        "path": path.name,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    },
                }
            )
        document["checks"] = checks
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(document))
        gate.check(path, tmp_path, identity["sourceCommit"], identity["version"])

    return reports, document, run, tmp_path


def test_complete_contract_and_explicit_unqualified_cloud(evidence):
    _, _, run, _ = evidence
    run()


@pytest.mark.parametrize(
    "mutation",
    [
        "partial",
        "mock",
        "gpu",
        "online",
        "large-memory",
        "missing-format",
        "missing-scan",
        "missing-long",
        "missing-criteria",
        "self-review",
        "mismatched-result",
    ],
)
def test_model_shortcuts_never_qualify(evidence, mutation):
    reports, _, run, root = evidence
    report = reports["local_model"]
    if mutation == "partial":
        for case in report["cases"]:
            case["extractionStatus"] = "partial"
    elif mutation == "mock":
        report["model"]["adapter"] = "scripted-test"
    elif mutation == "gpu":
        report["execution"]["gpuUsed"] = True
    elif mutation == "online":
        report["execution"]["networkBlocked"] = False
    elif mutation == "large-memory":
        report["execution"]["memoryCeilingBytes"] = 32 * 1024**3
    elif mutation == "missing-format":
        report["cases"] = [c for c in report["cases"] if c["format"] != "hwp"]
    elif mutation == "missing-scan":
        for case in report["cases"]:
            case["pdfKind"] = "text"
    elif mutation == "missing-long":
        for case in report["cases"]:
            case["longDocument"] = False
    elif mutation == "missing-criteria":
        for case in report["cases"]:
            case["semanticReview"]["criteria"].pop("cross_page_notes")
    elif mutation == "self-review":
        report["cases"][0]["semanticReview"]["independent"] = False
    else:
        case = report["cases"][0]
        path = root / case["resultPath"]
        result = json.loads(path.read_text())
        result["extraction"]["status"] = "partial"
        path.write_text(json.dumps(result))
        case["resultSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        run()


@pytest.mark.parametrize(
    "mutation",
    [
        "cloud-claim",
        "cloud-omitted",
        "intel-route",
        "http-auth",
        "container-offline",
        "client-version",
        "generic-tests",
    ],
)
def test_operational_support_requires_direct_evidence(evidence, mutation):
    reports, manifest, run, _ = evidence
    if mutation == "cloud-claim":
        manifest["support"]["cloud_model"] = "qualified"
    elif mutation == "cloud-omitted":
        manifest.pop("support")
    elif mutation == "intel-route":
        reports["packaged_macos-x86_64"]["fullExtractionRoute"] = "native-cpu"
    elif mutation in {"http-auth", "container-offline"}:
        name, check = (
            ("http_service", "authentication")
            if mutation == "http-auth"
            else ("container_internal", "offline_extraction")
        )
        reports[name]["tests"] = [t for t in reports[name]["tests"] if t["id"] != check]
    elif mutation == "client-version":
        reports["client_chatgpt"].pop("clientVersion")
    else:
        reports["http_service"]["tests"] = [{"id": "anything", "passed": True}]
    with pytest.raises(ValueError):
        run()


def test_build_workflow_pins_actions_and_scopes_attestation():
    import re

    body = (ROOT / ".github/workflows/build.yml").read_text()
    assert all(
        re.fullmatch(r"[a-f0-9]{40}", pin) for pin in re.findall(r"uses: [^@\s]+@([^\s]+)", body)
    )
    assert "attest-build-provenance@" in body
    assert "subject-path:" in body and "candidate/*.pack.zip" not in body
    assert "core-python-sbom.cdx.json" in body and "pip-audit==2.10.1" in body
    assert "--fix" not in body


def test_linked_result_must_remain_inside_evidence_root(evidence):
    reports, _, run, _ = evidence
    reports["local_model"]["cases"][0]["resultPath"] = "../outside.json"
    with pytest.raises(ValueError, match="Unsafe evidence path"):
        run()


def test_result_corruption_is_not_hidden_by_report_success(evidence):
    reports, _, run, root = evidence
    path = root / reports["local_model"]["cases"][0]["resultPath"]
    path.write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        run()


@pytest.mark.parametrize(
    "field,value",
    [
        ("memorySwapMaxBytes", 1),
        ("memorySwapPeakBytes", 1),
        ("oomEventsDelta", 1),
        ("oomKillEventsDelta", 1),
        ("gpuDeviceCount", 1),
        ("networkMode", "bridge"),
        ("exitCode", 1),
        ("timedOut", True),
        ("measurementComplete", False),
        ("limitsUnchanged", False),
        ("recorderErrors", ["sampling failure"]),
        ("cgroupMemoryPeakBytes", 17 * 1024**3),
        ("memorySwapMaxBytes", False),
    ],
)
def test_cgroup_resource_failures_never_qualify(evidence, field, value):
    reports, _, run, _ = evidence
    reports["local_model"]["execution"][field] = value
    with pytest.raises(ValueError, match="CPU 16GB"):
        run()


def test_rss_diagnostic_is_not_the_cgroup_ceiling(evidence):
    reports, _, run, _ = evidence
    reports["local_model"]["execution"]["peakProcessTreeBytes"] = 30 * 1024**3
    run()


def test_chatgpt_honest_unsupported_ai_is_not_success(evidence):
    reports, _, run, _ = evidence
    chat = reports["client_chatgpt"]
    chat["capabilities"]["aiExtraction"] = "not-supported"
    chat.pop("aiResult")
    chat["artifacts"] = ["core-linux-x86_64"]
    chat["tests"] = [t for t in chat["tests"] if t["id"] != "extraction_result"]
    chat["tests"].append({"id": "unsupported_ai_is_explicit", "passed": True})
    run()
    chat["tests"].append({"id": "extraction_result", "passed": True})
    with pytest.raises(ValueError, match="must not"):
        run()


@pytest.mark.parametrize("client", ["codex", "claude_code", "claude_desktop"])
def test_other_clients_require_actual_ai(evidence, client):
    reports, _, run, _ = evidence
    reports[f"client_{client}"]["capabilities"]["aiExtraction"] = "not-supported"
    with pytest.raises(ValueError, match="AI capability"):
        run()


def test_installed_sha_must_match_actual_archive(evidence):
    reports, _, run, _ = evidence
    reports["packaged_linux-x86_64"]["artifactSha256"] = "f" * 64
    with pytest.raises(ValueError, match="Installed artifact"):
        run()


def test_release_archive_corruption_is_rejected(evidence):
    _, _, run, root = evidence
    (root / "core-linux-x86_64.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        run()


def test_missing_recognition_binding_rejected(evidence):
    reports, _, run, _ = evidence
    reports["local_model"]["artifacts"].remove("recognition-linux-x86_64")
    with pytest.raises(ValueError, match="pipeline artifact"):
        run()


def test_frozen_review_spec_corruption_rejected(evidence):
    _, _, run, root = evidence
    (root / "spec-0.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        run()


def test_execution_receipt_cannot_be_reused_for_another_report(evidence):
    _, manifest, run, root = evidence
    run()
    item = next(c for c in manifest["checks"] if c["id"] == "local_model")
    ref = item["executionReceipt"]
    path = root / ref["path"]
    receipt = json.loads(path.read_text())
    receipt["executionRunId"] = "another-run"
    path.write_text(json.dumps(receipt))
    ref["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="this model run"):
        gate_module().check(manifest_path, root, manifest["sourceCommit"], manifest["version"])
