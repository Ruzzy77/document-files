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

### Table metadata and evolving request context

`interpretation/table_source_wire.py` shares repeated properties in table structure
requests. Every node ID and its text remain explicit and in order. Same-shaped native
metadata uses shared properties and nested-key columns; equal columns can share one
exact value without merging source nodes. Cell and relation lists use the same typed
record encoding when it saves space. Missing keys, null, empty strings, whitespace,
numeric types, arrays, formatting and source-cell geometry survive exact expansion.
`expand_table_sources` is an inspection helper for product-created requests, not a
decoder for model output. The original observation and compiler inputs are unchanged.

Savings include the decoding instruction. Planning and dispatch both measure the
messages produced by `contract_messages`; a shorter JSON payload alone is not enough.
The public response contract is unchanged. Table protocol v21 and region plan v21
invalidate checkpoints made with the previous display and planning behavior.

The first region's column mapping may not exist when the initial plan is made.
Before later **unstarted** table work, `add_table_definition_context` retains that
accepted mapping's actual definition nodes and cell geometry as context. It does not
guess which physical row is a header, set native header flags or add value owners.
This keeps a header below a title available in later slices without treating the
title as that header. `sameTableMapping` remains a prior model decision, not a native
declaration or proof of semantic correctness.

`replan_table_region` measures this current context and splits complete observed rows
again if needed. Child regions keep the original table identity and inherited header
and leading context. The engine saves the replacement plan, rebuilds continuation
candidates and leaves earlier accepted regions untouched. Work with model attempts or
accepted structure is not silently replaced. An indivisible row/context remains an
explicit partial failure; there is no source truncation or automatic budget increase.
Both partial and completed checkpoints have regression coverage for no replay of
accepted rows. This does not fix the remaining model, applicability or empty-fragment
evidence errors.

### Native spreadsheet projections

`extractors.py` (XLSX adapter v10) uses bounded worksheet XML observations to tell
an explicitly stored empty cell from an implicit OpenPyXL grid gap. Bare empty cells
retain `kind: blank` and exact empty `raw`; declared empty strings retain string type.
No zero, null or the word `None` is substituted. A formula without a cached result is
still a formula, and whitespace remains original text. `xlsx_empty_cells.py` indexes
merged rectangles without expanding them; empty covered positions are not independent
cell values. XML/cell/output limits remain in effect, and partial metadata is not proof
of a missing or blank value. Original files are unchanged.

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

Native observation adapter v2 maps public `semantic.cell.columnSpan` to internal
`colSpan`; the earlier spelling mismatch collapsed HWP/HWPX/XLSX merged widths to one.
The legacy `colSpan` key is only a fallback when the public property is absent.
Row/column spans and all source-cell metadata remain separate from header-role guesses.
Native node dictionaries are not rewritten, and the backend version is included in
observation provenance and extraction checkpoint identity. Old native checkpoints cannot
reuse geometry from the previous adapter; public observation/result schemas stay v1.

### Logical document outline (HWP/HWPX)

`interpretation/document_outline.py` adds `document.outline` without repurposing
`documentSchema` (which validates the original node map), changing native nodes or
turning `semanticAccounting` into structure. Other formats retain their existing
native structure; they do not yet receive this logical-block interpretation.

For each owned non-cell text source, the scalar interpreter must choose a document
role and certainty. Titles use outline level 0, section headings use levels 1–12;
other roles have no heading level. Default native paragraph levels/styles do not
establish logical headings. Caption links select nearby observed table identities,
not model-created table names. Prior interpreted headings are context only; their
text counts against the actual request budget and cannot become newly owned data.
Native HWP/HWPX captions keep their own role/meaning region instead of being consumed
a second time by table interpretation.

For HWPX, `hwpx_structure.py` preserves directly declared paragraph alignment and
character height, color and bold/italic flags under `sourceStructure.formatting`.
Paragraph/run references and original header/text XML addresses identify each
property; height is an unconverted source value, not a rendered measurement.
Mixed runs remain distinct. The addresses cover source XML before text normalization,
not offsets into normalized text or an individually split long view. Conditional
format branches remain explicitly unresolved; they are never flattened together.
Run metadata is bounded to 256 entries per emitted segment, with a visible issue
when exceeded or a referenced character definition is absent. Text is not discarded.

In the private role request, `nativeRole` identifies the source container rather
than a completed semantic decision. A default-style paragraph can still contain a
logical title or caption. Formatting, wording, order and table context are evidence
for the model, not deterministic title heuristics. The decoder contract separates
role/level/target alternatives: ordinary paragraphs cannot carry heading levels,
and an interpreted caption must identify an offered table. The compiler repeats
these checks for clients that do not enforce the decoder grammar.

