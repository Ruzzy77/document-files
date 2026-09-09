# Support and delivery boundaries

The Python API, CLI, MCP and HTTP interfaces use one Document Files engine. Local
model capability is supplied by separate versioned runtime/model packs. Core,
recognition, llama.cpp runtime and model versions can be inspected independently.

| Environment | Native core and CPU semantic runtime | Full recognition profile |
|---|---|---|
| macOS Apple Silicon | Targeted | Native isolated Docling CPU pack |
| macOS Intel | Targeted, not pinned to old Torch/ORT | Local Linux x64 CPU container |
| Windows x64 | Targeted | Native isolated Docling CPU pack |
| Linux x64 | Targeted | Isolated Docling CPU pack / container |
| Linux ARM64 | Targeted | Isolated Docling CPU pack / container |

**Targeted is not a claim of completed end-to-end qualification.** Each pack must
state its actual minimum OS, libc/CPU requirements and compatible runtime digest.
Upstream wheels/binaries existing is different from a tested product installation.
The container requires a compatible locally installed container environment; it
is not the same as the Python-free native portable distribution.

The selected recognition configuration is Docling CPU, Heron layout, TableFormer
accurate and Tesseract Korean/English. Semantic inference uses official Qwen3.5-9B
converted to Q4_K_M by pinned llama.cpp, non-thinking, with one CPU slot. These are
implementation selections, not accuracy or practical-speed guarantees. Long or
ambiguous documents may produce partial results and unresolved relationships.

## 1.8.0 candidate status

This checkout is **not a qualified stable release**. The source-bound compiler,
regional interpretation, durable jobs and HTTP/MCP interfaces have regression
coverage. Apple Silicon core bundles and separate CPU runtime/model/recognition
pack candidates have been assembled locally. Core CLI/MCP processing was exercised
without a host Python on PATH; recognition-pack relocation was checked without
loading the recognition models. Neither establishes installed-client qualification.

### Linux ARM64 addition

Linux ARM64 (`linux-aarch64`) is now a fifth release target; the four existing targets
remain required. Core/PBS, CPU runtime and native recognition builders have native
ARM CI jobs and target checks. Linux ELF machine, loader and actual startup host must
agree. Both Linux architectures retain the Bookworm ABI baseline. The new CPU runtime
candidate defaults to `b10853-cpu.4`; installed packs and model bindings are unchanged.

Images select `linux-x86_64` or `linux-aarch64` explicitly. The portable core, patched
HWP binary, Docker base/final image and exported config must share that target.
Qualification v3 requires separate x64 and ARM64 model, HTTP and container checks;
ARM observations cannot replace x64 evidence. Actual Docker OS/architecture, image
and container IDs, CPU quota and cgroup evidence are bound to each run. Container
identity v2 rejects older receipts that lack these observations.
The release gate also parses the delivered image export and verifies its actual
configuration ID, architecture and ordered layer hashes, rather than trusting the
build receipt alone.

Both designated DGX Spark hosts are reachable through Tailscale SSH and reported
ARM64, Ubuntu 24.04 and cgroup v2. A fresh check on September 10 confirmed Docker
access for the existing account on both hosts; no authentication request remains for
that access. No permissions, GPU, swap, network or security settings were changed.
Spark-A now has the exact pinned ARM Bookworm image cached. Image acquisition is not
container execution or full-product qualification. At source 25e6cd8, ARM core, CPU
runtime and native recognition CI builds passed; the native delivery archive was
later recovered and statically verified without rewriting prior failed transfers.
Complete recognition stages, CPU-only four-core/16 GiB execution, installation/rollback
and client use remain unqualified.

An explicit ARM torchvision derivative was prepared from the pinned CPU wheel in a
new output location. It restores five codec loader references and omits the now
unreferenced bundled loader; 190 other members, package metadata and the original
license remain byte-identical. The new build tag is `1dfarmloader1`. Static RECORD
and byte checks passed. A subsequent isolated Spark-A run using the selected PBS
passed native PNG/JPEG/WebP checks and linked all five codec guard references to the
single system loader. The component cgroup peaked at 1,281,576,960 bytes with no
swap/OOM; exit 0 and owned-container removal were confirmed. This is not full OCR/model
16 GiB qualification or redistribution approval. Original inputs and installed
environments remain unchanged.

The Linux input preparation helper checks an approved inventory and requires explicit
byte, disk and time limits. Its default is offline inspection; acquisition and network
access require separate flags. It neither installs inputs nor approves a stage. The
legacy ARM inventory fails readiness checks. The normalized v2 inventory instead
selects explicit input components and preserves unresolved full-stage requirements
separately. Its 117 files and 873,482,385 bytes pass metadata and local-original checks;
models remain local-copy-only. One bounded acquisition then verified all 117 files
(873,482,385 bytes) in 540.16 seconds; none were installed or executed. Native inputs,
dynamic-library closure and actual stage assembly/qualification remain unresolved.

### Current source follow-up: table meaning, visual observations and Linux compatibility

Prompt v23 / planner v14 / table protocol v8 / compiler v15 require an explicit
role for every observed row not fixed by a native header declaration. OCR header
flags remain predictions; omitted roles never become data records. Scope v7 uses
the compiled header classification when constructing header-group candidates.
Missing source cells remain missing, and invalid decimal text remains uncertain
with its original spelling and source range instead of being read as a number.

Non-record subtotal, note and unmapped cells now have a separate scalar region.
Its value bindings do not overlap the parent record readings, it shares the document
budget, and it resumes independently after a pause. Duplicate removal requires the
same successful binding and value representation; a failed numeric reading does not
erase a valid source-text field. Scripted tests cover subtotal/note values, mixed
rows, sparse cells, partial bindings and pause/resume. Unseen forms remain unqualified.

