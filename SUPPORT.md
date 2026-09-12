# Extraction readiness and priorities

This file records current capability, checked outcomes and unresolved defects.
Implementation details belong in [the extraction engine](docs/extraction-engine.md),
not in an accumulating experiment log. Last source audit: **2026-09-12**, product
commit **8744a5c1c89b87370c8b6dd3c8d49cd464400277**. The later documentation commit
`ea30cb9` records the same implementation. Version **1.8.0 is not formally released**.

## Current scope

1. **DGX Spark:** reliable document structure, schema, values, relationships and
   source links. Linux ARM64 recognition on CPU; internal interpretation on CUDA.
2. **Personal Mac use:** adapt and check the accurate extraction path after Spark.
3. **Deferred:** Windows, Intel Mac and Linux x64 qualification; five-platform
   distribution; CPU-only 16 GiB qualification; publication and downstream application
   upgrades. Existing builders and interfaces remain available for explicit use.

Company-specific back-office implementation, personal source reconstruction and
cloud-model quality certification are not part of this completion target. Installed consumers and active model packs remain unchanged.

## What is implemented and what is checked

| Area | Current state |
|---|---|
| Native formats | TXT, Markdown, HTML, DOCX, HWP/HWPX, XLSX and PPTX parsing and common structure extraction are implemented. Broad independent AI quality is not established. |
| PDF and scans | CPU recognition, source-bound pixel review, additional literal reading and reviewed alternative tables are implemented. Development examples ran through this path; arbitrary scans are not qualified. |
| Tables | Structure/content/applicability are separated. The compiler reads all mapped rows and preserves precision and source bindings. **Two data-preservation risks remain below.** |
| Jobs and APIs | Python/CLI/MCP and authenticated HTTP use the same engine. Cancellation, saved results and explicit-budget resume have regression coverage. An installed Spark HTTP service has not been qualified. |
| Spark | Actual bounded GPU extraction runs exist with the installed ARM64 recognition, CUDA runtime and vision packs. These are development runs, not independent quality approval or CPU memory qualification. |
| Mac | Existing native core and CPU pack preparation are retained. Personal 1.8.0 end-to-end use still needs a separate check. |
| Distribution | Pack builders, integrity/license checks and manual CI are retained. There is no qualified public 1.8.0 release; no consumer migration is claimed. |

The full local check after the independent-package corrections passed **2,637 tests,
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

## Latest Spark development evidence

The following runs used the same product source above. Their 182 source files were
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

## Unresolved defects and next implementation order

| Priority | Defect / owning code | Required correction and acceptance check |
|---|---|---|
| 1 | **A real text-only data row can be removed as a header.** `interpretation/compiler.py` relabels a fully definition-cited row when no cell is a bare number. A synthetic Name/State table loses Alice/active and keeps only Bob/paused, with no issue. | Do not let model definition citations override observed data ownership. Preserve the row; report conflicting definitions for repair or uncertainty. Test real text rows, valid repeated headers, numeric rows, subtotal/note rows and source links. |
| 2 | **Equal values remove the `continue` choice.** `interpretation/regions.py` sets `rightRepeatsLeft` from matching cell text/positions; `engine.py` then offers only duplicate/separate/unresolved. Separate transactions with identical values can genuinely continue. | Keep the distinction between an additional record and a second presentation of the same record. Equal text is a candidate signal, not identity proof. Test equal-valued new rows, exact repeated presentations, explicit continuation context and retained per-page provenance. |
| 3 | Repeated condition fields and blank-cell scalars remain in the latest continued/form outputs. Existing review checks can accept original node text instead of the actual bound substring and do not fully check duplicate folding. | Tighten the prior expectations and review the actual values, binding ranges, field set, order and applicability. Remove redundancy only when source/role/representation prove it; preserve legitimate repeated values and explicit empty cells. |
| 4 | Long-table requests and scope provenance still hit fixed limits. A 50-row request measured 23,624 characters against a 16,000-character limit; larger cases reach candidate/source limits. | Compact repeated geometry and verify every selected binding through a bounded representation. Preserve all rows, order, page links and missingness. Do not simply raise caps, trim the tail or turn partial into success. |
| 5 | Broader unseen-format and scan quality is not independently approved. | After relevant fixes, use new prior-answer documents on Spark. Check exact structure, all values, definitions, units/conditions/notes, blank/absent/unread states and both field/value provenance. A failed holdout becomes a development example. |

The first two defects were reproduced without a model call. Fix those invariants
before another broad inference run. Then run only affected examples within a
predeclared budget; retain raw failures and do not automatically increase that budget.

## What finishes the Spark extraction work

- The data-preservation defects above are fixed with directly relevant regressions.
- New independently reviewed documents cover native formats, native and scanned PDF,
  merged and continued tables, scalar forms and multi-page/long content. All required
  values, relationships and source links match the prior expectations.
- An oversized or unresolved document exposes the missing work in a useful partial
  result; that separate failure-behavior check does not count as accuracy success.
- The actual Spark product entry point runs the pinned recognition and inference
  configuration, records total cost, and preserves results across explicit resume.
  No provided model answers or outside-agent interpretation stand in for this path.
- Operation remains bounded and safe on the shared host, with no source mutation,
  hidden model download or unrequested external document transfer.

There is no separate response-time SLA. Evaluation defaults remain short **12 calls /
900 seconds**, long **64 calls / 3,600 seconds**; development comparisons can use an
explicit different budget recorded before execution. Neither a passed script nor an
engine `complete` is a substitute for the original/result comparison.
