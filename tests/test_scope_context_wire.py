"""Long scope delivery checks, not actual model or document-quality approval."""

import copy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_scope_rows import fixture

from document_files.interpretation.compiler import compile_region
from document_files.interpretation.integration import apply_scope_decision, build_scope_tasks
from document_files.interpretation.legacy_engine import contract_messages
from document_files.interpretation.scope_context_wire import (
    SYSTEM as CONTEXT_SYSTEM,
)
from document_files.interpretation.scope_context_wire import (
    _pack,
    _unpack,
    compact_scope_context,
    expand_scope_context,
)
from document_files.interpretation.scope_protocol import SYSTEM
from document_files.interpretation.scope_selection_wire import prepare_scope_selection_wire
from document_files.interpretation.scope_source_binding import bind_scope_sources
from document_files.interpretation.semantic_types import RegionInterpretation
from document_files.interpretation.source_dictionary import _same


def long_table(count=50):
    doc, region, ir = fixture()
    value = ir.model_dump()
    for row in range(4, count + 1):
        for col, text in enumerate((f"item-{row:06d}", f"{row}.00")):
            ref = doc.node(f"c{row}:{col}", text)
            doc.bind(ref, start=0, end=len(text), candidateRole="content")
            doc.tables["t"]["cells"].append(
                {
                    "sourceRef": ref,
                    "row": row,
                    "col": col,
                    "rowSpan": 1,
                    "colSpan": 1,
                    "isHeader": False,
                }
            )
    doc.tables["t"]["rowCount"] = count + 1
    value["repeats"][0].update(
        rowEnd=count,
        rowRoles=[
            {"row": r, "role": "header" if r == 0 else "data", "sourceRefs": [f"c{r}:0", f"c{r}:1"]}
            for r in range(count + 1)
        ],
    )
    value["dispositions"] = [
        {"sourceRef": ref, "role": "data", "explanation": "Scripted geometry fixture."}
        for ref in doc.nodes
    ]
    region.update(nodeIds=list(doc.nodes), bindingIds=list(doc.bindings))
    return doc, region, RegionInterpretation.model_validate(value)


def test_blocks_keep_types_missing_keys_null_empty_records_and_order():
    records = [
        {"sourceRef": f"n{i}", "text": "literal recordBlocks", "value": value, "range": [i, i + 1]}
        for i, value in enumerate([True, 1, 1.0, None] * 8)
    ]
    records.insert(5, {"sourceRef": "different-shape", "text": ""})
    records[8]["optional"] = None
    before = copy.deepcopy(records)
    packed = _pack(records)
    assert isinstance(packed, dict)
    assert _same(_unpack(packed), records) and records == before
    # A shared-only record still produces one row per original occurrence.
    equal = [{"text": "same"}] * 32
    assert len(_pack(equal)["recordBlocks"][0]["rows"]) == 32
    assert _unpack(_pack(equal)) == equal
    assert _unpack(_pack([{}] * 32)) == [{}] * 32


def test_encoding_savings_include_instructions_and_short_lists_remain_plain():
    payload = {"candidates": [{"context": [{"sourceRef": "n", "text": "1"}]}]}
    assert compact_scope_context(payload) == payload
    assert CONTEXT_SYSTEM not in contract_messages(SYSTEM, payload, {})[0]["content"]
    doc, reg, ir = long_table()
    task = build_scope_tasks(doc, [reg], [compile_region(ir, doc, reg)])[0]
    wire = prepare_scope_selection_wire([task])
    plain = expand_scope_context(wire.payload)
    messages = contract_messages(SYSTEM, wire.payload, wire.contract)
    assert messages[0]["content"].count(CONTEXT_SYSTEM) == 1
    assert sum(len(m["content"]) for m in messages) < sum(
        len(m["content"]) for m in contract_messages(SYSTEM, plain, wire.contract)
    )
    with pytest.raises(ValueError, match="already_encoded"):
        compact_scope_context(wire.payload)


