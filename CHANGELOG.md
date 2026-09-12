# Changes

## 1.8.0 — development version (not released)

### Document extraction

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
