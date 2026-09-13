"""Layout-first contracts, geometry and public execution; scripted, not quality."""

import copy
import io
import json

import pytest
from jsonschema import Draft202012Validator

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.observe import observe_document
from document_files.interpretation import table_layout as layout
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.table_protocol import structural_ir
from document_files.interpretation.table_selection_wire import encode_selection
from document_files.interpretation.table_source_wire import expand_table_sources
from document_files.interpretation.table_structure_wire import encode

HTML = (
    b"<table><tr><th>Code</th><th>Size</th></tr>"
    b"<tr><td>0007</td><td>1.2300</td></tr>"
    b"<tr><td>0008</td><td>0.00</td></tr></table>"
)


def fixture(content=HTML):
    doc = observe_document(content, "html", {})
    region = prepare_regions(doc, context_chars=16000)[0]
    return doc, region, region_payload(doc, region)


def response(doc, region, roles=None, kind="record_table", previous=None):
    order = region_payload(doc, region)["tables"][region["tableRef"]]["rowRoleOrder"]
    return {
        "regionId": region["id"],
        "tableKind": kind,
        "rowRoles": roles if roles is not None else ["data"] * len(order),
        "baseRevision": previous["sha256"] if previous else None,
    }


def mapping(payload):
    table_ref, table = next(iter(payload["tables"].items()))
    record = {
        "id": "records",
        "key": "records",
        "label": "Records",
        "tableRef": table_ref,
        "rowStart": 0,
        "rowEnd": 2,
        "definitionRefs": [r for c in table["columnCandidates"] for r in c["headerRefs"]],
        "columns": [
            {
                "id": key,
                "key": key,
                "label": key,
                "column": col,
                "valueType": typ,
                "definitionRefs": table["columnCandidates"][col]["headerRefs"],
            }
            for col, key, typ in [(0, "code", "string"), (1, "size", "decimal")]
        ],
        "rowRoles": [],
    }
    value = encode({"regionId": payload["regionId"], "tableKind": "record_table", "record": record})
    value["record"].pop("rowRoles")
    return value


class Model:
    identity = {"adapter": "layout-test", "model": "scripted-not-qualified"}

    def __init__(self, *, bad_mapping=False, broken_layout=False, revise=False):
        self.requests = []
        self.bad_mapping, self.broken_layout, self.revise = bad_mapping, broken_layout, revise

    def infer(self, request):
        self.requests.append(request)
        payload = expand_table_sources(json.loads(request.messages[-1]["content"]))
        if payload["tableStage"] == "layout":
            if self.broken_layout:
                raise ModelError("ai_response_incomplete")
            table = next(iter(payload["tables"].values()))
            roles = ["data"] * len(table["rowRoleOrder"])
            if self.revise and "previousLayout" in payload:
                roles[-1] = "unresolved"
            value = {
                "regionId": payload["regionId"],
                "tableKind": "record_table",
                "rowRoles": roles,
                "baseRevision": payload.get("baseLayoutSHA256"),
            }
        elif payload["tableStage"] == "structure":
            value = mapping(payload)
            if self.bad_mapping:
                value["record"]["columns"]["0"]["valueType"] = "boolean"
        else:
            value = encode_selection(
                {
                    "sourceDecisions": {
                        s["sourceRef"]: {"decision": "no_additional_meaning", "explanation": None}
                        for s in payload["meaningSources"]
                    }
                }
            )
        Draft202012Validator(request.output_schema).validate(value)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def run(model, *, states=None, restore=None, cancelled=None, **options):
    return extract_schema_from_stream(
        AnalysisJob(job_id="layout", input=AnalysisInput.from_bytes(HTML, format_id="html")),
        io.BytesIO(HTML),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        cancelled=cancelled,
    )