Two bounded development probes stopped after structure, deliberately before meaning:

| Development input | Actual structure inference | Checked result |
|---|---|---|
| Merged-header HTML | 1 call / 80.2 seconds | Exact two rows, long IDs and decimal spelling |
| First table of the existing scan | 1 call / 97.6 seconds, plus one exact paragraph replay | Header excluded from records, Bolt/Nut cells correctly placed, unobserved Note uncertain |

Saved structure responses were also recompiled after the final defensive changes,
with identical values and value-source evidence. Both whole results remain partial
because the probes stopped before meaning. They are not fresh OCR, full-document,
independent-holdout, final-candidate or 16 GiB qualification.

Table meaning now selects literal source quotes. The program validates exact Unicode
ranges and hashes; context-only text cannot become direct evidence. Explicit source
reviews cover text outside the quotes, including text already read as values or
headers. Missing review is not inferred from successful value extraction.

A repair may change kind, description, scope or status, split or merge meanings, or
withdraw a mistaken interpretation. It must identify the previous revision, explain
changed IDs and retain review of their original text. Revision hashes bind content,
source inventory and transition history. Structure and source values cannot change;
issue-count reduction is not a quality test. Invalid repairs and exhausted budgets
preserve the prior accepted result. Scalar-form contracts remain unchanged.

Table protocol v8 separates at most two initial meaning attempts from one review
of an accepted meaning. Structure still permits at most two attempts. All calls
share the unchanged document call/time budget; a failed or unchanged review does
not trigger another attempt without an explicit grant. Repair feedback now includes
the exact remaining source ranges, their text and hashes, not only source IDs.
The program does not decide that a title has no further meaning or repair a wrong
quote occurrence automatically. This scheduling change has scripted regression
coverage; the fresh model run described below did not reach post-acceptance review.

Two full-path development runs used 2 calls each (148.2 and 146.5 seconds) within a
preselected 5-call / 900-second limit. Both reported complete but failed separate
content review: the unit was classified as a note and applied to the entire record,
and both meanings quoted the whole caption. The second run correctly limited the
condition to Length. Both preserved exact rows, precision and column/value sources.
Record-definition provenance also included data cells too broadly. No independent
holdout or final-candidate quality claim follows from these runs. The first run used
the pre-hardening source snapshot; exact execution source hashes are retained.

A source-first output-order experiment then used 2 calls / 229.8 seconds and also
reported complete, but regressed in content review: it combined the caption into a
record-wide note and misclassified four numeric values as units. That experiment was
reverted to prompt v21 / table protocol v6. Two human-segmented clause
probes, followed by a four-call sampling comparison, did not produce a profile that
correctly handled both the unit and condition. These component diagnostics do not
test automatic source selection or qualify a production profile.

A subsequent two-call comparison changed only thinking mode, retaining greedy sampling
and the same requests. Thinking was active, but both calls exhausted their 3,072-token
total output limit without final JSON (520.5 seconds combined). This is a budgeted
completion failure, not evidence about the correctness of an answer that was never
returned. Further prompt/sampling variants stopped while runtime/model compatibility
and explicit reasoning/final-output budgeting were examined.

The follow-up metadata audit found no mixed runtime, GGUF, template or tokenizer
files; it did not establish numerical conversion equivalence. A separate four-call
comparison then used the runtime's finite reasoning limit of 1,024 tokens per block,
retaining the same Q4 model, greedy sampling, requests and 3,072-token total ceiling.
Both limited-thinking calls returned the correct kind, content and column scopes;
non-thinking reproduced its previous errors. Total time was 226.4 seconds. This is
a pass for two human-segmented development clauses only, not automatic source
selection, a full product run or approval to change the production default.

The managed client now accepts an explicit administrator reasoning limit, records it
in execution/checkpoint identity and uses the same mode for template checks and
inference. Omission retains the non-thinking default. At clean source 1da72ad, a fresh
full-path development run with a 1,024-token reasoning limit used 3 calls / 588.8 seconds.
The final result preserved the two rows, identifiers, decimal spellings and exact value
and header provenance. The unit was mm on Length and Width only; the re-inspection
condition applied to Length only. Both direct quotes selected the correct clauses.

The first meaning response had invalid quote-occurrence indices and was rejected.
The corrected response left the caption's title range unreviewed, so the existing
stage limit ended the run partial. The title text itself was preserved as the repeat
label, but that does not establish semantic review of its source range. No additional
call or budget increase followed. This is a material development improvement, not
complete extraction or independent/final-candidate qualification. Repeat-level data
cell references were reviewed as relevant evidence of the same table's rows/structure;
their presence alone is not an unrelated-source failure. Column-name and value
provenance were checked separately.

At clean source 4f07271, one new full-path run stopped at the same 900-second budget
after two calls (900.94 seconds including cleanup). Structure was accepted after
797.99 seconds with exact rows, precision and header/value sources; meaning timed
out in the remaining 101.92 seconds. Thus neither full meaning quality nor the new
post-acceptance review was verified. The first request differed only in table protocol
version and again had 2,088 input tokens. Measured prompt processing was 12.05 tok/s
and generation 2.61 tok/s, substantially slower than the previous run. A single host
snapshot recorded swap use, but does not establish the cause. No automatic rerun or
budget increase followed; the server was closed and the partial result preserved.

Recognition adapter v12 added measured framework render/crop/rotation and cell-input
links to the serialized single-page PDF and original page. A fresh two-page OCR run
took 11.6 seconds and preserved all 12 input-coordinate links, including eight repair
crops. Both actual page and OCR rotations were zero. The original 12 issues remained
unchanged, including unobserved cells and repair-budget exhaustion; the result stayed
partial. This verifies input-coordinate capture, not OCR text or reported output boxes.

