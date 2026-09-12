# Product architecture

Document Files turns document bytes into source-linked structure, schema, values and
meaning. It owns observation, model requests, compilation, validation and bounded
repair. A caller does not need another AI agent to construct the interpretation.

**Primary formats:** HWP/HWPX and Excel XLSX. Complete accurate extraction and create
an editable, copy-like HWPX/XLSX using only the extraction result, without reopening
the original file. Word and PPTX follow; Google Docs/Sheets/Slides are a later expansion.
The primary AI runtime remains DGX Spark (Linux ARM64, CUDA); personal Mac use follows.
Other platforms, formal release publication and downstream application upgrades are
deferred. Existing format adapters and interfaces remain; their presence does not
establish accuracy or reconstruction support.
See [current readiness and defects](../SUPPORT.md) before relying on a feature.

## Reading map

| Question | Reference |
|---|---|
| What owns each part of the product? | This document |
| How are PDF pages, tables, values and meanings interpreted? | [Extraction engine](extraction-engine.md) |
| How do I call it and consume results? | [Python, CLI and MCP API](python-api.md) |
| How do I run, stop, resume and update it? | [Operations](operations.md) |
| What is checked, and what must be fixed next? | [Readiness](../SUPPORT.md) |
| How are model evaluations reviewed? | [Evaluation](../evaluation/README.md) |
| How are optional distributable packs built? | [Deployment](../deployment/README.md) |

These documents describe current behavior by topic, not one entry per experiment.
Git preserves earlier implementations; private run directories preserve original
requests, failures and reviews. Neither a historical run nor a prose description
supersedes the current source or an independently checked output.

## Processing flow

```text
caller-owned bytes + AnalysisJob + explicit options/profile
  → private immutable input snapshot
  → native observations / optional isolated CPU recognition
  → optional source-bound PDF visual review and additional reading
  → region planning and value ownership
  → table structure or scalar interpretation
  → program-owned value compilation + content review
  → cross-page relations + independent applicability decisions
  → validation, coverage and public result
```

Original observations are never replaced by a model answer. A reviewed alternative
PDF reading is a separate projection with its own source links. Interpretation works
on an explicitly selected view, and records which original uncertainty it can resolve.
A model can propose relationships but cannot supply authoritative document values,
write JSON pointers, execute a condition or silently declare unobserved content empty.

## Components and code entry points

All paths below are relative to `src/document_files/`.

| Layer | Files | Responsibility |
|---|---|---|
| Public calls | `api.py`, `cli.py`, `mcp_server.py` | Shared application functions, typed input/output and capability reporting |
| Analysis | `analysis.py`, `processor.py`, `extractors.py`, format adapters | Byte-stream analysis, native locations, text/structure projections |
| Observation | `document_model/model.py`, `observe.py`, `native.py`, `html.py`, `markdown.py`, `pdf.py` | Immutable nodes, tables, bindings, geometry and source identity |
| Recognition | `document_model/docling_adapter.py`, `docling_pipeline.py`, `recognition_worker.py` | Offline Docling/OCR, measured pixels and additive recognition evidence |
| Interpretation | `interpretation/engine.py`, `regions.py`, `table_protocol.py`, `compiler.py` | Region/stage control, proposals, exact value reads and compiled results |
| Meaning and relations | `interpretation/integration.py`, `scope_protocol.py`, `scope_source_binding.py` | Table continuation/duplication, applicability selection and source verification |
| Execution | `interpretation/workflow.py`, `jobs.py`, `server_worker.py`, `http_server.py` | Saved results, cumulative budgets, one supervised worker and authenticated jobs |
| Models and packs | `profiles.py`, `runtime_packs.py`, `interpretation/backends.py` | Explicit model selection, verified offline assets and managed llama.cpp lifetime |

## Public boundaries

`AnalysisJob v1`, `AnalysisResult v1`, byte-stream input, existing CLI/MCP names and
`schema-extraction-result.v1` semantics remain unchanged. Private model schemas and
checkpoints are versioned independently. Their incompatibility prevents unsafe
resume; it does not invalidate an already saved public result.

The result separates `dataSchema`, `data`, semantic definitions/relationships,
evidence, validation, coverage and issues. Values point back to observed bindings;
field and column definitions have their own source references. Lexical numeric
precision, native types, formula text and saved formula caches are distinct.
Present empty strings, absent fields, unread cells and uncertain interpretations
must not collapse into a single null or disappear.

`complete` means the engine's configured processing and checks completed. It is
not a semantic accuracy certificate: a structurally valid model mistake may pass
those checks. Independent review compares the original, prior expected result and
actual bound output. Useful partial results never count as complete extraction.

## Runtime choice

The primary development configuration uses the ARM64 CPU recognition pack followed
by a CUDA llama.cpp runtime and a compatible Qwen3.5-9B Q4_K_M vision model pack.
Recognition and inference have separate processes and identities. Installed assets
are checksum-pinned; processing does not download models or choose another endpoint.
A chat subscription does not imply usable model API credentials.

Spark is a shared host. Container limits, host memory pressure and GPU allocations
must all be observed. A cgroup ceiling alone is not proof of a complete GPU memory
bound. Host swap, authentication, other users' services and existing workloads are
not changed to make a test pass. See [operations](operations.md).

## Source preservation and adjacent features

