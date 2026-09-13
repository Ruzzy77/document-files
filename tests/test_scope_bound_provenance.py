"""Compiler-owned long provenance, not actual-model or native-quality approval."""

import copy
import io
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from test_scope_context_wire import long_table

from document_files.api import (
    AnalysisInput,
    AnalysisJob,
    ExtractionOptions,
    extract_schema_from_stream,
)
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.integration import (
    BoundScopeDecision,
    ScopeDecision,
    apply_scope_decision,
    build_scope_tasks,
    parse_scope_choices,
    scope_output_schema,
)
from document_files.interpretation.scope_context_wire import expand_scope_context
from document_files.interpretation.scope_protocol import (
    replay_scope_record,
    scope_policy,
    scope_request_identity,
)
from document_files.interpretation.scope_selection_wire import prepare_scope_selection_wire
from document_files.interpretation.scope_source_binding import (
    bind_scope_choices,
    bind_scope_sources,
)
from document_files.interpretation.table_selection_wire import encode_selection


def selection(payload, *, all_columns=True):
    display = expand_scope_context(payload)
    record = next(c for c in display["candidates"] if "rowOptions" in c)
    rows = [
        r
        for r in display["rowBoundaryCandidates"]
        if r["targetHandle"] == record["targetHandle"] and r["role"] == "data"
    ]
    columns = {"kind": "allMappedColumns"}
    if not all_columns:
        column = record["rowOptions"]["columns"][-1]["columnHandle"]
        columns = {"kind": "selectedColumns", "columnHandles": [column]}
    return {
        "taskId": payload["taskId"],
        "decision": "apply",
        "explanation": "Scripted exact row range; not a semantic quality judgment.",
        "selections": [
            {
                "kind": "record",
                "recordHandle": record["targetHandle"],
                "parts": [
                    {
                        "rowCoverage": {
                            "kind": "rowRange",
                            "rowStartRef": rows[0]["rowRef"],
                            "rowEndRef": rows[-1]["rowRef"],
                        },
                        "columnCoverage": columns,
                    }
                ],
            }
        ],
    }


def prepared(count=50, *, all_columns=True):
    doc, region, ir = long_table(count)
    compiled = compile_region(ir, doc, region)
    # Offline compiler fixture, not an increase to any actual model's input allowance.
    task = build_scope_tasks(doc, [region], [compiled], context_chars=1000000)[0]
    assert task.complete_candidates
    wire = prepare_scope_selection_wire([task])
    value = selection(wire.payload, all_columns=all_columns)
    Draft202012Validator(wire.contract).validate(value)
    decoded = wire.decode(value)
    return compiled, task, wire, decoded


@pytest.mark.parametrize(
    "rows,all_columns", [(49, True), (50, True), (96, True), (99, False), (200, True)]
)
def test_complete_compiler_sources_do_not_use_the_model_citation_count(rows, all_columns):
    compiled, task, wire, decoded = prepared(rows, all_columns=all_columns)
    before = copy.deepcopy((compiled, task, decoded))
    bound, trace = bind_scope_sources(
        decoded, task, [compiled], expected_fingerprint=task.fingerprint
    )
    assert type(bound) is BoundScopeDecision and decoded["sourceRefs"] == []
    assert bound.rowSelections and not bound.targetHandles  # No whole-column substitution.
    assert len(bound.sourceRefs) == rows * (2 if all_columns else 1) + 2
    assert len(bound.sourceRefs) == len(set(bound.sourceRefs))
    assert len([b for b in trace["bindings"] if b["basis"] == "selected_value_binding"]) == (
        rows * (2 if all_columns else 1)
    )
    assert all(
        len(b["valueProofSHA256"]) == 64
        for b in trace["bindings"]
        if b["basis"] == "selected_value_binding"
    )
    result, changed = apply_scope_decision([compiled], task, bound)
    assert changed and result[0].data == compiled.data
    assert len(result[0].semantic_details[0]["scope"]) == rows * (2 if all_columns else 1)
    assert result[0].semantic_details[0]["interpretationStatus"] == "interpreted"
    assert (compiled, task, decoded) == before
    assert parse_scope_choices(bound, [task]) == ([bound], False)
    legacy = scope_output_schema([task])
    assert legacy["properties"]["sourceRefs"]["maxItems"] == 100
    assert "sourceRefs" not in wire.contract["properties"]
    if len(bound.sourceRefs) > 100:
        with pytest.raises(ValueError):
            ScopeDecision.model_validate(bound.model_dump())
        assert parse_scope_choices(bound.model_dump(), [task]) == ([], True)
        with pytest.raises(CompileError, match="invalid_scope_decision"):
            apply_scope_decision([compiled], task, bound.model_dump())


