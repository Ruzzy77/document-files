"""Scripted table revision regression tests, not independent model qualification."""

import copy

import pytest

from document_files.document_model.model import ObservationDocument
from document_files.interpretation import engine
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.semantic_types import RegionInterpretation
from document_files.interpretation.table_protocol import meaning_ir, meaning_response
from document_files.interpretation.table_revisions import (
    MeaningRevisionError,
    meaning_revision,
    preserve_reviewed_ranges,
    validate_revision,
)
from document_files.interpretation.table_sources import source_inventory


@pytest.fixture
def table():
    observation = ObservationDocument(
        nodes={
            "header": {"text": "Length"},
            "cell": {"text": "001.2300"},
            "caption": {"text": "Measurements. Unit is mm. Inspect when length is low."},
            "context": {"text": "Context only; not directly owned"},
        },
        bindings={"value": {"sourceRef": "cell", "path": "/text", "candidateRole": "cell"}},
        tables={
            "t": {
                "basis": "native_html",
                "cells": [
                    {"sourceRef": "header", "row": 0, "col": 0, "isHeader": True},
                    {"sourceRef": "cell", "row": 1, "col": 0},
                ],
            }
        },
    )
    region = {
        "id": "r",
        "tableRef": "t",
        "nodeIds": ["header", "cell", "caption"],
        "contextNodeIds": ["context"],
        "bindingIds": ["value"],
    }
    ir = RegionInterpretation.model_validate(
        {
            "regionId": "r",
            "repeats": [
                {
                    "id": "rows",
                    "key": "rows",
                    "label": "Measurements",
                    "tableRef": "t",
                    "rowStart": 0,
                    "rowEnd": 1,
                    "definitionRefs": ["header"],
                    "rowRoles": [
                        {"row": 0, "role": "header", "sourceRefs": ["header"]},
                        {"row": 1, "role": "data", "sourceRefs": ["cell"]},
                    ],
                    "columns": [
                        {
                            "id": "length",
                            "key": "length",
                            "label": "Length",
                            "column": 0,
                            "valueType": "decimal",
                            "definitionRefs": ["header"],
                        }
                    ],
                }
            ],
        }
    )
    return observation, region, ir, source_inventory(observation, region)


def note(mid="m", text="Unit is mm.", kind="unit", ref="caption", **changes):
    return {
        "id": mid,
        "kind": kind,
        "description": text,
        "sourceQuotes": [{"sourceRef": ref, "text": text}],
        "scope": {"kind": "columns", "columnIds": ["length"]},
        "status": "interpreted",
        **changes,
    }


def review(refs=None, role="no_additional_meaning"):
    return {
        "sourceRefs": refs or ["header", "cell", "caption"],
        "role": role,
        "explanation": "Scripted comparison of remaining original source",
    }


def response(meanings, *, previous=None, reviews=None, changes=None):
    return {
        "regionId": "r",
        "meanings": meanings,
        "sourceReviews": [review()] if reviews is None else reviews,
        "baseRevision": previous.tableMeaningState.revisionSHA256 if previous else None,
        "changes": changes or [],
    }


def change(prior=None, replacements=None, refs=None):
    return {
        "previousIds": prior or ["m"],
        "replacementIds": replacements or [],
        "reviewSourceRefs": refs or [],
        "reason": "Corrected after exact source comparison",
    }


def compile_meanings(table, payload, previous=None):
    observation, region, frozen, inventory = table
    current = meaning_ir(payload, previous or frozen, inventory)
    return current, compile_region(current, observation, region)


def test_combined_wrong_meaning_can_split_without_changing_structure_or_values(table):
    old, before = compile_meanings(
        table,
        response(
            [
                note(
                    text="Unit is mm. Inspect when length is low.",
                    kind="condition",
                    scope={"kind": "record"},
                )
            ]
        ),
    )
    new, after = compile_meanings(
        table,
        response(
            [note("unit"), note("condition", "Inspect when length is low.", "condition")],
            previous=old,
            changes=[change(replacements=["unit", "condition"])],
        ),
        old,
    )
    assert before.data == after.data == {"rows": [{"length": "001.2300"}]}
    assert before.schema == after.schema and old.repeats == new.repeats
    assert before.consumed_bindings == after.consumed_bindings
    assert engine._meaning_repair_improves(old, before, new, after)
    assert {m.kind for m in new.meanings} == {"unit", "condition"}
    assert len(before.issues) == len(after.issues)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "note"},
        {"description": "Corrected source description"},
        {"sourceQuotes": [{"sourceRef": "caption", "text": "mm"}]},
        {"status": "uncertain"},
        {"scope": {"kind": "record"}},
    ],
)
def test_kind_description_range_status_and_scope_are_revisable(table, changes):
    old, before = compile_meanings(table, response([note()]))
    new, after = compile_meanings(
        table, response([note(**changes)], previous=old, changes=[change(replacements=["m"])]), old
    )
    assert engine._meaning_repair_improves(old, before, new, after)
    assert (
        after.data == before.data and new.meanings[0].model_dump() != old.meanings[0].model_dump()
    )
    if changes.get("status") == "uncertain":
        assert len(after.issues) > len(before.issues)
        assert after.meaning_review["unresolved"] == []
        assert any(i["code"] == "semantic_scope_uncertain" for i in after.issues)


