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
| Linux ARM64 | Targeted | Isolated Docling CPU pack / container |

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

### Latest table-meaning development check

Prompt v24 / table protocol v16 / compiler v18 / scope integration v11 now preserve
content uncertainty independently from applicability. Earlier conversion changed
a clear meaning to uncertain when its scope was unknown, while later scope
application could promote an explicitly uncertain meaning to interpreted. The
new contract distinguishes these cases: a valid scope resolves only applicability.
Content uncertainty remains a partial result, including after checkpoint resume;
private status is part of task freshness, not a new public result field. Old
internal checkpoints are rejected. Local checks passed 2,435 tests (227 skipped,
12 subtests), including 465 related tests with warnings as errors. Current-source
ARM regression is pending; these checks are not actual-model qualification.

Row selection still uses actual source geometry and compiler-owned value mappings.
It creates no rows or values, preserves gaps and unresolved roles, and cannot attach
a row-only scope to the whole column schema. Source references and original fragment
coordinates survive continuation. Invalid or overlapping choices are rejected before
changing values. Shared request assembly now ensures development checks include the
same visible output contract as product calls, without changing serialization.

Four more one-call controls on source `bf58327` used the existing Spark CPU model.
Each had a predeclared 300-second / 1,536-output-token cap and no repair or grant:

| Control | Inference / host seconds | Actual result |
|---|---|---|
| Supported six-category grammar | 98.03 / 111.54 | Exact unit and condition quotes; remainder review still unresolved |
| Explicit remainder-review labels | 100.69 / 114.42 | Remainder reviewed, but the unit was misclassified as a definition |
| Grouped observed row cells | 92.48 / 105.65 | Row applicability unresolved despite complete supplied coordinates |
| Same grouped rows, thinking capped at 512 tokens | 126.46 / 139.75 | Row applicability still unresolved |

**None passed its quality check; none of these experimental wire changes or thinking
settings is adopted.** Content controls omitted scope generation; row controls used
given structure and note content, so neither is a fresh full-document extraction.
The pinned upstream Python converter confirmed that the earlier category schema's
sibling `properties`/`anyOf` combination lost required category properties; the
corrected schema produces all six category rules. This conversion inspection is
not a full C++ grammar-language validation. Responses were not repaired by inferring
roles from explanations or moving units to another category. Diagnostic compilation
preserved values, schema and source evidence.

All four containers used 4 CPU / 16GiB, with zero swap/OOM/GPU/external network.
Cgroup peaks ranged from 6,730,346,496 to 6,734,839,808 bytes. Some runs overlapped,
so these are not speed comparisons. No new OCR or rendering was performed, and
this is not full recognition-plus-inference resource approval. Original failed
product budgets are unchanged. Owned servers and containers ended; transfer archives,
temporary pack activations and the downloaded converter were hash-checked and removed.
Failure evidence and pinned source references remain available. The prior shared-
assembly source passed 429 related tests on ARM; that is not verification of v16.

#### Earlier v15 content and target-reference controls

An identical-request v15 comparison on Spark produced schema-valid, compiler-accepted
replies in both modes, but **neither passed semantic quality**. Non-thinking omitted
the separate unit; bounded thinking (512 tokens per reasoning block, within the same
3,072-token total cap) separated the condition and unit with exact clause quotes.
Both applied every meaning to the whole record, incorrectly including Sample ID.
Each had one predeclared 300-second call: 111.70/158.75 inference seconds and
124.56/171.50 host seconds. The shared input/schema was identical; input token counts
differed with the model template. The two 4 CPU/16GiB containers overlapped, so this
is not a speed benchmark. Cgroup peaks were 6,763,479,040/6,790,471,680 bytes with
zero swap/OOM/GPU/external network. Source values, schema and source bindings stayed
intact, while the accepted broad scopes added incorrect semantic links. Original
responses and failed quality results are preserved; no product profile changed.

Preparing a separate applicability check exposed an eight-reference context cutoff:
the record had eleven short definition references that fit the request budget, yet
the omitted tail forced `candidateCoverage: bounded`. Scope integration v8 now bounds
encoded context by the existing request allowance instead of reference count. Text
truncation, absent nodes and omitted candidates still prevent full resolution.
Three bounded scope-only comparisons then kept those five complete candidates.
Non-thinking selected overlapping record/column scopes for both meanings; the
existing compiler rejected both atomically. With 512-token bounded thinking, the
condition selected Length correctly but the unit still selected the whole record.
Increasing reasoning alone therefore did not pass this development comparison.

A final non-thinking control changed **only typed target IDs** to short aliases
such as `@column2` and `@headerGroup3`. Original wording, source references, candidate
order, containment and all five options stayed identical, verified by exact inverse
translation. Condition then applied to Length alone; unit applied to Length and
Width, never Sample ID or the record. The unit also selected its header group;
existing exact-target deduplication retained the same four value targets. Each had
one 300-second / 1,536-output-token cap: 67.84/70.33 inference seconds,
81.13/83.08 host seconds, and cgroup peaks 6,781,837,312/6,781,943,808 bytes under
4 CPU/16GiB/no-swap/no-OOM/no-GPU/no-external-network limits. These overlapping
runs are not a speed benchmark. No original quality budget was increased.

Scope integration v9 / scope reference wire v1 now performs that target-only
translation at the product model boundary, then decodes before validation and
stores only canonical decisions. Foreign aliases, canonical-ID bypasses and
malformed siblings remain invalid without losing valid sibling decisions; old
checkpoint identities are rejected. Values, quotations, source IDs and literal
alias-like text are not translated. Local replay of the saved responses through
the product codec matches the reviewed scopes, values, schema and provenance;
existing unordered disposition entries are compared as a multiset only.

**This is not a complete product or independent document pass.** The scope controls
reuse content from the thinking detail response and explicitly defer all scopes in
a separate diagnostic copy. Original replies/checkpoints are unchanged. The product
still sends only unresolved meanings to applicability review; a confidently wrong
record scope can bypass it. Mandatory separate content/applicability decisions need
a design that preserves row-specific and row/column scopes, record scope and
cross-region references. The codec does not add those capabilities or change the
production non-thinking profile.

Table protocol v15 uses separate, durable source selection inside the
meaning stage. Selection and detail use the existing shared finite allowances, not
new budgets. Source/model/structure/reference identity and selection-to-meaning
history are checked on resume. All-negative reviews compile without a detail call;
unknown/deferred sources remain partial. Incorrect choices can be explicitly revised,
with old meanings preserved until a valid replacement is accepted. Detail output
now contains meanings and selected-source remainder reviews only; saved choices and
other reviews are reused without model regeneration. Positive selections cannot
return an empty meaning list. The program also fixes the meaning revision baseline
in the output contract, rather than allowing the model to copy the selection hash.
That v15 bookkeeping fix passed actual controlled detail calls above; a successful
fresh full-document product run is still pending.
Regression checks alone do not establish quality or release qualification.

The v14 source `2df5f12` completed the same bounded fresh-HTML/restart path in 4 calls,
345.05 charged product seconds and 360.97 host seconds. Selection again matched all
11 source choices. Focused detail replies now contained a meaning, but both copied
the selection hash into the initial meaning baseline and were correctly rejected
with `table_meaning_initial_revision_has_history`. Independently, their proposed
condition covered the whole record and no separate unit meaning was produced:
**development quality still failed**. v15 fixes only the baseline bookkeeping; it
does not relabel these scope/meaning errors as successes. The two rows, precision,
schema and provenance remain intact. The cgroup peak was 7,087,624,192 bytes under
the same 4 CPU/16GiB/no-swap/no-OOM/no-GPU/no-external-network limits. No new OCR or
render calls occurred, and owned servers/containers were terminated.

The preceding v13 source `8d45c94` completed a fresh native HTML product-engine run
on Spark: 4 calls within the predeclared 900-second development budget, 429.82 seconds
of charged product time and 445.98 seconds on the host. It saved the correct source
selection (11/11), stopped the first server, then resumed details on a new server
without reselecting. Both detail replies nevertheless omitted the unit and condition,
so the compiler rejected them with `table_source_decision_quote_mismatch`. The final
result is **partial and development quality failed**, with both rows, precise strings,
schema and provenance preserved. There were no new OCR or render calls. Four CPU cores,
16GiB cgroup memory, zero swap/OOM/GPU/external network and a 7,079,960,576-byte cgroup
peak were recorded; this HTML run is not full recognition resource qualification.
Owned servers/containers were terminated. The original exhausted HTML trial and the
v12 failures below remain unchanged; later candidates do not relabel v13 as a pass.

