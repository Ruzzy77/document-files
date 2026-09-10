"""Source-first contract regressions; scripted semantics are not quality qualification."""

import copy

import pytest
from jsonschema import Draft202012Validator

from document_files.document_model.model import ObservationDocument
from document_files.interpretation import engine
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.legacy_engine import decode
from document_files.interpretation.semantic_types import RegionInterpretation
from document_files.interpretation.table_protocol import (
    meaning_decision_ir,
    meaning_decision_response,
    meaning_decision_schema,
)
from document_files.interpretation.table_revisions import MeaningRevisionError, meaning_snapshot
from document_files.interpretation.table_source_decisions import (
    source_decisions_from_flat,
)
from document_files.interpretation.table_sources import source_inventory


@pytest.fixture
def sample():
    doc = ObservationDocument(
        nodes={
            "h": {"text": "Length"},
            "v": {"text": "001.2300"},
            "a": {"text": "Lengths use mm; warm tests require review."},
            "b": {"text": "Applies only to Length."},
            "empty": {"text": ""},
            "context": {"text": "Other context is not direct evidence."},
        },
        bindings={"value": {"sourceRef": "v", "path": "/text", "candidateRole": "cell"}},
        tables={
            "t": {
                "basis": "native_html",
                "cells": [
                    {"sourceRef": "h", "row": 0, "col": 0, "isHeader": True},
                    {"sourceRef": "v", "row": 1, "col": 0},
                ],
            }
        },
    )
    region = {
        "id": "r",
        "tableRef": "t",
        "nodeIds": ["h", "v", "a", "b", "empty"],
        "contextNodeIds": ["context"],
        "bindingIds": ["value"],
    }
    frozen = RegionInterpretation.model_validate(
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
                    "definitionRefs": ["h"],
                    "rowRoles": [
                        {"row": 0, "role": "header", "sourceRefs": ["h"]},
                        {"row": 1, "role": "data", "sourceRefs": ["v"]},
                    ],
                    "columns": [
                        {
                            "id": "length",
                            "key": "length",
                            "label": "Length",
                            "column": 0,
                            "valueType": "decimal",
                            "definitionRefs": ["h"],
                        }
                    ],
                }
            ],
        }
    )
    inventory = source_inventory(doc, region)
    flat = {
        "regionId": "r",
        "meanings": [],
        "sourceReviews": [
            {"sourceRefs": [ref], "role": "no_additional_meaning", "explanation": "Scripted review"}
            for ref in region["nodeIds"]
        ],
        "baseRevision": None,
        "changes": [],
    }
    return doc, region, frozen, inventory, flat


def meaning(mid="unit", kind="unit", text="Lengths use mm", **extra):
    return {
        "id": mid,
        "kind": kind,
        "description": "Scripted source interpretation",
        "sourceQuotes": [{"sourceRef": "a", "text": text}],
        "scope": {"kind": "columns", "columnIds": ["length"]},
        "status": "interpreted",
        **extra,
    }


def test_schema_requires_each_owned_source_and_branches_before_meaning(sample):
    doc, region, frozen, inventory, flat = sample
    schema = meaning_decision_schema(doc, region, frozen)
    Draft202012Validator.check_schema(schema)
    wire = source_decisions_from_flat(flat, inventory)
    Draft202012Validator(schema).validate(wire)
    assert schema["properties"]["sourceDecisions"]["required"] == region["nodeIds"]
    assert "context" not in schema["properties"]["sourceDecisions"]["properties"]
    assert list(schema["properties"])[:2] == ["regionId", "sourceDecisions"]
    ir = meaning_decision_ir(wire, frozen, inventory)
    assert compile_region(ir, doc, region).data == {"rows": [{"length": "001.2300"}]}
    wire["sourceDecisions"]["a"]["meanings"] = []
    assert list(Draft202012Validator(schema).iter_errors(wire))
    with pytest.raises(CompileError, match="table_source_decision_shape"):
        meaning_decision_ir(wire, frozen, inventory)


