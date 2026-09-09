# Support and delivery boundaries

The Python API, CLI, MCP and HTTP interfaces use one Document Files engine. Local
model capability is supplied by separate versioned runtime/model packs. Core,
recognition, llama.cpp runtime and model versions can be inspected independently.

| Environment | Native core and CPU semantic runtime | Full recognition profile |
|---|---|---|
| macOS Apple Silicon | Targeted | Native isolated Docling CPU pack |
| macOS Intel | Targeted, not pinned to old Torch/ORT | Local Linux x64 CPU container |
| Windows x64 | Targeted | Native isolated Docling CPU pack |
| Linux x64 | Targeted | Isolated Docling CPU pack / container |

**Targeted is not a claim of completed end-to-end qualification.** Each pack must
state its actual minimum OS, libc/CPU requirements and compatible runtime digest.
Upstream wheels/binaries existing is different from a tested product installation.
The container requires a compatible locally installed container environment; it
is not the same as the Python-free native portable distribution.

The selected recognition configuration is Docling CPU, Heron layout, TableFormer
accurate and Tesseract Korean/English. Semantic inference uses official Qwen3.5-9B
converted to Q4_K_M by pinned llama.cpp, non-thinking, with one CPU slot. These are
implementation selections, not accuracy or practical-speed guarantees. Long or
ambiguous documents may produce partial results and unresolved relationships.

## 1.8.0 candidate status

This checkout is **not a qualified stable release**. The source-bound compiler,
regional interpretation, durable jobs and HTTP/MCP interfaces have regression
coverage. Apple Silicon core bundles and separate CPU runtime/model/recognition
pack candidates have been assembled locally. Core CLI/MCP processing was exercised
without a host Python on PATH; recognition-pack relocation was checked without
loading the recognition models. Neither establishes installed-client qualification.

### Latest source follow-up: release preparation

The scope interpreter now offers observed header groups over their actual mapped
child columns and separates the current statement from surrounding source context.
Scope v5 normalizes repeated exact destinations (a group and its leaves), rejects
mixed ancestor/child selections and keeps region-local pointers distinct. Compiler
v12 records the column-definition mapping privately; public result v1 is unchanged.

The first v3 real-model rerun still applied the unit too broadly despite a complete
result flag. With v4, two real scope calls selected the correct measurement columns
and condition after replaying the accepted regional response, but duplicate group
and leaf targets were rejected. Replaying those saved decisions through v5 preserved
the two exact records, applied mm to Length/Width only and the condition to Length.
The subsequent fresh v5 full-product-path development run completed in 3 calls /
245 seconds and independently matched exact rows, precision, header provenance and
both scopes. None of these runs is independent holdout or 16 GiB qualification.

The saved-recognition scan returned partial after 6 calls / 359 seconds. Model-only
metadata compaction (prompt v14 / planner v9) removed input overflow: 10 regions,
none oversized, instead of 18 regions with 15 oversized inputs. The actual rerun
still failed after 2 calls / 373 seconds because the model emitted repeated copies
of the same records until the output limit. Source observations were not truncated.

Prompt v16 / planner v10 / table protocol v2 now separate one record-structure
decision from meaning over its frozen compiled values. Label/value forms use the
existing scalar path. Stage attempts and usage are checkpointed; each stage permits
at most two calls within the unchanged total budget. Cancellation, invalid meaning
or budget exhaustion preserves a compiled structure, not a complete extraction.
Header-only rows and shifted data-row references are rejected before freezing.
Compiler v13 leaves unbounded parent/child meaning scopes unresolved instead of
silently broadening column assertions; explicitly bounded row intersections remain.

A fresh two-stage merged-header run used 2 calls / 113 seconds but incorrectly froze
an extra header record. After structural checks, a bounded 5-call / 258-second run
preserved exactly two source records and precision. It still failed semantic review:
the unit scope was unresolved after an invalid broad selection, and caption accounting
was incomplete. The previous single-stage development pass does not qualify this new
protocol. Do not increase a failed run's budget automatically or call it a quality pass.

