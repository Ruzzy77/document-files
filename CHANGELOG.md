# Changes

## 1.8.0 — independent product candidate (not released)

- PDF page review v16 records whether a pixel unit lies entirely on the page's own vector
  line objects (`onlyNativeRulePixels`, from a complete PDF object inventory) and offers
  such a unit `native_rule` when it is not a recognized table border; a native rule proves
  no cell empty and no text correct. The review also prepares a lossless detail covering
  every displayed unit when the missing-slot crop does not, instead of halting with
  `visual_rule_context_not_displayed`. The delivery-form development page framed a note
  row below its recognized table: the frame shared a component with the table grid,
  was displayed, had no admissible label, and the first pass had prepared no detail.
- Scope-axis protocol v6 adds one system sentence: a condition selects the values it
  tests, not the subject of its outcome, so a rule that tests one field and states a
  record-level outcome selects only that field. The fifteenth continued-table run had
  applied the Korean condition to every mapped column and the English wording of the
  same rule to the quantity column; replaying the saved requests (scope-prompt-probe-03)
  reproduced the record-wide answer exactly and, with the sentence, chose the quantity
  column for the Korean condition while the English condition and the unit kept their
  answers. Stored scope decisions under v5 are incompatible.
- Compiler v23 reads a delimiter label/value line as one scalar within a region: when a
  field binds the line's value span, a further field that binds only the label span or
  the whole line is dropped and recorded as `label_value_line_field_dropped`, and a
  meaning that listed the dropped field keeps its remaining scope. The fifteenth
  continued-table run's raster page emitted three fields per repeated condition line
  (whole line, label, value); only the value folded into the earlier page's field, so
  the whole-line and label fields survived as extra properties.
- Applicability batches are sized to the input budget minus a 4,500-character reserve
  for the applicability output allowance, because the managed context check counts
  that allowance and Korean-heavy JSON runs near 2.3 characters per token: a batch
  sized to the full budget exceeded the model context in the fourteenth continued-table
  run.
- Table protocol v18 offers a source whose text is a bare number only the review
  choices, never a meaning branch: a bare number states no unit, condition, note or
  definition, and the continued-table development runs had selected plain data values
  as carrying meaning and then quoted them for header definitions, which the details
  phase rejected as a quote mismatch. Prior table-stage checkpoints are incompatible.
- After a decided `continue` or `duplicate`, the later fragment's repeat and column
  keys follow the earlier fragment's keys by column position (recorded as
  `continuation_columns_renamed`), because regions are interpreted independently and
  the twelfth continued-table run's later page named the same columns differently, so
  the join was refused as a column conflict; value types must still agree. The earlier
  fragment's column definitions then also govern the appended rows (recorded as
  `continuation_definition_extended`), so a column handle of the earlier fragment means
  every data row: the thirteenth run had joined four rows but applied the unit and
  conditions to the first two only.
- Compiler v22 accounts for a selected node whose entire text is the consumed binding
  of a field: its whole content is the field's value, so no disposition is needed. The
  ninth continued-table run had bound the raster page's title that way, given no
  disposition, and been sent to a repair that dropped the title and reused its meaning
  ids as field ids.
- PDF page review v15 (v13 and v14 were intermediate development identities) makes
  every wire decision an object keyed by inventory id (units, missing slots, source
  checks and grid checks), so the grammar binds each id exactly once in input order,
  and offers each unit only the labels its own facts allow (text labels need a
  referenced string, `table_border` boundary-only pixels of a table, `rule_edge` a
  rule-edge candidate; `unknown` always), with shared label sets in the contract's
  `$defs`: the sixth continued-table run read every cell and line exactly, then its
  reading review answered `unknown` for the two source strings at the positions of
  the border units; the seventh labeled a string-referencing unit and a content unit
  as table borders; the eighth's one-branch-per-id contract pushed the reading review
  past the model context. Prior review checkpoints are incompatible.
- Image read v5 reads a page's measured grid cells and its text lines in separate
  bounded requests: cells over the prepared page and lossless detail, lines over
  lossless page strips of exactly one line each (review images v2 adds the
  `line_strip` purpose and several crops per preparation) shown on one line sheet per
  request: up to twelve strips pasted in entry order with an entry-number label
  column, because the managed vision policy admits two images per request; each
  entry names its label and its rectangle inside the sheet.
  Every part must fit the remaining model-call budget before any is spent, the merged
  answer is validated as one reading, and the checkpoint records the parts and strip
  identities. Bounded probes had read every cell (12/12) and every line on strips
  (4/4); the single request over the scaled page put the second continued-table run's
  header and first data row into the line entries and answered every cell empty, and
  strips of several lines were filled in reading order regardless of the entry
  rectangles (third and fourth runs). Prior read checkpoints are incompatible.
- PDF page review v12 no longer fails a page when the model labels a pixel unit that
  references no observed string as `source_text` or `text_and_border`: the label
  accepts nothing, the receiver records it under `reinterpretations` as `unknown` and
  the page stays unresolved for the literal-reading attempt; the prompt says such a unit
  is never source text. The first continued-table development run (page 2 raster with
  two new rows) had halted there with `visual_text_without_source`. Prior review
  checkpoints are incompatible.
