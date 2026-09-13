# Extraction readiness and priorities

Current capability, confirmed defects and the next acceptance checks. Last verified:
**2026-09-14**. Implementation and checkpoint behavior belong in
[the extraction engine](docs/extraction-engine.md); raw comparisons remain in private
qualification evidence. This is not a chronological work log. Version **1.8.0 is not
formally released**, and the primary-format structural KPI is not yet approved.

## Current scope

1. **HWP/HWPX and Excel XLSX:** consistent document structure and understanding
   across varied layouts and forms, with complete hierarchy, fields/records, exact
   values, relationships and source links. This is the product KPI.
2. **Runtime:** native parsing and internal AI on DGX Spark, then personal Mac use.
   Recognition runs on CPU when needed; internal model inference uses CUDA.
3. **Later formats:** Word and PPTX after the primary formats; Google Docs/Sheets/Slides
   when those are complete and time permits. Existing adapters stay available.
4. **Deferred:** broad PDF/scan qualification; Windows, Intel Mac and Linux x64
   qualification; five-platform
   distribution; CPU-only 16 GiB qualification; publication and downstream application
   upgrades. Existing builders and interfaces remain available for explicit use.

Company-specific back-office implementation and cloud-model quality certification
remain outside this completion target. Reconstruction illustrates how much structure
the result should retain; implementing a writer is not a separate feature target or
completion gate. Binary `.xls` and `.doc` are not currently supported native formats.
Installed consumers and packs are unchanged.

## What is implemented and what is checked

| Area | Current state |
|---|---|
| Native formats | TXT, Markdown, HTML, DOCX, HWP/HWPX, XLSX and PPTX parsing and common structure extraction are implemented. Broad independent AI quality is not established. |
| Optional native capture | HWPX/XLSX package parts can be retained in `reconstructionContext`; binary HWP capture and a result-to-file writer are unavailable. Capture is not evidence of logical structure or understanding, and a writer is not a current KPI gate. |
| PDF and scans | CPU recognition, source-bound pixel review, additional literal reading and reviewed alternative tables are implemented. Development examples ran through this path; arbitrary scans are not qualified. |
| Tables | Structure/content/applicability are separated. The compiler reads all mapped rows and preserves precision and source bindings. Citation-driven text-row loss is fixed in compiler v27; prompt v31 keeps continuation available for equal-valued records. Independent model quality remains unapproved. |
| Jobs and APIs | Python/CLI/MCP and authenticated HTTP use the same engine. Cancellation, saved results and explicit-budget resume have regression coverage. An installed Spark HTTP service has not been qualified. |
| Spark | Actual bounded GPU extraction runs exist with the installed ARM64 recognition, CUDA runtime and vision packs. These are development runs, not independent quality approval or CPU memory qualification. |
| Mac | Existing native core and CPU pack preparation are retained. Personal 1.8.0 end-to-end use still needs a separate check. |
| Distribution | Pack builders, integrity/license checks and manual CI are retained. There is no qualified public 1.8.0 release; no consumer migration is claimed. |

The full local check including the native outline path passed **3,423 tests,
with 227 skips and 12 subtests** on the current Mac's Python 3.13.15. Skips are not
passes, and this count is not a measure of model quality or qualification of the
pinned Python 3.12 deployment runtime.

### Standalone package checks

A fresh macOS ARM64 environment installed the declared, lock-pinned dependencies
without an editable checkout or agent plugin. The generated remote Skill read a
document using only its bundled package source and those dependencies. After installing
the wheel, public byte-stream analysis, result serialization, structured reading and
MCP startup/tool discovery/document reading passed from an unrelated working directory.
The source remained unchanged; stream calls did not create a retained result database.

AI extraction without a configured model returned `ai_unavailable`. A supplied test
client exercised the in-process model interface and exact source-bound values, but
was not a real-model quality check. Source and wheel contents were checked for private
files and application-specific linkage. Host-source bundles now include their declared
launchers, and each generated Skill points to its own runtime. These checks do not
qualify a platform installer, a live agent session or a formal release.

## Current extraction behavior

