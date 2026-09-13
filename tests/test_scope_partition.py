"""Window delivery/replay mechanics; scripted responses are not quality approval."""

import copy
import io
import json

import pytest
from jsonschema import Draft202012Validator
from test_scope_bound_provenance import LongScopeModel
from test_scope_context_wire import long_table

from document_files.api import (
    AnalysisInput,
    AnalysisJob,
    ExtractionOptions,
    extract_schema_from_stream,
)
from document_files.interpretation import scope_partition as partition
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.integration import build_scope_inventory
from document_files.interpretation.legacy_engine import contract_messages
from document_files.interpretation.scope_context_wire import expand_scope_context
from document_files.interpretation.scope_protocol import SYSTEM


def prepared(count=96):
    doc, region, ir = long_table(count)
    compiled = compile_region(ir, doc, region)
    task = build_scope_inventory(doc, [region], [compiled])[0]
    plan = partition.plan_scope_partitions(task, input_chars=16000, system=SYSTEM)
    return doc, compiled, task, plan


def answer(wire, *, outcome="apply", direct=False):
    display = expand_scope_context(wire.payload)
    record = next(c for c in display["candidates"] if "rowOptions" in c)
    selections = (
        [
            {
                "kind": "standalone",
                "targetHandle": record["rowOptions"]["columns"][0]["columnHandle"],
            }
            if direct
            else {
                "kind": "record",
                "recordHandle": record["targetHandle"],
                "parts": [
                    {
                        "rowCoverage": {"kind": "allDataRows"},
                        "columnCoverage": {"kind": "allMappedColumns"},
                    }
                ],
            }
        ]
        if outcome == "apply"
        else []
    )
    return {
        "taskId": display["taskId"],
        "decision": outcome,
        "explanation": "Scripted review of this window only.",
        "selections": selections,
    }


@pytest.mark.parametrize("count", [96, 200])
def test_every_source_row_has_one_window_with_exact_text_and_original_ordinals(count):
    doc, compiled, task, plan = prepared(count)
    original = copy.deepcopy((doc, compiled, task))
    assert plan.status == "ready" and len(plan.windows) > 1
    offered_rows, sources = [], {}
    for entry in plan.windows:
        wire = plan.wires[entry["id"]]
        assert (
            entry["requestChars"]
            == sum(
                len(m["content"])
                for m in contract_messages(
                    SYSTEM + partition.SYSTEM_SUFFIX, wire.payload, wire.contract
                )
            )
            <= 16000
        )
        display = expand_scope_context(wire.payload)
        boundaries = display["rowBoundaryCandidates"]
        offered_rows.extend(r["row"] for r in boundaries)
        assert all(
            r["dataRowNumberInFragment"] == r["row"] for r in boundaries if r["role"] == "data"
        )
        record = next(c for c in display["candidates"] if "rowOptions" in c)
        for source in record["context"]:
            assert source["text"] == doc.nodes[source["sourceRef"]]["text"]
            assert not source["truncated"]
            sources[source["sourceRef"]] = source["text"]
        assert "c0:0" in {s["sourceRef"] for s in record["context"]}
        assert display["candidateCoverage"] == "window"
        assert set(entry["candidateHandles"]) == set(task.target_map)
    assert offered_rows == list(range(count + 1))
    assert sources == {
        f"c{r}:{c}": doc.nodes[f"c{r}:{c}"]["text"] for r in range(count + 1) for c in (0, 1)
    }
    assert (doc, compiled, task) == original


