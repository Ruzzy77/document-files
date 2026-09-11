"""Synthetic owned reviews, not OCR truth or independent document qualification."""

import time
from copy import deepcopy

import pytest
from test_pdf_image_projection import approved, example, proposal, setup
from test_pdf_visual_apply import assert_unchanged

from document_files.interpretation.pdf_visual_apply import VERSION, apply_page_reviews
from document_files.interpretation.pdf_visual_plan import (
    build_page_plan,
    digest,
    observation_page_fingerprint,
    validate_decision,
)
from document_files.interpretation.regions import region_payload


def fixture(empty=False, change=None):
    doc, capture, reading, _, pixels = example(empty=empty)
    view = proposal(doc, reading)
    if change:
        change(view)
    plan = build_page_plan(view, capture, pixels, deadline=time.monotonic() + 30)
    review = {
        "plan": plan,
        "validation": validate_decision(plan, approved(plan), detail_bounds=[0, 0, 90, 90]),
    }
    return doc, view, [review]


@pytest.mark.parametrize("empty", [False, True])
def test_reviewed_replacement_resolves_only_its_own_gap_and_retains_raw_table(empty):
    doc, view, reviews = fixture(empty)
    before = deepcopy(view)
    result = apply_page_reviews(view, reviews)
    assert view == before
    app = result.provenance["pdfVisualReviewApplication"]
    assert app["status"] == "applied" and app["version"] == VERSION
    assert result.issues == [] and result.coverage["recognitionContentCompleteness"] == "complete"
    assert result.coverage["status"] == "observed"
    for field in ("nodes", "bindings", "tables"):
        assert all(getattr(result, field)[k] == v for k, v in getattr(doc, field).items())
    projection = result.provenance["pdfImageReadProjections"][0]
    assert projection == view.provenance["pdfImageReadProjections"][0]
    assert (
        projection["originalRegions"] == doc.regions and projection["originalIssues"] == doc.issues
    )
    assert result.tables["table"]["unobservedCellCount"] == 1
    new_ref = projection["selectedTableRefs"][0]
    table = result.tables[new_ref]
    assert len(table["cells"]) == 6 and table["unobservedCellCount"] == 0
    assert table["declaredRowCount"] == 3
    if empty:
        ref = f"{new_ref}/visual-blank/1:1"
        assert result.nodes[ref]["text"] == ""
        assert result.nodes[ref]["sourceStructure"]["slotKey"] == "slot-1-1"
        assert next(b for b in result.bindings.values() if b["sourceRef"] == ref)["blank"] is True
    else:
        assert "0.020" in [result.nodes[c["sourceRef"]]["text"] for c in table["cells"]]
    resolutions = app["resolvedIssues"]
    replacement = next(r for r in resolutions if r["basis"] == "owned_visual_replacement_table")
    assert replacement["originalIssue"] == doc.issues[0]
    assert replacement["priorTableRef"] == "table" and replacement["replacementTableRef"] == new_ref
    assert replacement["projectionFingerprint"] == projection["fingerprint"]
    assert replacement["planFingerprint"] == reviews[0]["plan"]["fingerprint"]
    assert replacement["decisionFingerprint"] == reviews[0]["validation"]["decisionFingerprint"]
    ledger = result.provenance["recognitionProcessingLedgers"][0]
    for field in ("entries", "rawOCRPasses", "rawOCRRuns", "unverifiedTableExtents"):
        assert ledger[field] == doc.provenance["recognitionProcessingLedgers"][0][field]
    assert ledger["processingDependencies"]["tableRefs"] == ["table"]
    assert not ledger["processingDependencies"]["issues"]
    assert ledger["visualReviewResolutions"] == [replacement]
    assert app["ocrTruthVerified"] is app["independentQualityApproval"] is False


@pytest.mark.parametrize("change", ["missing", "duplicate", "count", "page", "other_ledger"])
def test_inconsistent_original_issue_or_dependency_preserves_all_changes_atomically(change):
    def mutate(view):
        ledger = view.provenance["recognitionProcessingLedgers"][0]
        if change == "missing":
            ledger["processingDependencies"]["issues"] = []
        elif change == "duplicate":
            ledger["processingDependencies"]["issues"] *= 2
        elif change == "count":
            view.issues[0]["count"] = 2
        elif change == "page":
            ledger["processingDependencies"]["pages"] = [2]
        else:
            ledger["processingDependencies"]["tableRefs"] = ["unrelated"]

    _, view, reviews = fixture(True, mutate)
    assert_unchanged(apply_page_reviews(view, reviews), view)


@pytest.mark.parametrize("change", ["unknown_text", "unknown_grid", "unknown_blank"])
def test_unknown_review_never_closes_original_or_candidate_gaps(change):
    _, view, reviews = fixture(True)
    r = reviews[0]
    decision = deepcopy(r["validation"]["decision"])
    key = {"unknown_text": "sourceChecks", "unknown_grid": "gridChecks", "unknown_blank": "slots"}[
        change
    ]
    decision[key][0]["decision"] = "unknown"
    r["validation"] = validate_decision(r["plan"], decision, detail_bounds=[0, 0, 90, 90])
    assert_unchanged(apply_page_reviews(view, reviews), view)


