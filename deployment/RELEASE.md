# Frozen candidate qualification and promotion

These tools prepare and check a release. They do not establish model quality merely
by running, and no candidate in this checkout is thereby qualified. Preserve the
frozen consumer baseline until the final release and actual consumer checks pass.

## Build one source identity

Review and commit the selected changes before stable-candidate construction. Build
from that clean commit on each target, using a **new or empty** output directory:

```sh
python scripts/build_release.py --output /new/candidate-output \
  --rhwp /verified/rhwp/bin/rhwp --rhwp-license /verified/rhwp/LICENSE
```

The builder creates the wheel and sdist itself; do not prepopulate its output with
an earlier same-version wheel. It detects source changes during assembly and emits
`document-files.build-inventory.v2` with source, version, target, candidate mode and
actual archive hashes. `--development` explicitly permits dirty local experiments;
those receipts cannot qualify or promote a stable release. A failed output directory
is retained for diagnosis, not reused for another build.

Core, recognition, runtime, model and image remain independently versioned. Assemble
new immutable packs for changed configuration; never edit an installed manifest to
make its digest or compatibility match. Retain upstream provenance, license notices,
component SBOMs and the model conversion receipt. Review current advisories for
native libraries, recognition dependencies, model and container OS as well as the
core Python dependency audit. Record unresolved exceptions against exact digests.

### Linux build and runtime compatibility

The Linux CI recipes currently build on Ubuntu 22.04 with explicitly selected GCC 12.
`linux_abi.py` reads actual ELF version requirements and rejects requirements above
GLIBC 2.36, GLIBCXX 3.4.30 or CXXABI 1.3.13. Compiler paths, versions, hashes and build-host
libc are retained; declaration metadata is not a replacement for the ELF check.

CPU and native builders also run relocated startup checks in an actual pulled
`python:3.12-bookworm` image identified by its RepoDigest. The probe is non-root,
read-only and network-isolated, with bounded execution and cleanup records. This is
a compatibility probe, not the final slim product image or a recognition/model run.
Its logs and ABI receipts do not qualify memory limits, a minimum kernel, installed
clients or the public HTTP port. Final qualification must still bind the actual
selected image, packs, execution and independent review below.

Native source acquisition keeps expected/observed SHA, byte counts and bounded HTTP
failure metadata. A hash mismatch fails the build; retries, changed pins or reuse of
an incomplete file are not an automatic repair. Rust toolchain identity is collected
from the pinned source directory with the same environment used by its build.

The native CI now has a separate Linux-only delivery step. It checks the successful
candidate, exact source archives/tessdata, notices, compiler/runtime hashes and actual
bookworm startup/cleanup before uploading source-SHA/run/attempt-named inputs. Failed
checks retain review evidence but do not upload that binary set. Windows stays review-only.
This artifact is input for a full recognition-stage audit, not a recognition pack or
release approval. At source 1da72ad the delivery step passed actual CI, and the
87,061,149-byte downloaded ZIP matched the API SHA. Its 78 inventory entries,
candidate files, source archives, notices and recorded ELF/bookworm startup identity
were checked against that exact source. This is not the final recognition pack,
complete host-isolation evidence or compiler-runtime redistribution approval.

The image builder now requires the matching Linux portable core ZIP and includes its
verified patched `rhwp`, build metadata and LICENSE at `/opt/document-files-native/rhwp`.
It checks the actual product resolver, executable target/version and installed hashes
inside the image. Image-build v2 and the release gate bind those bytes to the source,
wheel and core receipt. This path has regression coverage; a real image build and HWP
document processing with fresh state remain pending. An unrelated cache or runtime
download is not a substitute, and preserving notices is not full license approval.

### Check Linux recognition inputs before acquisition

`prepare_linux_recognition_inputs.py` checks a hash-approved preparation inventory.
It does not install Python/wheels, run native files or approve redistribution, stage
assembly or model quality. Use a new output directory and explicit resource limits:

```sh
python scripts/prepare_linux_recognition_inputs.py \
  --inventory /verified/inventory.json --inventory-sha256 INVENTORY_SHA256 \
  --target linux-aarch64 --output /new/input-check \
  --max-total-bytes 2147483648 --max-download-bytes 536870912 \
  --max-file-bytes 268435456 --min-free-bytes 12884901888 \
  --timeout-seconds 600 --file-timeout-seconds 300
```

