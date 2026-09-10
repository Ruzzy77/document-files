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
limit does not guarantee a final-answer reservation or correctness.

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

### Internal table stages

Prompt v23 / planner v14 / table protocol v10 use a structural classification first.
Record tables compile one record definition before interpreting meanings over fixed
IDs; scalar forms retain their binding-based path. `coverage.tableInterpretation`
records each stage's attempts, status and usage. `structure_compiled` means retained
partial work, not complete extraction. Compiler v16 retains the rejection of broad
unbounded parent/child scope unions and preserves explicit bounded row intersections.
Public AnalysisJob v1, AnalysisResult v1 and extraction result contracts remain;
private checkpoint v2 checks the changed protocol identities.

The model-only meaning view places compiler-added header references in the frozen
column definitions instead of repeating a compiled-definition projection. It omits
all-cell/row provenance only where table geometry reproduces it and removes only
exact text copies from context; basis, conflicts and uncertainty remain. Canonical
result and checkpoint provenance are unchanged.

Reference-wire v2 can replace source/table references with separate short handles;
it never rewrites literal text, values, semantic IDs or JSON Schema references as
source identifiers. Activation compares actual request sizes independently of
repair feedback. Source-decision keys and additional quote references are restored
before normal validation. The complete dictionary is not repeated in model input.
Checkpoint `referenceWire` records version/dictionary/hash (or `null` when disabled); restore
recomputes the selection even for completed stages. `inputPreflight` records stage,
initial/repair phase, component character sizes, actual total and limit. These are
character diagnostics, not tokenizer counts. Preparation failure or overflow preserves
compiled structure and does not consume a model call.

Structure permits at most two attempts. Meaning permits at most two attempts to
obtain its first accepted response, followed by at most one review of that response.
These are stage ceilings, not extra document budget. `attempts` counts all stage
dispatches and `reviewAttempts` counts post-acceptance dispatches; usage remains
cumulative across explicit grants. Exhausted stages do not restart automatically.
Repair feedback includes `remainingSourceRanges` with exact source text, offsets
and hashes. Source reviews describe text outside direct meaning quotes in that source;
explicit `unreviewed` also keeps fully quoted or empty sources pending.
Changing stage policy invalidates earlier protocol checkpoints, not public v1 APIs.

Stage-one row decisions use `{row, role}` for every observed non-fixed row; omissions,
duplicates and unknown rows are rejected. Only wholly native-declared header rows
are fixed automatically. OCR flags remain predictions, and row sources come from
actual geometry. Missing source cells are never shifted or created. Scope v7 uses
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
The model supplies `sourceDecisions`, keyed by every owned `meaningSources` reference.
Each decision is `no_additional_meaning`, `unresolved`, `unreviewed`, or `has_meaning`.
The first three require an explanation and forbid meanings. `has_meaning` requires
`meanings` plus `remainderReview` (role and explanation). A meaning has `quotes`
(exact local text, optional zero-based `occurrence`) and optional `additionalQuotes`
with other owned `sourceRef` values. No model-generated offsets are accepted.
Joint evidence is anchored once at the earliest quoted source in inventory order;
multiple meanings per source and noncontiguous quotes remain valid. The program
records original Unicode ranges/hashes and canonical `sourceReviews` (review v2).
Successful value/header reads do not replace review, and reference context is not
direct evidence. Duplicate meaning IDs or identical meaning copies are rejected.

The full response requires `regionId`, `sourceDecisions`, `baseRevision` and `changes`.
The first response uses a null base and empty changes. Repairs cite the accepted
revision and account for every changed or withdrawn meaning with replacements and/or
source reviews. Kind, description, scope and status may change; prior source coverage
and compiled structure/values may not disappear. Reviewed sources cannot become
explicitly unreviewed, even when fully quoted or empty. Revision hashes include transition
reasons, and checkpoint resume validates the history. Repeated decisions, including
reason-only changes, are not progress. Exposing uncertainty can be a valid correction.

`coverage.semanticSourceReviews` contains versioned program-checked source ranges;
`semanticDetails` contains direct quote ranges separately from surrounding context.
Source review, applicability validity and independent semantic approval are distinct.
Public v1 contracts remain unchanged; these are private protocol/versioned metadata.

