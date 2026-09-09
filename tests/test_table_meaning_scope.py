"""Exclusive scope language/conversion tests; no inference or qualification."""

import copy

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.semantic_types import Meaning, RegionInterpretation
from document_files.interpretation.table_meaning import (
    LEGACY_SCOPE_FIELDS,
    meaning_from_wire,
    meaning_to_wire,
    meaning_wire_schema,
)


def frozen():
    return RegionInterpretation.model_validate(
        {
            "regionId": "r",
            "repeats": [
                {
                    "id": "records",
                    "key": "records",
                    "label": "Measurements",
                    "tableRef": "table",
                    "rowStart": 2,
                    "rowEnd": 3,
                    "definitionRefs": ["header"],
                    "columns": [
                        {
                            "id": name,
                            "key": name,
                            "label": name,
                            "column": col,
                            "valueType": "decimal",
                            "definitionRefs": ["header"],
                        }
                        for col, name in enumerate(("length", "width"))
                    ],
                }
            ],
        }
    )


def statement(scope):
    return {
        "id": "unit",
        "kind": "unit",
        "description": "Measurements use mm",
        "sourceRefs": ["note"],
        "status": "interpreted",
        "scope": scope,
    }


@pytest.mark.parametrize(
    "scope,fields,repeats,bounds",
    [
        (
            {"kind": "columns", "columnIds": ["length", "width"]},
            ["length", "width"],
            [],
            (None, None),
        ),
        ({"kind": "record"}, [], ["records"], (None, None)),
        (
            {"kind": "rows", "rowStart": 2, "rowEnd": 2, "columnIds": ["width"]},
            ["width"],
            ["records"],
            (2, 2),
        ),
        ({"kind": "rows", "rowStart": 2, "rowEnd": 3, "columnIds": []}, [], ["records"], (2, 3)),
        ({"kind": "unresolved"}, [], [], (None, None)),
    ],
)
def test_explicit_choice_roundtrips_without_inferring_applicability(scope, fields, repeats, bounds):
    ir = frozen()
    original = statement(scope)
    meaning = meaning_from_wire(original, ir)
    assert meaning.fieldIds == fields and meaning.repeatIds == repeats
    assert (meaning.rowStart, meaning.rowEnd) == bounds
    assert meaning.description == original["description"] and meaning.sourceRefs == ["note"]
    assert meaning.kind == "unit" and meaning.id == "unit"
    expected = original | {
        "status": "uncertain" if scope["kind"] == "unresolved" else "interpreted"
    }
    assert meaning_to_wire(meaning, ir) == expected
    assert original == statement(scope)


@pytest.mark.parametrize(
    "scope",
    [
        {"kind": "columns", "columnIds": ["length"], "repeatIds": ["records"]},
        {"kind": "record", "columnIds": ["length"]},
        {"kind": "columns", "columnIds": []},
        {"kind": "columns", "columnIds": ["unknown"]},
        {"kind": "columns", "columnIds": ["length", "length"]},
        {"kind": "columns", "columnIds": ["length"], "rowStart": 2, "rowEnd": 3},
        {"kind": "rows", "rowStart": 2, "columnIds": []},
        {"kind": "rows", "rowStart": 3, "rowEnd": 2, "columnIds": []},
        {"kind": "rows", "rowStart": 1, "rowEnd": 2, "columnIds": []},
        {"kind": "rows", "rowStart": 2, "rowEnd": 4, "columnIds": []},
        {"kind": "rows", "rowStart": True, "rowEnd": 3, "columnIds": []},
        {"kind": "unresolved", "repeatIds": ["records"]},
    ],
)
def test_unknown_duplicate_mixed_and_out_of_range_scope_cannot_compile(scope):
    with pytest.raises((CompileError, ValidationError)):
        meaning_from_wire(statement(scope), frozen())


def test_contract_keeps_original_meaning_metadata_and_bounded_source_choices():
    base = Meaning.model_json_schema()
    base["properties"]["sourceRefs"]["items"] = {"type": "string", "enum": ["note"]}
    before = copy.deepcopy(base)
    schema = meaning_wire_schema(base, frozen())
    Draft202012Validator.check_schema(schema)
    assert base == before
    assert LEGACY_SCOPE_FIELDS.isdisjoint(schema["properties"])
    for name in ("id", "kind", "description", "sourceRefs", "status"):
        assert schema["properties"][name] == base["properties"][name]
    validator = Draft202012Validator(schema)
    validator.validate(statement({"kind": "columns", "columnIds": ["length", "width"]}))
    assert not validator.is_valid(statement({"kind": "record", "columnIds": ["length"]}))
    assert not validator.is_valid(
        statement({"kind": "rows", "rowStart": 1, "rowEnd": 3, "columnIds": []})
    )
    assert not validator.is_valid(statement({"kind": "columns", "columnIds": ["length", "length"]}))
    assert not validator.is_valid(statement({"kind": "record"}) | {"sourceRefs": ["invented"]})
    for key in LEGACY_SCOPE_FIELDS:
        with pytest.raises(CompileError):
            meaning_from_wire(statement({"kind": "record"}) | {key: []}, frozen())


def test_legacy_parent_child_union_is_rejected_in_stateless_repair_export():
    ir = frozen()
    old = Meaning.model_validate(
        {
            "id": "m",
            "kind": "note",
            "description": "Not narrowed",
            "sourceRefs": ["note"],
            "fieldIds": ["length"],
            "repeatIds": ["records"],
        }
    )
    with pytest.raises(CompileError, match="parent_child_union"):
        meaning_to_wire(old, ir)


def test_bounded_row_and_column_intersection_still_compiles_to_exact_values():
    ir = frozen()
    nodes = {"header": {"text": "Measurements"}, "note": {"text": "Second column row two only"}}
    cells, bindings = [], {}
    for row in (2, 3):
        for col in (0, 1):
            ref = f"cell-{row}-{col}"
            nodes[ref] = {"text": f"{row}.{col}0"}
            cells.append({"row": row, "col": col, "sourceRef": ref})
            bindings[ref] = {"sourceRef": ref, "path": "/text", "candidateRole": "cell"}
    observation = ObservationDocument(
        nodes=nodes,
        bindings=bindings,
        tables={"table": {"cells": cells, "basis": "native_structure"}},
    )
    region = {"id": "r", "nodeIds": list(nodes), "bindingIds": list(bindings), "tableRef": "table"}
    before = compile_region(ir, observation, region)
    ir.meanings = [
        meaning_from_wire(
            statement({"kind": "rows", "rowStart": 2, "rowEnd": 2, "columnIds": ["width"]}), ir
        )
    ]
    compiled = compile_region(ir, observation, region)
    assert compiled.data == before.data
    assert compiled.consumed_bindings == before.consumed_bindings
    assert compiled.semantic_details[0]["scope"] == [{"space": "data", "path": "/records/0/width"}]