Adapter v13 additionally observes the full rendered RGB independently of OCR boxes.
It records non-white pixels, low-contrast pixels, bounded components and omitted
areas with source/page/render hashes. Non-white is not automatically text or a
graphic, and exact white is not a proven blank value. A render-only comparison of the
same two pages preserved their pixels and coordinate evidence and recorded 168/169
components; it used no OCR or semantic inference. Visual-content correspondence,
reading order and issue-specific completion remain unfinished. Original issues stay
active; visual observations alone cannot qualify recognition-backed PDF completion.

Adapter v14 adds bounded, two-way overlap candidates between those components and
source-bound native characters/raw OCR. Existing exact-text links connect structure
elements without trusting their reported boxes as content evidence. Reprocessing the
saved two-page observations took 0.032 seconds with no new render, OCR or model call.
Seven second-page components had no overlap candidate, and 11 of its 15 structure
elements had only partial text-range support. Original content, issues and model
payloads were unchanged. These are diagnostic candidates, not verified visual
assignments, blank values or a completion decision.

Adapter v16 connects repeated tokens only when a verified horizontal OCR line has a
unique, complete and exactly matching token sequence. It rejects table overlap,
cross-line joins, normalized text, ambiguous targets and incomplete duplicate lines.
Reimporting saved raw observations connected two repeated Korean tokens without new
OCR or inference. Their source text, coordinates and raw detections were unchanged;
the second page still has four unobserved cells and remains partial. Inspection of
the render confirmed that three of those cells contain text missed after the eight
repair calls were spent earlier in the table. Repair scheduling, blank-value evidence,
reading order and issue-specific completion remain unfinished.

Adapter v17 saves every planned cell before the first repair call, with immutable
indices/fingerprints and separate execution states. Budget stops, errors and
cancellation no longer remove later cells from that plan; no-ink observations are
not blank-value proof. This preserves snapshots, not a new guarantee that a forcibly
killed worker can write a final disk checkpoint.

A predeclared four-process comparison processed six images in 0.39 seconds
(124,716 input pixels). Two original Korean/English cell crops gave identical text,
geometry and confidence individually and in forward/reverse Tesseract file lists,
apart from the recorded input page number. Raw TSV and image-to-cell mapping were
preserved. Adapter v18 now implements an explicit list of at most two independent
cell images from one table/page/language/PSM. The default remains eight single-image
calls. Only an explicit batch profile selects 16 images within eight processes and
60 seconds; crop and total OCR-input pixel budgets are checked separately.

Raw OCR v2 stores the original full TSV once in `rawOCRRuns`; each capture retains
its input number, original row ordinal and cell/frame binding. Input number two is
not PDF page two. Partial output, timeout and a failed input cannot complete or
reuse the batch. The full unit plan and no-ink versus unobserved distinction remain.
Verified reuse still carries the existing conservative coverage warning. An error
later in the same table may leave earlier observations in raw evidence without
applying them to that attempt's structure input.

At clean source 25e6cd8, one fresh offline development recognition run processed the
same two-page PDF in 31.59 seconds (32.63 seconds including preparation), using six
repair processes and 11 images. The final `3 / 7.25 / OK`, Korean headers and item text
were returned, with the original TSV/input coordinates preserved. The result remains
partial: both empty Note locations are unobserved, a vertical line was additionally
read as `|`, and Korean spacing differs from the source. A subsequent pixel-only
inspection found a white cell interior but also faint edge pixels omitted by the
current OCR crop. This does not establish blank values or complete content. The outer
status wrapper failed after the recognition output was saved; its failure, uncaptured
Python exit code and successful OCR child exits are recorded separately. No retry,
semantic model, independent holdout or resource qualification was performed.

Adapter v19 now records the full cell, its interior, the OCR cell window and excluded
edge bands from existing canvas pixels. Exact white, faint/color and unknown-alpha
pixels stay distinct. Frame hashing, grid detection and repeated region inspection
share a separate finite observation budget; uncertain work after an error consumes
the remaining reservation without fabricating a measured-pixel count. A missing cache
does not trigger a render. Import checks internal statistics and source/frame linkage,
then requires unique unmerged table geometry before associating slots. Raw OCR links
are preserved separately and their execution remains unverified. These records do not
create values, prove blanks or clear existing issues/partial status. This change has
synthetic regression coverage, not a fresh whole-document recognition result.

Windows build preparation now takes pinned official product terms and records the
actual installed toolchain instead of searching recursively for a similarly named
license file. An installed-HTTP operational review links separate expected results,
raw observations, actual execution and image evidence. A fresh offline image builder
checks selected source/core/dependency bytes and exports an identified image. These
are implemented preparation paths, not executed Windows/image/HTTP qualification
or approval of redistribution rights. Host-published HTTP access needs its own check.

Linux builders now use Ubuntu 22.04 with explicit GCC 12, inspect actual ELF version
requirements and run relocated startup checks in an identified Debian bookworm image.
At commit b1a6cc9, the CPU binaries required GLIBC 2.34 / GLIBCXX 3.4.30 / CXXABI 1.3.13;
native Tesseract required GLIBC 2.35 with no dynamic GLIBCXX/CXXABI requirement. Both
passed actual bookworm startup. This resolves that build's earlier libc mismatch,
not final slim-image, OCR/inference, 16 GiB or minimum-kernel qualification.