Recognition adapter v10 retains original raw OCR detections and a bidirectional
processing ledger, separate native-text support, and a source-bound full-visible-page
render fingerprint. The actual applied rotation is distinct from an unknown orientation
observation. Render coverage, OCR truth and semantic completeness are different claims.
Missing cells, conflicts and unclassified visual content remain unresolved; no original
issue has been removed. Full visual-content and reading-order accounting is unfinished.
The current blanket recognition-completeness issue still blocks every recognition-backed
PDF from the complete-only release gate. This remains a release blocker.

Current core regression: **685 passed, 10 skipped, 12 subtests passed**; full Ruff passes.
The separate recognition-runtime test overlay passed 60 PDF/OCR tests; exact final
recognition bundles remain unqualified. Eleven input/specification cases covering nine
formats are prepared but have not undergone final model evaluation.

Evaluation v2 retains independently prepared per-case review specifications outside
model input. A separate review receipt binds the immutable inference report and
results. Release qualification v2 binds clean source, actual assets and installed
results, and uses linked cgroup measurements rather than summed process RSS.
Fresh-output builds and exact-byte promotion are implemented. Large packs use
explicit split/join transport without changing their reconstructed ZIP identity.
These tools do not supply missing platform, client or model-quality evidence.

Actual CI passed the Linux 16 GiB / four-CPU, swap-free, network-isolated preflight
and all four pinned CPU runtime builds. The preflight ran only a small synthetic
allocation, not OCR or inference. Linux, Windows and Apple Silicon portable core builds and
Python-free CLI/MCP smoke passed on an intermediate commit; this is not final-candidate
or installed-client qualification. Linux native recognition dependencies built and
passed relocated startup/language discovery; Windows build correction awaits rerun.
Compiler runtime notices are collected but redistribution review remains pending.
The built Linux CPU runtime requires glibc 2.38: the earlier bookworm preflight image
is not a compatible full-inference image. Final image/pack compatibility must be checked. The existing
Apple Silicon development pack remains selected for development runs; new runtime
packs must be paired with explicitly compatible model manifests before inference.
The x64 source-built runtime declares AVX2/FMA/F16C/BMI2 requirements, not generic x64.
See [release preparation and promotion](deployment/RELEASE.md).

### Improvements checked on the development cases

- Oversized non-table regions now have source-addressed subdivisions, unique value
  binding ownership and cumulative view coverage. Atomic fields, unbroken tokens
  or required context that cannot fit are explicitly partial, never truncated or
  silently omitted. Unseen long-document interpretation remains unqualified.
- One real CPU semantic call now returned the exact long identifier, integer `0`
  and boolean `false` with source-bound field definitions and values. That call
  remained partial because three local meaning scopes were absent. Same-region
  scope discovery and bounded scope-only repair have been implemented; scripted
  resume tests preserve the accepted region and repair three scopes in one call.
  One subsequent real CPU scope-only batch, following replay of that saved regional
  response, correctly attached all three meanings to their independently expected
  fields, preserved typed data and cleared the issues. The new scope call used
  1,767 input and 528 output tokens; no regional model call was repeated. This is
  affected-stage evidence, not an independent holdout or a fresh end-to-end run.
- On the existing public scanned-table case, the corrected `ruled_cells_v2`
  pipeline recovered all 11 nonempty cells exactly through actual TableFormer,
  including the Korean/English header order. Original recognition indices/text
  remain unchanged; structure-view clones carry a separate order and source map.
  One explicit budget resume reused completed pages/cells. The missing empty cell
  remains unobserved, not a fabricated blank. This is an affected development case,
  not full scanned-document or unseen-form qualification.
- Table request planning now measures the current wire contract rather than reserving
  12,000 characters unconditionally. Identical reference choices share schema
  definitions without weakening validation; required context is still retained.
- Prompt v12, planner v7 and compiler v7 give the interpreter program-derived table
  geometry: every table payload lists `columnCandidates` (each column index with the
  declared header cells above it) and `dataRows`; a repeat may map each column index
  once, data rows left outside every repeat are reported as `table_rows_outside_repeat`,
  a table's own caption is read with the table instead of as a separate region, and
  token (lexeme) spans are no longer offered as value choices because the interpreter
  cannot identify them by offset and guessed spans produced meaningless values. The
  managed interpreter now decodes greedily with a recorded seed. Regression tests cover
  the payload, the compiler checks, caption ownership across slices and the sampling
  identity; the real-model effect is recorded under the CPU timing follow-up.
