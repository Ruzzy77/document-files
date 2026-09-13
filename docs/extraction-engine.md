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

### Binary HWP notes and exact body ownership

Native observation adapter v4 preserves relationships from stored HWP controls in
`document_model/hwp_notes.py`. A note is keyed by section number, section stream,
control kind (`fn  ` or `en  `) and native note ID. IDs from different section
streams, footnotes and endnotes cannot alias one another. The control's
`owner_paragraph_record` identifies the body paragraph; the parent of a number field
usually identifies a paragraph *inside* the note and is not a substitute body anchor.

The nearest note frame in `container_path` owns its contents, including nested table
content. A nested note's content is not also attached to its outer note. Explicit
control membership produces `contains` edges; body paragraphs point to note paragraphs
with `noteReference` edges. These retain `noteKind`, `nativeNoteId`, `controlRef`,
`basis: native_hwp_control`, `anchorGranularity: paragraph` and the original
`numberSourceRefs`. Number references require the matching native kind and a stored
integer control value. There is no generated numbering or invented character offset.
Multiple text segments of the exact body paragraph remain separate source nodes.

Missing or ambiguous controls, missing section/owner/content and conflicting frame
ownership produce `native_hwp_note_*` issues rather than nearest-text guesses.
Blank note paragraphs remain present. A 100,000-new-edge guard stops with an explicit
observation budget issue; it does not silently discard the tail or alter source nodes,
values or bindings. Region context can expose the declared body/note links to the
interpreter, but it does not certify the model's note roles or final interpretation.

This is an internal observation addition. Native structured projection still exposes
its existing units and `sourceStructure`; it does not acquire a new graph response
shape. The AI result retains the observed relationships under `document.structure`.
Public v1 contracts are unchanged. The adapter version participates in observation
identity, so checkpoints with the old observation cannot silently resume as the new one.

`document_model/note_objects.py` derives the bounded `nativeNotes` inventory from
that control graph. Each source control ID owns its kind, body references, content
paragraphs, all member references and typed stored-number sources. Repeated numbers
or equal text do not identify an object. A note with several paragraphs remains one
object. Unlinked/ambiguous controls remain unresolved; graph or number conflicts return
an incomplete inventory, never a successful prefix. The limits are 1,000 objects,
200,000 inspected relationships and 2 MiB of serialized inventory, not a process-memory
qualification. Original nodes, bindings and relationships are unchanged.

Role, structure, revision and value requests include this inventory when relevant.
Previously the role-derived structure request only showed text-bearing paragraphs:
empty controls and stored note-number values were absent even though their IDs could
be offered by the response contract. Context now exposes their exact declared meaning
without copying irrelevant formatting. `ownedRefs` distinguishes values owned by the
current region from whole-object context; it never grants access to a different
region's values. Source text and value bindings remain separately available.

`native_note_checks.py` rejects a present/blank scalar or record cell that offers
several declared notes, or several distinct referring body paragraphs, as interchangeable
value sources. Shared definition references and explicit uncertainty remain allowed.
Interpreted note/unit/condition meanings cannot combine separate notes; genuine reference
and relationship proposals can still cite multiple objects. No values, new records,
headings or note counts are inferred by these checks. Multiple text segments belonging
to the same source object are not rejected merely for having different node IDs.

The result also retains `document.structure.nativeNotes`. Compiler v36 can account for
a structural note disposition through its declared body link, recording `nativeNoteObjects`
and `nativeNoteBasis` in the source-use ledger. This does **not** resolve an additional
unit/condition's applicability or approve its interpretation. Unlinked notes and undecided
additional meanings retain their existing issues.

`native_note_accounting.py` derives structural dispositions only for owned, empty
controls, explicitly structural containers and verified stored-number nodes belonging
to a fully linked native note. It records the object IDs and native basis; original
nodes, numbers and relationships are unchanged. Body/note paragraphs, nonempty members,
unknown empty nodes and unlinked/conflicting objects are not covered by this rule.
An explicit unresolved disposition takes precedence. Required value bindings still need
reading or explicit exclusion, including stored note numbers; this is not a blanket
exemption of attributes, note content or additional applicability.

`native_value_batches._request` keeps the same note facts in both value and accounting
requests. The earlier v3 reconstruction omitted them. Batch v4 sizes each request with
all that context and fails explicitly if it cannot fit; it never strips the inventory to
make a batch pass. `native_note_context.py` separates role-only, structure, value/accounting
and revision instructions. The role stage sees declared note relationships but receives
no field/value-selection tasks. A note reference does not force a paragraph or heading
role. Structure/revision distinguish the retained native attachment from genuinely
additional attributes and applicability; code does not delete meanings on that basis. `contract_messages` appends those fixed instructions to the system message
only for this product-generated inventory. No source-provided guidance is promoted into
instructions. Documents without that inventory keep their previous message content.

Ordinary note text and structural numbering need not be invented as business fields
merely to survive in the result: the exact text, typed numbers and relationships already
belong to the native document structure. Actual attributes inside notes still require
field/value interpretation. The public native unit projection is unchanged. Checkpoint
replay re-derives the object inventory and refuses changed numbers, owners or omitted
inventory. Document protocol v13 / native structure v18 / native adapter v4 / compiler v36
invalidate older incompatible checkpoints. Structure-revision change-ledger defects remain
separate work; malformed references are not accepted to make a failed run appear complete.