def test_fifty_rows_fit_actual_request_without_removing_text_or_candidates(monkeypatch):
    doc, reg, ir = long_table()
    compiled = compile_region(ir, doc, reg)
    tasks = build_scope_tasks(doc, [reg], [compiled], context_chars=16000)
    before = copy.deepcopy((doc, tasks, compiled))
    task = tasks[0]
    assert task.complete_candidates and len(compiled.data["rows"]) == 50
    wire = prepare_scope_selection_wire(tasks)
    assert (
        sum(len(m["content"]) for m in contract_messages(SYSTEM, wire.payload, wire.contract))
        <= 16000
    )
    monkeypatch.setattr(
        "document_files.interpretation.scope_selection_wire.compact_scope_context", lambda p: p
    )
    plain = prepare_scope_selection_wire(tasks)
    assert (
        sum(len(m["content"]) for m in contract_messages(SYSTEM, plain.payload, plain.contract))
        > 24000
    )
    assert _same(expand_scope_context(wire.payload), plain.payload)
    assert wire.contract == plain.contract and wire.base == plain.base
    assert wire.fingerprint != plain.fingerprint  # Actual delivery is part of identity.
    display = expand_scope_context(wire.payload)
    record = next(c for c in display["candidates"] if "rowOptions" in c)
    rows = display["rowBoundaryCandidates"]
    assert [r["row"] for r in rows] == list(range(51))
    assert {r["sourceRef"]: r["text"] for r in record["context"]} == {
        f"c{r}:{c}": doc.nodes[f"c{r}:{c}"]["text"] for r in range(51) for c in (0, 1)
    }
    column = next(
        c["columnHandle"] for c in record["rowOptions"]["columns"] if c["label"] == "Amount"
    )
    response = {
        "taskId": task.id,
        "decision": "apply",
        "explanation": "Scripted subset, not semantic evidence.",
        "selections": [
            {
                "kind": "record",
                "recordHandle": record["targetHandle"],
                "parts": [
                    {
                        "rowCoverage": {
                            "kind": "rowRange",
                            "rowStartRef": rows[1]["rowRef"],
                            "rowEndRef": rows[49]["rowRef"],
                        },
                        "columnCoverage": {"kind": "selectedColumns", "columnHandles": [column]},
                    }
                ],
            }
        ],
    }
    Draft202012Validator(wire.contract).validate(response)
    assert wire.decode(response) == plain.decode(response)
    choice, trace = bind_scope_sources(
        wire.decode(response), task, [compiled], expected_fingerprint=task.fingerprint
    )
    after, changed = apply_scope_decision([compiled], task, choice)
    assert changed and after[0].data == compiled.data
    assert len(after[0].semantic_details[0]["scope"]) == 49
    assert len([b for b in trace["bindings"] if b["basis"] == "selected_value_binding"]) == 49
    assert (doc, tasks, compiled) == before


@pytest.mark.parametrize("mutation", ["text", "role", "source", "type"])
def test_packed_source_and_geometry_changes_change_request_fingerprint(mutation):
    doc, reg, ir = long_table()
    task = build_scope_tasks(doc, [reg], [compile_region(ir, doc, reg)])[0]
    payload = copy.deepcopy(task.payload)
    record = next(c for c in payload["candidates"] if "rowOptions" in c)
    if mutation == "text":
        record["context"][-1]["text"] = "50.000"
    elif mutation == "role":
        record["rowOptions"]["rows"][-1]["role"] = "note"
    elif mutation == "source":
        record["context"][-1]["sourceRef"] = "another-source"
    else:
        record["context"][-1]["truncated"] = 0  # Not False.
    changed = replace(task, payload=payload)
    if mutation == "role":
        with pytest.raises(ValueError, match="axis_row_options_mismatch"):
            prepare_scope_selection_wire([changed])
        return
    assert (
        prepare_scope_selection_wire([task]).fingerprint
        != prepare_scope_selection_wire([changed]).fingerprint
    )


def test_batched_context_roundtrips_and_existing_discovery_limits_stay_visible(monkeypatch):
    doc, reg, ir = long_table()
    task = build_scope_tasks(doc, [reg], [compile_region(ir, doc, reg)])[0]
    other_payload = copy.deepcopy(task.payload)
    other_payload["taskId"] = "another-task"
    other = replace(task, id="another-task", payload=other_payload)
    wire = prepare_scope_selection_wire([task, other])
    monkeypatch.setattr(
        "document_files.interpretation.scope_selection_wire.compact_scope_context", lambda p: p
    )
    plain = prepare_scope_selection_wire([task, other])
    assert _same(expand_scope_context(wire.payload), plain.payload)
    assert wire.contract == plain.contract
    doc, reg, ir = long_table(96)
    task = build_scope_tasks(doc, [reg], [compile_region(ir, doc, reg)], context_chars=16000)[0]
    assert not task.complete_candidates and task.payload["candidateCoverage"] == "bounded"
    assert len(compile_region(ir, doc, reg).data["rows"]) == 96
