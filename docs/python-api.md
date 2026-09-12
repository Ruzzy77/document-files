# Python, CLI and MCP integration

Internal applicability uses scope-axis protocol v6, selection wire v2 and compiler-owned
source-binding v2 over scope integration v12 / reference wire v2 / compiler v19.
Private regional checkpoint v3 records the actual scope policy, batch context,
citation-free selection and compiler source trace. Replay regenerates the request
identity and source bindings before applying a saved decision; incompatible older
checkpoints are rejected. Public v1 APIs remain unchanged. This is not quality approval.

The model returns one `selections` list, choosing record intersections or standalone
candidates without being required to fill two unrelated lists. Record selections
use the same `columnHandle` identifiers displayed with column candidates and record
columns, not a second set of column IDs. Only typed selection fields are translated;
source text, labels and explanations remain literal. The general codec retains
scalar-only/mixed decisions, multiple records, header groups, bounded candidates,
missing rows and up to eight independent tasks. The axis codec still validates the
canonical intersections; overlaps and foreign references remain invalid.

For records the model independently selects rowCoverage and columnCoverage and the
compiler applies their intersection. allDataRows is not allMappedColumns. Row
references are fragment-local; optional numeric sourceRowRange endpoints retain
unobserved coordinates without inventing cells. Standalone scalar/mixed candidates
remain available. A row-only selection does not annotate a whole column's schema.
Scalar candidates now include valueOrigins: an existing binding and observation
status, bounded original value text and source-table coordinates. Compiled row roles
are labeled as interpretation, not native geometry or an applicability rule.
Missing bindings remain missing; disagreements between row mappings remain visible.
This context and the full private evidence participate in fingerprints; changing
geometry, role, binding or observed status invalidates prior context. Source-binding
v2 verifies selected scalar evidence before linking its existing value source.

The model does not write sourceRefs, values or generated pointers. Code binds current
content/definitions and existing selected scalar/row-filter value sources; blank,
absent and uncertain states remain distinct. Source binding and semantic accuracy
are separate checks. Overlaps, stale mappings and explicit expansion/source limits
still fail closed. Long-range compact provenance remains unimplemented.

On ManagedPackClient only, the engine requests reasoning budget 2,048 for this phase
and caps total output at 3,072 tokens (or a smaller client ceiling). Other phases keep
the profile default. The same request override reaches template/token checks and
inference, without mutating the profile or leaking to later calls. Generic/cloud
clients keep their own reasoning policy; no llama-specific parameters are sent.
Complete-only clients retain their own output limits, recorded distinctly in the
checkpoint. All calls and elapsed time share the existing cumulative document budget.

Prompt v24 / table protocol v17 distinguish a meaning's own uncertainty from
unknown applicability. Internal `Meaning.status` describes its kind and content;
an unresolved `scope` no longer overwrites that status. The compiler retains this
private status in task identity and marks the public result uncertain until both
content and applicability are resolved. Selecting valid rows or columns cannot
clear `semantic_interpretation_uncertain`; such results stay partial, including
after checkpoint resume. Scope-only uncertainty may be resolved normally. No new
public result field is added, and old internal checkpoints cannot resume. The
uncertainty issue itself does not trigger rereading unchanged content when only
applicability was being resolved. This does not certify actual-model quality.

The supported Python import is `document_files.api`. The engine, CLI, local MCP and
HTTP service call the same document interpretation implementation. A host chat
subscription is not an API credential. Nothing falls back to an unconfigured model.

## Native reading and HWPX operations

`inspect`, `extract` and `extract-structure` use the same byte-stream analysis path
and present a summary, text/Markdown or structured data respectively. Choose the
needed view rather than calling all three in sequence. Native reading needs no AI
model; basic HWP/HWPX reading also does not require the optional editing/rendering
backend. Runtime-dependent operations report their availability through `capabilities`.

`extract-structure` returns source-declared locations and values in `sourceStructure`
and format-common `semanticRole` / `semantic`. XLSX preserves typed values, formulas
and saved calculation caches without executing formulas. `unitPage.nextOffset` pages
large structured results. Output truncation and remaining pages are separate from
incomplete source extraction. `inspect` / `extract` expose dimensional coverage as
`coverageProfile`; structured extraction uses `coverage` for that object. Markdown
does not reproduce the source's page layout.