- Semantic prompt v28 (v25 to v27 were intermediate development identities) lets
  the cross-region relation decision name a `duplicate`: the right table shows the
  same rows again (a copy, a second print, an image of the same page) and adds no
  rows, next to `continue`, `separate` and `unresolved`. Each adjacent-page candidate
  now carries the observed row counts, whether every right cell repeats the left cell
  at the same position (`rightRepeatsLeft`), and the same row positions of both
  tables (first two rows and the last row, whole rows) as evidence instead of the
  last four and first four cells. The relation contract of each batch has one branch
  per candidate that binds `candidateId` and `sourceRefs` to the offered candidate
  and nodes and offers only the decisions its evidence allows: rows that repeat every
  left cell add nothing, so `continue` is not offered for them and `duplicate` is
  offered only for them; the prompt says so. The fourteenth GPU run had called the
  identical raster copy a continuation because it "appears on a subsequent page".
  Every candidate also offers both pages' interpreted text lines (titles, continuation
  markers, statements) as context, and the prompt states that rows that differ do not
  by themselves make a different table: the ninth continued-table run called the page
  with new rows a separate table without ever seeing that page's title or its
  "(continued)" marker. Cell views carry only text, table, row and column, line boxes
  are integers, at most eight lines per page are offered, and the relation call
  reserves 2,048 output tokens with a 1,024-token reasoning budget on the managed
  client instead of the managed maximum without reasoning, because the tenth run's
  two-page evidence exceeded the model context and the ninth and eleventh runs called
  any page with new rows a separate table. Each candidate also states whether the
  right table's first row repeats the left header (`rightHeaderRepeatsLeft`), and the
  prompt names that as the usual continuation (prompt v29).
  Repeated-text counterparts consider interpreted region members only, because the
  observation keeps every channel's copy of a line (native lines, recognizer source
  cells, superseded text) and those copies had made every wording ambiguous. The relation
  request sends a bounded position view of each cited node (text, role, page, box,
  table membership, row and column) instead of the full interpretation view, whose
  alignment and conflict metadata had pushed whole-row evidence past the 16,000
  character request budget in the twelfth GPU run; the thirteenth run, which had
  offered the left table's first and last rows against the right table's first two,
  compared the left last row with the right second row, answered `separate` and cited
  table identifiers instead of nodes. Compiler v21 (v20 was an intermediate
  development identity)
  binds a decided duplicate to the same rows: the copy adds cell and column-definition
  provenance to the earlier rows and its table definitions fold into the earlier ones
  (`duplicate_table_definition_merged`); a duplicate whose compiled rows differ is
  refused as `table_duplicate_rows_differ` and stays unresolved. After a `continue` or
  `duplicate`, a scalar field or unit/condition statement repeated on the joined page
  (every cited node is a page text whose exact wording appears once on each page, the
  earlier field cites those counterparts and carries the same value, or the earlier
  meaning cites them in the same resolution state) folds into the earlier field or
  meaning, recorded as `repeated_statement_merged` and `repeated_meaning_merged` in
  `coverage.programCorrections`; text identity never relates pages by itself. The
  later page's nodes join the schema and value evidence and the correction record,
  while each assertion keeps the earlier definition's own references: the fifteenth
  GPU run had presented merged references and the copy's cells inside applicability
  candidates, and the page 1 condition then chose every column of the record instead
  of the Qty column it had chosen before. Scope integration v13 treats the merged
  wording as statement text, so it is not offered as an applicability candidate. The
  mixed native/raster development document had compiled the raster copy as four rows
  with the title, unit and condition statements twice.
- Scope-axis protocol v5 (v4 was an intermediate development identity) states in its
  prompt that a meaning's own statement text is never offered as a candidate and that the decision selects the offered fields, columns
  or rows whose values the meaning qualifies (a unit the measured or counted values, a
  condition the values it constrains), and raises the managed applicability request
  policy to reasoning budget 2,048 within a 3,072 total output cap (v3: 1,024 within
  2,048; the managed output cap is 3,072). Bounded probes that replayed the eighth GPU
  whole-path run's two unit tasks with the product contract order restored reproduced
  the product answers exactly as controls, chose the Qty column for the native page at
  both budgets with the added sentence, and chose the Qty column for the raster page at
  2,048 (unresolved at 1,024). Two earlier probes had replayed contracts whose property
  order the recording harness had sorted, which changes the grammar; they are retained
  as records but are not evidence. Greedy sampling, the selection wire, source binding,
  public v1 and document budgets are unchanged; v3 policy identities are rejected on
  resume. Selection wire v2 additionally offers every column handle as a direct
  standalone target meaning all data rows of that column: the ninth run named the Qty
  column in its explanation and was then forced onto the only field the standalone
  branch allowed. Several direct columns of one record form one record entry with one
  part; the first decoder made one entry per column, which the axis codec rejected as
  a duplicate record when the native HTML rerun chose Length and Width directly. The PDF native object inventory identity no longer includes its
  elapsed time, which had given the same document a new identity per run and changed
  the table structure request through the native ruling consumption record.
  Subsequent whole-path development runs under these identities returned complete:
  the mixed native/raster PDF in 19 calls with every unit and condition applied to the
  Qty column only, and the native HTML case in five calls with unit scope Length/Width
  and condition scope Length; both resumed without calls. This is development evidence
  on two documents, not qualification.

- Applicability tasks no longer offer the scalar fields that carry the wording of a
  unit or condition statement of the same region (the statement's own fields or a
  sibling such as its translation), and a repeat row whose every cell is cited as a
  column definition is compiled as a header row with a
  `column_definition_row_relabeled_header` record in `coverage.programCorrections`
  instead of a repair request or an unresolved issue. In the seventh and eighth GPU
  whole-path runs the scope protocol ran on the mixed PDF but "applied" each unit and
  condition to the fields carrying its own wording or its translation because they
  were offered, and the raster table's structure answer kept the header row as data
  even after the repair feedback named it. Definition meanings keep their own fields
  as candidates; table columns, containers and other fields are unchanged.

- Recognition subsets are serialized deterministically. PDFium writes a per-process
  file identifier and the clock time into each single-page subset, so the same page
  received a new recognition identity on every run and every model payload that named
  its nodes carried a different hash; two otherwise identical GPU whole-path runs then
  diverged in the table structure answer. The worker now replaces both values in
  place (same byte length, offsets intact) with an identity derived from the source
  hash and page number. Recognition fingerprints of documents processed before this
  change differ from new ones; no released identity is affected.

- Regional compilation names three repairable defects precisely and routes
  statement-only applicability to the scope protocol. A repeat row whose every
  observed cell is cited as a column definition but is marked `data` (or `subtotal`/
  `note`) now fails with `column_definition_row_marked_<role>:<row>` before any header
  label is parsed as a value (the sixth GPU whole-path run saw only
  `binding_cannot_represent_requested_type` twice); a colliding scalar key fails with
  `duplicate_data_property:<key>` (the same run repeated `condition` for a bilingual
  pair); and a `unit` or `condition` meaning whose only targets are the string fields
  carrying its own statement is compiled as unresolved, so the separate scope-axis
  protocol decides which values it governs instead of the statement qualifying
  itself. A unit on a numeric field of its own statement keeps its scope. Public
  contracts and budgets are unchanged.