Source `ac49fd2` uses table protocol v12 / source decisions v3. The same meaning
response now lists all source choices before its meaning details and explicit
source reviews. Exact quotes, complete source accounting, frozen records and
revision checks remain required. A literal empty source cannot choose
`has_meaning`; a separate quotation from it is still rejected by the compiler.
Whitespace, blank-cell evidence and public v1 contracts are unchanged. Older table
checkpoints cannot resume under the new internal contract.

**The full v12 meaning call failed.** On the saved PDF observation and accepted
structure, it selected all eleven ordinary nonempty headers/values as additional
meaning and invented an `OK` quotation from the empty source. The compiler rejected
it with `quote_not_in_source`, preserving both records, zero, exact decimal strings,
blank note, schema and bindings. The one-call / 360-second development run took
325.05 inference seconds (4,444 input / 2,297 output tokens), 338.86 host seconds,
and peaked at 6,832,664,576 cgroup bytes. Merely ordering choices before details is
not a demonstrated fix.

Separate source-selection diagnostics then used both real frozen-table contexts,
with the original product payload preserved apart from the system/output contract.
All 12 ordinary PDF sources were correctly negative; in the merged-header HTML,
only the unit/condition caption among 11 sources was selected. The respective
inference times were 145.71 / 94.98 seconds, with 3,343/636 and 2,195/485 input/output
tokens. Each had a one-call / 240-second / 1,536-output-token cap. Runs overlapped on
the same Spark, so they are not a performance benchmark. Earlier short mixed
controls matched 19/21 and then 21/21 after distinguishing literal transcription
from extracted header units. These small development controls are not holdouts.

An offline replay of the negative selection into the existing compiler produced
zero meanings and 12 explicit source reviews, no issues, and unchanged data/schema/
bindings, without another model call. **Those diagnostics did not qualify the durable
selection substep**, and the positive case did not validate detailed meaning kinds,
quotes or unit/condition scopes under that workflow. Its design must retain
correctable decisions, existing finite call/time budgets, checkpoint identity,
partial results and frozen structure; it must not exclude headers/values by rule.

The source passed 2,321 local tests (227 skipped, 12 subtests), including 304 related
tests with warnings as errors, and Ruff/format for 208 files. The same 304 related
tests passed on ARM. The first ARM attempt failed before inference because the
recognition-only Python lacked core `pypdf`; a fresh isolated run mounted only the
verified core `pypdf` package and metadata read-only for its test child. No installed
core, recognition pack or dependency was changed; that preparation failure remains
recorded separately and consumed no model call.

All runs used 4 CPUs / 16 GiB per container without swap/OOM, GPU or external
network. The original PDF remains at 853.49/900 seconds and six calls; the earlier
HTML trial at 900.02/900 seconds and two calls; the scan at 886.39/900 seconds and
four calls. None was reset or resumed beyond its cap. There was no new OCR,
rendering or complete document run. Owned servers/containers and transfer/activation
duplicates were cleaned up; failure evidence and one exact shared source snapshot
remain. These results do not qualify whole-path ARM resources, independent
quality, installation or release. Existing product packs, Toolkit, Sync and clients
remain unchanged.

### Current pixel-review controls and display coverage

Source `d6765b1` adds PDF review v11 / unit display v2. A mixed-control inspection
found residual rule pixels grouped with lettering by overlapping source rectangles,
yet not displayed because they fell outside the unchanged edge-candidate band.
The display now includes every non-boundary group sharing an original pixel component
with a measured rule. This changes visibility, not edge eligibility or blank evidence.
A displayed stripe cannot acquire `rule_edge` permission merely from that display.
Older review/display checkpoints are incompatible; application v4 and public v1 remain.

On the exact saved scan, four more groups expose 32,830 previously undisplayed pixels.
All original 257,034 pixels, partitions, source candidates and observations are
unchanged. The prepared input has 11 masks, two images / 360,540 bytes / 7,915,034
pixels, and 7,073 message characters under the existing limits. It has not been sent
through a v11 full model review or applied as a product result.

The clean source passed 2,308 local tests (227 skipped, 12 subtests), 278 tests with
actual ARM recognition dependencies (no skips), and Ruff/format for 208 files.
The ARM test and saved-input display preparation took 4.72 host seconds and peaked
at 174,927,872 cgroup bytes with 4 CPUs / 16 GiB and no swap/OOM, GPU or network.
No OCR, source rendering or model call was used for that implementation check.

Separate development controls included text, rules, mixed content and a one-pixel
fragment. Both non-thinking and a paired 256-token reasoning limit matched only
4 of 7 inspected controls, under the same 512-token total output cap. Reasoning
fixed one mixed answer but lost another; it is not adopted as the production fix.
Their inference times were 138.36 / 157.67 seconds. The original scan budget remains
886.39/900 seconds and four calls; all four authorized follow-up/diagnostic calls
are recorded separately. Full review quality, table application/meaning and final
independent qualification remain open. Do not promote the earlier all-rule diagnostic
or these regression checks into a complete extraction result.

### Latest ARM64 installation and model follow-up

At source `7fc7306`, all five core CI jobs passed. The exact Linux ARM64 portable
archive (236,268,955 bytes, SHA-256
`d743617ec25ce4b898d68724cc7b87784727889062f89ab3608621f2d4dc932a`)
was downloaded, fully hash checked and cryptographically verified against the
specific GitHub-hosted build workflow and source commit. The outer CI ZIP was not
fully downloaded or independently hash checked. Assembly attestation is not upstream
binary reproducibility or final release qualification.

A separate Spark installation preserved all 7,791 files and permissions; 98 product
source files matched the commit. Bundled Python and an empty PATH passed native
CLI/MCP processing, including Korean text and `001.2300`. HTTP checks passed for
authentication, path/URL/endpoint rejection, idempotency/conflict, explicit missing
authentication and connection failure, terminal-result persistence across restart,
resume without increasing the one-attempt budget, and deletion. No live model was
used by these HTTP checks, and unavailable inference was not counted as success.
Host execution took 15.69 seconds; whole-cgroup peak was 169,869,312 bytes under
4 CPUs / 16 GiB with no swap/OOM, GPU or external network. This does not qualify
mid-inference cancellation, the final service image or installed user clients.

The user authorized further bounded model work, preferably on Spark. The previously
prepared scan review made one separate development call under a 360-second cap.
It returned in 211.70 seconds but classified all seven displayed rule-edge masks as
source text. Program status `reviewed` was rejected as development quality evidence;
the response was not applied. A second, 240-second diagnostic used the same images,
omitted overlapping source-string associations and asked only pixel-role questions.
It returned all seven as table rules in 167.71 seconds. Multiple input factors changed
together, so this does not isolate one cause, validate text/mixed/ambiguous controls,
or establish the full review contract. Source-text, grid and semantic checks remain
separate obligations; diagnostic labels do not overwrite the failed product response.

The original scan stays at 886.39/900 seconds and four calls. Both authorized extra
calls are separate development evidence, not original-budget or holdout success.
No new OCR, source rendering or product application occurred. Owned servers and
containers were stopped. Existing product packs, Toolkit, Sync and clients remain
unchanged. Mixed-role controls, full review accuracy, actual table application and
meaning extraction precede final independent qualification and consumer transition.

### Redistribution preparation

The release gate now requires a review tied to every exact non-metadata candidate
artifact, with no unresolved redistribution issues. Sources, recipes and notices
required by that review must be delivered even when classified as metadata. This
prevents omission; it does not automatically determine license obligations or
approve the unfinished candidate.

Pack provenance v2 and recognition audit/verification v2 distinguish original
downloads from locally built preparation artifacts. Both are hash checked; derived
inputs and shipped recipe/build-record references are bound without inventing an
upstream URL for the built output. Legacy v1 evidence remains distinct. Required
model/OCR bytes and ARM Torch's official CPU origin are not replaceable by derived
artifacts. These are packaging checks, not independent build, license or quality
approval. Linux stage preflight also rejects case-fold-colliding destinations;
explicitly omitted unused terminal data retains its original member evidence.
Recognition audit/verification v3 additionally distinguishes authored packaging
records and scoped license collections, with separate file/evidence hashes and
artifact associations. It is restricted to non-executable documentation and
cannot substitute runtime or model origins. The outer pack format stays v1;
authorship, redistribution approval and actual execution remain separate checks.

