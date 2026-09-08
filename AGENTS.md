# Document Files

Document Files owns its parser, AI-assisted interpretation, validation and execution loop. This repository is the source of truth; Toolkit and Sync consume pinned releases. README.md describes supported behavior, DESIGN.md the product boundary, and SCHEMA_EXTRACTION_DESIGN.md the extraction design. Keep implemented, verified and planned capabilities distinct.

Preserve AnalysisJob v1, AnalysisResult v1 and byte-stream input, existing CLI/MCP names, schema-extraction-result.v1 semantics and source immutability. Packaging alone must not change reanalysis_generation. Never infer model API access from a host chat subscription or silently send documents to another endpoint.

The approved 1.8.0 implementation includes directly related regression tests, cross-platform packaging checks and public-document model qualification; maintain these with their owning behavior. Do not add unrelated test frameworks or private documents, credentials or runtime databases. The release requires real client/model qualification, not just scripted tests. Do not label unverified capabilities complete.

Use pyproject.toml formatting/lint settings and existing pytest tests. Bounded parallel delegation is requested where useful; assign exclusive files and integrate results in the main task. Do not edit installed caches or generated copies as source. Build distribution artifacts from this repository only.
