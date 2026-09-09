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
`imageId` and a `document-files.image-build.v1` build receipt containing the clean
product identity, `archiveSha256` and matching `imageId`. `imageId` is Docker's
content-addressed configuration ID, not a registry manifest digest. Optional
`imageDigest` must agree with the build receipt when an independently established
manifest digest exists; do not invent it for a locally built image with no registry
RepoDigest. The final image build/export wrapper and a qualified final image remain
unimplemented/unverified; these fields define evidence requirements, not completion.

Every execution/installed report lists the **used** inventory IDs in `artifacts` and
repeats the exact aggregate `artifactInventory` reference. Bind core + runtime +
model + recognition for full extraction, and the image for container routes. A
platform report also names its actual installed core `artifactSha256`. Its runtime
and recognition targets must match its native or Linux-container route.

## Immutable raw results, independent review and resource evidence

The qualification document is `document-files.qualification.v2`, contains the clean
product identity, scope `printed-ko-en-cpu16gb-full-document.v1`, explicit
`support.cloud_model` (`qualified` or `not-qualified`), `artifactInventory`, `checks`
and `releaseAssets` (the inventory IDs intended for public delivery). Set
`publishArtifactInventory: true` explicitly: promotion also uploads the already
verified aggregate inventory JSON, without listing it inside itself or creating a
circular hash. Keep that public inventory limited to public relative paths and
provenance, never secrets or private document metadata.

Each check has `id`, `passed` and `evidence: {path, sha256}`. A model check additionally
has separate `review` and, for `local_model`, `executionReceipt` references. Keep the
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

`cpu-runtime.yml` builds the four native runtime candidates from the pinned source.
It runs only for an explicit dispatch or changes to that builder/workflow, leaves
outputs under runner-temporary storage and does not activate packs. Windows uses
the installed Visual Studio developer shell and discovers the actual installation's
`License.rtf`; absent or ambiguous redistribution notices fail the build rather
than substituting generated license text. Matching compiler/SDK, `dumpbin`, CMake,
CPU instructions, runtime source access and enough disk must actually exist on the
runner. An x64 build/`--version` smoke does not qualify inference or minimum-OS support.

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