These are explicit example ceilings, not permission to enlarge a failed run's budget.
The default performs no network transfer. Add `--acquire` to copy approved local
originals; add `--download` as well to allow missing inputs from the exact approved
official URLs. Target/Python/wheel identity, byte counts and hashes must match. Missing
metadata and unresolved inventory requirements return `not-ready`; they are not waived.
Local file hashes are checked only after the complete inventory passes preflight.
The current ARM inventory is not ready and no input acquisition has passed.

`acquisition.json` records the inventory, preparer hash, limits, verified bytes,
remaining inputs and failure/cleanup results. Redirects are bounded and validated
before following; transfers do not automatically retry. Failed or partial outputs
are retained and must not be reused as a new output directory. Worker cleanup covers
handled cancellation and errors, not a guarantee after SIGKILL or host loss. Actual
stage assembly, ELF dependencies, notices and installed execution remain separate.

## One evidence root and artifact inventory

Keep private reports and the exact candidate files beneath a dedicated evidence root.
All references use `{path, sha256}`. Paths in references are relative to that root,
except artifact `path` (relative to the inventory directory) and a model case's input,
result and review-specification paths (relative to its raw report directory).
Absolute paths, traversal and symlinks are rejected. Reports and inventories must
identify the same clean source commit and product version.

The aggregate inventory uses this shape:

```json
{
  "schemaVersion": "document-files.artifact-inventory.v2",
  "version": "1.8.0",
  "sourceCommit": "FULL_PRODUCT_COMMIT",
  "dirtySource": false,
  "artifacts": [
    {
      "id": "core-linux",
      "kind": "core",
      "target": "linux-x86_64",
      "path": "assets/document-files-1.8.0-linux-x86_64.zip",
      "sha256": "ARCHIVE_SHA256",
      "buildReceipt": {"path": "receipts/core-linux.json", "sha256": "RECEIPT_SHA256"}
    }
  ]
}
```

The illustration is incomplete and cannot pass the gate. Include every delivered
pipeline artifact. Kinds are `core`, `runtime`, `recognition`, `model`, `image` and
`metadata`. Pack entries also require `manifestSha256` and `target`; the actual
archive's manifest and clean product build identity are checked. Model target is
`any`. Core receipts must be stable `document-files.build-inventory.v2` receipts
whose target and filename/hash match. Image entries require the actual Docker
`imageId` and a `document-files.image-build.v2` build receipt containing the clean
product identity, `archiveSha256` and matching `imageId`. `imageId` is Docker's
content-addressed configuration ID, not a registry manifest digest. Optional
`imageDigest` must agree with the build receipt when an independently established
manifest digest exists; do not invent it for a locally built image with no registry
RepoDigest. The image build/export wrapper is implemented; no final image has yet
been built and qualified through this path. The receipt must also bind the same core
ZIP and its exact installed patched HWP backend; v1 receipts cannot qualify an image.
The gate opens the actual exported archive, checks its configuration SHA against the
Docker image ID, verifies Linux architecture and hashes the ordered layer contents.
A matching receipt and archive SHA alone cannot substitute for these content checks.

Every execution/installed report lists the **used** inventory IDs in `artifacts` and
repeats the exact aggregate `artifactInventory` reference. Bind core + runtime +
model + recognition for full extraction, and the image for container routes. A
platform report also names its actual installed core `artifactSha256`. Its runtime
and recognition targets must match its native or Linux-container route.

## Build and export the selected image

On the prepared Linux Docker host, use a clean matching source checkout, the exact
Linux core receipt/portable ZIP/wheel/sdist, a separately hash-locked wheelhouse and requirements
file, and a locally available base image pinned by both RepoDigest and actual image ID:

```sh
python scripts/build_release_image.py \
  --core-receipt /verified/core/build-inventory.json \
  --core-receipt-sha256 TRUSTED_CORE_RECEIPT_SHA256 \
  --target linux-x86_64 \
  --core-archive /verified/core/document-files-1.8.0-linux-x86_64.zip \
  --wheel /verified/core/document_files-1.8.0-py3-none-any.whl \
  --source /verified/core/document_files-1.8.0.tar.gz \
  --wheelhouse /verified/wheelhouse \
  --wheelhouse-inventory /verified/wheelhouse-inventory.json \
  --wheelhouse-inventory-sha256 TRUSTED_WHEELHOUSE_INVENTORY_SHA256 \
  --requirements /verified/requirements.lock \
  --requirements-sha256 TRUSTED_REQUIREMENTS_SHA256 \
  --base-image 'BASE_REPOSITORY@sha256:BASE_MANIFEST_SHA256' \
  --base-image-id 'sha256:BASE_CONFIG_SHA256' --output /new/image-output
```