For HWPX cell editing, use the `tableMap.tables` returned by `inspect` with verified `sectionPath`,
`tableIndex`, `row` and `col`. The `selectorBasis` value
`verified-section-xml-table-order` records alignment with the section XML and editor's
table order. Do not substitute a list position or sourceRef when no selector exists.
Pass the complete, untruncated cell text from the same input bytes as `expectedOldText`.
The editor refuses repeated physical-cell selectors, additional lines that exceed the
existing paragraph capacity, and edits of an outer cell containing a nested table.
Ordinary cells within that nested table remain editable. `verify.ok` and
`comparison.tableGeometryPreserved` answer different questions; check the latter for
source-relative table preservation. Conversion/editing publishes a separate output.

The analysis contract consists of `AnalysisJob v1` plus a separate byte stream and
returns `AnalysisResult v1`, with format, size and SHA256 identifying the bytes rather
than their local path. Temporary input copies allow safe repeated parser access and
are removed after analysis. The local `process` JSONL boundary used by Sync carries
an inherited read-only descriptor; Windows uses a separately verified read-only
snapshot. This transport does not change the shared analysis contract. Original-file
ownership, capture/revision/projection management and access policy belong to callers.

## In-process extraction

```python
from document_files.api import ChatCompletionsClient, extract_schema

client = ChatCompletionsClient(
    endpoint="http://127.0.0.1:8080/v1/chat/completions",
    model="your-explicitly-configured-model",
)
result = extract_schema(
    "/absolute/path/document.docx",
    model_client=client,
    options={"reconstructionContext": False},
    retain=False,
)
if result["extraction"]["status"] != "complete":
    handle_partial(result["issues"], result["coverage"])
```

Supply credentials through your application's secret handling, not source code.
`response_format="json_schema"` opts into a provider's structured-output support;
`json_object` remains the compatibility default. Both receive the same reference
checks. `ModelClient.complete(messages, timeout=...)` remains supported. The richer
`InferenceModelClient.infer(InferenceRequest(...))` adds an output contract, output
budget, cancellation and token usage. Cancellation of a blocking third-party
transport is bounded by its timeout; managed jobs can stop their worker process.

