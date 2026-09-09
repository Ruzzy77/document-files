# Offline runtime packs and CPU deployment

This directory supplies packaging/deployment interfaces, not prequalified model
weights or a tested Docker installation. No model is downloaded at document time.
The repository's HWP source-preservation backend remains in the core distribution.

## Pack contract

A ZIP contains `manifest.json` and regular inventoried files, without a wrapper
folder. `document-files.pack.v1` requires:

- `id`, `version`, `kind` (`core`, `recognition`, `llama-cpp-runtime`, `model`),
  `platform` (five native target names; `any` only for models).
- `minimumOS: {name, version}`: actual macOS version, Windows NT version, or Linux
  kernel version. Linux libc-dependent builds additionally set `minimumGlibc`.
  Declare actual CPU requirements and qualify them on the target; the importer
  does not infer instruction-set support from an x64 label.
- `files: [{path, size, sha256, license, executable}]`, covering every file.
- `licenses: [{id, spdx, path}]`; each file refers to a shipped notice. Preserve
  component-specific notices. Do not apply the engine license to model weights.
- `provenance.sources: [{uri, sha256, revision?}]`: immutable source archives or
  explicitly named inventory/receipt digest kinds. Git/HF revisions must be full
  commit IDs. Builder records its product commit and dirty-source state separately.
- `compatibleRuntimes: [{id, version, manifestSha256}]`: exact matches, not ranges.
- Optional CycloneDX `components`: audited dependency names, versions and licenses,
  supplementing the generated file-level SBOM.

llama.cpp runtimes declare executable `entrypoints.server` and
`entrypoints.quantize`, and include their runtime libraries and licenses. Model
packs declare `model: {name:'Qwen3.5-9B', quantization:'Q4_K_M', file:..., tokenizer:
'embedded-gguf', chatTemplate:'embedded-gguf', maxContextTokens:8192}`. The context
value is a selected working budget, not a promise that the CPU can process it fast.

Recognition packs declare:

```json
{
  "backend": "docling",
  "python": "python/bin/python3",
  "tesseract": "bin/tesseract",
  "artifacts": "models/docling",
  "tessdata": "models/tessdata",
  "languages": ["kor", "eng"],
  "layout": "heron",
  "tableMode": "accurate",
  "offline": true,
  "device": "cpu",
  "tableOcrRepair": "off"
}
```

Use the actual Windows paths in its manifest. Python and Tesseract must be listed
executables; model directories contain all config/weight files. Docling uses
`docling-project--docling-layout-heron/` and
`docling-project--docling-models/model_artifacts/tableformer/accurate/` beneath its
artifacts directory. Tesseract needs `kor.traineddata`, `eng.traineddata`, the
`osd.traineddata` orientation helper and the upstream `configs/tsv` file. OSD does
not add a third body-text language to the support profile.
The product launches the pack interpreter with `-I -B` and its private
`recognition_bootstrap.py`. Only the product's worker modules are loaded from the
core installation; the core's adjacent dependencies, current directory and user
site are not added to the pack interpreter's import path. Imports cannot add
bytecode to a checksummed pack. No copied alternate worker source is required.
Recognition releases must pin a **CPU-only** wheelhouse and
its runtime libraries. Do not reuse a generic Torch lock containing CUDA wheels.
Intel native recognition manifests are rejected; import the Linux pack for the
local Linux container instead, without masquerading it as a native Mac pack.

The optional `tableOcrRepair: "ruled_tables_v1"` uses the same fixed Tesseract on
bounded, layout-detected ruled-table crops; it does not run competing OCR engines.
The original image and OCR observations remain separate from the derived crop and
its tokens, with conflicts left unresolved. Its optional `repairBudget` keys are
`maxTables` (default 8, maximum 32), `maxCalls` (8/32), `maxPixels` (16000000/64000000),
and `maxSeconds` (60/300), all positive integers. This policy and budget are part
of the hashed recognition manifest and checkpoint identity. Prepare an explicitly
selected pack to change them; document requests cannot override model paths or
this preprocessing policy. A fixed or unreadable token is not an automatic retry.