The wheelhouse inventory is an exact filename-to-SHA256 map; the requirements file
uses exact versions and hashes. The wrapper copies only verified bytes into a new
context, checks source/helper/Dockerfile identity, disables build-step networking and
cache reuse, and verifies installed core and patched HWP files before export. The image receipt keeps
the actual image ID separate from the archive SHA. Failure outputs are retained, not
reused. The Dockerfile uses the installed frontend rather than fetching a syntax image.

`--network=none` constrains build steps; it is not proof that the Docker daemon or
host had no external access. Record host isolation separately. The base OS, exact
runtime libc/CPU compatibility, security/licenses and complete installed execution
still need review. A successful image build cannot qualify document extraction.

The export check also matches ordered uncompressed layer hashes to the image config,
with bounded tar/gzip inspection. All Docker commands share the supplied finite build
budget; probe cleanup has a separate short limit and checks the exact run label, ID
and image before deletion. Failure receipts distinguish CLI process cleanup from
unverified daemon-side build termination. Actual Docker execution remains unverified.

## Immutable raw results, independent review and resource evidence

The qualification document is `document-files.qualification.v3`, contains the clean
product identity, scope `printed-ko-en-cpu16gb-full-document.v1`, explicit
`support.cloud_model` (`qualified` or `not-qualified`), `artifactInventory`, `checks`
and `releaseAssets` (the inventory IDs intended for public delivery). Set
`publishArtifactInventory: true` explicitly: promotion also uploads the already
verified aggregate inventory JSON, without listing it inside itself or creating a
circular hash. Keep that public inventory limited to public relative paths and
provenance, never secrets or private document metadata.

Five native installation checks are mandatory: macOS ARM64/Intel, Windows x64,
Linux x64 and Linux ARM64. Linux execution checks are paired explicitly:

| Role | Linux x64 check | Linux ARM64 check |
|---|---|---|
| Full model quality and CPU 16 GiB | `local_model` | `local_arm64_model` |
| HTTP lifecycle and installed inference | `http_service` | `http_service_arm64` |
| Internal container execution | `container_internal` | `container_internal_arm64` |

Each pair uses the same validation logic and a different required target. Keep all
six results; do not count a Spark/ARM run as x64. Each run selects one matching image,
portable core, runtime and recognition pack; common wheel/source/model bytes may be
shared. Native Intel Mac recognition still routes to the x64 Linux container.

The image builder's `--target` accepts `linux-x86_64` (default) or `linux-aarch64`.
Select target-specific portable input and pinned base image before building; do not
rename an archive or relabel a receipt. The builder checks the actual base/final
Docker architecture, rhwp ELF machine and exported config. Image-build v2 records
`target` and `imageInspect` along with the existing exact input/native hashes.

Host `document-files.container-identity.v2` evidence includes Docker `Os` and
`Architecture`, actual CPU limits and the original container/image/run identity.
Older evidence without these fields must be collected again, not backfilled with
assumptions. Both architectures require at most four CPUs, 16 GiB and zero swap/OOM,
without GPU or external networking. Keep Spark's host GPU/swap settings unchanged;
limits belong to the qualification container. Internal-container checks also need
source-bound execution and actual host identity receipts, not only checklist flags.

Each check has `id`, `passed` and `evidence: {path, sha256}`. A model check additionally
has separate `review` and, for either local-model check, `executionReceipt` references. Keep the
raw `document-files.model-qualification.v2` report unchanged with `passed: false`.
The independent `document-files.semantic-review.v1` receipt binds its source-report
hash and every input/result/frozen-specification hash. The gate merges reviewed
states only in memory. The model never receives the review specification.

The CPU report records the measurement runner's `executionRunId`. The separate
`document-files.execution-receipt.v1` receipt binds that run ID and the raw report
in `outputs: [{path, sha256}]`; do not put the receipt hash back into the raw report,
which would create a circular hash dependency. The receipt's `measurements` reference
binds a raw measurement document with an identical `execution` object.

CPU acceptance requires verified cgroup memory ceiling at most 16 GiB and positive
`cgroupMemoryPeakBytes` within it; zero `memorySwapMaxBytes`, `memorySwapPeakBytes`,
`oomEventsDelta`, `oomKillEventsDelta` and `gpuDeviceCount`; CPU/no-GPU/offline/
network-blocked flags; `networkMode: "none"`; `exitCode: 0`; `timedOut: false`;
`measurementComplete: true`; `limitsUnchanged: true`; and `recorderErrors: []`.
`peakProcessTreeBytes` is an optional RSS diagnostic, **not** the cgroup peak. A
16 GiB container is not proof of headroom on a physical 16 GiB computer.

