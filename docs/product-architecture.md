# Product architecture

Document Files turns document bytes into source-linked structure, schema, values and
meaning. It owns observation, model requests, compilation, validation and bounded
repair. A caller does not need another AI agent to construct the interpretation.

**Primary formats:** HWP/HWPX and Excel XLSX. The product KPI is reliable structure
and document understanding across varied layouts and forms, with complete values,
relationships and source links. Word and PPTX follow; Google Docs/Sheets/Slides are
a later expansion.
The primary AI runtime remains DGX Spark (Linux ARM64, CUDA); personal Mac use follows.
Other platforms, formal release publication and downstream application upgrades are
deferred. Existing format adapters and interfaces remain; their presence does not
establish independently checked extraction accuracy.
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

## Structural completeness and document understanding

Consistency means a shared representation and preservation rules, not forcing unlike
forms into the same field names or table shape. The result must express what each
source actually contains: reading order and section hierarchy; labels and values;
tables, merged or nested cells and header hierarchy; repeated records; notes, units,
conditions and their exact applicability. Each value and each structural or semantic
interpretation must retain its source location and uncertainty.

Native parsers observe declared structure and values. Internal AI identifies logical
roles and relationships that vary by form. The compiler reads bound values and
preserves every mapped record in order. A citation, matching text or a convenient
schema must not silently remove a genuine row or invent a missing value. Apparent
layout and semantic relationships are separate evidence: a nearby note is not
necessarily applicable to every column.

Work on diverse layouts within HWP/HWPX and XLSX before expanding the format list.
Prepare expectations for label/value forms, record tables, different merged-header
structures, subtotal/note rows, nested or continued tables, and long documents.
Compare hierarchy, complete row/field sets, exact values, relationships, missingness
and actual field/value bindings. Include different presentations of equivalent
content to check that only the presentation changes, and genuinely different forms
to check that the engine does not impose a fixed template. Failures used for a fix
become development cases; independent approval uses new documents.

Reconstruction from the result is a downstream illustration of sufficiently rich
structure, not a separate writer feature or completion gate. A copy-like file does
not prove that the engine understood its contents, and retaining opaque native parts
alone does not satisfy this KPI. Conversely, building an editor, reproducing fonts or
matching an application's rendering must not displace structural extraction work.
Keep layout and resource information needed to understand the source and expose gaps.

The existing optional `reconstructionContext` preserves native parts for HWPX/XLSX;
its capture flags do not certify structure or meaning. Binary HWP capture and a
result-to-file writer are not implemented. These remain feature boundaries, not
current extraction blockers. See [the API](python-api.md#reconstruction-context-and-output-files)
for the capture contract; `reconstructionContext=False` remains supported.

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
