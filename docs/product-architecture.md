# Product architecture — approved implementation, 1.8.0

The current approved scope is printed Korean/English documents, including native
formats, PDF/scans and cross-page tables/notes. CPU-only full extraction targets a
16 GB host. HTTP is single-company installation, not multi-tenant SaaS. Intel Mac
full recognition uses a local Linux CPU container; native existing features remain.
This document describes the implementation target, not a qualification certificate.

## Stable boundaries

Input snapshot → immutable observations → structure relations → semantic decisions
→ source-bound compiler → public results. Document Files owns these representations;
Docling, OCR and model objects do not become public contracts. Native locations,
lexical values, formulas, caches and uncertainty remain distinct. Observation/model
revisions identify results but never trigger an automatic Corpus reanalysis.

`AnalysisJob v1`, `AnalysisResult v1`, byte streams and existing CLI/MCP names remain.
Public extraction v1 contracts are independent of private model proposals. New
structure and semantic detail fields are additive and versioned. Historical result
readers remain usable. Model protocol v8 uses region-local fields/groups/repetitions
and precomputed source binding IDs, not model-written values or JSON pointers.
An explicit legacy protocol adapter remains for existing scripted integrations.

Each request constrains source and binding references to the candidates actually
issued for that region. New scalar decisions explicitly select a binding and
presence state. For older compact decisions that omitted a binding, the compiler
can resolve a single delimiter-declared label/value pair within the field's cited
source; it records this derivation and refuses ambiguous alternatives. It does not
search for equal-looking values elsewhere or infer a company-specific field rule.
Verified complete label/value spans and bound table cells are accounted for by the
program, not repeated once per row in the model's response. This usage ledger does
not establish semantic correctness or excuse omitted units, notes or conditions.
Malformed scope IDs leave an unresolved source-linked statement without discarding
the valid values in the same region. Repair diagnostics survive budget exhaustion.

## Components

- `document_model`: native observations, precise binding candidates, region context,
  table geometry, explicit relations and optional offline PDF recognition adapters.
- `interpretation`: compact decisions, source-bound compilation, local correction,
  cross-region integration, checkpoints and explicit budgets.
- `jobs` / HTTP: durable SQLite jobs, one supervised process, private snapshots,
  cancellation/resume, configured model profiles, authenticated streaming uploads.
- `runtime_packs`: checksum-pinned offline installation/activation/rollback, CPU
  llama.cpp, separately pinned Qwen3.5-9B Q4_K_M and recognition assets.

## Selected backends

Keep existing Office/HWP parsers and checkbox preservation. Native Markdown uses
markdown-it-py, PDF uses PDFium/pdfplumber. Recognition uses Docling Standard PDF,
Heron, TableFormer accurate and explicit Tesseract `kor`/`eng`, all CPU/offline.
Use original observed table cells, never synthesized blank grid cells as evidence.
Native and OCR text conflicts remain visible. No library's cleaned text replaces
the immutable original observation.

An opt-in `ruled_tables_v1` repair uses the already computed layout TABLE boxes.
It removes verified long ruling lines only from a separate bounded OCR crop; the
source page and TableFormer image are unchanged. Original and transformed OCR are
retained separately; overlapping different tokens remain conflicts. A repaired
`o` is never silently changed to `0`. Only budget-interrupted repairs are resumable;
an OCR mistake is not an instruction to loop. The default policy remains off until
a specific pack/profile is explicitly prepared and qualified with it.

The opt-in `ruled_cells_v2` alternative segments only proven closed grid cells.
It crops original glyph pixels to their ink bounds, adds white padding, and selects
single-line or multi-line Tesseract segmentation from geometry. Empty ink, no OCR
output, failed recognition and unsupported merged/boundary-touching cells remain
different states. Coordinates use the actual rendered canvas scale, not a presumed
DPI ratio; whole-page rendering and per-cell OCR remain budgeted. Each committed
cell is reused on explicit budget resume, without changing the old v1 policy.