The full nine-format, Korean/English, scanned/text PDF, long-document and semantic
criteria remain required. Installed reports additionally reference an `aiResult`
whose actual result hash, complete/valid status and runtime/model provenance match
the selected assets. Codex, Claude Code and Claude Desktop require
`capabilities: {nativeDocuments: "qualified", aiExtraction: "qualified"}`.
ChatGPT may instead declare `aiExtraction: "not-supported"`, with actual native
processing and `unsupported_ai_is_explicit` checks, no `aiResult`, and no successful
`extraction_result` check. This does not qualify ChatGPT AI extraction or reduce CPU
model coverage.

## Actual container image identity is collected on its host

The `local_model` check also requires `containerIdentityReceipt: {path, sha256}`.
After the recorder finishes and before removing its dedicated container, run on the
Docker host (not inside the container):

```sh
python scripts/capture_container_identity.py \
  --evidence-root /evidence --artifact-inventory /evidence/artifact-inventory.json \
  --inventory-sha256 TRUSTED_INVENTORY_SHA256 --image-artifact product-image \
  --execution-receipt /evidence/cpu-run/receipt.json \
  --container-id FULL_64_HEX_CONTAINER_ID \
  --container-receipt-path /evidence/cpu-run/receipt.json \
  --output /evidence/container-identity.json
```

The host path and container path may differ; name the same original recorder receipt
at both locations. The tool reads only a fixed `docker inspect` projection and that
explicit receipt from the container. It records actual `.Image`, container ID,
separate image `RepoDigests`, limited isolation fields and mount destinations. It
never requests environment variables, full commands or mount-source paths. The
container must have actually started; names, supplied environment claims, changed
container state and a different receipt fail closed. The tool does not start,
modify or remove containers, and existing evidence is not overwritten.

The gate binds this host receipt to the original run ID, resource receipt SHA256,
selected image archive and actual image ID. It also rejects mounts that override
image executable directories. This is image/run identity evidence, not a model
quality pass, source-build attestation or proof of a hostile operator's honesty.
The plain preparation-image CPU preflight still does not qualify a final product
image or local model, and this tool must not relabel that preflight as such.

## Installed HTTP lifecycle preparation

`scripts/run_http_installation_check.py` runs with the selected image's installed
Python, not an editable source checkout. Supply the trusted inventory hash, selected
core/source/image/pack IDs, an administrator local-pack configuration, a public
multi-stage input, and explicit total call/time and outer-runner budgets. Its help
lists the exact arguments. It first checks installed core bytes, source-bound runner
bytes and selected pack identities; missing or mismatched assets stop the run.

The runner owns a new loopback service and state directory. It exercises authentication,
byte-only inputs, idempotency/conflicts, a one-call partial result and explicit grant,
actual inference-child cancellation, restart/interruption without automatic replay,
checkpoint resume, result preservation and deletion. Use a document that needs more
than one call; a one-call complete fixture cannot prove the budget/resume path.
The grant uses only the supplied total allowance, never an automatic increase.

Wrap this execution in `measure_cpu_execution.py`, then capture the actual image
identity on the container host. The raw `http-installation-run.v1` report always has
`passed: false` and `releaseQualification: false`; `checksPassed` describes only
the checks actually performed. Independent content review, image and isolation
receipts are still required. Preparing this runner does not qualify the service.

Supply `--review-specification` before starting the run to copy and hash-lock its
expected content independently of inference. The specification is not model input.
The runner stores actual response/status, process identity and retained/deleted-file
observations separately as `http-lifecycle-observations.v1`. This is an in-container
loopback profile; host-published-port access and the independent document-quality
suite are explicitly not covered.

After comparing those observations and the actual result with the frozen expected
content, save `document-files.operational-review-decisions.v1`: raw-report SHA,
reviewer/method/independence, execution and container receipt references, all twelve
lifecycle decisions with findings, and every specified content criterion. Then seal
the review without changing the raw report:

```sh
python scripts/review_operational.py --evidence-root /evidence \
  --report http/raw-report.json --decisions http/review-decisions.json \
  --output http/operational-review.json
```

Reference the sealed review from the `http_service` check's `review`. The release gate
rechecks observation predicates, exact artifacts, installed core/source/pack/image
identity, and cgroup evidence; a reviewer boolean or the old `passed: true` HTTP report
cannot substitute for these inputs. A separate check must exercise host-published
access when that route is offered. No actual final-image HTTP run has yet qualified.

## Large archives: transport parts only