@pytest.mark.parametrize(
    "change", ["projection_id", "prior_table", "source_inventory", "grid_rows"]
)
def test_rehashed_review_cannot_select_a_different_projection(change):
    _, view, reviews = fixture(True)
    r = reviews[0]
    proposed = r["plan"]["imageReadProposal"]
    if change == "projection_id":
        proposed["fingerprint"] = "b" * 64
    elif change == "prior_table":
        proposed["grids"][0]["priorTableRef"] = proposed["grids"][0]["tableRef"]
    elif change == "source_inventory":
        proposed["sourceIds"].reverse()
    else:
        proposed["grids"][0]["rows"] += 1
    r["plan"]["fingerprint"] = digest({k: v for k, v in r["plan"].items() if k != "fingerprint"})
    # No need to validate a changed/unmeasurable decision: application checks ownership first.
    assert_unchanged(apply_page_reviews(view, reviews), view)


@pytest.mark.parametrize("kind", ["raw_unresolved", "extent_unverified", "semantic", "other_table"])
def test_unrelated_unknowns_are_not_replaced_with_visual_approval(kind):
    def mutate(view):
        ledger = view.provenance["recognitionProcessingLedgers"][0]
        if kind == "raw_unresolved":
            ledger["entries"][0]["status"] = "unresolved"
            view.issues[1]["unresolvedDetections"] = 1
        elif kind == "extent_unverified":
            ledger["unverifiedTableExtents"] = ["table"]
        elif kind == "semantic":
            view.issue("semantic_scope_unresolved", page=1)
        else:
            issue = {"code": "recognition_table_cells_unobserved", "tableRef": "other", "count": 1}
            view.issues.append(issue)
            ledger["processingDependencies"]["issues"].append(deepcopy(issue))

    _, view, reviews = fixture(True, mutate)
    result = apply_page_reviews(view, reviews)
    assert result.provenance["pdfVisualReviewApplication"]["status"] == "applied"
    assert result.coverage["status"] == "partial"
    assert result.coverage["recognitionContentCompleteness"] == "unverified"
    assert {"code": "recognition_content_completeness_unverified"} in result.issues
    assert result.tables["table"] == view.tables["table"]
    assert not any(i.get("tableRef") == "table" for i in result.issues)
    if kind == "semantic":
        assert {"code": "semantic_scope_unresolved", "page": 1} in result.issues
    if kind == "other_table":
        assert any(i.get("tableRef") == "other" for i in result.issues)


def test_stale_raw_geometry_cannot_be_rebound_by_a_recomputed_page_hash():
    _, view, reviews = fixture(True)
    r = reviews[0]
    view.provenance["recognitionCellPixelObservations"][0]["observations"][0]["observation"][
        "slots"
    ][3]["fullPixelBox"][0] += 1
    r["plan"]["sourceObservationFingerprint"] = observation_page_fingerprint(view, 1)
    r["plan"]["fingerprint"] = digest({k: v for k, v in r["plan"].items() if k != "fingerprint"})
    assert_unchanged(apply_page_reviews(view, reviews), view)


@pytest.mark.parametrize("field", ["row", "col", "sourceRef", "rowSpan", "bbox"])
def test_rehashed_page_cannot_move_new_cells_away_from_their_source_slots(field):
    _, view, reviews = fixture(True)
    new_ref = view.provenance["pdfImageReadProjections"][0]["selectedTableRefs"][0]
    cell = view.tables[new_ref]["cells"][0]
    if field in {"row", "col"}:
        cell[field] += 1
    elif field == "sourceRef":
        cell[field] = view.tables[new_ref]["cells"][1]["sourceRef"]
    elif field == "rowSpan":
        cell[field] = True
    else:
        view.nodes[cell["sourceRef"]]["sourceStructure"]["bbox"]["left"] += 0.01
    r = reviews[0]
    r["plan"]["sourceObservationFingerprint"] = observation_page_fingerprint(view, 1)
    r["plan"]["fingerprint"] = digest({k: v for k, v in r["plan"].items() if k != "fingerprint"})
    assert_unchanged(apply_page_reviews(view, reviews), view)


def test_owned_runner_empty_cell_reaches_semantic_region_and_resumes_without_calls(monkeypatch):
    run, calls, _, _, doc = setup(monkeypatch, empty=True)
    result, state = run()
    # Two reading parts (cells, lines) and one review of the reading.
    assert result is not None and len(calls) == 3
    assert result.tables["table"] == doc.tables["table"]
    region = next(r for r in result.regions if r.get("tableRef"))
    blank_ref = f"{region['tableRef']}/visual-blank/1:1"
    payload = region_payload(result, region)
    assert payload["nodes"][blank_ref]["text"] == ""
    assert any(b["sourceRef"] == blank_ref and b.get("blank") for b in payload["bindings"].values())
    assert "table" not in payload["tables"]
    assert run(state)[0] == result and len(calls) == 3


@pytest.mark.parametrize("old_version", [None, "document-files.pdf-visual-apply.v3"])
def test_old_application_checkpoint_cannot_gain_new_resolutions_on_resume(monkeypatch, old_version):
    run, calls, _, _, _ = setup(monkeypatch, empty=True)
    _, state = run()
    if old_version is None:
        del state["applicationVersion"]
    else:
        state["applicationVersion"] = old_version
    with pytest.raises(ValueError, match="incompatible"):
        run(state)
    assert len(calls) == 3
