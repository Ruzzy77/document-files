"""Deterministic boundary checks, not model quality or default-engine qualification."""

import copy
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator
from test_cross_region_integration import fixture as scalar_fixture
from test_cross_region_integration import grouped_table_fixture
from test_scope_rows import fixture, two_fragments

from document_files.document_model.observe import observe_document
from document_files.interpretation.compiler import CompileError, compile_region, join_continuations
from document_files.interpretation.integration import (
    apply_scope_decision,
    build_scope_tasks,
    parse_scope_choices,
)
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.scope_axis_wire import prepare_scope_axis_wire
from document_files.interpretation.scope_source_binding import (
    bind_scope_choices,
    bind_scope_sources,
)
from document_files.interpretation.semantic_types import RegionInterpretation


def table_task():
    observation, region, ir = fixture()
    compiled = compile_region(ir, observation, region)
    task = build_scope_tasks(observation, [region], [compiled])[0]
    return observation, region, ir, compiled, task


def response(wire, task, *, record=None, ordinal=2, columns=None):
    records = wire.records[task.id]
    handle = record or next(iter(records))
    boundary = next(ref for ref in records[handle]["rows"] if ref.endswith(f".dataRow{ordinal}"))
    result = {
        "explanation": "Scripted choice; literal @record9.dataRow2 and source ID stay unchanged.",
        "taskId": task.id,
        "decision": "apply",
        "recordScopes": [
            {
                "recordHandle": handle,
                "parts": [
                    {
                        "rowCoverage": {
                            "kind": "rowRange",
                            "rowStartRef": boundary,
                            "rowEndRef": boundary,
                        },
                        "columnCoverage": {
                            "kind": "selectedColumns",
                            "columnIds": columns or ["amount"],
                        },
                    }
                ],
            }
        ],
    }
    if wire.standalone[task.id]:
        result["targetHandles"] = []
    return result


def bind(wire, value, task, compiled):
    decision = wire.decode(value)
    return bind_scope_sources(decision, task, compiled, expected_fingerprint=task.fingerprint)


def test_row_axis_round_trip_preserves_literals_sources_values_and_schema():
    _, _, _, compiled, task = table_task()
    before = copy.deepcopy((compiled, task))
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task)
    Draft202012Validator.check_schema(wire.contract)
    Draft202012Validator(wire.contract).validate(value)
    decision, trace = bind(wire, value, task, [compiled])
    assert decision["explanation"] == value["explanation"]
    assert decision["rowSelections"][0]["rowStart"] == 2
    assert "c2:1" in decision["sourceRefs"]
    assert not trace["modelSuppliedSourceRefs"]
    result, changed = apply_scope_decision([compiled], task, decision)
    assert changed and (compiled, task) == before
    assert result[0].data == compiled.data and result[0].schema == compiled.schema
    assert result[0].schema_evidence == compiled.schema_evidence
    assert result[0].value_observations == compiled.value_observations
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/rows/1/amount"}]
    assert all("/rows/" not in json.dumps(m) for m in [wire.payload, wire.contract])


@pytest.mark.parametrize("whole_columns", [False, True])
def test_all_rows_is_distinct_from_all_columns(whole_columns):
    _, _, _, compiled, task = table_task()
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task)
    part = value["recordScopes"][0]["parts"][0]
    part["rowCoverage"] = {"kind": "allDataRows"}
    if whole_columns:
        part["columnCoverage"] = {"kind": "allMappedColumns"}
    decision, _ = bind(wire, value, task, [compiled])
    result, _ = apply_scope_decision([compiled], task, decision)
    paths = [t["path"] for t in result[0].semantic_details[0]["scope"]]
    assert paths == (["/rows"] if whole_columns else [f"/rows/{i}/amount" for i in range(3)])
    assert not decision["rowSelections"]