@pytest.mark.parametrize("role", ["no_additional_meaning", "unresolved", "unreviewed"])
def test_empty_source_still_requires_an_explicit_review_without_quotes(sample, role):
    doc, region, frozen, inventory, flat = sample
    before = copy.deepcopy((doc, frozen, inventory))
    schema = meaning_decision_schema(doc, region, frozen)
    wire = source_decisions_from_flat(flat, inventory)
    wire["sourceDecisions"]["empty"] = {"decision": role}
    wire["sourceReviews"][-1]["role"] = role
    Draft202012Validator(schema).validate(wire)
    ir = meaning_decision_ir(wire, frozen, inventory)
    assert ir.tableMeaningState.sourceReviews[-1].role == role
    assert compile_region(ir, doc, region).data == {"rows": [{"length": "001.2300"}]}
    assert (doc, frozen, inventory) == before
    del wire["sourceDecisions"]["empty"]
    assert list(Draft202012Validator(schema).iter_errors(wire))


def test_empty_source_cannot_offer_a_meaning_branch_or_fabricated_space(sample):
    doc, region, frozen, inventory, flat = sample
    schema = meaning_decision_schema(doc, region, frozen)
    props = schema["properties"]["sourceDecisions"]["properties"]
    assert props["empty"] == {"$ref": "#/$defs/EmptySourceDecision"}
    assert all(props[ref] == {"$ref": "#/$defs/SourceDecision"} for ref in ["h", "v", "a", "b"])
    wire = source_decisions_from_flat(flat, inventory)
    item = meaning("blank", "note", " ")
    item.pop("sourceQuotes")
    item["sourceQuotes"] = [{"sourceRef": "empty", "text": " "}]
    wire["sourceDecisions"]["empty"] = {"decision": "has_meaning"}
    wire["meanings"] = [item]
    assert list(Draft202012Validator(schema).iter_errors(wire))
    with pytest.raises(CompileError, match="quote_not_in_source"):
        meaning_decision_ir(wire, frozen, inventory)
    # Only literal empty text changes the branch, never a semantic or whitespace heuristic.
    doc.nodes["empty"]["text"] = " "
    spaced = meaning_decision_schema(doc, region, frozen)
    assert spaced["properties"]["sourceDecisions"]["properties"]["empty"] == {
        "$ref": "#/$defs/SourceDecision"
    }


@pytest.mark.parametrize("mutation", ["missing", "extra", "context"])
def test_inventory_and_old_flat_response_cannot_bypass_source_decisions(sample, mutation):
    _, _, frozen, inventory, flat = sample
    wire = source_decisions_from_flat(flat, inventory)
    if mutation == "missing":
        wire["sourceDecisions"].pop("v")
    else:
        wire["sourceDecisions"][mutation] = wire["sourceDecisions"]["v"]
    with pytest.raises(CompileError, match="table_source_decision_inventory"):
        meaning_decision_ir(wire, frozen, inventory)
    with pytest.raises(CompileError, match="table_source_decisions_required"):
        meaning_decision_ir(flat, frozen, inventory)
    with pytest.raises(ValueError, match="duplicate JSON key"):
        decode('{"sourceDecisions":{"a":{},"a":{}}}')


