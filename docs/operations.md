# Internal installation and operation

Start with the checked release's support matrix and pack manifests. An archive
being well formed does not establish extraction quality or platform compatibility.

## Configuration and network

Copy `deployment/server.example.json` outside the source tree and use private
absolute storage/pack paths. Profiles are administrator-managed and revisioned.
Local CPU profiles reference installed packs. Cloud profiles reference an explicit
endpoint/model plus `apiKeyEnv`; optionally pair `packRoot` and `recognitionPackId`
for local PDF recognition. Never store inline API keys in profile JSON.
Cloud profiles can explicitly select `responseFormat`, `strictSchema`,
`maxOutputTokens` and `sampling` (`temperature`, `top_p`, `top_k`, `seed`); unset
sampling keeps the provider's defaults. The managed local interpreter always
decodes greedily. These settings belong to the administrator profile, not upload
options. Changing them changes execution identity; unsupported provider features
fail with a diagnostic rather than switching modes or endpoints automatically.

Local-pack profiles can set `threads` (generation) and `threadsBatch` (prompt
processing) as explicit integers. Unset values keep the pinned llama.cpp defaults;
the product never sizes them from the host. On the inspected Apple M5 (four
performance and six efficiency cores), `threads` 4 with `threadsBatch` 10 processed
prompts about a third faster than the default at unchanged generation speed, while
ten generation threads slowed generation by about a third. Under a container CPU
quota keep both values within the quota. These values are part of the profile
fingerprint and of the recorded model identity, like the other profile settings.

Generate a random server token of at least 32 ASCII characters and supply it via
`DOCUMENT_FILES_SERVER_TOKEN` or the container's owner-readable secret file.
The foreground service defaults to loopback. LAN publication still requires auth;
terminate TLS at the authenticated reverse proxy described in the deployment guide.
Use the offline Compose network for offline jobs. Cloud-enabled networks are an
explicitly different administrator configuration, not fallback behavior.

The service accepts `POST /v1/jobs` as a bounded `application/octet-stream` body
with `X-Document-Format`, `X-Model-Profile`, optional JSON `X-Extraction-Options`,
and an optional `Idempotency-Key`. Same key + same bytes/options/profile pins returns
that job; any difference conflicts. No server file paths or input URLs are accepted.

## Progress and failures

A process reaching its end is not proof of complete extraction. Inspect the stored
result's `extraction.status`, `validation` and `coverage`, not only job lifecycle.
The job response separates `executionStatus` from `extractionStatus`; a finished
attempt can still contain a partial extraction. `resultRevision` identifies the
committed snapshot, including paged reads. Restart pagination if the version changes.
A partial result may contain useful committed observations/values. Unresolved fields,
notes, OCR/native conflicts, oversized regions and unprocessed pages are not silently
converted into absent values. Authentication, quota, timeout, invalid output, unavailable
runtime and context-budget errors are distinct; do not work around them by enabling
another endpoint or disabling checks.

Recognition works on one framework page at a time; completed frames can be saved
before the worker finishes and replayed without repeating completed OCR. Semantic
checkpoints store accepted region decisions, not the whole conversation history.
Record tables additionally checkpoint structure and meaning separately. A structure
may be retained as `structure_compiled` while the extraction remains partial. Resume
then uses the saved structure, not another record-generation call. Each stage has at
most two attempts sharing the total call/time budget; exhausted stages require an
explicit additional grant. Checkpoint v2 includes table-protocol identity; old
incompatible checkpoints must not be force-resumed. A stage completing is not a
semantic quality approval. Non-record subtotal/note values now use a separate scalar
region, with no overlapping value bindings and the same total budget. Its unfinished
work remains partial and resumes without another record-structure call.
The current table protocol is v6. Every observed non-fixed row needs an explicit
role; only native-declared header rows are fixed automatically, not OCR predictions.
Missing cells and invalid decimal readings stay uncertain with original evidence.
Row source provenance is program-derived; meaning applicability uses exclusive
column/record/row/unresolved choices. Exact quotes and explicit remaining-text reviews
have a separate source inventory. Repairs can correct or withdraw a mistaken meaning
but must retain original text coverage and a hash-bound revision/change history.
Scope uncertainty is separate from unreviewed source text. Independent content review
is mandatory: structural acceptance and fewer issues do not establish correctness.
Resume checks the input and exact installed configuration. A budget grant is explicit;
it never resets existing usage. No automatic replay occurs after service interruption.
For a deliberately longer run, select the CPU profile and pass the supplied
`deployment/long-document-options.json` to `start-job --options` or
`extract-schema --options`. This grants up to 64 calls and one hour; it does not
increase the local model's 8,192-token context or promise completion within that
time. The legacy synchronous defaults remain unchanged. Budget exhaustion keeps
the unprocessed regions and a resumable position rather than dropping them.

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
silent model upgrade. Changing parser package versions alone does not request Corpus
reanalysis of unchanged documents.

Runtime packages, model weights, product source, installed-client evidence and final
quality qualification are tracked separately. Keep logs to job ID, stage, timings,
usage and fixed error codes. Default logs must not contain source text, keys, prompts
or model responses. Review `SECURITY.md` for document/native-code trust boundaries.

## Qualification and publication

Use [the release procedure](../deployment/RELEASE.md) to freeze a clean candidate,
bind its actual artifacts to independent reviews and installed execution evidence,
and publish those same bytes. Development results and historical candidate bundles
cannot be substituted for the new candidate. ChatGPT capability limitations are
recorded per feature; explicit AI unavailability is never an AI quality pass.