`strict_schema=True` (the default) adapts structured output to a closed-object,
all-properties-required wire contract while leaving public optional fields and
nullable types unchanged. Free dictionaries cannot be silently closed; an
unsupported strict contract returns `ai_output_contract_unsupported`. Use an
explicit `strict_schema=False` or `json_object` only for a provider configured for
that mode; the product does not change modes on failure. The closed/required rules
follow the [structured-output specification](https://developers.openai.com/api/docs/guides/structured-outputs).
The managed llama.cpp runtime uses its own constrained grammar without that cloud
wire dialect. Every mode passes through the same source-reference/compiler checks.

Use `ManagedPackClient(pack_root, runtime_id, model_id)` for installed CPU packs and
always call `close()` in a `finally` block. Its model starts lazily, after PDF
recognition. It validates the actual formatted prompt token count against its
fixed 8,192-token working context and 3,072-token output ceiling; it does not
silently expand memory or truncate the input. Model pack installation and a real
successful extraction are separate checks. Optional `threads` and `threads_batch`
keyword arguments pass explicit llama.cpp generation and prompt-processing thread
counts; they are recorded in `identity`, and unset values keep the runtime defaults.
The managed client decodes greedily (`temperature` 0 with a recorded seed). This fixes
sampling policy, not floating-point reduction order or run-to-run output identity.
`ChatCompletionsClient` accepts an explicit `sampling` mapping for cloud endpoints
and records it in its identity.

Only a model pack with an explicit `model.vision` projector can accept image content
parts in a managed `InferenceRequest`. The internal transport accepts inline PNG/JPEG
data URLs, not paths or remote URLs: up to two single-frame, opaque 8-bit RGB/L images,
16 MiB and 16 million pixels in total. It preserves the supplied bytes and rejects
ambiguous metadata or unsupported media. The server's actual multimodal capability
and image-aware input-token count are checked before generation. Projector digest,
image-token limits and `document-files.managed-vision.v1` policy belong to both client
and job execution identity. Text-only pack identity remains unchanged. This is an
input-transport capability, not an automatic PDF visual-review or completion result.

Optional `reasoning_budget_tokens=1024` enables thinking with a finite per-block limit.
The administrator-profile spelling is `reasoningBudgetTokens`. Omission preserves the
existing non-thinking default and profile identity. Explicit values must be integers
from 0 to 3,071, with the request's total output ceiling strictly larger; conflicts
fail with `ai_reasoning_budget_conflict` before startup rather than changing a budget.
The 3,072-token total ceiling still includes reasoning and final output. Mode, budget
and `document-files.managed-reasoning.v1` are included in execution/profile identity;
the original manifest's thinking setting is recorded separately, not overwritten.
Reasoning content is never substituted for final JSON. A reasoning-limited `length`
response with no final content retains usage and incomplete status. The per-block
limit does not guarantee a final-answer reservation or correctness. Internal
InferenceRequest.reasoning_budget_tokens overrides this setting for one managed
request; None inherits the profile. The generic HTTP adapter rejects an explicit
managed override with ai_request_reasoning_unsupported rather than silently ignoring it.

For split text regions, `coverage.regions[*].nodeViews` identifies original source
ranges, and `semanticDetails[*].sourceRanges` records the ranges actually read for
those statements. Value bindings retain original absolute positions. `readNodes`
counts fully covered nodes; `partiallyReadNodes` distinguishes nodes with pending
views. An oversized indivisible value/context remains partial with a budget reason.

For an installed recognition pack, use an administrator profile with
`packRoot`, `runtimeId`, `modelId`, `recognitionPackId`. The optional recognition
backend is resolved by `document_files.profiles.profile_clients(config, name)`:

```python
from document_files.api import extract_schema
from document_files.profiles import profile_clients

with profile_clients("/absolute/path/server.json", "cpu") as (model, observation):
    result = extract_schema(
        "/absolute/path/document.pdf",
        model_client=model,
        observation_backend=observation,
        options={"reconstructionContext": False},
    )
```

For byte streams, use `AnalysisInput.from_bytes`, `AnalysisJob` and
`extract_schema_from_stream(job, binary_stream, ...)`. This entry point does not
retain a database unless the caller supplies a checkpoint callback. Input bytes
must match the declared format, byte count and SHA256.

## Results and explicit resume

`extraction_result_schema()` returns the public JSON Schema, independently of the
private AI decision schema. `result_types` holds the public evidence/assertion types.

- `document.nodes`: unchanged native nodes plus additive precise observations.
- `document.bindings`: product-generated source addresses and Unicode code-point ranges.
- `document.structure`: versioned regions, observed cells, hierarchy and reference edges.
- `semantics`, `schemaEvidence`, `valueEvidence`: existing v1 interpretation and provenance.
- `semanticDetails`: source-linked applicability, including conditions that are **not executable**.
- `valueObservations`: exact native scalar/formula/cache observations where available.
- `coverage`: pending regions and explicit unsupported/unresolved content.
- `resultRevision`: monotonically increasing committed snapshot version for retained results.

Values come from bindings; the internal model cannot return a final `data` object
or calculate JSON Pointers. The program expands all observed repeated rows and
checks every canonical table-cell candidate is represented or explicitly classified.
Merged cell spans, synthesized recognition holes, blank values and missing values
are not interchangeable. `validation.valid` is mechanical consistency, **not** an
independent assessment of semantic correctness. `complete` is not an accuracy warranty.
An unresolved statement retains empty detailed scope and an uncertain assertion
anchored to the document, without discarding other source-bound values. Cross-region
resolution adds `scopeEvidence`; it never makes a natural-language condition executable.

`extract_schema(..., request_id="stable")` returns the existing result for identical
inputs; changing the input/options/model/observation/prompt/compiler identity conflicts.
`resume_extraction` is explicit. Existing result-only v1 databases remain readable;
old incompatible private checkpoints are not silently replayed. The explicitly
selected `interpretation_protocol="legacy"` transport option retains the old
Proposal adapter for integrations that deliberately require it; new work defaults
to compact region interpretation.

```python
from document_files.api import resume_extraction, get_extraction, delete_extraction

result = resume_extraction(
    "stable",
    model_client=client,
    additional_budget={"maxModelCalls": 4, "completionSeconds": 300},
)
page = get_extraction("stable", section="valueEvidence", offset=0, limit=100)
delete_extraction("stable")  # private result/checkpoint only; never original input
```

A resume without a grant uses only remaining cumulative budget. Use the same
observation backend when resuming. Grants do not alter interpretation options or
reset usage. Sections include `nodes`, `bindings`, `regions`, `tables`, `relations`,
`semantics`, `semanticDetails`, `schemaEvidence`, `valueEvidence`, `valueObservations`
and `issues`. Large results are one logical result, not independent documents.
Each page includes `resultRevision` and `extractionStatus` from the same stored
snapshot. If the revision changes between pages, restart the page traversal rather
than combining different snapshots. Legacy unversioned pages use revision zero;
existing full result bodies are not rewritten merely by reading them.

## Managed jobs

Python applications can explicitly own `JobStore` and `JobService`; call
`service.start()` and `service.close()` with application lifetime. There is one
supervised extraction process. No Redis, Celery or hidden daemon is installed.
`JobClient` connects to an explicitly running HTTP service.
Job `executionStatus="finished"` means the attempt ended, not that extraction is
complete. Check `extractionStatus` independently. `resultAvailable` distinguishes
no result from a readable legacy result whose `resultRevision` is zero. Existing
`status` values remain available for compatibility.

```sh
export DOCUMENT_FILES_SERVER_TOKEN='<read from your secret store>'
document-files serve --config /absolute/path/server.json
# In another terminal with the same auth configuration:
document-files start-job /absolute/path/document.pdf --profile cpu
document-files job-status JOB_ID
document-files job-result JOB_ID --section valueEvidence
document-files cancel-job JOB_ID
document-files resume-job JOB_ID --additional-budget /absolute/path/grant.json
document-files delete-job JOB_ID
```

`DOCUMENT_FILES_SERVER_URL` defaults to `http://127.0.0.1:8765`. A nonlocal endpoint
must be explicitly configured. Never expose an internal service through a public
tunnel merely to connect a hosted chat client.

Local MCP retains all previous tool names and adds `document_start_job`,
`document_job_status`, `document_job_result`, `document_cancel_job`,
`document_resume_job`, `document_delete_job`. These managed-job tools use the same
explicit server configuration. Deletes are destructive, not read-only. HTTP upload
options cannot supply server paths, model URLs, executable paths or storage roots.

See [deployment](../deployment/README.md), [architecture](product-architecture.md)
and [support boundaries](../SUPPORT.md) before treating a candidate as a release.

### Inference timing and unreported usage

`ManagedPackClient.last_diagnostics` is the last completed/failed call snapshot,
not a prompt or response log. It reports runtime preparation, context checking and
server-exchange seconds, checked input-token count and requested output limit.
Image requests additionally report validation time and input image hashes, dimensions
and byte/pixel counts; image bytes are not copied into these diagnostics.
The extraction result/checkpoint also retains this snapshot as
`extraction.lastInferenceDiagnostics`. It contains no document text, endpoint or key.
`InferenceResponse.timings` retains only supported finite, nonnegative numeric
server timing fields. When the server supplies them, prompt processing and token
prediction can be measured separately; a timeout with no response cannot establish
that split. The context-check duration is template/token-budget validation, **not**
model prompt-evaluation time. Slot waiting and initial pack verification are not
included in the reported inference-stage interval.

`extraction.usage.promptTokens` and `completionTokens` are reported subtotals.
`unreportedUsageCalls > 0` means at least one call has incomplete or unavailable
usage; a zero subtotal must not be read as zero consumption. The counter is saved
before invoking the model and survives interruption/resume. Historical checkpoints
without the counter conservatively treat their earlier calls as usage-unreported.
A complete-only `ModelClient` has no token usage receipt and remains unreported.
Neither this flag nor an incomplete response promotes partial model text to values.

For a bounded CPU diagnosis, reuse only an authorized public example and lower the
output-token cap explicitly; do not run the full evaluation suite or change model
packs. A `length` finish from that probe is expected and is not an extraction result.

Server timing keys follow the pinned
[llama.cpp timing response](https://github.com/ggml-org/llama.cpp/blob/9dcf84e5ae2718947188b539aab8b9c2b15d3ba1/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions-api).
They are optional measurements, not an accuracy certificate or timeout usage receipt.

## Internal extraction behavior

The public calling and result contracts above are separate from private model protocols.
See [the extraction-engine reference](extraction-engine.md) for observation ownership,
PDF/OCR review, table stages, applicability, exact bindings, request limits and
checkpoint identity. [SUPPORT.md](../SUPPORT.md) records current data-preservation
bugs and actual model checks. Do not infer semantic accuracy from a valid result schema.
