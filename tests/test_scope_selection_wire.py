"""General selection codec contracts; scripted choices do not establish AI quality."""

import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_cross_region_integration import grouped_table_fixture
from test_scope_axis_wire import native_group_fixture, response, scalar_origin_fixture, table_task
from test_scope_protocol import independent_tasks
from test_scope_rows import fixture, two_fragments

from document_files.interpretation.compiler import compile_region, join_continuations
from document_files.interpretation.integration import (
    apply_scope_decision,
    build_scope_tasks,
    parse_scope_choices,
)
from document_files.interpretation.scope_selection_wire import prepare_scope_selection_wire
from document_files.interpretation.scope_source_binding import (
    bind_scope_choices,
    bind_scope_sources,
)


def from_axis(wire, old):
    value = {k: old[k] for k in ("explanation", "taskId", "decision")}
    selections = []
    for record in old.get("recordScopes", []):
        record = copy.deepcopy(record)
        aliases = {
            cid: alias
            for alias, cid in wire.inverse_columns[old["taskId"]][record["recordHandle"]].items()
        }
        for part in record["parts"]:
            col = part["columnCoverage"]
            if "columnIds" in col:
                col["columnHandles"] = [aliases[c] for c in col.pop("columnIds")]
        selections.append({"kind": "record", **record})
    selections.extend(
        {"kind": "standalone", "targetHandle": h} for h in old.get("targetHandles", [])
    )
    value["selections"] = selections
    return value


def check_equivalent(wire, old):
    value = from_axis(wire, old)
    before = copy.deepcopy((value, wire))
    Draft202012Validator.check_schema(wire.contract)
    Draft202012Validator(wire.contract).validate(value)
    canonical = wire.decode(value)
    assert canonical == wire.base.decode(old)
    assert (value, wire) == before
    return canonical


@pytest.mark.parametrize("mode", ["rows", "all-rows", "all-columns", "whole-record", "unresolved"])
def test_selection_axes_keep_exact_existing_compiler_targets(mode):
    _, _, _, compiled, task = table_task()
    wire = prepare_scope_selection_wire([task])
    old = response(wire.base, task)
    part = old["recordScopes"][0]["parts"][0]
    if mode in {"all-rows", "whole-record"}:
        part["rowCoverage"] = {"kind": "allDataRows"}
    if mode in {"all-columns", "whole-record"}:
        part["columnCoverage"] = {"kind": "allMappedColumns"}
    if mode == "unresolved":
        old.update(decision="unresolved", recordScopes=[])
    canonical = check_equivalent(wire, old)
    choice, _ = bind_scope_sources(
        canonical, task, [compiled], expected_fingerprint=task.fingerprint
    )
    after, _ = apply_scope_decision([compiled], task, choice)
    assert after[0].data == compiled.data and after[0].schema == compiled.schema
    for candidate in wire.payload["candidates"]:
        if "rowOptions" not in candidate:
            continue
        for col in candidate["rowOptions"]["columns"]:
            assert "columnId" not in col
            shown = next(
                c for c in wire.payload["candidates"] if c["targetHandle"] == col["columnHandle"]
            )
            assert shown["label"] == col["label"]


@pytest.mark.parametrize("kind", ["record", "standalone", "mixed", "unresolved"])
def test_mixed_candidates_can_select_none_either_or_both_without_deleting_fields(kind):
    _, _, compiled, task = scalar_origin_fixture()
    wire = prepare_scope_selection_wire([task])
    old = response(wire.base, task)
    old["targetHandles"] = wire.base.standalone[task.id] if kind in {"standalone", "mixed"} else []
    if kind in {"standalone", "unresolved"}:
        old["recordScopes"] = []
    if kind == "unresolved":
        old["decision"] = "unresolved"
    canonical = check_equivalent(wire, old)
    choice, _ = bind_scope_sources(canonical, task, compiled, expected_fingerprint=task.fingerprint)
    after, _ = apply_scope_decision(compiled, task, choice)
    paths = [t["path"] for t in after[0].semantic_details[0]["scope"]]
    assert ("/subtotal" in paths) == (kind in {"standalone", "mixed"})
    assert ("/rows/1/amount" in paths) == (kind in {"record", "mixed"})
    assert [c.data for c in after] == [c.data for c in compiled]