The same CI exposed a Windows ZIP test-fixture problem and a native source-download
hash mismatch. Raw ZIP header tests and source acquisition diagnostics were corrected
without relaxing path or hash checks. Rust toolchain identity is now read in its pinned
source directory. At c02b7a6, core and CPU CI passed on all four targets; Linux and
Windows native preparation passed. Candidate attestation was skipped. At 1da72ad,
native preparation and the Linux-only delivery step also passed. The downloaded ZIP
matched the API SHA, and its files, sources, notices and recorded startup image were
checked against that exact source. Full recognition-stage and redistribution review
remain pending. Core CI subsequently passed all four platforms at 1da72ad. New 4f07271
CI passed pack contracts and three OS regression suites, but Windows regression
failed because Linux ZIP execute bits were checked through Windows-extracted file
permissions. The builder now checks the safe archive's recorded mode; actual image
execution checks remain unchanged. Direct regressions pass without adding skips,
and the follow-up 52c4d12 CI regression steps passed on all four existing platforms,
including Windows. Build completion is separate. These development runs are not one
final candidate.

After adapter v24 and the offline-stage assembler, core regression passed
**1,560 tests, with 212 skipped and 12 subtests**; Ruff and formatting checks passed.
The separate recognition-runtime overlay passed **399 PDF/OCR tests**. Core skips include optional recognition dependencies and actual
Windows process checks; they do not waive target-environment verification. No final
candidate has been fixed or published, and Toolkit/Sync consumption is unchanged.

A fresh adapter v19 development run returned in 7.88 seconds of recognition
(8.39 seconds including supervision), with child exit 0 and no known remaining
processes. Its 109 nodes, 357 bindings, seven issues and coverage matched the earlier
v18 result exactly. Twelve native-page cell slots were linked, but the scan-page
observation remained unavailable: hashing its full canvas twice exceeded the unchanged
16-million-pixel observation budget. Adapter v20 removes that duplicate hash within
one operation while independently checking externally supplied frames. Pixel evidence
does not resolve the remaining blank/line/content-completeness questions.

The fresh v20 full-path check then captured and uniquely linked all 24 slots in
7.73 seconds of recognition (8.18 seconds including supervision). Total cell observation
work was 14,985,921 pixels, below the unchanged limit. Nodes, bindings, tables, regions,
issues and coverage matched v19; the scan's 994 faint edge pixels remained recorded,
not erased or promoted to a blank value. This is development evidence, not holdout
quality or a 16 GiB resource qualification.

A clean-source adapter v21 development run completed one whole-path observation in
7.14 seconds (7.65 seconds including supervision), with exit 0 and confirmed known
process cleanup. Values, bindings, tables and all seven issues matched v20. The native
Note cell was not proved blank because embedded font programs and character mappings
were outside inventory v1. The scan retained its faint pixels. Adapter v22 adds narrow
source/native font-byte and glyph-outline checks before native blank decisions. Its
fresh run completed in 6.85 seconds but retained all seven issues: font bytes matched,
while eight PDFium text projections included trailing spaces absent from source Tj.
Adapter v23 separates literal source characters from explicitly verified generated
spacing without trimming real text. In a fresh clean-source v23 whole-path run, the
native Note cell was proved empty in 6.94 seconds of observation (7.47 seconds with
supervision). All 109 prior nodes, 357 binding definitions and their label/region
relationships were preserved; one blank node/binding was added. Per-run binding keys
shifted after insertion. Only the matching missing-cell issue was resolved, with its
original record retained. The scan remained unresolved and all six remaining issues
were preserved. Overall status is still partial. This is not
whole-page completeness or independent quality approval.

Adapter v24 then returned one clean-source whole-path observation in 8.19 seconds
(8.91 seconds including supervision), with exit 0 and confirmed known-process cleanup.
A 2,752-pixel window linked the unassigned native-page pipe detection to one original
vertical stroke: continuous identical profiles, gray edge pixels and white side
background were preserved. All values, tables, bindings, six issues and partial
coverage remained unchanged. Per-run timing and capture identities changed their
associated fingerprints. The native blank remained proved and the scan remained unresolved.
This is supporting evidence only: the original detection is not yet reclassified.

The 117 acquired ARM inputs were independently rehashed and inspected without installation.
Selected ELF files matched AArch64, but OpenCV's GUI dependencies, an empty Qt interpreter
entry and several bundled-library notices required follow-up; the explicit torchvision
derivative and its successful component probe are described above.
A pinned-source ANTLR 4.9.3 pure Python wheel was built once offline in a fresh isolated
environment; its 56 source files were preserved byte-for-byte. These are input-preparation
results, not complete recognition-stage, redistribution or target-execution approval.
The stage verifier now rejects bundled ELF loader names `ld-linux*.so`, including the
actual hash-renamed torchvision member. This is not detection of every arbitrarily
renamed loader. Header policy v2 allows the original ARM QtCore's one-byte empty
interpreter metadata only with its exact hash, SONAME, ELF type/entry/non-PIE checks
and an explicit shared-library role. Unspecified and executable roles still reject
it, and declared executables cannot be relabelled as libraries. The receipt records
this decision separately from execution qualification. Neither original file was
silently removed or replaced; the existing ABI limits remain unchanged.

The ARM native delivery archive was finally recovered with its exact API SHA and
statically reviewed against source 25e6cd8. Its 78 delivery files, 15 candidate files,
source notices, ARM ELF/loader and recorded Bookworm startup matched. Original failed
transfers remain failed records. No local native execution, fresh container inspection,
full recognition-stage approval or redistribution approval followed this review.

