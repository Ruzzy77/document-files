# Product architecture

Document Files turns document bytes into source-linked structure, schema, values and
meaning. It owns observation, model requests, compilation, validation and bounded
repair. A caller does not need another AI agent to construct the interpretation.

**Current priority:** make this extraction reliable on DGX Spark (Linux ARM64, CUDA
inference), then adapt the verified path for personal use on Mac. Other platforms,
formal release publication and Toolkit/Sync/client migration are deferred. Existing
interfaces and platform code remain; deferred does not mean qualified or removed.
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
Google native documents use the requested connection. This work does not add a
company-specific back office, database mapping, renderer or reconstruction engine.
Personal reconstruction context is a separate option, not the current accuracy gate.

The HWP checkbox guard compares original HWP and produced HWPX independently of
rhwp's IR. Keep the pinned checkbox patch and loss reporting until an explicitly
verified upstream replacement passes the same mixed-state cases. Reading must not
require an optional editor. Editing writes a separate output and checks the complete
old cell value and verified selector; ambiguous/unsafe nested-cell changes are refused.

Toolkit and Sync own registration, captures and projections. Document Files owns
parsing and extraction. Exact adapter/config identity records how an output was made;
it does not automatically change `reanalysis_generation` for unchanged source files.
Installed consumers remain on their existing versions until a separate verified update.

## Safety and optional delivery

No implicit external transfer, macro/script execution, source mutation or raw prompt/
key logging. Native subprocess separation is not an OS security sandbox. HTTP accepts
bounded bytes and administrator profile names, not caller-chosen paths or executables.

Packs remain immutable and carry source, license and hash records. Deferred release
work does not waive redistribution or security checks. The [release procedure](../deployment/RELEASE.md)
is retained for a future distribution; it is not the current Spark extraction task's
completion checklist.
