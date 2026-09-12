"""Retain distinct fields even with shared reads; fold only exact field aliases."""

import copy

import pytest
from test_regional_interpretation import _heading_document

from document_files.interpretation.compiler import CompileError, compile_region


@pytest.mark.parametrize("reversed_fields", [False, True])
def test_wrong_first_definition_cannot_delete_a_later_correct_field(reversed_fields):
    doc, region, ir, heading = _heading_document(disposition=True)
    correct = ir.fields[0]
    wrong = correct.model_copy(
        update={
            "id": "wrong",
            "key": "title",
            "label": "Document Title",
            "definitionRefs": [heading],
        }
    )
    ir.fields = [correct, wrong] if reversed_fields else [wrong, correct]
    result = compile_region(ir, doc, region)
    assert result.data == {"doc_no": "DC-1", "title": "DC-1"}
    assert not result.dropped_fields
    assert not any(c["code"] == "duplicate_binding_field_dropped" for c in result.corrections)
    assert {
        s["id"]: s["sourceRefs"] for s in result.semantics if s["kind"] == "field_definition"
    } == {"r:doc_no": ["n0"], "r:wrong": [heading]}
    # Source preservation is not semantic correctness: the wrong title remains
    # independently reviewable, rather than erasing the correctly defined field.


@pytest.mark.parametrize("change", ["label", "definition", "type"])
def test_incompatible_definitions_at_same_destination_fail_instead_of_first_wins(change):
    doc, region, ir, heading = _heading_document(disposition=True)
    other = ir.fields[0].model_copy(update={"id": "other"}, deep=True)
    if change == "label":
        other.label = "Different role"
    elif change == "definition":
        other.definitionRefs = [heading]
    else:
        other.valueType = "native"
    ir.fields.append(other)
    with pytest.raises(CompileError):
        compile_region(ir, doc, region)


def test_exact_same_field_fold_preserves_meaning_scope_referencing_alias():
    from document_files.interpretation.semantic_types import Meaning

    doc, region, ir, _ = _heading_document(disposition=True)
    alias = ir.fields[0].model_copy(update={"id": "alias"})
    ir.fields.append(alias)
    ir.meanings = [
        Meaning(
            id="rule",
            kind="note",
            description="A source-linked rule",
            sourceRefs=["h"],
            fieldIds=["alias"],
        )
    ]
    before = copy.deepcopy((doc, region, ir))
    result = compile_region(ir, doc, region)
    assert result.data == {"doc_no": "DC-1"} and result.dropped_fields == {"alias": "n0"}
    assert result.corrections[0]["basis"] == "same_source_definition_and_destination"
    assert result.semantic_details[0]["scope"] == [{"space": "data", "path": "/doc_no"}]
    assert not result.issues and before == (doc, region, ir)


def test_exact_quote_and_native_address_alias_accounts_for_both_bindings():
    from test_native_records import compile_value, fixture

    doc, region, value = fixture()
    ref = "meta"
    start = doc.nodes[ref]["text"].index("0007")
    bid = doc.bind(ref, start=start, end=start + 4, candidateRole="value")
    region["bindingIds"].append(bid)
    region["requiredBindingIds"] = [bid]
    value["fields"].append(
        {**value["fields"][0], "id": "alias", "sourceQuote": None, "bindingId": bid}
    )
    result = compile_value(doc, region, value)
    assert result.dropped_fields == {"alias": ref} and bid in result.consumed_bindings
    assert not result.issues
