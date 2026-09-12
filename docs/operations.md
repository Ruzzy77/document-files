# Operation on Spark and personal hosts

The primary environment is DGX Spark with CPU recognition and CUDA interpretation.
Mac use follows after extraction correctness. These instructions describe the runtime;
[SUPPORT.md](../SUPPORT.md) separates actual development checks from pending service
and quality qualification. A prepared Compose file is not an installed HTTP service.

## 1. Select and verify the environment

Before a run, identify the product commit, actual source-file hashes, pack root,
active manifests and explicit model/profile options. Use one known source checkout
or a clean copied source inventory, not a mixture of an old wheel and edited modules.
Other applications' installed copies are not updated by preparing this checkout.

On shared Spark hosts, inspect existing workloads, host available memory, GPU memory
and any active Document Files process before starting another. Begin with one model
run per available host, not an assumed four-model capacity. Previous concurrent
runs encountered OOM; total physical capacity is not free capacity. Record host
pressure/OOM and cgroup measurements alongside GPU allocation. Do not disable swap,
stop unrelated jobs, change authentication or weaken container controls for a test.

Use the already configured SSH/Tailscale route and existing authorized container
access. Keep private addresses, host paths and tokens outside public source. Each
run has an explicit source/input identity, call/time budget, new output location and
cleanup target. Only that run's processes/containers may be stopped or removed.

## 2. Packs and model configuration

Runtime, model and recognition packs are independently versioned. Use normal
PackStore verification, installation and activation; never edit an installed
manifest. PDF recognition uses an explicitly selected ARM64 CPU pack with its own
Python/native libraries. Core dependencies do not silently acquire GPU wheels.

A Linux llama.cpp runtime with `accelerator: cuda`, explicit `cudaArchitectures` and
optional `minimumDriverVersion` uses CUDA0 for model layers, projector and KV cache.
The current builder is `scripts/build_cpu_runtime.py --accelerator cuda`; the name
is retained for compatibility. Its CUDA build uses the selected GCC 12 / CUDA 13
container and static cudart/cuBLAS, while the host driver interface is injected by
CDI. The model's `compatibleRuntimes` must match the exact runtime manifest. CPU
packs retain CPU placement; accelerator identity prevents mixed-checkpoint resume.

A model pack may explicitly declare an inventoried `model.vision` projector and
integer image-token bounds `1024 <= minImageTokens <= maxImageTokens <= 1536`.
Prepare a separate immutable pack with `prepare_model_pack.py
--include-vision-projector`; an existing conversion may be redeclared for an exact
runtime with `--from-pack`. Neither option authorizes replacing an active pack.

