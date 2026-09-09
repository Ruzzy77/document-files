# One bounded Linux CPU / 16 GiB qualification run

This is a procedure and a configuration candidate, **not a passed platform test**.
A 16 GiB container ceiling is not a claim that a 16 GiB physical computer has enough
room for its operating system and container runtime. Keep host headroom available.
No Windows/native-platform support follows from a Linux container result.

## Current execution blocker

The preparation host inspected on 2026-09-08 is macOS ARM64, 24 GiB RAM and 10 CPU
cores. No Docker, Podman, Colima, Lima or OrbStack runtime/socket was found. Its
existing Tailscale client was stopped, with no current peer inventory; known
remote development host names did not resolve. No service was started, settings
changed, credentials exposed, remote document sent or model loaded for this check.
These are time-specific observations, not claims about unavailable hosts' hardware.

The CI definitions now include four-platform CPU runtime builds and a dedicated
Linux cgroup isolation preflight (`cpu-runtime.yml`, `cpu-environment.yml`). The
preflight executes only a small synthetic memory allocation, not a document or
model. Workflow definitions and contract tests are not execution evidence; retain
the actual run artifacts. Recognition plus model qualification remains separate. Mac-only runtime packs cannot be activated in the Linux image. A previously
prepared model may also declare an exact Mac runtime compatibility identity; do
not edit its manifest or bypass that check to make it load on Linux.

Before the single run below, an approved Linux x64 host must have an existing
Docker Engine/Compose installation, sufficient host RAM/free disk, cgroup v2 memory
and swap controllers, a locally loaded digest-pinned product image, and validated
Linux CPU recognition/runtime packs plus an exactly compatible model pack. Missing
Linux artifacts are a provisioning blocker, not a reason to run Mac binaries under
emulation or silently fetch replacements. Do not rebuild existing whole packs just
to perform an environment inventory. Resolve any provisioning need separately.

## Fixed configuration

Set the normal administrator paths described in `README.md` to a **dedicated**
qualification state/token/config copy, not a live personal store. Use Compose
2.24.4 or newer and exactly these two configuration files:

```sh
docker compose -f deployment/compose.yaml \
  -f deployment/compose.cpu-qualification.yaml config
docker compose -f deployment/compose.yaml \
  -f deployment/compose.cpu-qualification.yaml up -d --no-build --pull never
```

Review rendered configuration before starting. It must retain read-only root and
pack mounts, non-root UID, dropped capabilities, no device passthrough and bounded
process count. The override fixes RAM and RAM-plus-swap to 17,179,869,184 bytes
(16 GiB), four CPU equivalents, standard `runc`, and `network_mode: none`, removing
inherited ports/networks/devices. A missing feature is a failed preflight, never a
reason to remove the limit or enable privileged mode. Base-image, product, pack,
model, policy and source hashes belong in the run receipt.

