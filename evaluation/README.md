# Real-model qualification

Current product completion focuses on HWP/HWPX and XLSX extraction plus result-only
reconstruction. Use native fixtures and format-limited runs for that work. This runner
evaluates extraction; it does not create reconstructed documents or approve their
layout/editability. Those checks are specified in [SUPPORT.md](../SUPPORT.md).
Retained broad-format release gates are separate and must not be marked passed from
a primary-format result or weakened to make that result a formal release certificate.

Run from the independently built release source with Document Files installed:

```sh
python evaluation/run.py --kind local --output /path/to/private-evidence
python evaluation/run.py --kind cloud --holdout /path/to/public-holdout.json --output /path/to/private-evidence
# Exercise only affected native formats with an explicitly installed CPU pack:
python evaluation/run.py --kind local --config /path/to/server.json --profile cpu \
  --case-format txt --case-format html --options /path/to/budgets.json \
  --output /path/to/new-private-evidence
```

Configure `DOCUMENT_FILES_AI_ENDPOINT`, `DOCUMENT_FILES_AI_MODEL` and, when required,
`DOCUMENT_FILES_AI_API_KEY` in the executing environment. The evaluator uses the real
configured transport, not a scripted model. `--kind` records the operator's deployment
classification; it does not infer locality from a hostname or grant transmission permission.
Only public synthetic inputs and explicitly public holdouts may be submitted by this runner.
`--config` and `--profile` select the same managed packs used by the product, including
an optional recognition pack; no model is downloaded or silently substituted. Explicit
`--options` may grant longer CPU budgets before the run. `--case-format` limits reruns
to affected formats, and `--holdout-only` avoids replaying development cases. These
options do not turn development inputs into independent holdouts or make a 24 GB Mac
run evidence of a 16 GB-constrained deployment.

## Freeze inputs and expectations before inference

The built-in six-format examples remain development cases, not unseen-document evidence.
Use `--holdout-only` for a report intended for independent review. Older manifests containing
only file paths are deliberately rejected: migrate their original, pre-existing expectations
rather than writing new expected answers after inspecting model output.

A `document-files.evaluation-cases.v2` manifest uses stable case IDs and these fields:

```json
{
  "schemaVersion": "document-files.evaluation-cases.v2",
  "publicData": true,
  "cases": [{
    "id": "unseen-form", "path": "inputs/unseen-form.docx",
    "languages": ["en"], "printed": true,
    "pdfKind": "not-applicable", "longDocument": false,
    "reviewSpecification": {
      "path": "specifications/unseen-form.json", "sha256": "<actual file SHA256>"
    }
  }]
}
```

`pdfKind` is `text`, `scan`, or `mixed` for PDF, and `not-applicable` otherwise.
Long documents require `pageCount` and `regionCount`, each at least 3; these are declared
source properties, not counts inferred from potentially incomplete output. Case IDs must
be unique safe identifiers. Specification references must stay inside the manifest directory
(no absolute paths, traversal, or symlinks). Inputs may refer to another local public-data
folder; the runner preserves the original and freezes a copy in the new evidence directory.

Each pre-inference specification is separate from the document and options:

```json
{
  "schemaVersion": "document-files.review-specification.v1",
  "criteria": [{
    "id": "value_source_bindings",
    "description": "Compare every required value and its source locator",
    "expected": {"recordId": "00047", "quantity": "12.50"}
  }]
}
```

Criteria IDs are stable and unique; descriptions and expected values/relationships must be
prepared from the original before inference. `expected` can be structured JSON, including
null where the source requires it. Put the actual values, scope exclusions, source anchors
and forbidden inferences in this specification, never in the extractor options. All inputs,
specifications and optional `recognitionCostReceipt: {path, sha256}` files are copied and
hashed **before any case runs**. The model receives only the input snapshot and validated
product options; no review specification or manifest metadata is passed to it.

Default per-case budgets are **12 model calls / 900 seconds** for short inputs and
**64 / 3,600 seconds** for long inputs. `--options` is an explicit operator override, validated
before inference; budgets do not increase automatically after a failure. Each result records
its exact options, extraction wall time and the engine's unchanged usage receipt. Report wall
time includes the whole evaluator run. Recognition is `not-separately-measured` unless a real
separate receipt was supplied; do not infer recognition cost by subtracting unrelated clocks.
A prior recognition receipt remains prior-run evidence, not proof of a fresh full-path run.

## Independent review, without editing the run

The runner emits `document-files.model-qualification.v2` with `passed: false` and each
`semanticReview.status: pending`. It preserves input, result and specification references and
hashes. Running inference, structural validation or AI self-review cannot grant approval.

After comparing the original input snapshot, frozen specification and full source-linked
result, an identified independent reviewer writes `document-files.review-decisions.v1`:

```json
{
  "schemaVersion": "document-files.review-decisions.v1",
  "sourceReportSha256": "<raw local_model.json SHA256>",
  "reviewer": "<reviewer identity>", "independent": true,
  "method": "human-ground-truth",
  "cases": [{
    "id": "unseen-form",
    "inputSha256": "<input SHA256>", "resultSha256": "<result SHA256>",
    "reviewSpecificationSha256": "<frozen specification SHA256>",
    "criteria": {"value_source_bindings": true},
    "findings": ["<actual checked values, relationships and source locations; include failures>"]
  }]
}
```

This is a shape example, not a completed review. `independent-ground-truth` is also an accepted
method; it must represent a genuinely separate check, not the extraction AI's self-assertion.
Every case and every specified criterion must appear exactly once. Judgments must be JSON
booleans, not strings or integers. A false criterion, partial extraction, or invalid structure
produces a failed receipt. Missing findings, stale hashes, duplicate JSON keys and a result
linked to another source are rejected.

```sh
python evaluation/review.py --report /path/to/evidence/local_model.json \
  --decisions /path/to/review-decisions.json --output /path/to/evidence/semantic-review.json
```

This creates a **new** `document-files.semantic-review.v1` receipt, binding the raw report and
all input/result/specification hashes. It never rewrites the inference report or overwrites
an existing receipt. A failed review is still recorded; exit status is 1. Review completeness
is machine-checked, but the tool cannot certify that a fabricated judgment is true. Reviewer
identity, method and case preparation provenance must therefore remain honest.

## Bind a release candidate and its measured execution

For final qualification, provide the clean candidate's artifact inventory and exact consumed
asset IDs (repeat `--artifact` for core, runtime, model, recognition and any container image):

```sh
python evaluation/run.py --kind local --holdout-only --holdout /path/to/cases.json \
  --config /path/to/server.json --profile cpu --output /evidence/model-run \
  --evidence-root /evidence --artifact-inventory /evidence/inventory.json \
  --artifact core-wheel --artifact source-sdist --artifact runtime-linux --artifact model --artifact recognition-linux \
  --core-artifact core-wheel --evaluator-artifact source-sdist
```

The inventory is `document-files.artifact-inventory.v2`; version, clean source commit and
selected artifact hashes must match. `artifactInventory` is relative to the explicit evidence
root, while each case's snapshots and results are relative to its report directory. A run
without inventory remains usable development evidence, not final release qualification.

Inventory execution requires the actual imported package to match the selected wheel
RECORD and bytes, and the evaluator source to match the selected sdist. The comparison
runs before and after inference. `coreExecution` records these identities; the gate
rechecks them and the recorder script against the same sdist. This route does not
query repository Git metadata or need a Git ownership exception. The source-based
development route remains unqualified. Actual container image ID proof is a separate
pending host-side receipt requirement; choosing an image artifact ID alone does not
prove that it ran.

An external execution recorder sets `DOCUMENT_FILES_EXECUTION_RUN_ID`; the runner records it.
The recorder separately measures the cgroup/network/device envelope and hashes this finished
report as an output. The qualification check item links the raw `evidence`, semantic `review`
and `executionReceipt`, each with its SHA256. The raw report must not hash the execution
receipt: the receipt hashes the report, so doing both would create a circular dependency.
No cgroup peak, memory ceiling, swap/OOM or offline claim is invented by this evaluator.

## Coverage and eligibility checklist

Before running a final candidate, allocate predeclared criteria to independent public cases:

- TXT, Markdown, HTML, DOCX, HWP, HWPX, XLSX, PPTX and PDF; PDF needs both text and scan cases.
- Printed Korean and English; at least one document with three pages and three regions.
- `repeated_rows`, `merged_headers`, `common_units`, `footnotes`, `conditions`,
  `empty_missing_uncertain`, `number_precision`, `formulas_cached_values`,
  `field_source_bindings`, `value_source_bindings`, `cross_page_tables`, `cross_page_notes`.
- Check every required record, exact decimal/identifier spelling, source reference and scope
  exclusion. Honest partial/limit-exceeded cases are separate negative tests, not complete
  extraction successes. Only require a criterion where the document actually contains it.

Keep author role, source history, creation-before-inference and any exposure to prior outputs
in the private preparation record. A case used to fix the product becomes a development case;
prepare a new independent case for final judgment. Do not relabel failed or previously used
inputs as unseen. Do not copy private work documents into Git or upload them to an unapproved
model endpoint. No new model runs or coverage approvals are implied by the contract tests.

The release gate also requires packaged-runtime and actual client evidence. Model evidence
does not substitute for installation, and CI unit tests do not substitute for this evaluation.