@pytest.mark.parametrize("joined", [False, True])
def test_multiple_records_and_continuations_do_not_mix_column_handles(joined):
    obs, regions, compiled = two_fragments()
    if joined:
        compiled, _, _ = join_continuations(
            compiled,
            [
                {
                    "id": "join",
                    "leftRegion": "r",
                    "rightRegion": "p2",
                    "leftTable": "t",
                    "rightTable": "t2",
                    "confirmed": False,
                    "sourceRefs": ["c0:0", "p2-c0:0"],
                }
            ],
            {"join": "continue"},
        )
    task = next(t for t in build_scope_tasks(obs, regions, compiled) if t.region_id == "r")
    wire = prepare_scope_selection_wire([task])
    records = list(wire.base.records[task.id])
    for record in records:
        old = response(wire.base, task, record=record)
        canonical = check_equivalent(wire, old)
        choice, _ = bind_scope_sources(
            canonical, task, compiled, expected_fingerprint=task.fingerprint
        )
        after, _ = apply_scope_decision(compiled, task, choice)
        assert [c.data for c in after] == [c.data for c in compiled]
    value = from_axis(wire, response(wire.base, task, record=records[0]))
    value["selections"][0]["parts"][0]["columnCoverage"]["columnHandles"] = [
        next(iter(wire.inverse_columns[task.id][records[1]]))
    ]
    assert wire.decode(value)["targetHandles"] is None


@pytest.mark.parametrize("owned", [False, True])
def test_header_groups_remain_available_inside_or_outside_a_record(owned):
    if owned:
        region, task = native_group_fixture()
        compiled = [region]
    else:
        obs, regions, compiled = grouped_table_fixture()
        task = build_scope_tasks(obs, regions, compiled)[1]
    wire = prepare_scope_selection_wire([task])
    old = {"taskId": task.id, "decision": "apply", "explanation": "Scripted group"}
    if owned:
        record = next(iter(wire.base.records[task.id]))
        group = next(iter(wire.base.records[task.id][record]["groups"]))
        old["recordScopes"] = [
            {
                "recordHandle": record,
                "parts": [
                    {
                        "rowCoverage": {"kind": "allDataRows"},
                        "columnCoverage": {"kind": "headerGroup", "groupHandle": group},
                    }
                ],
            }
        ]
    else:
        old["targetHandles"] = [
            next(h for h in wire.base.standalone[task.id] if h.startswith("@headerGroup"))
        ]
    canonical = check_equivalent(wire, old)
    choice, _ = bind_scope_sources(canonical, task, compiled, expected_fingerprint=task.fingerprint)
    after, changed = apply_scope_decision(compiled, task, choice)
    assert changed and [c.data for c in after] == [c.data for c in compiled]


def test_bounded_candidates_retain_row_columns_without_inventing_whole_column_targets():
    _, _, _, _, task = table_task()
    payload = copy.deepcopy(task.payload)
    record = next(c for c in payload["candidates"] if "rowOptions" in c)
    record.pop("coversCandidates", None)
    payload["candidates"] = [record]
    task = replace(
        task,
        payload=payload,
        target_map={record["targetHandle"]: task.target_map[record["targetHandle"]]},
        complete_candidates=False,
    )
    wire = prepare_scope_selection_wire([task])
    old = response(wire.base, task)
    check_equivalent(wire, old)
    value = from_axis(wire, old)
    value["selections"][0]["parts"][0]["rowCoverage"] = {"kind": "allDataRows"}
    assert wire.decode(value)["targetHandles"] is None


