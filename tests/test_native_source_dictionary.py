"""Exact metadata sharing and whole-context sizing, not model quality approval."""

import copy
import json

import pytest
from test_document_protocol import StagedModel, execute
from test_hwpx_formatting import package
from test_native_structure import prepared

from document_files.interpretation import document_protocol as roles
from document_files.interpretation import native_structure as native
from document_files.interpretation import native_value_batches as batches
from document_files.interpretation.legacy_engine import contract_messages
from document_files.interpretation.regions import model_node, region_payload
from document_files.interpretation.source_dictionary import (
    SOURCE_SYSTEM,
    compact_sources,
    factor_reads,
    merge,
    source_nodes,
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize(
    "values",
    [
        [{"x": True}, {"x": 1}, {"x": 1.0}],
        [{"x": {"a": [True]}}, {"x": {"a": [1]}}, {"x": {"a": [1.0]}}],
        [{"x": None}, {}, {"x": []}, {"x": {}}],
        [{"x": {"shared": "한글🙂", "one": 1}}, {"x": {"shared": "한글🙂", "two": 2}}],
        [{"x": ["equal", "array"]}, {"x": ["equal", "array"]}],
        [],
    ],
)
def test_factor_keeps_exact_json_types_missing_keys_arrays_and_order(values):
    original = {str(i): v for i, v in enumerate(values)}
    before = copy.deepcopy(original)
    factored = factor_reads(original)
    restored = {k: merge(factored["template"], patch) for k, patch in factored["rows"]}
    assert encoded(restored) == encoded(original)
    assert original == before
    if restored:
        restored["0"]["new"] = "not a shared mutation"
        assert original == before


def formatted_sources(doc):
    for index, node in enumerate(doc.nodes.values()):
        node["sourceStructure"] = {
            "formatting": {
                "basis": "source_declared_test",
                "conditional_properties_unresolved": True,
                "shared": {str(k): "unaltered reference " + str(k) for k in range(20)},
                "runs": [{"ref": index, "flags": ["bold"] if index == 0 else []}],
            },
            "index": index,
        }


def test_roles_structure_values_and_each_batch_recover_every_owned_source_property():
    doc, region, decision, structure, _ = prepared()
    formatted_sources(doc)
    before = copy.deepcopy(doc)
    rp, rs = roles.role_request(doc, region)
    sp, ss = native.request(doc, region, decision, {"intent": "discover", "targetHandles": {}})
    vp, vs = native.value_request(structure, decision, doc, region)
    for payload, key in [(rp, "blocks"), (sp, "blocks"), (vp, "nodes")]:
        assert "sourceTemplate" in payload and "text" not in payload["sourceTemplate"]
        recovered = source_nodes(payload, key)
        assert list(recovered) == region["nodeIds"]
        assert all(v["text"] == doc.nodes[ref]["text"] for ref, v in payload[key].items())
        for ref, node in recovered.items():
            assert node["sourceStructure"] == doc.nodes[ref]["sourceStructure"]
            expected = model_node(doc.nodes[ref])
            expected["nativeRole"] = expected.pop("semanticRole", None)
            if key == "blocks":
                expected["textRange"] = {"path": "/text", "start": 0, "end": len(node["text"])}
            assert node == expected
    assert source_nodes(vp, "nodes") == region_payload(doc, region)["nodes"]
    for system, payload, schema in [
        (roles.ROLE_SYSTEM, rp, rs),
        (native.SYSTEM, sp, ss),
        (native.VALUE_SYSTEM, vp, vs),
    ]:
        assert SOURCE_SYSTEM in contract_messages(system, payload, schema)[0]["content"]
    for kind, keys in [("values", list(vp["handles"])[:1]), ("accounting", [])]:
        system, payload, schema = batches.request_for(
            vp, vs, kind, keys, {"values": [], "accounting": []}
        )
        assert source_nodes(payload, "nodes") == source_nodes(vp, "nodes")
        assert payload["bindings"] == vp["bindings"]
        assert SOURCE_SYSTEM in contract_messages(system, payload, schema)[0]["content"]
    assert doc == before


def test_small_or_different_metadata_is_not_replaced_by_a_larger_template_request():
    for nodes in [{}, {"a": {"text": "same"}}, {"a": {"text": "same"}, "b": {"text": "same"}}]:
        payload = {"blocks": nodes}
        assert compact_sources(payload, "blocks") is payload
        assert contract_messages("instruction", payload, {})[0]["content"] == "instruction"


def test_formatted_hwpx_stays_in_one_owned_context_and_checkpoint_rebuilds_it(tmp_path):
    raw = package(
        tmp_path,
        "".join(
            f'<p paraPrIDRef="2" styleIDRef="0"><run charPrIDRef="8"><t>Text {i}</t></run></p>'
            for i in range(8)
        ),
    ).read_bytes()
    model, states = StagedModel(), []
    result = execute(model, raw=raw, contextChars=16000, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(result["coverage"]["regions"]) == 1 and model.calls == 2
    role, structure = model.outline_requests[0], model.structure_requests[0]
    assert len(role["blocks"]) == len(structure["blocks"]) == 8
    assert "sourceTemplate" in role and "sourceTemplate" in structure
    assert [n["text"] for n in structure["blocks"].values()] == [f"Text {i}" for i in range(8)]
    assert len(result["document"]["outline"]["elements"]) == 8
    restored = execute(model, raw=raw, contextChars=16000, restore=states[-1])
    assert model.calls == 2 and restored["document"] == result["document"]
    state = copy.deepcopy(states[-1])
    state["identity"]["nativeStructureVersion"] = "document-files.native-structure.v8"
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, raw=raw, contextChars=16000, restore=state)
    assert model.calls == 2