- A PDF image-read projection now changes only its own page's observation identity.
  Previously the presence of any projection entered every page's fingerprint, so a
  multi-page document whose raster page had been read, proposed and reviewed failed the
  atomic application step with `visual_apply_observation_changed` because the native
  page's review plan no longer matched. Identities of documents without projections
  are unchanged.

- Image-read contract v4 (v2 and v3 were intermediate development identities). Each
  reading writes its literal text before its state and the wire contract binds them:
  `text` requires at least one character, `empty` is offered only for measured cells
  entirely inside the lossless detail and requires the empty string, and `uncertain`
  may keep a fragment. Non-table recognition text is planned as visual lines (fragments
  sharing half of the shorter box height vertically within one such height
  horizontally), each keeping its source references in reading order; the projection
  proposes one node and region per line and maps fragment context to it. The prompt
  keeps the v2 wording: a v3 sentence describing text entries as lines outside the grids
  made the pinned model read every grid cell as empty and table rows as text lines on
  the same inputs, while the v2 wording with the line-level plan read every cell and
  line exactly (bounded probes). v1 accepted `text` with an empty string and
  fragment-level entries that the model answered line by line, rejecting both only
  after generation, which halted the first two GPU whole-path mixed PDF development
  runs (`image_read_text_missing`, `image_read_empty_without_detail`). Validation,
  budgets, candidate exposure and the public v1 result are unchanged; older read
  checkpoints are incompatible on resume.

- CUDA runtime packs. A `llama-cpp-runtime` manifest may declare `accelerator: cuda`
  with explicit `cudaArchitectures` (Linux only); the managed server then offloads
  every layer, the projector and the KV cache to `CUDA0` instead of forcing CPU
  placement, and the client identity names the accelerator. CPU packs and their
  identities are unchanged. The runtime builder gains `--accelerator cuda` (static
  cudart/cuBLAS, GCC 12, explicit architectures, NVIDIA CUDA EULA notice) and the
  model pack builder gains `--from-pack` to re-declare compatible runtimes without
  reconversion. Deployment adds a DGX Spark GPU compose profile. No GPU pack is built
  or qualified by this change alone.

- Scope-axis protocol v3 raises the managed applicability request policy to reasoning
  budget 1,024 within a 2,048 total output cap (v2: 512 within 1,536). Greedy sampling,
  the selection wire, source binding, public v1 and document budgets are unchanged; v2
  policy identities are rejected on resume. Bounded development comparisons on the
  frozen v17 unit request showed the 512 budget truncating the model's reasoning: with
  1,024 the same request selected only the Length/Width header group, and official
  sampling at 512 also selected those columns but is not adopted. A fresh whole-path
  native HTML development run under v3 then returned complete in five calls with unit
  scope Length/Width and condition scope Length, and resumed without calls; this is a
  development pass on one case, not release qualification.

- Table protocol v17 makes record-table details content-only. Its actual request
  contract and decoder reject applicability fields; the compiler retains unresolved
  targets until the separate scope protocol chooses them. Repair feedback follows
  the same boundary. Explicit content uncertainty, scalar forms, source quotes and
  cumulative budgets remain unchanged. Reject old table identities on resume.
  A newly validated scope replacement clears only that task's stale-batch diagnostic
  after a legitimate content revision; incompatible stored provenance still fails.

- Activate scope-selection wire v1 under scope-axis protocol v2: one selection
  list for record/standalone choices and one shared column handle on display and
  output. Preserve scalar/mixed/batched decisions, multiple records, groups,
  bounded discovery and source row gaps through the existing axis validation.
  Fingerprint the exact new request/map and reject previous internal identities
  before resume. Public v1 and document budgets are unchanged. Actual-engine
  independent model quality remains unqualified; later full-path checks preserved
  no-call resume but still failed unit scope.

- Scope integration v12 adds scalar value origins to applicability candidates:
  exact binding/status, bounded value text, observed table coordinates and separately
  labeled compiled row roles. Keep legitimate subtotal fields available; geometry
  does not decide applicability. Candidate/checkpoint fingerprints cover this
  context, with stable field handles. Source-binding v2 also checks and attaches
  selected scalar value bindings without inventing sources for absent/uncertain
  fields. Public v1 contracts and the model's selection contract are unchanged.

- Compiler v19 preserves observed blank scalar values for decimal, numeric,
  boolean and null field declarations, matching blank repeat-cell behavior. Keep
  empty text and its binding instead of treating it as failed conversion. Nonempty
  sources still reject blank status; absence, uncertainty and typed null are not
  converted to blank. Old compiler checkpoint identities are rejected.

- Activate scope-axis protocol v1 and regional checkpoint v3. Use actual wire/schema
  size for batches; bind compiler provenance separately from model selections and
  regenerate it on resume. Save the original batch/context and execution policy.
  Managed scope requests use reasoning budget 512 within a 1,536-total-token cap;
  other phases retain their profile settings. Per-request overrides also reach
  context counting and diagnostics without mutating client identity. Cloud and
  complete-only clients keep explicitly recorded, distinct execution policies.
  Public v1 contracts remain unchanged. Content quality, compact long-range
  provenance and full product-path qualification are still pending.

- Prepare internal scope-axis wire v1 and compiler source-binding v1 for multiple
  records, scalars, mixed/batched decisions, continuation fragments and missing
  observations. Typed references, source origins and explicit resource limits
  are checked without changing values or public v1 results. These components
  were initially prepared without activation; engine integration follows above.

- Make the cumulative table-budget tampering test independent of Windows clock
  resolution, using explicit scripted-call durations. Also retain a valid
  zero-duration checkpoint/resume case. Product timing and budget rules are unchanged.

- Prompt v24 / table protocol v16 / compiler v18 / scope integration v11 separate
  content uncertainty from unknown applicability. Table conversion preserves the
  content status, and resolving a scope cannot promote explicitly uncertain
  content to interpreted. Private status participates in task freshness and
  checkpoint identity; unresolved content remains partial on resume without
  rereading an unchanged region solely because applicability was resolved.
  Public v1 result shapes are unchanged; older internal checkpoints are rejected.

- Share the exact product message assembly with internal development checks so
  the visible output contract cannot be confused with a transport-only grammar.
  Serialized requests and repair-prefix order are unchanged; this refactor does
  not alter a protocol, checkpoint identity or interpretation quality status.