A structure-only view orders candidates by grid cell, geometric text line and x
position. A token may be excluded from that view only when all its observed ink
lies within verified long grid strokes, never by its spelling. The original OCR
text, geometric counter-evidence and unresolved conflict remain in observations.
Docling's pinned layout postprocessor sorts candidates by index. The structure
view therefore receives new order indices on deep copies, with an explicit mapping
back to unchanged source indices. Reordering the list alone is insufficient.
[Upstream implementation](https://github.com/docling-project/docling/blob/v2.126.0/docling/utils/layout_postprocessor.py)

The corrected path recovered all 11 nonempty cells, including headers, in the
existing public scanned-table case through actual TableFormer. This is not a proof
that a missing table cell is blank or that the whole scanned document is understood.
Published candidate packs are not silently rewritten to enable the new policy.

The semantic runtime starts lazily after recognition releases its process. Calls
are bounded by region and include ancestors, headers, units and notes. Committed
regions survive errors; repair only changed regions and their dependents. Natural
language conditions are data, never executable code.
The fixed llama.cpp grammar adapter projects only large array cardinality bounds
out of the sampler grammar to avoid a verified upstream grammar expansion failure.
The original internal contract, post-generation Pydantic checks, and token/byte
budgets remain intact. This is tested for the product's internal decision schemas,
not a promise to support arbitrary external JSON Schema grammars.

After table continuation remaps generated pointers, unresolved unit/note/condition
statements can be linked to same-region definitions, bounded neighboring definitions
or explicit native note references. Same-region membership is not applicability proof. The product issues target handles and resolves their scope; the model
does not rewrite data or pointers. Different statements in one paragraph remain
separate. Truncated context and missing candidates keep an uncertain result. Saved
scope decisions are reused only while their statement/context/target fingerprint
matches; an unchanged unresolved question is not sent repeatedly. Up to eight
independent scope decisions share one bounded request. Invalid or duplicate decisions
do not discard valid siblings. A malformed/omitted decision can be retried only
with a new explicit model-call budget grant; a valid unchanged unresolved decision
is not automatically repeated. This repair stage never asks for the full schema or
values again. Scope integration v2 participates in checkpoint identity.

### Bounded non-table source views

An oversized text region is partitioned into disjoint source views. Original nodes
and bindings are not rewritten. Each view carries original character ranges and
program-issued binding IDs; declared label/value pairs stay atomic and each value
has one owning view. Adjacent text is read-only context, not another value owner.
Accounting and semantic source text refer to the view actually read. A source node
counts as fully read only after every owning view has been interpreted.

Indivisible values, cross-node atomic references, or explicit context larger than
the selected budget remain pending with a specific budget reason. No chunker can
make an arbitrarily large indivisible value fit by silently truncating it. The
region-plan version is part of checkpoint identity; old results remain readable,
but incompatible checkpoints are not automatically replayed.

### Smaller model-facing contracts

Only reachable schema definitions are sent for each region. A text-only region
cannot emit table repeats, so its empty-array contract does not include the unused
table/column graph. Cosmetic schema titles are removed, while reference enums,
types, limits and validation remain. New fields/columns explicitly select their
value type; ordinary labels do not need duplicate meaning entries. Historical
typed IR defaults are preserved for compatibility.

The contract stays in both the prompt and decoder request. A decoder constraint
does not itself teach the model the required structure, as described in the
[pinned llama.cpp grammar guide](https://github.com/ggml-org/llama.cpp/blob/9dcf84e5ae2718947188b539aab8b9c2b15d3ba1/grammars/README.md).

## Safety and delivery

No implicit external endpoints, model downloads, macro/script execution, source
mutation, or source/key/prompt logging. Native subprocess isolation is not a security
sandbox. Offline container workers have no external network. HTTP accepts bytes and
registered profile names, not arbitrary paths, endpoints or executables.

Build core/runtime/model packs separately, with hashes, licenses, SBOM and provenance.
Keep previous activated packs and results for rollback. Manual retention remains the
default. Preserve the separate personal reconstruction-context default; project
profiles explicitly disable it. Existing creation/editing functions are retained,
not replaced by a new document renderer or a company-specific back office.

## Release gate

Code, package availability and qualified support are separate. Related regressions
run without inference; real-model checks run only for affected behavior and the final
candidate. Independent public holdouts must test actual completeness, not merely
schema validity or honest partial results. CPU runtime, installed clients, HTTP and
container use need direct evidence before a supported release. No release or Toolkit
pin activation may claim these checks passed while they are still pending.

### Table request sizing

Region plan v4 sizes table views against the serialized request, system prompt,
current output contract and target-schema catalog, instead of reserving a fixed
12,000 characters for every schema. Required headers and context are still included.
Indivisible oversized rows remain explicit partial results. Long identical reference
enums are shared with internal JSON Schema references; the allowed choices and
post-generation compiler checks are unchanged. CPU tokenization remains the final
context guard, independent of this character-based preparation budget.

When a native table must be sliced, its leading declared header rows stay as
context for each data view, with explicit `headerCells` row/column/span geometry.
They are not emitted as an artificial header-only region. An all-header form is
not dropped, and no header is guessed for OCR tables. Table-context nodes can
explain definitions and scope but do not offer scalar value bindings in another
region; their own source region retains actual values. This avoids duplicate
reference choices and prevents context-only labels from becoming value candidates.