@pytest.mark.parametrize("joined", [False, True])
def test_multiple_records_keep_fragment_geometry_and_foreign_refs_are_invalid(joined):
    observation, regions, compiled = two_fragments()
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
    task = next(t for t in build_scope_tasks(observation, regions, compiled) if t.region_id == "r")
    wire = prepare_scope_axis_wire([task])
    assert len(wire.records[task.id]) == 2
    handle = next(
        h for h in wire.records[task.id] if task.target_map[wire.targets[h]]["regionId"] == "p2"
    )
    value = response(wire, task, record=handle)
    decision, trace = bind(wire, value, task, compiled)
    result, _ = apply_scope_decision(compiled, task, decision)
    expected = "/rows/4/amount" if joined else "/rows/1/amount"
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": expected}]
    assert any(b.get("sourceRefs") == ["p2-c2:1"] for b in trace["bindings"])
    foreign = next(h for h in wire.records[task.id] if h != handle)
    part = value["recordScopes"][0]["parts"][0]
    part["rowCoverage"]["rowStartRef"] = next(iter(wire.records[task.id][foreign]["rows"]))
    assert wire.decode(value)["targetHandles"] is None


def test_scalar_batch_bad_sibling_and_duplicates_are_not_hidden_by_binding():
    observation, regions, compiled = scalar_fixture()
    tasks = build_scope_tasks(observation, regions, compiled)
    wire = prepare_scope_axis_wire(tasks)
    values = []
    for task in tasks:
        values.append(
            {
                "explanation": "Literal @field0",
                "taskId": task.id,
                "decision": "apply",
                "targetHandles": [wire.standalone[task.id][0]],
            }
        )
    Draft202012Validator(wire.contract).validate({"decisions": values})
    good = copy.deepcopy(values)
    values[1]["targetHandles"] = ["canonical-ID-is-not-an-alias"]
    decoded, traces = bind_scope_choices(wire.decode({"decisions": values}), tasks, compiled)
    valid, invalid = parse_scope_choices(decoded, tasks)
    assert invalid and [v.taskId for v in valid] == [tasks[0].id] and set(traces) == {tasks[0].id}
    values = good
    values[1]["taskId"] = values[0]["taskId"]
    decoded, traces = bind_scope_choices(wire.decode({"decisions": values}), tasks, compiled)
    assert parse_scope_choices(decoded, tasks) == ([], True) and not traces


def test_unowned_header_group_remains_a_standalone_candidate():
    observation, regions, compiled = grouped_table_fixture()
    task = build_scope_tasks(observation, regions, compiled)[1]
    wire = prepare_scope_axis_wire([task])
    assert not wire.records[task.id]
    group = next(h for h in wire.standalone[task.id] if h.startswith("@headerGroup"))
    value = {
        "explanation": "Only the offered group",
        "taskId": task.id,
        "decision": "apply",
        "targetHandles": [group],
    }
    choice, _ = bind(wire, value, task, compiled)
    result, _ = apply_scope_decision(compiled, task, choice)
    assert len(result[0].semantic_details[1]["scope"]) == 2


def test_bounded_record_retains_rows_without_inventing_missing_column_handles():
    _, _, _, compiled, task = table_task()
    payload = copy.deepcopy(task.payload)
    record = next(c for c in payload["candidates"] if "rowOptions" in c)
    payload["candidates"] = [record]
    record.pop("coversCandidates", None)
    task = replace(
        task,
        payload=payload,
        target_map={record["targetHandle"]: task.target_map[record["targetHandle"]]},
        complete_candidates=False,
    )
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task)
    Draft202012Validator(wire.contract).validate(value)
    choice, _ = bind(wire, value, task, [compiled])
    result, _ = apply_scope_decision([compiled], task, choice)
    assert result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    value["recordScopes"][0]["parts"][0]["rowCoverage"] = {"kind": "allDataRows"}
    assert not Draft202012Validator(wire.contract).is_valid(value)
    assert wire.decode(value)["targetHandles"] is None