The current internal contracts are document protocol v15, native-structure v20,
structural wire v3, native-value-batches v4, structure-revision v10, scope-selection wire v3, prompt v40,
region plan v23, table protocol v34 and compiler v36. Public v1 result
and API contracts are unchanged. Old incompatible checkpoints cannot resume.
Scope integration v18 / scope protocol v10 / scope inventory v1 retain discovery of the explicit parent table
of routed nonrecord content even when the regions are not adjacent in processing order.
The next-row guide uses effective compiled column definitions, not rejected citations
from the original model response. Whole-record provenance remains stored, not replayed
as column context. The bounded comparison below verifies the later-row delivery
change; actual parent-table applicability still needs a completed comparison.

The current planner additionally repacks two adjacent unstarted views without
changing source ownership or replaying attempted work. Meaning requests no longer
expose unused value-binding candidates that neither response stage can address;
all source text, actual value/definition usage and compiler accounting remain.
Initial content and accepted-meaning repair receive their respective instructions.
These latest changes have regression/offline evidence, **not a new actual-model pass**.

Whole XLSX cells supply exact native strings, numeric spellings, empty content
and formula expressions to both table structure and meaning requests, rather than
normalized address/value display text. Quotes keep their actual source path and offsets; original observations,
value bindings and formula caches remain unchanged. Existing display-only bounded views
retain their `/text` path. Source inventory v2 has deterministic regression coverage;
it does not establish that the model interprets these sources correctly.

Joined tables preserve zero-record fragment origins on their accepted relations.
The fragment's original evidence no longer labels a nonempty combined array as blank.
An entirely zero-record result keeps all contributing sources and any uncertainty;
explicit blank cells and unrelated repeats are unchanged. This provenance correction
has deterministic regression coverage, not a new model-quality approval.

Table selection wire v4 requests only one status string per owned source. Canonical
selection record v2 explicitly records that per-source explanations were not requested;
the program does not fabricate reasons. Detail quotes, meanings, remainder reviews and
the overall correction reason remain required. Source inventories, values and fixed
document/output budgets are unchanged. Actual Spark results are described below.

Native observation adapter v4 preserves the public cell's `columnSpan` when
building internal geometry. XLSX extractor v10 retains explicitly stored empty XML
cells, distinguishing them from implicit gaps, empty strings, formulas without caches
and covered merge positions. These source fixes have direct native-file regressions;
they do not fix the long-table request/understanding failures below.

Binary HWP note controls now connect each note paragraph to its stored body owner,
keeping footnotes/endnotes, section-local IDs and number sources distinct. The original
14,848-byte development fixture retains 18 nodes and 28 bindings and now has all four
expected body/note links; it previously had none. This native graph check does not
establish final AI understanding, reading order or inline character positions.

- **Document roles and hierarchy:** source text, native markers, direct formatting
  and XML references remain separate from inferred title/heading/caption roles.
  Ambiguous roles and unjoined long-document fragments remain partial. Font size or
  alignment alone does not determine a logical role.
- **Native prose and forms:** logical item rows no longer require a physical table.
  Structure defines attributes, types, ordered occurrences, missing states and exact
  meanings before value choices. Value reading cannot silently rename or remove them.
- **Values and provenance:** code reads original bindings or exact quoted substrings.
  Frozen numeric fields can also select program-issued numeral locations with original
  surrounding text; the code resolves them through the same quote/compiler checks.
  Impossible type/state/row choices are excluded, but an offered choice is not proof
  that it belongs to the right attribute. Two equal values require their own correct
  source positions. Blank, absent, unreadable and uncertain remain distinct.
- **Bounded requests:** repeated source metadata is shared losslessly, including
  formatting and native references. Oversized value work can be divided into checked
  batches without discarding context or replaying completed batches. Real cross-region
  logical continuation is still missing.
- **Table request context:** table source wire v1 retains every source ID/text and
  shares only repeated metadata, cells and relations in structure and meaning
  requests. Unstarted table slices are re-sized against the nearest preceding column mapping and its original definition
  sources; previously accepted rows are not replayed. Native header flags and value
  ownership are unchanged. The original 50-row development files initially plan as
  11 HWPX / 5 XLSX regions instead of 53 / 14. Larger meaning inventories can still
  exceed the budget, and actual whole-document quality remains unapproved.