Docker's memory limit alone can permit an additional equal amount of swap. Equal
memory and memory-swap limits disable swap. `free` inside the container does not
prove that setting; check the effective cgroup files instead. Do not disable the
OOM killer. [Docker memory constraints](https://docs.docker.com/engine/containers/resource_constraints/)

The base internal bridge is useful for a loopback-published service but is not the
strict qualification isolation boundary. The `none` driver provides only loopback;
this run intentionally cannot validate host-port publishing. Compose merge reset
support is required to remove inherited configuration.
[Docker none networking](https://docs.docker.com/engine/network/drivers/none/),
[Compose merge/reset rules](https://docs.docker.com/reference/compose-file/merge/)

## Preflight and measurement

Use selected `docker inspect` fields only, never dump container environment or the
bearer token. Verify `HostConfig.Memory == HostConfig.MemorySwap == 17179869184`,
`NetworkMode == "none"`, empty GPU DeviceRequests/Devices, `Privileged == false`,
read-only root and expected mounts. Inside the container verify:

- `/sys/fs/cgroup/memory.max` is `17179869184`, `memory.swap.max` is `0` and
  `memory.swap.current` stays `0`. Capture `memory.events` before and after.
- `/sys/class/net` contains only `lo`; no `/dev/nvidia*` or `/dev/dri` GPU device
  exists. No socket beyond the service/llama loopback path is needed. CPU-only
  runtime provenance and the product's explicit `--device none --n-gpu-layers 0`
  remain required even on a GPU-equipped development host.
- Record `memory.current`, mandatory `memory.peak`, `memory.stat`, `cpu.stat`
  and `pids.current` at one-second intervals over the entire request/cancel/resume
  sequence. Save to private state with no document/token/log content. Stop the
  sampler after the run. Container-wide cgroup accounting includes workers,
  recognition, llama and the small sampler, including charged mapped-file cache.
- For diagnostic **process-tree RSS**, enumerate container PIDs/PPIDs and sum VmRSS
  from `/proc/<pid>/status`; also sum Pss from `smaps_rollup` when readable. Label
  this as sampled, potentially incomplete for short-lived processes; shared pages
  can be counted repeatedly in RSS. Do not mistake this sum for cgroup accounting
  or a precise peak. Do not record process command lines or environment values.

Docker stats can subtract cache from displayed usage, so it is not sufficient as
the sole memory measure for an mmap-backed GGUF model. Use raw cgroup controller
values and record which metrics were unavailable rather than inventing zeros.
[Docker runtime metrics](https://docs.docker.com/engine/containers/runmetrics/),
[Docker stats accounting](https://docs.docker.com/reference/cli/docker/container/stats/),
[pinned llama-server options](https://github.com/ggml-org/llama.cpp/blob/9dcf84e5ae2718947188b539aab8b9c2b15d3ba1/tools/server/README.md)

## Evidence recorder contract

Use `scripts/measure_cpu_execution.py` inside the already restricted container. It
refuses non-Linux hosts, root, missing cgroup-v2 isolation, excessive/absent limits,
network interfaces beyond loopback, GPU devices, privileges and incomplete counters.
The recorder does not create isolation or install a pack. The qualification gate
requires the same execution object in its receipt and raw measurements, with a
successful exit, no timeout, complete samples, unchanged limits and no recorder
errors. Failed launch/measurement/cleanup retains a failed receipt, not an approval.

The evidence directory must be a dedicated writable mount owned by the container
UID. Keep input, inventory and recorder source read-only. Example inside the
container (paths are deployment administrator selections):

```sh
python -B /source/scripts/measure_cpu_execution.py \
  --evidence-root /evidence --output measured-run-01 \
  --identity /evidence/identity.json --timeout 1200 \
  --report model-run-01/local_model.json -- \
  python -B /source/evaluation/run.py --kind local --holdout-only \
    --holdout /inputs/single-short-case.json --config /config/server.json \
    --profile cpu --output /evidence/model-run-01 --evidence-root /evidence \
    --artifact-inventory /evidence/inventory.json --artifact core-wheel \
    --artifact source-sdist --artifact runtime-linux --artifact model --artifact recognition-linux \
    --artifact product-image --core-artifact core-wheel --evaluator-artifact source-sdist
```

This bounded example assumes one short case (900 seconds inference plus at most
300 seconds recognition/overhead), prepared inputs, and artifact IDs that actually
exist in the candidate inventory. For a full manifest select a whole-run timeout
from all predeclared case and recognition budgets; do not silently enlarge a failed
inference budget. Identity supplies
`version`, clean `sourceCommit`, `dirtySource:false`, and the exact artifact IDs.
The recorder injects a fresh `DOCUMENT_FILES_EXECUTION_RUN_ID`; the evaluator
records it and leaves its report immutable. The receipt references that report's
hash. Qualification links the report, execution receipt and later independent
review without a cyclic hash. Raw measurements contain no input, token, environment
or subprocess stdout/stderr. Keep document results separately with appropriate
private permissions.

## Exactly one bounded end-to-end sequence

Use one public/synthetic mixed PDF with predetermined content assertions, not a
private source. Stream its bytes through `docker compose exec -T document-files
python ...` rather than a network upload from the host. The small in-container
client reads `/run/secrets/server_token` and sends an authenticated request to
`http://127.0.0.1:8765/v1/jobs` with `X-Document-Format: pdf` and
`X-Model-Profile: cpu`; it must never print the token. Choose the already approved
CPU profile and finite extraction/page/output/time budgets, not caller-selected
model endpoints or arbitrary input paths. Poll the returned job ID internally.

During that same job, exercise cancel and one explicit resume at the planned page
boundary if the checkpoint test requires it; do not rerun the full input from
scratch or cycle through model/OCR candidates. Fetch the final structured result
through the same authenticated loopback client. Preserve the partial result and
resource evidence on timeout, OOM or content failure; do not automatically retry.
Check the expected content/source links independently of the model's own status.

Record request completion/partial/failure, exact hashes, configured budgets,
wall-time, effective resource limits, measured peak, event-counter deltas, no-GPU
and network-isolation checks, child-process termination and pack integrity. A
resource-bounded process that loses content is **not** a quality pass. A good result
with an OOM event, swap use or missing bound evidence is **not** a 16 GiB pass.

Before stopping/removing the dedicated container, collect its host-side identity
with `scripts/capture_container_identity.py` using the explicit full Docker container
ID, trusted aggregate inventory SHA256, selected image artifact ID, host recorder
receipt and that same receipt's container path. Add its immutable reference as the
`local_model` check's `containerIdentityReceipt`. See [release identity instructions](RELEASE.md#actual-container-image-identity-is-collected-on-its-host).
The collector compares actual Docker `.Image` to the inventory/build receipt's
`imageId`; registry RepoDigests are recorded separately. It does not obtain image
identity from an in-container environment variable or claim that a prepared image
was actually built/qualified. Do not substitute the plain-image CI preflight.

Stop the dedicated container after collecting evidence. Keep the image, immutable
packs, original provision and result/receipt; remove only this run's temporary
container and scratch copies. Docker install/upgrade/rollback and host-published
HTTP connectivity remain separate unverified items unless actually exercised.