Code preserves exact `textBinding` ranges, original occurrences and native unit
order, builds section parents from interpreted levels, and retains native table
and nested-cell membership. `captionOf` relations connect a caption element to its
observed table element. Running headers/footers do not change section ownership.
Whole title/heading/caption copies claimed as value fields are rejected for bounded
repair, not silently deleted. Exact inner value candidates can coexist with the
structural role; required accounting still applies. Genuine fields, notes and applicability still use
the existing value/meaning paths; document roles never waive required candidate
accounting. A prose-only document can return empty business data without fabricating
fields. This does not waive a caller's target schema.

`document.outline.status` is `interpreted` only when its role inventory is accounted
for; it is not independent accuracy approval. Unreviewed/uncertain blocks and
unjoined long-paragraph views keep it partial. Exact fragments remain available;
a reviewed fragment cannot stand in for the whole paragraph. Mixed native text
segments, complex list/container hierarchy and long views still need broader
characterization. Source-bound values and the native table model remain separate
from outline quality.

Compiler v32, prompt v39, region plan v20 and document-outline v1 / document-protocol v6 identify this
behavior. A checkpoint's saved outline is recomputed from its validated role
decisions, not trusted as a finished hierarchy. Earlier policies cannot resume
under the new contract. See [checked outcomes and priorities](../SUPPORT.md).

#### Separate roles from content

`interpretation/document_protocol.py` owns document roles;
`interpretation/native_structure.py` owns semantic structure and value selection.
The engine persists all three separately in `documentStages`. Existing record-table and
other-format paths retain their contracts.

The **role request** sends owned text, source-declared formatting/outline hints,
source order, preceding accepted headings and nearby table context. Table previews
include up to 12 whole cells and 1,200 text characters, preferring declared headers
and then source order. Counts and completeness are explicit; unclassified cells
are never promoted to headers, and oversized text is not silently clipped. No scalar
binding inventory, value-generation contract or expected answer enters this stage.
Code validates exact source ownership, target choices and role/level pairs, then
retains the accepted roles and exact original text bindings. It does not certify
business values or semantic applicability from a role decision.

The **structure request** carries accepted roles and owned original blocks, with no
parser binding inventory or suggested label/value pairs. It discovers fields, groups,
record columns, every occurrence, types and missing states. Exact row anchors and
separate unit/condition/note quotes must resolve within owned sources. The compiler
creates a partial structure before any values are read.

Structural contract repair identifies the exact schema-owned member path and expected
type, for example a null field label where a string is required. Unknown property names
and source/model values are never echoed. The decoder does not fill missing labels,
accept nulls or treat a contract-valid label as semantically correct.

The **value request** offers program-owned handles for present/blank fields and cells.
The response selects a binding, exact quotation or unresolved value for each handle;
it cannot alter field names, types, presence decisions or occurrence structure. Missing
states remain source-grounded structural decisions, not value-stage downgrades.
Native-structure v7 uses `native_value_choices.py` to remove only impossible binding
choices: source/type read failures, nonempty-as-blank or empty-as-present selections,
unresolved observation conflicts, invalid decimal spelling, and sources outside the
handle or logical occurrence. It uses the unchanged scalar reader and the compiler's
row-anchor test; display hints cannot override original observations. A compound
numeric candidate can still be represented by a narrower verified quote. Binding
text and required-candidate accounting remain complete. No viable blank binding leaves
only `unresolved`; the structure is not silently reclassified. An offered choice is
source-readable, not proof of the correct attribute or occurrence within a row.

For value selection, `quote.text` is the exact value substring, not a citation sentence.
Copying that substring into a selector is permitted; returning output data or numerical
offsets is not. Numeric handles require the numeric literal alone. Equal-valued attributes
still need their distinct correct source occurrences; literal type validity is insufficient.

Definition sources are separate from value sources. Already compiled roles, fields
and rows survive a failed value call. A value-free region with no required candidates
finishes deterministically without an empty model call. Physical tables retain their
existing protocol; prose records do not invent table geometry.

Whole structural-text bindings cannot become generic scalar copies. Exact inner values,
including observed blanks, remain eligible without waiving candidate accounting.
Quoted meanings pass through the existing separate applicability stage. Source text,
actual value links and meaning scopes remain distinct checks.

Managed native stages use the existing bounded-thinking transport with a 1,024-token
per-think-block allowance. The complete role response is capped at 2,048 output
tokens; semantic structure and values retain the managed client output cap. Other clients retain their
own configured reasoning behavior. Protocol v7 and native-structure v12 identify the three-stage execution;
previous checkpoints cannot resume. The policy does not certify model accuracy.

