"""Synthetic contract fixtures only; these are not release qualification evidence."""

import copy
import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from test_evaluation_execution_identity import identity_helper, make_execution_artifacts

ROOT = Path(__file__).parents[1]
X64_CONFIG = b'{"os":"linux","architecture":"amd64","rootfs":{"type":"layers","diff_ids":[]}}'
ARM_CONFIG = X64_CONFIG.replace(b"amd64", b"arm64")
X64_IMAGE = "sha256:" + hashlib.sha256(X64_CONFIG).hexdigest()
ARM_IMAGE = "sha256:" + hashlib.sha256(ARM_CONFIG).hexdigest()


def synthetic_image(path, config):
    with tarfile.open(path, "w") as out:
        files = {"config.json": config, "manifest.json": b'[{"Config":"config.json","Layers":[]}]'}
        for name, raw in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            out.addfile(member, io.BytesIO(raw))


def gate_module():
    spec = importlib.util.spec_from_file_location(
        "gate", ROOT / "scripts/check_release_qualification.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def operational_helper():
    spec = importlib.util.spec_from_file_location(
        "http_review", ROOT / "scripts/review_operational.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def http_fixture(report, assets, core, write, root):
    """Synthetic observations for contract testing, never release evidence."""
    source = next(a for a in assets if a["id"] == "evaluator-source")
    raw_input = write("http-input.json", {"synthetic": "not an actual document run"})
    result = json.loads((root / report["aiResult"]["path"]).read_bytes())
    result.update(resultRevision=4)
    result["source"]["sha256"] = raw_input["sha256"]
    result["extraction"]["modelCalls"] = 4
    report["aiResult"] = write("installed-http_service.json", result)
    partial = write(
        "http-partial.json",
        {
            "resultRevision": 1,
            "extraction": {"modelCalls": 1},
            "issues": [{"code": "model_call_budget_exceeded"}],
        },
    )
    cancelled = write("http-cancelled.json", {"resultRevision": 2, "status": "partial"})
    report.update(
        schemaVersion="document-files.http-installation-run.v1",
        observationProfile="in-container-loopback.v1",
        notCovered=["host-published-port-access", "independent-document-quality-suite"],
        passed=False,
        releaseQualification=False,
        checksPassed=True,
        executionRunId="http-run",
        jobId="http-job",
        input=raw_input,
        partialResult=partial,
        cancelledResult=cancelled,
        budget={"maxModelCalls": 12, "completionSeconds": 900},
        residualCancelChildren=[],
        cleanupForcedChildren=[],
        reviewSpecification=write(
            "http-spec.json",
            {
                "schemaVersion": "document-files.review-specification.v1",
                "criteria": [{"id": "exact_values", "expected": "Synthetic fixture only"}],
            },
        ),
    )
    report["artifacts"].extend(["executed-wheel", "evaluator-source"])
    report["coreExecution"] = {
        "verification": "installed-wheel-and-runner-source-exact.v1",
        "coreArtifactId": "executed-wheel",
        "coreArtifactSha256": core["coreArtifactSha256"],
        "packageTreeSha256": core["packageTreeSha256"],
        "sourceArtifactId": source["id"],
        "sourceArtifactSha256": source["sha256"],
        "recorderSha256": core["recorderSha256"],
        "runnerSha256": hashlib.sha256(
            (ROOT / "scripts/run_http_installation_check.py").read_bytes()
        ).hexdigest(),
        "selectedImageArtifactId": "image",
        "actualImageIdentityVerified": False,
        "packManifestSha256": {
            key: next(a["manifestSha256"] for a in assets if a["id"] == name)
            for key, name in (
                ("runtimeId", "runtime-linux-x86_64"),
                ("modelId", "model"),
                ("recognitionPackId", "recognition-linux-x86_64"),
            )
        },
    }
    children = [{"pid": 321, "startTicks": "456", "name": "llama-server"}]
    observations = {
        "authentication": {"anonymousStatus": 401, "invalidTokenStatus": 401},
        "no_path_or_url_input": {
            "bodyStatuses": [415] * 3,
            "optionErrors": [{"status": 400, "error": "invalid-options"}] * 3,
        },
        "idempotency_same": {
            "status": 202,
            "initialJobId": "http-job",
            "repeatedJobId": "http-job",
        },
        "idempotency_conflict": {"status": 409, "error": "idempotency-conflict"},
        "input_snapshot": {
            "snapshotSha256": raw_input["sha256"],
            "submittedSha256": raw_input["sha256"],
        },
        "explicit_budget": {"httpStatus": 200, "jobStatus": "partial", "partialResult": partial},
        "cancel_tree": {
            "jobStatus": "cancelled",
            "childrenBefore": children,
            "childrenAliveAfter": [],
        },
        "persistent_results": {
            "httpStatus": 200,
            "before": cancelled,
            "after": cancelled,
            "snapshotSha256": raw_input["sha256"],
        },
        "restart_interrupted": {
            "before": {"executionStatus": "running", "attempt": 2},
            "after": {"status": "interrupted", "attempt": 2},
            "afterDelay": {"status": "interrupted", "attempt": 2},
            "childrenBefore": children,
            "childrenAliveAfterStop": [],
        },
        "resume_checkpoint": {
            "httpStatus": 200,
            "finalStatus": {"attempt": 3},
            "cancelledAttempt": 1,
            "result": report["aiResult"],
            "cancelledResult": cancelled,
        },
        "actual_ai_complete": {"jobStatus": "complete", "result": report["aiResult"]},
        "delete_results": {
            "deleteStatus": 200,
            "deleted": True,
            "lookupStatus": 404,
            "uploadExists": False,
            "resultDatabaseExists": False,
        },
    }
    report["lifecycleEvidence"] = write(
        "http-lifecycle.json",
        {
            "schemaVersion": "document-files.http-lifecycle-observations.v1",
            "executionRunId": "http-run",
            "jobId": "http-job",
            "observations": observations,
        },
    )


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
    synthetic_image(image, X64_CONFIG)
    image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
    image_receipt = write(
        "image-build.json",
        {
            **identity,
            "schemaVersion": "document-files.image-build.v1",
            "imageDigest": "sha256:" + "5" * 64,
            "imageId": X64_IMAGE,
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
            "imageId": X64_IMAGE,
            "buildReceipt": image_receipt,
        }
    )
    inventory = {
        **identity,
        "schemaVersion": "document-files.artifact-inventory.v2",
        "artifacts": assets,
    }
    extra_assets, core_execution, _, _ = make_execution_artifacts(tmp_path, identity)
    # Add the exact HTTP runner to the synthetic source archive; no executable is run.
    source_asset = next(a for a in extra_assets if a["id"] == "evaluator-source")
    source_path = tmp_path / source_asset["path"]
    with tarfile.open(source_path) as archive:
        members = [
            (member, archive.extractfile(member).read()) for member in archive if member.isfile()
        ]
    prefix = members[0][0].name.split("/")[0]
    with tarfile.open(source_path, "w:gz") as archive:
        for member, raw in members:
            archive.addfile(member, io.BytesIO(raw))
        raw = (ROOT / "scripts/run_http_installation_check.py").read_bytes()
        member = tarfile.TarInfo(prefix + "/scripts/run_http_installation_check.py")
        member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
        patch = b"synthetic selected patch"
        member = tarfile.TarInfo(prefix + "/patches/rhwp/checkbox-preservation.patch")
        member.size = len(patch)
        archive.addfile(member, io.BytesIO(patch))
    source_asset["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    core_execution["evaluatorArtifactSha256"] = source_asset["sha256"]
    build_path = tmp_path / source_asset["buildReceipt"]["path"]
    build = json.loads(build_path.read_bytes())
    build["artifacts"][source_path.name] = source_asset["sha256"]
    build_ref = write(build_path.name, build)
    for asset in extra_assets:
        asset["buildReceipt"] = build_ref
    assets.extend(extra_assets)
    # An image must carry the rhwp bytes from this exact portable core, not only a wheel.
    linux = next(a for a in assets if a["id"] == "core-linux-x86_64")
    wheel_asset = next(a for a in extra_assets if a["id"] == "executed-wheel")
    binary = b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00" + b"not executable"
    native = {
        "version": "0.8.6+pat.checkbox.1",
        "baseCommit": "f1f9c6ae58344ee9368996d3543f76b9345cf227",
        "patchSha256": hashlib.sha256(patch).hexdigest(),
        "binarySha256": hashlib.sha256(binary).hexdigest(),
    }
    native_files = {
        "rhwp": binary,
        "LICENSE": b"synthetic upstream notice",
        "build.json": json.dumps(native).encode(),
    }
    portable = {
        **identity,
        "target": "linux-x86_64",
        "wheelSha256": wheel_asset["sha256"],
        "rhwp": native,
    }
    with zipfile.ZipFile(tmp_path / linux["path"], "w") as out:
        out.writestr("document-files/BUILD.json", json.dumps(portable))
        for name, raw in native_files.items():
            info = zipfile.ZipInfo("document-files/rhwp/" + name)
            info.external_attr = (0o100755 if name == "rhwp" else 0o100644) << 16
            out.writestr(info, raw)
    linux["sha256"] = hashlib.sha256((tmp_path / linux["path"]).read_bytes()).hexdigest()
    linux_build = json.loads((tmp_path / linux["buildReceipt"]["path"]).read_bytes())
    linux_build["artifacts"] = {
        linux["path"]: linux["sha256"],
        wheel_asset["path"]: wheel_asset["sha256"],
        source_asset["path"]: source_asset["sha256"],
    }
    linux["buildReceipt"] = write(linux["buildReceipt"]["path"], linux_build)
    native_identity = {
        "path": "/opt/document-files-native/rhwp/rhwp",
        "version": "rhwp v0.8.6+pat.checkbox.1",
        "upstreamCommit": native["baseCommit"],
        "patchSha256": native["patchSha256"],
        "files": {n: hashlib.sha256(raw).hexdigest() for n, raw in native_files.items()},
    }
    installed = write(
        "installed-core.json",
        {
            "core": identity_helper().wheel_package(tmp_path / wheel_asset["path"]),
            "rhwp": {k: native_identity[k] for k in ("path", "version", "files")},
        },
    )
    image_build = json.loads((tmp_path / "image-build.json").read_bytes())
    image_build.update(
        schemaVersion="document-files.image-build.v2",
        target="linux-x86_64",
        imageInspect={"imageId": X64_IMAGE, "os": "linux", "architecture": "amd64"},
        status="built-unqualified",
        stage="complete",
        installedCoreVerification="selected-wheel-exact",
        installedNativeVerification="selected-core-rhwp-exact",
        installedNativeEvidence=installed,
        rhwp=native_identity,
        probeCleanup={"status": "removed"},
        inputs={
            "coreArchiveSha256": linux["sha256"],
            "coreReceiptSha256": linux["buildReceipt"]["sha256"],
            "wheelSha256": wheel_asset["sha256"],
            "sourceArchiveSha256": source_asset["sha256"],
        },
    )
    next(a for a in assets if a["id"] == "image")["buildReceipt"] = write(
        "image-build.json", image_build
    )
    # Independent synthetic ARM bytes/receipts; never relabel the x64 executable.
    arm_core = next(a for a in assets if a["id"] == "core-linux-aarch64")
    arm_binary = binary[:18] + (183).to_bytes(2, "little") + binary[20:]
    arm_native = {**native, "binarySha256": hashlib.sha256(arm_binary).hexdigest()}
    arm_files = {**native_files, "rhwp": arm_binary, "build.json": json.dumps(arm_native).encode()}
    arm_portable = {**portable, "target": "linux-aarch64", "rhwp": arm_native}
    with zipfile.ZipFile(tmp_path / arm_core["path"], "w") as out:
        out.writestr("document-files/BUILD.json", json.dumps(arm_portable))
        for name, raw in arm_files.items():
            info = zipfile.ZipInfo("document-files/rhwp/" + name)
            info.external_attr = (0o100755 if name == "rhwp" else 0o100644) << 16
            out.writestr(info, raw)
    arm_core["sha256"] = hashlib.sha256((tmp_path / arm_core["path"]).read_bytes()).hexdigest()
    arm_build = {
        **linux_build,
        "target": "linux-aarch64",
        "artifacts": {
            arm_core["path"]: arm_core["sha256"],
            wheel_asset["path"]: wheel_asset["sha256"],
            source_asset["path"]: source_asset["sha256"],
        },
    }
    arm_core["buildReceipt"] = write(arm_core["buildReceipt"]["path"], arm_build)
    arm_identity = {
        **native_identity,
        "files": {n: hashlib.sha256(raw).hexdigest() for n, raw in arm_files.items()},
    }
    arm_installed = write(
        "installed-core-arm64.json",
        {
            "core": identity_helper().wheel_package(tmp_path / wheel_asset["path"]),
            "rhwp": {k: arm_identity[k] for k in ("path", "version", "files")},
        },
    )
    arm_image = tmp_path / "image-arm64.tar"
    synthetic_image(arm_image, ARM_CONFIG)
    arm_image_sha = hashlib.sha256(arm_image.read_bytes()).hexdigest()
    arm_image_build = {
        **image_build,
        "target": "linux-aarch64",
        "imageId": ARM_IMAGE,
        "imageDigest": "sha256:" + "8" * 64,
        "archiveSha256": arm_image_sha,
        "imageInspect": {"imageId": ARM_IMAGE, "os": "linux", "architecture": "arm64"},
        "installedNativeEvidence": arm_installed,
        "rhwp": arm_identity,
        "inputs": {
            **image_build["inputs"],
            "coreArchiveSha256": arm_core["sha256"],
            "coreReceiptSha256": arm_core["buildReceipt"]["sha256"],
        },
    }
    assets.append(
        {
            "id": "image-arm64",
            "kind": "image",
            "target": "linux-aarch64",
            "path": arm_image.name,
            "sha256": arm_image_sha,
            "imageId": arm_image_build["imageId"],
            "imageDigest": arm_image_build["imageDigest"],
            "buildReceipt": write("image-build-arm64.json", arm_image_build),
        }
    )
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
                "cpuQuota": 4,
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
    for name in gate.REQUIRED - {"local_model", "local_arm64_model"}:
        role, linux_target = gate.LINUX_CHECKS.get(name, (name, None))
        required = (
            gate.HTTP_TESTS
            if role == "http_service"
            else gate.CONTAINER_TESTS
            if role == "container_internal"
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
            else report.get("target", linux_target or "linux-x86_64")
        )
        core_target = report.get("target", linux_target or "linux-x86_64")
        report["artifacts"] = [
            f"core-{core_target}",
            f"runtime-{runtime_target}",
            f"recognition-{runtime_target}",
            "model",
            "image-arm64" if runtime_target == "linux-aarch64" else "image",
        ]
        if role == "container_internal":
            report.update(
                coreExecution=core_execution,
                executionRunId="container-" + name,
                execution=dict(reports["local_model"]["execution"]),
            )
            report["artifacts"].extend(["executed-wheel", "evaluator-source"])
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
    http_fixture(reports["http_service"], assets, core_execution, write, tmp_path)
    reports["http_service"]["execution"] = dict(reports["local_model"]["execution"])
    # Build each ARM report from its own result files and architecture-specific manifests.
    arm_model = copy.deepcopy(reports["local_model"])
    arm_model["artifactInventory"] = inventory_ref
    arm_model["executionRunId"] = "synthetic-arm64-run"
    arm_model["artifacts"] = [
        a.replace("linux-x86_64", "linux-aarch64") if a != "image" else "image-arm64"
        for a in arm_model["artifacts"]
    ]
    arm_model["model"]["runtimeManifestSha256"] = next(
        a["manifestSha256"] for a in assets if a["id"] == "runtime-linux-aarch64"
    )
    arm_model["observation"]["packManifestSha256"] = next(
        a["manifestSha256"] for a in assets if a["id"] == "recognition-linux-aarch64"
    )
    for case in arm_model["cases"]:
        raw = json.loads((tmp_path / case["resultPath"]).read_bytes())
        raw["provenance"]["model"] = arm_model["model"]
        ref = write("arm64-" + case["resultPath"], raw)
        case.update(resultPath=ref["path"], resultSha256=ref["sha256"])
    reports["local_arm64_model"] = arm_model
    arm_http = copy.deepcopy(reports["http_service"])
    arm_http["artifactInventory"] = inventory_ref
    arm_http["artifacts"] = [
        a.replace("linux-x86_64", "linux-aarch64") if a != "image" else "image-arm64"
        for a in arm_http["artifacts"]
    ]
    arm_http["coreExecution"]["selectedImageArtifactId"] = "image-arm64"
    arm_http["coreExecution"]["packManifestSha256"].update(
        runtimeId=arm_model["model"]["runtimeManifestSha256"],
        recognitionPackId=arm_model["observation"]["packManifestSha256"],
    )
    raw = json.loads((tmp_path / arm_http["aiResult"]["path"]).read_bytes())
    raw["provenance"]["model"] = arm_model["model"]
    arm_http["aiResult"] = write("arm64-http-result.json", raw)
    lifecycle = json.loads((tmp_path / arm_http["lifecycleEvidence"]["path"]).read_bytes())
    for key in ("resume_checkpoint", "actual_ai_complete"):
        lifecycle["observations"][key]["result"] = arm_http["aiResult"]
    arm_http["lifecycleEvidence"] = write("arm64-http-lifecycle.json", lifecycle)
    reports["http_service_arm64"] = arm_http
    document = {
        **identity,
        "schemaVersion": "document-files.qualification.v3",
        "artifactInventory": inventory_ref,
        "releaseAssets": [a["id"] for a in assets],
        "publishArtifactInventory": True,
        "scope": "printed-ko-en-cpu16gb-full-document.v1",
        "support": {"cloud_model": "not-qualified"},
    }

    # Snapshot input hashes in the stored source-linked results before any test mutations.
    for case in [*cases, *arm_model["cases"]]:
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
            role = gate.LINUX_CHECKS.get(name, (name, None))[0]
            image_asset = next(
                (a for a in assets if a["kind"] == "image" and a["id"] in report["artifacts"]), None
            )
            path = tmp_path / f"{name}.json"
            path.write_text(json.dumps(report))
            review_ref = None
            execution_ref = None
            container_ref = None
            if role.endswith("_model"):
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
            if role.endswith("_model") or role in {"http_service", "container_internal"}:
                measurements = write(
                    f"measurements-{name}.json",
                    {
                        "execution": report["execution"],
                        "limitsBefore": {"cpuQuota": 4},
                        "limitsAfter": {"cpuQuota": 4},
                    },
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
                        "schemaVersion": "document-files.container-identity.v2",
                        "collectorSha256": hashlib.sha256(
                            (ROOT / "scripts/capture_container_identity.py").read_bytes()
                        ).hexdigest(),
                        "artifacts": report["artifacts"],
                        "artifactInventory": inventory_ref,
                        "executionRunId": report["executionRunId"],
                        "executionReceipt": execution_ref,
                        "containerReceiptSha256": execution_ref["sha256"],
                        "imageArtifactId": image_asset["id"],
                        "imageArtifactSha256": image_asset["sha256"],
                        "imageId": image_asset["imageId"],
                        "containerId": "c" * 64,
                        "imageInspect": {
                            "imageId": image_asset["imageId"],
                            "repoDigests": ["registry/image@" + image_asset["imageDigest"]],
                            "os": "linux",
                            "architecture": gate.LINUX_ARCHITECTURES[image_asset["target"]],
                        },
                        "dockerInspect": {
                            "containerId": "c" * 64,
                            "imageId": image_asset["imageId"],
                            "running": False,
                            "status": "exited",
                            "startedAt": "2026-09-09T00:00:00Z",
                            "finishedAt": "2026-09-09T00:01:00Z",
                            "user": "10001:10001",
                            "memory": 16 * 1024**3,
                            "nanoCpus": 4_000_000_000,
                            "cpuQuota": 0,
                            "cpuPeriod": 0,
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
            if role == "http_service":
                helper = operational_helper()
                decisions = {
                    "schemaVersion": "document-files.operational-review-decisions.v1",
                    "sourceReportSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "reviewer": "synthetic independent fixture",
                    "independent": True,
                    "method": "independent-ground-truth",
                    "executionReceipt": execution_ref,
                    "containerIdentityReceipt": container_ref,
                    "criteria": {
                        key: {"passed": True, "findings": ["Synthetic observation checked"]}
                        for key in gate.HTTP_TESTS
                    },
                    "semanticReview": {
                        "criteria": {"exact_values": True},
                        "findings": ["Synthetic result only"],
                    },
                }
                decisions_ref = write(
                    ("http" if name == "http_service" else name) + "-review-decisions.json",
                    decisions,
                )
                assessed = helper.assess(
                    tmp_path,
                    report,
                    decisions,
                    {"path": path.name, "sha256": decisions["sourceReportSha256"]},
                    gate,
                )
                review_ref = write(
                    ("http" if name == "http_service" else name) + "-review.json",
                    {
                        "schemaVersion": "document-files.operational-review.v1",
                        "reviewDecisions": decisions_ref,
                        **assessed,
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


def test_http_review_seals_separately_and_gate_revalidates_it(evidence):
    reports, document, run, root = evidence
    run()
    raw = (root / "http_service.json").read_bytes()
    helper = operational_helper()
    receipt = helper.seal(
        root,
        root / "http_service.json",
        root / "http-review-decisions.json",
        root / "sealed-http-review.json",
    )
    assert receipt["passed"] is True
    assert (root / "http_service.json").read_bytes() == raw
    assert json.loads(raw)["passed"] is False
    assert json.loads(raw)["releaseQualification"] is False
    with pytest.raises(FileExistsError):
        helper.seal(
            root,
            root / "http_service.json",
            root / "http-review-decisions.json",
            root / "sealed-http-review.json",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "boolean_only",
        "host_profile",
        "claims_quality_suite",
        "no_spec",
        "no_lifecycle",
        "wrong_run",
        "cancel_survivor",
        "no_real_child",
        "restart_replayed",
        "missing_persistence",
        "delete_retained",
        "new_budget",
        "unknown_result",
        "partial_ai",
        "wrong_core",
        "wrong_source",
        "wrong_recorder",
        "wrong_image",
        "rss_only",
        "swap",
        "oom",
        "http_network",
    ],
)
def test_http_review_rejects_missing_or_conflicting_actual_evidence(evidence, mutation):
    reports, _, run, root = evidence
    report = reports["http_service"]
    lifecycle_path = root / report["lifecycleEvidence"]["path"]
    lifecycle = json.loads(lifecycle_path.read_bytes())
    observations = lifecycle["observations"]
    if mutation == "boolean_only":
        report["passed"] = True
    elif mutation == "host_profile":
        report["observationProfile"] = "host-published-port"
    elif mutation == "claims_quality_suite":
        report["notCovered"] = []
    elif mutation == "no_spec":
        report.pop("reviewSpecification")
    elif mutation == "no_lifecycle":
        report.pop("lifecycleEvidence")
    elif mutation == "wrong_run":
        lifecycle["executionRunId"] = "different-run"
    elif mutation == "cancel_survivor":
        observations["cancel_tree"]["childrenAliveAfter"] = observations["cancel_tree"][
            "childrenBefore"
        ]
    elif mutation == "no_real_child":
        observations["cancel_tree"]["childrenBefore"] = []
    elif mutation == "restart_replayed":
        observations["restart_interrupted"]["afterDelay"]["attempt"] += 1
    elif mutation == "missing_persistence":
        observations.pop("persistent_results")
    elif mutation == "delete_retained":
        observations["delete_results"]["resultDatabaseExists"] = True
    elif mutation == "new_budget":
        report["budget"]["maxModelCalls"] = 1
    elif mutation in {"unknown_result", "partial_ai"}:
        path = root / report["aiResult"]["path"]
        result = json.loads(path.read_bytes())
        if mutation == "unknown_result":
            result["source"]["sha256"] = "7" * 64
        else:
            result["extraction"]["status"] = "partial"
        path.write_text(json.dumps(result))
        report["aiResult"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        observations["resume_checkpoint"]["result"] = report["aiResult"]
        observations["actual_ai_complete"]["result"] = report["aiResult"]
    elif mutation in {"wrong_core", "wrong_source", "wrong_recorder"}:
        field = {
            "wrong_core": "packageTreeSha256",
            "wrong_source": "sourceArtifactSha256",
            "wrong_recorder": "recorderSha256",
        }[mutation]
        report["coreExecution"][field] = "7" * 64
    elif mutation == "wrong_image":
        report["coreExecution"]["selectedImageArtifactId"] = "model"
    elif mutation == "rss_only":
        report["execution"].pop("cgroupMemoryPeakBytes")
        report["execution"]["rssBytes"] = 100
    elif mutation in {"swap", "oom"}:
        report["execution"]["memorySwapPeakBytes" if mutation == "swap" else "oomEventsDelta"] = 1
    else:
        report["execution"]["networkMode"] = "bridge"
    lifecycle_path.write_text(json.dumps(lifecycle))
    if "lifecycleEvidence" in report:
        report["lifecycleEvidence"]["sha256"] = hashlib.sha256(
            lifecycle_path.read_bytes()
        ).hexdigest()
    with pytest.raises((ValueError, KeyError)):
        run()


@pytest.mark.parametrize(
    "mutation",
    [
        "approve_boolean",
        "changed_decisions",
        "changed_raw",
        "other_execution",
        "wrong_cpu",
        "other_expected",
    ],
)
def test_operational_receipt_does_not_override_evidence(evidence, mutation):
    _, document, run, root = evidence
    run()
    item = next(c for c in document["checks"] if c["id"] == "http_service")
    gate = gate_module()
    assets = gate.artifact_inventory(document, root, document["sourceCommit"], document["version"])
    report = json.loads((root / item["evidence"]["path"]).read_bytes())
    if mutation == "approve_boolean":
        receipt_path = root / item["review"]["path"]
        receipt_path.write_text(
            json.dumps({"schemaVersion": "document-files.operational-review.v1", "passed": True})
        )
        item["review"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    elif mutation == "changed_decisions":
        (root / "http-review-decisions.json").write_text("{}")
    elif mutation == "changed_raw":
        report["checksPassed"] = False
    elif mutation == "other_execution":
        item["executionReceipt"] = next(c for c in document["checks"] if c["id"] == "local_model")[
            "executionReceipt"
        ]
    elif mutation == "wrong_cpu":
        report["executionRunId"] = "unmeasured-run"
    else:
        (root / report["reviewSpecification"]["path"]).write_text("{}")
    with pytest.raises((ValueError, KeyError)):
        gate._reviewed_operational(report, item, root, root / item["evidence"]["path"], assets)


@pytest.mark.parametrize("field,value", [("maxModelCalls", 13), ("completionSeconds", 901)])
def test_http_review_rejects_expanded_short_document_budget(evidence, field, value):
    reports, _, run, _ = evidence
    reports["http_service"]["budget"][field] = value
    with pytest.raises(ValueError, match="review|Review"):
        run()


def test_http_review_does_not_approve_negative_content_comparison(evidence):
    _, _, run, root = evidence
    run()
    report_path = root / "http_service.json"
    report = json.loads(report_path.read_bytes())
    decisions = json.loads((root / "http-review-decisions.json").read_bytes())
    criterion = next(iter(decisions["semanticReview"]["criteria"]))
    decisions["semanticReview"]["criteria"][criterion] = False
    receipt = operational_helper().assess(
        root,
        report,
        decisions,
        {"path": report_path.name, "sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()},
        gate_module(),
    )
    assert receipt["passed"] is False
    assert "independent-content-review" in receipt["missingEvidence"]


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "missing-native",
        "wrong-core",
        "wrong-wheel",
        "wrong-source",
        "wrong-native",
        "failed-cleanup",
        "wrong-installed",
        "absolute-proof",
    ],
)
def test_image_requires_exact_portable_native_and_installed_proof(evidence, mutation):
    _, document, _, root = evidence
    inventory_path = root / document["artifactInventory"]["path"]
    inventory = json.loads(inventory_path.read_bytes())
    image = next(a for a in inventory["artifacts"] if a["kind"] == "image")
    path = root / image["buildReceipt"]["path"]
    build = json.loads(path.read_bytes())
    if mutation == "legacy":
        build["schemaVersion"] = "document-files.image-build.v1"
    elif mutation == "missing-native":
        del build["installedNativeVerification"]
    elif mutation in ("wrong-core", "wrong-wheel", "wrong-source"):
        key = {
            "wrong-core": "coreArchiveSha256",
            "wrong-wheel": "wheelSha256",
            "wrong-source": "sourceArchiveSha256",
        }[mutation]
        build["inputs"][key] = "0" * 64
    elif mutation == "wrong-native":
        build["rhwp"]["files"]["rhwp"] = "0" * 64
    elif mutation == "failed-cleanup":
        build["probeCleanup"]["status"] = "failed"
    elif mutation == "absolute-proof":
        build["installedNativeEvidence"]["path"] = str(root / "installed-core.json")
    else:
        proof_path = root / build["installedNativeEvidence"]["path"]
        proof = json.loads(proof_path.read_bytes())
        proof["rhwp"]["version"] = "rhwp v0.8.6"
        proof_path.write_text(json.dumps(proof))
        build["installedNativeEvidence"]["sha256"] = hashlib.sha256(
            proof_path.read_bytes()
        ).hexdigest()
    path.write_text(json.dumps(build))
    image["buildReceipt"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    inventory_path.write_text(json.dumps(inventory))
    document["artifactInventory"]["sha256"] = hashlib.sha256(
        inventory_path.read_bytes()
    ).hexdigest()
    with pytest.raises((ValueError, KeyError)):
        gate_module().artifact_inventory(document, root, "a" * 40, "1.8.0")


@pytest.mark.parametrize(
    "missing",
    [
        "packaged_linux-aarch64",
        "local_arm64_model",
        "http_service_arm64",
        "container_internal_arm64",
        "local_model",
    ],
)
def test_each_linux_architecture_keeps_its_required_checks(evidence, missing):
    reports, _, run, _ = evidence
    del reports[missing]
    with pytest.raises(ValueError, match="Missing successful qualification"):
        run()


@pytest.mark.parametrize("kind", ["core", "runtime", "recognition", "image"])
def test_arm_qualification_rejects_x64_and_mixed_bindings(evidence, kind):
    reports, document, _, root = evidence
    gate = gate_module()
    assets = gate.artifact_inventory(document, root, document["sourceCommit"], document["version"])
    report = reports["local_arm64_model"]
    report["artifacts"].append("image" if kind == "image" else kind + "-linux-x86_64")
    with pytest.raises(ValueError):
        gate._bindings(report, "local_arm64_model", assets)
    report["artifacts"] = reports["local_model"]["artifacts"]
    with pytest.raises(ValueError, match="cannot substitute"):
        gate._bindings(report, "local_arm64_model", assets)


@pytest.mark.parametrize(
    "name",
    ["local_arm64_model", "http_service_arm64", "container_internal", "container_internal_arm64"],
)
def test_each_linux_route_requires_actual_architecture_and_cpu_quota(evidence, name):
    _, document, run, root = evidence
    run()
    item = next(c for c in document["checks"] if c["id"] == name)
    ref = item["containerIdentityReceipt"]
    path = root / ref["path"]
    original = json.loads(path.read_bytes())
    gate = gate_module()
    assets = gate.artifact_inventory(document, root, document["sourceCommit"], document["version"])
    report = json.loads((root / item["evidence"]["path"]).read_bytes())
    for field, value in [
        ("architecture", "amd64" if "arm64" in name else "arm64"),
        ("os", "darwin"),
    ]:
        broken = copy.deepcopy(original)
        broken["imageInspect"][field] = value
        path.write_text(json.dumps(broken))
        ref["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="actual container/image/run"):
            gate._container_identity(report, item, root, assets)
    for quota in [0, 5_000_000_000]:
        broken = copy.deepcopy(original)
        broken["dockerInspect"]["nanoCpus"] = quota
        path.write_text(json.dumps(broken))
        ref["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match="CPU quota"):
            gate._container_identity(report, item, root, assets)


def test_old_qualification_schema_cannot_omit_new_platform_requirements(evidence):
    _, document, run, _ = evidence
    document["schemaVersion"] = "document-files.qualification.v2"
    with pytest.raises(ValueError, match="Qualification identity"):
        run()


@pytest.mark.parametrize("target", ["linux-x86_64", "linux-aarch64"])
def test_image_export_cannot_be_swapped_even_with_updated_archive_hash(evidence, target):
    _, document, _, root = evidence
    inventory_path = root / document["artifactInventory"]["path"]
    inventory = json.loads(inventory_path.read_bytes())
    asset = next(
        a for a in inventory["artifacts"] if a["kind"] == "image" and a["target"] == target
    )
    synthetic_image(root / asset["path"], ARM_CONFIG if target == "linux-x86_64" else X64_CONFIG)
    asset["sha256"] = hashlib.sha256((root / asset["path"]).read_bytes()).hexdigest()
    receipt_path = root / asset["buildReceipt"]["path"]
    receipt = json.loads(receipt_path.read_bytes())
    receipt["archiveSha256"] = asset["sha256"]
    receipt_path.write_text(json.dumps(receipt))
    asset["buildReceipt"]["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    inventory_path.write_text(json.dumps(inventory))
    document["artifactInventory"]["sha256"] = hashlib.sha256(
        inventory_path.read_bytes()
    ).hexdigest()
    with pytest.raises(ValueError, match="Export differs"):
        gate_module().artifact_inventory(
            document, root, document["sourceCommit"], document["version"]
        )