@pytest.mark.parametrize("direct", [False, True])
def test_all_rows_and_direct_columns_never_escape_the_current_window(direct):
    _, compiled, task, plan = prepared()
    entry = plan.windows[1]
    wire = plan.wires[entry["id"]]
    response = answer(wire, direct=direct)
    Draft202012Validator(wire.contract).validate(response)
    record, choice = partition.bind_window_response(response, wire, task, [compiled])
    low, high = next(iter(entry["rowWindows"].values()))
    assert choice["targetHandles"] == []
    assert len(choice["rowSelections"]) == 1
    assert (choice["rowSelections"][0]["rowStart"], choice["rowSelections"][0]["rowEnd"]) == (
        low,
        high,
    )
    assert len(choice["rowSelections"][0]["columnIds"]) == (1 if direct else 0)
    state = partition.record_window(
        partition.new_state(plan, task, [compiled]), entry["id"], record
    )
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    paths = {s["path"] for s in result[0].semantic_details[0]["scope"]}
    assert paths == {
        f"/rows/{r - 1}/{c}"
        for r in range(low, high + 1)
        for c in (["name"] if direct else ["name", "amount"])
    }
    assert coverage["status"] == "unresolved"
    assert [w["outcome"] for w in coverage["windows"]].count("unseen") == len(plan.windows) - 1
    assert result[0].data == compiled.data


@pytest.mark.parametrize("count", [96, 200])
def test_complete_aggregation_rebinds_all_values_and_resume_does_not_change_sources(count):
    _, compiled, task, plan = prepared(count)
    state = partition.new_state(plan, task, [compiled])
    for entry in plan.windows:
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(answer(wire), wire, task, [compiled])
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert coverage["status"] == "reviewed"
    assert len(result[0].semantic_details[0]["scope"]) == count * 2
    assert len(result[0].semantic_details[0]["scopeEvidence"]["sourceRefs"]) == count * 2 + 2
    assert result[0].data == compiled.data
    resumed = partition.replay_partitions(json.loads(json.dumps(state)), plan, task, [compiled])
    assert resumed == (result, coverage)


@pytest.mark.parametrize("outcome", ["no_target", "unresolved"])
def test_negative_and_unresolved_are_explicit_and_never_inferred_from_an_empty_answer(outcome):
    _, compiled, task, plan = prepared()
    state = partition.new_state(plan, task, [compiled])
    for entry in plan.windows:
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(
            answer(wire, outcome=outcome), wire, task, [compiled]
        )
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert result == [compiled] and coverage["status"] == "unresolved"
    assert {w["outcome"] for w in coverage["windows"]} == {outcome}
    entry = plan.windows[0]
    wire = plan.wires[entry["id"]]
    response = answer(wire)
    response["selections"] = []
    with pytest.raises(CompileError):
        partition.bind_window_response(response, wire, task, [compiled])
    response = answer(wire)
    response["decision"] = outcome
    with pytest.raises(CompileError):
        partition.bind_window_response(response, wire, task, [compiled])


def test_positive_negative_unresolved_and_unseen_ranges_do_not_collapse_together():
    _, compiled, task, plan = prepared()
    state = partition.new_state(plan, task, [compiled])
    for entry, outcome in zip(plan.windows, ["apply", "no_target", "unresolved"], strict=False):
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(
            answer(wire, outcome=outcome), wire, task, [compiled]
        )
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert [w["outcome"] for w in coverage["windows"]] == [
        "apply",
        "no_target",
        "unresolved",
        "unseen",
    ]
    assert len(result[0].semantic_details[0]["scope"]) == 46
    assert result[0].semantic_details[0]["interpretationStatus"] == "uncertain"