Native role, structure, value, batch-accounting and structure-review requests use the
same lossless source dictionary (`source_dictionary.py`). When sharing is smaller,
`sourceTemplate` contains common metadata and each `blocks`/`nodes` entry contains
its differences. Recursive dictionary merging reconstructs every source property;
arrays and null replace rather than merge. Text, native role and owned text range
remain explicit per source. Formatting, conditional/unresolved flags, XML references,
source order and Unicode spelling are neither summarized nor dropped. Exact JSON types
are preserved, including booleans versus numbers. Single or dissimilar sources stay
inline when a template plus its decoding instruction would be larger.

The common request serializer adds the decoding instruction only when a template is
present. Planning, batch sizing and actual dispatch therefore count the same full
message, including that instruction and the output contract. Checkpoint identity
includes the changed role/structure protocols and regenerates the same request view.
The public source observation, bindings and extracted values do not use this compact
encoding. This can prevent an unnecessary short-document split; it does not implement
logical continuation across genuinely separate regions or prove model understanding.

The unchanged native development comparison did not improve with reasoning enabled;
see `SUPPORT.md`. Transport completion, valid role grammar and preserved source
bindings do not establish correct role or field selection.

Each stage gets at most two local attempts and separate cumulative usage counters;
all calls/time remain inside the original document budget. The role request hash
covers the actual system, owned views, source formatting, preceding headings, table
preview inventory and decoder contract. The engine identity additionally fixes the
source, model, options and protocol version. Accepted content must retain exactly
the accepted role decisions. Resume recomputes these checks, not the saved public
outline. A changed earlier role context cannot silently validate a later decision.

A failed/interrupted content stage preserves accepted roles and already compiled
table values, but does not mark those regions' content read or overall extraction
complete. Saved in-flight or transport-failed calls require an explicit additional
allowance before another dispatch; ordinary resume does not replay an unknown
response. An explicit grant can reopen unfinished stage attempts but retains total
costs and accepted structure. Completed stages are not reissued.

Direct regressions cover separated requests/counters, title/caption/prose hierarchy,
real inner values and unit meanings, conflicts, uncertain roles, partial table
previews, context limits, stage failures/resume and stale/tampered checkpoints.
These are not model-quality approval. The unchanged failed native files retain
their prior expectations and 12-call/900-second budgets. Additional layouts, binary
HWP and long source views still need characterization and independent comparisons.

#### Exact text values and logical records

Native protocol v6 freezes field/column types, missingness and source anchors before
value reading. `native_structure.value_request` issues stable `@value` handles and a
closed source-choice schema. Present values choose an offered binding or an exact
quote; explicit blanks require an actually empty offered binding. Either can return
`unresolved`, preserving the structure while leaving the extraction partial. Absent,
unreadable and uncertain structural entries require no value choice.

The value response has only `regionId`, `selections` and `excludedBindings`. It must
cover exactly the offered handles, with no mixed source branches, extra fields or
foreign sources. The independent validator enforces the same contract when a backend
ignores decoder constraints. `native_value_wire.py` decodes source choices into the
existing private binding/quote/status representation; public v1 contracts are unchanged.

**Value grounding.** The shared source-inventory resolver verifies owned views,
exact Unicode spelling and repeated/overlapping literal occurrences. An occurrence
is the match index of that exact text in its owned view, not an item or sentence
number. Omission stays omitted through decoding so repeated matches still require
an explicit occurrence. Models cannot supply offsets, normalized values or output
pointers. Blank values require an actual empty native binding; a narrative sentence
about missing data is not itself an empty value. Physical-table and other-format
requests do not offer this extension. Identifiers, metadata and missing states remain
in scope; a relevance flag is not a loss gate.

`native_records.prepare` builds a compilation-local binding overlay. Quote IDs hash
the verified source range/text identity; original nodes, native bindings and source
views are not changed. Values are read by the existing scalar compiler and carry
`exact_source_quote` evidence. A whole heading/title/caption cannot evade the
structural-value check by being quoted or placed in a logical record.

**Logical occurrences.** A `logicalRecords` entry defines columns once, then rows
with exact source quotes and one value link/status per column. Every value's source
must be inside its own occurrence anchors; overlapping anchors, repeated row IDs,
missing/duplicate columns and foreign sources are rejected. Rows are sorted by original
source position, not model response order or equal values. An empty array requires
explicit nonempty `emptySourceQuotes`; code verifies the citation, while whether the
source truly states emptiness remains a semantic quality question.

