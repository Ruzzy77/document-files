"""Scripted finite protocol tests, not actual model quality qualification."""

import copy
import io
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.observe import observe_document
from document_files.document_model.table_headers import fixed_header_rows, observed_rows
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.table_protocol import (
    STAGE_MAX_OUTPUT_TOKENS,
    TABLE_PROTOCOL_VERSION,
    TableMeaning,
    TableStructure,
    meaning_ir,
    meaning_payload,
    meaning_schema,
    structural_ir,
    structure_payload,
    structure_schema,
)
from document_files.interpretation.table_sources import source_inventory

HTML = (
    b"<table><tr><th>Code</th><th>Size</th></tr>"
    b"<tr><td>0007</td><td>1.2300</td></tr>"
    b"<tr><td>0008</td><td>0.00</td></tr></table>"
)


def record_response(payload):
    table_ref, table = next(iter(payload["tables"].items()))
    cells = table["cells"]
    if isinstance(cells, dict):
        cells = [dict(zip(cells["columns"], r, strict=True)) for r in cells["rows"]]
    headers = {c["col"]: c["sourceRef"] for c in cells if c.get("isHeader")}
    return {
        "regionId": payload["regionId"],
        "tableKind": "record_table",
        "record": {
            "id": "records",
            "key": "records",
            "label": "Records",
            "tableRef": table_ref,
            "rowStart": 0,
            "rowEnd": max(c["row"] + c.get("rowSpan", 1) - 1 for c in cells),
            "definitionRefs": list(headers.values()),
            "rowRoles": [
                {
                    "row": row,
                    "role": "header" if all(c.get("isHeader") for c in row_cells) else "data",
                }
                for row, row_cells in observed_rows(cells).items()
                if row not in fixed_header_rows({**table, "cells": cells})
            ],
            "columns": [
                {
                    "id": "code",
                    "key": "code",
                    "label": "Code",
                    "column": 0,
                    "valueType": "string",
                    "definitionRefs": [headers[0]],
                },
                {
                    "id": "size",
                    "key": "size",
                    "label": "Size",
                    "column": 1,
                    "valueType": "decimal",
                    "definitionRefs": [headers[1]],
                },
            ],
        },
    }


class TableModel:
    identity = {"adapter": "table-protocol-test", "model": "scripted-not-qualified"}
    max_output_tokens = 9999

    def __init__(self, meaning_error=None, invalid_structure=False, invalid_meaning=False):
        self.requests = []
        self.meaning_error = meaning_error
        self.invalid_structure = invalid_structure
        self.invalid_meaning = invalid_meaning

    def infer(self, request):
        self.requests.append(request)
        payload = json.loads(request.messages[-1]["content"])
        if payload["tableStage"] == "structure":
            value = record_response(payload)
            if self.invalid_structure:
                value["repeats"] = [value["record"]] * 10
        else:
            assert payload["tableStage"] == "meaning"
            assert len(payload["frozenStructure"]["repeats"]) == 1
            if self.meaning_error:
                raise ModelError(self.meaning_error)
            value = {
                "regionId": payload["regionId"],
                "meanings": [],
                "baseRevision": None,
                "changes": [],
                "sourceReviews": [
                    {
                        "sourceRefs": [s["sourceRef"] for s in payload["meaningSources"]],
                        "role": "no_additional_meaning",
                        "explanation": "Plain labels and values; no notes in this scripted fixture",
                    }
                ],
            }
            if self.invalid_meaning:
                value["fields"] = [{"id": "illegal-rewrite"}]
        if payload.get("meaningPhase") == "selection":
            value = {
                "sourceDecisions": {
                    item["sourceRef"]: {
                        "decision": "no_additional_meaning",
                        "explanation": "Scripted plain labels and values",
                    }
                    for item in payload["meaningSources"]
                },
                **({"fields": []} if self.invalid_meaning else {}),
            }
        elif payload["tableStage"] == "meaning":
            value.pop("sourceReviews")
            value["remainderReviews"] = []
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def execute(model, *, restore=None, states=None, additional_budget=None, cancelled=None, **options):
    return extract_schema_from_stream(
        AnalysisJob(job_id="table-stages", input=AnalysisInput.from_bytes(HTML, format_id="html")),
        io.BytesIO(HTML),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        additional_budget=additional_budget,
        cancelled=cancelled,
    )


def fixture():
    doc = observe_document(HTML, "html", {})
    region = prepare_regions(doc, context_chars=16000)[0]
    payload = region_payload(doc, region)
    return doc, region, payload, record_response(payload)


def test_contracts_cannot_generate_repeated_records_or_rewrite_frozen_columns():
    doc, region, payload, value = fixture()
    schema = structure_schema(doc, region)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value)
    assert set(schema["properties"]) == {"regionId", "tableKind", "record"}
    assert "Meaning" not in schema["$defs"]
    _, frozen = structural_ir(value, doc, region)
    wire = meaning_schema(doc, region, frozen)
    Draft202012Validator.check_schema(wire)
    assert {"fields", "repeats", "groups", "record"}.isdisjoint(wire["properties"])
    assert "RepeatLink" not in wire["$defs"]
    for key in ("fields", "repeats", "groups", "data"):
        bad = {"regionId": region["id"], key: []}
        assert not Draft202012Validator(wire).is_valid(bad)
        with pytest.raises(ValidationError):
            TableMeaning.model_validate(bad)
    for kind in ("scalar_form", "unresolved"):
        with pytest.raises(ValidationError):
            TableStructure.model_validate({**value, "tableKind": kind})