- Scope integration v10 / reference wire v2 / compiler v17 add row-range and
  row/column applicability over compiler-owned source geometry. Only existing data
  targets are selected; headers, notes, subtotals and blank rows create no values,
  and gaps or undecided rows preserve uncertainty. Row-only scopes do not annotate
  an entire column's schema. Continuation fragments retain their source row numbers
  while generated targets move to the joined record. Evidence, mapping freshness,
  overlap and bounded expansion are checked before application. Checkpoints use
  canonical handles and reject older internal identities; public v1 is unchanged.

- Scope integration v9 uses short typed target aliases at the model boundary.
  Candidate order, complete context, original text and source references are
  unchanged. Decode only target fields before validation; checkpoints retain
  canonical handles, and malformed/foreign aliases do not discard valid siblings.
  Wire version v1 is part of checkpoint identity; older scope identities cannot
  resume. This does not infer applicability or add row-scope capability.

- Scope integration v8 retains short definition references within the existing
  encoded request budget instead of silently cutting context after eight references.
  Truncated text, absent nodes and budget-omitted candidates remain explicitly
  incomplete; no applicability is inferred and old scope checkpoints are rejected.

- Table protocol v15 pins the detail response's `baseRevision` to the accepted
  meaning revision, or null before any meaning is accepted. Initial `changes` must
  be empty. A source-selection hash cannot be substituted for meaning history;
  schema constraints and the existing compiler checks both enforce that boundary.
  This fixes bookkeeping, not the observed unit/condition and scope quality failures.

- Table protocol v14 limits detail output to meanings and the selected sources'
  remainder reviews. Saved model choices and other explicit source reviews are
  reused, not regenerated or heuristically inferred. Positive selections require
  nonempty meanings; exact quotes, complete review coverage, explicit reselection
  and revision checks remain mandatory. Old table checkpoints cannot resume as
  v14. Restored document usage cannot be lower than the cumulative table stages,
  including after an explicit grant. Public v1 contracts are unchanged.

- Table protocol v13 adds a durable source-selection substep before detailed meaning
  generation. Every owned source receives a model choice and reason; all-negative
  choices compile to explicit reviews without another call, including after resume.
  Unknown/deferred reviews remain partial. Only selected sources can be directly
  quoted, and incorrect choices can be explicitly revised without erasing prior
  meanings. Selection histories bind inventory, frozen structure, model and reference
  identity; each accepted meaning revision names its selection. Selection/detail
  usage shares the unchanged stage/document caps. No checkpoint migration, blanket
  header/value exclusion, whitespace normalization or public v1 change is introduced.
  Actual model quality and release qualification remain separate from these checks.

- Table meaning protocol v12 / source decisions v3 finish all source choices before
  generating a separate meaning list and explicit source reviews in the same call.
  Selection must match direct quoted sources; other review roles must match their
  decisions. Grouped reviews cover every source once, and unresolved/deferred states
  survive even for fully quoted or empty text. Joint evidence, exact quotations,
  duplicate checks, revision history and frozen structure are preserved. Transcribed
  unit-bearing labels still require semantic review. No added stage or budget;
  older table checkpoints are incompatible and public v1 remains unchanged.

- Table meaning protocol v11 distinguishes already represented labels and ordinary
  values from additional meaning, while still requiring review of embedded notes,
  units and conditions. A literal empty source cannot offer `has_meaning` because
  no nonempty exact quote can exist there; explicit negative, unresolved and deferred
  reviews remain available and required. Whitespace and source text are not normalized.
  Older table checkpoints are incompatible; public v1 contracts remain unchanged.

- PDF review v11 / unit display v2 also display residual groups that share an
  original pixel component with measured table rules, even when those groups
  have no edge-candidate status. This exposes mixed lettering and residual marks
  previously hidden by source-box grouping. Original pixels, edge-search bounds,
  blank-cell rules and image budgets are unchanged; display grants no new content
  or edge authority. Older review/display checkpoints cannot be resumed as v11.

- Visual application v4 applies fully reviewed blank cells to alternative image-read
  tables and links each replaced recognizer-table gap to its exact reviewed replacement.
  Validate source-slot geometry for every candidate cell and preserve original tables,
  OCR captures, strings, bindings and issues in their original/evidence records. Only
  the matched processing dependency is resolved; raw unknowns, unverified extents and
  unrelated errors remain. Missing/changed ownership, incomplete grids or unknown
  decisions preserve the original view atomically. Older application checkpoints
  cannot acquire new resolutions on resume. Review v10/model inputs and public v1
  contracts are unchanged. Synthetic acceptance is not document quality approval.

- Add source-bound membership panel images (PDF review v10, unit display v1) within
  the existing two-image/byte/pixel limits. Keep the full source PNG and original RGB
  detail, show each required unit's exact mask separately, and bind panel coordinates,
  membership, actual PNG/RGB hashes and capture identity before inference. Masks do
  not assign semantics or original color. Missing/changed display remains blocked;
  checkpoint restore and application require the same evidence without replaying
  rendering/model work. Visual application v3 preserves the display fingerprint in
  page-review provenance. Public extraction v1 and finite document budgets are unchanged.

- Review v9 blocks candidate rule-edge units before inference and during response
  validation until exact source-bound membership can be displayed. The actual v8
  model still called those fragments source text and missed a known title line-break
  mismatch. Preserve the rejected development response, all pixels and the finite
  budget; do not accept a caller-supplied display flag or resume the v8 checkpoint.

- Review v8 separates residual connected components after measured rule pixels and
  candidate edge neighborhoods are partitioned. Source candidates follow those
  components, not the original grid-connected page shape. Every original pixel
  remains. A model may choose `rule_edge` only with offered context; short/detached
  marks do not acquire it from proximity, and it cannot prove a missing cell empty.
  Columnar input and ordered decision strings reduce repeated JSON; the program
  restores explicit IDs before validation/checkpointing. Old checkpoints are rejected.
  These are review mechanics, not an independent quality approval.

- Preserve an additional exact contrast-core mask alongside every original foreground
  pixel (pixel inventory v2). Grid v2 first keeps the existing all-foreground path,
  then may locate a complete grid from the contrast core without absorbing nearby
  marks or changing width/search limits. All residual pixels remain unassigned.
  Review v7 partitions actual rule runs separately from text, so full-cell bounds
  cannot let a model classify measured rule pixels as literal text. Independent
  text/edge review and document quality are still required.