@pytest.mark.parametrize(
    "mutation", ["wire", "response", "proof", "negative-value", "plan", "foreign-window"]
)
def test_checkpoint_cannot_forge_decisions_or_hide_changed_values(mutation):
    _, compiled, task, plan = prepared()
    entry = plan.windows[0]
    wire = plan.wires[entry["id"]]
    outcome = "no_target" if mutation == "negative-value" else "apply"
    record, _ = partition.bind_window_response(
        answer(wire, outcome=outcome), wire, task, [compiled]
    )
    state = partition.record_window(
        partition.new_state(plan, task, [compiled]), entry["id"], record
    )
    if mutation == "wire":
        state["windows"][entry["id"]]["wireFingerprint"] = "forged"
    elif mutation == "response":
        state["windows"][entry["id"]]["response"]["decision"] = "no_target"
    elif mutation == "proof":
        state["windows"][entry["id"]]["sourceBinding"]["bindings"][0]["sourceRefs"] = []
    elif mutation == "negative-value":
        compiled.data["rows"][-1]["amount"] = "changed after negative review"
    elif mutation == "plan":
        state["planFingerprint"] = "forged"
    else:
        state["windows"]["foreign-window"] = {"status": "invalid"}
    with pytest.raises(CompileError, match="scope_partition_record_incompatible"):
        partition.replay_partitions(state, plan, task, [compiled])


def test_source_coordinates_and_row_aliases_outside_the_window_are_rejected():
    _, compiled, task, plan = prepared()
    first, second = [plan.wires[e["id"]] for e in plan.windows[:2]]
    foreign = expand_scope_context(first.payload)["rowBoundaryCandidates"][0]["rowRef"]
    response = answer(second)
    response["selections"][0]["parts"][0]["rowCoverage"] = {
        "kind": "rowRange",
        "rowStartRef": foreign,
        "rowEndRef": foreign,
    }
    with pytest.raises(CompileError):
        partition.bind_window_response(response, second, task, [compiled])


def test_unsendable_context_and_plan_resource_limits_are_explicit(monkeypatch):
    doc, compiled, task, _ = prepared()
    limited = partition.plan_scope_partitions(task, input_chars=6000, system=SYSTEM)
    assert limited.status == "partial" and not limited.wires
    assert len(limited.windows) == 97
    assert {e["status"] for e in limited.windows} == {"context_unavailable"}
    monkeypatch.setattr(partition, "MAX_WINDOWS", 2)
    limited = partition.plan_scope_partitions(task, input_chars=16000, system=SYSTEM)
    assert limited.status == "resource_limited" and limited.windows == () and not limited.wires
    assert len(compiled.data["rows"]) == 96 and doc.nodes["c96:1"]["text"] == "96.00"


@pytest.mark.parametrize("role", ["note", "subtotal", "blank", "unresolved", "unobserved"])
def test_windows_preserve_nondata_roles_missingness_and_reference_context(role):
    doc, region, ir = long_table(96)
    if role == "unobserved":
        doc.tables["t"]["cells"] = [c for c in doc.tables["t"]["cells"] if c["row"] != 30]
        ir.repeats[0].rowRoles = [r for r in ir.repeats[0].rowRoles if r.row != 30]
    else:
        ir.repeats[0].rowRoles[30].role = role
    if role == "blank":
        # A blank-row scope fixture needs genuinely empty observed cells. The
        # source-contradiction tests separately reject blank roles over values.
        for ref in ir.repeats[0].rowRoles[30].sourceRefs:
            doc.nodes[ref]["text"] = ""
            for binding in doc.bindings.values():
                if binding["sourceRef"] == ref and binding.get("start") is not None:
                    binding.update(start=0, end=0)
    compiled = compile_region(ir, doc, region)
    task = build_scope_inventory(doc, [region], [compiled])[0]
    plan = partition.plan_scope_partitions(task, input_chars=16000, system=SYSTEM)
    state = partition.new_state(plan, task, [compiled])
    for entry in plan.windows:
        wire = plan.wires[entry["id"]]
        if role != "unobserved":
            # Outside-window notes and unclear rows remain reference context.
            display = expand_scope_context(wire.payload)
            record = next(c for c in display["candidates"] if "rowOptions" in c)
            assert {"c30:0", "c30:1"} <= {c["sourceRef"] for c in record["context"]}
        record, _ = partition.bind_window_response(answer(wire), wire, task, [compiled])
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert len(result[0].data["rows"]) == 95 and result[0].data == compiled.data
    assert len(result[0].semantic_details[0]["scope"]) == 190
    assert (coverage["status"] == "reviewed") == (role not in {"unobserved", "unresolved"})