@pytest.mark.parametrize("mutation", ["row_range", "duplicate_column", "wrong_table"])
def test_structural_checks_apply_even_when_client_ignores_wire_schema(mutation):
    doc, region, _, value = fixture()
    if mutation == "row_range":
        value["record"]["rowEnd"] = 1
    elif mutation == "duplicate_column":
        value["record"]["columns"][1]["column"] = 0
    else:
        value["record"]["tableRef"] = "unknown"
    with pytest.raises(CompileError):
        structural_ir(value, doc, region)


def test_two_stages_preserve_exact_values_and_account_usage_without_duplicate_outputs():
    model, states = TableModel(), []
    result = execute(model, states=states)
    assert len(model.requests) == 2
    assert result["data"] == {
        "records": [{"code": "0007", "size": "1.2300"}, {"code": "0008", "size": "0.00"}]
    }
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["extraction"]["budget"] == {
        "maxModelCalls": 12,
        "completionSeconds": ExtractionOptions().completionSeconds,
    }
    assert [r.max_output_tokens for r in model.requests] == [STAGE_MAX_OUTPUT_TOKENS, 1536]
    stages = next(iter(result["coverage"]["tableInterpretation"].values()))
    for name in ("structure", "meaning"):
        assert stages[name]["status"] == "complete"
        assert stages[name]["usage"]["modelCalls"] == 1
        assert stages[name]["usage"]["promptTokens"] == 10
        assert stages[name]["usage"]["completionTokens"] == 20
        assert stages[name]["usage"]["unreportedUsageCalls"] == 0
    assert any(
        s["result"]["coverage"]["regions"][0]["status"] == "structure_compiled" for s in states
    )
    assert result["provenance"]["tableProtocolVersion"] == TABLE_PROTOCOL_VERSION
    resumed = execute(model, restore=states[-1])
    assert resumed["extraction"]["status"] == "complete"
    assert len(model.requests) == 2


@pytest.mark.parametrize("mutation", ["header_as_data", "submitted_header", "wrong_data_refs"])
def test_structure_rejects_header_records_and_shifted_data_row_evidence(mutation):
    doc, region, _, value = fixture()
    roles = value["record"]["rowRoles"]
    if mutation == "header_as_data":
        roles.append({"row": 0, "role": "data"})
    elif mutation == "submitted_header":
        roles.append({"row": 0, "role": "header"})
    else:
        roles.append({"row": 1, "role": "data", "sourceRefs": value["record"]["definitionRefs"]})
    with pytest.raises(CompileError, match="table_structure_"):
        structural_ir(value, doc, region)


def test_unbounded_parent_and_column_scope_is_unresolved_not_silently_broadened():
    doc, region, _, value = fixture()
    _, frozen = structural_ir(value, doc, region)
    source = value["record"]["columns"][1]["definitionRefs"]
    meaning = {
        "id": "unit",
        "kind": "unit",
        "description": "size unit",
        "sourceRefs": source,
        "fieldIds": ["size"],
        "repeatIds": ["records"],
    }
    # Public/internal IR retains its defensive legacy overlap validation.
    from document_files.interpretation.semantic_types import Meaning

    ir = frozen.model_copy(deep=True)
    ir.meanings = [Meaning.model_validate(meaning)]
    fragment = compile_region(ir, doc, region)
    detail = next(d for d in fragment.semantic_details if d["id"].endswith(":unit"))
    assert detail["scope"] == []
    assert detail["scopeErrors"] == ["meaning_has_overlapping_scope"]
    assert any(i["code"] == "semantic_scope_unresolved" for i in fragment.issues)
    assert fragment.data["records"][0]["size"] == "1.2300"
    # Explicit row bounds deliberately intersect the selected repeat and columns.
    meaning.update(rowStart=1, rowEnd=1)
    ir.meanings = [Meaning.model_validate(meaning)]
    bounded = compile_region(ir, doc, region)
    detail = next(d for d in bounded.semantic_details if d["id"].endswith(":unit"))
    assert detail["scope"] == [{"space": "data", "path": "/records/0/size"}]
    assert "scopeErrors" not in detail