def test_in_memory_decisions_revalidate_and_duplicate_tasks_stay_invalid():
    compiled, task, _, decoded = prepared()
    bound, _ = bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    assert parse_scope_choices({"decisions": [bound, bound]}, [task, task]) == ([], True)
    bound.rowSelections[0].columnIds.append("foreign-column")
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError):
        apply_scope_decision([compiled], task, bound)
    assert compiled == before
    bound.rowSelections[0].rowStart = True
    assert parse_scope_choices(bound, [task]) == ([], True)


def test_serialized_tag_cannot_authorize_compiler_provenance():
    compiled, task, _, decoded = prepared()
    bound, _ = bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    forged = bound.model_dump() | {"type": "BoundScopeDecision"}
    assert parse_scope_choices(forged, [task]) == ([], True)
    with pytest.raises(CompileError, match="cannot_replace_model_scope_citations"):
        bind_scope_sources(forged, task, [compiled], expected_fingerprint=task.fingerprint)


@pytest.mark.parametrize("limit", ["bytes", "trace", "work"])
def test_resource_exhaustion_remains_atomic_without_a_partial_provenance_union(monkeypatch, limit):
    import document_files.interpretation.scope_source_binding as binding

    compiled, task, _, decoded = prepared()
    bound, trace = bind_scope_sources(
        decoded, task, [compiled], expected_fingerprint=task.fingerprint
    )
    expected_size = len(binding._encoded(bound.model_dump())) + len(binding._encoded(trace))
    if limit == "bytes":
        monkeypatch.setattr(binding, "MAX_PROVENANCE_BYTES", expected_size)
        assert bind_scope_sources(
            decoded, task, [compiled], expected_fingerprint=task.fingerprint
        ) == (bound, trace)
        monkeypatch.setattr(binding, "MAX_PROVENANCE_BYTES", expected_size - 1)
        code = "provenance_budget"
    elif limit == "trace":
        monkeypatch.setattr(binding, "MAX_TRACE_BINDINGS", len(trace["bindings"]) - 1)
        code = "trace_budget"
    else:
        monkeypatch.setattr(binding, "MAX_ROW_WORK", 99)
        code = "row_expansion_budget"
    before = copy.deepcopy((compiled, task, decoded))
    with pytest.raises(CompileError, match=code):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    choices, traces = bind_scope_choices(decoded, [task], [compiled])
    assert parse_scope_choices(choices, [task]) == ([], True) and traces == {}
    assert (compiled, task, decoded) == before


def test_existing_trace_limit_still_rejects_too_many_values():
    compiled, task, _, decoded = prepared(500)
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError, match="trace_budget"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)
    assert compiled == before


def test_unresolved_defaults_and_unicode_are_in_the_actual_serialized_byte_budget(monkeypatch):
    import document_files.interpretation.scope_source_binding as binding

    compiled, task, _, _ = prepared()
    decoded = {
        "taskId": task.id,
        "decision": "unresolved",
        "sourceRefs": [],
        "explanation": "적용 대상을 결정하지 못했습니다.",
    }
    bound, trace = bind_scope_sources(
        decoded, task, [compiled], expected_fingerprint=task.fingerprint
    )
    size = len(binding._encoded(bound.model_dump())) + len(binding._encoded(trace))
    assert size > len(binding._encoded(decoded)) + len(binding._encoded(trace))
    monkeypatch.setattr(binding, "MAX_PROVENANCE_BYTES", size)
    assert bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint) == (
        bound,
        trace,
    )
    monkeypatch.setattr(binding, "MAX_PROVENANCE_BYTES", size - 1)
    with pytest.raises(CompileError, match="provenance_budget"):
        bind_scope_sources(decoded, task, [compiled], expected_fingerprint=task.fingerprint)


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "data",
        "raw",
        "binding",
        "state",
        "source",
        "transform",
        "links",
        "saved-union",
        "saved-proof",
    ],
)
def test_json_checkpoint_recomputes_long_provenance_and_exact_value_state(mutation):
    compiled, task, wire, decoded = prepared()
    bound, trace = bind_scope_sources(
        decoded, task, [compiled], expected_fingerprint=task.fingerprint
    )
    policy = scope_policy(SimpleNamespace())
    stored = json.loads(
        json.dumps(
            {
                "fingerprint": task.fingerprint,
                "selection": decoded,
                "decision": bound.model_dump(),
                "sourceBinding": trace,
                "request": scope_request_identity([task], wire, policy),
            }
        )
    )
    evidence = next(e for e in compiled.value_evidence if e["target"]["path"] == "/rows/49/amount")
    if mutation == "data":
        compiled.data["rows"][-1]["amount"] = "50.0000"
    elif mutation == "raw":
        evidence["raw"] = "changed"
    elif mutation == "binding":
        evidence["binding"]["path"] = "/semantic/value/raw"
    elif mutation == "state":
        evidence["status"] = "uncertain"
    elif mutation == "source":
        evidence["sourceRefs"] = ["different-source"]
    elif mutation == "transform":
        evidence["transformation"] = "changed"
    elif mutation == "links":
        evidence["semanticIds"].append("another-applied-meaning")
    elif mutation == "saved-union":
        stored["decision"]["sourceRefs"].pop()
    elif mutation == "saved-proof":
        next(b for b in stored["sourceBinding"]["bindings"] if "valueProofSHA256" in b)[
            "valueProofSHA256"
        ] = "forged"
    before = copy.deepcopy((compiled, stored))
    if mutation in {"none", "links"}:
        restored = replay_scope_record(stored, task, [task], [compiled], policy)
        assert type(restored) is BoundScopeDecision and restored == bound
        assert apply_scope_decision([compiled], task, restored)[1]
    else:
        with pytest.raises(CompileError, match="scope_record_incompatible"):
            replay_scope_record(stored, task, [task], [compiled], policy)
    assert (compiled, stored) == before