- PDF review v6 rejects alternative grids whose original-page line measurements
  remain unresolved, before spending a review call and again on response validation.
  An affirmative model answer cannot override missing/ambiguous bands. Preserve the
  v5 development failure, original measurements and remaining document budget.

- Add reversible image-reading projections and a separately budgeted text/grid review.
  PDF review v5 requires every proposed string and rectangular grid to be checked
  against the images before its alternate regions can enter semantic interpretation.
  Original OCR, nodes, bindings, tables and issues remain available. Unknown/failed
  reviews preserve the original view and are not replayed. Visual application v2
  records selected projection fingerprints; older checkpoints remain incompatible.
  Replacement-specific issue resolution and new blank-cell application were still
  conservative partial paths at v2; v4 adds these links without quality certification.

- Add bounded literal image-reading candidates after unresolved PDF review or an
  unlinked grid prevents review planning. Preserve text, precision, uncertainty and
  source-pixel references separately from original OCR and table structure. PDF
  review v4 / image-read v1 count the attempt before inference, preserve an unstarted
  reading for budgeted resume, and never replay completed/interrupted/failed reads.
  Candidates are returned in partial results; reviewed structural application is
  still required before they can become active extraction values.

- Match decimal parser page dimensions with PDFium binary32 dimensions only when
  they have the same binary32 representation and differ by at most 0.001 pixel.
  Retain source, boundary, pixel and crop checks; do not equate renderer pixels
  or rewrite table structure. Adapter v29 prevents older checkpoint reuse.

- Collect missing table cell geometry from existing cached page pixels independently
  of optional OCR repair. Reuse prior complete/partial/unavailable observations,
  retain shared pixel/cell limits, and report unavailable cache/layout evidence
  without rendering, OCR or text/structure changes. Adapter v28 rejects older
  checkpoints; image text recovery and document quality are still incomplete.

- Bind ruled-table repair orientation to the unique containing original OCR crop,
  not the last OCR region on the page. Require observed upright orientation and
  matching input-pixel evidence, record the source selection and check it on reuse.
  Adapter v27 invalidates older checkpoints; OCR budgets and opt-in policy stay
  unchanged. This fixes a repair gate, not the remaining OCR quality failures.

- Add Linux assembly input/receipt v2 for explicit, hash-pinned omissions of
  Windows-only pip/setuptools installer launchers. Verify original wheel members
  and installed bytes before excluding them, retain omission evidence and rewrite
  the affected installed RECORD files. Native code/model exclusions and automatic
  pruning are not supported; the pack's foreign-binary rejection remains intact.
- Make synthetic deadline and pack-path tests portable: compare against the actual
  supplied absolute deadline and use manifest-style paths on Windows. Production
  execution budgets and path restrictions are unchanged.
- Add recognition audit/verification v3 for explicitly authored, non-runtime
  packaging records and scoped license collections. Bind their hashes, declared
  authorship, licenses and artifact associations without inventing upstream
  origins. Limit paths, permissions, text size and inventory; retain ordinary
  wheel/native/model origin checks. Legacy audits and the public pack format are
  unchanged; the new receipt does not authenticate authorship or approve a release.
- Add optional, pack-owned Linux recognition library directories to the actual
  worker environment. Validate and bind the ordered paths to adapter v26 identity;
  reject unsafe paths and do not inherit host loader/preload settings. This supplies
  a controlled loading path, not dependency-closure or recognition qualification.
- Add pack provenance v2 and recognition audit/verification v2 for locally built
  preparation artifacts. Bind original inputs, derived output hashes and shipped
  recipe/build records without falsely assigning an upstream URL to built bytes.
  Hash all supplied inputs, reject cyclic/unbound derivations and retain original
  model/OCR and official ARM CPU Torch checks. Legacy v1 evidence remains distinct;
  the outer pack format and public extraction APIs are unchanged.
- Reject NFC/case-fold-colliding Linux stage destinations before installer execution.
  Explicit unused terminal-database omissions preserve original member evidence;
  no automatic alias choice or weaker pack path policy is introduced.

- Require a candidate-bound redistribution review for qualification v4. Cover every
  non-metadata artifact, reject unresolved/stale reviews, verify selected embedded
  ZIP notices and bind required source/recipe/notice artifacts by SHA. Promotion
  refuses to omit required metadata while keeping unrelated private evidence private.
  Existing multipart transport and exact-byte upload/download checks are unchanged;
  these checks do not make legal decisions or approve a candidate's redistribution.

- Require a source-first table meaning response (table protocol v10). Each owned
  source has an explicit decision before optional meanings, with exact local quotes
  and additional owned-source evidence. Preserve independent meanings and joint
  evidence while rejecting duplicate IDs and identical meaning copies.
- Preserve explicit unreviewed decisions, including fully quoted and empty sources
  (meaning review v2, compiler v16). Reject repairs and saved histories that turn
  reviewed sources back into unreviewed work.
- Extend reference-wire v2 to source-decision keys and additional quotes. Keep the
  full reversible dictionary in checkpoint identity, not repeated in model input.
  Public v1 APIs and document budgets are unchanged; older internal checkpoints
  are rejected. Actual v10 meaning quality remains unverified.

- Compact the model-only table meaning request (table protocol v9). Keep compiled
  header references beside each column, remove only geometry-reproduced provenance
  and byte-equal context text copies, and retain full evidence in canonical results.
- Use reversible, role-specific source/table handles only when the complete input
  including its dictionary is smaller. Preserve literal quotes, values and semantic
  IDs; restore references before the existing quote, revision and compiler checks.
  Bind dictionary version/hash and activation to checkpoints, including completed
  stages. Reject older protocols instead of silently migrating them.
- Record exact initial/repair character preflight, preserve compiled structure on
  overflow, and stop deterministic preparation failures without retrying. Do not
  reattach a table's own mapping on resume; frozen meaning stages need no mapping guide.

- Preserve an installer's original error if process-group cleanup is denied.
  Record each cleanup step, try only a still-live owned child as fallback, and
  refuse successful assembly when cleanup or its receipt is unconfirmed.
- Constrain PDF review arrays to the actual inventory (v3), with no dummy slot for
  empty inventories. Offer unique, overlapping exact-text native line geometry as
  an additional candidate; retain original bounds, pixel runs and unknown decisions.