def test_header_group_is_only_its_columns_at_the_offered_rows():
    from test_scope_axis_wire import native_group_fixture

    from document_files.interpretation.scope_axis_wire import prepare_scope_axis_wire

    compiled, task = native_group_fixture()
    base = prepare_scope_axis_wire([task])
    handle = next(iter(base.records[task.id]))
    group = next(iter(base.records[task.id][handle]["groups"]))
    _, wire = partition._window_wire(
        task, base, [c["targetHandle"] for c in base.payload["candidates"]], {handle: [3, 3]}
    )
    value = answer(wire)
    value["selections"][0]["parts"][0]["columnCoverage"] = {
        "kind": "headerGroup",
        "groupHandle": group,
    }
    record, choice = partition.bind_window_response(value, wire, task, [compiled])
    assert choice["rowSelections"][0]["rowStart"] == choice["rowSelections"][0]["rowEnd"] == 3
    assert set(choice["rowSelections"][0]["columnIds"]) == {"length", "width"}
    assert not choice["targetHandles"]
    assert any(b.get("observationStatus") == "blank" for b in record["sourceBinding"]["bindings"])


def test_distinct_record_families_cover_the_whole_inventory_without_foreign_targets():
    from test_scope_rows import two_fragments

    from document_files.interpretation.scope_axis_wire import prepare_scope_axis_wire

    doc, regions, compiled = two_fragments()
    task = build_scope_inventory(doc, regions, compiled)[0]
    base = prepare_scope_axis_wire([task])
    families = partition._families(base, task)
    assert len(families) == 2
    # Choose a measured window limit that holds either whole family but not both.
    sizes = []
    for family in families:
        _, wire = partition._window_wire(task, base, family, {})
        sizes.append(
            sum(
                len(m["content"])
                for m in contract_messages(
                    SYSTEM + partition.SYSTEM_SUFFIX, wire.payload, wire.contract
                )
            )
        )
    plan = partition.plan_scope_partitions(task, input_chars=max(sizes), system=SYSTEM)
    assert plan.status == "ready" and len(plan.windows) == 2
    offered = [h for e in plan.windows for h in e["candidateHandles"]]
    assert len(offered) == len(set(offered)) and set(offered) == set(task.target_map)
    first, second = [plan.wires[e["id"]] for e in plan.windows]
    response = answer(first)
    response["selections"][0]["recordHandle"] = answer(second)["selections"][0]["recordHandle"]
    with pytest.raises(CompileError):
        partition.bind_window_response(response, first, task, compiled)


def test_reviewed_exclusions_do_not_stop_a_complete_positive_subset():
    _, compiled, task, plan = prepared()
    state = partition.new_state(plan, task, [compiled])
    for index, entry in enumerate(plan.windows):
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(
            answer(wire, outcome="apply" if index == 1 else "no_target"), wire, task, [compiled]
        )
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert coverage["status"] == "reviewed" and len(result[0].semantic_details[0]["scope"]) == 48


def test_oversized_aggregate_does_not_make_valid_saved_window_proofs_incompatible(monkeypatch):
    from document_files.interpretation import scope_source_binding

    _, compiled, task, plan = prepared()
    state = partition.new_state(plan, task, [compiled])
    for entry in plan.windows:
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(answer(wire), wire, task, [compiled])
        state = partition.record_window(state, entry["id"], record)
    monkeypatch.setattr(scope_source_binding, "MAX_TRACE_BINDINGS", 80)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert result[0].data == compiled.data and coverage["status"] == "unresolved"
    assert coverage["aggregationIssue"] == "scope_source_trace_budget_exceeded"
    assert {w["outcome"] for w in coverage["windows"]} == {"apply"}
    assert coverage["appliedWindows"] == [plan.windows[0]["id"]]
    assert len(coverage["unappliedWindows"]) == 3
    assert len(result[0].semantic_details[0]["scope"]) == 46
    assert result[0].semantic_details[0]["interpretationStatus"] == "uncertain"