The compiler creates arrays, schema, field/value evidence and scope targets without
inventing observed tables or cells. Scope catalogs distinguish logical occurrences
with `tableRef: null`, `recordRef` and `basis: source_grounded_logical_occurrences`.
Their row indices are source-ordered occurrence indices, not physical geometry.
`fieldIds` alone selects complete columns; bounded `repeatIds` plus row bounds and
optional `fieldIds` selects only existing values in those occurrences. Unbounded
record-plus-column IDs remain a union and cannot silently broaden an applicability
intersection. Physical tables keep their existing row/cell/formula/cache traversal.

**Limits, accounting and resume.** A region admits at most 1,000 logical rows and
10,000 logical values, 10,000 distinct quotes and 5,000,000 source characters scanned
for quotation matching. Anchors are checked in sorted order rather than pairwise
across all rows. A limit violation is explicit; no tail is silently truncated.
All required native candidates still need consumption or an explicit disposition;
reading one substring never waives the rest of a multi-attribute paragraph. Public
`coverage.nativeContentGrounding` exposes derived value links and logical occurrence
anchors separately from original observations. Repairs cannot drop an accepted
occurrence or column to hide an unbound missing value. Accepted quotes and records
are recompiled on resume, and old compiler/prompt/native-protocol identities fail.

Direct regressions exercise the actual HWPX reader, native role/content wire, exact
values, ordering, blank/absence, column and row-specific scopes, target-schema handles,
source immutability and checkpoint rebuilding. They do **not** approve AI quality.

**Exact candidate display.** Native protocol v5 adds `exactText` beside each
text binding in the private content request. It is resolved by the same source reader
as compilation, with exact whitespace, decimal spelling and empty strings retained.
An owned source window bounds the read. This display does not add, relabel or change
observed candidates, and it does not certify delimiter-derived label/value semantics.
If a candidate contains several attributes, the model must choose a narrower exact
quote rather than treat the whole string as a number. The original observation and
public result do not acquire display-only fields.

For a type-read failure, native repair also identifies the offered binding ID and
requested type. The stable error code is retained; no raw document text or model
field name is added to the diagnostic. Quotes, numeric conversion and missingness
still pass the original compiler checks. Display text counts toward the existing
request budget; excess input is not truncated or exempted. Prompt v37/protocol v5
invalidate older native checkpoints. These changes have deterministic tests; actual
model selection is checked separately.

**Scalar identity.** Compiler v31 no longer folds fields merely because their
source binding matches. `field_identity.py` compares the exact source address,
value type/presence, label, definition-source set and compiler-resolved destination.
Different definitions or destinations remain separate, including legitimate shared
values. Incompatible definitions at one destination fail rather than choosing the
first. Only fully identical aliases can fold; their meaning scopes map to the kept
field, and equivalent source addresses retain required-candidate accounting.
This prevents a wrongly named first field from deleting a correctly named later one.
It does not establish that either name or value association is semantically correct;
extra/wrong fields still fail independent whole-result review. Label/whole-line and
physical record-cell duplicate rules are separate, not strengthened by this change.

**Remaining boundaries.** The original seed receipt is split into four text regions
at the managed 16,000-character request budget. This extension does not yet join
logical records across regions; same-key arrays conflict visibly rather than merge
without a decision. Native packing still reserves a coarse fixed contract allowance.
Further comparisons of this receipt and a differently arranged equivalent must
check all records, identifiers, missingness and scopes within the unchanged document
budget. Further packing/continuation changes need their own tests; larger budgets
or a single compact fixture cannot stand in for long-document correctness. Independent
new HWP/HWPX and XLSX cases are still required after development fixes.


### Native stage planning, preservation and remaining boundaries

The default native product path now separates semantic structure from value selection.
This replaces the candidate-driven decision that the v5 comparison failed; implementation
and regression success do not yet establish actual-model quality.

`regions.prepare_regions` measures actual role and structure systems, payloads and
contracts instead of reserving a fixed 12,000 characters for native prose. Unknown
roles are sized conservatively, not adopted as decisions. Source-order packing keeps
one owner per value and uses exact disjoint windows for oversized text. Fixed protocol
cost no longer makes an otherwise splittable paragraph atomic. Every dispatched stage,
including values and repair feedback, still checks its actual full request against the
same hard limit. No source is truncated and no document budget is raised automatically.

`documentStages.structure` retains the accepted response, request/structure hashes,
attempt count and cumulative cost. Value selections are recorded separately. Resume
revalidates contracts, exact quotes, owned sources and the resulting compiled structure;
it rejects changed identities or inconsistent selections. Native meaning ranges are
program-derived and tied to the source inventory. Value compilation does not prune
frozen fields merely because a parser label or a record value shares their source.