`tableOcrRepair: "ruled_cells_v2"` is a separate explicit source implementation:
it uses original-ink cell crops, geometry-selected page segmentation and cell-level
resume, with the same budget keys and no competing OCR engine. It also supplies a
coordinate-ordered structure view while preserving original OCR and ruling-line
conflict evidence. It does not fabricate blank values for cells with no ink. The
previous v1 pack is immutable and does not contain or enable v2; prepare a new
compatible pack before claiming deployed support. Neither policy is qualified for
all scans. See [support status](../SUPPORT.md) for evidence boundaries.

For the remaining physical/CPU qualification, use the bounded
[Linux CPU procedure](CPU_QUALIFICATION.md). Its configuration has static regression
coverage only until an actual Linux container host and matching packs are available.

## Preparation and packing

`upstream-pins.json` records independently fetched official metadata for the
selected llama.cpp release and Qwen snapshot. Those facts do not qualify the assets
or establish minimum OS/CPU requirements. Download only during an explicitly
authorized preparation step. Verify hashes before unpacking. Never run an
untrusted archive's installer or follow links outside its staging directory.

Prepare an audited staging directory, license notices and a declaration containing
all metadata above; use `defaultLicense`, exact `fileLicenses` overrides and an
`executables` list rather than a manually generated `files` array. Then run:

```sh
python scripts/build_runtime_pack.py --stage /audited/stage \
  --declaration /audited/declaration.json --output /output/runtime.pack.zip \
  --source-artifact VERIFIED_SHA256=/audited/upstream-archive
```

For recognition stages, run `scripts/prepare_recognition_pack.py` first with the
separately reviewed `--audit-sha256`, exact stage/declaration, source archives and
wheel/native-linkage evidence. It verifies only by default; adding `--output`
invokes the existing pack builder after the audit. CPU-only wheels, target binary
headers, shipped per-file notices, exact source model/OCR bytes and recorded native
linkage are required. It is not an installer, native build or target-execution
certificate. Linux/Windows stages and actual relocation/model checks remain
necessary; the existing Mac assembly is not portable evidence for those targets.

Every declared source digest must match a supplied local artifact. The builder
inventories actual bytes and refuses to overwrite an output. It emits `.sha256`,
`.manifest.json` and `.cdx.json` alongside the ZIP. Archive checksums cannot replace
publisher authentication or the review of a source inventory.

For the approved model, acquire the **official safetensors snapshot** at the full
revision in `upstream-pins.json`. Prepare a source inventory `{source,revision,
files:[{path,sha256}]}` from official file/LFS metadata; small Git blobs also need
verified local SHA256 after acquisition from that immutable revision. Keep the
snapshot directory exactly equal to the inventory, including tokenizer/config and
license files, without cache symlinks. Do not use a third-party prequantized GGUF.
Prepare a clean llama.cpp checkout at the pinned commit and an isolated conversion
environment with its conversion dependencies locked separately. Conversion is a
build-time activity; those dependencies do not enter the inference pack.

```sh
python scripts/prepare_model_pack.py \
  --source /audited/Qwen3.5-9B --snapshot-inventory /audited/source-inventory.json \
  --model-revision c202236235762e1c871ad0ccb60c8ee5ba337b9a \
  --llama-source /audited/llama.cpp \
  --llama-revision 9dcf84e5ae2718947188b539aab8b9c2b15d3ba1 \
  --converter-python /audited/converter/bin/python \
  --converter-lock /audited/converter-requirements.lock \
  --quantize /audited/runtime/bin/llama-quantize \
  --runtime-manifest /audited/runtime/manifest.json \
  --work /new/conversion-work --output /output/qwen.pack.zip --version 1
```

