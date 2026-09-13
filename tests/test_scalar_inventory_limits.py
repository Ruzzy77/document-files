"""Grammar bounds derived from existing compiler uniqueness, not model-quality tests."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.semantic_types import RegionInterpretation, region_output_schema


def fixture():
    doc = ObservationDocument(nodes={"owned": {"text": "value"}, "context": {"text": "label"}})
    bid = doc.bind("owned", start=0, end=5)
    region = {
        "id": "scalar",
        "nodeIds": ["owned"],
        "contextNodeIds": ["context", "owned"],
        "bindingIds": [bid],
    }
    return doc, region, bid


def test_candidate_cardinality_includes_context_without_duplicate_source_allowance():
    doc, region, _ = fixture()
    original = deepcopy((doc, region))
    schema = region_output_schema(doc, region)
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["dispositions"]["maxItems"] == 2
    assert schema["properties"]["excludedBindings"]["maxItems"] == 1
    assert (doc, region) == original
    public = RegionInterpretation.model_json_schema()
    for name in ("fields", "groups", "meanings", "unresolved"):
        assert schema["properties"][name]["maxItems"] == public["properties"][name]["maxItems"]
    assert public["properties"]["dispositions"]["maxItems"] == 5000
    assert public["properties"]["excludedBindings"]["maxItems"] == 2000


@pytest.mark.parametrize("disposition_count,excluded", [(0, False), (1, False), (2, True)])
def test_every_unique_source_and_binding_decision_still_compiles(disposition_count, excluded):
    doc, region, bid = fixture()
    value = {
        "regionId": "scalar",
        "dispositions": [
            {"sourceRef": ref, "role": "structural", "explanation": "Fixture review"}
            for ref in ["owned", "context"][:disposition_count]
        ],
        "excludedBindings": [
            {"bindingId": bid, "role": "structural", "explanation": "Fixture review"}
        ]
        if excluded
        else [],
    }
    Draft202012Validator(region_output_schema(doc, region)).validate(value)
    compiled = compile_region(RegionInterpretation.model_validate(value), doc, region)
    assert not compiled.data  # Compiler diagnostics are allowed; no invented values.


@pytest.mark.parametrize("kind", ["dispositions", "excludedBindings"])
def test_over_inventory_duplicates_were_already_rejected_by_compiler(kind):
    doc, region, bid = fixture()
    item = (
        {"sourceRef": "owned", "role": "structural", "explanation": "Fixture review"}
        if kind == "dispositions"
        else {"bindingId": bid, "role": "structural", "explanation": "Fixture review"}
    )
    value = {"regionId": "scalar", kind: [deepcopy(item) for _ in range(3)]}
    assert not Draft202012Validator(region_output_schema(doc, region)).is_valid(value)
    with pytest.raises(CompileError, match="invalid_node_dispositions|invalid_excluded_binding"):
        compile_region(RegionInterpretation.model_validate(value), doc, region)


def test_no_binding_candidates_removes_only_unreachable_exclusions():
    doc, region, _ = fixture()
    region["bindingIds"] = []
    schema = region_output_schema(doc, region)
    assert schema["properties"]["excludedBindings"]["maxItems"] == 0
    assert "BindingDisposition" not in schema["$defs"]
    Draft202012Validator(schema).validate({"regionId": "scalar", "excludedBindings": []})


def test_large_inventories_keep_existing_global_caps():
    doc = ObservationDocument()
    region = {
        "id": "large",
        "nodeIds": [f"n{i}" for i in range(5001)],
        "contextNodeIds": [],
        "bindingIds": [f"b{i}" for i in range(2001)],
    }
    schema = region_output_schema(doc, region, compact=False)
    assert schema["properties"]["dispositions"]["maxItems"] == 5000
    assert schema["properties"]["excludedBindings"]["maxItems"] == 2000
