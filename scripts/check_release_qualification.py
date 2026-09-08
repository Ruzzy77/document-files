#!/usr/bin/env python3
"""Fail closed unless independently collected evidence qualifies this source revision."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = frozenset(
    {
        "local_model",
        "cloud_model",
        "packaged_macos-aarch64",
        "packaged_macos-x86_64",
        "packaged_windows-x86_64",
        "packaged_linux-x86_64",
        "client_codex",
        "client_claude_code",
        "client_claude_desktop",
        "client_chatgpt",
    }
)


def check(manifest: Path, evidence_root: Path, source_commit: str, version: str) -> None:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        document.get("schemaVersion") != "document-files.qualification.v1"
        or document.get("version") != version
        or document.get("sourceCommit") != source_commit
    ):
        raise ValueError("Qualification identity does not match this release source")
    checks = document.get("checks", [])
    if len({c["id"] for c in checks}) != len(checks):
        raise ValueError("Duplicate qualification checks")
    by_id = {c["id"]: c for c in checks}
    for name in sorted(REQUIRED):
        item = by_id.get(name, {})
        if item.get("passed") is not True:
            raise ValueError(f"Missing successful qualification: {name}")
        evidence = item.get("evidence", {})
        relative = Path(evidence["path"])
        path = evidence_root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not path.resolve().is_relative_to(evidence_root.resolve())
            or any(parent.is_symlink() for parent in [path, *path.parents])
        ):
            raise ValueError(f"Unsafe evidence path: {name}")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != evidence.get("sha256"):
            raise ValueError(f"Evidence checksum mismatch: {name}")
        report = json.loads(raw)
        if report.get("sourceCommit") != source_commit or report.get("version") != version:
            raise ValueError(f"Stale evidence: {name}")
        if name in {"local_model", "cloud_model"}:
            model = report.get("model", {})
            if (
                report.get("schemaVersion") != "document-files.model-qualification.v1"
                or report.get("dirtySource") is not False
                or report.get("passed") is not True
                or not model.get("model")
                or not model.get("adapter")
                or model.get("adapter") in {"scripted-test", "fake", "mock"}
                or report.get("endpointKind") != name.removesuffix("_model")
            ):
                raise ValueError(f"Actual model evidence required: {name}")
            cases = report.get("cases", [])
            formats = {"txt", "md", "html", "docx", "hwpx", "xlsx"}
            if not formats.issubset({c.get("format") for c in cases if c.get("holdout") is True}):
                raise ValueError(f"Independent holdout coverage required: {name}")
            for case in cases:
                review = case.get("semanticReview", {})
                if (
                    case.get("extractionStatus") != "complete"
                    or case.get("structurallyValid") is not True
                    or review.get("status") != "passed"
                    or not review.get("reviewer")
                    or not review.get("findings")
                ):
                    raise ValueError(f"Incomplete independent semantic review: {name}")
                result_relative = Path(case["resultPath"])
                result_path = path.parent / result_relative
                if (
                    result_relative.is_absolute()
                    or ".." in result_relative.parts
                    or not result_path.resolve().is_relative_to(evidence_root.resolve())
                    or any(p.is_symlink() for p in [result_path, *result_path.parents])
                ):
                    raise ValueError(f"Unsafe model result path: {name}")
                if hashlib.sha256(result_path.read_bytes()).hexdigest() != case["resultSha256"]:
                    raise ValueError(f"Model result checksum mismatch: {name}")
                result = json.loads(result_path.read_bytes())
                observed_model = result.get("provenance", {}).get("model", {})
                if (
                    result.get("extraction", {}).get("status") != case["extractionStatus"]
                    or result.get("validation", {}).get("valid") is not True
                    or result.get("source", {}).get("sha256") != case.get("inputSha256")
                    or any(
                        observed_model.get(key) != model.get(key)
                        for key in ("adapter", "model", "configurationId")
                    )
                ):
                    raise ValueError(f"Report does not match actual model result: {name}")
        else:
            tests = report.get("tests", [])
            if not tests or any(test.get("passed") is not True for test in tests):
                raise ValueError(f"Failed or empty evidence: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    try:
        check(args.manifest, args.evidence_root, commit, version)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"Release NOT qualified: {exc}") from exc
    print(f"Release qualified: {version} ({commit})")


if __name__ == "__main__":
    main()