Native-structure v7 retains source-quotation error codes and reports schema-owned
member paths/types in bounded structural repair. A meaning quote and a row quote
must not report the same invalid occurrence differently. Raw source values, model
labels and unknown property names never enter these diagnostics. Earlier native
checkpoints are incompatible with this source-choice and repair policy.

The v1 Spark run exposed output size as a separate limit even when input fits.
`native_structure_wire.py` now carries the same decisions with less repeated JSON:

- Code issues field, record, column, row and meaning IDs. Model keys and labels still
  define the document's own organization; existing group/parent relationships remain.
- Each record defines columns once. Every row has one explicit state per column, in
  column order. A bare state explicitly cites the row's anchor sources; a state object
  can narrow those sources. Missing/extra states and foreign sources are rejected.
- An anchor is an owned block ID for its exact entire view, or an exact source quote
  for part of a block. Several items in one block need distinct narrower anchors;
  overlapping whole-block rows cannot become distinct records by changing IDs.
- Column definition sources may inherit the record's sources; field definitions may
  inherit their value sources. Distinct definitions remain explicit. This is a defined
  model choice, not an inference from a nearby parser label.
- The raw response and its hash are retained beside the expanded structure. Resume
  decodes it again and compares the result; editing only the expanded state is invalid.
  Expansion is bounded before per-cell objects are created.

Value requests carry each occurrence's source quotes once and refer to that context
from field handles. Native-structure v4 also shares identical closed selection schemas
and their unresolved branch; a keyed handle does not repeat its own ID in the payload.
Original nodes, inline formatting and bindings are not removed to make a request fit.
An unread skeleton's expected unresolved values remain visible in the partial result,
but are not repair feedback for the first value call. Subsequent value failures still
supply their actual diagnostics. Exact source choices and compiler ownership checks
are unchanged.
The compact wire does not increase output limits, accept truncated JSON, omit missing
states or certify model completeness. Duplicate scalar/record proposals and wrong
field types remain semantic failures, not aliases to drop automatically. In the v3 Spark comparison, the original preserved two rows but the value request
exceeded the input limit; the compact form duplicated item attributes and truncated.
V4's actual 9B proposal duplicated fields again and exceeded the value-input limit.
The existing 314B backend produced two item rows and fit its value requests, but its
selection responses violated committed present/blank states; no values were accepted.
V5 reports the offending program-issued handle and required state in bounded repair
instead of the misleading generic JSON error. Unknown keys, invalid accounting and
shape mismatches have safe codes; diagnostic messages never echo source/model text.
All closed-contract, presence and source constraints remain unchanged. This feedback
correction has local coverage, not an actual-model quality result.

The current boundary is deliberately strict: an incorrect **valid** structure cannot
be silently rewritten during value repair. Native-structure v8 adds an explicit, source-grounded
revision after exhausted value reading, as described below. Logical continuation between separate native
regions also remains unimplemented; equal keys cannot authorize an unconditional merge.
Long-fragment hierarchy, actual-model bounded value execution, and varied
HWP/HWPX/XLSX whole-result quality still need investigation. Repeated use of a development document is regression
work, never a new independent holdout.

#### Source-grounded structure revision

`native_structure_revision.py` implements one revision cycle for a native region after
its single-value or batch-local two-attempt allowance is exhausted without complete
reading. Unknown/in-flight value exchanges are not reasons to launch another model
request. Successful value stages are unchanged: this is not a universal semantic review
or independent approval of otherwise `complete` outputs.

The review request includes accepted roles, the previous complete structural response,
program-issued entity references, the prior content-state hash and failure codes.
Revision v4 shows previous structure and accepted roles through lossless `{columns,rows}`
tables when equal-shaped object arrays become smaller. Every property value and array
position remains; differently shaped objects stay explicit, including absent optional
keys. Nested tables follow the same rule. The canonical checkpoint base is never replaced
by this display encoding. Before-entity references are offered in the closed response
contract rather than duplicated in a payload list. `native_structure_history.py` owns
this projection; response structure, exhaustive change accounting and compilation are
unchanged. The review explicitly reassesses a failed extraction on the same source; it is not
source-change detection, and unchanged source text is no justification for retain. It
avoids repeating the entire initial discovery instruction, while the full output
contract remains visible and enforced.

A shared dictionary template and per-block patches preserve every original source
property, including text, formatting and XML references. This is lossless factoring,
not a summary or permission to drop source context. The actual request and any repair
still pass the original character/time/call limits; indivisible context stays unfinished.