At clean source `7d22c07`, a fresh Linux ARM64 assembly from 469 pinned inputs
passed the complete v3 stage audit and produced a private preparation pack. Its
29,439 files include 149 authored records/collections tied to 181 original or
derived artifacts; 135 scoped collections preserve 504 exact embedded-text
references, including legacy-encoded upstream license bytes. The first audit's
14 Windows-only pip/setuptools launchers were explicitly omitted by assembly v2,
with two matching RECORD rewrites. All other original file bytes/modes and all
463 native files were retained. Foreign-binary checks were not relaxed.

The same 2,216,829,968-byte archive, SHA-256
`e2076f65d0c060fd11334a1d1d8d6a4ea1a1518872820007ff5bf5fb45190b36`,
was installed and activated in a separate private store, selected through the
normal recognition profile, and used by the actual recognition worker. All
29,439 installed files were reverified afterward. During the one-page scan test,
20 ms process-map sampling observed 202 pack-owned ELF files and eight external
glibc files from the existing system allowlist. This does not establish coverage
of every transient mapping. Pack-owned libstdc++, libgcc_s, zlib and FriBidi loaded
with normal non-executable shared-library file permissions.

Installation/profile/worker execution passed, but document accuracy did not.
The single recognition call finished in 12.98 seconds under its fixed 180-second
limit. OCR omitted `Item`, `Length` and `8.25`, and read `Unit:` as `Unit;`.
The header row was absent and one data position remained unobserved. Importing
the saved result through the product correctly retained partial coverage and did
not declare the unobserved position blank. Worker status `complete` is conversion
completion, not complete document extraction. This new installation calibration
is a development case, not an independent holdout; its original result and
one-call installation budget remain unchanged.

A subsequent component check matched the exact original canvas and three OCR
crop pixel hashes. All missing text was visible in the original table input.
The existing closed-grid helper prepared six unchanged-glyph cell inputs, selected
before OCR. Six bounded OCR calls took 0.65 seconds and read five cells correctly:
`Length`, both sample labels, `12.50` and `8.25`. `Item` was misread as `[607`.
This was not a second full recognition run, and the caption punctuation was not
reprocessed. Neither this component nor the whole document passes quality.

The same saved capture exposed a repair-selection defect: the table crop had an
observed upright orientation, but a later caption had no orientation observation.
The old repair guard used the last crop's state for every table. Adapter v27 now
requires the table's unique containing original OCR crop, observed upright OSD
and matching input-pixel geometry. Unknown, rotated, conflicting or unbound crop
evidence stays unresolved. The source selection is recorded and required for
repair reuse; old adapter checkpoints cannot resume silently. The repair option
remains opt-in and existing packs are unchanged. Changed contracts passed 106
warnings-as-errors tests with the actual ARM recognition dependencies and selected
the correct saved table crop without another OCR call. A broader run retained two
core-PDF dependency failures (443 tests passed) in that recognition-only test
environment; it is not a full core-plus-recognition qualification.

The isolated installation/run used 4 CPUs, 16 GiB, no swap/OOM, GPU or network;
whole-cgroup peak was 4,171,268,096 bytes. This is recognition-pack execution, not
the final recognition-plus-inference resource qualification or a Python-free core
installation. Redistribution approval, corresponding-source publication and the
remaining final-candidate checks are still unfinished. Existing installed product
packs, Toolkit, Sync and the personal 1.7.0 CLI were not changed.

The product model builder has now produced a fresh preparation pack with a v2
vision conversion receipt. Its quantized weights and F16 image projector match
the earlier development bytes, but the new archive binds the five specific CPU
runtime manifests. Four Unix runtime archives were downloaded and fully hashed;
Windows has review metadata only, with binary delivery and redistribution review
still pending. Declared compatibility is not five-platform execution approval.

The new model and ARM64 runtime were installed in a separate Spark store. The
managed CPU-only server started in 8.02 seconds and reported vision support.
Installation, startup and page-input preparation peaked at 16,751,300,608 bytes
under a 16 GiB cgroup with 4 CPUs, no swap/OOM, GPU or network. No inference was
performed; this does not qualify the full recognition-plus-inference path.
The saved scan's page review stopped before inference with
`visual_slot_inventory_incomplete`: one missing cell had no source-bound slot
observations. At adapter v27 those observations were coupled to optional table
OCR repair. Adapter v28 now collects missing table geometry from the existing
full-page image cache after the optional repair step. It does not invoke OCR,
render a cache miss, rewrite text or assign header roles. Existing measurements,
including partial/unavailable ones, are retained without another attempt, under
the same cumulative pixel/cell limits. Changed and existing repair contracts
passed 117 warnings-as-errors tests with actual ARM recognition dependencies.
This is not a new whole-document quality result. The established visual verification
checks supplied text and empty slots; additional image reading is described below.
Applying and reviewing recovered text and a conflicting table structure remain
required; relaxing the guard or repeating the old review request is not a solution.

A bounded component capture now measured six slots (3 rows, 2 columns) from
the actual cached renderer image, with no new OCR or model call. The independent
full-page renderer has different pixels and remains separately identified. The
first import exposed a decimal/binary32 dimension mismatch; adapter v29 permits
only the same binary32 representation within 0.001 canvas pixel, retaining other
coordinate checks. Revalidating the saved measurement verified its original-page
coordinates without rerendering or rewriting observations. The 3-row grid remains
unlinked to the original 2-row table; this is not repaired text or a complete
recognition/interpretation execution. Both failed probe records are retained.

PDF review v9 now has a reachable, budgeted image-reading stage after an unresolved
review or a missing-slot inventory failure. It obtains literal text/empty/uncertain
candidates from measured cells and existing text regions without providing OCR strings
as answers. Partial results preserve those candidates with source-pixel identities;
they do not rewrite original nodes/tables, clear issues or become final values. Unstarted
reads can resume within explicit remaining budget, while completed/failed/interrupted
attempts cannot be silently repeated. A separate budgeted review now checks each proposed
literal string and rectangular grid against the image before selecting alternate regions.
Raw nodes, bindings, tables and region records remain available; roles and units are not
inferred by the projection. Failed/unknown reviews preserve the original view. Original
processing issues and new blank-cell application remain conservative partial paths;
independent quality and completed extraction are not established by this implementation.

A bounded ARM64 development read on clean source `66feddb` used the previously saved
full-page image plus a new lossless detail, without OCR, recognition or rendering.
One managed greedy CPU call returned eight candidates in 159.78 seconds (3,205 input,
285 output tokens). All six grid-cell strings and the caption matched the prior expected
text, recovering `Item`, `Length`, `8.25` and `Unit:`; the title region had one additional
line break. That difference is retained, not normalized into an exact-match claim.
The whole cgroup peaked at 7,153,172,480 bytes under 4 CPUs / 16 GiB with no swap/OOM,
GPU or external network. Saved inputs and existing read-only packs were reused, so this
is not full recognition-plus-inference resource qualification or an independent holdout.
Original observations and the two-row recognized table stayed unchanged; applying a
reviewed three-row alternative and completing semantic extraction remain unfinished.

The following single v5 review on clean source `6ded2f9` returned in 206.36 seconds
(4,163 input / 501 output tokens) and accepted the proposed 3x2 table. Root review
**did not approve that result**: all seven original-page grid bands remained unresolved,
the model labeled the border-containing unit as source text, and it missed the known
title line-break difference. V6 now blocks unmeasured alternate grids before inference
and when validating answers; the saved v5 decision is not migrated or replayed. The
underlying broken/ambiguous stripe measurement and exact text quality remain unfinished.

This scan has used two actual model calls and 481.34 of its initial 900 seconds,
including prior preparation/installation execution. No additional OCR, rendering or
recognition was performed for this review. Its whole cgroup peaked at 7,204,683,776
bytes with 4 CPUs / 16 GiB and no swap/OOM/GPU/external network. This is reused-input
component evidence, not an independent holdout or whole-pipeline resource qualification.
The source-preserving projection and budgeted review were exercised on ARM; they do not
establish completed extraction. Old observations, responses and the rejected applied
view remain as development evidence, not a final active document.

A subsequent saved-pixel diagnosis found multiple near-white parallel stripes around
the visible lines; the input PDF uses DCTDecode. The all-non-white measurement treated
these as ambiguous stripes. Pixel/grid v2 add a separate contrast-core measurement,
retaining all original pixels. The same saved source now yields all seven line cores
under the unchanged 8-pixel search / 7-pixel maximum-width limits. Of 257,034 original
foreground pixels, 37,597 belong to the measured cores and 219,437 remain unassigned;
none are discarded or automatically called noise/blank. Pure faint lines still use the
original path. V7 separates those exact core pixels from text candidates.