def test_invalid_answer_in_one_window_does_not_discard_other_checked_windows():
    _, compiled, task, plan = prepared()
    state = partition.new_state(plan, task, [compiled])
    state = partition.record_window(state, plan.windows[0]["id"], {"status": "invalid"})
    for entry in plan.windows[1:]:
        wire = plan.wires[entry["id"]]
        record, _ = partition.bind_window_response(answer(wire), wire, task, [compiled])
        state = partition.record_window(state, entry["id"], record)
    result, coverage = partition.replay_partitions(state, plan, task, [compiled])
    assert coverage["status"] == "unresolved" and coverage["windows"][0]["outcome"] == "invalid"
    assert len(result[0].semantic_details[0]["scope"]) == 146


def engine_fixture(monkeypatch):
    # Isolate scope scheduling from native region packing. All actual scripted
    # requests still obey the configured 16k cap; only scope preflight uses 12k.
    # This is not evidence of a native-format/real-model long-document run.
    from test_table_protocol import layout_fixture

    from document_files.interpretation import engine, table_layout
    from document_files.interpretation.legacy_engine import contract_messages

    def scripted_layout_capacity(payload, observation, region, catalog=None):
        # The scope fixture explicitly supplies data roles for every non-fixed
        # row. Isolate scope-window scheduling from the conservative reserve for
        # OTHER possible layouts. Actual requests still pass the engine's 16k
        # preflight; native packing/reservation is tested in test_table_layout.
        chosen = table_layout.accept(layout_fixture(payload), observation, region)
        return {
            "layout": sum(
                len(m["content"])
                for m in contract_messages(
                    table_layout.SYSTEM,
                    table_layout.request(payload),
                    table_layout.schema(observation, region),
                )
            ),
            "mappingReserve": sum(
                len(m["content"])
                for m in contract_messages(
                    table_layout.MAPPING_SYSTEM,
                    table_layout.mapping_request(payload, chosen, observation, region),
                    table_layout.mapping_schema(observation, region, catalog),
                )
            ),
        }

    monkeypatch.setattr(table_layout, "planned_request_sizes", scripted_layout_capacity)
    readiness = engine.scope_readiness
    monkeypatch.setattr(
        engine, "scope_readiness", lambda task, *, input_chars: readiness(task, input_chars=12000)
    )
    content = (
        "<p>Lengths use mm.</p><table><tr><th>Length</th><th>Width</th></tr>"
        + "".join(
            f"<tr><td>001.2300</td><td>{'' if r == 17 else f'{r}.0000'}</td></tr>"
            for r in range(1, 29)
        )
        + "</table>"
    ).encode()
    job = AnalysisJob(
        job_id="partition-engine", input=AnalysisInput.from_bytes(content, format_id="html")
    )
    return content, job


