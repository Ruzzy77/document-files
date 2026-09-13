"""Coordinate wire preservation and real engine boundaries, not model quality."""

import copy
import io
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_table_formula_contract import formula_table
from test_table_protocol import HTML, TableModel, fixture

from document_files.api import (
    AnalysisInput,
    AnalysisJob,
    ExtractionOptions,
    extract_schema_from_stream,
)
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.legacy_engine import decode as decode_json
from document_files.interpretation.table_protocol import structural_ir, structure_model_schema
from document_files.interpretation.table_structure_wire import decode, encode


def test_coordinate_roundtrip_keeps_every_decision_source_value_and_canonical_record():
    doc, region, _, canonical = fixture()
    before = copy.deepcopy((doc, region, canonical))
    wire = encode(canonical)
    schema = structure_model_schema(doc, region)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(wire)
    assert list(wire["record"]["columns"]) == ["0", "1"]
    assert wire["record"]["rowRoles"] == ["data", "data"]
    assert decode(wire, doc, region) == canonical
    _, old = structural_ir(canonical, doc, region)
    _, new = structural_ir(decode(wire, doc, region), doc, region)
    assert new == old
    assert compile_region(new, doc, region).data == {
        "records": [{"code": "0007", "size": "1.2300"}, {"code": "0008", "size": "0.00"}]
    }
    assert (doc, region, canonical) == before


def test_sparse_shifted_and_merged_geometry_keeps_actual_positions_and_subset_choice():
    doc, region, _, canonical = fixture()
    table = doc.tables[region["tableRef"]]
    for c in table["cells"]:
        c["row"] += 4 if c["row"] < 2 else 7
        c["col"] += 3
    table["cells"][0]["colSpan"] = 2
    r = canonical["record"]
    r.update(
        rowStart=4, rowEnd=9, rowRoles=[{"row": 5, "role": "data"}, {"row": 9, "role": "note"}]
    )
    r["columns"] = [{**r["columns"][0], "column": 3}]
    wire = encode(canonical)
    schema = structure_model_schema(doc, region)
    Draft202012Validator(schema).validate(wire)
    assert decode(wire, doc, region) == canonical
    # Gap rows are not offered. A native-declared header stays program-derived.
    assert schema["$defs"]["StructureRecord"]["properties"]["rowRoles"]["minItems"] == 2
    # Preserve the prior bounded grid range, including implicit column gaps.
    assert set(schema["$defs"]["StructureRecord"]["properties"]["columns"]["properties"]) == set(
        "01234"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "legacy",
        "missing_row",
        "foreign_row",
        "fixed_row",
        "row_object",
        "extra_coordinate",
        "empty_columns",
        "foreign_column",
        "alias",
        "leading_zero",
    ],
)
def test_closed_shape_rejects_legacy_duplicate_coordinate_channels_and_missing_rows(damage):
    doc, region, _, canonical = fixture()
    wire = encode(canonical)
    r = wire["record"]
    if damage == "legacy":
        wire = canonical
    elif damage == "missing_row":
        r["rowRoles"].pop()
    elif damage in {"foreign_row", "fixed_row"}:
        r["rowRoles"].append("data")
    elif damage == "row_object":
        r["rowRoles"][0] = {"row": 1, "role": "data"}
    elif damage == "extra_coordinate":
        r["columns"]["0"]["column"] = 1
    elif damage == "empty_columns":
        r["columns"] = {}
    else:
        key = {"foreign_column": "2", "alias": "1.0", "leading_zero": "01"}[damage]
        r["columns"][key] = r["columns"].pop("1")
    assert not Draft202012Validator(structure_model_schema(doc, region)).is_valid(wire)
    with pytest.raises(CompileError, match="coordinate_wire"):
        decode(wire, doc, region)


@pytest.mark.parametrize("kind", ["scalar_form", "unresolved"])
def test_nonrecord_kinds_keep_null_record_without_synthetic_columns(kind):
    doc, region, _, _ = fixture()
    wire = {"regionId": region["id"], "tableKind": kind, "record": None}
    Draft202012Validator(structure_model_schema(doc, region)).validate(wire)
    assert structural_ir(decode(wire, doc, region), doc, region)[1] is None


@pytest.mark.parametrize("value_type", ["string", "native", "number", "decimal", "boolean"])
def test_formula_type_constraints_survive_coordinate_schema_and_native_cache(value_type):
    doc, region, canonical = formula_table(cache=True)
    canonical["record"]["columns"][1]["valueType"] = value_type
    wire = encode(canonical)
    allowed = value_type in {"string", "native"}
    assert Draft202012Validator(structure_model_schema(doc, region)).is_valid(wire) == allowed
    if not allowed:
        with pytest.raises(CompileError, match="formula_requires_text"):
            structural_ir(decode(wire, doc, region), doc, region)
    else:
        _, decision = structural_ir(decode(wire, doc, region), doc, region)
        result = compile_region(decision, doc, region)
        assert result.data["records"][0]["expression"] == "=A2+1"
        assert "2.0000" not in str(result.data)