- Prompt v13, planner v8 and compiler v8 withhold declared header cells and captions as
  value choices altogether, show each column candidate's header text, attach the
  declared headers above a column to its definition provenance, and report a cited
  header that does not sit above its column and a missing lowest header. A header text
  can therefore no longer be extracted as a data value, which is a documented limitation
  for pivot-style headers. Compiler v9 additionally drops a scalar field that re-reads a
  record cell of a repeat column, records the drop on that cell's accounting entry, and
  routes meanings that pointed at the dropped field to the scope repair step; subtotal,
  note and header rows keep their scalar fields. Compiler v10 degrades a source-less
  present field to uncertain instead of invalidating the response, compiler v11 drops a
  source-less scalar field defined only by mapped column headers, and scope statements
  from one source node are decided in separate requests after a batched decision was
  copied from one statement to the other.
- Prompt v11, planner v6 and compiler v6 treat declared header cells as definitions:
  header text is no longer a required value candidate, a field that reads a declared
  header cell as its value is reported as `header_cell_bound_as_value` and can be
  repaired without counting the dropped header reading as a regression, and repair
  feedback follows the unchanged region and contract in the request. Regression tests
  cover the flag, the repair and the planner. The real-model effect on the
  merged-header case is recorded under the CPU timing follow-up.
- A new synthetic multi-row-header form exposed header-only region slicing: the
  model interpreted header labels as values. That run was stopped after the first
  response and the checkpoint retained. Region plan v4 keeps header geometry with
  data views and removes context-only scalar choices; this example now fits as one
  15,668-character table request rather than four table fragments. These changes
  have regression coverage. One bounded CPU call on the corrected table timed out
  at 360 seconds without a complete response. It is **not** a semantic quality pass;
  token usage for that interrupted response is unknown, not zero consumption.

### CPU timing follow-up

Two bounded probes reused the same authorized public table request without trying
another full extraction: maximum 32 output tokens/90 seconds, then maximum one
output token/45 seconds with one native stack sample. Both timed out after checking
4,410 input tokens. Runtime preparation took about 16.46 and 8.02 seconds; context
checks took 0.015 and 0.030 seconds. The remaining wait was in the server exchange.
A one-second owned-process sample during the second request contained CPU tensor
computation/llama decode frames, not grammar/sampler frames. This establishes what
was active at that sampled instant, not a whole-run time breakdown or universal
cause. Neither probe produced server prompt/prediction timing receipts.

Increasing the response length limit or repeating the complete evaluation is not
justified by these findings. The next performance work should reduce repeated input
and contract processing and measure prompt evaluation separately from generation,
with the same correctness contract and fixed CPU pack. Do not substitute a faster
model or enable GPU/network fallback automatically. New managed diagnostics and
unreported-usage counters are implemented; old zero usage subtotals do not establish
zero computation or consumption.

A split measurement on 2026-09-09 (same Mac: Apple M5, four performance and six
efficiency cores, 24 GB) reused the current prompt v10 request of the same public
synthetic form through the product launcher and its loopback server, with bounded
token counts and generated text discarded. The formatted request is 4,253 tokens
(the earlier v9 request measured 4,410; a 3.6% token reduction). With the pinned
default threads, full prompt processing took 77.8 s cold and 78.0 s warm (54.5
tokens/s); a cached repeat processed 4 tokens in 0.12 s. Adding the product grammar
cost no measurable setup time (0.129 s versus 0.128 s for one token), and 64-token
generation ran at 12.8 tokens/s with grammar and 12.9 tokens/s without. Ten threads
raised prompt processing to 72.1 tokens/s but cut generation to 8.7 tokens/s; four
generation threads with ten prompt-processing threads gave 73.0 and 12.6 tokens/s.
Prompt processing alone therefore needs about 80 s at default threads, which
accounts for the earlier 45 s and 90 s probe timeouts; grammar setup, grammar
sampling and model page-in are excluded as causes. The remaining cost is output
length: the 3,072-token ceiling alone takes about 240 s at 12.8 tokens/s. Local-pack
profiles can now set `threads`/`threadsBatch` explicitly; defaults are unchanged.
These are Mac measurements, not x64 or 16 GiB evidence.

