# Extraction engine: implementation reference

This document explains the current processing rules and the code that owns them.
It is not an experiment diary or a quality certificate. Read [current defects](../SUPPORT.md)
before treating a compiler correction as safe. Public calls and result examples are
in [the API reference](python-api.md); model setup and jobs are in [operations](operations.md).

HWP/HWPX and XLSX are the primary completion targets. Existing PDF processing below
remains documented, but broad PDF/scan qualification is no longer a prerequisite for
finishing those formats. The KPI is [consistent structure and understanding across
varied forms](product-architecture.md#structural-completeness-and-document-understanding),
not a separate reconstruction feature.

## 1. Observation, source bindings and region ownership

`document_model/observe.py` builds an `ObservationDocument` from caller-owned bytes.
Native parsers preserve nodes, character ranges, tables, row/column/span geometry,
values, formulas, cached values, explicit references and uncertainties. Observed
text is not replaced by normalized model text. `interpretation/bindings.py` and
`compiler.py` read values from program-issued binding IDs, never from a model's
replacement value or JSON pointer.

`interpretation/regions.py` assigns value ownership. Headers, captions and adjacent
context can define or explain a value without becoming another scalar value choice.
A table owns its caption. Declared header cells and lexeme spans are excluded from
ordinary value candidates. Context-only bindings cannot become values in a second
region. Unknown or conflicting observations remain visible.

For each column, `columnCandidates` contains program-derived header references and
text, while `dataRows` describes observed row positions. Header-group candidates
come from actual geometry and mapped child columns. Choosing a group expands to
those existing columns; it neither invents a new data group nor infers a unit.
Definitions receive their own provenance, separate from cell-value evidence.

Oversized non-table regions use disjoint source views with original Unicode ranges.
A declared label/value pair stays atomic and each binding has one owner. Adjacent
text is context only. A source node is fully read only after all its owning views
are accounted for. An indivisible value or required context that cannot fit remains
pending, not truncated. Table slices keep declared leading headers as context;
headers do not become a fabricated standalone data region. OCR header predictions
are not native declarations.

### Native projections and unresolved document roles

The XLSX parser's readable node text includes a generated coordinate prefix, such
as `A2=Alice`; its actual typed cell value is separately stored in `semantic.value`.
`native.py::bind_spans` preserves the readable node, but derives label/value and
lexeme candidates from the exact native string at `/semantic/value/value`. Ranges
therefore address original cell content, not a normalized display. An actual cell
containing `A2=Alice` still has a genuine delimiter candidate; a cell containing
only `Alice` does not. Unicode, embedded whitespace, multiple clauses and empty
inner values retain their original offsets. Non-string cells retain native number,
formula and cache bindings without treating display punctuation as extra fields.

Region plan v16 invalidates checkpoints whose candidate IDs used the display-derived
boundaries. Required-candidate accounting is unchanged: real inner fields remain
required, and missing values are not excused by this correction. Public observation
nodes, exact numeric spellings, formulas and caches are not rewritten.

The simple HWPX development case also retains a title and caption as two generic
scalar fields. Native observations, interpreted document roles and business data
need separate handling; identical text alone cannot identify redundant occurrences.
These are current implementation gaps, not the intended structure contract. See
[current native checks and priorities](../SUPPORT.md).

## 2. PDF recognition and optional visual reading

### CPU observation

`document_model/docling_adapter.py` and `docling_pipeline.py` use the isolated
Docling Standard PDF path: Heron layout, TableFormer accurate and explicit Tesseract
Korean/English recognition. Native PDF structure/text remains available. Completed
recognition pages can be saved and reused without rerunning completed OCR.

Coordinate records link the actual render/crop, optional rotation or padded OCR
cell image, serialized one-page PDF and original page. Source/page/profile hashes
are checked on import. Synthetic padding has no original support. Parser/PDFium
page dimensions may differ only within the implemented binary32 comparison and
0.001-canvas-pixel tolerance; nonzero origins, crop, rotation and source checks remain.
A valid transform does not verify OCR text or reported recognition rectangles.

The optional `ruled_tables_v1` policy removes proven long ruling strokes only from
a separate OCR crop. `ruled_cells_v2` instead uses closed-cell geometry, original
ink bounds and explicit single/multiline OCR segmentation. Original OCR, transformed
input and conflicting readings remain separate. An `o` is not silently changed to
`0`. No ink, no OCR output, failed recognition and unsupported cells are distinct.

Cell geometry may also be measured from already cached page pixels when repair is
off. Missing cache or exhausted budget remains unavailable; this does not authorize
another render or OCR call. Upright table orientation needs a unique matching OCR
crop on that page, not a later caption's orientation. Structure ordering uses copies
with new indices mapped to original OCR indices, so a downstream index sort cannot
silently undo geometric reading order.

Raw OCR retains the complete TSV, input-page index and exact per-image transform.
File-list repair batches at most two compatible images (same table/page/language/PSM).
`repairBudget` keeps separate limits: `batchSize` 1–2, `maxImages` 1–64,
`maxInputPixels` up to 64,000,000, `maxCalls`, `maxSeconds` and per-crop `maxPixels`.
Defaults are 1 image/batch, 8 images, 16 million input pixels, 8 calls and 60 seconds;
batching does not multiply the other budgets.

### Pixel evidence is not semantic approval

The full-render inventory counts all non-white pixels before bounded connected-
component analysis. Low contrast, page edges and omitted geometry stay explicit.
Default bounds include 16 million pixels, 65,536 row runs and 4,096 components.
Two-way visual correspondence links native glyph/raw-OCR candidates to pixel
components, retaining missing, ambiguous and partial matches. Its default bounds
are 8,192 source items, 1,024 observations per kind, 262,144 bbox comparisons and
8,192 candidates. A text box enclosing pixels does not prove those pixels are text.

A repeated OCR token can match through a unique complete horizontal line sequence,
with original text, TSV line/word identity and coordinates rechecked. Cross-line,
normalized, overlapping-table and ambiguous duplicate matches are not accepted.
These records are provenance, not model payloads or evidence of complete reading order.

Native empty-cell proof is deliberately narrow: a complete supported native PDF
object inventory, four unique closed borders, no touching content and a fully
opaque-white interior. Embedded font mapping and glyph outlines must be verified;
a missing outline alone is not an empty glyph. Unsupported objects, transforms,
fonts or geometry block the proof. Original gap issues remain in provenance.
A proven empty string is not an absent, zero or not-applicable business value.

Native-ruling consumption similarly requires original pixel/line evidence, an exact
raw OCR link and a unique closed grid. A verified ruling artifact keeps its original
text/binding but is excluded from data candidates with the disposition recorded.
Neither ruling consumption nor an empty-cell decision establishes page completeness.

### Visual review and alternative tables

With an explicitly selected compatible vision pack, `pdf_visual_runner.py` reviews
pages before semantic region planning. The plan inventories pixel units, missing
slots, source/grid checks and observed block order. Decision objects are keyed by
issued IDs; labels are restricted by each item's evidence and `unknown` is available.
`onlyNativeRulePixels` permits a native-rule label only for pixels supported by a
complete native object inventory; it proves neither text nor blankness.

`pdf_visual_grid.py` measures all required horizontal/vertical bands. Missing or
ambiguous geometry blocks the request rather than widening tolerance. A contrast-
core grid may provide geometry if the all-foreground attempt fails, but faint or
unclaimed pixels remain in the complete inventory and still require review.

`pdf_visual_display.py` supplies unchanged RGB detail plus labeled exact membership
masks for mixed text/rule units. Black in a mask means membership, not original ink.
Each panel has its own original-pixel mapping; there is no false single affine for
the composite sheet. The original RGB, PNG/encoder identity, panel recipe and every
unit's runs are hash-bound. Missing panels, changed references or pixel/byte/time
overflow stop preparation. Displaying a mark does not approve its interpretation.

When review remains unresolved, `pdf_image_read.py` can make one additional literal
reading attempt, split into bounded requests within the same document allowance.
Measured cells are read from page/detail images. Existing visual text lines use
lossless one-line strips in entry order, up to twelve per labeled line sheet. All
parts must fit the remaining call budget before dispatch. The model gets pixels
and bounds, not original OCR strings or prior answers. It returns one literal entry
per candidate; `text` requires characters, and `empty` is offered only for fully
displayed measured cells and requires an empty string. Uncertainty may retain a fragment.

Readings first appear as `pdfImageReadCandidates` requiring text/structure review.
They do not overwrite observations or populate final data by themselves.
`pdf_image_projection.py` proposes only uniquely matched, non-overlapping grids and
line groups. Each new source retains literal text and original capture/reading IDs.
No header role is inferred; an empty reading alone cannot create a blank value.

Every proposed string/grid and the page pixels/order require review. All pages must
pass before `pdf_visual_apply.py` atomically selects alternative regions or adds
reviewed blanks. Original tables/nodes/OCR stay intact. Only a complete source-bound
replacement grid may resolve the exact inactive table's missing-cell issue, with
old/new table IDs and fingerprints recorded. Other unknown detections, extent errors
and semantic failures remain. `ocrTruthVerified` and independent quality approval
are not set true by the model's agreement.

Page attempts are saved before dispatch. Reviewed pages can resume; completed,
failed, interrupted or unknown attempts are not silently replayed. Image data URLs
are not stored in checkpoints. Preparation and application recheck image/source/
membership identities; missing or older application policy prevents resume.

## 3. Record tables, scalar forms and content review

`table_protocol.py` classifies the structure first. Every observed non-fixed row
needs one explicit role; unknown, duplicate or omitted row decisions fail validation.
Only wholly native-declared header rows are fixed without a model decision. A record
structure compiles before its meanings are interpreted. Missing cells are not shifted,
filled or invented. Decimal strings are validated lexically, without float conversion.
Unsupported locale/unit-bearing text stays uncertain with its original spelling.

Subtotal, note and unmapped value cells use a separate scalar child region with
`parentRegionId` / `tableContextRef`. `valueRoutes` records each cell's owner; the
child cannot regenerate records. Its context contains relevant headers and nearby
text, not every already compiled record value. An unresolved mapped value remains
in its original record. Label/value forms keep the existing scalar path.

The meaning stage first selects every owned source as `has_meaning`,
`no_additional_meaning`, `unresolved` or `unreviewed`, with a short reason. A literal
empty source cannot select `has_meaning`. Plain data-cell values need not be repeated
as meanings; this is a model decision, not a blanket rule that numeric text has no note.
Validated `sourceSelections` bind source inventory, frozen structure, model identity,
reference dictionary and revision. A stopped detail call reuses the saved selection.

Detail replies contain meanings, exact owned-source quotes, status and remainder
reviews, **not applicability**. `scope`, target IDs and row bounds are rejected in
this active response contract. The compiler inserts unresolved applicability, and
each retained meaning must then pass through the separate scope phase. Context may
explain a quote but cannot be its direct evidence. Unicode ranges are program-resolved;
ambiguous, invented or mismatched quotes fail. One meaning can cite several sources
without being repeated. Every selected source needs a remainder review even when
quotes cover all its text; unresolved/unreviewed material stays pending.

A detail response may explicitly revise the selection, with the previous selection
hash and a reason. Accepted meaning revisions can correct, split, merge or withdraw
content but must account for changed IDs and preserve source coverage. Reviewed
sources cannot silently revert to unreviewed. `baseRevision` is supplied by the
program; transition reasons and source inventories participate in revision hashes.
Changed explanations alone are not progress. Uncertainty can be a valid correction.

Structure permits two attempts. Meaning uses two initial dispatches, including
selection and detail, plus one post-acceptance review. These are ceilings inside
the cumulative document budget, not additional budgets. A failed first detail after
selection may exhaust the stage. Explicit grants preserve already consumed usage.
`phaseUsage`, attempts, revisions and `inputPreflight` make unfinished work visible.
`structure_compiled` is a retained partial result, not a completed document.

## 4. Value compilation and redundancy rules

`compiler.py` validates candidates, reads bindings and emits schema/data/evidence.
Unknown binding IDs invalidate a proposal; a present field without a binding is
retained as uncertain with `field_binding_missing`. A single unambiguous, delimiter-
declared label/value pair can support the documented legacy binding derivation;
the compiler does not search elsewhere for an equal-looking value.

Redundancy handling is source-sensitive. Record-cell scalars, mapped header-label
fields, a second field over the same binding, and redundant label/whole-line reads
can be removed with `redundantFieldIds` or `programCorrections` recorded. Matching
spelling alone is not sufficient to remove a legitimate repeated value. Context-only
meanings and unsupported heading definitions can be dropped; original nodes remain.
Accounting proves what was processed, not whether a model interpreted it correctly.

Column definition citations never promote a content row to a header. Only the
shared `fixed_header_rows` rule can correct a content role: every observed cell in
that row must be a native-declared header. The correction is recorded as
`native_header_row_role_corrected`. Recognition predictions, mixed header/value rows
and the presence or absence of numeric text do not fix a row's role; an explicit AI
header decision remains possible.

Citations of data/subtotal/note cells are dropped when other definition citations
remain. If every citation conflicts, `column_definition_conflicts_with_content`
marks the proposed column definition and its schema evidence uncertain while the
compiler retains the row roles and bound values. The table structure stage asks for
repair rather than freezing that proposal; exhausted repair stays partial. Correct
source citations and actual value evidence remain separate checks. Compiler v27
invalidates checkpoints made with the earlier citation-driven row rule.

## 5. Cross-page relations and applicability

`regions.py` proposes bounded table relations; `interpretation/engine.py` obtains a
relation decision. `continue` appends later rows in order. `duplicate` treats another
presentation as provenance for existing rows, refusing different compiled rows with
`table_duplicate_rows_differ`. `separate` preserves distinct tables; unresolved
relations remain visible. Exact repeated fields/meanings can fold into prior ones
with their additional sources retained, including a later meaning covering several
earlier meanings. The latest continued-table case still has a duplicate condition.

`rightRepeatsLeft` reports equal cell text at equal row/column positions, not
record identity. Prompt v31 keeps `continue` available for equal-valued records and
also permits a `duplicate` proposal for exact text matches. The model must distinguish
additional occurrences from another presentation using source context; ambiguous
identity stays unresolved. `duplicate` still requires equal compiled rows before
folding, and continuation retains each appended row's own value binding. Earlier
prompt checkpoints cannot silently resume under this policy.

After pointer remapping, `integration.py` prepares unresolved applicability tasks.
Targets are existing standalone fields, records, row/column intersections and
geometry-derived header groups. `scope_selection_wire.py` presents one selection
list with shared column handles; selecting a standalone column covers its data rows.
The model selects targets, never data or destination pointers. A meaning's own text
is not a target. Meanings from the same source are decided separately so a caption's
unit and condition do not inherit one another's scope.

`scope_source_binding.py` independently verifies every selected target's bindings.
The fingerprint includes source quotes/context, target mapping, header membership,
execution policy and source trace. Resume recomputes that trace against current
compiled observations rather than trusting a saved success flag. Missing/truncated
context or bounded-away candidates cannot become a complete decision. Valid siblings
survive an invalid selection, and unchanged unresolved work is not repeatedly called.

Managed applicability requests use bounded reasoning (2,048 tokens within 3,072 total
output tokens, or a smaller compatible client ceiling). Table-relation requests use
1,024 / 2,048. Other phases retain their profile policy. Greedy sampling is explicit
but does not promise bit-identical output across floating-point execution orders.

Current long-source limits remain 100 source references, 1,000 trace entries and
1,000,000 row/column work units. They can prevent long-table completion. Planned
compaction must still check every binding, preserve public evidence and detect row/
column/value changes on resume; raising limits or dropping the tail is not the fix.

## 6. Request sizing, checkpoints and result validation

Model-only compaction shares repeated JSON Schema definitions and optionally uses
`columns-rows.v1` for homogeneous cell objects. One wire row is one cell, not one
business record. `table_reference_wire.py` uses reversible short handles for source/
table references only, never literal text, values, semantic IDs or JSON pointers.
Activation compares actual request sizes; the dictionary stays in checkpoint identity.
Detail reference context carries text, role and a differing native value without
repeating stored per-node geometry. Canonical observations/provenance are unchanged.

Preflight measures the serialized system, payload and output contract; tokenizer
context checks are separate. Overflow preserves compiled work without a model call.
The llama.cpp grammar adapter omits only large array cardinality bounds from sampler
grammar to avoid a pinned upstream expansion failure. Full contracts and post-response
checks still enforce those bounds. This is not arbitrary JSON Schema support.

Saved identity includes input bytes, observations, program versions, active packs,
profile policy, reference mapping, revisions and selected source traces. Incompatible
checkpoints are rejected, not force-migrated. Saved public results remain readable.
Budget expiry preserves committed work and explicit unread areas. Job lifecycle,
extraction completeness, schema validation and independent accuracy are distinct.

### Current internal identities

These identify the implementation audited at product commit `8744a5c`; update the
relevant row when changing its owning behavior. Do not patch stored IDs to resume.

| Contract | Version |
|---|---|
| Semantic prompt / region plan / result compiler | v31 / v16 / v27 |
| Table protocol / table reference wire | v20 / v2 |
| Scope integration / scope-axis protocol | v13 / v6 |
| Scope selection wire / source binding / regional checkpoint | v2 / v2 / v3 |
| Recognition adapter | 29 |
| PDF review / unit display / visual application | v16 / v2 / v4 |
| PDF image reading / review images / line sheet | v5 / v2 / v1 |

## 7. Where to investigate a failure

| Symptom | Start with | Existing regression families |
|---|---|---|
| Lost/duplicated record or header | `compiler.py`, `table_protocol.py`, `regions.py` | `test_table_duplicates.py`, table compiler/protocol tests |
| Wrong unit/condition or source trace | `integration.py`, `scope_protocol.py`, `scope_source_binding.py` | scope selection/binding and semantic integration tests |
| Wrong blank/OCR/pixel interpretation | `document_model/recognition_*.py`, `interpretation/pdf_visual_*.py` | recognition observations, PDF review/application tests |
| Stale resume or exhausted stage | `interpretation/engine.py`, `workflow.py`, `jobs.py` | checkpoint, budget and managed-job tests |
| Runtime mismatch/context failure | `profiles.py`, `runtime_packs.py`, `backends.py` | runtime pack, local model and inference-policy tests |

Start from the saved failing input/response and exact source identity. Add a narrow
regression, then use the affected real document only if model behavior changed.
Independent accuracy review still needs a new document after a failure informed a fix.