Signed Bookworm metadata was linked to 61 downloaded Debian packages, all matching
size and SHA. Static inspection retained their native file inventories and notice
paths without installing them. An isolated Spark-A base-image inventory found 32
matching package versions, 27 missing package names and two older versions. A separate
exact two-file QtCore/PCRE2 loading probe returned Qt 5.15.19, with the original unusual
interpreter metadata unchanged; required mappings and the unique system loader matched
observed hashes. Both probes exited cleanly and removed their owned containers. These
checks do not approve full OpenCV execution, portable-stage placement, corresponding
sources or redistribution terms.

The first full ARM stage attempt installed all 103 pinned distributions offline but
stopped at two legitimate shared console-script owners; inspection also found that
isolated Python had ignored the environment-only no-bytecode setting. Its failed
receipt and partial files remain preserved. After explicit `-B` and source/RECORD-bound
shared-owner validation, a fresh-source, fresh-output attempt completed in 56.19 seconds
(38.96 seconds assembly, 11.08 seconds relocated probe). The relocated stage contains
32,023 files and 2,201,045,715 bytes, unchanged before and after 17 headless imports,
one CPU PNG operation and Tesseract version/language checks. All 103 distributions,
178 actual mapped files, the unique system loader and the loaded selected OS files
were checked. Six original auxiliary scripts and the manpage remain preserved;
auxiliary CLI execution is not claimed.
The enforced container had four CPUs, 16 GiB memory, no extra swap, no network or GPU;
its cgroup peak was 5,098,418,176 bytes, with zero swap/OOM events. Exit 0 and owned
container cleanup were confirmed. Model weights were not loaded and no document/OCR
job was run. Native inputs still came from the earlier pinned development build:
this is not same-candidate full-pipeline, redistribution or release qualification.

### Earlier release-preparation history

The scope interpreter now offers observed header groups over their actual mapped
child columns and separates the current statement from surrounding source context.
Scope v5 normalizes repeated exact destinations (a group and its leaves), rejects
mixed ancestor/child selections and keeps region-local pointers distinct. Compiler
v12 records the column-definition mapping privately; public result v1 is unchanged.

The first v3 real-model rerun still applied the unit too broadly despite a complete
result flag. With v4, two real scope calls selected the correct measurement columns
and condition after replaying the accepted regional response, but duplicate group
and leaf targets were rejected. Replaying those saved decisions through v5 preserved
the two exact records, applied mm to Length/Width only and the condition to Length.
The subsequent fresh v5 full-product-path development run completed in 3 calls /
245 seconds and independently matched exact rows, precision, header provenance and
both scopes. None of these runs is independent holdout or 16 GiB qualification.

The saved-recognition scan returned partial after 6 calls / 359 seconds. Model-only
metadata compaction (prompt v14 / planner v9) removed input overflow: 10 regions,
none oversized, instead of 18 regions with 15 oversized inputs. The actual rerun
still failed after 2 calls / 373 seconds because the model emitted repeated copies
of the same records until the output limit. Source observations were not truncated.

Prompt v18 / planner v12 / table protocol v4 now separate one record-structure
decision from meaning over its frozen compiled values. Label/value forms use the
existing scalar path. Stage attempts and usage are checkpointed; each stage permits
at most two calls within the unchanged total budget. Cancellation, invalid meaning
or budget exhaustion preserves a compiled structure, not a complete extraction.
Row-role decisions contain only row and role; provenance is attached from actual
observed geometry, with missing cells left missing. Rows whose observed cells are
all explicitly declared headers are fixed by the program; mixed or unknown header
flags still require interpretation. Meaning scopes exclusively select columns, the
record, bounded row/column intersections, or unresolved applicability. That earlier repair preserved even mistaken meanings. Table protocol v6 instead
allows source-reviewed corrections while preserving frozen values; accepting a wire
response is not independent semantic approval.
Compiler v13 leaves unbounded parent/child meaning scopes unresolved instead of
silently broadening column assertions; explicitly bounded row intersections remain.

A fresh two-stage merged-header run used 2 calls / 113 seconds but incorrectly froze
an extra header record. After structural checks, a bounded 5-call / 258-second run
preserved exactly two source records and precision. It still failed semantic review:
the unit scope was unresolved after an invalid broad selection, and caption accounting
was incomplete. The previous single-stage development pass does not qualify this new
protocol. With the then-current protocol, a fresh 3-call / 277-second run compiled exactly
two rows on the first attempt, but merged caption unit/condition statements into a
record-wide condition and incorrectly called data-row summaries units. The repair
repeated that response; the result remains partial and fails content review. A saved-
recognition whole-scan attempt on protocol v2 used its 12-call cap / 761 seconds and
failed sparse-row mapping. Do not increase failed budgets or treat syntax/structure
acceptance as quality approval. An earlier-protocol first-scan-table probe (one exact
paragraph replay, two real calls / 222 seconds, planned stop after that table) kept
sparse data cells in their actual positions and the missing note uncertain, but included
the header as an extra record and produced a false unit/data summary. Its two stages
were mechanically complete; content review still failed. Neither whole-document nor
fresh-recognition qualification was attempted. Subtotal/note scalar preservation still
needs work.

Recognition adapter v10 retains original raw OCR detections and a bidirectional
processing ledger, separate native-text support, and a source-bound full-visible-page
render fingerprint. The actual applied rotation is distinct from an unknown orientation
observation. Render coverage, OCR truth and semantic completeness are different claims.
Missing cells, conflicts and unclassified visual content remain unresolved; no original
issue has been removed. Full visual-content and reading-order accounting is unfinished.
The current blanket recognition-completeness issue still blocks every recognition-backed
PDF from the complete-only release gate. This remains a release blocker.

At that earlier checkpoint, core regression was **744 passed, 10 skipped, 12 subtests
passed** and the separate recognition-runtime overlay passed 60 PDF/OCR tests; exact final
recognition bundles remain unqualified. Eleven input/specification cases covering nine
formats are prepared but have not undergone final model evaluation.

