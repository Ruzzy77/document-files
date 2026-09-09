"""Small synthetic converter contracts only; no upstream/model execution or qualification."""

import argparse
import hashlib
import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "model_pack_builder", ROOT / "scripts/prepare_model_pack.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    source = tmp_path / "source"
    source.mkdir()
    llama = tmp_path / "llama"
    llama.mkdir()
    (llama / "convert_hf_to_gguf.py").write_text("# synthetic converter")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for n in ["quantize", "server", "LICENSE"]:
        (runtime / n).write_text(n)
    rev = "a" * 40
    manifest = {
        "schemaVersion": "document-files.pack.v1",
        "id": "llama-cpu",
        "version": "test.1",
        "kind": "llama-cpp-runtime",
        "platform": "linux-aarch64",
        "minimumOS": {"name": "linux", "version": "1"},
        "files": [
            {
                "path": n,
                "sha256": digest(runtime / n),
                "size": (runtime / n).stat().st_size,
                "license": "runtime",
                "executable": n != "LICENSE",
            }
            for n in ["quantize", "server", "LICENSE"]
        ],
        "licenses": [{"id": "runtime", "spdx": "MIT", "path": "LICENSE"}],
        "provenance": {"sources": [{"uri": tool.LLAMA_URI, "revision": rev, "sha256": "b" * 64}]},
        "entrypoints": {"quantize": "quantize", "server": "server"},
        "compatibleRuntimes": [],
    }
    (runtime / "manifest.json").write_text(json.dumps(manifest))
    config = {
        "vision_config": {
            "hidden_size": 1152,
            "out_hidden_size": 4096,
            "patch_size": 16,
            "temporal_patch_size": 2,
            "spatial_merge_size": 2,
        }
    }
    preprocessor = {
        "processor_class": "Qwen3VLProcessor",
        "image_processor_type": "Qwen2VLImageProcessorFast",
        "patch_size": 16,
        "temporal_patch_size": 2,
        "merge_size": 2,
        "size": {"shortest_edge": 65536, "longest_edge": 16777216},
        "image_mean": [0.5] * 3,
        "image_std": [0.5] * 3,
    }
    for name, body in {
        "LICENSE": "synthetic",
        "config.json": json.dumps(config),
        "preprocessor_config.json": json.dumps(preprocessor),
        "tokenizer.json": "{}",
        "tokenizer_config.json": "{}",
        "model.safetensors": "synthetic",
    }.items():
        (source / name).write_text(body)
    inventory = tmp_path / "inventory.json"

    def refresh():
        inventory.write_text(
            json.dumps(
                {
                    "source": tool.MODEL_URI,
                    "revision": rev,
                    "files": [{"path": p.name, "sha256": digest(p)} for p in source.iterdir()],
                }
            )
        )

    refresh()
    lock = tmp_path / "lock"
    lock.write_text("synthetic lock")
    python = tmp_path / "python"
    python.write_text("synthetic interpreter")
    args = argparse.Namespace(
        source=source,
        snapshot_inventory=inventory,
        model_revision=rev,
        llama_source=llama,
        llama_revision=rev,
        converter_lock=lock,
        converter_python=python,
        runtime_manifest=runtime / "manifest.json",
        quantize=runtime / "quantize",
        work=tmp_path / "work",
        output=tmp_path / "pack.zip",
        timeout=1,
        version="test.1",
        context_tokens=8192,
        compatible_runtime_manifest=[],
    )
    state = {
        "runs": [],
        "projector": {
            "metadata": {
                "general.architecture": "clip",
                "general.file_type": 1,
                "clip.projector_type": "qwen3vl_merger",
                "clip.has_vision_encoder": True,
                "clip.vision.embedding_length": 1152,
                "clip.vision.projection_dim": 4096,
            },
            "tensorCount": 2,
            "tensorTypes": ["0", "1"],
        },
    }

    def run(command, **kwargs):
        state["runs"].append(command)
        assert (
            kwargs["env"]["HF_HUB_OFFLINE"] == "1" and kwargs["env"]["TRANSFORMERS_OFFLINE"] == "1"
        )
        if "--mmproj" in command and state.get("fail"):
            raise subprocess.CalledProcessError(1, command)
        output = (
            Path(command[command.index("--outfile") + 1])
            if "--outfile" in command
            else Path(command[2])
        )
        output.write_bytes(b"GGUF-synthetic")
        if "--mmproj" in command and state.get("mutate"):
            state["mutate"]()

    def check_output(command, **kwargs):
        if command[0] == "git":
            if "rev-parse" in command:
                return rev
            if "status" in command:
                return ""
            return tool.LLAMA_URI
        code = command[2]
        if "VisionProjectorType" in code:
            assert "VisionProjectorType.QWEN3VL.value" not in code
            return json.dumps(state["projector"])
        if "GGUFReader" in code:
            return json.dumps(
                {
                    "metadataKeys": [
                        "general.architecture",
                        "tokenizer.ggml.model",
                        "tokenizer.chat_template",
                    ],
                    "tensorCount": 1,
                }
            )
        return json.dumps({"python": "synthetic", "packages": [["gguf", "synthetic"]]})

    monkeypatch.setattr(tool.subprocess, "run", run)
    monkeypatch.setattr(tool.subprocess, "check_output", check_output)
    return tool, args, state, refresh