The internal image transport accepts at most two inline opaque single-frame 8-bit
RGB/L PNG or JPEG images, totaling 16 MiB and 16 million pixels. External URLs,
paths, animation, transparency and EXIF/XMP are rejected without resampling. Context
checks and generation use the same frozen multimodal request. Text-only token
estimates do not replace multimodal capability checks. [PDF reading details](extraction-engine.md#2-pdf-recognition-and-optional-visual-reading)

Local profiles may explicitly set `threads` and `threadsBatch`; otherwise the
pinned runtime defaults remain. Respect container CPU quota. Managed sampling is
greedy; seed, sampling, accelerator and thread settings are part of identity, but
greedy is not a bit-for-bit reproducibility guarantee across execution orders.

Optional profile `reasoningBudgetTokens` accepts integers 0–3071, not null/bool or
unlimited values. Omitting it retains non-thinking defaults outside phase-specific
policies. The per-block reasoning allowance must be smaller than the total output
ceiling, otherwise `ai_reasoning_budget_conflict` is returned. It does not guarantee
a reserved final-answer length. Current managed applicability uses 2048/3072 tokens;
table relations use 1024/2048. Actual request policy, not an old run-plan label, is
recorded in checkpoints. Incomplete generation remains incomplete, not empty success.

Cloud profiles are optional explicit endpoint/model configurations with `apiKeyEnv`,
never inline keys. They may specify `responseFormat`, `strictSchema`, `maxOutputTokens`
and `sampling` (`temperature`, `top_p`, `top_k`, `seed`); unset sampling uses provider
defaults. There is no automatic cloud fallback or cloud quality claim.

## 3. Run through the product, not a substitute interpreter

For local source use, prepare the pinned environment and call the normal launcher:

```sh
uv sync --frozen --python 3.12
launchers/document-files capabilities
launchers/document-files diagnose
launchers/document-files extract-schema input.pdf --options /private/run-options.json \
  --request-id example --storage-dir /private/results
```

These commands do not prepare missing model assets. Configure verified packs/profile
first using [deployment](../deployment/README.md) and [API options](python-api.md).
An evaluation wrapper must call the same public engine; prior expected answers or
provided model replies cannot substitute for an actual full-path accuracy check.

Set a finite call/time allowance before dispatch. Evaluation defaults are 12 calls /
900 seconds for short documents and 64 / 3600 for long ones. The supplied
`deployment/long-document-options.json` grants the long allowance, not a larger model
context or a completion guarantee. A development comparison may use a different
explicit allowance; failure does not automatically increase it. Record recognition
cost and actual total processing separately from model tokens and generation time.

## 4. Optional HTTP service and container

Copy `deployment/server.example.json` outside the repository and set private absolute
state/pack paths and administrator-managed profiles. Upload options cannot choose
arbitrary endpoints or executables. Generate a random server token of at least 32
ASCII characters; supply `DOCUMENT_FILES_SERVER_TOKEN` or an owner-readable secret
file, not source control or a run log.

`deployment/compose.gpu.yaml` is the Spark template: locally loaded digest-pinned
ARM64 image, CDI GPU access, read-only root/packs, non-root process, dropped
capabilities, bounded PIDs, no new privileges and loopback port 8765. It defaults to
8 CPUs and 32 GiB memory with equal memory-plus-swap limits. **These cgroup settings
do not by themselves establish a complete GPU/unified-memory cap.** Verify actual
host and GPU use. The internal offline network is not evidence that every possible
external route is blocked; inspect the deployed isolation and test it explicitly.

Review rendered configuration before starting. Do not pull/build images as part of
document processing. LAN publication requires authentication and the deployment's
TLS reverse-proxy arrangement. Spark model tests do not qualify service installation,
restart, access control or client operation; check those when deploying this service.

`POST /v1/jobs` accepts a bounded `application/octet-stream` body with
`X-Document-Format`, `X-Model-Profile`, optional JSON `X-Extraction-Options` and an
optional `Idempotency-Key`. The same key and identical bytes/options/profile pins
return the same job; differences conflict. Paths and input URLs are rejected.
See [managed-job commands](python-api.md#managed-jobs) for status, results and control.

## 5. Progress, cancellation and resume

Inspect `extraction.status`, validation, coverage and issues, not only process exit.
A finished attempt can contain partial work. Job `executionStatus` is separate from
`extractionStatus`; `resultRevision` identifies the committed paged result. Restart
pagination if that revision changes. Unread cells/pages, conflicts, missing scope or
oversized regions remain explicit rather than becoming absent values.

Recognition pages, PDF review and semantic/table stages have separate checkpoints.
A record structure can survive as `structure_compiled` while content or scope is
pending. Explicit resume reuses compatible committed work and preserves cumulative
usage. It does not grant a fresh stage allowance, replay an interrupted model request
or reset a failed independent evaluation. Call/time additions are explicit.

Resume rechecks input, source, active packs, execution policy, protocol/revision and
source trace. Do not edit version fields to bypass a conflict. Old public results
can be read without reinterpreting them. Request preflight overflow preserves work
without a model call; context checks, timeout, quota, invalid output and unavailable
runtime remain different errors. The detailed stage and retry rules are in
[the extraction engine](extraction-engine.md).

Cancellation preserves committed results and targets the managed worker and its
children. Confirm actual process/container termination after a bounded run; a client
timeout alone is not a teardown receipt. Do not terminate another shared workload.

## Retention, backup and deletion

Manual retention is the default. Set `defaultTtlSeconds` only when the installation
requires automatic expiry; it applies to newly submitted jobs, not earlier results.
Cancellation preserves committed observations/results. Deleting a managed job removes
its uploaded snapshot, results and checkpoints, but not the original caller file.
The synchronous `delete_extraction` removes only its retained database/checkpoint.

For a consistent filesystem backup, stop the foreground service, confirm no worker
remains, and back up the private state directory and active pack manifest. Protect
backups like original documents. Restore on a compatible engine/pack combination;
read old results without automatically reinterpreting them. Do not restore another
user's secrets from source-control history or qualification artifacts.

## Update and rollback

Prepare new packs in new version directories, verify their independently acquired
SHA256, inspect license/source inventories, then explicitly activate them. Preserve
previous versions and activation state for rollback. Never update an in-use model
behind a running job. The service pins pack manifests when submitting and checks
again before execution/resume; changed active configuration is a conflict, not a
silent model upgrade. Changing parser package versions alone does not request
reanalysis of unchanged documents in a calling application.

Runtime packages, model weights, product source, installed-client evidence and final
quality qualification are tracked separately. Keep logs to job ID, stage, timings,
usage and fixed error codes. Default logs must not contain source text, keys, prompts
or model responses. Review `SECURITY.md` for document/native-code trust boundaries.

## Optional qualification and publication

Formal multi-platform release and consumer migration are deferred. The retained
checks are not the completion criteria for current Spark extraction work. Do not
substitute GPU development results for CPU-only or independent-release evidence.

Use [the release procedure](../deployment/RELEASE.md) to freeze a clean candidate,
bind its actual artifacts to independent reviews and installed execution evidence,
and publish those same bytes. Development results and historical candidate bundles
cannot be substituted for the new candidate. ChatGPT capability limitations are
recorded per feature; explicit AI unavailability is never an AI quality pass.
Qualification v4 also requires a redistribution review bound to the exact candidate
inventory. Required source, build instructions and notices must be selected for public
delivery; promotion refuses their omission even when they are metadata artifacts.
Private review evidence is not uploaded automatically. A validated receipt is not an
automated legal decision or a replacement for the component review.

## Preparing a Linux recognition stage

`scripts/assemble_linux_recognition_stage.py` accepts an explicit hashed input
inventory and a new output directory. `--check-only` checks source archives and
wheels without executing their code. Actual assembly must run on the matching
Linux architecture under separately enforced network, memory and process limits.
It uses only the selected offline wheels, records copied/derived file origins and
keeps failures without promoting them. Installed temporary console entry points
are not shipped; original auxiliary CLI sources are retained with execution support
explicitly unverified. A completed assembly remains unapproved until the existing
pack verifier, relocation, full processing, notices and release gates pass.

Preflight rejects NFC/case-fold-colliding output paths before executing the stage
installer, even on case-sensitive Linux. Explicit `pythonOmissions` may exclude
unused runtime data, with each omitted original member and hash retained. For the
non-interactive recognition profile, the PBS terminal database must be explicitly
excluded when it has conflicting terminal-name aliases; do not choose an arbitrary
alias, mutate the source archive or disable the pack's cross-platform path checks.
Such an omission is not qualification of a general interactive Python runtime.

Linux assembly input/receipt v2 supports an explicit `wheelOmissions` list for
Windows installer templates bundled in the selected pip/setuptools wheels. Each
entry supplies `sourceSha256`, original `member`, exact member `sha256` and `reason`.
Only the known Windows launcher names and Windows PE program headers are eligible;
runtime modules, libraries and model files cannot be removed this way. The whole
original wheel and installed bytes are checked first, and affected RECORD files
are rewritten to match the shipped files. Original wheel inputs and omitted-member
evidence remain available. V1 manifests cannot silently use this option; v2 without
explicit omissions is rejected. Unlisted files remain, and the final pack verifier
still rejects foreign native binaries rather than treating them as harmless data.

Locally built wheels use the separately versioned
[derived-artifact provenance and versioned recognition audits](../deployment/README.md#original-inputs-and-locally-built-artifacts).
Keep original download hashes, built outputs, shipped recipes and actual build
records distinct. A successful provenance/packaging check is neither a reproducible
build claim nor document quality, full-resource or redistribution approval.
Authored recipes, build records and scoped license collections use audit v3's
separate hash-bound documentation records, not fabricated upstream origins. This
does not replace source verification for runtime files or authenticate authorship.