def test_public_layout_then_columns_preserves_values_and_resumes_without_calls():
    model, states = Model(), []
    result = run(model, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {
        "records": [{"code": "0007", "size": "1.2300"}, {"code": "0008", "size": "0.00"}]
    }
    stages = result["coverage"]["tableInterpretation"]["semantic-region:1"]
    assert list(stages)[0:3] == ["layout", "structure", "meaning"]
    assert [json.loads(r.messages[-1]["content"])["tableStage"] for r in model.requests] == [
        "layout",
        "structure",
        "meaning",
    ]
    assert (
        result["extraction"]["modelCalls"]
        == sum(stages[s]["usage"]["modelCalls"] for s in ("layout", "structure", "meaning"))
        == 3
    )
    assert run(model, restore=states[-1])["data"] == result["data"]
    assert len(model.requests) == 3


@pytest.mark.parametrize(
    "mutation", ["extra", "short", "long", "enum", "kind", "base", "coordinate"]
)
def test_layout_contract_rejects_invented_or_missing_decisions(mutation):
    doc, region, _ = fixture()
    value = response(doc, region)
    if mutation == "extra":
        value["guess"] = "private text"
    if mutation == "short":
        value["rowRoles"].pop()
    if mutation == "long":
        value["rowRoles"].append("data")
    if mutation == "enum":
        value["rowRoles"][0] = "guess"
    if mutation == "kind":
        value["tableKind"] = "scalar_form"
    if mutation == "base":
        value["baseRevision"] = "foreign"
    if mutation == "coordinate":
        value["rowRoles"][0] = {"row": 1, "role": "data"}
    with pytest.raises(CompileError, match="table_layout_"):
        layout.accept(value, doc, region)


def test_history_and_mapping_are_bound_to_original_source_not_native_flag_mutation():
    doc, region, payload = fixture()
    before = copy.deepcopy(doc)
    current = layout.accept(response(doc, region), doc, region)
    request = layout.mapping_request(payload, current, doc, region)
    expanded = expand_table_sources(request)
    value = mapping(expanded)
    Draft202012Validator(layout.mapping_schema(doc, region)).validate(value)
    _, ir = structural_ir(layout.decode_mapping(value, current, doc, region), doc, region)
    assert len(compile_region(ir, doc, region).data["records"]) == 2
    assert doc == before
    progress = {"status": "complete", "usage": {"modelCalls": 1}, "history": [current]}
    assert layout.validate_history(progress, doc, region) == current
    bad = copy.deepcopy(progress)
    bad["history"][0]["response"]["rowRoles"][0] = "header"
    with pytest.raises(ValueError):
        layout.validate_history(bad, doc, region)
    doc.nodes[region["nodeIds"][0]]["text"] += "changed"
    with pytest.raises(ValueError):
        layout.validate_history(progress, doc, region)


def test_failed_mapping_gets_one_explicit_layout_review_without_resetting_attempts():
    model, states = Model(bad_mapping=True), []
    result = run(model, states=states)
    assert result["data"] is None and result["extraction"]["status"] == "partial"
    assert [json.loads(r.messages[-1]["content"])["tableStage"] for r in model.requests] == [
        "layout",
        "structure",
        "layout",
        "structure",
    ]
    state = states[-1]["tableStages"]["semantic-region:1"]
    assert state["layout"]["attempts"] == state["structure"]["attempts"] == 2
    assert len(state["layout"]["history"]) == 2
    review = json.loads(model.requests[2].messages[-1]["content"])
    assert review["previousLayout"] == state["layout"]["history"][0]["response"]
    assert "invalid_table_value_selection" in json.dumps(review["repairFeedback"])
    run(model, restore=states[-1])
    assert len(model.requests) == 4


def test_unavailable_layout_is_not_automatically_replayed_on_resume():
    model, states = Model(broken_layout=True), []
    result = run(model, states=states)
    assert result["data"] is None and len(model.requests) == 1
    run(model, restore=states[-1])
    assert len(model.requests) == 1


@pytest.mark.parametrize("format_id", ["hwpx", "xlsx"])
def test_native_rows_and_header_candidates_keep_exact_source_and_layout_identity(format_id):
    from test_native_merged_geometry import observe
    from test_table_source_wire import native_table

    doc = observe(native_table(format_id, count=8), format_id)
    before = copy.deepcopy(doc)
    region = prepare_regions(doc, context_chars=16000)[0]
    order = region_payload(doc, region)["tables"][region["tableRef"]]["rowRoleOrder"]
    value = response(doc, region, ["header" if r == 0 else "data" for r in order])
    record = layout.accept(value, doc, region)
    candidates = layout.header_candidates(record, doc, region)
    for candidate, label in zip(candidates, ["ID", "Length", "Width"], strict=True):
        assert candidate["headerText"] == label
        assert candidate["modelHeaderRefs"] == candidate["headerRefs"]
    projected = region_payload(doc, region)
    initial = expand_table_sources(layout.request(projected))
    mapped = expand_table_sources(layout.mapping_request(projected, record, doc, region))
    assert initial["nodes"] == mapped["nodes"]
    assert (
        initial["tables"][region["tableRef"]]["cells"]
        == mapped["tables"][region["tableRef"]]["cells"]
    )
    assert mapped["tableLayout"]["sha256"] == record["sha256"]
    assert doc.nodes == before.nodes and doc.bindings == before.bindings
    for ref, table in before.tables.items():
        assert doc.tables[ref] == table


@pytest.mark.parametrize("basis", ["native_structure", "recognition", None])
@pytest.mark.parametrize("native_flag", [False, True])
def test_mixed_spans_and_row_labels_do_not_become_column_headers(basis, native_flag):
    from document_files.document_model.model import ObservationDocument
    from document_files.document_model.table_headers import column_header, row_role_order

    cells = [
        {"sourceRef": "span", "row": 0, "col": 0, "rowSpan": 2, "isHeader": native_flag},
        {"sourceRef": "leaf", "row": 0, "col": 1, "isHeader": False},
        {"sourceRef": "value", "row": 1, "col": 1, "isHeader": False},
        {"sourceRef": "row-label", "row": 3, "col": 0, "isHeader": True, "headerScope": "row"},
        {"sourceRef": "note", "row": 5, "col": 0, "isHeader": False},
    ]
    table = {"id": "table", "basis": basis, "cells": cells}
    doc = ObservationDocument(
        nodes={c["sourceRef"]: {"text": c["sourceRef"]} for c in cells},
        bindings={},
        regions=[],
        tables={"table": table},
        relations=[],
        issues=[],
        coverage={},
        provenance={},
    )
    region = {"id": "region", "nodeIds": list(doc.nodes), "tableRef": "table"}
    roles = {0: "header", 1: "data", 3: "header", 5: "note"}
    value = {
        "regionId": "region",
        "tableKind": "record_table",
        "baseRevision": None,
        "rowRoles": [roles[r] for r in row_role_order(table)],
    }
    current = layout.accept(value, doc, region)
    candidates = layout.header_candidates(current, doc, region)
    assert candidates[0]["headerRefs"] == []
    assert candidates[1]["headerRefs"] == ["leaf"]
    assert column_header(cells[0], table, roles) is False
    assert column_header(cells[3], table, roles) is False


@pytest.mark.parametrize("stop_after", [1, 2])
def test_cancel_preserves_layout_or_compiled_values_and_resumes_only_remaining_stage(stop_after):
    model, states = Model(), []
    result = run(model, states=states, cancelled=lambda: len(model.requests) >= stop_after)
    assert len(model.requests) == stop_after
    assert (result["data"] is None) == (stop_after == 1)
    assert states[-1]["tableStages"]["semantic-region:1"]["layout"]["status"] == "complete"
    resumed = run(model, restore=states[-1])
    assert resumed["extraction"]["status"] == "complete"
    assert len(model.requests) == 3


def test_pre_dispatch_layout_budget_spends_no_mapping_call_and_does_not_restart_layout():
    model, states = Model(), []
    result = run(model, states=states, maxModelCalls=1)
    assert result["data"] is None and len(model.requests) == 1
    resumed = run(model, restore=states[-1], maxModelCalls=1)
    assert resumed["data"] is None and len(model.requests) == 1


@pytest.mark.parametrize(
    "mutation", ["layout_hash", "response", "usage", "missing", "binding", "reopen", "pending"]
)
def test_tampered_layout_or_mapping_association_is_rejected_before_dispatch(mutation):
    model, states = Model(), []
    run(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    state = checkpoint["tableStages"]["semantic-region:1"]
    if mutation == "layout_hash":
        state["layout"]["history"][0]["sha256"] = "0" * 64
    elif mutation == "response":
        state["layout"]["history"][0]["response"]["rowRoles"][0] = "header"
    elif mutation == "usage":
        checkpoint["usage"]["modelCalls"] = 2
    elif mutation == "missing":
        state["layout"]["history"] = []
    elif mutation == "reopen":
        state["layout"]["status"] = "pending"
    elif mutation == "pending":
        state["structure"]["layoutRevisionPending"] = True
    else:
        state["structure"]["layoutSHA256"] = "0" * 64
    with pytest.raises(ValueError, match="incompatible"):
        run(model, restore=checkpoint)
    assert len(model.requests) == 3


def test_changed_layout_does_not_reset_failed_mapping_attempts_or_publish_previous_roles():
    model, states = Model(bad_mapping=True, revise=True), []
    result = run(model, states=states)
    state = states[-1]["tableStages"]["semantic-region:1"]
    history = state["layout"]["history"]
    assert history[0]["response"]["rowRoles"] != history[1]["response"]["rowRoles"]
    assert history[1]["response"]["baseRevision"] == history[0]["sha256"]
    assert state["structure"]["layoutSHA256"] == history[1]["sha256"]
    assert state["structure"]["attempts"] == 2 and result["data"] is None
    assert len(model.requests) == 4
    run(model, restore=states[-1])
    assert len(model.requests) == 4


def test_mapping_cannot_supply_a_different_layout_even_without_schema_decoding():
    doc, region, payload = fixture()
    current = layout.accept(response(doc, region), doc, region)
    value = mapping(expand_table_sources(layout.mapping_request(payload, current, doc, region)))
    value["record"]["rowRoles"] = ["header", "header"]
    with pytest.raises(CompileError, match="cannot_change_layout"):
        layout.decode_mapping(value, current, doc, region)


@pytest.mark.parametrize("kind", ["scalar_form", "unresolved"])
def test_nonrecord_layout_requires_null_roles_and_never_calls_column_mapping(kind):
    class Nonrecord(Model):
        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            self.requests.append(request)
            if payload.get("tableStage") == "layout":
                value = {
                    "regionId": payload["regionId"],
                    "tableKind": kind,
                    "rowRoles": None,
                    "baseRevision": None,
                }
                return InferenceResponse(json.dumps(value), {})
            assert "tableStage" not in payload and payload["tableKind"] == "scalar_form"
            raise ModelError("ai_cancelled")

    model, states = Nonrecord(), []
    result = run(model, states=states)
    assert result["data"] is None and len(model.requests) == (2 if kind == "scalar_form" else 1)
    assert states[-1]["tableStages"]["semantic-region:1"]["structure"]["attempts"] == 0


@pytest.mark.parametrize("format_id", ["hwpx", "xlsx"])
def test_fifty_row_plans_reserve_real_mapping_capacity_and_preserve_all_cells(format_id):
    from test_native_merged_geometry import observe
    from test_table_source_wire import native_table

    from document_files.interpretation.legacy_engine import contract_messages

    doc = observe(native_table(format_id), format_id)
    original = [c["sourceRef"] for t in doc.tables.values() for c in t["cells"]]
    seen = []
    for region in prepare_regions(doc, context_chars=16000):
        payload = region_payload(doc, region) | {"intent": "discover", "targetHandles": {}}
        order = payload["tables"][region["tableRef"]]["rowRoleOrder"]
        current = layout.accept(
            response(doc, region, ["header" if r == 0 else "data" for r in order]), doc, region
        )
        sizes = layout.planned_request_sizes(payload, doc, region, {})
        actual = sum(
            len(m["content"])
            for m in contract_messages(
                layout.MAPPING_SYSTEM,
                layout.mapping_request(payload, current, doc, region),
                layout.mapping_schema(doc, region, {}),
            )
        )
        assert actual <= sizes["mappingReserve"] <= region["requestChars"] <= 16000
        seen.extend(c["sourceRef"] for c in doc.tables[region["tableRef"]]["cells"])
    assert seen == original and len(seen) == len(set(seen))


def test_mapping_reservation_bounds_every_role_combination_without_native_mutation():
    from itertools import product

    from document_files.interpretation.legacy_engine import contract_messages

    raw = (
        b'<table><tr><td rowspan="2">ID</td><td>Measure</td></tr>'
        b"<tr><td>Width</td></tr><tr><td>0004</td><td>1.20</td></tr></table>"
    )
    doc, region, payload = fixture(raw)
    before = copy.deepcopy(doc)
    bound = layout.planned_request_sizes(payload, doc, region)["mappingReserve"]
    for roles in product(layout.ROLES, repeat=3):
        current = layout.accept(response(doc, region, list(roles)), doc, region)
        messages = contract_messages(
            layout.MAPPING_SYSTEM,
            layout.mapping_request(payload, current, doc, region),
            layout.mapping_schema(doc, region),
        )
        assert sum(len(m["content"]) for m in messages) <= bound
    assert doc == before


def test_oversized_mapping_repair_keeps_the_layout_and_does_not_charge_another_call():
    class Limited(Model):
        def infer(self, request):
            response = super().infer(request)
            if json.loads(request.messages[-1]["content"])["tableStage"] == "structure":
                self.input_budget_chars = 1
            return response

    model, states = Limited(bad_mapping=True), []
    result = run(model, states=states)
    state = states[-1]["tableStages"]["semantic-region:1"]
    assert result["data"] is None and len(model.requests) == 2
    assert len(state["layout"]["history"]) == 1 and state["layout"]["attempts"] == 1
    assert state["layout"]["inputPreflight"]["withinBudget"] is False
    assert state["structure"]["attempts"] == 1 and state["structure"]["layoutRevisionPending"]
    assert "invalid_table_value_selection" in json.dumps(state["layout"]["feedback"])
    run(model, restore=states[-1])
    assert len(model.requests) == 2


@pytest.mark.parametrize("kind", ["scalar_form", "unresolved"])
def test_layout_review_may_reclassify_a_failed_record_without_stale_mapping_work(kind):
    class Reclassify(Model):
        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if payload.get("tableStage") == "layout" and "previousLayout" in payload:
                self.requests.append(request)
                value = {
                    "regionId": payload["regionId"],
                    "tableKind": kind,
                    "rowRoles": None,
                    "baseRevision": payload["baseLayoutSHA256"],
                }
                return InferenceResponse(json.dumps(value), {})
            if payload.get("tableKind") == "scalar_form":
                self.requests.append(request)
                raise ModelError("ai_cancelled")
            return super().infer(request)

    model, states = Reclassify(bad_mapping=True), []
    result = run(model, states=states)
    state = states[-1]["tableStages"]["semantic-region:1"]
    assert state["kind"] == kind and state["structure"]["status"] == "complete"
    assert not state["structure"].get("layoutRevisionPending")
    assert state["structure"]["attempts"] == 1 and len(state["layout"]["history"]) == 2
    assert not any(i.get("tableStage") == "structure" for i in result["issues"])
    assert result["data"] is None
    before = len(model.requests)
    run(model, restore=states[-1], cancelled=lambda: True)
    assert len(model.requests) == before
