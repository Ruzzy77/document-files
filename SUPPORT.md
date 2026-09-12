# Extraction readiness and priorities

Current capability, confirmed defects and the next acceptance checks. Last verified:
**2026-09-13**. Implementation and checkpoint behavior belong in
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

The full local check including the native outline path passed **2,953 tests,
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

The current internal contracts are document protocol v7, native-structure v11,
structure-revision v4, prompt v39, region plan v20 and compiler v32. Public v1 result
and API contracts are unchanged. Old incompatible checkpoints cannot resume.

- **Document roles and hierarchy:** source text, native markers, direct formatting
  and XML references remain separate from inferred title/heading/caption roles.
  Ambiguous roles and unjoined long-document fragments remain partial. Font size or
  alignment alone does not determine a logical role.
- **Native prose and forms:** logical item rows no longer require a physical table.
  Structure defines attributes, types, ordered occurrences, missing states and exact
  meanings before value choices. Value reading cannot silently rename or remove them.
- **Values and provenance:** code reads original bindings or exact quoted substrings.
  Impossible type/state/row choices are excluded, but an offered choice is not proof
  that it belongs to the right attribute. Two equal values require their own correct
  source positions. Blank, absent, unreadable and uncertain remain distinct.
- **Bounded requests:** repeated source metadata is shared losslessly, including
  formatting and native references. Oversized value work can be divided into checked
  batches without discarding context or replaying completed batches. Real cross-region
  logical continuation is still missing.
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

## Latest Spark-B verification

The actual product source **f277210db781d313b92b7a7cf31bdeec307e81e4** (native-structure
v10 / revision v3) was tested with the existing Qwen3.5-9B Q4_K_M components and a
separate, task-owned CUDA server. Python 3.12.3 passed **357 tests**, with eight
fixture-authoring tests deselected; those are not deployment-environment passes.

The unchanged eight-paragraph HWPX seed receipt returned **partial after 3 calls /
72.2 seconds** under the fixed 12-call / 900-second document allowance, 16,000-character
input limit and 3,072-token output cap. Typed decoding used `json_schema`, `strict=false`.
Actual server token probes matched usage and preserved the configured output reservation.

Both structure proposals contain two item rows, but the second item's requested count
is incorrectly blank and its received count incorrectly absent. Their row-local states
also cite a separate paragraph outside the allowed row anchors, so the compiler rejects
both with `logical_value_evidence_outside_occurrence`. Extra title/unit/handling fields,
per-item receipt dates and missing separate applicability remain. **No item values are
accepted; neither value reading nor the new structure-review stage is reached.**

All 24 displayed source views, original nodes/bindings, committed source and input hashes
match. This source-preservation check does not excuse the semantic failure. The separate
server/container and temporary source/input copies were removed after evidence collection;
shared RPC settings and host OOM stayed unchanged. Evidence: private
`structural-kpi-20260913/native-review-24/`.

### What the targeted diagnostics establish

Two four-call comparisons on the same source requests (`native-prompt-study-22/` and
`native-schema-study-23/`) tested shorter instructions, record-first property ordering,
exact value substrings and typed decoding. Neither wholesale prompt replacement nor
property reordering passed structure quality; neither was adopted. Typed decoding fixed
syntax/types, not missing row states or wrong organization. Explicit substring wording
selected numeric literals, but both equal counts still cited the first occurrence.
Accepted roles alone did not prevent a whole-title value choice.

Lossless prior-decision tables reduced the previously blocked review request from
18,819 to 15,878 characters, preserving every previous structure/role value. A separate
one-call probe of that review returned `retain` in **6.4 seconds**, citing unchanged
source text. It reached the model, but answered source-change detection instead of
reviewing the failed extraction. It did not approve or replace the old partial result.
This is a stage diagnostic, not checkpoint resume or complete-product qualification
(`native-review-probe-25/`).

