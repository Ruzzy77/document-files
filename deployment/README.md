# Offline packs and deployment options

This directory supplies packaging/deployment interfaces, not prequalified model
weights or a tested Docker installation. No model is downloaded at document time.
The repository's HWP source-preservation backend remains in the core distribution.

The current priority is Spark extraction using ARM64 CPU recognition and a CUDA
inference pack, followed by personal Mac use. Start with [operations](../docs/operations.md)
and `compose.gpu.yaml`. Other-platform distribution and CPU-only 16 GiB qualification
are deferred. Five target names remain in the pack format for compatibility; they
are not a claim that all five are qualified. All CI in this repository is manual-only.

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
Linux packs may declare `nativeLibraryDirectories: ["python/lib"]` in `recognition`.
This optional ordered list contains at most eight unique pack-relative directories,
each represented by inventoried files. Absolute, escaping, symlinked and loader-token
paths are rejected. The selected, verified pack supplies these directories to the
actual worker's `LD_LIBRARY_PATH`; host loader paths and preload settings are never
inherited. An absent or empty list leaves the existing filtered environment unchanged.
Nonempty lists are rejected on other platforms. Declaring a search path is not proof
of dependency closure: qualification must inspect actual external library mappings,
including libraries opened dynamically. Only the documented system libraries may
come from outside the pack.
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
all metadata above; use exact `fileLicenses` (a `defaultLicense` only where applicable) and an
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

### Original inputs and locally built artifacts

Legacy pack provenance and recognition audit/verification v1 remain supported.
When a wheel or another preparation artifact is built locally, use
`document-files.pack-provenance.v2` and recognition audit/verification v2. The outer
`document-files.pack.v1` installation format and public extraction APIs are unchanged.
`provenance.sources` still describes original downloadable bytes with their real
HTTPS URL and SHA256; never put a built wheel's hash beside its parent source URL.

V2 adds `provenance.derivedArtifacts`. Each entry contains exactly:

- `sha256`: the local output artifact's verified SHA256.
- `inputs`: unique SHA256 values of original sources or earlier derived entries.
  Entries are topologically ordered; unknown/forward inputs, cycles and duplicate
  outputs are rejected.
- `recipe` and `buildEvidence`: each a `{path, sha256}` reference to a file shipped
  in the pack. Both must match the pack file inventory and its normal license rules.

Supply every original and derived artifact through `--source-artifact`; both are
hashed before packing. The recognition v2 audit covers their union in `sources`
and keeps wheel locks and installed distribution origins bound to exact outputs.
Recipe and build-record hashes bind reviewed evidence, not a claim that this
verifier executed the recipe or independently authenticated its contents. Review
the actual inputs, recipe, recorded build and resulting bytes before approving
the audit hash. Corresponding-source delivery and redistribution review remain
separate release requirements.

Required model/OCR resources must still be original source bytes, not derived
substitutes. ARM Torch must still match the original official CPU wheel, including
its original URL and staged bytes. V2 does not relax those checks or automatically
migrate an old audit into approval for a new declaration.

#### Authored packaging records

Use recognition audit/verification **v3** with provenance v2 when the stage also
contains project-authored recipes, build records or license collections. Do not
give those files a fictitious upstream archive member or `sourceSha256`. Their
audit file entries instead contain `authoredMetadata: {path, sha256}`, referring
to a separately reviewed JSON record relative to the audit directory:

```json
{
  "schemaVersion": "document-files.authored-metadata.v1",
  "files": [{
    "path": "provenance/derivations/component/record.json",
    "sha256": "<exact staged file SHA256>",
    "license": "<declared file license ID>",
    "author": "<declared author or preparing project>",
    "role": "build-record",
    "relatedArtifacts": ["<original or derived artifact SHA256>"],
    "basis": "<how this file was prepared and relates to those artifacts>"
  }]
}
```

Every record entry must match exactly one authored file using that evidence.
Hashes, licenses, nonempty authorship/basis and unique artifact references are
checked. `build-recipe`/`build-record` files must belong under
`provenance/derivations/`, identify a derived artifact and retain any direct
recipe/build-record relationship declared in provenance. `license-collection`
files belong under `licenses/` (or the existing `native/THIRD_PARTY_NOTICES.txt`).
Collections preserve the included components' separate terms; the metadata
designation is not a new license grant.

Only non-executable documentation is eligible. Authored text is UTF-8. A license
collection may additionally list `embeddedTexts: [{path, sha256}]` for original
terms shipped elsewhere in the same inventory. These preserve legacy encodings:
each exact original byte sequence must appear once between
`\n=== BEGIN <path>; SHA256 <sha256> ===\n` and `\n=== END <path> ===\n`.
References, original bytes and delimiters are verified; binary/control payloads
are rejected, and the remaining authored framing must be UTF-8. Do not silently
transcode upstream terms to make a collection pass.

Runtime entry points, model
directories, native-library directories and other runtime/package paths cannot
use this origin. Limits are 512 authored files, 2 MiB per file and 16 MiB total;
the evidence itself is separately limited to 512 entries and 16 MiB total.
Ordinary source, wheel, native and model checks still apply unchanged. The v3
receipt reports authored file/evidence counts separately and explicitly does not
authenticate authorship. Review the actual records before trusting the audit hash;
passing the check is not execution, reproducibility, license or quality approval.
V1/v2 audits reject the new field, and v3 requires authored records rather than
silently relabeling older evidence. The installed pack format remains v1.

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
