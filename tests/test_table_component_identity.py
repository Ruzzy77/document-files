"""Mechanical identity is distinct from model-chosen names and source citations."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_table_layout import Model, mapping, run

from document_files.interpretation import table_identity
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.semantic_types import RegionInterpretation
from document_files.interpretation.table_read_domains import encode_columns
from document_files.interpretation.table_source_wire import expand_table_sources


def proposal(keys):
    return {
        "regionId": "r",
        "record": {
            "id": "source:shared",
            "key": "records",
            "label": "Records",
            "tableRef": "t",
            "rowStart": 0,
            "rowEnd": 1,
            "definitionRefs": ["source:shared"],
            "columns": [
                {
                    "id": "source:shared",
                    "key": key,
                    "column": index,
                    "label": f"Field {index}",
                    "definitionRefs": ["source:shared"],
                    "valueType": "string",
                }
                for index, key in enumerate(keys)
            ],
        },
    }


@pytest.mark.parametrize(
    "keys",
    [
        ["a", "b"],
        ["a", "a"],
        ["a", "a", "a__column_0"],
        ["x" * 120, "x" * 120],
        ["a/b~c", "a/b~c"],
        ["동일한 열", "동일한 열"],
    ],
)
def test_distinct_components_and_keys_preserve_every_field_and_source(keys):
    original = proposal(keys)
    before = copy.deepcopy(original)
    result, receipt = table_identity.assign(original)
    record = result["record"]
    assert record["id"] == "record"
    assert [c["id"] for c in record["columns"]] == [f"column_{i}" for i in range(len(keys))]
    assert len(set(c["key"] for c in record["columns"])) == len(keys)
    for index, column in enumerate(record["columns"]):
        assert 1 <= len(column["key"]) <= 120
        assert (
            column | {"id": "source:shared", "key": keys[index]}
            == before["record"]["columns"][index]
        )
        if keys.count(keys[index]) == 1:
            assert column["key"] == keys[index]
    assert original == before
    # Input dictionary/list order cannot change allocation of duplicate names.
    shuffled = copy.deepcopy(original)
    shuffled["record"]["columns"].reverse()
    reordered, _ = table_identity.assign(shuffled)
    assert sorted(reordered["record"]["columns"], key=lambda c: c["column"]) == record["columns"]
    frozen = RegionInterpretation(regionId="r", repeats=[record])
    table_identity.validate(receipt, frozen)
    assert table_identity.assign(original)[1] == receipt


@pytest.mark.parametrize("mutation", ["id", "key", "proposal", "missing", "version"])
def test_receipt_and_accepted_keys_are_rechecked_on_resume(mutation):
    value, receipt = table_identity.assign(proposal(["a", "a"]))
    frozen = RegionInterpretation(regionId="r", repeats=[value["record"]])
    if mutation == "id":
        frozen.repeats[0].columns[0].id = "source:shared"
    elif mutation == "key":
        frozen.repeats[0].columns[0].key = "new accepted name"
    elif mutation == "proposal":
        receipt["columns"]["0"]["proposedKey"] = "changed"
    elif mutation == "missing":
        receipt.pop("columns")
    else:
        receipt["version"] = "future"
    with pytest.raises(ValueError, match="invalid_table_component_identity"):
        table_identity.validate(receipt, frozen)


def test_coordinate_collisions_and_invalid_proposal_names_are_not_fixed():
    duplicate = proposal(["a", "a"])
    duplicate["record"]["columns"][1]["column"] = 0
    with pytest.raises(CompileError, match="coordinate"):
        table_identity.assign(duplicate)
    invalid = proposal(["a", "a"])
    invalid["record"]["columns"][1]["id"] = []
    with pytest.raises(CompileError, match="name_invalid"):
        table_identity.assign(invalid)


class IdentityModel(Model):
    def infer(self, request):
        payload = expand_table_sources(json.loads(request.messages[-1]["content"]))
        if payload.get("tableStage") != "structure":
            return super().infer(request)
        self.requests.append(request)
        value = mapping(payload)
        # No internal ID is needed. Keep all headers and explicit read choices.
        value["record"].pop("id")
        for column in value["record"]["columns"].values():
            column.pop("id")
            column["key"] = "shared"
        value = encode_columns(value)
        Draft202012Validator(request.output_schema).validate(value)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def test_actual_engine_accepts_an_id_free_mapping_without_duplicate_name_repair():
    model, states = IdentityModel(), []
    result = run(model, states=states)
    assert len(model.requests) == 3  # layout, one mapping, one negative selection
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {
        "records": [
            {"shared__column_0": "0007", "shared__column_1": "1.2300"},
            {"shared__column_0": "0008", "shared__column_1": "0.00"},
        ]
    }
    state = next(iter(states[-1]["tableStages"].values()))
    assert state["structure"]["identityAssignment"]["version"] == table_identity.VERSION
    assert run(model, restore=states[-1])["data"] == result["data"]
    assert len(model.requests) == 3
    forged = copy.deepcopy(states[-1])
    next(iter(forged["tableStages"].values()))["structure"]["identityAssignment"]["version"] = (
        "future"
    )
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(model, restore=forged)
    assert len(model.requests) == 3
