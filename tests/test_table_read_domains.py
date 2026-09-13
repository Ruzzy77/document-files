"""Source-checked read domains preserve choices/states; not model quality tests."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator
from test_native_merged_geometry import observe
from test_table_formula_contract import formula_table
from test_table_precision_repair import EXACT, RepairModel, source_file

from document_files.interpretation import table_layout
from document_files.interpretation.compiler import CompileError, compile_region, preferred_binding
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.table_protocol import structural_ir
from document_files.interpretation.table_read_domains import (
    MODES,
    TYPES,
    decode_columns,
    encode_columns,
    permitted_reads,
)
from document_files.interpretation.table_structure_wire import encode


def layout_for(doc, region, roles):
    return table_layout.accept(
        {
            "regionId": region["id"],
            "tableKind": "record_table",
            "rowRoles": roles,
            "baseRevision": None,
        },
        doc,
        region,
    )


def model_mapping(doc, region, current):
    from document_files.interpretation.backends import InferenceRequest
    from document_files.interpretation.legacy_engine import contract_messages

    model = RepairModel()
    return model.infer(
        InferenceRequest(
            messages=contract_messages(
                table_layout.MAPPING_SYSTEM,
                table_layout.mapping_request(region_payload(doc, region), current, doc, region),
                table_layout.mapping_schema(doc, region, layout=current),
            ),
            output_schema={},
            timeout=30,
        )
    )


@pytest.mark.parametrize("format_id", ["hwpx", "xlsx"])
def test_source_precision_pair_schema_and_decoder_preserve_full_canonical_choices(format_id):
    import json

    doc = observe(source_file(format_id), format_id)
    region = prepare_regions(doc, context_chars=16000)[0]
    current = layout_for(doc, region, ["header", "data", "data"])
    original = json.loads(model_mapping(doc, region, current).text)
    before = deepcopy((doc, region, original))
    contract = table_layout.mapping_schema(doc, region, layout=current)
    validator = Draft202012Validator(contract)
    assert not validator.is_valid(encode_columns(original))  # source:number loses digits
    original["record"]["columns"]["1"]["valueType"] = "decimal"
    paired = encode_columns(original)
    validator.validate(paired)
    assert decode_columns(paired) == original
    canonical = table_layout.decode_mapping(paired, current, doc, region)
    _, ir = structural_ir(canonical, doc, region)
    compiled = compile_region(ir, doc, region)
    assert compiled.data["records"] == [
        {"code": "00007", "quantity": EXACT},
        {"code": "00008", "quantity": "4.5000"},
    ]
    assert (doc, region) == before[:2]
    assert set(
        permitted_reads(doc, region, {0: "header", 1: "data", 2: "data"})["1"]["source"]
    ) == {
        "decimal",
        "string",
        "native",
    }
    # The same source remains visible; only the duplicate layout display is omitted.
    from document_files.interpretation.table_protocol import structure_payload
    from document_files.interpretation.table_source_wire import expand_table_sources

    payload = region_payload(doc, region)
    mapped = expand_table_sources(table_layout.mapping_request(payload, current, doc, region))
    reference = expand_table_sources(structure_payload(payload))
    for ref, table in mapped["tables"].items():
        restored = deepcopy(table)
        restored["rowCandidates"] = reference["tables"][ref]["rowCandidates"]
        restored["columnCandidates"] = reference["tables"][ref]["columnCandidates"]
        assert restored == reference["tables"][ref]
    for key in ["nodes", "nodeIds", "contextNodeIds", "relations"]:
        if key in reference:
            assert mapped[key] == reference[key]


@pytest.mark.parametrize("cache", [False, True])
def test_every_offered_pair_agrees_with_actual_compiler_for_formula_and_scalar(cache):
    doc, region, value = formula_table(cache=cache)
    current = layout_for(doc, region, ["header", "data"])
    domains = permitted_reads(doc, region, {0: "header", 1: "data"})
    before = deepcopy(doc)
    for index in [0, 1]:
        for mode in MODES:
            for kind in TYPES:
                trial = deepcopy(value)
                trial["record"]["columns"] = [trial["record"]["columns"][index]]
                trial["record"]["columns"][0].update(bindingMode=mode, valueType=kind)
                wire = encode(trial)
                wire["record"].pop("rowRoles")
                wire = encode_columns(wire)
                valid = kind in domains[str(index)].get(mode, [])
                assert (
                    Draft202012Validator(
                        table_layout.mapping_schema(
                            doc,
                            region,
                            layout=current,
                        )
                    ).is_valid(wire)
                    == valid
                )
                try:
                    _, ir = structural_ir(trial, doc, region)
                    compiled = compile_region(ir, doc, region)
                except CompileError:
                    assert not valid, (index, mode, kind)
                else:
                    assert valid, (index, mode, kind)
                    if index == 1 and mode == "cached" and not cache:
                        assert compiled.value_evidence[0]["status"] == "absent"
                    if index == 1 and mode == "source" and kind == "decimal":
                        assert compiled.value_evidence[0]["status"] == "uncertain"
    assert doc == before


@pytest.mark.parametrize("state", ["blank", "conflict", "gap"])
def test_nonpresent_cells_do_not_turn_read_domains_into_value_approval(state):
    doc, region, value = formula_table()
    table = doc.tables[region["tableRef"]]
    cell = next(c for c in table["cells"] if c["row"] == 1 and c["col"] == 0)
    bid = preferred_binding({k: doc.bindings[k] for k in region["bindingIds"]}, cell["sourceRef"])
    if state == "gap":
        table["cells"].remove(cell)
    elif state == "conflict":
        doc.bindings[bid]["candidateStatus"] = "unresolved_conflict"
    else:
        candidate = doc.bindings[bid]
        candidate.update(path="/text", start=0, end=0, candidateRole="cell")
        # Remove other paths so both planning and compiler choose this exact empty view.
        region["bindingIds"] = [
            k
            for k in region["bindingIds"]
            if doc.bindings[k]["sourceRef"] != cell["sourceRef"] or k == bid
        ]
    domain = permitted_reads(doc, region, {0: "header", 1: "data"})
    assert domain["0"]["source"] == list(TYPES)
    _, ir = structural_ir(value, doc, region)
    compiled = compile_region(ir, doc, region)
    assert (
        compiled.value_evidence[0]["status"]
        == {
            "blank": "blank",
            "conflict": "uncertain",
            "gap": "absent",
        }[state]
    )


def test_last_row_and_exact_regional_span_are_checked_not_first_row_or_whole_text():
    doc = observe(source_file("hwpx"), "hwpx")
    region = prepare_regions(doc, context_chars=16000)[0]
    cells = doc.tables[region["tableRef"]]["cells"]
    first = next(c for c in cells if c["row"] == 1 and c["col"] == 1)
    last = next(c for c in cells if c["row"] == 2 and c["col"] == 1)
    for cell, literal in [(first, "1.0"), (last, EXACT)]:
        ref = cell["sourceRef"]
        bid = preferred_binding({k: doc.bindings[k] for k in region["bindingIds"]}, ref)
        doc.nodes[ref]["text"] = "prefix " + literal + " suffix"
        doc.bindings[bid].update(path="/text", start=7, end=7 + len(literal), candidateRole="cell")
        region["bindingIds"] = [
            k for k in region["bindingIds"] if doc.bindings[k]["sourceRef"] != ref or k == bid
        ]
    first_only = permitted_reads(doc, region, {0: "header", 1: "data", 2: "note"})
    both = permitted_reads(doc, region, {0: "header", 1: "data", 2: "data"})
    assert "number" in first_only["1"]["source"] and "number" not in both["1"]["source"]
    assert "decimal" in both["1"]["source"]


@pytest.mark.parametrize("mutation", ["extra", "mixed", "coordinate", "mode", "type"])
def test_paired_decoder_rejects_conflicting_or_invented_choices(mutation):
    value = {"record": {"columns": {"0": {"definition": {"id": "i"}, "read": "source:string"}}}}
    column = value["record"]["columns"]["0"]
    if mutation == "extra":
        column["valueType"] = "decimal"
    elif mutation == "mixed":
        column["definition"]["bindingMode"] = "cached"
    elif mutation == "coordinate":
        column["definition"]["column"] = 1
    elif mutation == "mode":
        column["read"] = "evaluate:string"
    else:
        column["read"] = "source:rounded"
    before = deepcopy(value)
    with pytest.raises(CompileError, match="table_column_read_wire_invalid"):
        decode_columns(value)
    assert value == before


def test_overlapping_geometry_is_not_resolved_or_mistaken_for_a_type_failure():
    doc, region, value = formula_table()
    table = doc.tables[region["tableRef"]]
    first = next(c for c in table["cells"] if c["row"] == 1 and c["col"] == 0)
    other = next(c for c in table["cells"] if c["row"] == 1 and c["col"] == 1)
    table["cells"].append({**first, "sourceRef": other["sourceRef"]})
    before = deepcopy(doc)
    assert permitted_reads(doc, region, {0: "header", 1: "data"})["0"]["source"] == list(TYPES)
    _, ir = structural_ir(value, doc, region)
    with pytest.raises(CompileError, match="overlapping_observed_table_cells"):
        compile_region(ir, doc, region)
    assert doc == before


def test_completed_read_domain_identity_cannot_be_tampered_before_resume():
    from test_table_layout import Model, run

    model, states = Model(), []
    run(model, states=states)
    checkpoint = deepcopy(states[-1])
    structure = checkpoint["tableStages"]["semantic-region:1"]["structure"]
    assert len(structure["readDomainsSHA256"]) == 64
    structure["readDomainsSHA256"] = "0" * 64
    with pytest.raises(ValueError, match="incompatible"):
        run(model, restore=checkpoint)
    assert len(model.requests) == 3