Recognition adapter v26 binds optional `native_library_directories` to its identity.
Linux administrator profiles resolve the manifest's `nativeLibraryDirectories`
within the verified recognition pack; the actual worker uses only these explicit
directories, never the host's `LD_LIBRARY_PATH` or `LD_PRELOAD`. JSON array transport
is normalized to an immutable tuple. Older adapter checkpoints cannot resume as v26;
the public result v1 contracts are unchanged.

Adapter v27 selects table orientation from a unique containing original OCR crop
on the same local page. It requires observed upright OSD plus matched crop/input
pixels; a later caption's orientation or no-rotation fallback cannot authorize or
block another table. `document-files.table-orientation.v1` records the selection in
repair evidence and binds reuse. Unknown, rotated, ambiguous or mismatched sources
remain unresolved. The default repair policy and budgets are unchanged; adapter
v26 checkpoints do not resume as v27.

Recognition adapter v18 introduced explicit `ruled_cells_v2` file-list repair.
Administrator `repairBudget` accepts `batchSize` (1–2), `maxImages` (1–64) and
`maxInputPixels` (1–64,000,000), all integers excluding booleans. Defaults remain
1 / 8 / 16,000,000; a two-image profile must opt into `batchSize: 2` and an image
budget such as `maxImages: 16`. Existing `maxCalls: 8`, `maxSeconds: 60` and crop
`maxPixels: 16000000` are separate limits, not multiplied by the batch size.

`document-files.raw-ocr.v2` retains a complete, unmodified process TSV in
`rawOCRRuns`. Per-image captures bind its fingerprint, original TSV row ordinal,
input page number and exact cell/image transform. `cell-image-batch.v1` validates
at most two images from one table/page/language/PSM. A TSV input index does not
change the source PDF page. Incomplete or failed output remains evidence, not a
completed or reusable observation. Prior policy identities cannot resume as this
contract. These are internal observation records; the public result v1 is unchanged.

Adapter v23 records `document-files.pdf-native-objects.v3` and
`document-files.native-cell-decision.v2`. The first is a bounded, original-byte
inventory of supported PDF content streams and native objects, not whole-document
completeness. Missing unmerged cells can be added as exact empty-string observations
only when the same source/page has a complete supported inventory, four unique
closed native borders, no other touching content and a fully opaque-white observed
interior. Only that table's missing-cell count is reduced; the original issue and
per-cell evidence remain in provenance. An empty source cell does not mean absent,
zero or not applicable. Existing OCR text and global completeness warnings remain.
Embedded TrueType support requires original font bytes to match the native loaded
font, an unambiguous supported character mapping and verified glyph outlines. PDFium
text projection remains unchanged; `sourceText` separately preserves mapped source
bytes. Actual non-generated characters must match their native object and literal
source exactly. Additional projection characters require verified generated status,
character order and object membership; real trailing spaces are never stripped. Text
paint bounds include both the original logical bounds and transformed outline control
points, including overhangs. A null outline is not proof of an empty glyph; the matching
font glyph interval must also be empty, and logical space bounds remain protected.
Unsupported fonts/content, rotation, ambiguous geometry, missing pixels or budget
limits prevent this decision. Earlier recognition adapter checkpoints cannot be resumed.

Adapter v20 emits `document-files.cell-observation.v2` records. It inspects existing
canvas pixels for the full cell, interior, OCR window and remaining edge bands without
additional rendering. Frame creation and observation share one actual full-canvas
hash inside one operation; separately supplied frames still require an independent
pixel hash comparison. This avoids duplicate work without increasing the pixel budget.
Pixel, cell and preparation work are bounded; low-contrast and
nonopaque pixels are not silently discarded. `recognitionCellPixelObservations`
provenance separates internal measurement checks, original-page coordinate linkage
and unique table/slot correspondence. Import does not recompute unavailable RGB
pixels or approve OCR accuracy. Raw OCR-link evidence retains an `unverified` execution
status even when references agree. No new value binding, blank-value assertion or
content-completeness approval follows; older adapter checkpoints cannot be resumed
under the new identity, and v1 cell-observation records are not accepted as v2 evidence.

Recognition adapter v12 records `document-files.recognition-coordinates.v1` evidence
for captured OCR input pixels through the actual framework crop/rotation and the
serialized single-page PDF to the original page. It rechecks raw-pass/source/page
fingerprints on import. Synthetic padding has no source support; unsupported mapping
remains unverified. The record does not validate OCR text, framework-reported structure
boxes, complete visual coverage or reading order.