The model either retains the old structure or proposes a full replacement. `retain`
does not approve unread values. A replacement includes an exhaustive change ledger:
every old and new field, group, record, column, row and meaning appears exactly once
in a keep/replace/remove/add change, with exact source anchors and a reason. Positional
entity IDs refer to the candidate structure, not data paths. Missing entries cannot
silently delete fields or rows. A keep must be identical; a replacement can split or
merge entities. Each affected owned source is covered by its change evidence. A change
can cite a genuinely empty owned block, without fabricating a nonempty quotation;
logical-row/meaning anchor rules are not relaxed. Missing coverage is reported using
only program-owned entity references, not source or model labels.

Full wire validation, exact source grounding and compilation against the caller's
target schema precede commitment. A malformed, truncated or interrupted proposal
leaves the previous partial data, roles, structure and reads intact. Contract/source
validity does not prove a semantically correct replacement. No fixed business template,
relevance filter or label-based deletion rule decides the new organization.

An accepted replacement atomically invalidates the current value response, value-batch
plan, source accounting and saved applicability decisions. **No prior value is reused
by equal key, ordinal ID or even identical binding.** The complete earlier structure,
reads and usage remain in the revision base for audit and replay validation; current
values become explicitly unread until reread under the new structure. New batches
compile against the replacement and all applicability is rebuilt against current
targets. Other regions' observations and values remain intact.

The controller processes the revised region before moving on. Review has at most two
local attempts, within the same document allowance as the original reads and subsequent
rereading; it never raises that allowance automatically. Checkpoints revalidate the
old wire, reads/batches, request hash, exhaustive change ledger, accepted transition
and current value response. Separate revision/current/retired-read usage prevents lost
cost on reset. An unknown review exchange needs an explicit additional allowance to
resume. A failed review cannot be replayed indefinitely; a completed revision cycle
is not reopened for another replacement.

Regressions cover atomic success and rejection, retained structure, full field/row/
meaning accounting, source evidence, exact decimal rereading, lost/tampered history,
input/budget stops, transport/truncation and in-flight state. A 32-field batch case
remains partial at its original 12-call limit and needs an explicit two-call grant for
final source accounting; this is a controller check, not a short-document quality pass.
Cross-region logical continuation still needs separately source-bound earlier-record
context and a relation decision; nearby text or equal keys cannot authorize merging.

### Native structure design comparison — not adopted

Separating occurrence discovery from attribute/state decisions was tested as a private
prototype, not added to the product controller. It locates source-grounded occurrences,
issues program IDs, and can transpose per-column state maps into the existing native
row wire without changing values, row order or source ownership. Eight scripted projection
checks cover exact transposition and rejection of missing/extra/foreign references; they
do not validate model grouping or whole-product role compilation.

Actual grouping failed on both multi-paragraph and one-paragraph presentations of the
same items. The model selected repeated text, paragraph containers or the whole document
instead of comparable item records. A scalar form also exposed false-positive grouping.
Bounded thinking, more explicit record terminology, lossless text-first presentation and
a source-summary prefix did not make this abstraction reliable. Source text, formatting,
metadata types and original references were not trimmed. A fitted initial request and
valid source anchors are insufficient evidence of the correct semantic granularity.

The ordinary-prose control described the main items and facts, and a summary-first reply
could explicitly name two items yet still return one document-wide record. These reading
notes are model output, not source evidence or accepted data. They also did not establish
complete missingness, metadata scope or value bindings. No later definition/value/scope
stage was run against groups already known to be wrong, and no prototype was promoted
merely because its deterministic projection passed.

The subsequent per-item/attribute draft also failed: it classified unrelated source
paragraphs as items or copied record/note paragraphs into metadata. A matched control
removed only the `response_format` wire member; generated content was byte-identical
on all three files. Decoder grammar did not cause this particular failure. The original
text, metadata, output contract and sampling were identical in that control.

A separate free-form data-tree draft removed the prescribed item/attribute/evidence
schema while retaining complete original source views. It represented both ordered items
and their main counts in both layouts, and connected the scalar form's separate label
and precise value. It also introduced internal region IDs as business data, copied source
formatting in one case, used null instead of explicit missing states, and left qualifier
and metadata scope insufficiently specified. These are untrusted hypotheses, not values
or source evidence. Neither draft design has been integrated or approved.