@pytest.mark.parametrize("role", ["note", "subtotal", "blank", "unresolved", "unobserved"])
def test_row_gaps_and_nondata_never_become_new_observations(role):
    observation, region, ir = fixture()
    if role == "unobserved":
        observation.tables["t"]["cells"] = [
            c for c in observation.tables["t"]["cells"] if c["row"] != 2
        ]
        ir.repeats[0].rowRoles = [r for r in ir.repeats[0].rowRoles if r.row != 2]
    else:
        ir.repeats[0].rowRoles[2].role = role
    compiled = compile_region(ir, observation, region)
    task = build_scope_tasks(observation, [region], [compiled])[0]
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task, ordinal=1)
    record = next(iter(wire.records[task.id].values()))
    rows = value["recordScopes"][0]["parts"][0]["rowCoverage"]
    if role == "unobserved":
        rows.clear()
        rows.update(kind="sourceRowRange", rowStart=1, rowEnd=2)
    else:
        rows["rowEndRef"] = next(ref for ref, row in record["rows"].items() if row == 3)
    Draft202012Validator(wire.contract).validate(value)
    choice, _ = bind(wire, value, task, [compiled])
    result, _ = apply_scope_decision([compiled], task, choice)
    assert (
        result[0].data == compiled.data
        and result[0].value_observations == compiled.value_observations
    )
    assert result[0].semantic_details[0]["interpretationStatus"] == (
        "uncertain" if role in {"unobserved", "unresolved"} else "interpreted"
    )


@pytest.mark.parametrize(
    "basis,status", [("native_structure", "absent"), ("recognition", "uncertain")]
)
def test_missing_cell_keeps_missingness_and_only_row_geometry_provenance(basis, status):
    observation, region, ir = fixture()
    observation.tables["t"]["basis"] = basis
    observation.tables["t"]["cells"] = [
        c for c in observation.tables["t"]["cells"] if c["sourceRef"] != "c3:1"
    ]
    ir.repeats[0].rowRoles[3].sourceRefs = ["c3:0"]
    compiled = compile_region(ir, observation, region)
    task = build_scope_tasks(observation, [region], [compiled])[0]
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task, ordinal=3)
    choice, trace = bind(wire, value, task, [compiled])
    missing = next(
        b for b in trace["bindings"] if b["basis"] == "selected_row_geometry_without_value_binding"
    )
    assert missing["sourceRefs"] == ["c3:0"] and missing["observationStatus"] == status
    result, _ = apply_scope_decision([compiled], task, choice)
    assert result[0].data["rows"][-1]["amount"] is None
    assert result[0].value_evidence[-1]["status"] == status
    assert result[0].value_observations == compiled.value_observations


@pytest.mark.parametrize(
    "mutation",
    [
        "citation",
        "raw-target",
        "foreign-column",
        "boolean",
        "duplicate-record",
        "duplicate-column",
        "extra-field",
    ],
)
def test_invalid_new_wire_inputs_never_silently_repair_or_drop(mutation):
    _, _, _, compiled, task = table_task()
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task)
    item = value["recordScopes"][0]
    part = item["parts"][0]
    if mutation == "citation":
        value["sourceRefs"] = []
    elif mutation == "raw-target":
        item["recordHandle"] = wire.targets[item["recordHandle"]]
    elif mutation == "foreign-column":
        part["columnCoverage"]["columnIds"] = ["unknown"]
    elif mutation == "boolean":
        part["rowCoverage"]["rowStartRef"] = True
    elif mutation == "duplicate-record":
        value["recordScopes"].append(copy.deepcopy(item))
    elif mutation == "duplicate-column":
        part["columnCoverage"]["columnIds"] *= 2
    else:
        part["rowCoverage"]["sourceRefs"] = ["c2:0"]
    before = copy.deepcopy((value, compiled, task))
    decoded, traces = bind_scope_choices(wire.decode(value), [task], [compiled])
    assert parse_scope_choices(decoded, [task]) == ([], True) and not traces
    assert (value, compiled, task) == before