This fixes the grid-location failure, not the remaining image-content review or semantic
extraction. The unassigned table component still contains 61,318 pixels, including
57,238 low-contrast pixels and 4,080 core pixels; it must not be silently treated as
background or correct text. The saved reading and v5 quality failure remain unchanged.
No additional model, OCR or render call was used for this diagnosis and implementation.

On clean source `f743899`, the same saved-pixel calculation on ARM produced exactly
this plan fingerprint and retained the original observations. The actual dependency
suite passed 198 tests with no skips; the local full suite passed 2,228 with 227 skipped
and 12 subtests. The diagnostic took 0.86 seconds, with 3.67 seconds for host preparation
and tests; the cgroup peak was 110,407,680 bytes with no swap/OOM. This is a no-model
component check, not recognition-plus-inference qualification. Including this host
execution, that checkpoint had used 485.00/900 seconds and two model calls;
about 415.00 seconds remained before the subsequent review below.

The subsequent v8 review separated residual components and used a compact ordered
response. It still mislabeled all seven edge-candidate units (51,407 pixels) as source
text and missed the known title newline mismatch. The actual output was 99 tokens
rather than the previous 501, but prompt processing remained 158.54 seconds; the
call took 174.22 seconds. Product acceptance/application is rejected development
quality evidence, not a verified document. Original observations and issues remain.

The preceding source `454f518` (review v9) blocked these plans with
`visual_rule_context_not_displayed` before inference and during response validation.
Bounding rectangles do not expose a unit's exact pixel membership. Source-bound
membership display was not implemented at that stage; no caller flag bypassed the guard.
The failure is retained on resume; old checkpoints are rejected. Existing plans
without edge candidates keep their review path. That no-model guard check preceded
the v10 display work described below.

The local full suite passed 2,250 tests with 227 skips and 12 subtests; current actual
ARM dependency checks passed 220 with no skips. The original 257,034 pixels and
source strings are unchanged. V8's host execution used 181.90 seconds and its cgroup
peak was 7,177,662,464 bytes without swap/OOM; the v9 no-model guard check took another
3.81 host seconds. At that stage the development scan had used 670.72/900 seconds
and three model calls, leaving about 229.28 seconds. This reused recognition/reading evidence,
so it is neither whole-pipeline resource qualification nor independent quality approval.
Owned containers/servers and verified temporary transport copies were cleaned up;
original packs and clients are unchanged. The follow-up below implements exact
unit membership display without expanding the two-image/byte/pixel or document budgets.

### PDF review v10: exact pixel display and initial timeout

Source `c283538` adds PDF review v10, unit display v1 and review application v3.
The original full-page PNG is unchanged. The second image contains separate original
RGB detail and exact membership-mask panels; black mask pixels identify membership,
not source color or text/border/background classification. Labels are generated and
kept separate from source content. Each panel has its own source coordinates,
RGB/membership hashes and actual PNG evidence. Missing or altered display evidence
is rejected during inference preparation, response validation, resume and application;
a caller flag cannot substitute for it. Old checkpoints are not migrated.

The original 257,034 foreground pixels, observations, tables and issues are preserved.
All seven required edge masks (51,407 pixels) were checked byte-for-byte on Mac and
ARM. Panel RGB and whole composite RGB match across hosts; compressed PNG bytes
differ and retain their actual hashes. Both hosts use Pillow 12.3.0. No cause for the
PNG byte difference is asserted. The actual two images total 345,200 bytes and
7,157,464 pixels, within the unchanged 16 MiB / 16 million pixel limits, without
resampling or dropping membership pixels.

The local full suite passed 2,279 tests with 227 skips and 12 subtests; actual ARM
recognition dependencies passed 249 with no skips. Ruff and format checks passed
for 207 files. These checks cover actual transmitted image bytes, tamper rejection,
resume without rendering/inference and application provenance, not model accuracy.

One development review reused saved PNG, recognition and literal reading evidence
on Spark under a 210-second inference cap. It timed out without returning a model
response. Input preflight counted 5,267 tokens; the last server prompt-processing
record was at 196.72 seconds. This does not establish exact final input progress or
completion token usage. No response validation or reviewed observation was created.
No OCR, recognition, source rendering or pack installation was repeated. The earlier
v8 misclassification and title newline mismatch remain failed development evidence.

Host execution took 215.67 seconds. Whole-cgroup peak was 7,202,758,656 bytes under
4 CPU / 16 GiB, with no swap, OOM, GPU or external networking. Owned server and
container cleanup and unchanged original pack/activation files were verified. This
component replay is not whole-pipeline resource or independent quality qualification.
The scan's cumulative record is now 886.39/900 seconds and four model calls, leaving
13.61 seconds. It will not be automatically retried or given a larger original budget.

The prepared one-call, 360-second development follow-up was later authorized and
executed; the latest section above records its quality failure and the subsequent
input-isolation diagnostic. The original budget and failed evidence are unchanged.
The other PDF's one-call meaning check is also authorized but not yet executed;
its frozen source/input must be verified before a separate bounded dispatch.
Formal release and consumer migration remain open.

### Reviewed replacement tables and blank-cell application

Source `7fc7306` adds visual application v4 without changing review v10 or its model
input. Two missing connections are fixed: an alternative table's source slots are
verified through its exact projection association, and a fully reviewed replacement
can resolve the inactive recognizer table's gap dependency. Every candidate cell's
position is checked against the original measured slot, not just a rehashed rectangle.

Only blanks that pass the existing detail, grid and pixel review become new nodes
and bindings in the alternative table and active semantic region. Original tables,
strings, bindings, OCR captures and issue records remain available unchanged. A
replacement resolution links both table IDs, the original gap count, projection,
plan and decision fingerprints. Only its matching ledger dependency is removed.
Unresolved raw OCR, unverified extents, other tables and semantic/execution errors
remain; inconsistent evidence aborts application atomically. Old application
checkpoints are incompatible, not automatically upgraded to the new outcome.

The local full suite passed 2,306 tests with 227 skips and 12 subtests; Ruff/format
checks passed for 208 files. Spark passed 276 related tests with no skips. Both hosts
also verified candidate-cell geometry in the saved actual scan input without changing
it. These were regression tests and read-only input inspection: no actual model,
OCR, source rendering or document application was performed. The earlier response
timeout, title mismatch, development budgets and pending approvals are unchanged.
There is still no independently approved extraction or final release qualification.
The owned ARM test container and verified source-transport copies were removed;
original installed packs and consumers were not changed.

### Source-first table meaning review

Table protocol v10 asks for one decision per owned source before any meanings.
Only `has_meaning` permits meanings; exact quotes can span multiple owned sources,
and a source can express multiple independent meanings. A joint meaning appears
once under its earliest quoted source. Reference context cannot substitute for
direct evidence. The compiler rejects duplicate IDs and identical meaning copies.
Explicit `unreviewed` stays pending even for fully quoted or empty sources, and
repairs/checkpoint histories cannot return reviewed sources to that state.

The compact model-facing view retains original source text, context,
compiled names/types, header references and canonical provenance.
Reference-wire v2 uses separate source/table handles only when they reduce the
actual request. The full dictionary stays in checkpoint identity rather than being
repeated in model input. The engine restores original references before
checking exact quotes, revisions and compiled results. Checkpoints bind the dictionary
and its activation, and old protocol checkpoints are rejected rather than migrated.

The v10 offline reconstruction of the saved PDF development case is 14,957
message-content characters against the unchanged 16,000-character limit. A synthetic
all-unreviewed repair is 19,976 characters and would not fit; this is a size stress
diagnostic, not a model response or a promise that every repair can run.
Every initial and repair request is checked at its actual size; overflow retains the
accepted structure and does not dispatch a model call or enlarge the document budget.

The preceding v9 development continuation made one actual meaning call from the saved
source-bound structure, with no recognition or page-review rerun. Input fit at 4,469
prompt tokens, but the response repeated the same quantity-unit interpretation and
header quotations until the 3,072-output-token ceiling (330.54 seconds, finish reason
`length`). The engine rejected the incomplete response and preserved every previously
accepted value, source and structure. The cumulative budget was 6 calls / 853.49 seconds
of the unchanged 12 / 900 limit. This remains partial, not meaning-quality success or
independent qualification. The owned server exited; installed packs stayed unchanged.
The source-first v10 implementation has regression coverage, but no actual model
quality result yet. Only 46.51 seconds remain in this case's recorded budget; no
new inference, recognition or page review was run for the v10 implementation.

### Optional source-bound PDF page review