def test_numeric_source_range_does_not_turn_an_unobserved_row_into_data():
    obs, region, ir = fixture()
    obs.tables["t"]["cells"] = [c for c in obs.tables["t"]["cells"] if c["row"] != 2]
    ir.repeats[0].rowRoles = [r for r in ir.repeats[0].rowRoles if r.row != 2]
    compiled = compile_region(ir, obs, region)
    task = build_scope_tasks(obs, [region], [compiled])[0]
    wire = prepare_scope_selection_wire([task])
    old = response(wire.base, task, ordinal=1)
    old["recordScopes"][0]["parts"][0]["rowCoverage"] = {
        "kind": "sourceRowRange",
        "rowStart": 1,
        "rowEnd": 2,
    }
    canonical = check_equivalent(wire, old)
    choice, _ = bind_scope_sources(
        canonical, task, [compiled], expected_fingerprint=task.fingerprint
    )
    after, changed = apply_scope_decision([compiled], task, choice)
    assert changed and after[0].data == compiled.data
    assert after[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/rows/0/amount"}]
    assert after[0].semantic_details[0]["interpretationStatus"] == "uncertain"


@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "citation",
        "column-id",
        "column-integer",
        "foreign-task",
        "foreign-record",
        "record-as-standalone",
        "duplicate",
        "limit",
        "extra",
        "wrong-kind",
    ],
)
def test_invalid_selections_are_not_repaired_or_partially_applied(mutation):
    _, _, compiled, task = scalar_origin_fixture()
    wire = prepare_scope_selection_wire([task])
    old = response(wire.base, task)
    value = from_axis(wire, old)
    if mutation == "legacy":
        value = old
    elif mutation == "citation":
        value["sourceRefs"] = []
    elif mutation == "column-id":
        column = value["selections"][0]["parts"][0]["columnCoverage"]
        column["columnIds"] = column.pop("columnHandles")
    elif mutation == "column-integer":
        value["selections"][0]["parts"][0]["columnCoverage"]["columnHandles"] = [0]
    elif mutation == "foreign-task":
        value["taskId"] = "unknown"
    elif mutation == "foreign-record":
        value["selections"][0]["recordHandle"] = "unknown"
    elif mutation == "record-as-standalone":
        value["selections"] = [
            {"kind": "standalone", "targetHandle": value["selections"][0]["recordHandle"]}
        ]
    elif mutation in {"duplicate", "limit"}:
        value["selections"] *= 2 if mutation == "duplicate" else 101
    elif mutation == "extra":
        value["selections"][0]["sourceRefs"] = []
    else:
        value["selections"][0]["kind"] = "column"
    before = copy.deepcopy((value, compiled))
    decoded, traces = bind_scope_choices(wire.decode(value), [task], compiled)
    assert parse_scope_choices(decoded, [task]) == ([], True) and traces == {}
    assert (value, compiled) == before


def test_eight_task_batch_preserves_literal_strings_and_rejects_duplicate_tasks():
    tasks = independent_tasks(8)
    tasks[0].payload["statement"]["description"] = "Literal col-0, columnIds, @column0"
    wire = prepare_scope_selection_wire(tasks)
    assert (
        wire.payload["tasks"][0]["statement"]["description"]
        == tasks[0].payload["statement"]["description"]
    )
    values = [
        {
            "taskId": t.id,
            "decision": "unresolved",
            "explanation": "col-0 @column0",
            "selections": [],
        }
        for t in tasks
    ]
    Draft202012Validator(wire.contract).validate({"decisions": values})
    result = wire.decode({"decisions": values})
    assert len(parse_scope_choices(result, tasks)[0]) == 8
    values[1]["taskId"] = values[0]["taskId"]
    assert len(parse_scope_choices(wire.decode({"decisions": values}), tasks)[0]) == 6
    values[1] = {"taskId": tasks[1].id}
    assert len(parse_scope_choices(wire.decode({"decisions": values}), tasks)[0]) == 7
    changed = copy.deepcopy(tasks)
    changed[0].payload["statement"]["description"] += " more"
    assert prepare_scope_selection_wire(changed).fingerprint != wire.fingerprint
    assert "columnIds" in json.dumps(wire.payload)  # Literal text was not rewritten.