def test_binding_fingerprint_missing_evidence_and_budgets_fail_atomically(monkeypatch):
    import document_files.interpretation.scope_source_binding as binding

    _, _, _, compiled, task = table_task()
    wire = prepare_scope_axis_wire([task])
    decoded = wire.decode(response(wire, task))
    with pytest.raises(CompileError, match="stale_bound_scope_task"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint="old")
    missing = copy.deepcopy(compiled)
    missing.value_evidence = [
        e for e in missing.value_evidence if e["target"]["path"] != "/rows/1/amount"
    ]
    with pytest.raises(CompileError, match="value_evidence"):
        bind_scope_sources(decoded, task, [missing], expected_fingerprint=task.fingerprint)
    before = copy.deepcopy(compiled)
    monkeypatch.setattr(binding, "MAX_SOURCE_REFS", 1)
    with pytest.raises(CompileError, match="binding_budget"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    monkeypatch.setattr(binding, "MAX_SOURCE_REFS", 100)
    monkeypatch.setattr(binding, "MAX_TRACE_BINDINGS", 1)
    with pytest.raises(CompileError, match="trace_budget"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    monkeypatch.setattr(binding, "MAX_TRACE_BINDINGS", 1000)
    monkeypatch.setattr(binding, "MAX_ROW_WORK", 0)
    with pytest.raises(CompileError, match="expansion_budget"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    assert compiled == before


def test_current_source_definition_and_row_options_must_match():
    _, _, _, compiled, task = table_task()
    wire = prepare_scope_axis_wire([task])
    decoded = wire.decode(response(wire, task))
    stale = copy.deepcopy(compiled)
    stale.semantics[0]["description"] = "changed"
    with pytest.raises(CompileError):
        bind_scope_sources(decoded, task, [stale], expected_fingerprint=task.fingerprint)
    changed = copy.deepcopy(task)
    record = next(c for c in changed.payload["candidates"] if "rowOptions" in c)
    record["rowOptions"]["rows"][1]["role"] = "note"
    with pytest.raises(ValueError, match="row_options_mismatch"):
        prepare_scope_axis_wire([changed])


def test_wire_fingerprint_tracks_literal_and_mapping_changes():
    _, _, _, compiled, task = table_task()
    original = prepare_scope_axis_wire([task])
    modified = copy.deepcopy(task)
    modified.payload["statement"]["description"] += " Literal @record1.dataRow1"
    new = prepare_scope_axis_wire([modified])
    assert original.fingerprint != new.fingerprint
    assert new.payload["statement"]["description"] == modified.payload["statement"]["description"]
    value = {
        "explanation": "Unknown",
        "taskId": task.id,
        "decision": "unresolved",
        "recordScopes": [],
    }
    decision, trace = bind(original, value, task, [compiled])
    assert not trace["bindings"] and decision["sourceRefs"] == []
    assert apply_scope_decision([compiled], task, decision) == ([compiled], False)


def native_group_fixture():
    content = (
        b"<table><caption>Measures have a shared annotation.</caption>"
        b'<tr><th rowspan="2">ID</th><th colspan="2">Measures</th></tr>'
        b"<tr><th>Length</th><th>Width</th></tr>"
        b"<tr><td>001</td><td>0</td><td>1.20</td></tr>"
        b"<tr><td>002</td><td>3.00</td><td></td></tr></table>"
    )
    obs = observe_document(content, "html", {})
    region = prepare_regions(obs, context_chars=16000)[0]
    table_ref, table = next(iter(obs.tables.items()))
    cell = {(c["row"], c["col"]): c["sourceRef"] for c in table["cells"]}
    ir = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "repeats": [
                {
                    "id": "records",
                    "key": "records",
                    "label": "Records",
                    "tableRef": table_ref,
                    "rowStart": 0,
                    "rowEnd": 3,
                    "definitionRefs": [cell[0, 0]],
                    "columns": [
                        {
                            "id": key,
                            "key": key,
                            "label": key,
                            "valueType": "string",
                            "column": col,
                            "definitionRefs": [cell[0 if col == 0 else 1, col]],
                        }
                        for col, key in enumerate(["id", "length", "width"])
                    ],
                    "rowRoles": [
                        {
                            "row": row,
                            "role": "header" if row < 2 else "data",
                            "sourceRefs": [
                                c["sourceRef"]
                                for c in table["cells"]
                                if c["row"] <= row < c["row"] + c.get("rowSpan", 1)
                            ],
                        }
                        for row in range(4)
                    ],
                }
            ],
            "meanings": [
                {
                    "id": "note",
                    "kind": "note",
                    "description": "Measures annotation",
                    "sourceRefs": [table["contextNodeIds"][0]],
                    "status": "interpreted",
                }
            ],
        }
    )
    compiled = compile_region(ir, obs, region)
    task = build_scope_tasks(obs, [region], [compiled])[0]
    return compiled, task


@pytest.mark.parametrize("one_row", [False, True])
def test_native_header_group_and_row_intersection_preserve_blank(one_row):
    compiled, task = native_group_fixture()
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task, columns=["length"])
    part = value["recordScopes"][0]["parts"][0]
    record = wire.records[task.id][value["recordScopes"][0]["recordHandle"]]
    group = next(iter(record["groups"]))
    part["columnCoverage"] = {"kind": "headerGroup", "groupHandle": group}
    if not one_row:
        part["rowCoverage"] = {"kind": "allDataRows"}
    Draft202012Validator(wire.contract).validate(value)
    choice, trace = bind(wire, value, task, [compiled])
    result, _ = apply_scope_decision([compiled], task, choice)
    paths = [t["path"] for t in result[0].semantic_details[0]["scope"]]
    assert len(paths) == (2 if one_row else 4) and not any(p.endswith("/id") for p in paths)
    assert (
        result[0].data == compiled.data
        and result[0].value_observations == compiled.value_observations
    )
    assert any(b.get("observationStatus") == "blank" for b in trace["bindings"]) == one_row
    assert (result[0].schema_evidence == compiled.schema_evidence) == one_row


