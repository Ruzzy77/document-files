#!/usr/bin/env python3
"""Offline conversion of an official Qwen3.5-9B snapshot using pinned llama.cpp.

Network acquisition is a separate authorized preparation step. Supply an audited
snapshot inventory containing official HF blob/LFS SHA256 values, not hashes from
an untrusted third-party GGUF. No remote-code trust, pip install or HF download.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_runtime_pack import build_pack  # noqa: E402

from document_files.portability import subprocess_environment  # noqa: E402
from document_files.runtime_packs import (  # noqa: E402
    PackError,
    safe_relative,
    sha256_file,
    validate_manifest,
)

MODEL_URI = "https://huggingface.co/Qwen/Qwen3.5-9B"
LLAMA_URI = "https://github.com/ggml-org/llama.cpp"


def prepare(args: argparse.Namespace) -> dict:
    if not args.converter_lock.is_file():
        raise PackError("model_missing_converter_lock")
    inventory = json.loads(args.snapshot_inventory.read_text())
    if inventory.get("source") != MODEL_URI or inventory.get("revision") != args.model_revision:
        raise PackError("model_unapproved_snapshot")
    import re

    if not re.fullmatch(r"[a-f0-9]{40}", args.model_revision):
        raise PackError("model_unpinned_revision")
    if not inventory.get("files"):
        raise PackError("model_missing_source_inventory")
    source_files = {safe_relative(item["path"]): item for item in inventory["files"]}
    actual = {p.relative_to(args.source).as_posix() for p in args.source.rglob("*") if p.is_file()}
    if actual != set(source_files):
        raise PackError("model_snapshot_inventory_mismatch")
    for name, item in source_files.items():
        path = args.source / name
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):
            raise PackError("model_snapshot_symlink")
        if sha256_file(path) != item["sha256"]:
            raise PackError("model_snapshot_hash_mismatch")
    if any(
        name.endswith((".py", ".pkl", ".pickle", ".bin", ".pt", ".pth")) for name in source_files
    ):
        raise PackError("model_executable_or_pickle_source")
    for required in ("LICENSE", "config.json", "tokenizer.json", "tokenizer_config.json"):
        if required not in source_files:
            raise PackError("model_missing_source_asset")
    if not any(name.endswith(".safetensors") for name in source_files):
        raise PackError("model_missing_safetensors")
    git = lambda *command: subprocess.check_output(  # noqa: E731
        ["git", "-C", str(args.llama_source), *command], text=True
    ).strip()
    if (
        not re.fullmatch(r"[a-f0-9]{40}", args.llama_revision)
        or git("rev-parse", "HEAD") != args.llama_revision
    ):
        raise PackError("model_unpinned_converter")
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise PackError("model_dirty_converter")
    if git("remote", "get-url", "origin").removesuffix(".git") != LLAMA_URI:
        raise PackError("model_unapproved_converter")
    runtime = validate_manifest(json.loads(args.runtime_manifest.read_text()))
    if runtime["kind"] != "llama-cpp-runtime":
        raise PackError("model_wrong_runtime")
    if not any(
        source.get("uri", "").startswith(LLAMA_URI)
        and source.get("revision") == args.llama_revision
        for source in runtime["provenance"]["sources"]
    ):
        raise PackError("model_converter_runtime_revision_mismatch")
    runtime_root = args.runtime_manifest.parent
    for item in runtime["files"]:
        path = runtime_root / item["path"]
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise PackError("model_runtime_symlink")
        if not path.is_file() or sha256_file(path) != item["sha256"]:
            raise PackError("model_runtime_file_hash_mismatch")
    quantize_name = runtime["entrypoints"]["quantize"]
    if args.quantize.resolve() != (runtime_root / quantize_name).resolve():
        raise PackError("model_quantizer_outside_runtime")
    quantize_item = next(item for item in runtime["files"] if item["path"] == quantize_name)
    if sha256_file(args.quantize) != quantize_item["sha256"]:
        raise PackError("model_unverified_quantizer")
    # Do not overwrite or merge a previous conversion, intermediate or release.
    args.work.mkdir(parents=True, exist_ok=False)
    stage = args.work / "stage"
    stage.mkdir()
    env = subprocess_environment()
    env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    intermediate = args.work / "model-f16.gguf"
    converted = stage / "Qwen3.5-9B-Q4_K_M.gguf"
    converter = args.llama_source / "convert_hf_to_gguf.py"
    subprocess.run(
        [
            str(args.converter_python),
            str(converter),
            str(args.source),
            "--outfile",
            str(intermediate),
            "--outtype",
            "f16",
        ],
        check=True,
        env=env,
        timeout=args.timeout,
    )
    subprocess.run(
        [str(args.quantize), str(intermediate), str(converted), "Q4_K_M"],
        check=True,
        env=env,
        timeout=args.timeout,
    )
    # Inspect the GGUF using the pinned converter's own parser, never import model code.
    inspection = (
        "import json,sys; from gguf import GGUFReader; r=GGUFReader(sys.argv[1]); "
        "required=['general.architecture','tokenizer.ggml.model','tokenizer.chat_template']; "
        "assert all(k in r.fields for k in required), 'missing GGUF tokenizer/template'; "
        "print(json.dumps({'metadataKeys': sorted(r.fields), 'tensorCount':len(r.tensors)}))"
    )
    env["PYTHONPATH"] = str(args.llama_source / "gguf-py")
    inspection_result = subprocess.check_output(
        [str(args.converter_python), "-c", inspection, str(converted)],
        env=env,
        timeout=args.timeout,
        text=True,
    )
    shutil.copyfile(args.source / "LICENSE", stage / "LICENSE.model")
    shutil.copyfile(args.snapshot_inventory, stage / "source-inventory.json")
    shutil.copyfile(args.converter_lock, stage / "converter-requirements.lock")
    environment_record = json.loads(
        subprocess.check_output(
            [
                str(args.converter_python),
                "-c",
                "import json,sys,importlib.metadata as m; "
                "print(json.dumps({'python':sys.version, 'packages':sorted("
                "[(d.metadata['Name'],d.version) for d in m.distributions()])}))",
            ],
            env=env,
            text=True,
            timeout=30,
        )
    )
    receipt = {
        "schemaVersion": "document-files.model-conversion.v1",
        "source": MODEL_URI,
        "sourceRevision": args.model_revision,
        "snapshotInventorySha256": sha256_file(args.snapshot_inventory),
        "converterSource": LLAMA_URI,
        "converterRevision": args.llama_revision,
        "converterSha256": sha256_file(converter),
        "converterDependencyLockSha256": sha256_file(args.converter_lock),
        "converterEnvironment": environment_record,
        "quantizerSha256": sha256_file(args.quantize),
        "quantization": "Q4_K_M",
        "intermediateSha256": sha256_file(intermediate),
        "modelSha256": sha256_file(converted),
        "ggufInspection": json.loads(inspection_result),
    }
    receipt_path = stage / "conversion.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    inventory_sha = sha256_file(args.snapshot_inventory)
    receipt_sha = sha256_file(receipt_path)
    compatible_runtimes = [
        {
            "id": runtime["id"],
            "version": runtime["version"],
            "manifestSha256": sha256_file(args.runtime_manifest),
        }
    ]
    for path in args.compatible_runtime_manifest:
        other = validate_manifest(json.loads(path.read_text()))
        if other["kind"] != "llama-cpp-runtime" or not any(
            source.get("uri", "").startswith(LLAMA_URI)
            and source.get("revision") == args.llama_revision
            for source in other["provenance"]["sources"]
        ):
            raise PackError("model_incompatible_additional_runtime")
        entry = {
            "id": other["id"],
            "version": other["version"],
            "manifestSha256": sha256_file(path),
        }
        if entry not in compatible_runtimes:
            compatible_runtimes.append(entry)
    declaration = {
        "schemaVersion": "document-files.pack.v1",
        "id": "qwen3.5-9b-q4-k-m",
        "version": args.version,
        "kind": "model",
        "platform": "any",
        "minimumOS": {"name": "runtime-dependent", "version": "0"},
        "licenses": [{"id": "model", "spdx": "Apache-2.0", "path": "LICENSE.model"}],
        "defaultLicense": "model",
        "provenance": {
            "sources": [
                {
                    "uri": MODEL_URI,
                    "revision": args.model_revision,
                    "sha256": inventory_sha,
                    "digestKind": "file-inventory-v1",
                },
                {
                    "uri": LLAMA_URI,
                    "revision": args.llama_revision,
                    "sha256": receipt_sha,
                    "digestKind": "conversion-receipt-v1",
                },
            ]
        },
        "compatibleRuntimes": compatible_runtimes,
        "model": {
            "name": "Qwen3.5-9B",
            "file": converted.name,
            "quantization": "Q4_K_M",
            "tokenizer": "embedded-gguf",
            "chatTemplate": "embedded-gguf",
            "maxContextTokens": args.context_tokens,
            "thinking": False,
        },
    }
    result = build_pack(
        stage,
        declaration,
        args.output,
        {inventory_sha: args.snapshot_inventory, receipt_sha: receipt_path},
    )
    # The large F16 intermediate has completed its role; preserve receipt and source.
    intermediate.unlink()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in (
        "source",
        "snapshot-inventory",
        "llama-source",
        "converter-python",
        "converter-lock",
        "quantize",
        "runtime-manifest",
        "work",
        "output",
    ):
        parser.add_argument("--" + option, type=Path, required=True)
    for option in ("model-revision", "llama-revision", "version"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--compatible-runtime-manifest", type=Path, action="append", default=[])
    parser.add_argument("--context-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=7200)
    print(json.dumps(prepare(parser.parse_args())))


if __name__ == "__main__":
    main()