GitHub requires each release asset to be under 2 GiB. The prepared Qwen archive
exceeds that limit. Split its **unchanged bytes** into local 1 GiB parts before
publication; this does not alter the pack manifest or PackStore contract.
[GitHub release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

```sh
python scripts/release_parts.py split /verified/qwen.pack.zip --output /new/qwen-parts
python scripts/release_parts.py join /prepared/qwen.pack.zip.parts.json \
  --output /new/qwen.pack.zip --sha256 TRUSTED_ORIGINAL_ARCHIVE_SHA256
```

The transport manifest `document-files.release-parts.v1` records the original
archive's name, size and SHA256 and ordered, archive-namespaced parts with individual
sizes and SHA256. Join is explicit and local-only: it never fetches missing parts,
requires an independently trusted original digest, and creates the output only after
whole-archive verification. Existing outputs and unsafe/tampered inputs are rejected.
Failed split stages remain for inspection; an interrupted stage is not a deliverable.

Keep the original archive as the inventory artifact and add:

```json
"transport": {
  "manifest": {"path": "qwen-parts/qwen.pack.zip.parts.json", "sha256": "TRANSPORT_MANIFEST_SHA256"}
}
```

Promotion checks both the original archive and streamed reconstruction, then uploads
its parts and transport manifest **instead of** the oversized original. Report
bindings still identify the original archive. Prepare all downloaded parts explicitly,
verify the authenticated inventory/digests, join, then use ordinary pack installation.
Nothing is downloaded or joined during document processing.

## Verify, then explicitly publish

```sh
python scripts/promote_release.py /evidence/qualification.json --evidence-root /evidence
```

Default is a local dry-run: no tag, release or network mutation. Only after final
quality, platform, client, security/license and consumption readiness review:

```sh
python scripts/promote_release.py /evidence/qualification.json --evidence-root /evidence \
  --publish --notes /reviewed/public-release-notes.md
```

The publisher is fixed to `Ruzzy77/document-files`. It refuses an existing release
or a tag pointing to another commit. It stages exact verified public assets, creates
a new draft, downloads and checks every uploaded byte, rechecks tag/draft state and
only then makes the release public. A failure leaves the draft for inspection; it
does not overwrite, delete or automatically retry. Private evidence is not uploaded
implicitly: only explicit `releaseAssets`, their verified transport files and the explicitly
selected public aggregate inventory are sent.
Validate external installation and actual consumer operation before changing the
Toolkit/Sync pinned release. Keep rollback and existing stored results available.

## Candidate CI is not delivery qualification

`cpu-runtime.yml` builds the five native runtime candidates from the pinned source.
It runs only for an explicit dispatch or changes to that builder/workflow, leaves
outputs under runner-temporary storage and does not activate packs. Windows uses
`prepare_windows_build.ps1` to identify the installed VS2022 Enterprise instance,
prepare explicitly pinned official Enterprise/Professional terms, and record actual
compiler/SDK/tool and available static-library hashes. The original DOCX, derived
text, source URL, collection time and SHA receipt remain distinct. Changed URLs,
hashes, product mismatches and preview terms fail rather than using generated text.
The host receipt exists before toolchain lookup, so preparation failures retain their
primary stage. Earlier Windows candidates accidentally
selected an extension's VS2015 preview license and must not be promoted. The VS2022
identity screen is only an obvious-mismatch guard: independently verify the installed
edition, applicable product redistribution terms, static CRT files and final notices.
A generic runtime-use license is not a substitute for those checks. Matching compiler/SDK, `dumpbin`, CMake,
CPU instructions, runtime source access and enough disk must actually exist on the
runner. An x64 build/`--version` smoke does not qualify inference or minimum-OS support.

Windows CPU and native recognition CI preserve review JSON, logs and notices but do
not upload unreviewed Windows binaries. The official terms and successful compilation
are not redistribution approval. Review the exact output hashes and conditions before
enabling their delivery.

To verify an **existing** candidate's packs, dispatch `packs.yml` with its tag, the
public aggregate inventory asset name and its independently trusted SHA256. The
workflow checks that inventory before fetching packs, reconstructs multipart ZIPs
locally and uses normal offline PackStore import without activation. Its custom
verification attestation covers only the uploaded original ZIPs or uploaded parts
and transport manifests; the receipt records reconstructed original/manifest hashes.
It does not claim upstream build provenance or model quality. Missing trust input,
stale source, unsafe/altered bytes or inadequate disk fails closed. Multipart model
verification needs space for retained parts, one reconstructed ZIP and one isolated
installation simultaneously; do not delete unrelated host tools to bypass preflight.