def test_mixed_record_and_scalar_selection_keeps_every_candidate():
    obs, region, _, table, _ = table_task()
    extra_obs, extra_regions, scalar = scalar_fixture()
    obs.nodes.update(extra_obs.nodes)
    regions = [region, extra_regions[1]]
    compiled = [table, scalar[1]]
    task = build_scope_tasks(obs, regions, compiled)[0]
    wire = prepare_scope_axis_wire([task])
    assert wire.records[task.id] and wire.standalone[task.id]
    represented = set(wire.standalone[task.id])
    for handle, record in wire.records[task.id].items():
        represented.update([handle, *record["columnHandles"].values(), *record["groups"]])
    assert represented == {c["targetHandle"] for c in wire.payload["candidates"]}
    value = response(wire, task)
    value["targetHandles"] = [wire.standalone[task.id][0]]
    Draft202012Validator(wire.contract).validate(value)
    choice, _ = bind(wire, value, task, compiled)
    after, changed = apply_scope_decision(compiled, task, choice)
    assert changed and len(after[0].semantic_details[0]["scope"]) == 2
    assert [c.data for c in after] == [c.data for c in compiled]


def scalar_origin_fixture(status="blank"):
    obs, region, ir = fixture()
    ir.repeats[0].rowRoles[-1].role = "subtotal"
    record = compile_region(ir, obs, region)
    separate = {**region, "id": "separate"}
    bid = next(b for b, v in obs.bindings.items() if v["sourceRef"] == "c3:1")
    scalar = compile_region(
        RegionInterpretation.model_validate(
            {
                "regionId": "separate",
                "fields": [
                    {
                        "id": "subtotal",
                        "key": "subtotal",
                        "label": "Subtotal amount",
                        "valueType": "decimal",
                        "status": status,
                        "bindingId": bid if status == "blank" else None,
                        "definitionRefs": ["c0:1"],
                    }
                ],
            }
        ),
        obs,
        separate,
    )
    compiled = [record, scalar]
    regions = [region, separate]
    task = build_scope_tasks(obs, regions, compiled)[0]
    return obs, regions, compiled, task


