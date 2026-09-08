# Real-model qualification

Run from the independently built release source with Document Files installed:

```sh
python evaluation/run.py --kind local --output /path/to/private-evidence
python evaluation/run.py --kind cloud --holdout /path/to/public-holdout.json --output /path/to/private-evidence
```

Configure `DOCUMENT_FILES_AI_ENDPOINT`, `DOCUMENT_FILES_AI_MODEL` and, when required,
`DOCUMENT_FILES_AI_API_KEY` in the executing environment. The evaluator uses the real
configured transport, not a scripted model. `--kind` records the operator's deployment
classification; it does not infer locality from a hostname or grant transmission permission.
Only public synthetic inputs and explicitly public holdouts may be submitted by this runner.

The built-in six-format examples are development cases, not unseen-document evidence.
Holdouts must be prepared independently of prompt development. Their manifest is:

```json
{"publicData": true, "cases": [{"path": "unseen-form.docx"}]}
```

Review the emitted source-linked results against the original public documents. Check
field discovery, repeated records, shared units, note/condition scope, missingness and
original values. For a holdout, replace the example-specific review checklist with the
case's independently specified expected relationships. Do not use the extractor's AI
review flag as an accuracy certificate. Record checked targets, findings and reviewer
identity in each semanticReview. A failed check or partial output cannot qualify that case
for complete extraction support. Unsupported visual/long cases are tested separately for
honest partial output, not counted as successful complete extraction.

The runner deliberately writes `passed: false`: running inference alone does not complete
qualification. After independent review, each case must have semanticReview.status `passed`
and a nonempty review record; preserve its result hash. Add independently prepared holdouts
covering the formats being claimed, and set passed only when these checks pass. Source
commit, model identity and all evidence hashes are retained. Never relabel development cases
as holdouts, reuse evidence from different code, or copy private work documents into Git.

The release gate also requires packaged-runtime and actual client evidence. Model evidence
does not substitute for installation, and CI unit tests do not substitute for this evaluation.