The managed client now has a separate, explicit vision-projector path. It preserves
bounded inline PNG/JPEG bytes, checks actual server multimodal capability and counts
image tokens using the same frozen request used for inference. Default text-only
packs and installed client configurations are unchanged. With that explicit pack,
the engine now reviews PDF pages before semantic region planning. It can apply
reviewed blank slots and reading order only after all page decisions, exact pixel
membership, source references and processing dependencies pass. Other issues remain.
This is processing coverage, not independent OCR accuracy or release qualification.

A development F16 projector was converted once from the already verified official
Qwen3.5-9B snapshot with the matching pinned llama.cpp converter: 6.52 seconds,
918,165,888 bytes, 334 tensors. Inspection identified `qwen3vl_merger`; an initially
incorrect inspector constant was corrected without another conversion. The original
weights and converter remained unchanged. A separate private development model pack
reuses the exact Q4_K_M text weights. Neither projector conversion nor pack assembly
establishes image inference quality, whole-pipeline memory fit or release approval.

The first actual CPU image request returned normally in 110.28 seconds (one call,
768-token output limit). A transient PDF helper reproduced the previous page-2 RGB
digest exactly and supplied the full page plus a lossless cell-detail crop, with no
OCR rerun. The checked image-aware input count matched the actual 4,158 prompt tokens;
the response used 191 output tokens and the owned server exited. Original inputs,
observations and activation state were unchanged. This was a development feasibility
run on the Mac, partly concurrent with regression tests, not a 16 GiB benchmark.
The model called the detail border-only, but incorrectly ordered the table after the
lower condition notes. No empty value, reading order or page completion was accepted.
The subsequent page-review v1 request reused those same PNG/OCR inputs (no new
render or OCR). One CPU call finished in 123.74 seconds with 5,075 prompt and 260
output tokens. It accounted for 84,206 foreground pixels across 20 units, reviewed
the missing Note slot as empty, and returned the correct title/unit/table/condition
order. The fixed validator accepted this page decision; source text, original
observations and activation were unchanged, and the server exited. The old OCR
canvas is not treated as identical to this render. This single development page
does not establish all-page application, semantic extraction or holdout quality.

The engine shares page and semantic calls/time, checkpoints each attempt before
inference, and resumes reviewed pages without another call. Unknown, failed and
interrupted calls are not automatically retried. PNG bytes stay out of checkpoints.
Page-review v2 excludes measured elapsed time and the separate legacy whole-page
projection from page identity while retaining source/observation evidence. Older
v1 plans and checkpoints are rejected; there is no automatic decision migration.
An owned whole-document development continuation reused page 2 after explicitly
checking source, image, payload and decision equivalence. Its first new page-1 call
returned in 119.71 seconds but was rejected: an unconstrained empty-slot branch
allowed a dummy item, and a 38-pixel detached glyph stroke had no source candidate.
No all-page changes or semantic calls occurred. Version 3 constrains array counts
and supplies a unique overlapping exact-text native line's observed bounds, without
padding or changing the original source. Rebuilding from saved PNGs preserves all
74,892 page-1 and 84,206 page-2 foreground pixels; this is not a new model approval.
V1/v2 checkpoints are not automatically migrated to v3.
The changed page-1 request was then accepted in 104.66 seconds. With the explicitly
revalidated saved page-2 decision, the product applied both page reviews and retained
the six original issues as resolution evidence. Each table has 12 observed cells;
all prior text, locations and bindings are unchanged. The first table compiled two
rows, including `12.50`, `7.25`, zero and the empty Note value. The run remains partial:
the next meaning request was 19,014 characters against the 16,000-character model
input limit, so no meaning call occurred. This continuation used three new calls in
262.07 seconds; prior work/failure remains charged (five calls, 522.37 seconds at the
checkpoint). Saved-stage reuse is not a final candidate or independent quality run.
The optional projector builder now has an explicit flag and conversion receipt v2;
its actual final-pack and multi-platform qualification remain pending.

### Linux ARM64 addition

An additional 31 notice files (310,088 bytes) are prepared separately for audited
Python/PBS components and referenced Debian common-license texts. Their original
wheel/runtime/source archive or signed-package identities are recorded. These
overlays have not changed the installed stage. Per-file license applicability,
corresponding-source delivery, mixed model and Qt/FFmpeg obligations, and the formal
ARM stage audit remain release requirements.

Linux ARM64 (`linux-aarch64`) is now a fifth release target; the four existing targets
remain required. Core/PBS, CPU runtime and native recognition builders have native
ARM CI jobs and target checks. Linux ELF machine, loader and actual startup host must
agree. Both Linux architectures retain the Bookworm ABI baseline. The new CPU runtime
candidate defaults to `b10853-cpu.4`; installed packs and model bindings are unchanged.

Images select `linux-x86_64` or `linux-aarch64` explicitly. The portable core, patched
HWP binary, Docker base/final image and exported config must share that target.
Qualification v3 requires separate x64 and ARM64 model, HTTP and container checks;
ARM observations cannot replace x64 evidence. Actual Docker OS/architecture, image
and container IDs, CPU quota and cgroup evidence are bound to each run. Container
identity v2 rejects older receipts that lack these observations.
The release gate also parses the delivered image export and verifies its actual
configuration ID, architecture and ordered layer hashes, rather than trusting the
build receipt alone.

Both designated DGX Spark hosts are reachable through Tailscale SSH and reported
ARM64, Ubuntu 24.04 and cgroup v2. A fresh check on September 10 confirmed Docker
access for the existing account on both hosts; no authentication request remains for
that access. No permissions, GPU, swap, network or security settings were changed.
Spark-A now has the exact pinned ARM Bookworm image cached. Image acquisition is not
container execution or full-product qualification. At source 25e6cd8, ARM core, CPU
runtime and native recognition CI builds passed; the native delivery archive was
later recovered and statically verified without rewriting prior failed transfers.
Complete recognition stages, CPU-only four-core/16 GiB execution, installation/rollback
and client use remain unqualified.

The f68fe0f ARM portable core was later acquired as a bounded selected archive member
and matched the CI-attested portable SHA256. Its shipped Python and real launcher
successfully ran capability inspection, document inspection and structure extraction
on the approved two-page mixed PDF in the isolated Spark-A environment. The scanned
page correctly remained partial without recognition. Additional native collection
attempts failed in the test harness (an incorrect optional import, then a 2 MiB raw
result storage cap), not with an established product defect. Native result review is
therefore still incomplete. Both failures were preserved, with no swap/OOM or owned
process/container remaining. These old-source component checks are not installation,
current full-path or 16 GiB model qualification.

An explicit ARM torchvision derivative was prepared from the pinned CPU wheel in a
new output location. It restores five codec loader references and omits the now
unreferenced bundled loader; 190 other members, package metadata and the original
license remain byte-identical. The new build tag is `1dfarmloader1`. Static RECORD
and byte checks passed. A subsequent isolated Spark-A run using the selected PBS
passed native PNG/JPEG/WebP checks and linked all five codec guard references to the
single system loader. The component cgroup peaked at 1,281,576,960 bytes with no
swap/OOM; exit 0 and owned-container removal were confirmed. This is not full OCR/model
16 GiB qualification or redistribution approval. Original inputs and installed
environments remain unchanged.

The Linux input preparation helper checks an approved inventory and requires explicit
byte, disk and time limits. Its default is offline inspection; acquisition and network
access require separate flags. It neither installs inputs nor approves a stage. The
legacy ARM inventory fails readiness checks. The normalized v2 inventory instead
selects explicit input components and preserves unresolved full-stage requirements
separately. Its 117 files and 873,482,385 bytes pass metadata and local-original checks;
models remain local-copy-only. One bounded acquisition then verified all 117 files
(873,482,385 bytes) in 540.16 seconds; none were installed or executed. Native inputs,
dynamic-library closure and actual stage assembly/qualification remain unresolved.

### Current source follow-up: table meaning, visual observations and Linux compatibility

Prompt v23 / planner v14 / table protocol v9 / compiler v15 require an explicit
role for every observed row not fixed by a native header declaration. OCR header
flags remain predictions; omitted roles never become data records. Scope v7 uses
the compiled header classification when constructing header-group candidates.
Missing source cells remain missing, and invalid decimal text remains uncertain
with its original spelling and source range instead of being read as a number.

Non-record subtotal, note and unmapped cells now have a separate scalar region.
Its value bindings do not overlap the parent record readings, it shares the document
budget, and it resumes independently after a pause. Duplicate removal requires the
same successful binding and value representation; a failed numeric reading does not
erase a valid source-text field. Scripted tests cover subtotal/note values, mixed
rows, sparse cells, partial bindings and pause/resume. Unseen forms remain unqualified.