- **Explicit review:** failed value reading may trigger one bounded correctness review
  of the structure on the same source. It is not source-change detection. Full prior
  decisions are displayed through lossless property-name sharing, while canonical
  checkpoints keep the original objects. Only fully checked replacements are committed;
  current values are reread and retired responses/costs remain. `retain` is not approval.
- **Table preservation:** header roles are not inferred from a definition citation or
  numeric spelling. Data, subtotal/note rows, precise values and original cell bindings
  survive the reproduced compiler cases. Equal-valued new rows may continue; duplicate
  folding needs a separate contextual decision and compiled-row check.

Regression coverage verifies these mechanics, not the model's decisions. No relevance
filter, fixed business template, automatic deletion of legitimate fields or silent
budget increase is used to manufacture a successful result.

## Implemented preservation and review mechanisms

The current source and test counts are listed with the Spark verification below.
These mechanisms have direct regression coverage, not independent model-quality approval.

Joint role/structure revision is implemented for native HWP/HWPX regions. The
current value-first controller attempts actual value selection before requesting a
bounded review of a role/value conflict. A structural source is not automatically
an invalid value source. A reviewer can preserve a real title
and later select an exact inner value, revise roles with the full structure, or remove
an invented field. It cannot silently change source metadata or drop real content.

Role changes require every owned role and complete old/new role and structure accounting.
A checked replacement rebuilds hierarchy and invalidates dependent values, accounting
and applicability; the original role response and prior reads/costs remain. Rejected,
interrupted and retained decisions preserve their preceding partial result. A role edit
that would stale another already-saved region's role context is explicitly rejected;
cross-region role-history replacement is not yet implemented.

The public-path regressions cover role-only replacement, legitimate title/inner-value
retention, exact source/ledger checks and checkpoint replay. Role-stage note guidance
is now separate from field/value instructions. A referring paragraph may genuinely
be a heading; native note membership never forces its role.

Compiler v36 records verified empty note controls, explicit containers and stored-number
nodes as native structural use. It does not automatically cover paragraphs, other
empty nodes, nonempty controls or unlinked/conflicting objects. Explicit uncertainty
wins; required value bindings still need reading or exclusion. Stored note attachments
need not be reinvented as fields or additional meanings, but real attributes and
additional applicability remain subject to interpretation and review.

Revision evidence reuses the replacement's exact `SourceQuote` schema without changing
allowed text, IDs or occurrence rules. Saved requests from the preceding actual run
now occupy **15,469 / 15,808 characters**, compared with **15,777 / 16,119** before;
a small fixed repair example also fits. This preserves all source/history content,
but larger proposals or feedback can still exceed the unchanged 16,000-character limit.
These saved-request checks are not new model decisions.

