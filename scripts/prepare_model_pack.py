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


def vision_options(args: argparse.Namespace) -> dict | None:
    enabled = getattr(args, "include_vision_projector", False)
    minimum = getattr(args, "image_min_tokens", None)
    maximum = getattr(args, "image_max_tokens", None)
    if not enabled:
        if minimum is not None or maximum is not None:
            raise PackError("model_image_tokens_without_vision")
        return None
    minimum = 1024 if minimum is None else minimum
    maximum = 1536 if maximum is None else maximum
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or not 1024 <= minimum <= maximum <= 1536
    ):
        raise PackError("model_invalid_image_tokens")
    return {
        "file": "Qwen3.5-9B-mmproj-f16.gguf",
        "minImageTokens": minimum,
        "maxImageTokens": maximum,
    }


def vision_source(source: Path, files: dict) -> dict:
    if "preprocessor_config.json" not in files:
        raise PackError("model_missing_preprocessor_asset")
    try:
        preprocessing = json.loads((source / "preprocessor_config.json").read_text())
        config = json.loads((source / "config.json").read_text())
        vision = config["vision_config"]
        if (
            preprocessing["processor_class"] != "Qwen3VLProcessor"
            or preprocessing["image_processor_type"] != "Qwen2VLImageProcessorFast"
        ):
            raise ValueError
        import math

        for key in ("image_mean", "image_std"):
            values = preprocessing[key]
            if (
                not isinstance(values, list)
                or len(values) != 3
                or any(
                    type(v) not in (int, float)
                    or not math.isfinite(v)
                    or (key == "image_std" and v <= 0)
                    for v in values
                )
            ):
                raise ValueError
        size = preprocessing["size"]
        if (
            type(size["shortest_edge"]) is not int
            or type(size["longest_edge"]) is not int
            or not 0 < size["shortest_edge"] <= size["longest_edge"]
        ):
            raise ValueError
        for processor_key, config_key in (
            ("patch_size", "patch_size"),
            ("temporal_patch_size", "temporal_patch_size"),
            ("merge_size", "spatial_merge_size"),
        ):
            value = preprocessing[processor_key]
            if type(value) is not int or value <= 0 or value != vision[config_key]:
                raise ValueError
        if any(
            type(vision[key]) is not int or vision[key] <= 0
            for key in ("hidden_size", "out_hidden_size")
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError) as error:
        raise PackError("model_invalid_vision_source_configuration") from error
    return vision


def validate_projector(inspection: dict, config: dict) -> None:
    try:
        metadata = inspection["metadata"]
        if (
            metadata["general.architecture"] != "clip"
            or type(metadata["general.file_type"]) is not int
            or metadata["general.file_type"] != 1
            or metadata["clip.projector_type"] != "qwen3vl_merger"
            or metadata["clip.has_vision_encoder"] is not True
            or type(metadata["clip.vision.embedding_length"]) is not int
            or type(metadata["clip.vision.projection_dim"]) is not int
            or metadata["clip.vision.embedding_length"] != config["hidden_size"]
            or metadata["clip.vision.projection_dim"] != config["out_hidden_size"]
            or type(inspection["tensorCount"]) is not int
            or inspection["tensorCount"] <= 0
            or not isinstance(inspection["tensorTypes"], list)
            or not inspection["tensorTypes"]
            or not set(inspection["tensorTypes"]) <= {"0", "1"}
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError) as error:
        raise PackError("model_invalid_projector_gguf") from error