Two bounded development probes stopped after structure, deliberately before meaning:

| Development input | Actual structure inference | Checked result |
|---|---|---|
| Merged-header HTML | 1 call / 80.2 seconds | Exact two rows, long IDs and decimal spelling |
| First table of the existing scan | 1 call / 97.6 seconds, plus one exact paragraph replay | Header excluded from records, Bolt/Nut cells correctly placed, unobserved Note uncertain |

Saved structure responses were also recompiled after the final defensive changes,
with identical values and value-source evidence. Both whole results remain partial
because the probes stopped before meaning. They are not fresh OCR, full-document,
independent-holdout, final-candidate or 16 GiB qualification.

Table meaning now selects literal source quotes. The program validates exact Unicode
ranges and hashes; context-only text cannot become direct evidence. Explicit source
reviews cover text outside the quotes, including text already read as values or
headers. Missing review is not inferred from successful value extraction.

A repair may change kind, description, scope or status, split or merge meanings, or
withdraw a mistaken interpretation. It must identify the previous revision, explain
changed IDs and retain review of their original text. Revision hashes bind content,
source inventory and transition history. Structure and source values cannot change;
issue-count reduction is not a quality test. Invalid repairs and exhausted budgets
preserve the prior accepted result. Scalar-form contracts remain unchanged.

Table protocol v8 separates at most two initial meaning attempts from one review
of an accepted meaning. Structure still permits at most two attempts. All calls
share the unchanged document call/time budget; a failed or unchanged review does
not trigger another attempt without an explicit grant. Repair feedback now includes
the exact remaining source ranges, their text and hashes, not only source IDs.
The program does not decide that a title has no further meaning or repair a wrong
quote occurrence automatically. This scheduling change has scripted regression
coverage; the fresh model run described below did not reach post-acceptance review.

Two full-path development runs used 2 calls each (148.2 and 146.5 seconds) within a
preselected 5-call / 900-second limit. Both reported complete but failed separate
content review: the unit was classified as a note and applied to the entire record,
and both meanings quoted the whole caption. The second run correctly limited the
condition to Length. Both preserved exact rows, precision and column/value sources.
Record-definition provenance also included data cells too broadly. No independent
holdout or final-candidate quality claim follows from these runs. The first run used
the pre-hardening source snapshot; exact execution source hashes are retained.

A source-first output-order experiment then used 2 calls / 229.8 seconds and also
reported complete, but regressed in content review: it combined the caption into a
record-wide note and misclassified four numeric values as units. That experiment was
reverted to prompt v21 / table protocol v6. Two human-segmented clause
probes, followed by a four-call sampling comparison, did not produce a profile that
correctly handled both the unit and condition. These component diagnostics do not
test automatic source selection or qualify a production profile.

A subsequent two-call comparison changed only thinking mode, retaining greedy sampling
and the same requests. Thinking was active, but both calls exhausted their 3,072-token
total output limit without final JSON (520.5 seconds combined). This is a budgeted
completion failure, not evidence about the correctness of an answer that was never
returned. Further prompt/sampling variants stopped while runtime/model compatibility
and explicit reasoning/final-output budgeting were examined.

The follow-up metadata audit found no mixed runtime, GGUF, template or tokenizer
files; it did not establish numerical conversion equivalence. A separate four-call
comparison then used the runtime's finite reasoning limit of 1,024 tokens per block,
retaining the same Q4 model, greedy sampling, requests and 3,072-token total ceiling.
Both limited-thinking calls returned the correct kind, content and column scopes;
non-thinking reproduced its previous errors. Total time was 226.4 seconds. This is
a pass for two human-segmented development clauses only, not automatic source
selection, a full product run or approval to change the production default.

The managed client now accepts an explicit administrator reasoning limit, records it
in execution/checkpoint identity and uses the same mode for template checks and
inference. Omission retains the non-thinking default. At clean source 1da72ad, a fresh
full-path development run with a 1,024-token reasoning limit used 3 calls / 588.8 seconds.
The final result preserved the two rows, identifiers, decimal spellings and exact value
and header provenance. The unit was mm on Length and Width only; the re-inspection
condition applied to Length only. Both direct quotes selected the correct clauses.

The first meaning response had invalid quote-occurrence indices and was rejected.
The corrected response left the caption's title range unreviewed, so the existing
stage limit ended the run partial. The title text itself was preserved as the repeat
label, but that does not establish semantic review of its source range. No additional
call or budget increase followed. This is a material development improvement, not
complete extraction or independent/final-candidate qualification. Repeat-level data
cell references were reviewed as relevant evidence of the same table's rows/structure;
their presence alone is not an unrelated-source failure. Column-name and value
provenance were checked separately.

At clean source 4f07271, one new full-path run stopped at the same 900-second budget
after two calls (900.94 seconds including cleanup). Structure was accepted after
797.99 seconds with exact rows, precision and header/value sources; meaning timed
out in the remaining 101.92 seconds. Thus neither full meaning quality nor the new
post-acceptance review was verified. The first request differed only in table protocol
version and again had 2,088 input tokens. Measured prompt processing was 12.05 tok/s
and generation 2.61 tok/s, substantially slower than the previous run. A single host
snapshot recorded swap use, but does not establish the cause. No automatic rerun or
budget increase followed; the server was closed and the partial result preserved.

Recognition adapter v12 added measured framework render/crop/rotation and cell-input
links to the serialized single-page PDF and original page. A fresh two-page OCR run
took 11.6 seconds and preserved all 12 input-coordinate links, including eight repair
crops. Both actual page and OCR rotations were zero. The original 12 issues remained
unchanged, including unobserved cells and repair-budget exhaustion; the result stayed
partial. This verifies input-coordinate capture, not OCR text or reported output boxes.

Adapter v13 additionally observes the full rendered RGB independently of OCR boxes.
It records non-white pixels, low-contrast pixels, bounded components and omitted
areas with source/page/render hashes. Non-white is not automatically text or a
graphic, and exact white is not a proven blank value. A render-only comparison of the
same two pages preserved their pixels and coordinate evidence and recorded 168/169
components; it used no OCR or semantic inference. Visual-content correspondence,
reading order and issue-specific completion remain unfinished. Original issues stay
active; visual observations alone cannot qualify recognition-backed PDF completion.

Adapter v14 adds bounded, two-way overlap candidates between those components and
source-bound native characters/raw OCR. Existing exact-text links connect structure
elements without trusting their reported boxes as content evidence. Reprocessing the
saved two-page observations took 0.032 seconds with no new render, OCR or model call.
Seven second-page components had no overlap candidate, and 11 of its 15 structure
elements had only partial text-range support. Original content, issues and model
payloads were unchanged. These are diagnostic candidates, not verified visual
assignments, blank values or a completion decision.

Adapter v16 connects repeated tokens only when a verified horizontal OCR line has a
unique, complete and exactly matching token sequence. It rejects table overlap,
cross-line joins, normalized text, ambiguous targets and incomplete duplicate lines.
Reimporting saved raw observations connected two repeated Korean tokens without new
OCR or inference. Their source text, coordinates and raw detections were unchanged;
the second page still has four unobserved cells and remains partial. Inspection of
the render confirmed that three of those cells contain text missed after the eight
repair calls were spent earlier in the table. Repair scheduling, blank-value evidence,
reading order and issue-specific completion remain unfinished.

Adapter v17 saves every planned cell before the first repair call, with immutable
indices/fingerprints and separate execution states. Budget stops, errors and
cancellation no longer remove later cells from that plan; no-ink observations are
not blank-value proof. This preserves snapshots, not a new guarantee that a forcibly
killed worker can write a final disk checkpoint.

A predeclared four-process comparison processed six images in 0.39 seconds
(124,716 input pixels). Two original Korean/English cell crops gave identical text,
geometry and confidence individually and in forward/reverse Tesseract file lists,
apart from the recorded input page number. Raw TSV and image-to-cell mapping were
preserved. Adapter v18 now implements an explicit list of at most two independent
cell images from one table/page/language/PSM. The default remains eight single-image
calls. Only an explicit batch profile selects 16 images within eight processes and
60 seconds; crop and total OCR-input pixel budgets are checked separately.

Raw OCR v2 stores the original full TSV once in `rawOCRRuns`; each capture retains
its input number, original row ordinal and cell/frame binding. Input number two is
not PDF page two. Partial output, timeout and a failed input cannot complete or
reuse the batch. The full unit plan and no-ink versus unobserved distinction remain.
Verified reuse still carries the existing conservative coverage warning. An error
later in the same table may leave earlier observations in raw evidence without
applying them to that attempt's structure input.