def test_predicted_header_is_not_fixed_and_declared_header_only_table_needs_no_role_guess():
    doc, region, _, canonical = fixture()
    doc.tables[region["tableRef"]]["basis"] = "recognition_prediction"
    canonical["record"]["rowRoles"].insert(0, {"row": 0, "role": "unresolved"})
    wire = encode(canonical)
    Draft202012Validator(structure_model_schema(doc, region)).validate(wire)
    assert decode(wire, doc, region)["record"]["rowRoles"][0]["role"] == "unresolved"
    doc, region, _, canonical = fixture()
    table = doc.tables[region["tableRef"]]
    table["cells"] = [c for c in table["cells"] if c["row"] == 0]
    canonical["record"].update(rowEnd=0, rowRoles=[])
    wire = encode(canonical)
    assert wire["record"]["rowRoles"] == []
    Draft202012Validator(structure_model_schema(doc, region)).validate(wire)
    assert decode(wire, doc, region) == canonical


@pytest.mark.parametrize("part", ["columns", "rowRoles"])
def test_fixture_inverse_never_discards_duplicate_positions(part):
    _, _, _, canonical = fixture()
    canonical["record"][part].append(copy.deepcopy(canonical["record"][part][0]))
    with pytest.raises(ValueError, match="duplicate"):
        encode(canonical)


class WireModel(TableModel):
    def __init__(self, damage=None):
        super().__init__()
        self.damage = damage

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if payload["tableStage"] == "layout":
            from test_table_protocol import layout_fixture

            from document_files.interpretation.backends import InferenceResponse

            self.requests.append(request)
            return InferenceResponse(json.dumps(layout_fixture(payload)), {})
        response = super().infer(request)
        if payload["tableStage"] != "structure":
            return response
        wire = encode(json.loads(response.text))
        wire["record"].pop("rowRoles")
        Draft202012Validator(request.output_schema).validate(wire)
        text = json.dumps(wire)
        if self.damage == "legacy":
            return response
        if self.damage == "duplicate_key":
            text = text.replace('"columns": {', '"columns": {"0":{},', 1)
        return replace(response, text=text)


def run(model, *, states=None, restore=None, additional_budget=None, max_calls=4):
    # Intentionally no canonical fixture wrapper: the public path must decode
    # only the current model wire, preserve accepted state and reject raw damage.
    return extract_schema_from_stream(
        AnalysisJob(
            job_id="coordinate-wire", input=AnalysisInput.from_bytes(HTML, format_id="html")
        ),
        io.BytesIO(HTML),
        model_client=model,
        options=ExtractionOptions(maxModelCalls=max_calls, reconstructionContext=False),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        additional_budget=additional_budget,
    )


@pytest.mark.parametrize("damage", ["legacy", "duplicate_key"])
def test_unconstrained_backend_cannot_bypass_wire_and_never_publishes_failed_records(damage):
    model, states = WireModel(damage), []
    result = run(model, states=states)
    assert result["data"] is None and result["extraction"]["status"] == "partial"
    assert result["extraction"]["modelCalls"] == len(model.requests) == 3
    assert states[-1]["tableStages"]["semantic-region:1"]["layout"]["attempts"] == 1
    assert states[-1]["accepted"] == {}
    assert states[-1]["tableStages"]["semantic-region:1"]["meaning"]["attempts"] == 0
    if damage == "duplicate_key":
        with pytest.raises(ValueError, match="duplicate JSON key"):
            decode_json('{"1":"data","1":"note"}')


def test_partial_and_completed_checkpoint_keep_canonical_structure_without_replaying_rows():
    model, states = WireModel(), []
    partial = run(model, states=states, max_calls=2)
    expected = partial["data"]
    assert len(expected["records"]) == 2
    assert partial["extraction"]["status"] == "partial"
    record = states[-1]["accepted"]["semantic-region:1"]["repeats"][0]
    assert isinstance(record["columns"], list) and isinstance(record["rowRoles"], list)
    completed = run(
        model,
        states=states,
        restore=states[-1],
        max_calls=2,
        additional_budget={"maxModelCalls": 1},
    )
    assert completed["data"] == expected and completed["extraction"]["status"] == "complete"
    assert len(model.requests) == 3
    assert run(model, restore=states[-1], max_calls=2)["data"] == expected
    assert len(model.requests) == 3