A bounded rerun of the merged-header development case on the same day (two calls,
900 s budget, default threads) completed both calls instead of timing out: the
regional call processed 4,253 prompt tokens in 81 s and generated 1,055 tokens in
89 s; the repair call reused only 708 cached tokens, processed 3,568 tokens in 68 s
and generated 1,862 tokens in 163 s. Both responses read the declared header cells
as scalar field values and mapped no repeat, so the two data rows were never
expanded; identifiers and decimals were preserved exactly, and the mm unit and the
length condition were attached to scalar fields. This is a semantic failure of the
affected case, not a latency failure. Prefix-reuse probes showed that the pinned
server restores an early checkpoint (708 tokens in these runs) whenever the new
prompt diverges before the previous prompt's final tokens, so the repair request
reprocessed 3,568 tokens although it shared about 2,300 tokens with the original;
`--checkpoint-min-step 512` did not change this. An identical prompt, or one that
differs only after position 4,249, is reused in full. Prompt v11 therefore places
repair feedback after the unchanged region and contract; scope calls with a
different payload still pay most of the prompt cost.

A further bounded rerun with prompt v11, planner v6, compiler v6 and explicit
`threads` 4 / `threadsBatch` 10 through the product client (three calls, 900 s
budget, 591 s used) again completed without timeouts. The regional call processed
4,300 tokens at 73 tokens/s and generated 2,534 tokens; the repair call reused
3,784 cached tokens and processed only 565 (8.6 s instead of 68 s); the caption
region call processed 1,684 tokens. Semantically, the first response still read the
four declared header cells as values, now reported as `header_cell_bound_as_value`,
and mapped a single repeat row with duplicated columns. The repair dropped the
header values but also dropped the repeat and one data cell, so the engine kept the
first response and reported `region_repair_no_progress`. The caption region produced
fields from arbitrarily chosen lexeme bindings. Exact identifiers and decimals were
preserved and the mm unit reached the length and width fields. The case remains
failed on repeat mapping and merged-header relationships; it is development
material.

With prompt v12, planner v7, compiler v7 and greedy decoding (two calls, 405 s; the
caption now belongs to the table region, so no separate caption call), the first
response for the first time mapped one repeat over both data rows with data row
roles, but paired the merged "Measurements" header with column 1 and the "Length"
header with column 2, dropped "Width", and still added a scalar field for every
cell, including the four header cells and the caption. The repair reused 3,495
cached tokens (7.8 s of prompt processing) but repeated property keys and was
rejected as invalid, so the first response stayed. Identifiers and decimals were
exact; the unit and condition reached the length fields and the repeat. Prompt
v13, planner v8 and compiler v8 respond structurally: header cells and captions
are no longer value choices, column candidates carry their header text, and
misplaced header citations are reported for repair.

With prompt v13, planner v8 and compiler v8 (two calls, 236 s), the first response
mapped one repeat over both data rows with the correct column indices and the
lowest header of each column cited, so the compiled records matched the two
expected source rows exactly and the merged "Measurements" header entered the
column provenance programmatically. It still added three scalar fields on record
cells (one mislabelled) and attached the unit and condition to those scalar
fields; the repair swapped two column indices and referenced consumed bindings in
`excludedBindings`, was rejected, and the first response stayed. Compiler v9 now
drops such scalar readings of record cells and routes their meanings to scope
repair. The rerun with compiler v9 (two calls, 264 s) exposed the next weakness:
the first response repeated the correct repeat but also emitted a `present` field
with no source binding, which invalidated the whole response, and the repair then
removed the repeat and read two width cells as length and width. Compiler v10
therefore degrades a source-less present field to `uncertain` with a
`field_binding_missing` report instead of discarding the response. Greedy decoding
with ten prompt-processing threads did not reproduce identical outputs across
runs; it removes sampling noise, not floating-point order effects.

The rerun with compiler v10 (three calls, 340 s) is the first run of this case that
ended `complete`: the regional call kept the correct two-row repeat while the
compiler dropped five redundant scalar readings and degraded one source-less
field; the repair (3,343 cached tokens) resolved that field as absent and kept the
repeat with data row roles; the scope call attached the unit and the condition to
the Length column. Compiled records match the two expected source rows exactly and
the merged header sits in the column provenance. Two defects remain against the
fixed expectations: the mm unit was applied to Length only, not Width, and the
model still declared three `absent` scalar fields for header labels (`sample_id`,
`measurement_value`, `measurement_value_row`) that add null properties. This is one
development case with a 9B CPU model, not a holdout pass.

