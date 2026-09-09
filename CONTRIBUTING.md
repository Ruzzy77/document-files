# Contributing

Read README.md, docs/product-architecture.md and the contract relevant to the change.
Document Files owns document observation, interpretation, source binding and
validation; do not turn an external LLM application into an undocumented dependency.
Preserve the public v1 contracts and existing HWP checkbox patch behavior.

## Development

Use the pinned Python/dependency environment (`uv sync --frozen --python 3.12`).
Run the existing tests that cover the change (`uv run --frozen pytest`), and apply
the repository Ruff settings. Optional OCR/LLM dependencies belong in separate
packs; never add GPU wheels to the core or install dependencies during processing.
For CPU recognition, prepare an explicit target-specific CPU wheelhouse, with
exact wheel filenames and SHA256. A generic resolver lock containing CUDA packages
is not an approved CPU recognition lock.

## Changes and evidence

- Use synthetic/public documents and independently prepared holdouts. Do not
  commit private originals, credentials, model caches or generated job databases.
- Preserve original text, explicit structure, coordinates, uncertainty and source
  references; never disguise an unsupported area as an empty successful result.
- Add directly relevant regressions. Mock transports test contracts, not semantic
  quality. Keep installed-client, platform, recognition and real-model evidence
  separate. Avoid repeating expensive model evaluation after unrelated edits.
- Packs are immutable version directories. Use a new version and explicit active
  manifest update; never edit installed copies or overwrite a previous result.
- Submit the applicable code/model license notices, source revisions, conversion
  receipts and hashes with changes to third-party runtimes or weights.

## Building packs

See deployment/README.md. The builder requires local source evidence, audited
staging files and a manifest declaration. It emits the archive, manifest, SHA256
and file-level CycloneDX inventory. Add transitive component/version information
in the declaration's `components` field for a distributable dependency SBOM.
Build from clean source for candidate attestation. The optional pack workflow
checks all four host contract surfaces; its explicit attestation step verifies
candidate bytes without claiming it built or quality-qualified the upstream model.

Do not automatically publish or mark a candidate stable merely because packaging
or scripted tests passed. Maintain the feature support and qualification matrix.