def test_joint_and_multiple_meanings_keep_exact_noncontiguous_quotes_and_scope(sample):
    doc, region, frozen, inventory, flat = sample
    flat["meanings"] = [
        meaning(
            sourceQuotes=[
                {"sourceRef": "a", "text": "Lengths use"},
                {"sourceRef": "a", "text": "mm"},
                {"sourceRef": "b", "text": "Applies only to Length."},
            ]
        ),
        meaning("condition", "condition", "warm tests require review."),
    ]
    before = copy.deepcopy((doc, frozen, inventory, flat))
    wire = source_decisions_from_flat(flat, inventory)
    assert len(wire["meanings"]) == 2
    assert len(wire["meanings"][0]["sourceQuotes"]) == 3
    assert [q["sourceRef"] for q in wire["meanings"][0]["sourceQuotes"]] == ["a", "a", "b"]
    assert wire["sourceDecisions"]["b"]["decision"] == "has_meaning"
    Draft202012Validator(meaning_decision_schema(doc, region, frozen)).validate(wire)
    ir = meaning_decision_ir(wire, frozen, inventory)
    assert [m.id for m in ir.meanings] == ["unit", "condition"]
    assert [r.text for r in ir.meanings[0].sourceRanges] == [
        "Lengths use",
        "mm",
        "Applies only to Length.",
    ]
    assert [m.fieldIds for m in ir.meanings] == [["length"], ["length"]]
    assert compile_region(ir, doc, region).data == {"rows": [{"length": "001.2300"}]}
    feedback = meaning_decision_response(ir, inventory)
    roundtrip = meaning_decision_ir(
        {**feedback, "baseRevision": None, "changes": []}, frozen, inventory
    )
    assert roundtrip.meanings == ir.meanings
    assert roundtrip.tableMeaningState.sourceReviews == ir.tableMeaningState.sourceReviews
    assert (doc, frozen, inventory, flat) == before


@pytest.mark.parametrize(
    "bad",
    ["owner", "context", "missing_text", "duplicate_quote", "duplicate_meaning", "duplicate_id"],
)
def test_bad_evidence_anchor_and_duplicates_are_rejected(sample, bad):
    _, _, frozen, inventory, flat = sample
    flat["meanings"] = [meaning()]
    wire = source_decisions_from_flat(flat, inventory)
    item = wire["meanings"][0]
    if bad in {"owner", "context"}:
        item["sourceQuotes"] = [
            {"sourceRef": "h" if bad == "owner" else "context", "text": "Length"}
        ]
    elif bad == "missing_text":
        item["sourceQuotes"][0]["text"] = "Text not in source"
    elif bad == "duplicate_quote":
        item["sourceQuotes"].append(copy.deepcopy(item["sourceQuotes"][0]))
    else:
        other = copy.deepcopy(item)
        if bad == "duplicate_meaning":
            other["id"] = "different-id"
        else:
            other["kind"] = "note"
        wire["meanings"].append(other)
    with pytest.raises(CompileError):
        meaning_decision_ir(wire, frozen, inventory)


def test_same_quote_can_support_distinct_meanings_not_id_only_duplicates(sample):
    _, _, frozen, inventory, flat = sample
    flat["meanings"] = [meaning(), meaning("second", "condition")]
    result = meaning_decision_ir(source_decisions_from_flat(flat, inventory), frozen, inventory)
    assert len(result.meanings) == 2


@pytest.mark.parametrize("ref", ["a", "b", "empty"])
def test_explicit_unreviewed_remains_pending_even_if_fully_quoted_or_empty(sample, ref):
    doc, region, frozen, inventory, flat = sample
    flat["meanings"] = [
        meaning(
            sourceQuotes=[
                {"sourceRef": "a", "text": doc.nodes["a"]["text"]},
                {"sourceRef": "b", "text": doc.nodes["b"]["text"]},
            ]
        )
    ]
    for review in flat["sourceReviews"]:
        if ref in review["sourceRefs"]:
            review.update(
                role="unreviewed", explanation="Explicitly deferred by the scripted model"
            )
    wire = source_decisions_from_flat(flat, inventory)
    ir = meaning_decision_ir(wire, frozen, inventory)
    compiled = compile_region(ir, doc, region)
    assert ref in compiled.meaning_review["unreviewed"]
    assert any(i["code"] == "table_meaning_source_unreviewed" for i in compiled.issues)
    saved = next(r for r in ir.tableMeaningState.sourceReviews if ref in r.sourceRefs)
    assert (
        saved.role == "unreviewed"
        and saved.explanation == flat["sourceReviews"][region["nodeIds"].index(ref)]["explanation"]
    )
    assert ir.tableMeaningState.version == "document-files.table-meaning-review.v2"
    feedback = meaning_decision_response(ir, inventory)
    roundtrip = meaning_decision_ir(
        {**feedback, "baseRevision": None, "changes": []}, frozen, inventory
    )
    assert roundtrip.tableMeaningState.sourceReviews == ir.tableMeaningState.sourceReviews
    assert roundtrip.meanings == ir.meanings


