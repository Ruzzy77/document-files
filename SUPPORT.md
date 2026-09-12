# Extraction readiness and priorities

This file records current capability, checked outcomes and unresolved defects.
Implementation details belong in [the extraction engine](docs/extraction-engine.md),
not in an accumulating experiment log. Last source audit: **2026-09-12**. Current
native-format and relation development checks used **b97fe61df3c3195f592e50f7099724e3ea14d9ed**.
Earlier HTML/PDF runs used **8744a5c1c89b87370c8b6dd3c8d49cd464400277**. Neither set
is independent approval of the structural KPI.
Version **1.8.0 is not formally released**.

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

The full local check after the row-preservation corrections passed **2,657 tests,
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

## Native-format and relation checks on Spark

The corrected source ran on Spark-A with the installed CUDA/model packs and Python
3.12 recognition-pack runtime. ARM regressions passed **213 tests** before inference.
Expected answers stayed outside the extractor input.

| Check | Actual calls / seconds | Result |
|---|---|---|
| Relation stage, four equal-cell-text contexts | 4 / 91.8 | New occurrences → continue; same-page copy → duplicate; different register → separate; ambiguous identity → unresolved. This checks only the relation stage, not whole-document quality. |
| HWPX record table | 3 / 38.9 | Two ordered records, exact values and column/value sources are correct. The title and caption became two generic scalar fields instead of distinct document roles. Engine `complete` does not close this structural defect. |
| XLSX record table | 2 / 25.6 | The same records and source bindings are correct, but four generated display-text candidates remain unaccounted. Result stays `partial`. |

Both native cases remain development-only and below the full KPI. Raw requests,
results, frozen expectations and independent-of-inference comparison are retained in
`private/qualification/structural-kpi-20260912/first-native-01/`. Source/input hashes
matched; owned servers and the container stopped; shared services and installed packs
were unchanged. The run used a 16 GiB cgroup limit with no cgroup swap or OOM, but GPU
memory was not captured by that counter; it is not a CPU-only 16 GiB qualification.

## Earlier HTML/PDF development evidence

These earlier HTML/PDF runs exercise shared interpretation code, not independent
HWP/HWPX or XLSX quality. The following runs used the model-run source above. Their 182 source files were
rechecked against the committed tree, and collection hashes were checked. Expected
answers were kept outside extractor input. These documents have already influenced
development and cannot be reused as independent holdouts.

| Case / evidence directory | Actual calls / seconds | Checked output and remaining qualification limits |
|---|---|---|
| Merged-header HTML / `arm-gpu-html-product-09` | 5 / 158.3 | Two rows, identifiers and decimal spelling retained. mm applies to Length and Width; the condition applies only to Length. |
| Native/raster duplicate PDF / `arm-gpu-pdf-product-21` | 18 / 424.5 | Two records retained after joining repeated presentations. A development result, not general duplicate-detection proof. |
| Continued PDF table / `arm-gpu-pdf-continued-19` | 17 / 427.3 | Four rows and quantity applicability retained. The final output still contains a repeated condition field. |
| Delivery form / `arm-gpu-pdf-form-09` | 10 / 247.6 | Three item rows and subtotal values 34 / 160 retained. A blank-cell field and insufficiently strict review predicates remain. |

All four returned engine `complete`; that does not close the issues below. Private
raw evidence remains under `private/qualification/dgx-preparation-20260909/`.
Some old run-plan fields say 1,024 scope reasoning tokens while actual requests used
2,048, and some source/patch receipt fields are stale. Preserve those originals;
use the separately checked source-file identity, not a rewritten success receipt.
GPU cgroup measurements must not be advertised as a CPU-only 16 GiB qualification.

## Row-preservation correction

Compiler v27 no longer infers a header from definition citations or numeric spelling.
Native-declared header-only rows use the same geometry rule as the table structure
protocol. Other data, subtotal and note roles stay intact. Content-cell citations
are removed when other citations remain; content-only definitions are uncertain and
request structural repair. An exhausted repair does not report complete extraction.

Local regressions cover English/Korean text records, numbers and precise strings,
explicit empty cells, subtotal/note roles, predicted or mixed headers, repeated
headers, exact value bindings, bounded repair and incompatible old checkpoints.
These deterministic checks close the reproduced compiler defect. They do not approve
AI row classification or primary-format quality; affected real-model examples still
need bounded reruns on the corrected source.

Prompt v31 also removes the forced choice between continuation and duplicate
presentation when cell text matches. Equal-valued new records may continue; identical
presentations may fold only after the contextual decision and compiled-row check.
Regressions cover all four decisions, original per-page bindings and prior-checkpoint
rejection. The bounded real-model relation comparison above matched all four contexts;
general duplicate/continuation quality still requires varied document tests.

## Unresolved defects and next implementation order

| Priority | Defect / owning code | Required correction and acceptance check |
|---|---|---|
| 1 | **XLSX display coordinates become false label/value candidates.** `document_model/native.py::bind_spans` reads program-rendered `A2=Alice` as another value, while `regions.py` also requires the actual native cell binding. | Separate generated address/display text from source cell content. Keep native values, formulas/caches and genuine label/value text inside cells; prove candidate coverage without ignoring all unaccounted bindings or hiding distinct values. Regress on the frozen XLSX case and typed/multiline cells, then rerun only affected native cases. |
| 2 | **HWPX title and caption are ordinary scalar fields.** The native table case preserves two text occurrences but does not distinguish their document roles. | Trace native role/containment observations through regional interpretation and output. Preserve title/caption occurrences as structure and context, not invented business fields. Do not solve this by deleting equal strings: identical text can have different roles. Check ordinary prose and genuine label/value fields as counterexamples. |
| 3 | HWP/HWPX and XLSX have not been characterized across enough different layouts and forms. Existing HTML/PDF development examples do not establish native-format accuracy. | Use independently prepared expectations to check the current native observations and complete extraction path. Cover section/reading hierarchy, label/value forms, record tables, different merged-header structures, nested/continued tables, subtotal/note rows and long content. Compare equivalent content in different layouts as well as genuinely different forms; do not force a fixed template. |
| 4 | Repeated condition fields and blank-cell scalars remain in the latest continued/form outputs. Existing review checks can accept original node text instead of the actual bound substring and do not fully check duplicate folding. | Review actual values, binding ranges, field set, order and applicability. Remove redundancy only when source/role/representation prove it; preserve legitimate repeated values and explicit empty cells. |
| 5 | Long-table requests and scope provenance still hit fixed limits. A 50-row request measured 23,624 characters against a 16,000-character limit; larger cases reach candidate/source limits. | Compact repeated geometry and verify every selected binding through a bounded representation. Preserve all rows, order, page links and missingness. Do not simply raise caps, trim the tail or turn partial into success. |
| 6 | New HWP/HWPX and XLSX documents have not passed independent end-to-end structural and semantic review. | Freeze unseen native inputs with prior expected structure, values, relationships and provenance; run the actual product path on Spark. Review the full result independently of execution. A failed holdout used for a fix becomes a development case. |

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