- Stabilize PDF page-review identity (v2): exclude grid measurement time and the
  separate legacy whole-page projection, retaining original PDF and observation
  evidence. Reject v1 plans/checkpoints rather than silently migrating decisions.
- Add an internal PDF page-review stage for explicitly image-capable managed packs.
  Account for exact non-white pixel runs, measure line candidates on the same RGB
  render, and reject missing-source pixels, incomplete slot borders and conflicting
  reading order. Share document call/time budgets and durable page checkpoints.
- Apply accepted page decisions atomically before semantic planning: retain original
  strings/raw OCR, add only reviewed blank cells, reorder matched regions, and keep
  exact issue-resolution evidence. Recompute derived observation counts/status,
  without claiming OCR truth or weakening the final complete predicate.
- Add an explicit optional F16 projector conversion to the model-pack builder with
  preprocessor/config checks, GGUF inspection and conversion receipt v2. Default
  text-only conversion is unchanged. Fix a POSIX-only test path assertion for Windows.

- Add optional, explicitly pinned CPU vision-projector transport without changing
  text-only pack behavior. Validate bounded inline images, freeze image/schema input,
  use the owned server's multimodal token calculation and pin visual policy in job
  identity. Image transport is not PDF content review or completeness approval.
- Prepare transient PDF review images by reproducing the original full-page render
  and verifying source, recipe, dimensions, coordinates and RGB digest. Preserve
  bounded lossless crops without embedding image bytes into public observations.
  Stable image identity excludes elapsed time; content/blank/order decisions remain
  outside this helper.
- Consume a native ruling only after rechecking its captured pixels, unique original
  OCR source membership and closed native table-cell borders (adapter v25, processing
  ledger v5, consumption v1). Preserve text, bindings and table context; exclude only
  that source from data candidates and retain the exact resolved issues as evidence.
  Scan, OCR truth and global completeness remain separate unresolved checks.
- Compare reopened file handles consistently when checking Linux-stage input
  mutation. Preserve all identity/time checks; adapt only synthetic Windows tests
  for POSIX modes and a single-child pipe boundary. Actual target CI remains required.
- Add Linux ARM64 as a fifth target without replacing Linux x64. Pin the ARM PBS
  runtime and connect native core/CPU/recognition CI with ELF/loader/host checks.
  The next CPU runtime candidate is `b10853-cpu.4`; existing installed packs stay unchanged.
- Bind Linux image inputs, patched HWP, Docker architecture and exported config to
  one target. Qualification v3 requires separate x64/ARM64 model, HTTP and container
  evidence; container-identity v2 records actual architecture and CPU quota.
  Actual ARM installation, full recognition and CPU 16 GiB qualification remain pending.
- Verify the delivered image export's actual config, target and ordered layer hashes
  again at the release gate, independently of its build receipt.
- Reject bundled Linux ELF `ld-linux*.so` files, including hash-renamed copies, during
  recognition-stage verification. System loader references remain allowed; other
  loader aliases and redistribution conditions still need independent review.
- Record Linux ELF header policy v2. Permit only the byte-pinned original ARM QtCore
  shared library's empty PT_INTERP metadata, with explicit role, SONAME, ELF type,
  entry and non-PIE checks. Default/executable paths remain strict; stage declarations
  cannot relabel an executable to bypass them. Preserve the original binary, ABI
  ceilings and loader rejection; this policy is not full-stage execution approval.
- Distinguish a wheel's top-level distribution metadata from vendored `dist-info`
  files. Preserve vendored metadata as package data instead of treating it as an
  extra installed distribution or rejecting a valid wheel as ambiguous.
- Add an explicit, hash-pinned ARM torchvision derivative recipe. Restore five codec
  references to the system loader in a new build-tagged wheel, preserve the original
  input and licenses, and regenerate RECORD with a derivation receipt. This is not
  target execution or redistribution approval.
- Capture bounded, source-linked OCR ruling windows without another render or OCR
  call (adapter v24, ruling-pixels v1). Preserve faint pixels, validate the exact
  frame/detection/TSV, and compare a continuous line profile with one native stroke.
  Record supporting evidence separately; do not remove text, bindings or issues.
- Add bounded native PDF object/stream inspection and a narrow missing-cell blank
  decision (recognition adapter v23, native-object v3 and cell-decision v2). Verify linked
  embedded font bytes, supported character mappings and glyph paint bounds without
  discarding logical text bounds. Preserve literal source text separately from PDFium
  projections and explain extra spacing only through verified generated characters. Require a complete supported source inventory,
  four unique closed native borders and a fully observed opaque-white interior.
  Preserve original values and resolved issue evidence; scans, ambiguous content and
  global completeness remain unverified.
- Preserve multiple owners of an omitted generated console script only when their
  pinned source definitions, installed RECORD rows and actual bytes agree. Use
  explicit no-bytecode mode for isolated PBS bootstrap; environment flags alone
  are ignored in isolated mode. Preserve the failed development-stage evidence.
- Add a separate hash-pinned Linux recognition-stage assembler. Check offline inputs
  before executing the selected PBS runtime, preserve source files and archive-link
  provenance, validate installed bytes/RECORD, and keep original auxiliary scripts
  without temporary installation paths. Assembly is not pack or release approval.
- Add bounded Linux recognition-input checking/acquisition with explicit inventory
  hashes, official origins, local-byte verification and owned-worker cleanup. Default
  inspection is offline; this does not assemble or approve a recognition stage.
- Recheck local input bytes before publishing a copied file, sharing the original
  deadline and recording extra reads. Equal size/timestamps no longer hide an input
  mutation; failures retain the partial copy without promoting it.
- Add explicit v2 component selection for recognition inputs without changing legacy
  v1 blocking behavior. Preserve unresolved stage requirements separately, bind each
  selected file to an exact source-manifest row, and keep model reuse local-copy-only.
- Record bounded full-cell, interior, OCR-window and edge pixels separately from OCR
  values (adapter v20, cell-observation v2). Build the source frame and pixel identity
  together to avoid a second full-canvas hash without increasing the pixel budget;
  independently recheck externally supplied frames. Link verified frames to unique
  unmerged table geometry only;
  missing pixels, ambiguous layouts and OCR-link execution remain unverified. Preserve
  existing values, issues and partial status; no blank/completeness approval follows.

