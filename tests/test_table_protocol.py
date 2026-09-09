"""Scripted finite protocol tests, not actual model quality qualification."""

import copy
import io
import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.observe import observe_document
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
            "rowEnd": 2,
            "definitionRefs": list(headers.values()),
            "rowRoles": [],
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
            value = {"regionId": payload["regionId"]}
            if self.invalid_meaning:
                value["fields"] = [{"id": "illegal-rewrite"}]
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
    assert all(r.max_output_tokens == STAGE_MAX_OUTPUT_TOKENS for r in model.requests)
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
    for mutation in ("version", "protocol"):
        checkpoint = copy.deepcopy(states[-1])
        if mutation == "version":
            checkpoint["version"] = "document-files.regional-checkpoint.v1"
        else:
            checkpoint["identity"].pop("tableProtocolVersion")
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
    request = meaning_payload(payload, frozen, compiled)
    definition = next(
        d for d in request["frozenStructure"]["compiledDefinitions"] if d["id"] == "size"
    )
    assert definition["definitionRefs"]
    assert "0007" not in json.dumps(request["frozenStructure"])
    merged = meaning_ir({"regionId": region["id"]}, frozen)
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
    value["record"]["rowRoles"] += [{"row": 1, "role": "data"}, {"row": 2, "role": "data"}]
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
        not in meaning_payload(payload, frozen, compiled)["tables"][region["tableRef"]]
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
    assert value["record"]["rowRoles"] == []
    request = structure_payload(payload)
    candidates = request["tables"][region["tableRef"]]["rowCandidates"]["rows"]
    assert candidates[0]["fixedRole"] == "header"
    assert all("fixedRole" not in item for item in candidates[1:])
    schema = structure_schema(doc, region)
    assert schema["$defs"]["RowDecision"]["properties"]["row"]["enum"] == [1, 2]
    _, frozen = structural_ir(value, doc, region)
    assert [(r.row, r.role) for r in frozen.repeats[0].rowRoles] == [(0, "header")]
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
    _, frozen = structural_ir(value, doc, region)
    assert frozen.repeats[0].rowRoles == []
    value["record"]["rowRoles"] = [{"row": 0, "role": "header"}]
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
    assert value["record"]["rowRoles"] == []
    _, frozen = structural_ir(value, doc, region)
    roles = frozen.repeats[0].rowRoles
    assert [(role.row, role.role) for role in roles] == [(0, "header"), (1, "header")]
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
