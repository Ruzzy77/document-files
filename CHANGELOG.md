# Changes

## 1.8.0 — independent product candidate (not released)

- Split record-table structure and meaning into finite checkpointed stages; preserve
  compiled values on meaning failure and reject incompatible old checkpoints
  (prompt v16, planner v10, table protocol v2, checkpoint v2).
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