Revision v4 now explicitly asks for correctness review on the **same** source. Its
complete request measures **15,980 characters** at the same 16,000-character limit;
source/history and acceptance rules remain unchanged. A one-call comparison returned a
replacement in **56.4 seconds**, but it omitted both old and new meanings from its change
ledger and was correctly rejected. Its 11 scalar fields, values used as labels and lack
of item records also fail semantic review. The actual feedback would make the request
16,102 characters, so there was no repair call or increased allowance. This is a targeted
stage comparison, not a v11 full-product pass (`native-review-probe-26/`).

The next comparison will test occurrence-first structure decisions rather than repeat
whole-document inference or add accounting-only repairs. That design is **not implemented
or adopted as successful**; its boundaries and acceptance checks are in the extraction
engine document. Exact source occurrences, complete metadata/missing states and review
headroom must be checked alongside row grouping.

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
installation was added, and the shared RPC/model services were not reconfigured.

Owned servers use loopback authentication, read-only components, four CPUs, a 16 GiB
cgroup limit with no cgroup swap allowance, and host-memory/OOM safeguards. The host has
swap configured; GPU allocations are not fully represented by cgroup memory. These GPU
development checks are **not CPU-only 16 GiB, whole-host no-swap, GPU-memory, managed-pack,
platform-installer or independent quality qualification**.

## Unresolved defects and next implementation order

| Priority | Defect / owning code | Required correction and acceptance check |
|---|---|---|
| 1 | **Native complete extraction is not approved.** The latest Spark-B product proposes two rows but mislabels the second item's counts and cites missing-state evidence outside the row's allowed anchors. Neither structure attempt compiles. Other runs retain per-item scalars, title copies and wrong equal-valued source occurrences. | Reassess the structure-decision and repair representation before another broad run. Distinguish values inside an item from separate metadata/missing-state evidence; do not relax row ownership as a shortcut. Confirm logical rows, all statuses and distinct source positions together. The smaller review request now fits, but a callable review is not a correct replacement. Preserve atomic transitions and finite budgets, then implement genuine cross-region continuation. |
| 2 | HWP/HWPX and XLSX have not been characterized across enough different layouts and forms. Existing HTML/PDF development examples do not establish native-format accuracy. | Check current observations and the complete path against independent expectations. Cover role/reading hierarchy, title/caption ambiguity, label/value forms and prose records, merged/nested/continued tables, subtotal/note rows and long content. Role repair alone has not resolved the whole-result defects. Compare equivalent content in different layouts as well as genuinely different forms; do not force a fixed template. |
| 3 | Repeated condition fields and blank-cell scalars remain in the latest continued/form outputs. Existing review checks can accept original node text instead of the actual bound substring and do not fully check duplicate folding. | Review actual values, binding ranges, field set, order and applicability. Remove redundancy only when source/role/representation prove it; preserve legitimate repeated values and explicit empty cells. |
| 4 | Long-table requests and scope provenance still hit fixed limits. A 50-row request measured 23,624 characters against a 16,000-character limit; larger cases reach candidate/source limits. | Compact repeated geometry and verify every selected binding through a bounded representation. Preserve all rows, order, page links and missingness. Do not simply raise caps, trim the tail or turn partial into success. |
| 5 | New HWP/HWPX and XLSX documents have not passed independent end-to-end structural and semantic review. | Freeze unseen native inputs with prior expected structure, values, relationships and provenance; run the actual product path on Spark. Review the full result independently of execution. A failed holdout used for a fix becomes a development case. |

Fix reproduced preservation defects before another broad inference run. Run only
affected examples within a predeclared budget; retain raw failures and do not
increase that budget automatically. A result-to-file writer, visual editor and
pixel-matched reproduction are not prerequisites for this work.

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

There is no separate response-time SLA. Evaluation defaults remain short **12 calls /
900 seconds**, long **64 calls / 3,600 seconds**; development comparisons can use an
explicit different budget recorded before execution. Neither a passed script nor an
engine `complete` is a substitute for the original/result comparison.