Evaluation v2 retains independently prepared per-case review specifications outside
model input. A separate review receipt binds the immutable inference report and
results. Release qualification v3 binds clean source, actual assets and installed
results, and uses linked cgroup measurements rather than summed process RSS.
Fresh-output builds and exact-byte promotion are implemented. Large packs use
explicit split/join transport without changing their reconstructed ZIP identity.
These tools do not supply missing platform, client or model-quality evidence.

Actual CI passed the Linux 16 GiB / four-CPU, swap-free, network-isolated preflight
and all four pinned CPU runtime builds. The preflight ran only a small synthetic
allocation, not OCR or inference. All four platforms, including Intel Mac, passed
portable core builds and Python-free CLI/MCP smoke on intermediate commit 526f25d;
this is not final-candidate or installed-client qualification. Linux native recognition
dependencies built and passed relocated startup/language discovery. Windows native
recognition then failed an optional SW package-manager dependency; the builder now
disables it explicitly, with target rerun still required.

Notice review found that the earlier Windows CPU candidate and recognition build
selected an extension's VS2015 preview EULA, not the installed VS2022 product terms.
Those candidates are ineligible for release. Both builders now reject that mismatch;
a matching identity screen still does not approve redistribution. Exact installed-edition
terms, static-runtime rights and final bundled notices remain independently reviewable
release blockers. Linux notices now include explicit IJG and Berkeley acknowledgments.
The built Linux CPU runtime requires glibc 2.38: the earlier bookworm preflight image
is not a compatible full-inference image. Final image/pack compatibility must be checked. The existing
Apple Silicon development pack remains selected for development runs; new runtime
packs must be paired with explicitly compatible model manifests before inference.
The x64 source-built runtime declares AVX2/FMA/F16C/BMI2 requirements, not generic x64.
See [release preparation and promotion](deployment/RELEASE.md).

### Improvements checked on the development cases

- Oversized non-table regions now have source-addressed subdivisions, unique value
  binding ownership and cumulative view coverage. Atomic fields, unbroken tokens
  or required context that cannot fit are explicitly partial, never truncated or
  silently omitted. Unseen long-document interpretation remains unqualified.
- One real CPU semantic call now returned the exact long identifier, integer `0`
  and boolean `false` with source-bound field definitions and values. That call
  remained partial because three local meaning scopes were absent. Same-region
  scope discovery and bounded scope-only repair have been implemented; scripted
  resume tests preserve the accepted region and repair three scopes in one call.
  One subsequent real CPU scope-only batch, following replay of that saved regional
  response, correctly attached all three meanings to their independently expected
  fields, preserved typed data and cleared the issues. The new scope call used
  1,767 input and 528 output tokens; no regional model call was repeated. This is
  affected-stage evidence, not an independent holdout or a fresh end-to-end run.
- On the existing public scanned-table case, the corrected `ruled_cells_v2`
  pipeline recovered all 11 nonempty cells exactly through actual TableFormer,
  including the Korean/English header order. Original recognition indices/text
  remain unchanged; structure-view clones carry a separate order and source map.
  One explicit budget resume reused completed pages/cells. The missing empty cell
  remains unobserved, not a fabricated blank. This is an affected development case,
  not full scanned-document or unseen-form qualification.
- Table request planning now measures the current wire contract rather than reserving
  12,000 characters unconditionally. Identical reference choices share schema
  definitions without weakening validation; required context is still retained.
- Prompt v12, planner v7 and compiler v7 give the interpreter program-derived table
  geometry: every table payload lists `columnCandidates` (each column index with the
  declared header cells above it) and `dataRows`; a repeat may map each column index
  once, data rows left outside every repeat are reported as `table_rows_outside_repeat`,
  a table's own caption is read with the table instead of as a separate region, and
  token (lexeme) spans are no longer offered as value choices because the interpreter
  cannot identify them by offset and guessed spans produced meaningless values. The
  managed interpreter now decodes greedily with a recorded seed. Regression tests cover
  the payload, the compiler checks, caption ownership across slices and the sampling
  identity; the real-model effect is recorded under the CPU timing follow-up.
- Prompt v13, planner v8 and compiler v8 withhold declared header cells and captions as
  value choices altogether, show each column candidate's header text, attach the
  declared headers above a column to its definition provenance, and report a cited
  header that does not sit above its column and a missing lowest header. A header text
  can therefore no longer be extracted as a data value, which is a documented limitation
  for pivot-style headers. Compiler v9 additionally drops a scalar field that re-reads a
  record cell of a repeat column, records the drop on that cell's accounting entry, and
  routes meanings that pointed at the dropped field to the scope repair step; subtotal,
  note and header rows keep their scalar fields. Compiler v10 degrades a source-less
  present field to uncertain instead of invalidating the response, compiler v11 drops a
  source-less scalar field defined only by mapped column headers, and scope statements
  from one source node are decided in separate requests after a batched decision was
  copied from one statement to the other.
- Prompt v11, planner v6 and compiler v6 treat declared header cells as definitions:
  header text is no longer a required value candidate, a field that reads a declared
  header cell as its value is reported as `header_cell_bound_as_value` and can be
  repaired without counting the dropped header reading as a regression, and repair
  feedback follows the unchanged region and contract in the request. Regression tests
  cover the flag, the repair and the planner. The real-model effect on the
  merged-header case is recorded under the CPU timing follow-up.