At clean source 25e6cd8, one fresh offline development recognition run processed the
same two-page PDF in 31.59 seconds (32.63 seconds including preparation), using six
repair processes and 11 images. The final `3 / 7.25 / OK`, Korean headers and item text
were returned, with the original TSV/input coordinates preserved. The result remains
partial: both empty Note locations are unobserved, a vertical line was additionally
read as `|`, and Korean spacing differs from the source. A subsequent pixel-only
inspection found a white cell interior but also faint edge pixels omitted by the
current OCR crop. This does not establish blank values or complete content. The outer
status wrapper failed after the recognition output was saved; its failure, uncaptured
Python exit code and successful OCR child exits are recorded separately. No retry,
semantic model, independent holdout or resource qualification was performed.

Adapter v19 now records the full cell, its interior, the OCR cell window and excluded
edge bands from existing canvas pixels. Exact white, faint/color and unknown-alpha
pixels stay distinct. Frame hashing, grid detection and repeated region inspection
share a separate finite observation budget; uncertain work after an error consumes
the remaining reservation without fabricating a measured-pixel count. A missing cache
does not trigger a render. Import checks internal statistics and source/frame linkage,
then requires unique unmerged table geometry before associating slots. Raw OCR links
are preserved separately and their execution remains unverified. These records do not
create values, prove blanks or clear existing issues/partial status. This change has
synthetic regression coverage, not a fresh whole-document recognition result.

Windows build preparation now takes pinned official product terms and records the
actual installed toolchain instead of searching recursively for a similarly named
license file. An installed-HTTP operational review links separate expected results,
raw observations, actual execution and image evidence. A fresh offline image builder
checks selected source/core/dependency bytes and exports an identified image. These
are implemented preparation paths, not executed Windows/image/HTTP qualification
or approval of redistribution rights. Host-published HTTP access needs its own check.

Linux builders now use Ubuntu 22.04 with explicit GCC 12, inspect actual ELF version
requirements and run relocated startup checks in an identified Debian bookworm image.
At commit b1a6cc9, the CPU binaries required GLIBC 2.34 / GLIBCXX 3.4.30 / CXXABI 1.3.13;
native Tesseract required GLIBC 2.35 with no dynamic GLIBCXX/CXXABI requirement. Both
passed actual bookworm startup. This resolves that build's earlier libc mismatch,
not final slim-image, OCR/inference, 16 GiB or minimum-kernel qualification.

The same CI exposed a Windows ZIP test-fixture problem and a native source-download
hash mismatch. Raw ZIP header tests and source acquisition diagnostics were corrected
without relaxing path or hash checks. Rust toolchain identity is now read in its pinned
source directory. At c02b7a6, core and CPU CI passed on all four targets; Linux and
Windows native preparation passed. Candidate attestation was skipped. At 1da72ad,
native preparation and the Linux-only delivery step also passed. The downloaded ZIP
matched the API SHA, and its files, sources, notices and recorded startup image were
checked against that exact source. Full recognition-stage and redistribution review
remain pending. Core CI subsequently passed all four platforms at 1da72ad. New 4f07271
CI passed pack contracts and three OS regression suites, but Windows regression
failed because Linux ZIP execute bits were checked through Windows-extracted file
permissions. The builder now checks the safe archive's recorded mode; actual image
execution checks remain unchanged. Direct regressions pass without adding skips,
and the follow-up 52c4d12 CI regression steps passed on all four existing platforms,
including Windows. Build completion is separate. These development runs are not one
final candidate.

After adapter v25, the stage file-handle fix and optional image preparation/transport,
core regression passed
**1,842 tests, with 213 skipped and 12 subtests**; Ruff and formatting checks passed.
The separate recognition-runtime overlay passed **424 PDF/OCR tests**. Core skips include optional recognition dependencies and actual
Windows process checks; they do not waive target-environment verification. No final
candidate has been fixed or published, and Toolkit/Sync consumption is unchanged.

A fresh adapter v19 development run returned in 7.88 seconds of recognition
(8.39 seconds including supervision), with child exit 0 and no known remaining
processes. Its 109 nodes, 357 bindings, seven issues and coverage matched the earlier
v18 result exactly. Twelve native-page cell slots were linked, but the scan-page
observation remained unavailable: hashing its full canvas twice exceeded the unchanged
16-million-pixel observation budget. Adapter v20 removes that duplicate hash within
one operation while independently checking externally supplied frames. Pixel evidence
does not resolve the remaining blank/line/content-completeness questions.

The fresh v20 full-path check then captured and uniquely linked all 24 slots in
7.73 seconds of recognition (8.18 seconds including supervision). Total cell observation
work was 14,985,921 pixels, below the unchanged limit. Nodes, bindings, tables, regions,
issues and coverage matched v19; the scan's 994 faint edge pixels remained recorded,
not erased or promoted to a blank value. This is development evidence, not holdout
quality or a 16 GiB resource qualification.

A clean-source adapter v21 development run completed one whole-path observation in
7.14 seconds (7.65 seconds including supervision), with exit 0 and confirmed known
process cleanup. Values, bindings, tables and all seven issues matched v20. The native
Note cell was not proved blank because embedded font programs and character mappings
were outside inventory v1. The scan retained its faint pixels. Adapter v22 adds narrow
source/native font-byte and glyph-outline checks before native blank decisions. Its
fresh run completed in 6.85 seconds but retained all seven issues: font bytes matched,
while eight PDFium text projections included trailing spaces absent from source Tj.
Adapter v23 separates literal source characters from explicitly verified generated
spacing without trimming real text. In a fresh clean-source v23 whole-path run, the
native Note cell was proved empty in 6.94 seconds of observation (7.47 seconds with
supervision). All 109 prior nodes, 357 binding definitions and their label/region
relationships were preserved; one blank node/binding was added. Per-run binding keys
shifted after insertion. Only the matching missing-cell issue was resolved, with its
original record retained. The scan remained unresolved and all six remaining issues
were preserved. Overall status is still partial. This is not
whole-page completeness or independent quality approval.

Adapter v24 then returned one clean-source whole-path observation in 8.19 seconds
(8.91 seconds including supervision), with exit 0 and confirmed known-process cleanup.
A 2,752-pixel window linked the unassigned native-page pipe detection to one original
vertical stroke: continuous identical profiles, gray edge pixels and white side
background were preserved. All values, tables, bindings, six issues and partial
coverage remained unchanged. Per-run timing and capture identities changed their
associated fingerprints. The native blank remained proved and the scan remained unresolved.
This is supporting evidence only: the original detection is not yet reclassified.

The 117 acquired ARM inputs were independently rehashed and inspected without installation.
Selected ELF files matched AArch64, but OpenCV's GUI dependencies, an empty Qt interpreter
entry and several bundled-library notices required follow-up; the explicit torchvision
derivative and its successful component probe are described above.
A pinned-source ANTLR 4.9.3 pure Python wheel was built once offline in a fresh isolated
environment; its 56 source files were preserved byte-for-byte. These are input-preparation
results, not complete recognition-stage, redistribution or target-execution approval.
The stage verifier now rejects bundled ELF loader names `ld-linux*.so`, including the
actual hash-renamed torchvision member. This is not detection of every arbitrarily
renamed loader. Header policy v2 allows the original ARM QtCore's one-byte empty
interpreter metadata only with its exact hash, SONAME, ELF type/entry/non-PIE checks
and an explicit shared-library role. Unspecified and executable roles still reject
it, and declared executables cannot be relabelled as libraries. The receipt records
this decision separately from execution qualification. Neither original file was
silently removed or replaced; the existing ABI limits remain unchanged.

The ARM native delivery archive was finally recovered with its exact API SHA and
statically reviewed against source 25e6cd8. Its 78 delivery files, 15 candidate files,
source notices, ARM ELF/loader and recorded Bookworm startup matched. Original failed
transfers remain failed records. No local native execution, fresh container inspection,
full recognition-stage approval or redistribution approval followed this review.

Signed Bookworm metadata was linked to 61 downloaded Debian packages, all matching
size and SHA. Static inspection retained their native file inventories and notice
paths without installing them. An isolated Spark-A base-image inventory found 32
matching package versions, 27 missing package names and two older versions. A separate
exact two-file QtCore/PCRE2 loading probe returned Qt 5.15.19, with the original unusual
interpreter metadata unchanged; required mappings and the unique system loader matched
observed hashes. Both probes exited cleanly and removed their owned containers. These
checks do not approve full OpenCV execution, portable-stage placement, corresponding
sources or redistribution terms.