Adapter v13 adds `document-files.full-render-visual.v1` observations to the full-page
render capture and `pdfPageVisualObservations` to imported metadata. Actual full RGB,
profile, source and page identities bind pixel counts and bounded connected components.
The default limits are 16 million pixels, 65,536 foreground row runs and 4,096 components;
omitted geometry and unprocessed areas remain explicit. Non-white and low-contrast pixels
are observations, not text/graphic/background classifications. Import validates the
record's binding and counts, not OCR truth. These records do not resolve existing issues,
prove blank values or change extraction completeness. Public v1 remains unchanged.

Adapter v14 adds `document-files.visual-correspondence.v1` records under provenance
`visualCorrespondences`. They link component and native-character/raw-OCR bbox
candidates in both directions, with source/coordinate/render/recognition identities.
Structure links reuse only rechecked exact source-text ranges; reported structure
boxes are not used as independent evidence. Default per-page limits are 8,192 source
items, 1,024 observations per kind, 262,144 bbox comparisons and 8,192 candidates.
Missing geometry, partial source support, multiple/no candidates and unexamined
comparisons remain explicit. The records are not added to model payloads, do not
verify pixel assignments, and do not resolve completeness, reading order or blankness.

Adapter v16 uses `document-files.ordered-ocr-source.v2` under provenance
`recognitionOrderedSourceAlignments`. Only a unique full-text sequence in a verified
horizontal raw OCR line can support repeated tokens; table overlap and ambiguous or
incomplete duplicate lines are excluded. Raw captures and original text are unchanged.
`observed-processing-ledger.v4` records accepted source positions without claiming OCR
truth, blankness or page completeness. Prior derived records/checkpoints are not reused
as current evidence.

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

### OCR ruling evidence

Recognition adapter v24 adds `recognitionNativeRulingObservations` to observation
provenance. Bounded RGB windows from the actual OCR input retain every pixel and
link to the frame, raw TSV and detection. A narrow native-line check may report
matching support; it does not classify or delete the original text, clear issues,
or approve document completeness. Older recognition checkpoints are incompatible.
The public result, CLI and MCP contracts are unchanged.

Adapter v25 adds `recognitionNativeRulingConsumptions` (consumption v1 and observed
processing ledger v5). The receiver rechecks original pixel/native evidence, an exact
one-to-one raw OCR/source cell link, and a unique table grid with four closed native
cell borders. A matching source keeps its original text, node, binding and table
context but is excluded from data candidates. The affected disposition and issues
are retained in resolution provenance. A complete returned-detection ledger does
not establish page completeness, OCR truth or scan blankness. Prior checkpoints
remain incompatible; public v1 contracts are unchanged.


### Internal PDF page review

An explicit managed vision pack enables `document-files.pdf-visual-review.v3` before
regional interpretation. Internal page state uses `reviewing_pdf`; source observation,
image preparation, exact pixel/grid and application policy versions participate in
checkpoint identity. This adds no public caller-supplied interpretation endpoint.
Version 2 excludes grid timing and the separate legacy whole-page projection from
page identity; original PDF, additive observations and geometry remain bound.
Version 3 also offers a unique overlapping native line's bounds when its text exactly
matches an observed source. Both original bounds and the additional source reference
are retained; no margin is invented and this candidate does not approve text. Empty
decision inventories allow only empty arrays; other arrays have exact cardinality.
Previous plans/checkpoints are incompatible and are not automatically migrated.

The model classifies source-linked pixel units and missing slots and orders observed
blocks. The receiver rejects unsupported structural pixels, incomplete detail coverage,
unknown units and geometry-conflicting order. All pages must pass before atomic
application; original nodes, bindings and raw OCR remain unchanged. Added blank nodes
use `observationBasis="visual_pdf_page_review"`, never native-proof metadata.
`provenance.observation.pdfVisualReviewApplication` records the accepted page/decision
fingerprints, region order and exact original issues replaced by the review. Derived
observation counts/status are recomputed. `ocrTruthVerified` and independent quality
approval remain false; table content accuracy is not certified by an empty-slot decision.

Page attempts consume the same finite model-call/time budget as semantic interpretation.
The engine saves a running attempt before inference, reuses reviewed pages after resume,
and does not automatically replay unknown, failed or interrupted page calls. The existing
final extraction-complete predicate and public v1 result/CLI/MCP contracts are unchanged.