def unpack(args):
    with zipfile.ZipFile(args.output) as z:
        return (
            json.loads(z.read("manifest.json")),
            json.loads(z.read("conversion.json")),
            set(z.namelist()),
        )


def test_default_retains_text_only_v1(prepared):
    tool, args, state, _ = prepared
    tool.prepare(args)
    manifest, receipt, files = unpack(args)
    assert "vision" not in manifest["model"] and "vision" not in receipt
    assert receipt["schemaVersion"] == "document-files.model-conversion.v1"
    assert len(state["runs"]) == 2 and not any("mmproj" in n for n in files)
    assert not (args.work / "model-f16.gguf").exists()


def test_explicit_vision_has_exact_files_receipt_and_compatible_runtime(prepared):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    args.image_min_tokens = 1200
    args.image_max_tokens = 1400
    tool.prepare(args)
    manifest, receipt, files = unpack(args)
    vision = manifest["model"]["vision"]
    assert vision == {
        "file": "Qwen3.5-9B-mmproj-f16.gguf",
        "minImageTokens": 1200,
        "maxImageTokens": 1400,
    }
    assert vision["file"] in files and "preprocessor_config.json" in files
    assert receipt["schemaVersion"] == "document-files.model-conversion.v2"
    item = next(x for x in manifest["files"] if x["path"] == vision["file"])
    assert (
        item["sha256"]
        == receipt["vision"]["sha256"]
        == digest(args.work / "stage" / vision["file"])
    )
    assert item["size"] == receipt["vision"]["size"]
    assert receipt["vision"]["preprocessor"]["sha256"] == digest(
        args.source / "preprocessor_config.json"
    )
    env = json.dumps(
        receipt["converterEnvironment"], sort_keys=True, separators=(",", ":")
    ).encode()
    assert receipt["vision"]["converterEnvironmentSha256"] == hashlib.sha256(env).hexdigest()
    assert receipt["vision"]["converterPythonSha256"] == digest(args.converter_python)
    assert receipt["vision"]["runtimeManifestSha256"] == digest(args.runtime_manifest)
    assert manifest["provenance"]["sources"][1]["digestKind"] == "conversion-receipt-v2"
    assert len(state["runs"]) == 3 and state["runs"][-1][-1] == "--mmproj"
    assert state["runs"][-1][-2] == "f16"


@pytest.mark.parametrize(
    "minimum,maximum", [(1023, 1536), (1024, 1537), (1400, 1200), (True, 1536)]
)
def test_invalid_image_budget_fails_before_conversion(prepared, minimum, maximum):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    args.image_min_tokens, args.image_max_tokens = minimum, maximum
    with pytest.raises(tool.PackError, match="model_invalid_image_tokens"):
        tool.prepare(args)
    assert not state["runs"] and not args.work.exists()


