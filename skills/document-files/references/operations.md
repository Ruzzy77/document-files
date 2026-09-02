# Document Files operations

Use the `document_*` MCP tools or the plugin-root `launchers/document-files` command. All
document operations are local and headless. PDF, DOCX, PPTX, XLSX, HWP, HWPX,
HTML, Markdown, and text share one extraction and coverage contract.

## Check capabilities

```bash
launchers/document-files capabilities
```

Confirm `headless: true`, `nativeAppAutomation: false`, the exact backend
versions, extraction formats, and per-format artifact operations. HWP conversion
and SVG/PDF rendering require the pinned `rhwp` backend. HWPX operations require `python-hwpx 6.3.0` and
`python-hwpx-automation 7.0.3`; a different pair is reported as unavailable and
fails closed before document work begins.

## Inspect and extract

```bash
launchers/document-files inspect input.pdf
launchers/document-files inspect input.docx --max-chars 20000
launchers/document-files extract input.pptx --format text
launchers/document-files extract input.xlsx --format markdown
launchers/document-files inspect input.hwp
launchers/document-files inspect input.hwpx --max-chars 20000
launchers/document-files extract input.hwp --format text
launchers/document-files extract input.hwpx --format markdown
```

Inspection returns bounded text, structure counts, coverage, issues, format
metadata, and available format-specific checks. Extraction returns bounded
content directly and reports `truncated: true` instead of silently omitting the
limit. Preserve the per-dimension `coverage` values and material issue codes in downstream work.

Encrypted HWP content returns `protected-document`; do not attempt to bypass
protection.

## Corpus process boundary

```bash
launchers/document-files process --describe
```

Corpus uses `launchers/document-files process` with one inherited read-only file
descriptor and one JSONL request. The process returns structural units,
derivation methods, geometry, confidence, quality flags, coverage, and issues.
It does not accept or return Corpus document IDs, revision IDs, Source unit IDs,
source paths, anchors, or authority fields. Bounded PDF page and Office image
continuations finish inside this process before the result crosses into Corpus.

## Convert

```bash
launchers/document-files convert input.hwp output.hwpx
launchers/document-files convert input.hwp output.md --format markdown
launchers/document-files convert input.hwpx output.pdf
launchers/document-files convert input.hwp svg-pages --format svg
```

HWP-to-HWPX conversion writes to a temporary location, runs package/reopen
checks, compares the intermediate representation and page count, confirms that
the source hash is unchanged, and only then publishes the output. Measurable
differences fail closed by default. After reviewing the returned `losses`, retry
with `--allow-lossy` only when the differences are acceptable.

Direct HWP editing and HWPX-to-HWP conversion are not part of the supported
contract.

## Render in the background

```bash
launchers/document-files render input.hwp output.pdf
launchers/document-files render input.hwp svg-pages --format svg
launchers/document-files render input.hwpx preview.html
```

- PDF and SVG use `rhwp` and support HWP/HWPX.
- HTML is a `python-hwpx` layout approximation for HWPX only.
- SVG output is a new directory containing one file per rendered page.
- Use `--page 0` for a single zero-based page with SVG or PDF.

Every result records `nativeRenderChecked: false`. These commands never open or
control Hancom Office.

## Edit an HWPX copy

Create a JSON plan:

```json
{
  "schemaVersion": "document-files.edit.v1",
  "textReplacements": [
    {
      "find": "기존 제목",
      "replace": "새 제목",
      "expectedCount": 1,
      "sectionPath": "Contents/section0.xml"
    }
  ],
  "tableCells": [
    {
      "tableIndex": 0,
      "row": 2,
      "col": 1,
      "text": "2026.08.09.",
      "expectedOldText": "2026.08.01.",
      "sectionPath": "Contents/section0.xml"
    }
  ]
}
```

Run the same plan twice:

```bash
launchers/document-files edit input.hwpx edit-plan.json
launchers/document-files edit input.hwpx edit-plan.json \
  --output output.hwpx --apply
```

The first command is a dry run. The second writes a separate HWPX after the
planned edits pass package and reopen checks. `expectedOldText` and
`expectedCount` prevent stale or ambiguous changes.

## Verify

```bash
launchers/document-files verify output.hwpx \
  --reference input.hwpx \
  --expect "새 제목" \
  --forbid "기존 제목"
```

Reference comparison reports changed package parts and whether table rows,
columns, and merged-cell counts remain the same.

## Create a new HWPX

Use the `hwpx.document_plan.v1` shape:

```json
{
  "schemaVersion": "hwpx.document_plan.v1",
  "title": "업무 계획",
  "metadata": {
    "organization": "예시 기관",
    "date": "2026-08-09"
  },
  "blocks": [
    {
      "type": "heading",
      "level": 1,
      "text": "개요"
    },
    {
      "type": "paragraph",
      "text": "계획의 목적과 범위를 작성합니다."
    },
    {
      "type": "table",
      "caption": "일정",
      "columns": [
        {"key": "item", "label": "항목", "widthWeight": 2},
        {"key": "date", "label": "일자", "widthWeight": 1}
      ],
      "rows": [
        {"item": "검토", "date": "2026.08.09."}
      ]
    }
  ],
  "qualityGates": {
    "validatePackage": true,
    "validateDocument": true,
    "reopen": true,
    "requiredText": ["업무 계획", "개요"],
    "visualReviewRequired": false
  }
}
```

```bash
launchers/document-files create document-plan.json output.hwpx
```

## Result receipts

Write operations report absolute source and output paths, size and SHA-256,
engine versions, source preservation, validation, losses or warnings, and
`nativeRenderChecked`. Keep these distinctions:

- package/reopen validation proves structural usability;
- HTML/SVG/PDF background rendering provides non-native layout evidence;
- neither proves native Hancom rendering.

## Private material

Keep reusable private templates under a user-owned private location such as:

```text
~/Library/Application Support/Document Files/templates/
```

Institution-specific mappings and real work files stay with the private template
or project. The plugin package contains only generic format handling.