class LongScopeModel:
    identity = {"adapter": "long-scope-regression", "model": "scripted"}

    def __init__(self):
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        p = json.loads(messages[-1]["content"])
        if "taskId" in p:
            if not any("rowOptions" in c for c in p["candidates"]):
                return json.dumps(
                    {
                        "taskId": p["taskId"],
                        "decision": "unresolved",
                        "selections": [],
                        "explanation": "Scripted record candidate is outside discovery coverage.",
                    }
                )
            return json.dumps(selection(p))
        if p.get("meaningPhase") == "selection":
            return json.dumps(
                encode_selection(
                    {
                        "sourceDecisions": {
                            s["sourceRef"]: {
                                "decision": "no_additional_meaning",
                                "explanation": None,
                            }
                            for s in p["meaningSources"]
                        }
                    }
                )
            )
        if not p["tables"]:
            return json.dumps(
                {
                    "regionId": p["regionId"],
                    "meanings": [
                        {
                            "id": "unit",
                            "kind": "unit",
                            "description": "Lengths use mm",
                            "sourceRefs": p["nodeIds"],
                            "status": "interpreted",
                        }
                    ],
                    "dispositions": [
                        {"sourceRef": r, "role": "note", "explanation": "Scripted source"}
                        for r in p["nodeIds"]
                    ],
                }
            )
        table_ref, table = next(iter(p["tables"].items()))
        cells = table["cells"]
        if isinstance(cells, dict):
            cells = [dict(zip(cells["columns"], r, strict=True)) for r in cells["rows"]]
        headers = {c["col"]: c["sourceRef"] for c in cells if c["row"] == 0}
        end = max(c["row"] for c in cells)
        return json.dumps(
            {
                "regionId": p["regionId"],
                "tableKind": "record_table",
                "record": {
                    "id": "rows",
                    "key": "rows",
                    "label": "Measurements",
                    "tableRef": table_ref,
                    "rowStart": 0,
                    "rowEnd": end,
                    "definitionRefs": list(headers.values()),
                    "rowRoles": [{"row": r, "role": "data"} for r in range(1, end + 1)],
                    "columns": [
                        {
                            "id": key,
                            "key": key,
                            "label": key,
                            "valueType": "string",
                            "column": i,
                            "definitionRefs": [headers[i]],
                        }
                        for i, key in enumerate(("length", "width"))
                    ],
                },
            }
        )


def test_engine_persists_long_provenance_with_complete_inventory():
    content = (
        "<p>Lengths use mm.</p><table><tr><th>Length</th><th>Width</th></tr>"
        + "".join(
            f"<tr><td>001.2300</td><td>{'' if r == 37 else f'{r}.0000'}</td></tr>"
            for r in range(1, 51)
        )
        + "</table>"
    ).encode()
    job = AnalysisJob(
        job_id="long-scope", input=AnalysisInput.from_bytes(content, format_id="html")
    )
    # Deliberately roomy scripted planning, not an actual inference budget change.
    options = ExtractionOptions(contextChars=120000, maxModelCalls=12, reconstructionContext=False)
    model, states = LongScopeModel(), []
    result = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, checkpoint=states.append
    )
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(result["data"]["rows"]) == 50 and len(result["semanticDetails"][0]["scope"]) == 100
    assert result["data"]["rows"][36]["width"] == ""
    assert all(r["length"] == "001.2300" for r in result["data"]["rows"])
    stored = next(s for s in states[-1]["scopeDecisions"].values() if "decision" in s)
    assert len(stored["decision"]["sourceRefs"]) == 103
    assert stored["selection"]["sourceRefs"] == []
    assert len([b for b in stored["sourceBinding"]["bindings"] if "valueProofSHA256" in b]) == 100
    checkpoint, calls = json.loads(json.dumps(states[-1])), model.calls
    restored = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
    )
    assert model.calls == calls
    for key in ("data", "semantics", "semanticDetails", "valueEvidence", "valueObservations"):
        assert restored[key] == result[key]
    checkpoint["identity"]["scopeProtocol"]["sourceBindingVersion"] = (
        "document-files.scope-source-binding.v2"
    )
    with pytest.raises(ValueError, match="incompatible"):
        extract_schema_from_stream(
            job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
        )
    assert model.calls == calls