def test_invalid_structure_is_repaired_before_it_can_be_frozen():
    class ShiftedHeaders(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if len(self.requests) == 1:
                value = json.loads(response.text)
                value["record"]["rowRoles"] = [{"row": 0, "role": "data"}]
                return InferenceResponse(json.dumps(value), response.usage)
            return response

    model, states = ShiftedHeaders(), []
    result = execute(model, states=states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert [json.loads(r.messages[-1]["content"])["tableStage"] for r in model.requests] == [
        "structure",
        "structure",
        "meaning",
    ]
    assert len(result["data"]["records"]) == 2
    assert all(len((s["result"]["data"] or {}).get("records", [])) in {0, 2} for s in states)


@pytest.mark.parametrize("error", ["ai_timeout", "ai_cancelled", "ai_response_incomplete"])
def test_meaning_failure_preserves_structure_and_resumes_only_meaning(error):
    model, states = TableModel(meaning_error=error), []
    partial = execute(model, states=states)
    assert partial["data"]["records"][0]["code"] == "0007"
    assert partial["extraction"]["status"] == "partial"
    assert partial["coverage"]["regions"][0]["status"] == "structure_compiled"
    assert states[-1]["tableStages"]["semantic-region:1"]["structure"]["status"] == "complete"
    model.meaning_error = None
    result = execute(model, restore=states[-1])
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert [json.loads(r.messages[-1]["content"])["tableStage"] for r in model.requests] == [
        "structure",
        "meaning",
        "meaning",
    ]


def test_global_budget_and_pre_dispatch_cancellation_keep_committed_structure():
    model, states = TableModel(), []
    result = execute(model, maxModelCalls=1, states=states)
    assert len(model.requests) == 1
    assert result["data"]["records"]
    assert result["extraction"]["status"] == "partial"
    assert any(i["code"] == "model_call_budget_exceeded" for i in result["issues"])
    resumed = execute(
        model, maxModelCalls=1, restore=states[-1], additional_budget={"maxModelCalls": 1}
    )
    assert resumed["extraction"]["status"] == "complete"
    model, states = TableModel(), []
    stopped = execute(model, states=states, cancelled=lambda: len(model.requests) == 1)
    assert stopped["data"]["records"]
    assert len(model.requests) == 1
    assert any(i["code"] == "ai_cancelled" for i in stopped["issues"])
    assert execute(model, restore=states[-1])["extraction"]["status"] == "complete"


def test_stage_repairs_are_finite_across_resume_and_explicit_grants():
    model, states = TableModel(invalid_meaning=True), []
    result = execute(model, states=states)
    assert len(model.requests) == 3
    assert result["extraction"]["status"] == "partial"
    assert result["data"]["records"]
    execute(model, restore=states[-1])
    assert len(model.requests) == 3
    model.invalid_meaning = False
    resumed = execute(model, restore=states[-1], additional_budget={"maxModelCalls": 1})
    assert resumed["extraction"]["status"] == "complete"
    assert len(model.requests) == 4
    broken, states = TableModel(invalid_structure=True), []
    result = execute(broken, states=states)
    assert len(broken.requests) == 2
    assert result["data"] is None
    execute(broken, restore=states[-1])
    assert len(broken.requests) == 2


def test_prior_protocol_checkpoint_rejected_before_dispatch():
    model, states = TableModel(), []
    execute(model, states=states)
    for mutation in ("version", "protocol", "v10", "v11", "v12", "v13", "v14", "v15"):
        checkpoint = copy.deepcopy(states[-1])
        if mutation == "version":
            checkpoint["version"] = "document-files.regional-checkpoint.v1"
        elif mutation == "protocol":
            checkpoint["identity"].pop("tableProtocolVersion")
        else:
            checkpoint["identity"]["tableProtocolVersion"] = (
                "document-files.table-protocol." + mutation
            )
        with pytest.raises(ValueError, match="incompatible"):
            execute(model, restore=checkpoint)
    assert len(model.requests) == 2


def test_meaning_targets_include_compiled_header_provenance_without_expanded_values():
    doc, region, payload, value = fixture()
    value["record"]["columns"][1]["definitionRefs"] = value["record"]["columns"][0][
        "definitionRefs"
    ]
    _, frozen = structural_ir(value, doc, region)
    compiled = compile_region(frozen, doc, region)
    request = meaning_payload(payload, frozen, compiled, source_inventory(doc, region))
    definition = next(
        d for d in request["frozenStructure"]["repeats"][0]["columns"] if d["id"] == "size"
    )
    assert definition["definitionRefs"] == next(
        d["sourceRefs"] for d in compiled.semantics if d["id"] == region["id"] + ":size"
    )
    assert "compiledDefinitions" not in request["frozenStructure"]
    assert "0007" not in json.dumps(request["frozenStructure"])
    merged = meaning_ir(
        {
            "regionId": region["id"],
            "meanings": [],
            "sourceReviews": [],
            "baseRevision": None,
            "changes": [],
        },
        frozen,
        source_inventory(doc, region),
    )
    assert merged.repeats == frozen.repeats
    assert merged is not frozen


@pytest.mark.parametrize("kind", ["scalar_form", "unresolved"])
def test_scalar_forms_keep_binding_path_and_ambiguous_tables_do_not_expand(kind):
    content = b"<table><tr><td>Code</td><td>0007</td></tr></table>"
    job = AnalysisJob(
        job_id="scalar-table", input=AnalysisInput.from_bytes(content, format_id="html")
    )

    class FormModel:
        identity = {"adapter": "explicit-scalar-form-fixture"}
        calls = 0

        def complete(self, messages, *, timeout):
            self.calls += 1
            payload = json.loads(messages[-1]["content"])
            if payload.get("tableStage") == "structure":
                return json.dumps(
                    {"regionId": payload["regionId"], "tableKind": kind, "record": None}
                )
            assert "tableStage" not in payload
            assert payload["tableKind"] == "scalar_form"
            assert payload["outputContract"]["properties"]["repeats"]["maxItems"] == 0
            label = next(
                ref for ref, node in payload["nodes"].items() if node.get("text") == "Code"
            )
            source = next(
                ref for ref, node in payload["nodes"].items() if node.get("text") == "0007"
            )
            binding = next(
                b
                for b, item in payload["bindings"].items()
                if item["sourceRef"] == source and item["path"] == "/text"
            )
            return json.dumps(
                {
                    "regionId": payload["regionId"],
                    "dispositions": [
                        {"sourceRef": label, "role": "heading", "explanation": "Field label"}
                    ],
                    "excludedBindings": [
                        {"bindingId": bid, "role": "label", "explanation": "Defines Code"}
                        for bid, item in payload["bindings"].items()
                        if item["sourceRef"] == label
                    ],
                    "fields": [
                        {
                            "id": "code",
                            "key": "code",
                            "label": "Code",
                            "valueType": "string",
                            "bindingId": binding,
                            "status": "present",
                            "definitionRefs": [label],
                        }
                    ],
                }
            )

    model, states = FormModel(), []
    kwargs = {"options": ExtractionOptions(reconstructionContext=False), "model_client": model}
    result = extract_schema_from_stream(
        job, io.BytesIO(content), checkpoint=states.append, **kwargs
    )
    if kind == "scalar_form":
        assert model.calls == 2, result["issues"]
        assert result["data"] == {"code": "0007"}
        assert result["extraction"]["status"] == "complete", result["issues"]
    else:
        assert model.calls == 1
        assert result["data"] is None
        assert result["extraction"]["status"] == "partial"
        assert any(i["code"] == "table_kind_unresolved" for i in result["issues"])
    before = model.calls
    restored = extract_schema_from_stream(job, io.BytesIO(content), restore=states[-1], **kwargs)
    assert model.calls == before
    assert restored["data"] == result["data"]
    assert restored["extraction"]["status"] == result["extraction"]["status"]


def sparse_fixture():
    content = (
        b"<table><tr><th>Code</th><th>Qty</th><th>Price</th><th>Note</th></tr>"
        b"<tr><td>0007</td><td>2</td><td>1.2300</td></tr>"
        b"<tr><td>0008</td><td>3</td><td>0.00</td><td>Checked</td></tr></table>"
    )
    doc = observe_document(content, "html", {})
    region = prepare_regions(doc, context_chars=20000)[0]
    table = doc.tables[region["tableRef"]]
    # Simulate sequential OCR cell IDs: the absent row-1 Note has no ID at all.
    for index, cell in enumerate(table["cells"]):
        old_ref, new_ref = cell["sourceRef"], f"scan:table/cell/{index}"
        doc.nodes[new_ref] = doc.nodes.pop(old_ref)
        region["nodeIds"] = [new_ref if ref == old_ref else ref for ref in region["nodeIds"]]
        for binding in doc.bindings.values():
            if binding["sourceRef"] == old_ref:
                binding["sourceRef"] = new_ref
        cell["sourceRef"] = new_ref
    payload = region_payload(doc, region)
    value = record_response(payload)
    value["record"]["columns"] = [
        {
            "id": key,
            "key": key,
            "label": key.title(),
            "column": index,
            "valueType": kind,
            "definitionRefs": [f"scan:table/cell/{index}"],
        }
        for index, (key, kind) in enumerate(
            [("code", "string"), ("qty", "integer"), ("price", "decimal"), ("note", "string")]
        )
    ]

    return doc, region, payload, value


def test_row_candidates_keep_sparse_geometry_and_program_attaches_only_actual_cells():
    doc, region, payload, value = sparse_fixture()
    original = copy.deepcopy((doc.tables, doc.nodes, payload, value))
    wire = structure_schema(doc, region)
    Draft202012Validator(wire).validate(value)
    assert set(wire["$defs"]["RowDecision"]["properties"]) == {"row", "role"}
    candidates = structure_payload(payload)["tables"][region["tableRef"]]["rowCandidates"]
    assert candidates["cellColumns"] == ["column", "columnSpan", "sourceRef", "text"]
    row1, row2 = candidates["rows"][1:]
    assert row1["row"] == 1
    assert row1["cells"] == [
        [0, 1, "scan:table/cell/4", "0007"],
        [1, 1, "scan:table/cell/5", "2"],
        [2, 1, "scan:table/cell/6", "1.2300"],
    ]
    assert row2["cells"][0] == [0, 1, "scan:table/cell/7", "0008"]
    _, frozen = structural_ir(value, doc, region)
    assert frozen.repeats[0].rowRoles[1].sourceRefs == [f"scan:table/cell/{i}" for i in (4, 5, 6)]
    assert frozen.repeats[0].rowRoles[2].sourceRefs == [
        f"scan:table/cell/{i}" for i in (7, 8, 9, 10)
    ]
    compiled = compile_region(frozen, doc, region)
    assert compiled.data["records"] == [
        {"code": "0007", "qty": 2, "price": "1.2300", "note": None},
        {"code": "0008", "qty": 3, "price": "0.00", "note": "Checked"},
    ]
    # Null here retains the existing compiler's explicit missing-value contract;
    # stage one did not create a cell, a binding, or a synthetic blank string.
    assert len(doc.tables[region["tableRef"]]["cells"]) == 11
    assert (doc.tables, doc.nodes, payload, value) == original
    assert (
        "rowCandidates"
        not in meaning_payload(payload, frozen, compiled, source_inventory(doc, region))["tables"][
            region["tableRef"]
        ]
    )


@pytest.mark.parametrize("role", ["header", "data", "subtotal", "note", "blank", "unresolved"])
def test_stage_one_rejects_supplied_row_sources_even_when_the_refs_would_be_valid(role):
    doc, region, _, value = fixture()
    value["record"]["rowRoles"] = [
        {"row": 0, "role": role, "sourceRefs": value["record"]["definitionRefs"]}
    ]
    assert not Draft202012Validator(structure_schema(doc, region)).is_valid(value)
    with pytest.raises(CompileError, match="sources_are_program_derived"):
        structural_ir(value, doc, region)


def test_row_spanning_cells_attach_to_each_intersected_row_without_shifting_column():
    doc, region, payload, value = sparse_fixture()
    cells = doc.tables[region["tableRef"]]["cells"]
    cells[4]["rowSpan"] = 2
    del cells[7]  # Original row-2 code now covered by the observed row span.
    payload = region_payload(doc, region)
    request = structure_payload(payload)
    candidates = request["tables"][region["tableRef"]]["rowCandidates"]["rows"]
    assert candidates[2]["row"] == 2
    assert candidates[2]["cells"][0] == [0, 1, "scan:table/cell/4", "0007"]
    _, frozen = structural_ir(value, doc, region)
    assert frozen.repeats[0].rowRoles[2].sourceRefs == [
        "scan:table/cell/4",
        "scan:table/cell/8",
        "scan:table/cell/9",
        "scan:table/cell/10",
    ]


@pytest.mark.parametrize("mutation", ["duplicate", "outside", "unobserved"])
def test_row_decisions_cannot_invent_or_repeat_observation_geometry(mutation):
    doc, region, _, value = fixture()
    if mutation == "duplicate":
        value["record"]["rowRoles"] = [{"row": 1, "role": "note"}] * 2
    elif mutation == "outside":
        value["record"]["rowRoles"].append({"row": 99, "role": "blank"})
    else:
        doc.tables[region["tableRef"]]["cells"] = [
            cell for cell in doc.tables[region["tableRef"]]["cells"] if cell["row"] != 1
        ]
        value["record"]["rowRoles"].append({"row": 1, "role": "blank"})
    with pytest.raises(CompileError, match="row_roles|no_observed_cells"):
        structural_ir(value, doc, region)


def test_v2_protocol_checkpoint_rejected_without_model_call():
    model, states = TableModel(), []
    execute(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    checkpoint["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v2"
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=checkpoint)
    assert len(model.requests) == 2


def test_declared_header_rows_are_program_roles_not_model_choices():
    doc, region, payload, value = fixture()
    assert all(r["role"] == "data" for r in value["record"]["rowRoles"])
    request = structure_payload(payload)
    candidates = request["tables"][region["tableRef"]]["rowCandidates"]["rows"]
    assert candidates[0]["fixedRole"] == "header"
    assert all("fixedRole" not in item for item in candidates[1:])
    schema = structure_schema(doc, region)
    assert schema["$defs"]["RowDecision"]["properties"]["row"]["enum"] == [1, 2]
    _, frozen = structural_ir(value, doc, region)
    assert [(r.row, r.role) for r in frozen.repeats[0].rowRoles] == [
        (0, "header"),
        (1, "data"),
        (2, "data"),
    ]
    assert frozen.repeats[0].rowRoles[0].sourceRefs == value["record"]["definitionRefs"]
    assert (frozen.repeats[0].rowStart, frozen.repeats[0].rowEnd) == (0, 2)
    model = TableModel()
    result = execute(model)
    assert len(model.requests) == 2  # No structural correction to rediscover headers.
    assert len(result["data"]["records"]) == 2


@pytest.mark.parametrize("state", [False, None, "mixed"])
def test_unknown_ocr_and_mixed_rows_are_not_automatically_headers(state):
    doc, region, _, value = fixture()
    cells = doc.tables[region["tableRef"]]["cells"]
    headers = [cell for cell in cells if cell["row"] == 0]
    for index, cell in enumerate(headers):
        if state == "mixed" and index == 0:
            continue
        if state is None:
            cell.pop("isHeader", None)
        else:
            cell["isHeader"] = False
    payload = region_payload(doc, region)
    candidate = structure_payload(payload)["tables"][region["tableRef"]]["rowCandidates"]["rows"][0]
    assert "fixedRole" not in candidate
    schema = structure_schema(doc, region)
    assert 0 in schema["$defs"]["RowDecision"]["properties"]["row"]["enum"]
    # Even mapping only the header-marked column cannot promote a mixed row.
    if state == "mixed":
        value["record"]["columns"] = value["record"]["columns"][:1]
    with pytest.raises(CompileError, match="row_roles_incomplete"):
        structural_ir(value, doc, region)
    value["record"]["rowRoles"].insert(0, {"row": 0, "role": "header"})
    _, frozen = structural_ir(value, doc, region)
    assert frozen.repeats[0].rowRoles[0].role == "header"  # Explicit AI decision remains allowed.


def test_declared_multilevel_rowspan_headers_keep_actual_sources_and_full_range():
    content = (
        b'<table><tr><th rowspan="2">Code</th><th>Measurement</th></tr>'
        b"<tr><th>Size</th></tr><tr><td>0007</td><td>1.2300</td></tr></table>"
    )
    doc = observe_document(content, "html", {})
    before = copy.deepcopy(doc.tables)
    region = prepare_regions(doc, context_chars=20000)[0]
    payload = region_payload(doc, region)
    value = record_response(payload)
    assert all(r["role"] == "data" for r in value["record"]["rowRoles"])
    _, frozen = structural_ir(value, doc, region)
    roles = frozen.repeats[0].rowRoles
    assert [(role.row, role.role) for role in roles] == [(0, "header"), (1, "header"), (2, "data")]
    cells = doc.tables[region["tableRef"]]["cells"]
    spanning = next(cell["sourceRef"] for cell in cells if cell.get("rowSpan") == 2)
    assert spanning in roles[0].sourceRefs and spanning in roles[1].sourceRefs
    assert (frozen.repeats[0].rowStart, frozen.repeats[0].rowEnd) == (0, 2)
    assert compile_region(frozen, doc, region).data["records"] == [
        {"code": "0007", "size": "1.2300"}
    ]
    assert doc.tables == before


def test_v3_row_decision_checkpoint_is_rejected_before_dispatch():
    model, states = TableModel(), []
    execute(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    checkpoint["identity"]["tableProtocolVersion"] = "document-files.table-protocol.v3"
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=checkpoint)
    assert len(model.requests) == 2


@pytest.mark.parametrize("roles", [None, [], [{"row": 1, "role": "data"}]])
def test_every_nonfixed_observed_row_requires_an_explicit_decision(roles):
    doc, region, _, value = fixture()
    if roles is None:
        value["record"].pop("rowRoles")
    else:
        value["record"]["rowRoles"] = roles
    assert not Draft202012Validator(structure_schema(doc, region)).is_valid(value)
    with pytest.raises((ValidationError, CompileError)):
        structural_ir(value, doc, region)


def test_positive_ocr_header_flags_are_predictions_not_fixed_roles_or_excluded_values():
    doc = observe_document(HTML, "html", {})
    table = next(iter(doc.tables.values()))
    table["basis"] = "docling_table_cells"
    region = prepare_regions(doc, context_chars=20000)[0]
    payload = region_payload(doc, region)
    request = structure_payload(payload)
    assert all(
        "fixedRole" not in r for r in request["tables"][table["id"]]["rowCandidates"]["rows"]
    )
    header_refs = {c["sourceRef"] for c in table["cells"] if c["isHeader"]}
    assert header_refs <= {doc.bindings[bid]["sourceRef"] for bid in region["bindingIds"]}
    value = record_response(payload)
    assert value["record"]["rowRoles"][0] == {"row": 0, "role": "header"}
    _, frozen = structural_ir(value, doc, region)
    fragment = compile_region(frozen, doc, region)
    assert len(fragment.data["records"]) == 2
    assert not fragment.issues


def test_unknown_rows_and_geometry_holes_never_become_records():
    doc, region, _, value = fixture()
    _, frozen = structural_ir(value, doc, region)
    frozen.repeats[0].rowRoles = frozen.repeats[0].rowRoles[:1]
    fragment = compile_region(frozen, doc, region)
    assert fragment.data == {"records": []}
    assert any(i["code"] == "repeat_row_roles_incomplete" for i in fragment.issues)
    assert fragment.value_evidence[-1]["status"] == "uncertain"
    assert fragment.value_evidence[-1]["transformation"] == "unresolved_repeat_rows"
    # Missing observed row 1 is not a blank row; only 0 and 2 are actual geometry.
    doc.tables[region["tableRef"]]["cells"] = [
        c for c in doc.tables[region["tableRef"]]["cells"] if c["row"] != 1
    ]
    value["record"]["rowRoles"] = [{"row": 2, "role": "data"}]
    _, frozen = structural_ir(value, doc, region)
    assert compile_region(frozen, doc, region).data["records"] == [{"code": "0008", "size": "0.00"}]


@pytest.mark.parametrize("raw", ["Size", "1,23", "12.5 mm", "NaN", "Infinity"])
def test_decimal_type_conflicts_preserve_original_and_remain_unresolved(raw):
    doc = observe_document(HTML.replace(b"1.2300", raw.encode()), "html", {})
    region = prepare_regions(doc, context_chars=20000)[0]
    value = record_response(region_payload(doc, region))
    ref = next(
        c["sourceRef"]
        for c in doc.tables[region["tableRef"]]["cells"]
        if c["row"] == 1 and c["col"] == 1
    )
    _, frozen = structural_ir(value, doc, region)
    fragment = compile_region(frozen, doc, region)
    assert fragment.data["records"][0]["size"] is None
    ev = next(e for e in fragment.value_evidence if e["target"]["path"] == "/records/0/size")
    assert ev["raw"] == raw and ev["status"] == "uncertain" and ev["binding"] is None
    assert doc.nodes[ref]["text"] == raw
    assert any(i["code"] == "decimal_format_unresolved" for i in fragment.issues)


NONRECORD_HTML = (
    b"<table><tr><th>Code</th><th>Size</th></tr>"
    b"<tr><td>0007</td><td>1.2300</td></tr>"
    b"<tr><td>0008</td><td>0.00</td></tr>"
    b"<tr><td>Total</td><td>1.2300</td></tr>"
    b'<tr><td colspan="2">Checked after cooling</td></tr></table>'
)


class NonrecordModel(TableModel):
    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("tableKind") == "nonrecord_values":
            self.requests.append(request)
            assert "RepeatLink" not in request.output_schema.get("$defs", {})
            assert payload["valueRegion"]["parentRegionId"] != payload["regionId"]
            # The scalar region's table view holds only the cells it owns or sees.
            cells = next(iter(payload["tables"].values()))["cells"]
            if isinstance(cells, dict):
                cells = [dict(zip(cells["columns"], row, strict=True)) for row in cells["rows"]]
            visible = set(payload["nodeIds"]) | set(payload["contextNodeIds"])
            assert cells and {c["sourceRef"] for c in cells} <= visible
            assert all(
                "semanticInput" not in n or "basis" not in n["semanticInput"]
                for n in payload["nodes"].values()
                if "sourceStructure" not in n
            )
            value = {
                "regionId": payload["regionId"],
                "fields": [
                    {
                        "id": f"extra{index}",
                        "key": f"extra{index}",
                        "label": "Observed extra value",
                        "bindingId": bid,
                        "definitionRefs": [binding["sourceRef"]],
                        "valueType": "string",
                    }
                    for index, (bid, binding) in enumerate(payload["bindings"].items())
                ],
            }
            return InferenceResponse(json.dumps(value), {})
        response = super().infer(request)
        if payload.get("tableStage") == "structure":
            value = json.loads(response.text)
            for role in value["record"]["rowRoles"]:
                if role["row"] >= 3:
                    role["role"] = "subtotal" if role["row"] == 3 else "note"
            response = InferenceResponse(json.dumps(value), response.usage)
        return response


def execute_nonrecord(model, states, *, restore=None, additional_budget=None, **options):
    return extract_schema_from_stream(
        AnalysisJob(
            job_id="nonrecord", input=AnalysisInput.from_bytes(NONRECORD_HTML, format_id="html")
        ),
        io.BytesIO(NONRECORD_HTML),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, **options),
        checkpoint=states.append,
        restore=restore,
        additional_budget=additional_budget,
    )


def test_subtotal_and_note_values_use_scalar_region_and_retain_exact_records_and_sources():
    model, states = NonrecordModel(), []
    result = execute_nonrecord(model, states)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(model.requests) == 3
    assert result["data"] == {
        "records": [{"code": "0007", "size": "1.2300"}, {"code": "0008", "size": "0.00"}],
        "extra0": "Total",
        "extra1": "1.2300",
        "extra2": "Checked after cooling",
    }
    parent, child = states[-1]["regions"]
    assert not set(parent["nodeIds"]) & set(child["nodeIds"])
    assert not set(parent["bindingIds"]) & set(child["bindingIds"])
    assert set(child["nodeIds"]) <= set(parent["contextNodeIds"])
    # The scalar region sees the column headers and the surrounding text, never the
    # data cells already compiled into the record.
    cells = result["document"]["structure"]["tables"][parent["tableRef"]]["cells"]
    header_refs = {c["sourceRef"] for c in cells if c["row"] == 0}
    data_refs = {c["sourceRef"] for c in cells if c["row"] in (1, 2)}
    assert header_refs <= set(child["contextNodeIds"])
    assert not data_refs & set(child["contextNodeIds"])
    assert len(result["document"]["structure"]["tables"][parent["tableRef"]]["cells"]) == 9
    routes = states[-1]["tableStages"][parent["id"]]["structure"]["valueRoutes"]
    assert sum(r["valueRoute"] == "scalar_region" for r in routes) == 3
    resumed = execute_nonrecord(model, [], restore=states[-1])
    assert resumed["data"] == result["data"] and len(model.requests) == 3


def test_nonrecord_pending_budget_and_resume_do_not_repeat_or_erase_table_structure():
    model, states = NonrecordModel(), []
    partial = execute_nonrecord(model, states, maxModelCalls=2)
    assert partial["extraction"]["status"] == "partial"
    assert len(partial["data"]["records"]) == 2 and len(model.requests) == 2
    assert partial["coverage"]["unprocessedRegions"] == [states[-1]["regions"][1]["id"]]
    result = execute_nonrecord(
        model,
        [],
        restore=states[-1],
        maxModelCalls=2,
        additional_budget={"maxModelCalls": 1, "completionSeconds": 60},
    )
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert len(model.requests) == 3


def test_source_review_does_not_hide_an_explicit_unsupported_disposition():
    doc, region, _, value = fixture()
    _, frozen = structural_ir(value, doc, region)
    inventory = source_inventory(doc, region)
    ref = inventory["sources"][0]["sourceRef"]
    candidate = meaning_ir(
        {
            "regionId": region["id"],
            "meanings": [],
            "baseRevision": None,
            "changes": [],
            "sourceReviews": [
                {
                    "sourceRefs": [s["sourceRef"] for s in inventory["sources"]],
                    "role": "no_additional_meaning",
                    "explanation": "Scripted source review",
                }
            ],
            "dispositions": [
                {
                    "sourceRef": ref,
                    "role": "unsupported",
                    "explanation": "Explicit conflict",
                }
            ],
        },
        frozen,
        inventory,
    )
    fragment = compile_region(candidate, doc, region)
    assert {"code": "node_semantics_unsupported", "sourceRef": ref} in fragment.issues
    assert not fragment.meaning_review["unreviewed"]


def test_meaning_model_view_keeps_sources_roles_and_canonical_provenance_unchanged():
    doc, region, payload, value = fixture()
    _, frozen = structural_ir(value, doc, region)
    compiled = compile_region(frozen, doc, region)
    before = copy.deepcopy((payload, frozen.model_dump(), compiled.semantics))
    inventory = source_inventory(doc, region)
    view = meaning_payload(payload, frozen, compiled, inventory)
    record = view["frozenStructure"]["repeats"][0]
    assert view["meaningSources"] == [
        {"sourceRef": item["sourceRef"], "text": item["text"]} for item in inventory["sources"]
    ]
    assert view["sourceInventorySHA256"] == inventory["sha256"]
    assert record["rowRoles"] == [
        {"row": r.row, "role": r.role} for r in frozen.repeats[0].rowRoles
    ]
    # This header-only subset is not redundant all-cell record provenance.
    assert record["definitionRefs"] == frozen.repeats[0].definitionRefs
    assert (payload, frozen.model_dump(), compiled.semantics) == before


@pytest.mark.parametrize(
    "kind,raw,value",
    [
        ("text", "same", "same"),
        ("text", "different", "same"),
        ("decimal", "same", "same"),
        ("text", None, "same"),
    ],
)
def test_context_compaction_only_removes_exact_text_copies(kind, raw, value):
    from document_files.interpretation.table_protocol import _meaning_context_node

    node = {
        "text": "same",
        "sourceStructure": {"page": 1, "bbox": [1, 2, 3, 4]},
        "semantic": {
            "value": {
                "kind": kind,
                "raw": raw,
                "value": value,
                "basis": "observed",
                "uncertain": True,
            }
        },
        "semanticInput": {"role": "context_only", "conflicts": ["retained"]},
    }
    before = copy.deepcopy(node)
    result = _meaning_context_node(node)
    expected = copy.deepcopy(node)
    if kind == "text":
        for key in ("raw", "value"):
            if expected["semantic"]["value"][key] == "same":
                expected["semantic"]["value"].pop(key)
    assert result == expected
    assert node == before


def test_meaning_preflight_stops_before_dispatch_and_preserves_structure():
    class Limited(TableModel):
        def infer(self, request):
            response = super().infer(request)
            if len(self.requests) == 1:
                self.input_budget_chars = 1
            return response

    model, states = Limited(), []
    result = execute(model, states=states)
    assert len(model.requests) == 1
    assert result["data"]["records"][0] == {"code": "0007", "size": "1.2300"}
    meaning = next(iter(states[-1]["tableStages"].values()))["meaning"]
    assert meaning["attempts"] == meaning["usage"]["modelCalls"] == 0
    assert meaning["inputPreflight"]["stage"] == "meaning"
    assert meaning["inputPreflight"]["phase"] == "initial"
    assert meaning["inputPreflight"]["withinBudget"] is False
    assert meaning["inputPreflight"]["limitCharacters"] == 1
    assert "referenceWire" in meaning


def test_resume_never_adds_the_regions_own_table_mapping():
    model, states = TableModel(meaning_error="ai_request_failed"), []
    execute(model, states=states)
    first = json.loads(model.requests[-1].messages[-1]["content"])
    assert "sameTableMapping" not in first
    model.meaning_error = None
    execute(model, restore=states[-1])
    resumed = json.loads(model.requests[-1].messages[-1]["content"])
    assert "sameTableMapping" not in resumed
    assert first == resumed


@pytest.mark.parametrize("mutation", ["missing", "dictionary", "version"])
def test_completed_meaning_checkpoint_rejects_wire_identity_tampering(mutation):
    model, states = TableModel(), []
    execute(model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    meaning = next(iter(checkpoint["tableStages"].values()))["meaning"]
    if mutation == "missing":
        meaning.pop("referenceWire")
    elif meaning["referenceWire"] is None:
        meaning["referenceWire"] = {"version": "invalid", "dictionary": {}}
    elif mutation == "dictionary":
        meaning["referenceWire"]["dictionary"]["sources"]["@s0"] = "wrong"
    else:
        meaning["referenceWire"]["version"] = "old"
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, restore=checkpoint)
    assert len(model.requests) == 2


def test_wire_preparation_failure_does_not_loop_or_discard_structure(monkeypatch):
    from document_files.interpretation import engine

    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise ValueError("do not repeat preparation or expose this detail")

    monkeypatch.setattr(engine, "prepare_meaning_wire", broken)
    model, states = TableModel(), []
    result = execute(model, states=states)
    assert calls == [1]
    assert len(model.requests) == 1
    assert result["data"]["records"][0] == {"code": "0007", "size": "1.2300"}
    assert result["extraction"]["stage"] == "paused"
    assert any(i["code"] == "table_reference_wire_preparation_failed" for i in result["issues"])
    assert next(iter(states[-1]["tableStages"].values()))["meaning"]["attempts"] == 0


def test_model_view_removes_only_geometry_reproduced_provenance():
    doc, region, payload, value = fixture()
    _, frozen = structural_ir(value, doc, region)
    repeat = frozen.repeats[0]
    repeat.definitionRefs = [c["sourceRef"] for c in doc.tables[repeat.tableRef]["cells"]]
    compiled = compile_region(frozen, doc, region)
    # Exercise the view's defensive branch in isolation. The compiler rejects
    # this selective row provenance; it is not an accepted engine structure.
    repeat.rowRoles[1].sourceRefs = repeat.rowRoles[1].sourceRefs[:1]
    payload["sameTableMapping"] = {"instruction": "Structure-only reuse guide"}
    view = meaning_payload(payload, frozen, compiled, source_inventory(doc, region))
    assert "definitionRefs" not in view["frozenStructure"]["repeats"][0]
    assert (
        view["frozenStructure"]["repeats"][0]["rowRoles"][1]["sourceRefs"]
        == repeat.rowRoles[1].sourceRefs
    )
    assert "sameTableMapping" not in view
    assert payload["sameTableMapping"]
    assert repeat.definitionRefs


def test_active_completed_checkpoint_rejects_disabled_wire_and_preserves_canonical_refs(
    monkeypatch,
):
    from dataclasses import asdict

    from document_files.document_model.model import ObservationDocument
    from document_files.interpretation import engine

    observe = engine.observe_document
    prefix = "source-bound-recognition-node-with-a-long-document-page-and-table-identifier/"

    def long_refs(*args, **kwargs):
        doc = observe(*args, **kwargs)
        mapping = {ref: prefix + ref for ref in doc.nodes}

        def rewrite(value):
            if isinstance(value, dict):
                return {mapping.get(k, k): rewrite(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rewrite(v) for v in value]
            return mapping.get(value, value) if isinstance(value, str) else value

        return ObservationDocument(**rewrite(asdict(doc)))

    monkeypatch.setattr(engine, "observe_document", long_refs)
    model, states = TableModel(), []
    result = execute(model, states=states, contextChars=100000)
    checkpoint = copy.deepcopy(states[-1])
    meaning = next(iter(checkpoint["tableStages"].values()))["meaning"]
    assert meaning["status"] == "complete" and meaning["referenceWire"] is not None
    meaning_payload_wire = json.loads(model.requests[-1].messages[-1]["content"])
    assert meaning_payload_wire["meaningSources"][0]["sourceRef"].startswith("@s")
    ir = next(iter(checkpoint["accepted"].values()))
    assert all(
        ref.startswith(prefix)
        for review in ir["tableMeaningState"]["sourceReviews"]
        for ref in review["sourceRefs"]
    )
    assert result["data"]["records"][0] == {"code": "0007", "size": "1.2300"}
    # An intact completed checkpoint must resume without another inference.
    execute(model, restore=states[-1], contextChars=100000)
    meaning["referenceWire"] = None
    with pytest.raises(ValueError, match="incompatible with table reference wire"):
        execute(model, restore=checkpoint, contextChars=100000)
    assert len(model.requests) == 2