@pytest.mark.parametrize("select_scalar", [False, True])
def test_scalar_cell_geometry_is_context_not_automatic_exclusion(select_scalar):
    obs, _, compiled, task = scalar_origin_fixture()
    before = copy.deepcopy((obs, compiled))
    wire = prepare_scope_axis_wire([task])
    scalar = next(c for c in wire.payload["candidates"] if c["label"] == "Subtotal amount")
    origin = scalar["valueOrigins"][0]
    assert origin["observationStatus"] == "blank" and origin["valueText"] == ""
    assert origin["valueSource"]["sourceRef"] == "c3:1"
    assert origin["tableLocations"] == [
        {
            "tableRef": "t",
            "row": 3,
            "column": 1,
            "rowSpan": 1,
            "columnSpan": 1,
            "tableBasis": "native_structure",
            "rowRoleBasis": "compiled_interpretation",
            "rowRoles": [{"row": 3, "role": "subtotal"}],
        }
    ]
    assert "/subtotal" not in json.dumps(wire.payload)
    value = response(wire, task)
    if select_scalar:
        value["recordScopes"] = []
        value["targetHandles"] = [scalar["targetHandle"]]
    Draft202012Validator(wire.contract).validate(value)
    choice, trace = bind(wire, value, task, compiled)
    after, changed = apply_scope_decision(compiled, task, choice)
    assert changed
    assert after[0].semantic_details[0]["scope"] == [
        {"space": "data", "path": "/subtotal" if select_scalar else "/rows/1/amount"}
    ]
    assert (
        any(b["basis"] == "selected_scalar_value_binding" for b in trace["bindings"])
        == select_scalar
    )
    assert (obs, compiled) == before


@pytest.mark.parametrize("status", ["absent", "uncertain"])
def test_unbound_scalar_does_not_acquire_an_observed_cell(status):
    _, _, _, task = scalar_origin_fixture(status)
    scalar = next(c for c in task.payload["candidates"] if c["label"] == "Subtotal amount")
    assert scalar["valueOrigins"] == [{"observationStatus": status, "valueSource": None}]


@pytest.mark.parametrize("mutation", ["binding", "status", "raw", "geometry", "role"])
def test_scalar_origin_changes_invalidate_fingerprint_and_saved_context(mutation):
    obs, regions, compiled, task = scalar_origin_fixture()
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task)
    value["targetHandles"] = wire.standalone[task.id]
    choice, _ = bind(wire, value, task, compiled)
    if mutation == "binding":
        compiled[1].value_evidence[0]["binding"]["sourceRef"] = "c2:1"
    elif mutation == "status":
        compiled[1].value_evidence[0]["status"] = "uncertain"
    elif mutation == "raw":
        compiled[1].value_evidence[0]["raw"] = "changed"
    elif mutation == "geometry":
        next(c for c in obs.tables["t"]["cells"] if c["sourceRef"] == "c3:1")["colSpan"] = 2
    else:
        compiled[0].row_scopes["rows"]["rows"]["3"]["role"] = "note"
    new = build_scope_tasks(obs, regions, compiled)[0]
    assert new.fingerprint != task.fingerprint
    assert prepare_scope_axis_wire([new]).fingerprint != wire.fingerprint
    if mutation in {"binding", "status", "raw"}:
        with pytest.raises(CompileError, match="stale_bound_scope_scalar_value"):
            bind(wire, value, task, compiled)
        with pytest.raises(CompileError, match="stale_scope_scalar_value"):
            apply_scope_decision(compiled, task, choice)


def test_scalar_context_limits_and_conflicting_roles_remain_explicit():
    obs, regions, compiled, _ = scalar_origin_fixture()
    compiled[1].value_evidence[0]["raw"] = "x" * 501
    extra = copy.deepcopy(compiled[0])
    extra.row_scopes["rows"]["rows"]["3"]["role"] = "note"
    from document_files.interpretation.scope_values import (
        ScalarOriginCatalog,
        scalar_value_evidence,
    )

    origins, refs, complete = ScalarOriginCatalog(obs, [*compiled, extra]).describe(
        scalar_value_evidence(compiled[1], compiled[1].semantics[0])
    )
    assert not complete and refs == ["c3:1"]
    assert origins[0]["valueTextTruncated"] and len(origins[0]["valueText"]) == 500
    assert origins[0]["tableLocations"][0]["rowRoles"] == [
        {"row": 3, "role": "note"},
        {"row": 3, "role": "subtotal"},
    ]
    task = build_scope_tasks(obs, regions, compiled)[0]
    assert not task.complete_candidates