def test_false_unit_over_data_can_be_withdrawn_with_original_value_preserved(table):
    old, before = compile_meanings(table, response([note(text="001.2300", ref="cell")]))
    new, after = compile_meanings(
        table, response([], previous=old, changes=[change(refs=["cell"])]), old
    )
    assert not new.meanings
    assert before.data == after.data == {"rows": [{"length": "001.2300"}]}
    assert engine._meaning_repair_improves(old, before, new, after)
    assert not after.meaning_review["unreviewed"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_change",
        "stale",
        "context",
        "quote",
        "source_refs",
        "offset",
        "unknown_replacement",
        "missing_withdrawal_review",
    ],
)
def test_unexplained_or_unbound_revisions_are_rejected(table, mutation):
    old, _ = compile_meanings(table, response([note()]))
    payload = response(
        [note(description="Corrected")], previous=old, changes=[change(replacements=["m"])]
    )
    if mutation == "missing_change":
        payload["changes"] = []
    elif mutation == "stale":
        payload["baseRevision"] = "0" * 64
    elif mutation == "context":
        payload["meanings"][0]["sourceQuotes"] = [{"sourceRef": "context", "text": "Context only"}]
    elif mutation == "quote":
        payload["meanings"][0]["sourceQuotes"][0]["text"] = "invented paraphrase"
    elif mutation == "source_refs":
        payload["meanings"][0]["sourceRefs"] = ["caption"]
    elif mutation == "offset":
        payload["meanings"][0]["sourceQuotes"][0]["start"] = 14
    elif mutation == "unknown_replacement":
        payload["changes"][0]["replacementIds"] = ["absent"]
    else:
        payload["meanings"] = []
        payload["sourceReviews"] = [review(["header", "cell"])]
        payload["changes"] = [change()]
    with pytest.raises((CompileError, ValueError)):
        compile_meanings(table, payload, old)


def test_retracting_old_quote_cannot_hide_a_new_nonwhitespace_gap(table):
    old, before = compile_meanings(table, response([note()]))
    new, after = compile_meanings(
        table,
        response(
            [note(text="mm")],
            previous=old,
            reviews=[review(["header", "cell"])],
            changes=[change(replacements=["m"])],
        ),
        old,
    )
    assert after.meaning_review["unreviewed"] == ["caption"]
    with pytest.raises(MeaningRevisionError, match="coverage_regressed"):
        preserve_reviewed_ranges(before.meaning_review, after.meaning_review)


@pytest.mark.parametrize("reword_review", [False, True])
def test_same_meaning_response_is_not_progress_even_with_reworded_review(table, reword_review):
    old, _ = compile_meanings(table, response([note()]))
    payload = response([note()], previous=old)
    if reword_review:
        payload["sourceReviews"][0]["explanation"] = "Different words, same decision"
    with pytest.raises(CompileError, match="no_progress"):
        compile_meanings(table, payload, old)


def test_full_wire_feedback_keeps_resolved_quotes_not_offsets(table):
    old, _ = compile_meanings(table, response([note()]))
    payload = meaning_response(old, table[3])
    assert payload["meanings"][0]["sourceQuotes"] == [
        {"sourceRef": "caption", "text": "Unit is mm.", "occurrence": 0}
    ]
    assert "sourceRanges" not in payload["meanings"][0]
    assert "sourceRefs" not in payload["meanings"][0]


def test_forged_range_or_inventory_is_rejected_by_compilation_even_after_rehash(table):
    current, _ = compile_meanings(table, response([note()]))
    observation, region, _, _ = table
    for key in ("source", "range"):
        forged = copy.deepcopy(current)
        if key == "source":
            forged.tableMeaningState.inventorySHA256 = "0" * 64
        else:
            forged.meanings[0].sourceRanges[0].text = "forged"
        forged.tableMeaningState.revisionSHA256 = meaning_revision(forged)
        with pytest.raises(CompileError):
            compile_region(forged, observation, region)


def test_initial_response_cannot_forge_prior_revision_history(table):
    value = response([note()])
    value["baseRevision"] = "0" * 64
    with pytest.raises(CompileError, match="initial_revision"):
        compile_meanings(table, value)


def test_revision_validates_stored_content_hash(table):
    current, _ = compile_meanings(table, response([note()]))
    current.meanings[0].description = "Changed without updating the accepted hash"
    with pytest.raises(MeaningRevisionError, match="revision_mismatch"):
        validate_revision(None, current)


def test_whitespace_only_change_reason_is_not_an_explanation(table):
    old, _ = compile_meanings(table, response([note()]))
    amended = change(replacements=["m"]) | {"reason": "  \n "}
    with pytest.raises((CompileError, ValueError)):
        compile_meanings(
            table, response([note(description="Corrected")], previous=old, changes=[amended]), old
        )


def test_revision_hash_binds_transition_history_reason(table):
    old, _ = compile_meanings(table, response([note()]))
    new, _ = compile_meanings(
        table,
        response(
            [note(description="Corrected")], previous=old, changes=[change(replacements=["m"])]
        ),
        old,
    )
    new.tableMeaningState.changes[0].reason = "Altered historical explanation"
    with pytest.raises(MeaningRevisionError, match="revision_mismatch"):
        validate_revision(old, new)


@pytest.mark.parametrize("field", ["meanings", "sourceReviews", "baseRevision", "changes"])
def test_required_table_meaning_response_fields_are_not_inferred(table, field):
    payload = response([note()])
    payload.pop(field)
    with pytest.raises((CompileError, ValueError)):
        compile_meanings(table, payload)