The first full ARM stage attempt installed all 103 pinned distributions offline but
stopped at two legitimate shared console-script owners; inspection also found that
isolated Python had ignored the environment-only no-bytecode setting. Its failed
receipt and partial files remain preserved. After explicit `-B` and source/RECORD-bound
shared-owner validation, a fresh-source, fresh-output attempt completed in 56.19 seconds
(38.96 seconds assembly, 11.08 seconds relocated probe). The relocated stage contains
32,023 files and 2,201,045,715 bytes, unchanged before and after 17 headless imports,
one CPU PNG operation and Tesseract version/language checks. All 103 distributions,
178 actual mapped files, the unique system loader and the loaded selected OS files
were checked. Six original auxiliary scripts and the manpage remain preserved;
auxiliary CLI execution is not claimed.
The enforced container had four CPUs, 16 GiB memory, no extra swap, no network or GPU;
its cgroup peak was 5,098,418,176 bytes, with zero swap/OOM events. Exit 0 and owned
container cleanup were confirmed. Model weights were not loaded and no document/OCR
job was run. Native inputs still came from the earlier pinned development build:
this is not same-candidate full-pipeline, redistribution or release qualification.

### Earlier release-preparation history

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

Prompt v18 / planner v12 / table protocol v4 now separate one record-structure
decision from meaning over its frozen compiled values. Label/value forms use the
existing scalar path. Stage attempts and usage are checkpointed; each stage permits
at most two calls within the unchanged total budget. Cancellation, invalid meaning
or budget exhaustion preserves a compiled structure, not a complete extraction.
Row-role decisions contain only row and role; provenance is attached from actual
observed geometry, with missing cells left missing. Rows whose observed cells are
all explicitly declared headers are fixed by the program; mixed or unknown header
flags still require interpretation. Meaning scopes exclusively select columns, the
record, bounded row/column intersections, or unresolved applicability. That earlier repair preserved even mistaken meanings. Table protocol v6 instead
allows source-reviewed corrections while preserving frozen values; accepting a wire
response is not independent semantic approval.
Compiler v13 leaves unbounded parent/child meaning scopes unresolved instead of
silently broadening column assertions; explicitly bounded row intersections remain.

A fresh two-stage merged-header run used 2 calls / 113 seconds but incorrectly froze
an extra header record. After structural checks, a bounded 5-call / 258-second run
preserved exactly two source records and precision. It still failed semantic review:
the unit scope was unresolved after an invalid broad selection, and caption accounting
was incomplete. The previous single-stage development pass does not qualify this new
protocol. With the then-current protocol, a fresh 3-call / 277-second run compiled exactly
two rows on the first attempt, but merged caption unit/condition statements into a
record-wide condition and incorrectly called data-row summaries units. The repair
repeated that response; the result remains partial and fails content review. A saved-
recognition whole-scan attempt on protocol v2 used its 12-call cap / 761 seconds and
failed sparse-row mapping. Do not increase failed budgets or treat syntax/structure
acceptance as quality approval. An earlier-protocol first-scan-table probe (one exact
paragraph replay, two real calls / 222 seconds, planned stop after that table) kept
sparse data cells in their actual positions and the missing note uncertain, but included
the header as an extra record and produced a false unit/data summary. Its two stages
were mechanically complete; content review still failed. Neither whole-document nor
fresh-recognition qualification was attempted. Subtotal/note scalar preservation still
needs work.

Recognition adapter v10 retains original raw OCR detections and a bidirectional
processing ledger, separate native-text support, and a source-bound full-visible-page
render fingerprint. The actual applied rotation is distinct from an unknown orientation
observation. Render coverage, OCR truth and semantic completeness are different claims.
Missing cells, conflicts and unclassified visual content remain unresolved; no original
issue has been removed. Full visual-content and reading-order accounting is unfinished.
The current blanket recognition-completeness issue still blocks every recognition-backed
PDF from the complete-only release gate. This remains a release blocker.

At that earlier checkpoint, core regression was **744 passed, 10 skipped, 12 subtests
passed** and the separate recognition-runtime overlay passed 60 PDF/OCR tests; exact final
recognition bundles remain unqualified. Eleven input/specification cases covering nine
formats are prepared but have not undergone final model evaluation.

Evaluation v2 retains independently prepared per-case review specifications outside
model input. A separate review receipt binds the immutable inference report and
results. Release qualification v4 binds clean source, actual assets and installed
results, and uses linked cgroup measurements rather than summed process RSS.
Fresh-output builds and exact-byte promotion are implemented. Large packs use
explicit split/join transport without changing their reconstructed ZIP identity.
These tools do not supply missing platform, client or model-quality evidence.

Actual CI passed the Linux 16 GiB / four-CPU, swap-free, network-isolated preflight
and all four pinned CPU runtime builds. The preflight ran only a small synthetic
allocation, not OCR or inference. All four platforms, including Intel Mac, passed
portable core builds and Python-free CLI/MCP smoke on intermediate commit 526f25d;
this is not final-candidate or installed-client qualification. Linux native recognition
dependencies built and passed relocated startup/language discovery. Windows native
recognition then failed an optional SW package-manager dependency; the builder now
disables it explicitly, with target rerun still required.

Notice review found that the earlier Windows CPU candidate and recognition build
selected an extension's VS2015 preview EULA, not the installed VS2022 product terms.
Those candidates are ineligible for release. Both builders now reject that mismatch;
a matching identity screen still does not approve redistribution. Exact installed-edition
terms, static-runtime rights and final bundled notices remain independently reviewable
release blockers. Linux notices now include explicit IJG and Berkeley acknowledgments.
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
| Scanned table correctness | Recognition, literal image reading and source-bound pixel display have separate development evidence. The latest full review returned but mislabeled seven rule-edge masks as text; a mask-only diagnostic classified them correctly, without validating the full product contract. Application v4 links reviewed replacement tables and blanks, but actual model accuracy, header/record/meaning checks and independent scans remain open. |
| Semantic scope quality | The two-step table protocol is implemented. A development result preserved two rows, unit and condition scope, but source review remained partial. Source-first meaning review v10 awaits its bounded actual-model check. The extra call is authorized but not yet executed; preserve output/context failures. No independent holdout has passed. |
| Long documents | Use affected long/continued-table cases to check complete repeat ranges, heading/note scope and source-view accounting; keep indivisible-context limits explicit. |
| 16 GB / GPU-free operation | Two ARM64 Sparks are reachable through Tailscale and existing Docker. ARM recognition and review components ran under 4 CPU / 16 GiB without swap/OOM, but both Linux x64 and ARM64 still require same-candidate whole recognition-plus-inference qualification. Follow the [bounded CPU procedure](deployment/CPU_QUALIFICATION.md); component peaks do not substitute for it. |
| Delivery | Install/update/rollback and actual client processing still need Windows, Linux, Intel Mac, installed Codex/Claude and ChatGPT-host evidence. |

Source `7fc7306` is present in the verified ARM64 core bundle, but it is not a final
qualified core/runtime/model/recognition/image set. Any further product change needs
a new explicitly versioned/configured candidate and affected delivery checks. Static container-policy tests are not an actual
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

Adapter v25 consumes only an exact original OCR source backed by freshly checked
pixels and a closed native table-cell boundary. A consumer-only replay of saved v24
development observations removed one source from data candidates and retained its
text, raw TSV, node, binding and table context. Two affected issues were retained
with their resolutions; page 2 and global partial status were unchanged. This replay
performed no OCR, native parsing, rendering or model calls and does not replace a
whole-product or independent quality run.

The f68fe0f Windows regression failed in 49 Linux-stage synthetic tests. The follow-up
uses consistent reopened-handle metadata for mutation checks and adapts only the
synthetic POSIX mode/pipe boundary on Windows. All 86 related tests passed locally. The actual bb87d11 Windows
regression step also passed; its portable build was still running at inspection. Target support and production guards were not relaxed.


A separate Linux ARM64 development worker then processed the public two-page PDF
with pinned f68fe0f product source and the assembled recognition runtime. It used
Heron, TableFormer accurate and Tesseract CLI on CPU, returning both pages in 13.67
seconds (32.23 seconds including preparation and supervision). Under 4CPU/16GiB,
whole-cgroup peak was 3,579,691,008 bytes with zero swap/OOM. All runtime files were
copied into this cgroup before execution, and original/copy/packet hashes and owned
process/container cleanup passed. Worker `complete` is not quality approval: each
table still lacked one observed Note cell and returned no column-header flags. The
core PDF importer/native checks and semantic model were not run. This is not a
same-candidate full-pipeline resource, independent quality or release qualification.
