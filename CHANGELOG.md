# Changes

## 1.8.0 — development version (not released)

### Document extraction

- Preserve native merged-column geometry by mapping public `columnSpan` to internal
  `colSpan` (native observation adapter v2). Reject prior native-geometry checkpoints.
  Retain stored XLSX empty XML cells without inventing implicit gaps or covered merge
  cells (XLSX extractor v10). Empty strings, whitespace and uncached formulas remain
  distinct; original files, values and public v1 contracts are preserved.

- Compact long applicability context and row-boundary lists without removing rows,
  exact source text, geometry or missing properties (scope-selection wire v3).
  Preserve original selection contracts and compiler-bound provenance; include the
  decoding instruction in request sizing and checkpoint identity. The 50-row
  delivery regression now fits the existing input limit. Larger candidate/source
  bounds and actual-model long-document quality remain open.

- Add optional program-issued numeral locations for frozen native numeric fields
  (native-structure v12 / native-value-batches v3). Show exact original context for
  repeated equal values; regenerate selectors through unchanged quote, type and row
  checks. Preserve original precision, blank bindings and source accounting. Under
  delivery pressure, drop only the whole optional aid and retain ordinary choices,
  all source and the fixed budget; validate that delivery limit on checkpoint resume.
  Scripted regression coverage is not real-model or independent quality approval.

- Fit explicit native structure review by sharing property names in prior object
  arrays (native-structure v11 / revision v4). Preserve all previous decisions and
  source context, keep the canonical checkpoint unmodified, and retain complete
  entity/source checks. Explicitly review extraction correctness on the same source,
  not changes to that source. Clarify that value quotes select the exact inner value,
  not a surrounding citation sentence; this does not establish correct occurrence
  selection or approve model quality.

- Share repeated native source metadata across role, structure, value, accounting
  and revision requests without trimming original text, formatting or XML references
  (document protocol v7 / native-structure v9). Keep per-source text/role/range explicit,
  preserve exact JSON types, and include decoding cost in planning and dispatch.
  Source observations and public contracts remain unchanged; changed internal
  identities reject prior checkpoints. This reduces artificial context splitting,
  not the work needed for cross-region continuity or independent quality approval.

- Add one explicit source-grounded native structure revision cycle after exhausted
  value reading (native-structure v8 / structure-revision v1). Validate a complete
  old/new entity ledger and the replacement before changing current state. Preserve
  prior partial results on failed/unknown reviews; invalidate and reread values,
  batches/accounting and applicability after accepted changes. Revalidate both the
  retired base and current transition on resume, preserving all call/time costs.
  Review requests losslessly factor source metadata and keep the same document budget.

- Remove impossible native binding choices using the original scalar reader and
  shared occurrence-ownership check (native-structure v7). Keep source observations,
  required accounting, exact quote alternatives and unresolved frozen fields intact.
  Structural repair now identifies schema-owned paths/types without echoing raw values
  or unknown property names; neither mechanism certifies semantic correctness.

- Partition oversized native value requests without trimming original source or
  formatting (native-structure v6 / native-value-batches v1). Preserve the complete
  frozen structure, compile accumulated reads, then account for remaining candidates
  without excluding consumed bindings. Keep per-batch usage and source-bound checkpoint
  replay; completed batches are not rerun and unknown exchanges require explicit resume.
  Scripted HWPX reading covers 32 fields in eight calls at the existing input limit;
  this is not an actual-model quality claim or a new document budget.

- Identify invalid native value handles and required present/blank states in bounded
  repair (native-structure v5), rather than reporting a valid JSON selection error
  as invalid JSON. Preserve closed contracts, source privacy and frozen structure;
  local repair/checkpoint regressions are not actual-model quality approval.

- Share identical native value-choice schemas without loosening their source/status
  constraints, remove redundant inner handle IDs, and keep unread-skeleton placeholders
  out of the initial value request (native-structure v4). Preserve original text and
  formatting. Scripted HWPX extraction now runs at the same 16,000-character limit.
  The v3 Spark comparison preserved two rows but failed whole-result quality; measured
  v4 request compaction is not actual-model approval.

- Compact native structural responses without removing semantic decisions: program
  IDs, exact whole-block/partial-quote anchors, ordered column states and explicit
  source overrides (native-structure v3 / wire v1, prompt v39). Preserve raw and
  expanded decisions with hashes and bounded expansion; revalidate both on resume.
  Share record-occurrence context in value requests. No output-budget increase or
  actual-model quality approval is implied.

- Preserve specific source-quotation errors in native structural repair rather than
  hiding meaning-quote failures behind generic contract feedback. Native-structure
  v2 invalidates old stage checkpoints; the correction has local regression coverage
  but does not establish whole-result model quality.

- Separate native HWP/HWPX semantic structure from value reading in protocol v6
  / native-structure v1. Freeze source-grounded fields, types, repeated occurrences
  and missing states before issuing exact value handles; preserve them on value
  failure and validate both stages on resume. Compiler v32 / prompt v38 invalidate
  previous checkpoints. Meaning quotes retain separate applicability decisions.
- Plan native source regions with actual role/structure request sizes (region plan
  v20), retaining source windows and hard dispatch limits. The value request cannot
  discard committed fields/rows or change numeric types to avoid a read error.
  Explicit semantic-structure revisions and cross-region logical continuation remain
  open; regression success is not actual-model quality approval.

- Show program-resolved `exactText` beside native text candidates in protocol v5;
  preserve whitespace, precision, blanks, source windows and original observations.
  Native type-error feedback identifies the failed offered binding/type without raw
  source in diagnostics. Prompt v37 rejects previous checkpoints. Actual model
  field/record selection remains a separate, unapproved quality check.