Existing reading, conversion, HWPX creation/editing and the single Document Files
Skill remain. Ordinary DOCX/XLSX/PPTX/PDF creation uses the host's appropriate library;
Google native documents use the requested connection. Company-specific back-office
and database mapping remain outside this product. Existing secondary-format features
are retained, but expanding them must not delay HWP/HWPX and XLSX completion.

The HWP checkbox guard compares original HWP and produced HWPX independently of
rhwp's IR. Keep the pinned checkbox patch and loss reporting until an explicitly
verified upstream replacement passes the same mixed-state cases. Reading must not
require an optional editor. Editing writes a separate output and checks the complete
old cell value and verified selector; ambiguous/unsafe nested-cell changes are refused.

The calling application owns document registration, access policy, revisions,
indexing and business-specific projections. Document Files owns parsing and extraction;
it neither imports that application nor needs its database or configuration. Exact
adapter/config identity records how an output was made; it does not automatically
change `reanalysis_generation` for unchanged source files. Reprocessing and application
upgrades remain explicit caller decisions.

## Reconstruction from extraction results

This is an accepted product requirement, **not an implemented end-to-end feature**.
The generator receives the serialized extraction result and its self-contained,
hash-addressed resources. It must create editable HWPX or XLSX without a source path,
original file, access to the source store or another extraction pass. HWP inputs
target HWPX output; legacy `.xls` and `.doc` are not currently supported adapters.
An identical ZIP byte stream is not required; preserved content, document structure,
formatting, layout and editability are the acceptance criteria.

### Current building blocks and gaps

`document_model/capture.py` already preserves the XML/relationship parts and binary
resources of HWPX and XLSX under `reconstructionContext`. This retains useful native
format details alongside normalized nodes, bindings and values. It does not provide
a reconstruction writer or verify that a receiving application can open the output.
`nativeCaptureComplete` reports capture only; `recipientReconstructionVerified` remains
false. Binary HWP returns `structured_native_capture_unavailable` on this path.

`engine.create_hwpx` writes from an authoring plan and `edit_hwpx` edits a supplied
document copy. Neither consumes an extraction result. XLSX authoring currently uses
host libraries through the Skill; a product-owned result-to-XLSX writer is missing.
Native XML and resources can be reused for fidelity; their preservation must not be
presented as proof that the logical schema, values or relationships were understood.

### Implementation direction

1. Fix shared row-loss/duplicate rules and characterize native HWP/HWPX/XLSX output
   with independently prepared expectations. Inventory which required formatting,
   resource and relationship information is structured, retained as native parts,
   or absent. Keep those states distinct.
2. Build result-only HWPX/XLSX writers in the product, initially reusing the captured
   native parts where they preserve fidelity. Validate the manifest, member names,
   sizes, digests, relationships and expanded-byte limits before writing. Preserve
   format-required package ordering/content and publish only to a separate output.
   Do not read the source archive as a fallback or fetch missing resources.
3. Complete the HWP-to-HWPX capture path with conversion provenance and source links.
   Compare the converted content with original HWP observations, including checkbox
   states. Conversion loss remains visible; converted locations do not replace
   original HWP evidence. Do not claim complete HWP reproduction while required
   layout or object information is unavailable.
4. Verify output in a source-free generation environment. A separate reviewer may
   access the original for comparison. Reopen with the parser, compare structure and
   values, then check layout in compatible document applications and edit sample
   cells/text. A screenshot-only copy is not an editable reconstructed document.

The result/resources format and any new writer API must be explicitly versioned
when implemented. Preserve existing AnalysisJob/AnalysisResult and extraction calls;
`reconstructionContext=False` remains valid for applications needing only schema and
values. Such a result is not required to be reconstructible. Extraction accuracy,
capture completeness and reconstruction fidelity need separate outcomes.

## Independent delivery and embedding

The Python wheel contains the `document_files` package, result schemas and required
notices. It can be installed in another application's environment and called through
`document_files.api`, without plugin registration, an agent account or a running
document service. The CLI, MCP plugin and optional HTTP service are adapters around
the same engine; they are not prerequisites for an in-process call.

Use `AnalysisJob` plus a binary stream for an application-owned input. The stream API
returns its result directly; it does not create the retained job database. Temporary
parser files are private and removed after use. Result storage/checkpoints, managed
job workers and pack activation are separate, explicitly selected facilities.
Model and recognition clients are supplied through product interfaces. No endpoint,
credentials, model weights or recognition assets are bundled into the core wheel.
The declared Python dependencies are still required; independence does not mean a
standard-library-only package or a model-free AI extractor.

Source archives and platform/plugin bundles carry their own launchers and Skill.
The remote `.skill` carries its helper and the same package source, using an existing
host Python environment. None of these forms resolves a sibling project. See the
[embedding API](python-api.md#embedding-the-python-package) for ownership and setup.

## Safety and optional delivery

No implicit external transfer, macro/script execution, source mutation or raw prompt/
key logging. Native subprocess separation is not an OS security sandbox. HTTP accepts
bounded bytes and administrator profile names, not caller-chosen paths or executables.

Packs remain immutable and carry source, license and hash records. Deferred release
work does not waive redistribution or security checks. The [release procedure](../deployment/RELEASE.md)
is retained for a future distribution; it is not the current Spark extraction task's
completion checklist.