def test_source_decision_repair_preserves_explicit_split_merge_and_withdrawal_history(sample):
    doc, region, frozen, inventory, flat = sample
    flat["meanings"] = [meaning(text="Lengths use mm; warm tests require review.")]
    original = meaning_decision_ir(source_decisions_from_flat(flat, inventory), frozen, inventory)
    changed = copy.deepcopy(flat)
    changed["baseRevision"] = original.tableMeaningState.revisionSHA256
    changed["meanings"] = [meaning("u"), meaning("c", "condition", "warm tests require review.")]
    changed["changes"] = [
        {
            "previousIds": ["unit"],
            "replacementIds": ["u", "c"],
            "reviewSourceRefs": [],
            "reason": "Split independent clauses",
        }
    ]
    split = meaning_decision_ir(source_decisions_from_flat(changed, inventory), original, inventory)
    assert split.tableMeaningState.changes[0].replacementIds == ["u", "c"]
    changed["baseRevision"] = split.tableMeaningState.revisionSHA256
    changed["meanings"] = [meaning("merged", text="Lengths use mm; warm tests require review.")]
    changed["changes"] = [
        {
            "previousIds": ["u", "c"],
            "replacementIds": ["merged"],
            "reviewSourceRefs": [],
            "reason": "Scripted merge correction",
        }
    ]
    merged = meaning_decision_ir(source_decisions_from_flat(changed, inventory), split, inventory)
    changed["baseRevision"] = merged.tableMeaningState.revisionSHA256
    changed["meanings"] = []
    changed["changes"] = [
        {
            "previousIds": ["merged"],
            "replacementIds": [],
            "reviewSourceRefs": ["a"],
            "reason": "Scripted withdrawal after review",
        }
    ]
    withdrawn = meaning_decision_ir(
        source_decisions_from_flat(changed, inventory), merged, inventory
    )
    assert not withdrawn.meanings and len(withdrawn.tableMeaningState.changes) == 1
    assert compile_region(withdrawn, doc, region).data == compile_region(original, doc, region).data
    changed["baseRevision"] = original.tableMeaningState.revisionSHA256
    with pytest.raises(CompileError, match="stale_base_revision"):
        meaning_decision_ir(source_decisions_from_flat(changed, inventory), merged, inventory)


@pytest.mark.parametrize("ref", ["a", "b", "empty"])
@pytest.mark.parametrize("correct_meaning", [False, True])
def test_reviewed_source_cannot_be_deferred_during_repair_or_history_restore(
    sample, ref, correct_meaning
):
    doc, region, frozen, inventory, flat = sample
    flat["meanings"] = [
        meaning(
            sourceQuotes=[
                {"sourceRef": "a", "text": doc.nodes["a"]["text"]},
                {"sourceRef": "b", "text": doc.nodes["b"]["text"]},
            ]
        )
    ]
    old = meaning_decision_ir(source_decisions_from_flat(flat, inventory), frozen, inventory)
    before = compile_region(old, doc, region)
    changed = copy.deepcopy(flat)
    changed["baseRevision"] = old.tableMeaningState.revisionSHA256
    for review in changed["sourceReviews"]:
        if ref in review["sourceRefs"]:
            review.update(role="unreviewed", explanation="Explicitly deferred in repair")
    if correct_meaning:
        changed["meanings"][0]["description"] = "Corrected interpretation"
        changed["changes"] = [
            {
                "previousIds": ["unit"],
                "replacementIds": ["unit"],
                "reviewSourceRefs": [],
                "reason": "Corrected the description after source review",
            }
        ]
    new = meaning_decision_ir(source_decisions_from_flat(changed, inventory), old, inventory)
    after = compile_region(new, doc, region)
    assert not before.meaning_review["unreviewed"]
    assert after.meaning_review["unreviewed"] == [ref]
    assert not any(r["role"] == "unreviewed" for r in after.meaning_review["ranges"])
    assert after.data == before.data
    with pytest.raises(CompileError, match="table_meaning_review_coverage_regressed"):
        engine._meaning_repair_improves(old, before, new, after)
    # A self-consistent new hash must not make this transition resumable.
    progress = {
        "acceptedResponse": True,
        "usage": {"modelCalls": 2},
        "revisions": [meaning_snapshot(old), meaning_snapshot(new)],
    }
    with pytest.raises(MeaningRevisionError, match="table_meaning_review_coverage_regressed"):
        engine._validate_meaning_history(progress, new, doc, region, None)