The script checks every snapshot file, rejects model Python/pickle weights, checks
converter git identity and quantizer identity, converts F16 then Q4_K_M, checks
embedded tokenizer/chat-template metadata, records hashes and produces a pack.
The required conversion dependency lock is bundled with its SHA256; the receipt
also records the actual Python and installed package versions. The converter runs
with an allowlisted environment and cannot inherit an external GGUF override,
Python module path or model-provider credentials.
Use repeated `--compatible-runtime-manifest` arguments to approve the exact other
platform runtime manifests from the same llama.cpp revision without duplicating
the model weights. This records intended compatibility; qualify each target's
actual inference separately. After successful packing it deletes only its own F16 intermediate. Failed work is
left for diagnosis; remove it explicitly once no longer useful. The source weights
and conversion receipt remain. Large snapshot/pack duplication requires adequate
disk space. Conversion and real inference remain qualification steps, not assumed
successful because this preparation interface exists.

## Installation, activation and rollback

```python
from document_files.runtime_packs import PackStore, managed_llama_endpoint

store = PackStore("/private/state/packs")
pack = store.install("/offline/runtime.pack.zip", expected_sha256="trusted digest")
store.activate(pack.manifest["id"], pack.manifest["version"])
# Install/activate the compatible model separately, then:
with managed_llama_endpoint(store, "llama-cpp-cpu", "qwen3.5-9b-q4-k-m") as endpoint:
    # endpoint.base_url includes /v1; api_key is secret and excluded from repr.
    pass  # The owning engine creates its Chat Completions client here.
store.rollback("llama-cpp-cpu")
```

Use the exact 64-hex trusted digest, not the illustrative text above. Foreign
platform packs may be imported but cannot be activated/executed on that host.
`active.json` updates atomically per pack and retains the previous identity.
Activate mutually compatible runtime/model versions during maintenance; a mismatched
pair fails closed, never falls back. Updates do not modify stored extraction
results or automatically reanalyse source documents. Do not remove packs used by
running jobs. A crash-held `.write.lock` requires operator inspection rather than
automatic timeout stealing. Treat writable same-account storage as a trust boundary.

The local manager uses loopback, private token file, one slot and explicit CPU-only
flags. HTTP worker calls use `parent_managed=True` so POSIX children remain in the
worker group and are terminated with it on forced cancellation. Tokens/prompts are
not written to model logs; model stdout/stderr are discarded. Runtime packs are
trusted native executables, not sandboxed code.

## Linux CPU container / Intel Mac full recognition

Build from this directory only after preparing `wheelhouse/` and
`requirements.lock`: exact platform wheels for the core/server and dependencies,
with `--hash=sha256:...` for every requirement. Include the built product wheel.
The Docling/LLM dependencies live in their mounted packs, not this wheelhouse.
Supply a Python 3.12 Linux base image by immutable SHA256 digest; the Dockerfile
rejects tag-only input. Pin/review its OS packages and SBOM too. Build uses no pip
index, apt, model acquisition or curl. Use BuildKit provenance/SBOM export on the
authorized build host, and retain the base image and final image digest.

Create UID/GID 10001-owned private state, copy and adjust `server.example.json`,
and prepare a random owner-only bearer token file. Export a dedicated container
copy of the pack store; its files, config and token must be readable by container
UID 10001 while remaining inaccessible to other accounts. Do not change ownership
of an existing personal installation to work around a mount permission error.
Verify Docker Desktop bind-mount permission behavior on the target host. Set the
variables required by `compose.yaml`. `DOCUMENT_FILES_PACKS` is the installed store's `packs/` subdirectory;
`DOCUMENT_FILES_ACTIVE` is its exact verified `active.json`. They are mounted
read-only, while `/state/packs` itself can hold temporary local token files.

The image must already be loaded locally; `pull_policy: never` prohibits runtime
pulling. Compose uses Linux x64, a read-only root, no capabilities, bounded resources,
private tmpfs, loopback host publishing and an internal-only network. HTTP and its
worker/recognition/LLM subprocesses share one service; there is no second public
inference endpoint or externally routed cloud fallback. CPU recognition exits
before semantic inference starts so their large model allocations need not overlap.

Container configuration is **not yet an actual Docker/platform qualification**.
Verify host container support, read-only state routing, permissions, process-tree
cancellation, no egress, HWP processing and an actual authenticated document request
on the delivery platform. A container runtime's own licensing and installation are
separate from the Document Files OSS license. Keep remote access disabled unless
an administrator explicitly supplies TLS/authentication/network policy.