- Constrain native value sources to one binding, exact quote or missingness branch
  in protocol v4, including independent decoding when a backend ignores grammar.
  Compiler v31 folds only identical source/definition/destination aliases and keeps
  their meaning scopes; shared source alone no longer deletes a later field. Prompt
  v36 rejects old checkpoints. This is not approval of model field selection.

- Add exact-source quotation grounding and logical records for HWP/HWPX native text.
  Compile ordered values, explicit blanks/missing states and field/row scopes without
  inventing native table geometry. Preserve source observations, public evidence and
  grounding coverage; rebuild accepted quotes on resume and reject incompatible
  compiler v30 / prompt v35 / native protocol v3 checkpoints. Scripted product-path
  regressions pass; actual complete extraction and cross-region logical continuation
  are not yet approved.

- Separate original observations, structural interpretation, exact source-bound
  value compilation and semantic relationships behind unchanged public v1 APIs.
- Add region ownership, bounded long-text views, declared header/column candidates,
  group-header scope candidates and exact lexical numeric handling.
- Separate record-table structure, source selection, content and applicability;
  preserve compiled rows across bounded repairs and explicit-budget resume.
- Preserve text-only data, subtotal and note rows when model column citations
  conflict. Only native-declared header rows can be corrected automatically;
  content-only definitions request bounded repair. Compiler v27 rejects old
  checkpoints from the citation-driven rule. Source bindings and precision remain.
- Keep continuation available for equal-valued records in prompt v31. Matching cell
  text enables a duplicate proposal but is not record identity; contextual ambiguity
  remains unresolved. Preserve the compiled-row check and per-page value bindings.
  Other quality gaps remain in [SUPPORT.md](SUPPORT.md); these rules are not
  independently quality-approved.
- Derive spreadsheet label/value candidates from exact native cell strings, not
  generated coordinate/display text. Keep real inner fields, empty values and native
  number/formula/cache bindings. Region plan v16 rejects incompatible candidate IDs
  in old checkpoints; missing-value accounting is not relaxed.
- Add source-linked HWP/HWPX document roles and logical outline separately from
  native observations and business data. Require owned-role decisions, exact text
  bindings, heading levels and explicit caption links; reject title/value conflicts
  without deleting values. Preserve uncertain/missing/fragmented structure as partial.
  Version compiler v28, prompt v32 and region plan v17 for checkpoint safety.
- Preserve HWPX paragraph/run formatting as source-linked XML evidence without
  inventing logical roles from typography. Keep conditional properties unresolved
  and mixed runs distinct. Prompt v33 and region plan v18 separate native containers
  from logical roles; constrained role/level/target alternatives reject invalid
  combinations before they consume a repair call. Old checkpoints cannot resume.
- Separate native document-role decisions from immutable-role value/meaning work.
  Preserve roles through content failure, verify bounded source-linked table context,
  and retain exact inner values in structural text. Track stage usage and resume
  identities without replaying unknown calls or increasing the original budget.
  Prompt v34 / region plan v19 / compiler v29 identify the new stages. Native
  protocol v2 uses bounded thinking for managed role/content requests; other clients
  retain their configured behavior, with no extra document allowance.
- Add isolated CPU recognition and source-bound PDF visual review, literal image
  reading, alternative table projections, exact pixel membership displays and
  conservative blank-cell application. Original OCR and document bytes remain.
- Preserve HWP checkbox semantics and existing reading, editing and conversion paths.

### Runtime and integration

- Add explicit offline runtime/model/recognition pack install, activation and rollback.
  Support CPU llama.cpp and a separately declared Linux CUDA runtime for DGX Spark.
- Add compatible vision projector packs, greedy managed sampling, explicit thread
  settings and phase-specific bounded reasoning. Do not download or switch models
  during document processing.
- Add one-worker durable SQLite jobs, private snapshots, authenticated byte uploads,
  cancellation, paged result reads and cumulative-budget resume across CLI/MCP/HTTP.
- Record execution identity and sanitized timing/usage diagnostics. Unknown token
  usage is not reported as zero consumption.

### Packaging and development

- Document the independent plugin and embeddable Python package as two delivery
  forms of the same engine. Remove application-specific references and sibling
  runtime paths from shipped documentation, source comments and the Skill.
- Include the declared CLI/MCP launchers in host-source bundles and generate each
  Skill's invocation for its own layout. Add public byte-stream and relocated-bundle
  regressions without changing the extraction or result contracts.
- Add clean-source candidate builds, target-aware ARM64/x64 packaging, offline input
  inventories, SBOMs, source/license records and exact-byte release qualification.
  Packaging success is not model quality or redistribution approval.
- Prioritize consistent HWP/HWPX and XLSX structure and understanding across varied
  forms. Reconstruction illustrates structural sufficiency, not a separate writer
  requirement. Use Spark for internal AI and then verify personal Mac use;
  expand to Word/PPTX afterward and Google documents
  later. Defer other-platform qualification, formal publication and application upgrades.
- Use `main` as the normal working branch. All GitHub workflows are manual-only;
  platform workflows operate on one explicitly selected target, defaulting to ARM64.
- Replace chronological status accumulation with topic-based architecture, extraction,
  API, operations and readiness documents. Earlier development history remains in Git
  and private raw run evidence; no historical failure has been relabeled a success.

Detailed internal protocol identities and behavior are maintained in
[the extraction-engine reference](docs/extraction-engine.md), not repeated here.
Original Git history, NOTICE and third-party conditions remain. Downstream
applications have not been upgraded.