- A new synthetic multi-row-header form exposed header-only region slicing: the
  model interpreted header labels as values. That run was stopped after the first
  response and the checkpoint retained. Region plan v4 keeps header geometry with
  data views and removes context-only scalar choices; this example now fits as one
  15,668-character table request rather than four table fragments. These changes
  have regression coverage. One bounded CPU call on the corrected table timed out
  at 360 seconds without a complete response. It is **not** a semantic quality pass;
  token usage for that interrupted response is unknown, not zero consumption.

### CPU timing follow-up

Two bounded probes reused the same authorized public table request without trying
another full extraction: maximum 32 output tokens/90 seconds, then maximum one
output token/45 seconds with one native stack sample. Both timed out after checking
4,410 input tokens. Runtime preparation took about 16.46 and 8.02 seconds; context
checks took 0.015 and 0.030 seconds. The remaining wait was in the server exchange.
A one-second owned-process sample during the second request contained CPU tensor
computation/llama decode frames, not grammar/sampler frames. This establishes what
was active at that sampled instant, not a whole-run time breakdown or universal
cause. Neither probe produced server prompt/prediction timing receipts.

Increasing the response length limit or repeating the complete evaluation is not
justified by these findings. The next performance work should reduce repeated input
and contract processing and measure prompt evaluation separately from generation,
with the same correctness contract and fixed CPU pack. Do not substitute a faster
model or enable GPU/network fallback automatically. New managed diagnostics and
unreported-usage counters are implemented; old zero usage subtotals do not establish
zero computation or consumption.

A split measurement on 2026-09-09 (same Mac: Apple M5, four performance and six
efficiency cores, 24 GB) reused the current prompt v10 request of the same public
synthetic form through the product launcher and its loopback server, with bounded
token counts and generated text discarded. The formatted request is 4,253 tokens
(the earlier v9 request measured 4,410; a 3.6% token reduction). With the pinned
default threads, full prompt processing took 77.8 s cold and 78.0 s warm (54.5
tokens/s); a cached repeat processed 4 tokens in 0.12 s. Adding the product grammar
cost no measurable setup time (0.129 s versus 0.128 s for one token), and 64-token
generation ran at 12.8 tokens/s with grammar and 12.9 tokens/s without. Ten threads
raised prompt processing to 72.1 tokens/s but cut generation to 8.7 tokens/s; four
generation threads with ten prompt-processing threads gave 73.0 and 12.6 tokens/s.
Prompt processing alone therefore needs about 80 s at default threads, which
accounts for the earlier 45 s and 90 s probe timeouts; grammar setup, grammar
sampling and model page-in are excluded as causes. The remaining cost is output
length: the 3,072-token ceiling alone takes about 240 s at 12.8 tokens/s. Local-pack
profiles can now set `threads`/`threadsBatch` explicitly; defaults are unchanged.
These are Mac measurements, not x64 or 16 GiB evidence.

A bounded rerun of the merged-header development case on the same day (two calls,
900 s budget, default threads) completed both calls instead of timing out: the
regional call processed 4,253 prompt tokens in 81 s and generated 1,055 tokens in
89 s; the repair call reused only 708 cached tokens, processed 3,568 tokens in 68 s
and generated 1,862 tokens in 163 s. Both responses read the declared header cells
as scalar field values and mapped no repeat, so the two data rows were never
expanded; identifiers and decimals were preserved exactly, and the mm unit and the
length condition were attached to scalar fields. This is a semantic failure of the
affected case, not a latency failure. Prefix-reuse probes showed that the pinned
server restores an early checkpoint (708 tokens in these runs) whenever the new
prompt diverges before the previous prompt's final tokens, so the repair request
reprocessed 3,568 tokens although it shared about 2,300 tokens with the original;
`--checkpoint-min-step 512` did not change this. An identical prompt, or one that
differs only after position 4,249, is reused in full. Prompt v11 therefore places
repair feedback after the unchanged region and contract; scope calls with a
different payload still pay most of the prompt cost.

A further bounded rerun with prompt v11, planner v6, compiler v6 and explicit
`threads` 4 / `threadsBatch` 10 through the product client (three calls, 900 s
budget, 591 s used) again completed without timeouts. The regional call processed
4,300 tokens at 73 tokens/s and generated 2,534 tokens; the repair call reused
3,784 cached tokens and processed only 565 (8.6 s instead of 68 s); the caption
region call processed 1,684 tokens. Semantically, the first response still read the
four declared header cells as values, now reported as `header_cell_bound_as_value`,
and mapped a single repeat row with duplicated columns. The repair dropped the
header values but also dropped the repeat and one data cell, so the engine kept the
first response and reported `region_repair_no_progress`. The caption region produced
fields from arbitrarily chosen lexeme bindings. Exact identifiers and decimals were
preserved and the mm unit reached the length and width fields. The case remains
failed on repeat mapping and merged-header relationships; it is development
material.

With prompt v12, planner v7, compiler v7 and greedy decoding (two calls, 405 s; the
caption now belongs to the table region, so no separate caption call), the first
response for the first time mapped one repeat over both data rows with data row
roles, but paired the merged "Measurements" header with column 1 and the "Length"
header with column 2, dropped "Width", and still added a scalar field for every
cell, including the four header cells and the caption. The repair reused 3,495
cached tokens (7.8 s of prompt processing) but repeated property keys and was
rejected as invalid, so the first response stayed. Identifiers and decimals were
exact; the unit and condition reached the length fields and the repeat. Prompt
v13, planner v8 and compiler v8 respond structurally: header cells and captions
are no longer value choices, column candidates carry their header text, and
misplaced header citations are reported for repair.