Text quote, logical-row and meaning anchors now offer only nonempty owned source
views, separately from typed value sources, shared definitions and dispositions.
Empty native note controls and stored number bindings remain in the document; no
text is invented for them. Revision evidence may cite an actual empty owned block
by its ID, but cannot quote nonexistent text. Native note/body ownership constraints,
exact source checking and complete old/new replacement accounting remain strict.
See [the implementation](docs/extraction-engine.md#text-anchors-are-not-typed-value-sources).

Other directly tested preservation mechanisms include:

- Distinct native footnotes/endnotes, their stored numbers and body owners, including
  multi-paragraph notes and the distinction between native objects and added meanings.
- All mapped table rows, exact cell values and zero-record fragment provenance;
  explicit blank cells remain different from implicit gaps and uncached formulas.
- Lossless request factoring and bounded value/accounting requests. Accepted source,
  structure and values survive rejected revisions, cancellation and explicit resume.
- Complete applicability inventories, row-bounded partitioning and compiler-owned
  provenance. The 96/200-row synthetic cases fit 4/8 requests below 16,000 characters
  and retain 192/400 values plus 194/402 source references through JSON replay.
  Those are scripted delivery/compiler checks, not actual-model interpretation.

Raw checks remain in `structural-kpi-20260913/native-note-role-stage-59/`,
`native-role-model-59/` and `scope-partitions-52/` and the preceding source-specific evidence folders. Historical
counts and failed model responses are preserved there and in version history; they
are not accumulated here as a substitute for current readiness.

## Latest Spark verification

Current implementation source is **b5833dea985bb2adea34c8221359c0a6381f0e28**.
Table protocol v34 separates known coordinates from semantic choices: columns are
keyed by physical index, and row roles follow a program-supplied order. Canonical
records, source evidence, native value-first review and compiler v36 are unchanged.
Local checks passed **3,423 tests / 227 skips / 12 subtests**; the related checks
passed **212 tests with warnings treated as errors**. The same source passed
**738 related tests on Spark-A Python 3.12.3**. These are not model-quality results.

### Same-source HWP backend comparison

Both backends used **c075ab0b47c9ef3da18305e8caba7a65729b60a3**, the same binary HWP
fixture and unchanged **12-call / 900-second / 16,000-character** allowance, with
3,072 output tokens and an 8,192-token context. Expected answers stayed local.

| Backend | Actual result | Confirmed remaining defect |
|---|---|---|
| Existing Motif-3-314B-Q4_K_M service | 12 calls / 567.4 seconds, partial; two exact footnote values accepted | One actual role/value conflict produced a checked replacement; another review retained the contradiction. Added note applicability was unresolved or self-targeted. |
| Existing Qwen3.5-9B-Q4_K_M runtime | 6 calls / 111.4 seconds, partial; no accepted values | Structures made artificial paragraph/note records, first with inconsistent row states and then with missing-state evidence from other occurrences. No value stage was reached. |

Both preserve all 18 original nodes, 28 bindings, four native note objects, stored
numbers and body attachments. Neither passes whole extraction. The 9B path did
less accepted work, so its shorter duration is not equivalent-work speedup. Model
weights and runtime both differ; this is not a weights-only causal comparison.
No actual-model duplicate-property repair was exercised.

The frozen HWP specification requires exact text, native control roles and source-linked
attachments. It does not require a particular logical `paragraph` enum or six invented
scalar fields. Additional reviewer assumptions are separate; the original expectations
and historical failed reviews are unchanged. Correct native preservation does not
approve incomplete declared values or unsupported added relationships.

The 9B components were hash-checked before use and its model/runtime files again after
execution. Its owned authenticated server and temporary source were removed after
34 evidence files were collected. Shared services and original components are unchanged.
No OOM counter increased. Separate model-container and engine limits are **not** a
whole-job 16 GiB, GPU-memory or swap-free qualification. Detailed resource evidence
remains with the corresponding run rather than implying comparable memory regimes.

### XLSX input-holdout failure

The previously unrun credit-register input used that same **c075ab0** source and the
existing 314B service. It returned **partial after 2 calls / 125.8 seconds**, with no
accepted records. Both structure responses marked the leaf-header row as data; an
offline replay reproduced the first rejected read exactly: **C2 `Credit` requested
as a number**. The repair changed identifiers but repeated the rejected mapping.
The visible column choices also cited the group header instead of the Credit leaf
and selected numeric source reads for formulas. Meaning and applicability were not
reached, so these later requirements are not reported as separately tested failures.

Native IDs, original decimal spelling and both uncached formulas survived. D4 is an
implicit XML gap with an explicit blank-not-zero note, not a stored empty cell; no
zero or formula cache was invented. This is necessary source preservation, not
semantic approval. The failure guided the current source-localized diagnostic, so
the input is now development-only. Its frozen specification and original responses
are unchanged; this was input holdout coverage, not an external blinded review.

That run passed 441 related ARM tests. All 19 evidence files were hash-checked before
its owned engine copy and transfer archives were removed. Sampled available-memory
minima were 13.86 GiB on A / 26.05 GiB on B; OOM counts did not increase. Shared model
settings were unchanged, and full 314B shard hashes remain unverified. No peak-memory,
swap-free or deployment qualification is implied.

Evidence: `structural-kpi-20260913/native-value-first-61/` (314B and criterion audit),
`native-backend-62/` (9B comparison), `independent-xlsx-63/` (original failed holdout
and membership change), `table-read-feedback-64/` (source-local feedback), and
`table-read-contract-65/` (formula contract and repair-context audit),
`table-source-feedback-66/` (grouped diagnostics and native-source discrepancy), and
`table-native-view-67/` (native source delivery), and `table-coordinate-wire-68/`
(current source, coordinate decisions, actual responses, repair-design audit and cleanup).

### Coordinate duplication fixed; header interpretation still fails

The current source returned **partial after 2 calls / 128.3 seconds**, with no
accepted data. Both replies now choose each of the five physical columns once and
supply exactly one role for every offered row. The native strings, exact decimals,
formula expressions and source paths are unchanged. This verifies the coordinate
response on the actual model path, not correct whole-document interpretation.

Both replies still classify the leaf-header row as data and request C2 `Credit`
as a number. The second request includes its exact source/type/coordinate diagnostic,
but the repair retains that role and adds data-cell references as column definitions.
The first also cites the Volume group rather than the Credit leaf. Compilation
stops at the typed read; grouped compiler findings, meanings and applicability are
not reached. The two automatic structural attempts end within the original
12-call / 900-second allowance; it was not exhausted or enlarged.

All source remains in one region. Actual requests are **15,329 / 15,580 characters**.
A draft with a separate schema property for every row made requests larger and split
this source and a scope regression fixture. The adopted role-string array instead
follows explicit `rowRoleOrder`, preserving actual coordinates without repeated
per-row schema metadata. The failed draft checks remain private evidence.

Nineteen evidence files were hash-checked; the owned engine returned and 3,814 temporary
files plus transfer archives were removed. Shared services and retained components
are unchanged. Sampled minima were 13.78 GiB on A / 26.12 GiB on B, with no OOM count
increase. These are not peak-memory, swap-free or deployment qualifications.

**Next: correct an inaccurate row hint before another repair redesign.**
`dataRows` is calculated from cells not declared as headers, not model-confirmed data.
For this XLSX it spans rows 0–6, including the leaf headers and notes. Its wording
can imply a role the program has not established; the causal model effect is unproven.
The general semantic prompt also calls for a repeat over all `dataRows`. Remove or
rename that implication, preserving the exact positions already carried by
`rowRoleOrder` and row candidates. Check native/predicted/mixed headers, spans, gaps,
scalar forms and request sizing; do not alter native flags or force header roles.

A complete prior-response repair remains deferred. Adding the actual first proposal
requires **16,770 characters**. A hypothetical program-owned-ID/range contract needs
15,967 even before its new instructions. It is not a lossless rewrite of the old
model-chosen IDs, an implemented contract or a demonstrated repair. No source or
semantic choice may be discarded and no budget raised merely to fit it.

### Outstanding 50-row HWPX / XLSX baseline

The most recent actual full-path run of these two development files used source
**e3452fba507a1f5dd1ddb1d87549d59c2fdf1295**, the 9B CUDA runtime and the frozen
**12-call / 900-second / 16,000-character** limit:

| File | Returned result | Still missing |
|---|---|---|
| HWPX | Partial, 7 calls / 147.2 seconds; 12 of 50 rows | Remaining rows, correct title/caption/unit structure and applicability. A detail request was too large and was not sent. |
| XLSX | Partial, 12 calls / 207.5 seconds; 45 of 50 rows | Remaining rows, nonrecord content and applicability; the call budget was exhausted. |

All returned values, row order and original cell bindings match the prior expectations.
Equal-valued rows 11/12 keep distinct cells; XLSX row 37 retains its stored blank.
These are checks of returned rows, **not whole-document passes**. Subsequent request,
source and scope corrections have regression coverage but have not rerun these files.
Using the 64-call / 3,600-second long-document allowance on these already-failed cases
still requires the pending budget decision. No automatic increase or identical failed
rerun is planned. The files do not establish three-page qualification.
Evidence: `structural-kpi-20260913/native-mapping-scope-45/`; original frozen inputs
and expectations: `native-long-files-33/`.

## Earlier evidence that still limits the claim

The documents below have influenced development and are **not independent holdouts**.
Raw requests, responses, prior expectations, source identities and root reviews remain
in their existing qualification directories rather than being rewritten as successes.

| Comparison | Confirmed result and limitation |
|---|---|
| Plain XLSX record table | Two ordered rows, exact cell values, header/value sources and no extra scalar matched prior expectations after the candidate fix. This single development case does not establish diverse-form quality. |
| HWPX native record tables | Exact rows, precision and cell/header links matched, but title/caption hierarchy and extra fields failed whole-result review. |
| Native prose, 9B and existing 314B | Representation, type/state, record grouping and value-source failures remain across both models. Model size or more reasoning alone did not fix them. |
| Whole-context native v9 | All eight paragraphs reached every request; actual value batches ran, but both failed. The review was too large to call. Full context is not understanding. |
| Relation-only equal-text contexts | Continue, duplicate, separate and unresolved matched four deliberately different contexts. This is not general whole-document duplicate/continuation approval. |
| Earlier merged-header HTML | Two rows and precise values retained; mm applied to Length/Width and the condition only to Length. Secondary-format development evidence. |
| Earlier PDF duplicate/continued/form cases | Records were retained, but repeated condition fields, blank-cell scalars and insufficiently strict review predicates remain. Engine `complete` did not close these defects. |

Two older HWPX title/caption expectations describe author intent, not explicit caption
XML: the fixture writer emitted ordinary paragraphs with heading formatting. Preserve
the failed author-intent comparison, but do not misreport it as loss of a native caption
marker. New qualification must separate explicit roles, contextual readings and genuine
ambiguity. Unrelated invented fields are still definite defects.

Evidence locations: `structural-kpi-20260912/first-native-01/` and
`xlsx-candidates-02/`; `structural-kpi-20260913/outline-*`, `roles-*`, `native-*` and
`shared-*`; earlier HTML/PDF records under `dgx-preparation-20260909/`. Some old PDF
plan/receipt fields are stale; use the separately verified source identities and actual
requests, not rewritten historical receipts.

## Environment and qualification boundaries

Spark-B retains verified copies of the existing model, CUDA runtime, licenses and core
Python dependencies for the next bounded primary-format check. They are not a complete
installed model pack or an always-running service. No external model download or global
installation was added, and the shared RPC/model services were not reconfigured. The latest completed Spark-A comparison uses
the existing shared model with per-request sampling, not a new task-owned server.

The earlier task-owned 9B servers use loopback authentication, read-only components, four CPUs, a 16 GiB
cgroup limit with no cgroup swap allowance, and host-memory/OOM safeguards. The host has
swap configured; GPU allocations are not fully represented by cgroup memory. These GPU
development checks are **not CPU-only 16 GiB, whole-host no-swap, GPU-memory, managed-pack,
platform-installer or independent quality qualification**. The shared-model run instead
limits the owned Python process, checks both hosts and leaves server settings intact.
Host OOM counters remain unchanged from their pre-run baselines (A: 11, B: 0); this
is no new OOM, not a claim that the hosts have never had one. The temporary source,
library and input copies were hash-checked and removed after evidence collection.

## Unresolved defects and next implementation order

| Priority | Defect / owning code | Required correction and acceptance check |
|---|---|---|
| 1 | **Complete primary-format extraction is not approved.** HWP lacks complete occurrence-bound values and relationships. Current XLSX replies no longer duplicate physical coordinates but still read leaf headers as data, even with source-local repair feedback. The 50-row baseline still retains only 12/45 rows. | Correct the unclassified-row hint currently called `dataRows`; preserve exact geometry and all semantic choices. Then verify actual header/record interpretation and HWP occurrence binding. A full-prior-proposal contract remains oversized and unadopted. No role forcing, source deletion or approval by labels/counts. |
| 2 | Nonrecord title/caption/unit content is still confused with values. Run 44 applied a unit to a field containing its wording. Exact parent-table discovery is fixed, but actual applicability quality is unverified. | Distinguish document roles and inner values; confirm that units govern measured columns and conditions govern the intended values. Keep uncertainty when the source does not resolve the target. Do not infer correctness from a field's present state. |
| 3 | Repeated condition fields and blank-cell scalars remain in continued/form outputs. Zero-record fragment evidence mapping is corrected, but the model's field and row decisions remain unapproved. | Review fields, values, bindings, order and applicability together while preserving explicit blank cells and distinct equal-valued records. Retain the fragment's original evidence; do not delete sources just to satisfy a reviewer. |
| 4 | Complete inventory, per-task partitioning, checked partial aggregation and replay are implemented. The 96/200-row fixtures fit 4/8 windows at 16,000 characters, with no actual model run. Oversized fixed context or indivisible overlapping multi-record families can still remain partial. | Verify actual-model applicability after the complete native table path is fixed. Review group membership, row coordinates and positive/negative decisions together; measure remaining indivisible cases before changing the plan or its finite limits. |
| 5 | HWP/HWPX and XLSX lack independent end-to-end approval across varied forms. Prose records still have wrong per-item states and occurrence bindings. | Freeze new expectations for hierarchy, label/value and prose records, merged/nested/continued tables, subtotal/note rows and three-page content. Compare equivalent content in different layouts and genuinely different forms, not a fixed template. A failed holdout used for a fix becomes development evidence. |

Fix reproduced preservation defects before another broad inference run. Run only
affected examples within a predeclared budget; retain raw failures and do not
increase that budget automatically. A result-to-file writer, visual editor and
pixel-matched reproduction are not prerequisites for this work.

Whole-cell XLSX meaning requests use native strings and exact paths, not generated
coordinate labels. A merged title is not necessarily a column definition. That role
choice and actual applicability remain quality checks, even when the corrected request
fits its budget. Do not promote successful request preparation to correct interpretation.

## What finishes the primary formats

- The data-preservation defects above are fixed with directly relevant regressions.
- New HWP, HWPX and XLSX documents cover varied forms and layouts, including different
  presentations of equivalent content and documents whose structures genuinely differ.
  All required hierarchy, fields/records, values, relationships and source links match
  independently prepared expectations. Consistency means the same preservation and
  evidence rules, not identical field names or a fixed output template.
- Comparison covers the whole result: no missing/reordered record, duplicate field,
  incorrect header grouping or unreported change of scope. Unclear and unobserved
  elements remain distinguishable from explicit blanks and absent values.
- Layout and resource information needed to understand the source is retained or its
  absence reported. Raw text, an opaque XML archive or a copy-like output alone does
  not demonstrate logical structure and meaning. No standalone writer or application
  rendering check is required to finish this KPI.
- An oversized or unresolved document exposes the missing work in a useful partial
  result; this separate failure-behavior check does not count as accuracy success.
- The actual Spark product entry point uses native parsing and the configured model,
  records total cost, and preserves results across explicit resume. Recognition is
  included when the selected input actually requires it. No supplied model answers
  or outside-agent interpretation stand in for this path.
- Operation remains bounded and safe on the shared host, with no source mutation,
  hidden model download or unrequested external document transfer.

| Format | Required comparison for the selected completion cases |
|---|---|
| HWP / HWPX | Text, reading order and section hierarchy; labels and values; table cells, merges, nesting and header/record roles; checkbox states and list markers; notes, headers/footers and referenced objects. Link units, conditions and footnotes to the correct elements. Preserve original source locations and expose conversion loss or unsupported objects. |
| XLSX | Sheet names/order and cell coordinates; label/value and repeated-record structure; native types, exact strings and decimal spelling; formulas and saved caches separately; explicit blanks and missing cells; merges, header groups, notes and their applicability. Retain relevant native references and never execute formulas or external links to fabricate expected values. |

Use prior expected structure and independently inspect the actual bindings, not
just whether a string appears somewhere in the source. Secondary formats and broad
PDF qualification do not gate this target, and finishing it is not the older
multi-format formal release approval.

The earlier prepared independent set was re-audited: the identical HWPX receipt had
already been used in failed model runs, and the binary HWP note fixture guided the
native correction above. Both are development-only, removed from active independent
membership with original bytes, expectations and the membership-change evidence retained.
The primary XLSX credit-register then failed its first input-holdout model execution.
That failure guided source-localized structural repair, so it too is development-only
and removed from active membership. Original bytes, pre-inference expectations and
responses are unchanged. Eight candidates in deferred formats remain; that count is
not approval or a fresh unseen-status guarantee. New independent HWP, HWPX and XLSX
cases are required. This was input holdout coverage, not a blinded external review.

There is no separate response-time SLA. Evaluation defaults remain short **12 calls /
900 seconds**, long **64 calls / 3,600 seconds**; development comparisons can use an
explicit different budget recorded before execution. Neither a passed script nor an
engine `complete` is a substitute for the original/result comparison.