- Separate literal source ranges from revisable table meanings (prompt v23, planner v14,
  table protocol v8, compiler v15, scope v7). Require explicit source review beyond
  value/header reads and preserve source coverage through corrections and withdrawals.
- Bind meaning revisions to prior content and change history; reject stale bases,
  unrecorded changes, lost reviews and incompatible checkpoints without losing values.
- Link captured framework/cell OCR input pixels, serialized single-page PDF and original
  page coordinates with import-time checks (recognition adapter v12). Unknown transforms
  remain unverified. A fresh OCR run preserved all 12 input links without clearing any
  original issue; OCR content quality and reading order remain pending.
- Observe full rendered pixels independently of OCR rectangles with source-bound hashes,
  low-contrast counts and explicit component/area budgets (recognition adapter v13).
  Pixel/component observations do not establish complete content, blank values or OCR truth.
- Connect visual components to bounded native/raw-OCR overlap candidates and rechecked
  exact-text structure links without changing model inputs or original issues
  (recognition adapter v14). Missing and ambiguous correspondence remains explicit;
  rectangle overlap alone is not evidence that content was correctly processed.
- Connect repeated OCR tokens through a unique, exact sequence within one verified
  horizontal line (recognition adapter v16, ordered-ocr-source v2). Preserve ambiguity
  at table boundaries and incomplete duplicate lines; do not rewrite text or promote
  page completeness.
- Preserve every planned OCR cell before dispatch and record execution state separately
  (recognition adapter v17). Keep unattempted cells after budget/error/cancellation and
  distinguish no-ink from blank values. A small actual file-list comparison matched
  individual crops. Adapter v18 adds explicit maximum-two-image batches within separate
  process/image/input-pixel/time limits; default single-image behavior is unchanged.
  Raw OCR v2 retains the common original TSV and exact input/row/unit/frame membership.
  Incomplete runs cannot complete or reuse either input. Actual whole-table quality
  and conservative recognition-completeness checks remain pending.
- Record bounded full-path development meaning failures despite engine completion. Revert
  the failed source-first output-order experiment. Clause-level sampling diagnostics
  are not production-profile qualification.
- Separate initial meaning retries from one post-acceptance review within the existing
  document budget. Supply exact remaining source ranges and preserve accepted content
  on failure or no progress; reject incompatible old stage checkpoints. The new path
  has scripted regression coverage. Its fresh full run retained exact structure but
  timed out before returning meaning, so actual post-acceptance review remains unverified.
- Add an optional finite per-block reasoning limit for managed inference, consistently
  applied to template checks, inference and checkpoint identity. Keep the non-thinking
  default and total output ceiling; incomplete final answers remain failures.
  A bounded full-path development run now preserves the correct unit/condition scopes
  and precise source quotes, but remains partial because the caption title was not
  reviewed. This is not independent or release quality approval.
- Build Linux candidates with GCC 12, check actual ELF ABI requirements and exercise
  relocated startup in a digest-identified bookworm image. Actual CPU/native startup
  passed; final-image, full-model and 16 GiB qualification remain pending.
- Preserve original ZIP header validation on Windows, record pinned-source Rust toolchain
  identity, and retain bounded source-download failure evidence without relaxing hash pins.
  Independent holdout, same-candidate qualification and consumer migration remain pending.
- Prepare a separately checked Linux native delivery artifact containing the exact
  binaries, source archives and build/startup evidence. Windows remains review-only;
  this preparation is not a complete recognition pack or redistribution approval.
- Include the exact portable core's patched Linux HWP backend and notices in images.
  Image-build v2 binds core ZIP/source/wheel and actual installed native checks;
  real Docker/HWP execution and full license approval remain pending.
- Inspect portable Linux executable permissions from the verified ZIP, not a Windows
  extraction host's file mode. Preserve actual image execution checks and add direct
  cross-host permission regressions; actual Windows rerun remains pending.

- Require explicit roles for actual non-fixed table rows and distinguish native
  header declarations from OCR predictions (prompt v19, planner v13, table protocol v5).
- Preserve non-record subtotal/note values in budgeted, resumable scalar regions;
  validate decimal text without losing precision, and remove only exact successful
  duplicate reads (compiler v14). Missing roles/cells remain unresolved, not blank.
- Build group-scope candidates from compiled header roles, not OCR flags alone (scope v6).
- Separate page-local recognition checks from unrelated document issues and record
  measured render-to-original-page coordinate transforms (recognition adapter v11).
  Visual coverage, reading order and recognition-backed completion remain unfinished.
- Prepare Windows builds with pinned official terms and actual host/toolchain evidence;
  connect installed-HTTP observations to a separate hash-bound operational review.
- Build/export images from fresh verified inputs without dependency downloads during
  build steps. Actual Windows/image/HTTP qualification and redistribution review remain pending.

- Split record-table structure and meaning into finite checkpointed stages; preserve
  compiled values on meaning failure and reject incompatible old checkpoints
  (prompt v18, planner v12, table protocol v4, checkpoint v2).
- Attach row-role provenance from observed geometry instead of model-written sources;
  fix fully declared header rows while leaving sparse cells and unknown headers unresolved.
- Initially use exclusive table meaning scopes and accepted-statement preservation;
  protocol v6 replaces immutable meaning with auditable correction. Development meaning
  quality is still failing.
- Reject mismatched Windows compiler notices, explicitly disable Leptonica SW builds,
  and include IJG/Berkeley attributions. Exact redistribution review remains pending.
- Reject declared-header records and displaced data-row references before freezing;
  keep conflicting unbounded parent/child meaning scopes unresolved (compiler v13).
- Capture original raw OCR detections, separate exact native-text support, and record
  bidirectional source/structure accounting and actual rotation provenance.
- Fingerprint full visible PDF page renders and retain channel-specific unresolved
  issues (recognition adapter v10); visual completeness is not yet verified.
- Add a bounded installed-HTTP lifecycle runner requiring actual core/source/pack
  identities, with cancellation/restart/budget evidence kept separate from qualification.
- Add pinned Linux/Windows native recognition dependency builds, relocated startup
  checks and actual compiler-runtime notice collection; no automatic redistribution.
- Link actual container image IDs to the measured run and exact recorder receipt;
  fix Windows-specific test isolation and validate original ZIP entry names.

- Compact only model-facing observation metadata, retain unclassified leading rows
  across table slices, and bound continuation requests to compiled candidate pairs
  (prompt v14, planner v9); full source observations remain unchanged.