With prompt v13, planner v8 and compiler v8 (two calls, 236 s), the first response
mapped one repeat over both data rows with the correct column indices and the
lowest header of each column cited, so the compiled records matched the two
expected source rows exactly and the merged "Measurements" header entered the
column provenance programmatically. It still added three scalar fields on record
cells (one mislabelled) and attached the unit and condition to those scalar
fields; the repair swapped two column indices and referenced consumed bindings in
`excludedBindings`, was rejected, and the first response stayed. Compiler v9 now
drops such scalar readings of record cells and routes their meanings to scope
repair. The rerun with compiler v9 (two calls, 264 s) exposed the next weakness:
the first response repeated the correct repeat but also emitted a `present` field
with no source binding, which invalidated the whole response, and the repair then
removed the repeat and read two width cells as length and width. Compiler v10
therefore degrades a source-less present field to `uncertain` with a
`field_binding_missing` report instead of discarding the response. Greedy decoding
with ten prompt-processing threads did not reproduce identical outputs across
runs; it removes sampling noise, not floating-point order effects.

The rerun with compiler v10 (three calls, 340 s) is the first run of this case that
ended `complete`: the regional call kept the correct two-row repeat while the
compiler dropped five redundant scalar readings and degraded one source-less
field; the repair (3,343 cached tokens) resolved that field as absent and kept the
repeat with data row roles; the scope call attached the unit and the condition to
the Length column. Compiled records match the two expected source rows exactly and
the merged header sits in the column provenance. Two defects remain against the
fixed expectations: the mm unit was applied to Length only, not Width, and the
model still declared three `absent` scalar fields for header labels (`sample_id`,
`measurement_value`, `measurement_value_row`) that add null properties. This is one
development case with a 9B CPU model, not a holdout pass.

With compiler v11 and per-source scope requests (three calls, 207 s, 6,083 prompt
and 1,735 completion tokens), the same regional response compiled directly into the
correct two-row repeat with no scalar leftovers and no repair call: seven redundant
cell readings and one header-label field were dropped into the accounting ledger.
Decided separately, the condition statement was applied to the Length column and the
unit statement was left unresolved by the model, so the result is `partial` with one
`semantic_scope_unresolved` issue rather than a wrong scope. Against the fixed
expectations, records, merged-header provenance and the condition scope pass; the
mm unit should reach Length and Width and remains open.

### Input compaction follow-up

Prompt v10 / region planner v5 retain standard JSON Schema while sharing repeated
constraints. The model-only table projection uses explicit `columns-rows.v1`
headers and all cell rows when every cell has the same property keys and the
representation is smaller. Mixed keys stay objects: a missing property is never
padded with null. Header geometry, ordering, precision and source references are
preserved; stored observations and public results are unchanged.

A serialization-only comparison of the previously recorded public merged-header
request reduced the output contract from 6,226 to 6,053 characters and table data
from 1,294 to 723 characters. Including the new explanation of the representation,
full message content changed from 15,700 to 15,227 characters (about 3%). These are
character counts, not measured tokens or latency. No model calls were issued for
this comparison. The 165 related regression tests pass, including exact cell
round-trip, required/null/bounds validation and planned-versus-actual request size.

This modest total reduction does not resolve the CPU latency gate. Actual model
interpretation of the new representation and decoder acceptance remain unverified;
do not count stored-response tests as that evidence or publish the candidate as
qualified. Further optimization should target the measured input/compute bottleneck,
not remove meaning-bearing source content or repeatedly rerun full extraction.

### Remaining release gates and next bounded steps

| Gate | Required next step, without restarting all evaluation |
|---|---|
| Scanned table correctness | The affected 11 nonempty cells passed through TableFormer. Verify full field/header semantics and blank-versus-unobserved states, then unseen scans. Do not substitute observed no-ink for a semantic blank. |
| Semantic scope quality | The affected three-field scope repair passed. The merged-header development case now compiles the correct two-row repeat with merged-header provenance and the condition scope without repair calls; the unit statement stays unresolved. Next: full scanned/long-document semantics and independent forms. Use a two-step table protocol only if remaining structural errors or bounded execution require it; no release holdout has passed. |
| Long documents | Use affected long/continued-table cases to check complete repeat ranges, heading/note scope and source-view accounting; keep indivisible-context limits explicit. |
| 16 GB / GPU-free operation | Run the [bounded Linux CPU procedure](deployment/CPU_QUALIFICATION.md) on an available host with compatible Linux packs. The inspected Mac is 24 GB; no container runtime or reachable Tailscale peer was available. |
| Delivery | Install/update/rollback and actual client processing still need Windows, Linux, Intel Mac, installed Codex/Claude and ChatGPT-host evidence. |

The latest source changes are not in the previously assembled core bundles or the
immutable v1 recognition pack. A new explicitly versioned/configured candidate is
required before delivery checks. Static container-policy tests are not an actual
Compose, no-network or 16 GiB resource test. Mac CPU measurements are neither x64
speed evidence nor proof that a physical 16 GB computer has sufficient headroom.

Do not publish stable assets, advertise these targets as passed, or switch Toolkit/
Sync to this candidate while these gates remain open. Existing results and the
frozen compatibility source are retained; packaging changes do not force reanalysis.

## Required delivery evidence

Record the product commit, pack manifest digests, OS/hardware, install/update/
rollback results, actual client and HTTP processing, and independently reviewed
recognition/extraction cases. Include the license inventory, model conversion
receipt, support limitations and operational instructions. Keep scanned/long-
document quality and CPU latency/memory findings distinct from packaging success.
No SLA, managed service, automatic updates, indefinite historical-version support,
or specific processing throughput is implied by the OSS package.

Report ordinary issues through the repository issue tracker using versions,
platform and a minimal public or synthetic example. Report security issues through
SECURITY.md. A source document need not and should not be shared by default.