Separate draft grounding confirmed that handle-keyed source choices can distinguish the
same numeral in different attributes and read a genuine blank. It did not validate the
whole draft: notes/units became values, a real note and a scalar blank were excluded, and
role-only classification still misread internal identifiers and missing facts. These
prototypes remain unadopted. The production numeral-location aid below is a separate
source-choice mechanism for already frozen canonical definitions, not the draft-dependent
matcher or a replacement for semantic structure decisions.

The free-tree design still needs reliable field definitions, item ownership, missing
facts, qualifiers and full source coverage. Exact matching alone cannot supply them.
Existing atomic replacement, complete history, exhaustive change checks and public v1
contracts remain in force. Cross-region continuation remains separately unfinished.
Private comparison evidence is under `structural-kpi-20260913/native-occurrence-comparison-27/`,
`native-item-facts-28/` and `native-draft-grounding-29/`; rejected replies remain preserved.

#### Program-issued numeral locations

`native_literal_choices.py` offers optional `literalIds` to frozen present integer,
number and decimal handles. It scans complete original nodes, then applies owned views,
field source references and record anchors. A view cut through a numeral cannot create
an interior value. Canonical scalar reading filters incompatible spellings without
normalizing them; known conflicting binding sources are not offered by this aid.
This limited lexical recognizer is not semantic classification or a complete number parser.

A catalogue entry contains exact source text, its source reference and match occurrence,
plus bounded before/after context. Occurrence counts every exact substring match in the
owned view, including matches inside other numerals; it is not the scanner's index or an
item number. `accept_values` regenerates the catalogue and validates the closed per-handle
choice, converts the selection to an ordinary original-source quote, and uses the existing
compiler. Neither catalogue text supplied by a client nor a model's draft value is read.
Identifiers/other types, actual blank bindings and explicit unresolved choices retain
their existing paths. Source observations and required candidate accounting do not change.

The optional aid has a 256-location and 100,000-original-source-character bound. If either
is exceeded, it offers no partial prefix and reports the reason; ordinary bindings and
exact quotes remain available over unchanged source. The common message serializer adds
its decoding instruction only when a catalogue is present and counts that instruction in
the actual request. Native-structure v12 invalidates older stage checkpoints.

The bounded Spark comparison over the previous uncorrected real-model structure
still fails semantic reading. The runtime batch drops the optional aid to fit; its
numeric quotations include labels and fail type reading. A matched smaller request
with the aid compiles, but the received count selects the requested count's equal
`8` at the wrong original position. The selector is a valid location, not proof of
attribute ownership. This stage comparison is neither checkpoint resume nor a new
whole-product pass; it does not justify forcing numeric enums or changing partition
priorities. Evidence is in `native-literal-model-31/` under the private comparison root.

### Bounded native value requests

Value-batches v3 sizes each request with the actual source catalogue. If the optional aid
would consume the fixed repair reserve, a batch drops the whole aid and its selector
branch, reports `context_limit`, and retains all original sources, bindings, handles and
ordinary quote choices. This decision is deterministic from the stored integer
`contextLimit`, which is rechecked with the request and response on resume. A response
cannot select a literal not offered by its actual batch. Completed literal reads retain
their exact catalogue for source accounting; unused alternative IDs are not part of the
frozen field definition. This delivery fallback never excludes document information or
increases the call/time/input limits.

Native-structure v12 / native-value-batches v3 keeps the existing single request when
it fits. An oversized request instead uses deterministic batches of at most 16 value
handles, sized from the actual system, payload and closed output contract. Every batch
retains **all original nodes, formatting and binding text**; relevant occurrence
anchors and per-handle source/type/presence constraints remain explicit. No field,
column or row is removed to make it fit. If even one handle with this context cannot
fit, `native_value_context_indivisible` exposes the unfinished read. The actual dispatch
check still applies to repairs; planning does not exempt later messages from the limit.

Value batches cannot supply exclusions or mutate other batches. Code compiles the
accumulated selections against the entire frozen structure before accepting each
response. Unread handles remain unresolved placeholders, not absent values. A local
repair can resolve additional handles but cannot replace an already read value.
Each batch has at most two attempts within the same cumulative document call/time
budget; adding batches creates no additional document allowance.

Source accounting starts only after every value batch is resolved. It partitions the
remaining required candidates, excluding none of the already consumed bindings.
Each candidate receives exactly one disposition. Verified read context uses a shared
nested template plus `[handle, patch]` rows; recursively merging them reconstructs
all original definition and selection details without normalizing source text.
Unresolved or unfinished accounting remains partial even when every data value is
present. Scope/applicability work still follows; completed reading is not completion
of the whole document or independent approval of semantic accuracy.

