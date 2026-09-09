# Changes

## 1.8.0 — independent product candidate (not released)

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
