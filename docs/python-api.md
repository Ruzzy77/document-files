# Python, CLI and MCP integration

The supported Python import is `document_files.api`. The engine, CLI, local MCP and
HTTP service call the same document interpretation implementation. A host chat
subscription is not an API credential. Nothing falls back to an unconfigured model.

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
The managed client decodes greedily (`temperature` 0 with a recorded seed) so that the
same input, contract and pack produce the same structural decisions; `ChatCompletionsClient`
accepts an explicit `sampling` mapping for cloud endpoints and records it in its identity.

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

### Internal table stages

Prompt v19 / planner v13 / table protocol v5 use a structural classification first.
Record tables compile one record definition before interpreting meanings over fixed
IDs; scalar forms retain their binding-based path. `coverage.tableInterpretation`
records each stage's attempts, status and usage. `structure_compiled` means retained
partial work, not complete extraction. Compiler v14 rejects broad unbounded
parent/child scope unions while preserving explicit bounded row intersections.
Public AnalysisJob v1, AnalysisResult v1 and extraction result contracts remain;
private checkpoint v2 checks the changed protocol identities.

Stage-one row decisions use `{row, role}` for every observed non-fixed row; omissions,
duplicates and unknown rows are rejected. Only wholly native-declared header rows
are fixed automatically. OCR flags remain predictions, and row sources come from
actual geometry. Missing source cells are never shifted or created. Scope v6 uses
compiled header roles for group candidates, including AI-classified OCR headers.

Non-record subtotal/note and unmapped cells are routed to a separate scalar region
(`parentRegionId`, `tableContextRef`). `valueRoutes` records each actual cell's route;
parent and child do not own the same value bindings. The child shares the document
budget, is checkpointed, and cannot regenerate repeats. An unresolved mapped value
stays unresolved in its original record. Decimal strings are checked lexically,
without float conversion; unsupported locale/unit-bearing text remains uncertain
with its source spelling. Duplicate removal requires the same successful binding
and representation, not merely the same cell reference.

Stage-two `scope` selects exactly one of `columns` (`columnIds`), `record`,
`rows` (inclusive actual bounds and optional column intersection), or `unresolved`.
The internal wire is converted to the unchanged source-linked Meaning IR. Accounting
repair may add source-bound statements or resolve uncertain scopes, but cannot remove
accepted statements or change compiled structure/values to reduce issues. An accepted
response or interpreted scope denotes mechanical validity, not verified meaning quality.

### Internal input compaction

Prompt v11, planner v6 and compiler v6 additionally treat declared header cells as
definitions rather than value candidates. Prompt v12, planner v7 and compiler v7 add
program-derived `columnCandidates` (each column index with its declared header cells)
and `dataRows` to every table payload, enforce one property per column index, report
data rows outside every repeat, read a table's own caption with the table, and withhold
token spans as value choices. Prompt v13, planner v8 and compiler v8 also withhold declared
header cells and captions as value choices, add the header text to each column candidate,
attach the declared headers above a column to its definition provenance, and report
`column_definition_not_above_column` and `column_leaf_header_missing` for repair.
Compiler v9 drops a scalar field that re-reads a record cell of a repeat column, records
the drop as `redundantFieldIds` on that cell's entry in `coverage.semanticAccounting`, and
leaves any meaning that pointed at the dropped field to the scope repair step. Compiler
v10 keeps a `present` field that names no source binding as an `uncertain`, unread field
reported as `field_binding_missing`; only unknown binding identifiers still invalidate the
response. Compiler v11 drops a source-less scalar field whose definitions are only declared
header cells of mapped repeat columns, recording it as `redundantFieldIds` on the header's
accounting entry, and scope statements that share one source node are decided in separate
scope requests. SUPPORT.md records the affected-case status.
Regional prompt v10 and planner v5 use shared standard JSON Schema definitions and
an optional `columns-rows.v1` cell projection. In this private model input, each
matrix row represents one cell object (not one document record); `columns` specifies
its property order. Every cell and property value is retained. Heterogeneous cell
keys and small lists remain objects. This projection does not change public result
schemas, stored observations, source IDs or compiler value access. Custom model
clients receive the same complete decoder schema through `output_schema`; no new
shorthand schema dialect is required. Compatible checkpoint checks include the
new prompt/planner versions; do not bypass a mismatch to reuse an earlier decision.