Checkpoints retain the structure/source/request identities, deterministic plans,
accepted responses, hashes and cumulative usage for each batch. Resume regenerates
requests and compiles accepted reads again before trusting the aggregate; inconsistent
plans, quotes, accounting, costs or completion flags are rejected. Completed batches
are not replayed. Unknown exchanges halt until an explicit allowance reopens unfinished
work, retaining all prior costs and accepted reads. Coverage exposes batch status,
keys and preflight sizes, while raw responses remain in the checkpoint.

Scripted HWPX coverage reads 32 distinct measurements through the actual parser and
controller in eight calls at 16,000 characters: roles, structure, four value batches
and two accounting batches. It checks exact evidence, stopped-boundary persistence,
explicit resume, invalid completion flags and no rereading after all values are read.
A separate record comparison matches unbatched precision, explicit blank/absence and
all schema/value evidence. This is code-path verification, not actual-model quality.
Long structural responses and indivisible large contexts remain separate limits.
The v6 Spark model comparison did not exercise batches: the managed server failed to
start, and the explicit existing-backend continuation failed at structural discovery
and blank-value reading. Its absence of an oversized dispatch is not evidence that
actual-model batch extraction succeeds.

### Next boundary: explicit structure correction

This is **not implemented**. A model can propose syntactically valid item-numbered
fields and duplicate them as one-row records. Batching or compressing that proposal
cannot establish correct item structure. The larger-backend comparison improved item
structure but still failed values and complete applicability. Do not add a fixed
business template or silently delete every scalar sharing a record's source.

The next source-choice correction must also distinguish a claimed state from readable
source evidence. The v6 comparison offered nonempty bindings to a field frozen as
blank, allowing a choice that compilation necessarily rejects. Filter such impossible
binding choices using the same original-source/type/presence rules, without changing
canonical observations or removing valid quote alternatives. A reported blank in a
note is not itself an empty observed value. Structural diagnostics must identify the
invalid contract member without echoing arbitrary source/model strings; null attribute
labels must not be silently accepted or derived from the field's value.

A structural correction must be an explicit transition from the accepted structure
hash. It must describe each changed/removed field, column, occurrence and meaning,
retain source coverage, and pass the same quote, ownership, state and overlap checks.
Validate the replacement before committing it. Recompute affected value handles,
batch plans, accounting and applicability; never reuse a value or scope decision
merely because an ordinal ID or key stayed the same. Invalid or interrupted replacements
preserve the preceding partial structure. No additional document budget is created.

Test corrected titles versus genuine metadata, valid repeated scalars versus duplicate
item attributes, equal values in distinct occurrences, explicit blanks/absence,
failed replacement preservation and invalidated batches. Cross-region logical
continuation also remains open. New varied HWP/HWPX/XLSX documents must independently
pass the actual complete path; scripted transitions alone cannot approve the KPI.

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

Scope selection wire v3 optionally packs long `context` and `rowBoundaryCandidates`
lists through `scope_context_wire.py`. Contiguous records with identical property
keys share constant properties and use explicit `columns` and `rows`. Blocks and
rows retain their original order; omitted properties, null and exact JSON types
remain distinct. Shared-only records still have one empty row each. This operates
only on typed display positions, never text resembling encoding instructions.

The full decoding instruction is added only when total savings exceed its cost.
The packed payload and instruction are part of the wire fingerprint; selection
contracts, canonical tasks, row/column mappings and source binding are unchanged.
The 50-data-row regression retains all 51 observed rows and 102 complete texts,
reducing the actual request from 24,557 to 15,858 characters at the unchanged 16,000
limit. The same scripted 49-row subset resolves identical values and source traces.
This checks delivery and compiler behavior, not actual model or independent quality.
Earlier scope-selection checkpoints are incompatible with the new wire.

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
1,000,000 row/column work units. They can prevent long-table completion. Candidate
discovery is also unchanged: the 96-row fixture still reports bounded coverage and
does not offer its record candidate. Further work must check every binding,
preserve public evidence and detect row/
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

These identify the current implementation; update the relevant row when changing
its owning behavior. Do not patch stored IDs to resume.

| Contract | Version |
|---|---|
| Semantic prompt / region plan / result compiler | v39 / v21 / v32 |
| Document outline / native role-content protocol / native structure | v1 / v7 / v12 |
| Native structural response wire / native value batches / structure revision | v1 / v3 / v4 |
| Table protocol / table reference wire / table source wire | v21 / v2 / v1 |
| Scope integration / scope-axis protocol | v14 / v6 |
| Scope row-axis wire | v2 |
| Scope selection wire / context display / source binding / regional checkpoint | v3 / v1 / v2 / v3 |
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
