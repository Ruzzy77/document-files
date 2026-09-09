"""Byte-level qualification identity; no Git, installation or model execution.

This proves the imported package files match the selected wheel, not that an
operator's Python process or host is a security sandbox. Container image identity
must be measured independently by the host, never copied from an environment value.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_digest(files: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def checked_path(root: Path, relative: str, digest: str) -> Path:
    path = Path(relative)
    target = root / path
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in relative
        or any(p.is_symlink() for p in (target, *target.parents))
        or not target.resolve().is_relative_to(root.resolve())
        or sha256(target) != digest
    ):
        raise ValueError("Execution artifact path or SHA256 mismatch")
    return target


def wheel_package(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Duplicate wheel member")
        for name in names:
            if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name:
                raise ValueError("Unsafe wheel member")
        records = [name for name in names if name.endswith(".dist-info/RECORD")]
        if len(records) != 1:
            raise ValueError("One wheel RECORD is required")
        entries = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        by_name = {entry[0]: entry for entry in entries}
        if len(by_name) != len(entries) or set(by_name) != set(names):
            raise ValueError("Wheel RECORD inventory mismatch")
        result = {}
        for name in names:
            row = by_name[name]
            if len(row) != 3:
                raise ValueError("Invalid wheel RECORD entry")
            if name == records[0]:
                if row[1:] != ["", ""]:
                    raise ValueError("Invalid self-referential wheel RECORD")
                continue
            raw = archive.read(name)
            encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
            if row[1] != "sha256=" + encoded or row[2] != str(len(raw)):
                raise ValueError("Wheel RECORD hash or size mismatch")
            if name.startswith("document_files/"):
                result[name.removeprefix("document_files/")] = hashlib.sha256(raw).hexdigest()
        if "__init__.py" not in result:
            raise ValueError("Wheel has no document_files package")
        return result


def sdist_evaluator(path: Path) -> dict[str, str]:
    result = {}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            pieces = Path(member.name).parts
            if len(pieces) < 3 or pieces[1] != "evaluation" or not member.name.endswith(".py"):
                continue
            if not member.isfile() or ".." in pieces or Path(member.name).is_absolute():
                raise ValueError("Unsafe evaluator source member")
            name = Path(*pieces[2:]).as_posix()
            if name in result:
                raise ValueError("Duplicate evaluator source member")
            result[name] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    if not {"run.py", "execution_identity.py"} <= result.keys():
        raise ValueError("Source artifact must contain evaluator and identity helper")
    return result


def recorder_digest(path: Path) -> str:
    with tarfile.open(path, "r:gz") as archive:
        members = [
            m
            for m in archive.getmembers()
            if Path(m.name).parts[1:] == ("scripts", "measure_cpu_execution.py") and m.isfile()
        ]
        if len(members) != 1:
            raise ValueError("Source artifact must contain one measurement recorder")
        return hashlib.sha256(archive.extractfile(members[0]).read()).hexdigest()


def installed_tree(root: Path, *, python_only=False) -> dict[str, str]:
    result = {}
    for path in root.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError("Imported package/evaluator symlinks are not allowed")
        if path.is_file() and (not python_only or path.suffix == ".py"):
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def verify_execution(
    inventory: dict,
    inventory_path: Path,
    evidence_root: Path,
    selected: list[str],
    core_id: str,
    evaluator_id: str,
    package_root: Path,
    evaluator_root: Path,
    model: dict,
    observation: dict | None,
) -> tuple[dict, str]:
    assets = {asset["id"]: asset for asset in inventory["artifacts"]}
    if (
        len(assets) != len(inventory["artifacts"])
        or core_id not in selected
        or evaluator_id not in selected
        or core_id == evaluator_id
    ):
        raise ValueError("Select distinct executed core wheel and evaluator source artifact IDs")
    chosen = [assets[identifier] for identifier in (core_id, evaluator_id)]
    paths = []
    for asset in chosen:
        if asset.get("kind") != "core":
            raise ValueError("Executed wheel/source must be core build artifacts")
        paths.append(checked_path(inventory_path.parent, asset["path"], asset["sha256"]))
        ref = asset["buildReceipt"]
        receipt = json.loads(checked_path(evidence_root, ref["path"], ref["sha256"]).read_bytes())
        if (
            receipt.get("schemaVersion") != "document-files.build-inventory.v2"
            or receipt.get("candidateMode") != "stable"
            or receipt.get("dirtySource") is not False
            or receipt.get("sourceCommit") != inventory.get("sourceCommit")
            or receipt.get("version") != inventory.get("version")
            or receipt.get("artifacts", {}).get(paths[-1].name) != asset["sha256"]
        ):
            raise ValueError("Executed artifact does not match a clean stable build receipt")
    if not paths[0].name.endswith(".whl") or not paths[1].name.endswith(".tar.gz"):
        raise ValueError("Executed core requires wheel and evaluator requires source distribution")
    package_files, evaluator_files = wheel_package(paths[0]), sdist_evaluator(paths[1])
    if installed_tree(package_root) != package_files:
        raise ValueError("Actually imported document_files tree differs from selected wheel")
    if installed_tree(evaluator_root, python_only=True) != evaluator_files:
        raise ValueError("Actually executed evaluator differs from selected source artifact")
    for name, module in tuple(sys.modules.items()):
        location = getattr(module, "__file__", None)
        if (
            name.startswith("document_files.")
            and location
            and not Path(location).resolve().is_relative_to(package_root.resolve())
        ):
            raise ValueError("Imported document_files modules have mixed origins")
    checks = [
        ("runtime", model.get("runtimeManifestSha256")),
        ("model", model.get("modelManifestSha256")),
    ]
    if model.get("adapter") == "managed-llama-cpp.v1":
        observation = observation or {}
        checks.append(
            (
                "recognition",
                observation.get("packManifestSha256", observation.get("manifestSha256")),
            )
        )
        for kind, digest in checks:
            if not digest or not any(
                assets[key].get("kind") == kind and assets[key].get("manifestSha256") == digest
                for key in selected
            ):
                raise ValueError(
                    "Configured model/recognition does not match selected actual packs"
                )
    return {
        "verification": "wheel-record-and-source-exact.v1",
        "coreArtifactId": core_id,
        "coreArtifactSha256": chosen[0]["sha256"],
        "packageTreeSha256": tree_digest(package_files),
        "packageFileCount": len(package_files),
        "evaluatorArtifactId": evaluator_id,
        "evaluatorArtifactSha256": chosen[1]["sha256"],
        "evaluatorFilesSha256": tree_digest(evaluator_files),
        "recorderSha256": recorder_digest(paths[1]),
    }, inventory["sourceCommit"]