With compiler v11 and per-source scope requests (three calls, 207 s, 6,083 prompt
and 1,735 completion tokens), the same regional response compiled directly into the
correct two-row repeat with no scalar leftovers and no repair call: seven redundant
cell readings and one header-label field were dropped into the accounting ledger.
Decided separately, the condition statement was applied to the Length column and the
unit statement was left unresolved by the model, so the result is `partial` with one
`semantic_scope_unresolved` issue rather than a wrong scope. Against the fixed
expectations, records, merged-header provenance and the condition scope pass; the
mm unit should reach Length and Width and remains open.

### Input compaction follow-up

Prompt v10 / region planner v5 retain standard JSON Schema while sharing repeated
constraints. The model-only table projection uses explicit `columns-rows.v1`
headers and all cell rows when every cell has the same property keys and the
representation is smaller. Mixed keys stay objects: a missing property is never
padded with null. Header geometry, ordering, precision and source references are
preserved; stored observations and public results are unchanged.

A serialization-only comparison of the previously recorded public merged-header
request reduced the output contract from 6,226 to 6,053 characters and table data
from 1,294 to 723 characters. Including the new explanation of the representation,
full message content changed from 15,700 to 15,227 characters (about 3%). These are
character counts, not measured tokens or latency. No model calls were issued for
this comparison. The 165 related regression tests pass, including exact cell
round-trip, required/null/bounds validation and planned-versus-actual request size.

This modest total reduction does not resolve the CPU latency gate. Actual model
interpretation of the new representation and decoder acceptance remain unverified;
do not count stored-response tests as that evidence or publish the candidate as
qualified. Further optimization should target the measured input/compute bottleneck,
not remove meaning-bearing source content or repeatedly rerun full extraction.

### Remaining release gates and next bounded steps

| Gate | Required next step, without restarting all evaluation |
|---|---|
| Scanned table correctness | The affected 11 nonempty cells passed through TableFormer. Verify full field/header semantics and blank-versus-unobserved states, then unseen scans. Do not substitute observed no-ink for a semantic blank. |
| Semantic scope quality | The affected three-field scope repair passed. The merged-header development case now compiles the correct two-row repeat with merged-header provenance and the condition scope without repair calls; the unit statement stays unresolved. Next: full scanned/long-document semantics and independent forms. Use a two-step table protocol only if remaining structural errors or bounded execution require it; no release holdout has passed. |
| Long documents | Use affected long/continued-table cases to check complete repeat ranges, heading/note scope and source-view accounting; keep indivisible-context limits explicit. |
| 16 GB / GPU-free operation | Run the [bounded Linux CPU procedure](deployment/CPU_QUALIFICATION.md) on an available host with compatible Linux packs. The inspected Mac is 24 GB; no container runtime or reachable Tailscale peer was available. |
| Delivery | Install/update/rollback and actual client processing still need Windows, Linux, Intel Mac, installed Codex/Claude and ChatGPT-host evidence. |

The latest source changes are not in the previously assembled core bundles or the
immutable v1 recognition pack. A new explicitly versioned/configured candidate is
required before delivery checks. Static container-policy tests are not an actual
Compose, no-network or 16 GiB resource test. Mac CPU measurements are neither x64
speed evidence nor proof that a physical 16 GB computer has sufficient headroom.

Do not publish stable assets, advertise these targets as passed, or switch Toolkit/
Sync to this candidate while these gates remain open. Existing results and the
frozen compatibility source are retained; packaging changes do not force reanalysis.

## Required delivery evidence

Record the product commit, pack manifest digests, OS/hardware, install/update/
rollback results, actual client and HTTP processing, and independently reviewed
recognition/extraction cases. Include the license inventory, model conversion
receipt, support limitations and operational instructions. Keep scanned/long-
document quality and CPU latency/memory findings distinct from packaging success.
No SLA, managed service, automatic updates, indefinite historical-version support,
or specific processing throughput is implied by the OSS package.

Report ordinary issues through the repository issue tracker using versions,
platform and a minimal public or synthetic example. Report security issues through
SECURITY.md. A source document need not and should not be shared by default.