- Verify actual imported core/evaluator/recorder bytes against the qualified wheel
  and sdist, rather than treating a source checkout or selected asset ID as execution.
- Add an offline audited recognition-stage verifier; native packaging and runtime
  qualification still require real target environments.
- Add geometry-derived header-group scope candidates, focused statement/context inputs,
  stable group provenance and checkpoint identity, exact duplicated-target normalization,
  and region-aware rejection of ancestor/child scope overlap (scope v5, compiler v12).
- Freeze evaluation inputs/specifications before inference and keep execution reports
  pending until a separate hash-bound semantic review (evaluation/qualification v2).
- Require exact clean source/build/pack/image/evidence identities for release gates;
  record bounded Linux cgroup memory, swap, OOM, isolation and failed-run cleanup.
- Build fresh artifacts in a new output directory, verify multipart release transport,
  and promote only qualified identical files without rebuilding.
- Prepare pinned four-platform CPU runtime builds and a real Linux isolation preflight;
  neither workflow definitions nor synthetic checks count as model qualification.

- Measure prompt processing, grammar setup and generation separately on the fixed CPU
  pack, and allow explicit `threads`/`threadsBatch` in local-pack profiles and
  `ManagedPackClient`, forwarded as pinned llama.cpp thread flags and recorded in the
  model identity; defaults are unchanged.
- Derive table column candidates and observed data rows programmatically for the
  regional protocol (prompt v12, planner v7, compiler v7): the interpreter names,
  types and scopes columns but does not invent column indices; one property per
  column index is enforced, data rows left outside every repeat are reported as
  `table_rows_outside_repeat`, a table's own caption is read with the table, and
  token spans are no longer offered as value choices.
- Withhold declared header cells and captions as value choices, carry header text in
  column candidates, attach the declared headers above each column to its definition,
  and report misplaced or missing header citations (prompt v13, planner v8, compiler v8).
- Drop a scalar field that re-reads a cell already read as a record value by a repeat
  column: the program-enumerated record reading is kept, the drop is recorded on that
  cell's accounting entry as `redundantFieldIds`, and meanings that pointed at the
  dropped field go to the existing scope repair instead of a whole-region repair
  (compiler v9). Subtotal, note and header rows are not affected.
- Degrade a `present` or `blank` field that names no source binding to `uncertain`
  with a `field_binding_missing` report instead of invalidating the whole regional
  response (compiler v10); unknown binding identifiers still invalidate it.
- Drop a source-less scalar field defined only by declared header cells of mapped
  repeat columns, recording it on the header's accounting entry (compiler v11), and
  decide scope statements that share a source node in separate requests.
- Decode the managed interpreter greedily with a recorded seed; cloud profiles can set
  explicit `sampling` parameters, recorded in the transport identity.
- Treat declared header cells as definitions in the regional protocol (prompt v11,
  planner v6, compiler v6): header text is not a required value candidate, reading it
  as a field value is reported as `header_cell_bound_as_value` and repairable, and
  repair feedback follows the shared request prefix.
- Factor repeated regional JSON Schema constraints without relaxing validation;
  project homogeneous table cells as lossless columns/rows only in internal model
  input. Version prompts/planning for checkpoint compatibility; public observations
  and result contracts retain their original object form.

- Separate native observations, structural relations, compact semantic decisions and
  deterministic source-bound result generation; preserve public v1 analysis/results.
- Add Markdown syntax, HTML forms/merged tables, exact text spans, native scalar/cache
  provenance, and optional isolated CPU PDF layout/table/OCR observations.
- Interpret bounded regions; expand actual repeat rows, connect confirmed continuations,
  retain unresolved scopes and stop unchanged repair loops.
- Constrain source-reference choices, derive exact unique delimiter bindings and bound
  content accounting programmatically, and retain safe repair diagnostics across pauses.
- Partition oversized non-table regions into source-addressed views, retain single
  ownership of value bindings and track incomplete node views across resume.
- Reduce model-facing contracts to reachable definitions and require explicit field
  types without changing historical public result contracts.
- Preserve typed OCR source cells and opt-in ruled-table repair observations separately;
  do not replace original tokens, turn recognition holes into blanks, or hide conflicts.
- Repair unresolved same-region semantic scopes in bounded independent batches,
  preserving valid sibling decisions and reusing committed regional interpretations.
- Add explicit cell-level ruled-table OCR with original glyph crops, actual canvas
  coordinate scaling, durable per-cell budgets and a provenance-preserving structure view.
- Prepare a no-GPU/no-network Linux qualification override with an explicit memory and
  swap ceiling; this configuration is not an executed platform qualification.
- Preserve original recognition indices while giving structure-view copies explicit
  reading-order indices, so downstream layout sorting cannot undo the correction.
- Size table views using the actual request contract and share identical reference
  constraints instead of rejecting small rows with a fixed schema reserve.
- Keep declared multi-row headers and their geometry with sliced data rows; exclude
  context-only table bindings from scalar value choices while preserving their owners.
- Add stage checkpoints, explicit cumulative budget grants, one-worker SQLite jobs,
  authenticated byte-stream HTTP, and CLI/MCP job controls.
- Add verified offline runtime/model pack installation, activation/rollback, CPU llama.cpp
  management, separate model preparation and internal Linux container configuration.
- Add public integration/operations/security/support documentation, stronger release
  qualification, dependency SBOM/audit and workflow build provenance.
- Raise cryptography to 50.0.1 or later in its 50.x line following
  [the upstream PKCS#7 advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-g6cj-pr64-35w5).

Implementation tests are not actual model, operating-system or client qualification.
Do not promote this candidate or switch Toolkit/Sync consumption until the complete
release gate passes. GPU/remote model access, automatic downloads and implicit updates
are not required or activated by these changes.

The Apache-2.0 engine history originates in Personal Agent Toolkit. Original history
and notices remain in Git and NOTICE; this independent candidate does not rewrite them.

Candidate diagnostics follow-up:
- Separate managed CPU runtime preparation, context checks and server-exchange timing;
  preserve only whitelisted numeric server metrics and sanitized checkpoint diagnostics.
- Track calls without complete usage receipts so reported zero token subtotals cannot
  be mistaken for zero consumption after timeout, interruption or legacy-client calls.