#### Compatible source choices and complete conflict feedback

`native_occurrence_contract.py` specializes the standard JSON response contract using
exact declared note/body ownership. A present/blank scalar or explicit record cell must
choose sources from one compatible set. Note ownership and body-paragraph ownership
are independent: a body and a note may coexist, but two distinct notes or two distinct
body paragraphs cannot be interchangeable alternatives. Unmarked sources remain available;
the program does not choose a field name, meaning, value or nearest owner.

Interpreted note/unit/condition anchors likewise stay within one declared note; genuine
cross-object definitions, references and relationships remain expressible. Shared field
and column definitions are unchanged. Missing/unreadable/uncertain states may retain
multi-object evidence. A note with several paragraphs is still one object, and different
text segments of the same body paragraph retain their shared native owner. Context-only
references never become owned value choices.

For native note requests, present/blank record cells use explicit `{status, sourceRefs}`
rather than inheriting all row anchors through a bare status. This preserves the same
checked cell decisions while preventing inherited sources from bypassing the closed
choices. Other documents retain their existing wire choices. Native structure v18 /
structural wire v3 / document protocol v13 / revision v8 identify the current contract.
Both schema validation and the existing independent occurrence checks still apply.

Compatible sets are bounded at 1,024 combinations, 2,000,000 source/set comparisons
and 2 MiB of generated contract content before factoring. These are preparation limits,
not process-memory certification. An incomplete native inventory or exhausted bound has
no permissive response fallback. The planner can split source regions; it uses relaxed
schema size only to estimate fixed overhead, never to approve or dispatch a response.
An indivisible region remains explicitly incomplete with its original source preserved.

Structure and revision repair now receive all bounded conflicts: a program-issued value
or meaning handle, the actual known source references and their native owners. Model
labels, explanation text and unknown source IDs are not quoted into feedback. The limits
are 64 conflicts / 16,000 serialized feedback bytes; an overflow returns an explicit
limit error, not a successful-looking prefix. Malformed responses retain schema-owned
diagnostics. A correction never silently splits a scalar, reuses the first note or
accepts an invalid replacement. Revision change mappings still require complete old/new
coverage before atomic acceptance; improving source diagnostics does not fix their
separate bookkeeping failures.

#### Text anchors are not typed value sources

`native_structure_wire.contract` derives quote and logical-row/meaning anchor IDs from
`source_inventory(observation, region)`, using only nonempty owned source views. It does
not test the full node's display string. Bounded `nodeViews`, context-only nodes, archived
`source_text` and native spreadsheet source paths retain their existing inventory rules.
Whitespace is not normalized away. Literal quotes still require an exact source match;
offering a text-bearing node does not authorize arbitrary text on it.

A textless note control cannot be a literal quote, row anchor or interpreted meaning.
It still retains its native kind, ownership and number bindings. Scalar/cell value
sources, shared definitions and structural dispositions keep their separate eligibility
rules; a stored numeric value does not need a display quotation to remain readable.
The note-ownership restrictions intersect the nonempty anchor set rather than restoring
empty controls. With no text anchors the wire contract offers no records or meanings,
while retaining eligible typed scalar values and dispositions. Completely textless
regions still follow the existing role-planning path; this contract change does not add
a new extraction route for them.

Revision v6 deliberately distinguishes whole-block evidence from literal quotations.
A change can cite any actual owned inventory view by its bare ID, including a genuinely
empty block. A `{sourceRef, text}` quote can cite only a nonempty view. Change evidence
therefore does not inherit the narrower logical-row schema, nor permit invented text
on an empty source. Complete old/new entity accounting, atomic acceptance and exact
source checks remain enforced.

