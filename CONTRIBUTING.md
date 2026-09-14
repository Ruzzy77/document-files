# Contributing

Read README.md, docs/product-architecture.md and the contract relevant to the change.
Document Files owns document observation, interpretation, source binding and
validation; do not turn an external LLM application into an undocumented dependency.
Preserve the public v1 contracts and existing HWP checkbox patch behavior.

The plugin and Python package are independent delivery forms of one product. Do not
add consumer-specific imports, sibling-repository paths, registration requirements
or application names to their runtime, Skill or public documentation. Applications
integrate through the public API and own their storage, indexing and access policy.
Verify packaging changes from outside the checkout, using only the delivered files
and declared dependencies; a development editable install is not that check.

## Working tree and documents

Work directly on `main` in the registered product repository by default. Before
editing, inspect the branch, worktrees, uncommitted changes and running jobs. Preserve
other work. Do not create another worktree or a long-lived branch unless the task
needs one and the user chooses it. Use focused commits and normal pushes; no force
push or history rewrite is needed for this workflow.

Keep README focused on the public plugin introduction, available features, usage
conditions and getting started. Keep internal priorities, work logs, branch/CI policy
and rollout plans in their owning documents, not README. Architecture describes
boundaries, extraction-engine details algorithms, API serves callers, operations
covers execution, and SUPPORT records current limitations. Update the relevant section rather than appending another dated
run report. Raw run evidence belongs in private evaluation storage; old history stays
in Git. Do not create an additional handoff or duplicate implementation plan.

All GitHub workflows are `workflow_dispatch` only. Regular pushes and pull requests
do not start CI. Select one target for a necessary manual build; ARM64 is the default,
Mac is the next priority, other targets are optional. Manual runs can still notify
according to the user's existing GitHub preferences. Do not change account-wide
notification settings or dispatch expensive builds for documentation-only edits.

## Development

Use the pinned Python/dependency environment (`uv sync --frozen --python 3.12`).
Run only the existing tests that cover the change, and apply the repository Ruff
settings to changed files. Optional OCR/LLM dependencies belong in separate
packs; never add GPU wheels to the core or install dependencies during processing.
For CPU recognition, prepare an explicit target-specific CPU wheelhouse, with
exact wheel filenames and SHA256. A generic resolver lock containing CUDA packages
is not an approved CPU recognition lock.

### Minimal verification

- Bare `uv run --frozen pytest -q` runs only the native-input smoke files listed in
  `pyproject.toml`: byte-stream API boundaries, original typed values and HWP/HWPX/XLSX
  source structure/merged geometry. It is neither a full regression nor an AI quality check.
- For a code change, select the owning test file or test node explicitly, for example
  `uv run --frozen pytest -q tests/test_note_content.py`. Add a directly affected
  integration check only when the change crosses that boundary. Passing smoke alone
  does not validate an unrelated code change. Documentation-only changes need no pytest.
- The complete suite is explicit: `uv run --frozen pytest -q tests`. Use it only for
  a broad integration change or a release candidate, once after focused checks pass.
  Do not rerun it after each small edit or repeat it merely to recount passes.
- Keep PDF/OCR, packaging, HTTP and secondary-platform tests for changes in those
  components; they are not the default HWP/HWPX/XLSX development loop. Preserve their
  safety and source-integrity checks rather than marking unexecuted tests as passing.
- Do not run hundreds of the same pure-Python tests again on Spark before every model
  evaluation. Reuse verified source/environment evidence; select fresh checks only
  for affected ARM, Python-version, runtime or integration behavior. No model run,
  installation or broad environment setup is required for a test-only cleanup.
- Extend the owning regression rather than adding another file for each work session.
  One case per distinct failure/boundary is enough. Do not enumerate old version
  numbers for a shared identity-equality check or multiply independent validation
  cases across settings. Keep cross-products only for genuine interactions.
- If a necessary check is unexpectedly slow, add `--durations=10` to that selected
  run. Do not launch the full suite solely to obtain timings. Report the behavior
  checked and any remaining failure, not cumulative test counts as product progress.

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
checks one selected host; its explicit attestation step verifies candidate bytes
without claiming it built or quality-qualified the upstream model.

Consistent HWP/HWPX and XLSX structure and understanding across varied forms come
first. Compare source hierarchy, complete values, relationships and provenance;
a result-to-file writer is not a completion requirement.
Use Spark for internal AI and then check personal Mac use. Word and PPTX follow;
Google document integration is a later expansion. Keep existing secondary-format
features, but do not expand them or a broad PDF qualification effort ahead of the
primary formats. Formal release and consumer migration remain deferred.

Do not automatically publish or mark a candidate stable merely because packaging
or scripted tests passed. Maintain the feature support and qualification matrix.