def test_all_choices_precede_details_without_losing_grouped_source_reviews(sample):
    doc, region, frozen, inventory, flat = sample
    schema = meaning_decision_schema(doc, region, frozen)
    assert list(schema["properties"])[:4] == [
        "regionId",
        "sourceDecisions",
        "meanings",
        "sourceReviews",
    ]
    wire = source_decisions_from_flat(flat, inventory)
    assert all(set(item) == {"decision"} for item in wire["sourceDecisions"].values())
    wire["sourceReviews"] = [
        {
            "sourceRefs": region["nodeIds"],
            "role": "no_additional_meaning",
            "explanation": "Scripted shared review",
        }
    ]
    before = copy.deepcopy(wire)
    Draft202012Validator(schema).validate(wire)
    result = meaning_decision_ir(wire, frozen, inventory)
    assert not result.meanings
    assert [r.sourceRefs for r in result.tableMeaningState.sourceReviews] == [
        [ref] for ref in region["nodeIds"]
    ]
    assert wire == before


def test_encoding_cannot_overwrite_derived_choices_with_an_existing_wire(sample):
    _, _, _, inventory, flat = sample
    wire = source_decisions_from_flat(flat, inventory)
    with pytest.raises(CompileError, match="already_encoded"):
        source_decisions_from_flat(wire, inventory)


@pytest.mark.parametrize(
    "mutation",
    [
        "unquoted_choice",
        "quoted_negative",
        "review_mismatch",
        "review_missing",
        "review_duplicate",
        "nested_old_shape",
    ],
)
def test_choice_quote_and_review_consistency_is_not_optional(sample, mutation):
    doc, region, frozen, inventory, flat = sample
    flat["meanings"] = [meaning()]
    wire = source_decisions_from_flat(flat, inventory)
    if mutation == "unquoted_choice":
        wire["sourceDecisions"]["v"]["decision"] = "has_meaning"
    elif mutation == "quoted_negative":
        wire["sourceDecisions"]["a"]["decision"] = "no_additional_meaning"
    elif mutation == "review_mismatch":
        wire["sourceDecisions"]["v"]["decision"] = "unresolved"
    elif mutation == "review_missing":
        wire["sourceReviews"].pop()
    elif mutation == "review_duplicate":
        wire["sourceReviews"].append(copy.deepcopy(wire["sourceReviews"][0]))
    else:
        wire["sourceDecisions"]["a"]["meanings"] = wire.pop("meanings")
        wire.pop("sourceReviews")
    before = copy.deepcopy(frozen)
    with pytest.raises(CompileError):
        meaning_decision_ir(wire, frozen, inventory)
    assert frozen == before


def test_transcribed_header_can_still_supply_an_explicit_unit(sample):
    doc, region, frozen, _, flat = sample
    doc.nodes["h"]["text"] = "Length (mm)"
    inventory = source_inventory(doc, region)
    flat["meanings"] = [meaning(sourceQuotes=[{"sourceRef": "h", "text": "mm"}])]
    wire = source_decisions_from_flat(flat, inventory)
    assert wire["sourceDecisions"]["h"]["decision"] == "has_meaning"
    Draft202012Validator(meaning_decision_schema(doc, region, frozen)).validate(wire)
    result = meaning_decision_ir(wire, frozen, inventory)
    assert result.meanings[0].kind == "unit"
    assert result.meanings[0].sourceRanges[0].text == "mm"
    assert result.meanings[0].fieldIds == ["length"]
    assert compile_region(result, doc, region).data == compile_region(frozen, doc, region).data
