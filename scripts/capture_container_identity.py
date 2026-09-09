#!/usr/bin/env python3
"""Read one explicit host Docker container's identity and immutable run receipt.

No container is started, changed or removed. Only an allowlisted inspect projection
and the explicitly named recorder receipt are read; environment and command fields
are never requested. Docker .Image (image ID) is distinct from registry RepoDigests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

import check_release_qualification as gate

CONTAINER_FORMAT = (
    '{"containerId":{{json .Id}},"imageId":{{json .Image}},'
    '"running":{{json .State.Running}},"status":{{json .State.Status}},'
    '"startedAt":{{json .State.StartedAt}},"finishedAt":{{json .State.FinishedAt}},'
    '"user":{{json .Config.User}},"memory":{{.HostConfig.Memory}},'
    '"nanoCpus":{{.HostConfig.NanoCpus}},"cpuQuota":{{.HostConfig.CpuQuota}},'
    '"cpuPeriod":{{.HostConfig.CpuPeriod}},'
    '"memorySwap":{{.HostConfig.MemorySwap}},"networkMode":{{json .HostConfig.NetworkMode}},'
    '"readOnlyRoot":{{json .HostConfig.ReadonlyRootfs}},'
    '"privileged":{{json .HostConfig.Privileged}},'
    '"deviceCount":{{len .HostConfig.Devices}},'
    '"deviceRequestCount":{{len .HostConfig.DeviceRequests}},'
    '"mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}'
    '{"destination":{{json $m.Destination}},"readOnly":{{not $m.RW}}}{{end}}]}'
)
IMAGE_FORMAT = (
    '{"imageId":{{json .Id}},"repoDigests":{{json .RepoDigests}},'
    '"os":{{json .Os}},"architecture":{{json .Architecture}}}'
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def reference(root: Path, path: Path) -> dict:
    relative = path.absolute().relative_to(root.absolute()).as_posix()
    digest = sha256(path)
    gate._file(root, relative, digest)
    return {"path": relative, "sha256": digest}


def inspect(kind: str, identifier: str, template: str) -> dict:
    raw = subprocess.check_output(
        ["docker", kind, "inspect", "--format", template, identifier], timeout=60, text=True
    )
    return json.loads(raw)


def container_receipt(container: str, path: str) -> bytes:
    relative = PurePosixPath(path)
    if not relative.is_absolute() or ".." in relative.parts or "\n" in path or "\r" in path:
        raise ValueError("An explicit safe absolute container receipt path is required")
    with tempfile.TemporaryDirectory(prefix="document-files-container-receipt-") as temporary:
        copied = Path(temporary) / "receipt.json"
        subprocess.run(
            ["docker", "cp", container + ":" + path, str(copied)],
            timeout=60,
            check=True,
            capture_output=True,
        )
        if copied.is_symlink() or not copied.is_file() or copied.stat().st_size > 1024**2:
            raise ValueError("Container recorder receipt must be a bounded regular JSON file")
        return copied.read_bytes()


def capture(
    root: Path,
    inventory_path: Path,
    inventory_sha: str,
    image_id: str,
    execution_path: Path,
    container_id: str,
    container_receipt_path: str,
    output: Path,
) -> dict:
    if not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise ValueError(
            "Explicit full Docker container ID required, not a name or environment claim"
        )
    output.absolute().relative_to(root.absolute())
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Container identity output cannot use symlinks")
    if output.exists() or output.is_symlink():
        raise ValueError("Container identity output is never overwritten")
    inventory_ref = reference(root, inventory_path)
    if inventory_ref["sha256"] != inventory_sha:
        raise ValueError("Trusted artifact inventory SHA256 mismatch")
    inventory = json.loads(inventory_path.read_bytes())
    if inventory.get("schemaVersion") != "document-files.artifact-inventory.v2":
        raise ValueError("Artifact inventory v2 required")
    execution_ref = reference(root, execution_path)
    execution = json.loads(execution_path.read_bytes())
    gate._identity(execution, inventory["sourceCommit"], inventory["version"])
    assets = [a for a in inventory["artifacts"] if a["id"] == image_id and a["kind"] == "image"]
    if (
        inventory.get("dirtySource") is not False
        or len(assets) != 1
        or image_id not in execution.get("artifacts", [])
        or not execution.get("executionRunId")
        or execution.get("schemaVersion") != "document-files.execution-receipt.v1"
    ):
        raise ValueError("Image selection and original execution receipt identity mismatch")
    asset = assets[0]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", asset.get("imageId", "")):
        raise ValueError("Inventory must distinguish actual imageId from registry imageDigest")
    before = inspect("container", container_id, CONTAINER_FORMAT)
    image = inspect("image", before["imageId"], IMAGE_FORMAT)
    copied = container_receipt(container_id, container_receipt_path)
    after = inspect("container", container_id, CONTAINER_FORMAT)
    if before != after:
        raise ValueError(
            "Container state changed while capturing identity; no success receipt written"
        )
    if (
        before["containerId"] != container_id
        or before["imageId"] != asset["imageId"]
        or image["imageId"] != asset["imageId"]
        or asset.get("target") not in gate.LINUX_ARCHITECTURES
        or image.get("os") != "linux"
        or image.get("architecture") != gate.LINUX_ARCHITECTURES.get(asset.get("target"))
        or before["status"] not in {"running", "exited"}
        or not before["startedAt"]
        or before["startedAt"].startswith("0001-")
    ):
        raise ValueError("Actual Docker container/image differs from selected candidate")
    if hashlib.sha256(copied).hexdigest() != execution_ref["sha256"]:
        raise ValueError("This container does not contain the original run receipt bytes")
    result = {
        "schemaVersion": "document-files.container-identity.v2",
        "collectorSha256": sha256(Path(__file__)),
        **{key: execution[key] for key in ("version", "sourceCommit", "dirtySource", "artifacts")},
        "executionRunId": execution["executionRunId"],
        "executionReceipt": execution_ref,
        "artifactInventory": inventory_ref,
        "imageArtifactId": image_id,
        "imageArtifactSha256": asset["sha256"],
        "imageId": image["imageId"],
        "containerId": container_id,
        "containerReceiptSha256": hashlib.sha256(copied).hexdigest(),
        "dockerInspect": before,
        "imageInspect": image,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--artifact-inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--image-artifact", required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--container-id", required=True)
    parser.add_argument("--container-receipt-path", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = capture(
        args.evidence_root,
        args.artifact_inventory,
        args.inventory_sha256,
        args.image_artifact,
        args.execution_receipt,
        args.container_id,
        args.container_receipt_path,
        args.output,
    )
    print(
        json.dumps(
            {
                "containerId": result["containerId"],
                "imageId": result["imageId"],
                "executionRunId": result["executionRunId"],
                "qualification": "not-assessed",
            }
        )
    )


if __name__ == "__main__":
    main()
