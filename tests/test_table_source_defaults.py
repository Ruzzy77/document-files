"""Source-mode formulas read native expressions, never display addresses or caches."""

from copy import deepcopy

import pytest
from test_table_formula_contract import formula_table

from document_files.interpretation.compiler import CompileError, compile_region, preferred_binding
from document_files.interpretation.table_protocol import structural_ir


@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize("value_type", ["string", "native"])
@pytest.mark.parametrize("explicit_mode", [False, True])
def test_source_default_preserves_the_native_formula_and_its_exact_evidence(
    cache, value_type, explicit_mode
):
    doc, region, value = formula_table(cache=cache)
    column = value["record"]["columns"][1]
    column["valueType"] = value_type
    if explicit_mode:
        column["bindingMode"] = "source"
    else:
        column.pop("bindingMode")
    before = deepcopy((doc, value))
    _, candidate = structural_ir(value, doc, region)
    result = compile_region(candidate, doc, region)
    assert result.data == {"records": [{"input": 1, "expression": "=A2+1"}]}
    evidence = next(e for e in result.value_evidence if e["target"]["path"].endswith("/expression"))
    assert evidence["binding"]["path"] == "/semantic/value/formula"
    assert evidence["raw"] == "=A2+1" and evidence["status"] == "present"
    assert (doc, value) == before


@pytest.mark.parametrize("value_type", ["decimal", "integer", "number", "boolean"])
def test_source_default_does_not_evaluate_a_formula_to_satisfy_a_numeric_type(value_type):
    doc, region, value = formula_table(cache=True)
    value["record"]["columns"][1].update(bindingMode="source", valueType=value_type)
    _, candidate = structural_ir(value, doc, region)
    if value_type == "decimal":
        result = compile_region(candidate, doc, region)
        assert result.data["records"][0]["expression"] is None
        assert "decimal_format_unresolved" in {issue["code"] for issue in result.issues}
        evidence = result.value_evidence[-1]
        assert evidence["raw"] == "=A2+1" and evidence["status"] == "uncertain"
        return
    with pytest.raises(CompileError, match="binding_cannot_represent_requested_type"):
        compile_region(candidate, doc, region)


@pytest.mark.parametrize("cache", [False, True])
def test_explicit_formula_cached_and_display_modes_remain_distinct(cache):
    doc, region, value = formula_table(cache=cache)
    column = value["record"]["columns"][1]
    formula_ref = next(
        ref for ref, node in doc.nodes.items()
        if node.get("semantic", {}).get("cell", {}).get("coordinate") == "B2"
    )
    for mode in ["source", "formula", "text", "cached"]:
        column.update(bindingMode=mode, valueType="string")
        _, candidate = structural_ir(value, doc, region)
        result = compile_region(candidate, doc, region)
        if mode in {"source", "formula"}:
            expected = "=A2+1"
        elif mode == "text":
            expected = doc.nodes[formula_ref]["text"]
            assert expected != "=A2+1"  # Explicit display mode still includes the locator.
        else:
            expected = "2.0000" if cache else None
        assert result.data["records"][0]["expression"] == expected


def test_source_preference_keeps_native_raw_values_and_does_not_invent_missing_candidates():
    def binding(path):
        return {"sourceRef": "cell", "path": path, "start": None, "end": None}

    candidates = {
        "formula": binding("/semantic/value/formula"),
        "display": binding("/text") | {"candidateRole": "cell", "start": 0, "end": 20},
        "cached": binding("/semantic/value/cachedValue/raw"),
    }
    assert preferred_binding(candidates, "cell") == "formula"
    assert preferred_binding(candidates, "cell", "cached") == "cached"
    assert preferred_binding(candidates, "cell", "text") == "display"
    candidates["raw"] = binding("/semantic/value/raw")
    assert preferred_binding(candidates, "cell") == "raw"
    assert preferred_binding(candidates, "missing") is None


def test_native_geometry_helper_reads_beyond_the_first_public_unit_page():
    from test_native_merged_geometry import observe
    from test_table_source_wire import native_table

    doc = observe(native_table("hwpx", count=96), "hwpx")
    table = next(iter(doc.tables.values()))
    assert len(table["cells"]) == 97 * 3
    assert max(cell["row"] for cell in table["cells"]) == 96
    assert len(doc.provenance["legacyNodeIds"]) > 500