def test_content_uncertainty_survives_exact_axis_scope():
    obs, region, ir = fixture()
    ir.meanings[0].status = "uncertain"
    compiled = compile_region(ir, obs, region)
    task = build_scope_tasks(obs, [region], [compiled])[0]
    wire = prepare_scope_axis_wire([task])
    choice, _ = bind(wire, response(wire, task), task, [compiled])
    result, _ = apply_scope_decision([compiled], task, choice)
    assert result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    assert any(i["code"] == "semantic_interpretation_uncertain" for i in result[0].issues)


@pytest.mark.parametrize(
    "mutation", ["old-citations", "record-and-row", "duplicate-row", "value-source"]
)
def test_binding_does_not_bypass_the_existing_compiler(mutation):
    _, _, _, compiled, task = table_task()
    wire = prepare_scope_axis_wire([task])
    decoded = wire.decode(response(wire, task))
    if mutation == "old-citations":
        decoded["sourceRefs"] = ["c0:0"]
        with pytest.raises(CompileError, match="cannot_replace"):
            bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
        return
    if mutation == "record-and-row":
        decoded["targetHandles"] = [decoded["rowSelections"][0]["targetHandle"]]
    elif mutation == "duplicate-row":
        decoded["rowSelections"] *= 2
    else:
        compiled = copy.deepcopy(compiled)
        next(e for e in compiled.value_evidence if e["target"]["path"] == "/rows/1/amount")[
            "binding"
        ]["sourceRef"] = "c3:1"
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError):
        bound, _ = bind_scope_sources(
            decoded, task, [compiled], expected_fingerprint=task.fingerprint
        )
        apply_scope_decision([compiled], task, bound)
    assert compiled == before


def test_record_context_reuses_bound_anchors_without_unrelated_value_sources(monkeypatch):
    import document_files.interpretation.scope_source_binding as binding

    obs, region, _, compiled, _ = table_task()
    record_definition = next(d for d in compiled.semantics if d["id"] == "r:rows")
    record_definition["sourceRefs"] = list(obs.nodes)
    task = build_scope_tasks(obs, [region], [compiled])[0]
    wire = prepare_scope_axis_wire([task])
    monkeypatch.setattr(binding, "MAX_SOURCE_REFS", 2)
    choice, trace = bind(wire, response(wire, task), task, [compiled])
    assert set(choice["sourceRefs"]) == {"c0:1", "c2:1"}
    anchor = next(b for b in trace["bindings"] if b["basis"] == "selected_record_definition")
    assert anchor["referenceMode"] == "already_bound_intersection"
    assert set(anchor["sourceRefs"]) == set(choice["sourceRefs"])
    result, _ = apply_scope_decision([compiled], task, choice)
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/rows/1/amount"}]


def test_literal_column_identifier_and_escaped_output_key_are_not_wire_aliases():
    obs, region, ir = fixture()
    ir.repeats[0].columns[1].id = "@record1.dataRow1"
    ir.repeats[0].columns[1].key = "a/b~1"
    compiled = compile_region(ir, obs, region)
    task = build_scope_tasks(obs, [region], [compiled])[0]
    wire = prepare_scope_axis_wire([task])
    value = response(wire, task, columns=["@record1.dataRow1"])
    choice, _ = bind(wire, value, task, [compiled])
    assert choice["rowSelections"][0]["columnIds"] == ["@record1.dataRow1"]
    result, _ = apply_scope_decision([compiled], task, choice)
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/rows/1/a~1b~01"}]
    assert result[0].data == compiled.data