Regressions cover empty HWP controls, typed numbers, owned/context/archived views,
spreadsheet raw numeric spelling and generated displays, quote/row/meaning exclusions,
and valid bare empty-block revision anchors. The planner still sizes the full resulting
contract; it may split a region, never delete sources or dispatch a relaxed fallback.
See [current model verification](../SUPPORT.md#latest-spark-verification).

These structural constraints do not establish sensible fields/records, correct missing
states, nonduplicated interpretations or applicability. Whole-result model verification
is separate from schema validity and native graph preservation.

### Table metadata and evolving request context

`interpretation/table_source_wire.py` shares repeated properties in table structure
and meaning requests. Every node ID and its text remain explicit and in order. Same-shaped native
metadata uses shared properties and nested-key columns; equal columns can share one
exact value without merging source nodes. Cell and relation lists use the same typed
record encoding when it saves space. Missing keys, null, empty strings, whitespace,
numeric types, arrays, formatting and source-cell geometry survive exact expansion.
`expand_table_sources` is an inspection helper for product-created requests, not a
decoder for model output. The original observation and compiler inputs are unchanged.

Savings include the decoding instruction. Planning and dispatch both measure the
messages produced by `contract_messages`; a shorter JSON payload alone is not enough.
The public response contract is unchanged. Table protocol v29 and region plan v23
invalidate checkpoints made with the previous display and planning behavior.

An earlier region's column mapping may not exist when the initial plan is made.
Before later **unstarted** table work, `add_table_definition_context` retains that
nearest preceding accepted mapping's definition nodes and cell geometry as context.
Later accepted regions never become an earlier region's prior context on resume. It does not
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

`rebalance_table_pair` uses a bounded window of two adjacent **unstarted** views of
the same original physical table. It repacks them against the current compiled
mapping so a small tail can share a request with the following rows. No model role,
header, value or continuation decision is made by packing. Different physical tables,
gapped view boundaries, overlapping sources/spanning cells, text windows and altered
source geometry are not combined. An unsuccessful or unchanged proposal is rolled
back, including trial views and planning issues. Original sources, source order,
value ownership and required bindings must match exactly before replacement.

The engine does not replace either region once it has table state or an accepted
interpretation. Replacement IDs have bounded, non-nesting suffixes; continuation
candidates and the plan are saved before another call. A cancelled, attempted
structure keeps its identity and budget on resume. `replan_table_region` remains
the single-region fallback when pair packing cannot safely help.

This reduces planning overhead, not the number of semantic decisions the model
must make. Offline replay with the first accepted mapping produces 10 HWPX / 6 XLSX
table regions for the existing 50-row examples. Even if every structure and negative
source selection succeeds immediately, that is 20 / 12 table calls **before** other
content, repairs, continuation and applicability. A 12-call whole-document run is
therefore not a useful completion test for these examples under this protocol.
The original fixed-budget failures remain failures; no automatic budget extension
or new model run is implied by this planning result. Evidence: private
`structural-kpi-20260913/native-planning-46/`.

Meaning selection and details share cell/relation metadata **after** reference-wire
translation. Source choice inventories, literal quotes and the frozen structure
are unchanged by this display codec. Checkpoint reference dictionaries
still use canonical sources, not display templates. Exact expansion is tested for
both native HWPX and XLSX. Larger meaning inventories or repair feedback can still
exceed the limit; sharing is not permission to omit them or expand the budget.

Meaning selection/details cannot return a binding ID or read a candidate value path.
Their requests therefore omit `unaccountedBindings`: unused alternative candidates
are not unresolved document facts for the model to reinterpret. Full bindings,
required-value checks and accounting remain in the observation/compiler. Every
owned source text/range, actual value/definition usage and frozen structure stays
in the request. Initial details receive the content rules; revision-ledger
instructions are added only when an accepted meaning is being repaired. Quote,
coverage, change-ledger and applicability checks are unchanged.

Reconstructing the previously blocked HWPX detail request with the same sources and
saved choices measures 15,251 characters instead of 16,899 at the unchanged 16,000
limit. This is an offline current-protocol comparison, not checkpoint resume,
model generation or semantic approval. Larger requests can still remain partial.
No additional status-display codec or new model output format was introduced.

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

Table meaning review also reads native cell content, rather than the coordinate-prefixed
display. `table_sources.py` (source inventory v2) selects the stored string at
`/semantic/value/value`, the formula expression at `/semantic/value/formula`, or a
non-string scalar's existing lexical text at `/semantic/value/raw`. It requires an
observed sheet-cell role, sheet/cell metadata and a coordinate; it never removes an
address-looking prefix from arbitrary text. A cell whose actual content is
`A2=Alice` therefore keeps that entire string. Stored blanks and empty strings stay
distinct in the observation, with empty source text in both cases. Formula caches
remain separate value evidence and cannot replace the formula in a source quote.

Quotes, review ranges and their hashes retain the selected path and Unicode offsets
within that exact string, including repeated phrases, whitespace and numeric precision.
The compiler rechecks them against the observation before emitting semantic details.
Existing bounded `nodeViews` still address `/text`: display offsets are never reused
on a different native string. Other nodes, and cells without an eligible stored string,
keep their original `/text`; this correction does not recover missing source metadata or
restore normalization lost from a display-only view. Table protocol v29 / compiler v33
invalidate prior checkpoints; neither original nodes nor public v1 contracts change.

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

Compiler v32, prompt v39, region plan v21 and document-outline v1 / document-protocol v7 identify this
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
own configured reasoning behavior. Protocol v13 and native-structure v18 identify the current three-stage execution;
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

`native_structure_revision.py` implements one revision cycle per native HWP/HWPX region.
It can follow an exhausted two-attempt value/batch stage, or precede the first value
request when `native_role_review.overlaps` finds a potential role/attribute disagreement.
For that early review, a present/blank field or cell must refer only to title/heading/
caption sources without a known owned inner-value binding. Absent, uncertain and unreadable
entries are not treated as read values. This is not proof of an incorrect role: an exact
inner quotation can remain valid even without a pre-existing binding candidate.

The early review reports only program-issued value handles and source-role references,
not field labels or arbitrary source prose in diagnostics. It preserves all source data.
The bounds are 64 overlap entries and 16,000 serialized bytes; exceeding either leaves
an explicit preparation failure rather than a truncated successful-looking list.
Unknown/in-flight exchanges do not authorize another call. The review's at most two
attempts use the original document allowance. It is not universal review of otherwise
`complete` outputs or independent quality approval.

Revision v8 permits an optional complete `documentElements` array beside the full
structural replacement. Omission explicitly retains all original roles. If supplied,
it must contain every owned role exactly once in source order, satisfy the normal
role/level/caption rules, and account for every old and new `role:N` in the same checked
change ledger as fields, groups, records, rows, columns and meanings. Role-only edits
are allowed even when the structural wire is unchanged. `retain` can proceed to value
reading after an early review, but never permits a whole-title value that the compiler
would otherwise reject. A genuine inner value or explicit missing state is not deleted
to force a successful result.

The original role response stays unchanged in the checkpoint. `effective_roles` derives
a replacement only from the completed checked revision. On commit, the engine rebuilds
the role fragment and current structural request identity, invalidates current values,
batches, accounting and applicability, and preserves the prior reads and costs in the
revision base. Replay verifies both original and effective roles, the overlap record,
source/structure identities and exhaustive changes before trusting new values. A failed
replacement leaves the old partial data and roles intact.

Revision v9 adds a bounded follow-up in `native_revision_followup.py`. If an early
review used one attempt and chose `retain`, and actual subsequent value selection
reproduces `document_role_value_conflict`, the engine uses the one remaining review
attempt before repeating an incapable value-only request. No third automatic review
is available. An early review that already used both attempts cannot reopen.

The new review stores the unchanged early request identity, response, usage and base
in `priorReview`; its own base contains the actual attempted values and compiler failure.
`retainedReviewHash` identifies the previous decision, not an approval. The live early
review marker is removed before actual reading; its original snapshot remains intact.
Combined attempt and usage counters include both reviews. Unknown transport still
stops without an explicit grant; a grant does not erase consumed calls or prior history.

`roleValueFailure` stores the failed response, its hash and the original value-request
base hash. The base hash is not represented as a full transport/feedback hash. For a
batch it also records the phase, index, raw failed proposal and exact batch-request
hash. A failed batch is not inserted into the accepted batch responses. Replay checks
these identities, rebuilds the aggregate and reruns schema/source/value compilation:
the same specific conflict must recur. A fabricated error code or internally consistent
hash alone cannot authorize review. Malformed early/failure objects are rejected on
checkpoint loading.

A checked replacement invalidates all current values and forces fresh reads, including
previously accepted sibling batches; prior partial data stays in the review base. A
follow-up `retain` may use the remaining value attempt to select a genuine inner quote,
but cannot approve the rejected whole-title value. Regression cases cover single and
batched values, multiple batches, exhaustion, unknown transport, explicit resume,
invalid/no-op proposals, later retain, cumulative cost and tampered saved history.
Those scripted native-parser results do not prove that a live model chooses correctly.

Before committing edited roles, the engine recomputes all other saved role-request
fingerprints with the prospective heading context. A changed fingerprint rejects the
replacement with `native_revision_role_context_dependency`; it does not silently stale
or erase another region's interpretation. Replacing dependent cross-region role history
is a remaining limitation, not an implemented automatic cascade.

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

The model either retains the current decisions or proposes a full replacement. `retain`
does not approve unread values. A replacement includes an exhaustive change ledger:
every old and new field, group, record, column, row and meaning appears exactly once
in a keep/replace/remove/add change, with exact source anchors and a reason. Positional
entity IDs refer to the candidate structure, not data paths. Missing entries cannot
silently delete fields or rows. A keep must be identical; a replacement can split or
merge entities. Each affected owned source is covered by its change evidence. A change
can cite a genuinely empty owned block, without fabricating a nonempty quotation;
logical-row/meaning anchor rules are not relaxed. Missing coverage is reported using
only program-owned entity references, not source or model labels.

Revision evidence quotes reuse the replacement's standard `SourceQuote` definition
instead of displaying the same object schema twice. Owned nonempty source IDs, exact
text constraints and optional zero-based occurrence are unchanged; controls cannot
become quote sources. All original source and history content remains. This reduces
request overhead but does not guarantee that every proposal or repair fits a fixed
budget; an oversized request still stops before model generation.

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

Value-batches v4 retains the v3 source-catalogue sizing policy. If the optional aid
would consume the fixed repair reserve, a batch drops the whole aid and its selector
branch, reports `context_limit`, and retains all original sources, bindings, handles and
ordinary quote choices. This decision is deterministic from the stored integer
`contextLimit`, which is rechecked with the request and response on resume. A response
cannot select a literal not offered by its actual batch. Completed literal reads retain
their exact catalogue for source accounting; unused alternative IDs are not part of the
frozen field definition. This delivery fallback never excludes document information or
increases the call/time/input limits.

Native-structure v18 / native-value-batches v4 keeps the existing single request when
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

### Remaining structure-quality boundary

Source-grounded correction is implemented in `native_structure_revision.py`, including
complete entity-change accounting, atomic replacement and invalidation of dependent
reads. Type/presence filtering and compatible source choices are also implemented.
They are not pending mechanisms, and their regression success is not semantic approval.

A model can still propose syntactically valid item-numbered fields, duplicate them as
one-row records, choose a wrong missing state or fail the replacement's change ledger.
Batching and lossless compression cannot establish the right organization. Review the
actual source roles, whole field/record set, per-occurrence bindings and applicability;
do not impose a business template, fabricate a missing-state answer or silently delete
every scalar sharing a record source. Legitimate repeated fields and equal-valued items
must survive.

The earlier HWP comparison froze titles that later conflicted with whole-paragraph
scalar values. Joint role/structure revision now addresses the inability to edit those
roles, with direct parser/controller and ARM regressions and one accepted real-model
role replacement. Whole-document quality remains unapproved. Large fixed context or repair feedback can remain oversized; known
inner-binding paths can still fail at actual value choice rather than the early overlap
review. Complete interpretation needs source-grounded model decisions, not just a
successful state transition. Neither native note membership nor row position alone
decides the right document role.

The retained-review limitation reproduced by the earlier HWP run is fixed by revision
v9, as described above. The next actual comparison failed before reaching that path:
first-region structural discovery repeated a colliding scalar property key, while the
second region consumed both early review attempts on an incomplete then unchanged
replacement. No value request ran. The model also called a prior inferred title role
native evidence. This does not establish a failure of the new follow-up transition;
it establishes that its regression success is insufficient for complete extraction.

The next investigation is **not yet an implemented protocol change**:

1. Reproduce the duplicate-key and early-review failures using the collected responses.
   Keep the source/specification bytes and both original outcomes unchanged.
2. Design collision feedback around program-owned field references, their common
   property scope and distinct source positions. Repeated labels and equal values
   are legitimate; do not append suffixes, merge occurrences or discard fields by
   policy. Any changed structure must still be source-grounded and compile uniquely.
3. Compare the present potential-overlap review with a review scheduled only after
   concrete value failure. A possible overlap is not a failed read. The comparison
   must cover real title inner values, contradictory whole-title reads, initial
   structure failure, exhausted/unknown review states and downstream role context.
   Accepted native observations must remain separate from prior model role choices.
4. Select and implement the smallest supported change, including request identity,
   attempt/usage accounting and checkpoint compatibility. Check saved-request size
   before another bounded actual run; keep the existing 12-call/900-second/16,000-
   character allowance. Full change accounting cannot be bypassed to accept a no-op
   replacement or to manufacture completion.

This focuses on avoiding unproductive stages, not merely adding prompt instructions,
loosening role/value validation or allocating another automatic review cycle. It does
not establish that a larger model or further compression will fix semantic decisions.

Cross-region logical continuation still requires source-bound earlier-record context
and an explicit relation decision. Nearby text or equal keys cannot authorize merging.
New varied HWP/HWPX/XLSX documents must pass the complete actual path independently;
scripted correction transitions alone cannot approve the KPI.

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

The scalar request grammar bounds `dispositions` and `excludedBindings` by the
number of unique issued source IDs (owned plus context) and binding IDs. It keeps
at most the existing typed-IR limits of 5,000 / 2,000. The compiler already rejects
duplicates and unknown IDs in these two lists, so larger arrays cannot describe a
valid result. This is not a source-count cap on fields, groups, meanings or legitimate
absent fields; their existing limits remain. Prompt v40 separates checkpoints from
the earlier request contract.

The pinned b10853 runtime reproduced the old scalar grammar failure on the exact
recorded XLSX request. Changing only those two limits, from 5,000 / 2,000 to the
7 sources / 4 bindings actually offered, allowed sampling to start. Both versions
passed token-count preflight. The diagnostic generated at most one token; it proves
the specific initialization fix, not full JSON or document quality.

### Prior table mapping context

Region plan v22 uses the **compiled column definitions** when carrying a mapping
into the next slice of the same original physical table. `compiled_table_mapping`
reads the effective `field_definition.sourceRefs`, including compiler-added headers
and already-recorded removal of content-row citations. It does not reuse the raw
proposal's stale column references or make new header/type decisions.

Only these effective column sources are added as mapping context. Whole-record
provenance stays in the earlier result and checkpoint rather than being replayed
as if every record source defined every column. Existing current source text,
header/caption/note context, owned rows and value routes remain unchanged. The prior
raw model response, correction records, complete record provenance and value evidence
are retained. There is no reference-count cutoff or new header-only filter: every
reference in a compiled column definition is retained, including legitimate non-header
evidence. A missing compiled definition cannot silently fall back to the raw proposal.

The engine still chooses the nearest previously compiled mapping from the same
physical table, then measures and re-plans the complete current request before dispatch.
Names/types remain a previous decision for the model to inspect, not an automatic
reusable business template. Ambiguous structures and current row roles still require
decisions. Old plan-v21 checkpoints cannot resume under this changed context policy.
Scripted 50-row preservation is not a claim that the real model completes the document.

### Applicability across a table and its nonrecord content

Scope integration v15 also discovers candidates through the explicit
`parentRegionId` / `tableContextRef` relation created by table value routing. The
child's table reference must exist and equal its parent's physical view reference.
This works in either direction and does not depend on how many unprocessed regions
sit between them in the execution list. Similar labels, an unrelated parent name,
or a table hint without that exact relationship do not establish the connection.

Candidates expose `candidateBasis: tableValueRouting` and the checked relationship.
This is a reason to inspect them, not proof of applicability. Existing source/context
limits, candidate bounds, independent scope decisions and unresolved states remain.
The relationship participates in the candidate fingerprint, so changed routing cannot
silently reuse a saved scope decision. Discovery itself changes no values or meanings.
Scope protocol v7 tells the model that a candidate may merely store a unit/condition's
wording; it must inspect actual value origins instead of treating a present field or
its label as the governed quantity. This removes an inaccurate earlier assurance that
such fields were never offered. It is not a replacement for independent quality checks.

The meaning stage first classifies every owned source as `has_meaning`,
`no_additional_meaning`, `unresolved` or `unreviewed`. A literal empty source cannot
select `has_meaning`. Plain data-cell values need not be repeated as meanings;
source classification does not discard their original text or compiled values.
Validated `sourceSelections` bind source inventory, frozen structure, model identity,
reference dictionary and revision. A stopped detail call reuses the saved selection.

Selection wire v4 / table protocol v27 require one finite status string per offered
source in a closed `sourceDecisions` object. Classification requests no free-form
explanation, reason table, source lists or positional tuples. The decoder requires
exact source coverage and valid statuses; the JSON parser rejects duplicate keys.
It restores inventory order before decoding reference aliases. Equal-valued records
and their source links remain distinct. Every source still requires an explicit
model choice; there is no program-generated default or inferred shared decision.

Canonical selection record v2 stores `explanation: null` and
`explanationState: not_requested` in the hashed record. This is an explicit absence
of requested diagnostic prose, not an invented rationale. Internal canonical callers
can retain supplied explanations (`provided` or `partly_provided`); the wire display
helper rejects those rather than silently dropping them. Compiled negative-source
reviews report the saved status and say that no per-source explanation was requested.
They do not pretend this procedural text is a model-authored reason. Positive-source
remainder explanations, exact quotes and detailed meanings are still required later.
Reselection still requires one overall correction reason and a genuine status change.
Checkpoint validation includes explanation state; old table protocol checkpoints
are rejected before any model call.

Earlier optional reason sharing did not bound actual model output: v23 repeated
source IDs, v24/v25 tuple schemas were unsupported or incorrectly enforced by the
pinned b10853 runtime, and v26 copied a separate explanation for each source until
truncation. A successful token-count preflight alone did not prove generated output
obeyed the schema. No invalid or truncated response was accepted.

The source-selection output cap remains 1,536 tokens. Finite status-only output
removes unbounded explanation prose but does not guarantee that arbitrarily large
source inventories fit. Input planning, exact source coverage and existing document
call/time caps still apply. Truncation leaves the compiled structure as a partial
result; transport and scripted checks are not model-quality approval.
The v27 Spark development comparison completed all 48 HWPX / 37 XLSX source statuses
in 634 / 490 tokens, respectively, without a detail call for these all-negative
inventories. It did not complete either whole document. Prior data cells cited as
record/column definitions inflated subsequent mapping context beyond the input cap;
the scalar child then exposed a separate grammar-repetition failure. Keep definition
evidence distinct from record/value provenance when revising this path. Original
source evidence remains required, including legitimate definitions in headerless tables.


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

`record_fragment_evidence.py` separates zero-record fragments from the joined
array's value (compiler v34). An accepted continuation/duplicate relation can carry
`zeroRecordFragments`: each entry identifies the originating region, repeat, table,
inclusive native row bounds and its unchanged `originalEvidence`. These are local,
pre-join evidence snapshots; their old data targets are not assertions about the
current combined result. `compiledRecordCount: 0` means no records were compiled in
that fragment, not that missing or unresolved source rows were proven absent.

Once the joined array contains records, empty-fragment claims are removed from
active `valueEvidence` but remain on the relation. If the joined array still has zero
records, its aggregate evidence keeps all contributing sources and any uncertainty.
Explicit blank cells, separate equal-valued rows and unrelated repeats remain intact.
Pending, separate or rejected relations do not relocate evidence. Chained relations
record each origin once, and checkpoint resume rebuilds this information from accepted
proposals rather than trusting a stored result projection. Older compiler checkpoints
cannot resume under the new policy. This corrects provenance, not the model's row roles.

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
The original v3 display comparison retained all 51 observed rows and 102 complete
texts, reducing the request from 24,557 to 15,858 characters at the unchanged 16,000
limit. The same scripted 49-row subset resolved identical values and source traces.
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

### Long-table applicability: measured boundaries

Inventory, actual request size and source-binding resources are separate limits.
Before integration v17, the active engine used `build_scope_tasks` to size the
uncompressed candidate payload against at most 12,000 characters. The selection
codec then packed only the retained candidates. The historical v2 binder also reused
the legacy model's 100-reference count. These older limits explain the comparison
below; current inventory and provenance handling are described after it.

At product source `b80b4c7c50b46a9c51f3d7c6213c8955407b0c04`, the existing scripted
two-column fixture gives these sizes, including system instructions and the output
contract. This is offline request construction, not native extraction or a model run.

| Data rows | Discovery allowance | Record candidate retained | Actual request characters |
|---|---|---|---|
| 10 | 12,000 | Yes; complete candidate coverage | 10,773 |
| 50 | 12,000 | Yes; complete candidate coverage | 15,932 |
| 96 | 12,000 or 16,000 | No; bounded coverage | 5,433 |
| 96 | 120,000, diagnostic only | Yes | 22,924 |
| 200 | 120,000, diagnostic only | Yes | 39,844 |

All compiled rows remain present in these fixtures. The smaller 5,433-character
request omits the record candidate; it is not successful delivery of the complete
scope task. Raising discovery alone would expose an oversized request. No configured
input allowance was changed and no diagnostic request was sent.

Under the historical v2 binder, an explicit range covering 49 rows and both columns
bound exactly 100 unique references; 50 rows failed with
`scope_source_binding_budget_exceeded`. A one-column range succeeded through 98 rows
and failed at 99. These counts are fixture-specific, not universal document limits.
The failure was atomic and did not change values or their evidence.

Selecting a whole column instead uses its checked definition scope and can cover
all 200 fixture rows with one reference. It is not interchangeable with a filtered
row selection: the latter verifies each selected value's binding. Do not silently
convert one form into the other to avoid a bound. After a checked continuation, the
root column definition covers all six rows in the two-fragment fixture; the second
fragment's column covers only its last three rows. Whole-column selection therefore
does not have the suspected first-fragment-only defect in this case.

### Complete inventory and request preflight

Scope integration v18 / protocol v10 retain `build_scope_inventory` before request
planning. Eligibility still follows the existing same-region, adjacency, note-link
and explicit parent-table routing rules; this does not add arbitrary cross-document
targets. The old `build_scope_tasks` display-limited helper remains for compatibility,
but is no longer the engine's discovery path.

Scope inventory v1 admits up to **1,024 candidates and 4 MiB of compact UTF-8 JSON**
for candidate payload, private mappings and source signature. Final serialization is
checked again after containment links are added. This content measure excludes the
small inventory diagnostic itself and is not a process peak-memory guarantee.
Node text, scalar raw origins and note links are retained without the legacy
500-character/eight-link display clipping. Missing required context is still incomplete.

`inventory.status` is `complete`, `no_candidates`, `incomplete_context` or
`resource_limited`. Retained and omitted counts are explicit; counts not established
because the base content already exceeds the allowance remain unknown, not zero.
A bounded prefix may remain inspectable but cannot be sent as a complete task.
Candidate text and mapping hashes, including candidates omitted by admission, enter
the task fingerprint. `complete` describes this eligible inventory, not semantic
correctness or proof that every possible target in the document was discovered.

For complete inventories, `scope_readiness` constructs the existing selection wire
and measures all message content: system instructions, payload and output schema.
`ready` means it fits the effective input-character allowance. `requires_partition`
means the complete request is too large. Unavailable inventories and invalid catalogs
receive separate diagnostics. Ready whole tasks reach existing independent-task
batching; oversized complete tasks use the partition path below rather than sending
an incomplete candidate prefix.
Tokenizer, model-context and output-reservation checks remain separate.

Inventory diagnostics are exposed through `coverage.scopeIntegration`, not sent to
the model. Readiness issues are recomputed from current tasks, not restored as stale
errors from a checkpoint. Inventory policy joins the execution identity; older
incompatible scope checkpoints are rejected. Model selection and public v1 contracts
are unchanged.

In the same offline fixture, 10/50 rows retain all three candidates and fit
10,773 / 15,932 characters. The 96/200-row inventories also retain all three candidates,
but the requests require 22,924 / 39,844 characters and remain `requires_partition`
at 16,000 in whole-task preflight. Partition v1 now delivers these fixtures in bounded
windows as described below. No actual model call is part of this comparison.

### Compiler-owned scope provenance

Source binding v3, introduced with integration v16 / protocol v8 and retained by
integration v18 / protocol v10, separates a model selection
from its compiler-produced source union. The legacy `ScopeDecision` JSON schema,
including its 100-citation bound, is unchanged. The active selection wire still
accepts no citations from the model. Only after checking current definitions, row
mappings and selected value bindings does the binder issue an in-memory
`BoundScopeDecision`. Plain serialized data cannot select this validation path.

The compiler retains every bound reference in the public `scopeEvidence` and semantic
source lists. It does not turn a row range into a whole-column selection, truncate
sources, invent missing values or discard equal-valued rows to pass a count limit.
The 50-row/two-column fixture now binds 102 references; the 200-row fixture binds 402.
Their canonical row selections and data are unchanged. These are compiler checks,
not proof that the corresponding complete model requests fit or are interpreted correctly.

Resource checks remain finite: at most **1 MiB of combined compact UTF-8 JSON** for
one canonical decision and its source trace, **1,000 trace entries**, and **1,000,000
row/column work units**. References repeated in trace entries count toward the byte
budget; the public source list is deduplicated in source-binding order. Each trace
entry is checked before inclusion and the final serialization is checked again.
An over-budget decision fails atomically; a partial provenance prefix is not accepted.
This byte limit is not a document input limit, model allowance or memory qualification.

For row-filtered values and scalar origins, each trace entry also records
`valueProofSHA256` over its exact binding, raw text, status, transformation, source
references, destination and current compiled value. Joined rows use their current
data owner. Applicability links are excluded because applying another meaning can
legitimately change them without changing a value. Whole-column selection continues
to use the checked column definition rather than masquerading as a row filter.

Checkpoint replay reconstructs the decision and trace from the saved citation-free
selection, then compares both with the stored records. It never loads a saved tag as
authority to bypass source binding. Changed values, bindings, missingness or proofs
are rejected. Older scope policies are incompatible before another model call.
Public v1 values, evidence contracts and API names are unchanged.

The large-provenance engine regression now uses normal complete inventory discovery
for its 50-row HTML example. At its configured 120,000-character input allowance,
four scripted calls complete the result with 103 source references; JSON checkpoint
replay preserves the result without another call. No discovery override is used.
This does not demonstrate that this HTML request fits the 16,000-character model
profile, nor does it establish actual-model or native-format quality.

### Partitioned applicability

`scope_partition.py` implements partition v1 for complete inventories whose whole
request exceeds the effective character allowance. The plan is deterministic and
does not authorize extra model calls or time. Whole tasks that already fit retain
their existing wire; `no_target` is added only to the partition response contract.

**Planning.** Related candidates stay together: each record includes its column and
header-group handles, and containment links join overlapping candidate families.
The planner bisects families, then source-row ranges of a single record family if
necessary. Each proposed window is sized with its complete system instructions,
payload and output schema. Wide indivisible definitions or connected multi-record
families are not silently reduced to fit; a leaf that still exceeds the allowance is
`context_unavailable`. Column partitioning of such a family is not implemented.

**Row ownership.** Every window retains the meaning, surrounding context and column
definitions. It removes only data-row context exclusive to other windows. Shared
source nodes, header definitions, notes, unresolved rows and reference links remain
exact. Selectable row boundaries keep their original coordinates and original
`dataRowNumberInFragment`; numbering does not restart. A window's `allDataRows`, direct
column or header-group choice decodes to an explicit row intersection against the
original task. It cannot become a whole-column definition or select a foreign window's
row reference. Source binding still checks the original unsliced compiler mapping.

**Review and aggregation.** Each response explicitly chooses `apply`, `no_target`
or `unresolved`. `apply` selects the applicable subset and reviews the rest of that
window as excluded; the other two require empty selections. Missing or invalid
responses do not become negative reviews. Checked selections are combined only over
their offered ranges; adjacent identical column/range selections can be coalesced
without crossing a negative or unseen interval. All-negative review leaves the
meaning unresolved rather than inventing an applicable target. Unknown row roles or
missing source coordinates cannot prove complete applicability.

When budget, cancellation or an undecided window stops the work, compiled data and
checked partial links remain. If the combined source proof exceeds its resource
limit or overlaps incompatibly, an admissible checked partial union is retained.
Coverage lists `appliedWindows`, `unappliedWindows` and the aggregation issue; all
positive window answers/proofs remain saved. This is explicitly partial, not a
successful truncated source list. Resource limits are not waived to report success.

**Checkpoint replay.** `scopePartitions` stores the plan identity, each actual wire's
fingerprint, response and compiler proof, plus an exact value/evidence identity for
eligible regions. It does not trust a saved request body. Replay reconstructs the
plan and wire, decodes each response, rebinds
its sources and compares the saved proof. Negative reviews also depend on unchanged
values, source context, row roles, header membership and joins. Public coverage flags
are recomputed. Changed scope policy or stale proofs are rejected before a new call.
Ordinary resume skips answered windows; invalid or unresolved answers are retried only
with an explicit additional call allowance. Cancellation can resume within unused
existing allowance. Every invocation uses the cumulative document budget.

The plan has at most **128 windows** and **8 MiB of retained serialized window
payload/schema content**. Saved window responses/proofs have a separate **8 MiB
per-task state bound**. These are operational content limits, not peak-memory or
CPU-only qualification. A plan exceeding its bound sends no prefix. Inventory and
source-binding limits remain independent.

The 96/200-row scripted fixtures fit 4/8 windows under 16,000 characters, preserving
every observed row once, every exact source text, 192/400 linked values and 194/402
references. Tests also cover header groups, blank/subtotal/note and missing rows,
positive/negative/unresolved/unseen states, invalid siblings and changed-value replay.
The HTML engine scheduling tests isolate a 12,000-character scope preflight while
all invocations obey the configured 16,000 cap; they are not a complete native-format
model run. The next actual HWP/HWPX/XLSX evaluation must establish complete table and
meaning extraction under a predeclared budget, then review the model's window decisions
against independent expectations. Scripted delivery success does not replace that check.

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
| Semantic prompt / region plan / result compiler | v40 / v23 / v36 |
| Document outline / native role-content protocol / native structure | v1 / v14 / v19 |
| Native structural response wire / native value batches / structure revision | v3 / v4 / v9 |
| Table protocol / table reference wire / table source wire / selection wire | v29 / v2 / v1 / v4 |
| Table source inventory | v2 |
| Scope integration / scope-axis protocol | v18 / v10 |
| Scope inventory / scope partition | v1 / v1 |
| Native observation / native note objects | v4 / v1 |
| Scope row-axis wire | v2 |
| Scope selection wire / context display / source binding / regional checkpoint | v3 / v1 / v3 / v3 |
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