def prepare(args: argparse.Namespace) -> dict:
    vision = vision_options(args)
    if vision and (args.output.exists() or args.output.is_symlink()):
        raise PackError("pack_output_exists")
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
    vision_config = vision_source(args.source, source_files) if vision else None
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
    converter_sha = sha256_file(converter)
    converter_lock_sha = sha256_file(args.converter_lock)
    if vision:
        input_inventory_sha = sha256_file(args.snapshot_inventory)
        runtime_manifest_sha = sha256_file(args.runtime_manifest)
        converter_python_sha = sha256_file(args.converter_python)
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
    projector = None
    projector_inspection = None
    if vision:
        projector = stage / vision["file"]
        subprocess.run(
            [
                str(args.converter_python),
                str(converter),
                str(args.source),
                "--outfile",
                str(projector),
                "--outtype",
                "f16",
                "--mmproj",
            ],
            check=True,
            env=env,
            timeout=args.timeout,
        )
        if projector.is_symlink() or not projector.is_file():
            raise PackError("model_missing_projector_output")
        with projector.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise PackError("model_invalid_projector_gguf")
        projector_code = (
            "import json,sys; from gguf import GGUFReader,VisionProjectorType; "
            "r=GGUFReader(sys.argv[1]); "
            "keys=['general.architecture','general.file_type','clip.projector_type',"
            "'clip.has_vision_encoder','clip.vision.embedding_length',"
            "'clip.vision.projection_dim']; "
            "metadata={k:r.get_field(k).contents() for k in keys if r.get_field(k)}; "
            "assert metadata['clip.projector_type']==VisionProjectorType.QWEN3VL; "
            "print(json.dumps({'metadata':metadata,'tensorCount':len(r.tensors),"
            "'tensorTypes':sorted({str(t.tensor_type) for t in r.tensors})}))"
        )
        projector_inspection = json.loads(
            subprocess.check_output(
                [str(args.converter_python), "-c", projector_code, str(projector)],
                env=env,
                timeout=args.timeout,
                text=True,
            )
        )
        validate_projector(projector_inspection, vision_config)
        shutil.copyfile(
            args.source / "preprocessor_config.json", stage / "preprocessor_config.json"
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
    if vision:
        # Do not bind a projector to inputs modified while either conversion ran.
        if (
            sha256_file(converter) != converter_sha
            or sha256_file(args.converter_lock) != converter_lock_sha
            or sha256_file(args.snapshot_inventory) != input_inventory_sha
            or sha256_file(args.runtime_manifest) != runtime_manifest_sha
            or sha256_file(args.converter_python) != converter_python_sha
            or {
                p.relative_to(args.source).as_posix() for p in args.source.rglob("*") if p.is_file()
            }
            != set(source_files)
            or any((args.source / name).is_symlink() for name in source_files)
            or any((runtime_root / item["path"]).is_symlink() for item in runtime["files"])
            or git("rev-parse", "HEAD") != args.llama_revision
            or git("status", "--porcelain", "--untracked-files=normal")
            or any(
                sha256_file(args.source / name) != item["sha256"]
                for name, item in source_files.items()
            )
            or any(
                sha256_file(runtime_root / item["path"]) != item["sha256"]
                for item in runtime["files"]
            )
        ):
            raise PackError("model_conversion_inputs_changed")
        import hashlib

        environment_sha = hashlib.sha256(
            json.dumps(environment_record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        receipt["schemaVersion"] = "document-files.model-conversion.v2"
        receipt["vision"] = {
            **vision,
            "sha256": sha256_file(projector),
            "size": projector.stat().st_size,
            "outtype": "f16",
            "projectorType": "qwen3vl_merger",
            "ggufInspection": projector_inspection,
            "preprocessor": {
                "path": "preprocessor_config.json",
                "sha256": source_files["preprocessor_config.json"]["sha256"],
            },
            "modelConfigSha256": source_files["config.json"]["sha256"],
            "converterEnvironmentSha256": environment_sha,
            "converterPythonSha256": converter_python_sha,
            "runtimeManifestSha256": runtime_manifest_sha,
            "converterSha256": converter_sha,
            "converterRevision": args.llama_revision,
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
                    "digestKind": "conversion-receipt-v2" if vision else "conversion-receipt-v1",
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
    if vision:
        declaration["model"]["vision"] = vision
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
    parser.add_argument("--include-vision-projector", action="store_true")
    parser.add_argument("--image-min-tokens", type=int)
    parser.add_argument("--image-max-tokens", type=int)
    parser.add_argument("--timeout", type=int, default=7200)
    print(json.dumps(prepare(parser.parse_args())))


if __name__ == "__main__":
    main()