def test_image_tokens_require_explicit_opt_in(prepared):
    tool, args, state, _ = prepared
    args.image_min_tokens = 1024
    with pytest.raises(tool.PackError, match="without_vision"):
        tool.prepare(args)
    assert not state["runs"]


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_invalid_preprocessor_rejected_before_conversion(prepared, missing):
    tool, args, state, refresh = prepared
    args.include_vision_projector = True
    path = args.source / "preprocessor_config.json"
    if missing:
        path.unlink()
    else:
        path.write_text('{"processor_class":"wrong"}')
    refresh()
    with pytest.raises(tool.PackError, match="preprocessor|vision_source"):
        tool.prepare(args)
    assert not state["runs"] and not args.work.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("general.architecture", "qwen"),
        ("general.file_type", 2),
        ("clip.projector_type", "qwen3vl"),
        ("clip.has_vision_encoder", False),
        ("clip.vision.embedding_length", 12),
        ("clip.vision.projection_dim", 10),
    ],
)
def test_incompatible_projector_metadata_rejected(prepared, key, value):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    state["projector"]["metadata"][key] = value
    with pytest.raises(tool.PackError, match="invalid_projector"):
        tool.prepare(args)
    assert not args.output.exists()


def test_projector_conversion_failure_keeps_work_without_archive(prepared):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    state["fail"] = True
    with pytest.raises(subprocess.CalledProcessError):
        tool.prepare(args)
    assert not args.output.exists() and (args.work / "model-f16.gguf").exists()


@pytest.mark.parametrize("target", ["source", "runtime", "converter", "inventory", "python"])
def test_conversion_input_mutation_rejected(prepared, target):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    path = {
        "source": args.source / "preprocessor_config.json",
        "runtime": args.quantize,
        "converter": args.llama_source / "convert_hf_to_gguf.py",
        "inventory": args.snapshot_inventory,
        "python": args.converter_python,
    }[target]
    state["mutate"] = lambda: path.write_text("changed")
    with pytest.raises(tool.PackError, match="inputs_changed"):
        tool.prepare(args)
    assert not args.output.exists()


def test_existing_output_rejected_before_conversion(prepared):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    args.output.write_bytes(b"keep")
    with pytest.raises(tool.PackError, match="pack_output_exists"):
        tool.prepare(args)
    assert args.output.read_bytes() == b"keep" and not state["runs"]


def test_default_does_not_require_preprocessor(prepared):
    tool, args, _, refresh = prepared
    (args.source / "preprocessor_config.json").unlink()
    refresh()
    tool.prepare(args)
    assert unpack(args)[1]["schemaVersion"] == "document-files.model-conversion.v1"


@pytest.mark.parametrize(
    "field,value",
    [("tensorTypes", ["2"]), ("tensorTypes", "1"), ("tensorTypes", []), ("tensorCount", 0)],
)
def test_projector_tensor_metadata_rejected(prepared, field, value):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    state["projector"][field] = value
    with pytest.raises(tool.PackError, match="invalid_projector"):
        tool.prepare(args)
    assert not args.output.exists()


def test_snapshot_hash_mismatch_rejected_before_conversion(prepared):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    (args.source / "preprocessor_config.json").write_text("changed")
    with pytest.raises(tool.PackError, match="snapshot_hash_mismatch"):
        tool.prepare(args)
    assert not state["runs"]


def test_projector_invalid_magic_rejected(prepared):
    tool, args, state, _ = prepared
    args.include_vision_projector = True
    state["mutate"] = lambda: (args.work / "stage/Qwen3.5-9B-mmproj-f16.gguf").write_bytes(
        b"no GGUF"
    )
    with pytest.raises(tool.PackError, match="invalid_projector"):
        tool.prepare(args)
    assert not args.output.exists()


@pytest.mark.parametrize(
    "key,value",
    [
        ("image_std", [0, 0.5, 0.5]),
        ("image_mean", [float("nan"), 0.5, 0.5]),
        ("patch_size", 14),
        ("size", {"shortest_edge": 10, "longest_edge": 5}),
    ],
)
def test_preprocessor_parameters_rejected_before_conversion(prepared, key, value):
    tool, args, state, refresh = prepared
    args.include_vision_projector = True
    path = args.source / "preprocessor_config.json"
    payload = json.loads(path.read_text())
    payload[key] = value
    path.write_text(json.dumps(payload))
    refresh()
    with pytest.raises(tool.PackError, match="vision_source_configuration"):
        tool.prepare(args)
    assert not state["runs"]