def test_engine_preserves_partial_links_and_only_resumes_unseen_windows(monkeypatch):
    content, job = engine_fixture(monkeypatch)
    options = ExtractionOptions(contextChars=16000, maxModelCalls=5, reconstructionContext=False)
    model, states = LongScopeModel(), []
    result = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, checkpoint=states.append
    )
    assert result["extraction"]["status"] == "partial" and model.calls == 5
    assert len(result["data"]["rows"]) == 28 and result["data"]["rows"][16]["width"] == ""
    coverage = result["coverage"]["scopeIntegration"][0]["partition"]
    assert coverage["windows"][0]["outcome"] == "apply"
    assert any(w["outcome"] == "unseen" for w in coverage["windows"])
    assert result["semanticDetails"][0]["scope"]
    checkpoint = json.loads(json.dumps(states[-1]))
    calls = model.calls
    paused = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
    )
    assert model.calls == calls and paused["data"] == result["data"]
    resumed = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=model,
        restore=checkpoint,
        additional_budget={"maxModelCalls": 8, "completionSeconds": 300},
        checkpoint=states.append,
    )
    assert resumed["extraction"]["status"] == "complete", resumed["issues"]
    assert len(resumed["semanticDetails"][0]["scope"]) == 56
    assert model.calls - calls == sum(w["outcome"] == "unseen" for w in coverage["windows"])
    assert resumed["data"] == result["data"]
    calls = model.calls
    replay = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=model,
        restore=json.loads(json.dumps(states[-1])),
    )
    assert model.calls == calls and replay["semanticDetails"] == resumed["semanticDetails"]
    tampered = copy.deepcopy(states[-1])
    next(iter(tampered["scopePartitions"].values()))["valueIdentity"] = "forged"
    with pytest.raises(ValueError, match="incompatible"):
        extract_schema_from_stream(
            job, io.BytesIO(content), options=options, model_client=model, restore=tampered
        )
    assert model.calls == calls


def test_cancelled_window_loop_retains_completed_work_and_can_resume_without_new_allowance(
    monkeypatch,
):
    content, job = engine_fixture(monkeypatch)
    stopped = [False]

    class CancelAfterWindow(LongScopeModel):
        def complete(self, messages, **kwargs):
            response = super().complete(messages, **kwargs)
            if "scopeWindow" in json.loads(messages[-1]["content"]):
                stopped[0] = True
            return response

    model, states = CancelAfterWindow(), []
    options = ExtractionOptions(contextChars=16000, maxModelCalls=12, reconstructionContext=False)
    result = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=model,
        checkpoint=states.append,
        cancelled=lambda: stopped[0],
    )
    assert result["extraction"]["status"] == "partial" and model.calls == 5
    assert any(i["code"] == "ai_cancelled" for i in result["issues"])
    assert len(result["data"]["rows"]) == 28 and result["semanticDetails"][0]["scope"]
    # No new allowance: cancellation did not consume all 12 authorized scripted calls.
    resumed = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=model,
        restore=json.loads(json.dumps(states[-1])),
        cancelled=lambda: False,
    )
    assert resumed["extraction"]["status"] == "complete" and resumed["data"] == result["data"]


def test_invalid_window_is_not_retried_without_an_explicit_grant_and_keeps_valid_siblings(
    monkeypatch,
):
    content, job = engine_fixture(monkeypatch)

    class InvalidFirstWindow(LongScopeModel):
        bad = True
        seen = []

        def complete(self, messages, **kwargs):
            payload = json.loads(messages[-1]["content"])
            if "scopeWindow" in payload:
                identifier = payload["scopeWindow"]["id"]
                self.seen.append(identifier)
                if self.bad:
                    self.bad = False
                    self.calls += 1
                    return json.dumps({"taskId": payload["taskId"], "selections": []})
            return super().complete(messages, **kwargs)

    options = ExtractionOptions(contextChars=16000, maxModelCalls=12, reconstructionContext=False)
    model, states = InvalidFirstWindow(), []
    result = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, checkpoint=states.append
    )
    assert result["extraction"]["status"] == "partial" and result["semanticDetails"][0]["scope"]
    coverage = result["coverage"]["scopeIntegration"][0]["partition"]
    assert coverage["windows"][0]["outcome"] == "invalid"
    checkpoint, calls = json.loads(json.dumps(states[-1])), model.calls
    paused = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
    )
    assert paused["extraction"]["status"] == "partial" and model.calls == calls
    resumed = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=model,
        restore=checkpoint,
        additional_budget={"maxModelCalls": 1, "completionSeconds": 60},
    )
    assert resumed["extraction"]["status"] == "complete" and model.calls == calls + 1
    assert model.seen.count(model.seen[0]) == 2
    assert len(set(model.seen)) == len(model.seen) - 1
