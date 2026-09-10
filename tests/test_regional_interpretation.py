"""Contract/compiler regression tests, not actual model quality qualification."""

import io
import json

import pytest

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.model import ObservationDocument
from document_files.document_model.observe import observe_document
from document_files.interpretation.compiler import CompileError, combine_regions, compile_region
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.semantic_types import RegionInterpretation, region_output_schema


@pytest.mark.parametrize("feedback", [None, {}, {"issues": ["unresolved"]}])
def test_contract_messages_match_product_assembly_and_preserve_inputs(feedback):
    import copy

    from document_files.interpretation.legacy_engine import contract_messages, encode

    payload = {"source": "원문 @column0", "nested": {"outputContract": "literal source"}}
    contract = {"type": "object", "properties": {"value": {"type": "string"}}}
    original = copy.deepcopy((payload, contract, feedback))
    expected = {**payload, "outputContract": contract}
    if feedback is not None:
        expected["repairFeedback"] = feedback
    messages = contract_messages("System", payload, contract, feedback)
    assert messages == [
        {"role": "system", "content": "System"},
        {"role": "user", "content": encode(expected)},
    ]
    assert (payload, contract, feedback) == original
    if feedback is not None:
        assert list(json.loads(messages[1]["content"]))[-1] == "repairFeedback"


def test_contract_messages_do_not_hide_nonfinite_values():
    from document_files.interpretation.legacy_engine import contract_messages

    with pytest.raises(ValueError):
        contract_messages("System", {"value": float("nan")}, {})


class ReferenceModel:
    identity = {"adapter": "reference-protocol-test", "model": "not-a-real-model"}

    def __init__(self, *, fail=False, invalid=False):
        self.calls = 0
        self.messages = []
        self.fail = fail
        self.invalid = invalid

    def complete(self, messages, *, timeout):
        self.calls += 1
        self.messages.append(messages)
        if self.fail:
            from document_files.interpretation.backends import ModelError

            raise ModelError("ai_timeout")
        payload = json.loads(messages[-1]["content"])
        fields = []
        for bid, binding in payload["bindings"].items():
            if binding.get("candidateRole") != "value":
                continue
            label_binding = payload["bindings"][binding["labelRefs"][0]]
            text = payload["nodes"][label_binding["sourceRef"]]["text"]
            label = text[label_binding["start"] : label_binding["end"]]
            fields.append(
                {
                    "id": bid,
                    "key": label,
                    "label": label,
                    "bindingId": "unknown" if self.invalid else bid,
                    "valueType": "string",
                    "status": "blank" if binding.get("blank") else "present",
                    "definitionRefs": [label_binding["sourceRef"]],
                }
            )
        return json.dumps(
            {
                "regionId": payload["regionId"],
                "fields": fields,
                "dispositions": [
                    {"sourceRef": ref, "role": "data", "explanation": "Referenced fields"}
                    for ref in payload["nodeIds"]
                ],
            }
        )


def run(
    content=b"label: value\n",
    model=None,
    restore=None,
    checkpoint=None,
    additional_budget=None,
    **options,
):
    return extract_schema_from_stream(
        AnalysisJob(job_id="regional", input=AnalysisInput.from_bytes(content, format_id="txt")),
        io.BytesIO(content),
        options=ExtractionOptions(reconstructionContext=False, **options),
        model_client=model or ReferenceModel(),
        restore=restore,
        checkpoint=checkpoint,
        additional_budget=additional_budget,
    )


def test_default_protocol_program_reads_duplicate_zero_false_and_blank():
    model = ReferenceModel()
    result = run(
        b"first: 0\nsecond: 0\nflag: false\nblank: \nprecise: 1.234567890123456789\n", model
    )
    assert result["data"] == {
        "first": "0",
        "second": "0",
        "flag": "false",
        "blank": "",
        "precise": "1.234567890123456789",
    }
    assert result["validation"]["valid"], result["validation"]
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert model.calls == 1
    assert all(item["binding"] for item in result["valueEvidence"])
    assert (
        result["valueEvidence"][0]["binding"]["sourceRef"]
        != result["valueEvidence"][1]["binding"]["sourceRef"]
    )
    payload = json.loads(model.messages[0][-1]["content"])
    assert "proposal" not in payload and "previousProposal" not in payload
    assert "data" not in payload["outputContract"]["properties"]


class BindingOnlyModel(ReferenceModel):
    def complete(self, messages, *, timeout):
        value = json.loads(super().complete(messages, timeout=timeout))
        value["dispositions"] = []
        return json.dumps(value)


def test_bound_label_value_lines_do_not_need_duplicate_model_bookkeeping():
    model = BindingOnlyModel()
    content = (
        "식별자 / Identifier: 00012345678901234567890\n"
        "수량 / Count: 0\n활성 / Enabled: false\n빈 값: \n"
    ).encode()
    result = run(content, model)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert model.calls == 1
    accounting = result["coverage"]["semanticAccounting"]
    assert len(accounting) == 4
    assert all(item["basis"] == "program_derived" for item in accounting)
    assert all(item["bindingIds"] for item in accounting)
    assert result["data"]["식별자 / Identifier"] == "00012345678901234567890"


def test_bound_text_does_not_silently_account_for_an_uncovered_condition():
    result = run(b"count: 0; only when enabled\n", BindingOnlyModel())
    assert result["data"] == {"count": "0"}
    assert result["extraction"]["status"] == "partial"
    assert any(issue["code"] == "node_semantics_unaccounted" for issue in result["issues"])


def test_explicit_uncertainty_takes_precedence_over_mechanical_accounting():
    class UncertainModel(ReferenceModel):
        def complete(self, messages, *, timeout):
            value = json.loads(super().complete(messages, timeout=timeout))
            value["dispositions"][0]["role"] = "unresolved"
            return json.dumps(value)

    result = run(model=UncertainModel())
    assert result["extraction"]["status"] == "partial"
    assert result["coverage"]["semanticAccounting"][0]["role"] == "unresolved"


def test_unconsumed_pair_in_same_line_is_not_accounted_by_a_neighbor():
    class MissingFieldModel(BindingOnlyModel):
        def complete(self, messages, *, timeout):
            value = json.loads(super().complete(messages, timeout=timeout))
            value["fields"] = value["fields"][:1]
            return json.dumps(value)

    result = run(b"first: 0; second: false\n", MissingFieldModel())
    assert result["data"] == {"first": "0"}
    assert result["extraction"]["status"] == "partial"
    assert {item["code"] for item in result["issues"]} >= {
        "node_semantics_unaccounted",
        "value_candidate_unaccounted",
    }


def test_partial_checkpoint_is_retained_and_budget_is_cumulative():
    snapshots = []
    first = run(model=ReferenceModel(fail=True), maxModelCalls=1, checkpoint=snapshots.append)
    assert first["data"] is None and snapshots
    restored = run(maxModelCalls=1, restore=snapshots[-1])
    assert restored["extraction"]["modelCalls"] == 1
    assert any(i["code"] == "model_call_budget_exceeded" for i in restored["issues"])
    resumed = run(maxModelCalls=1, restore=snapshots[-1], additional_budget={"maxModelCalls": 1})
    assert resumed["data"] == {"label": "value"}
    assert resumed["extraction"]["modelCalls"] == 2


def test_resume_does_not_reparse_or_reobserve(monkeypatch):
    states = []
    run(model=ReferenceModel(fail=True), checkpoint=states.append)

    def forbidden(*args, **kwargs):
        raise AssertionError("committed observations must not be rerun")

    monkeypatch.setattr("document_files.interpretation.engine.analyze_document", forbidden)
    monkeypatch.setattr("document_files.interpretation.engine.observe_document", forbidden)
    result = run(restore=states[-1])
    assert result["data"] == {"label": "value"}


def test_resume_checks_source_model_options_and_checkpoint_version():
    states = []
    run(model=ReferenceModel(fail=True), checkpoint=states.append)
    with pytest.raises(ValueError, match="incompatible"):
        run(content=b"label: other", restore=states[-1])
    state = states[-1]
    state["version"] = "old-private-checkpoint"
    with pytest.raises(ValueError, match="incompatible"):
        run(restore=state)


@pytest.mark.parametrize("mutation", ["scope-v9", "missing-wire", "different-wire"])
def test_scope_wire_checkpoint_identity_rejects_old_or_changed_contract_before_call(mutation):
    states = []
    run(model=ReferenceModel(fail=True), checkpoint=states.append)
    state = states[-1]
    if mutation == "scope-v9":
        state["identity"]["scopeVersion"] = "document-files.scope-integration.v10"
    elif mutation == "missing-wire":
        del state["identity"]["scopeReferenceWireVersion"]
    else:
        state["identity"]["scopeReferenceWireVersion"] = "unknown-wire"
    model = ReferenceModel()
    with pytest.raises(ValueError, match="incompatible"):
        run(model=model, restore=state, additional_budget={"maxModelCalls": 1})
    assert model.calls == 0


@pytest.mark.parametrize("elapsed", [float("nan"), float("inf"), -1, True])
def test_checkpoint_cannot_reset_or_corrupt_accumulated_usage(elapsed):
    states = []
    run(model=ReferenceModel(fail=True), checkpoint=states.append)
    states[-1]["usage"]["elapsedSeconds"] = elapsed
    with pytest.raises(ValueError, match="incompatible"):
        run(restore=states[-1])


def test_unchanged_invalid_repair_stops_without_exhausting_global_budget():
    model = ReferenceModel(invalid=True)
    result = run(model=model)
    assert model.calls == 2
    assert result["data"] is None
    assert any(i["code"] == "region_repair_no_progress" for i in result["issues"])


def test_invalid_first_response_cause_survives_budget_exhaustion_and_resume():
    states = []
    first = run(model=ReferenceModel(invalid=True), maxModelCalls=1, checkpoint=states.append)
    assert {i["code"] for i in first["issues"]} >= {
        "region_interpretation_invalid",
        "model_call_budget_exceeded",
    }
    assert states[-1]["repairDiagnostics"]
    model = BindingOnlyModel()
    resumed = run(
        model=model, maxModelCalls=1, restore=states[-1], additional_budget={"maxModelCalls": 1}
    )
    payload = json.loads(model.messages[0][-1]["content"])
    assert payload["repairFeedback"] == ["binding_not_in_region"]
    assert list(payload)[-1] == "repairFeedback"
    assert resumed["extraction"]["status"] == "complete", resumed["issues"]
    assert not any(i["code"] == "region_interpretation_invalid" for i in resumed["issues"])


def test_invalid_model_member_does_not_leak_its_text_into_diagnostics():
    class UnknownMember(ReferenceModel):
        def complete(self, messages, *, timeout):
            value = json.loads(super().complete(messages, timeout=timeout))
            value["DO_NOT_RECORD_THIS_PRIVATE_MODEL_TEXT"] = True
            return json.dumps(value)

    result = run(model=UnknownMember(), maxModelCalls=1)
    assert "DO_NOT_RECORD_THIS" not in json.dumps(result["issues"])
    assert "unknown_member" in json.dumps(result["issues"])


def test_per_region_grammar_limits_reference_choices_without_changing_public_contract():
    from jsonschema import Draft202012Validator

    observation = observe_document(b"count: 0\n", "txt", {})
    region = prepare_regions(observation, context_chars=120000)[0]
    original = RegionInterpretation.model_json_schema()
    schema = region_output_schema(observation, region)
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["regionId"]["const"] == region["id"]
    assert schema["properties"]["repeats"]["maxItems"] == 0
    assert (
        schema["$defs"]["FieldLink"]["properties"]["definitionRefs"]["items"]["enum"]
        == region["nodeIds"]
    )
    value = next(
        b for b in region["bindingIds"] if observation.bindings[b].get("candidateRole") == "value"
    )
    candidate = {
        "regionId": region["id"],
        "fields": [
            {
                "id": "f",
                "key": "count",
                "label": "Count",
                "definitionRefs": region["nodeIds"],
                "bindingId": value,
                "valueType": "integer",
                "status": "present",
            }
        ],
    }
    assert Draft202012Validator(schema).is_valid(candidate)
    candidate["fields"][0]["bindingId"] = "not-an-issued-binding"
    assert not Draft202012Validator(schema).is_valid(candidate)
    assert RegionInterpretation.model_json_schema() == original


def test_single_source_delimiter_pair_can_supply_a_missing_binding_without_new_inference():
    class DefinitionsOnlyModel(BindingOnlyModel):
        def complete(self, messages, *, timeout):
            value = json.loads(super().complete(messages, timeout=timeout))
            for field in value["fields"]:
                field.pop("bindingId")
            return json.dumps(value)

    model = DefinitionsOnlyModel()
    result = run(b"identifier: 00012345678901234567890\ncount: 0\nflag: false\n", model)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert model.calls == 1
    assert result["data"] == {
        "identifier": "00012345678901234567890",
        "count": "0",
        "flag": "false",
    }
    assert all(
        item["transformation"] == "source_binding_from_unique_label_reference"
        for item in result["valueEvidence"]
    )
    ambiguous = run(b"first: 0; second: false\n", DefinitionsOnlyModel(), maxModelCalls=1)
    # Two pairs in one line cannot be resolved without inference: the fields stay
    # uncertain and unread rather than invalidating the response or guessing a value.
    assert ambiguous["extraction"]["status"] == "partial"
    assert ambiguous["data"] == {"first": None, "second": None}
    assert all(item["status"] == "uncertain" for item in ambiguous["valueEvidence"])
    codes = {i["code"] for i in ambiguous["issues"]}
    assert codes >= {"field_binding_missing", "value_not_resolved"}
    assert not any(item.get("errors") == ["binding_not_in_region"] for item in ambiguous["issues"])


def _table():
    doc = ObservationDocument()
    for row, values in enumerate((("Name", "Amount"), ("A", "0"), ("B", "0"), ("C", ""))):
        for col, value in enumerate(values):
            ref = doc.node(f"c{row}:{col}", value)
            doc.bind(ref, start=0, end=len(value), candidateRole="content")
    doc.tables["t"] = {
        "id": "t",
        "basis": "native_structure",
        "rowCount": 4,
        "colCount": 2,
        "cells": [
            {
                "sourceRef": f"c{r}:{c}",
                "row": r,
                "col": c,
                "rowSpan": 1,
                "colSpan": 1,
                "isHeader": r == 0,
            }
            for r in range(4)
            for c in range(2)
        ],
    }
    region = {
        "id": "r",
        "nodeIds": list(doc.nodes),
        "bindingIds": list(doc.bindings),
        "contextNodeIds": [],
        "tableRef": "t",
    }
    ir = RegionInterpretation.model_validate(
        {
            "regionId": "r",
            "repeats": [
                {
                    "id": "rows",
                    "key": "rows",
                    "label": "Rows",
                    "tableRef": "t",
                    "rowStart": 0,
                    "rowEnd": 3,
                    "definitionRefs": ["c0:0"],
                    "rowRoles": [
                        {
                            "row": row,
                            "role": "header" if row == 0 else "data",
                            "sourceRefs": [f"c{row}:0", f"c{row}:1"],
                        }
                        for row in range(4)
                    ],
                    "columns": [
                        {
                            "id": "name",
                            "key": "name",
                            "label": "Name",
                            "column": 0,
                            "definitionRefs": ["c0:0"],
                        },
                        {
                            "id": "amount",
                            "key": "amount",
                            "label": "Amount",
                            "column": 1,
                            "definitionRefs": ["c0:1"],
                        },
                    ],
                }
            ],
            "meanings": [
                {
                    "id": "unit",
                    "kind": "unit",
                    "description": "Declared common unit",
                    "sourceRefs": ["c0:1"],
                    "fieldIds": ["amount"],
                }
            ],
            "dispositions": [
                {"sourceRef": ref, "role": "data", "explanation": "Table member"}
                for ref in doc.nodes
            ],
        }
    )
    return doc, region, ir


def test_repeat_expands_every_observed_row_and_semantic_scope():
    doc, region, ir = _table()
    region["requiredBindingIds"] = list(doc.bindings)
    compiled = compile_region(ir, doc, region)
    result = combine_regions([compiled])
    assert result["data"] == {
        "rows": [
            {"name": "A", "amount": "0"},
            {"name": "B", "amount": "0"},
            {"name": "C", "amount": ""},
        ]
    }
    assert not result["errors"], result["errors"]
    assert not result["issues"], result["issues"]
    assert result["valueEvidence"][-1]["status"] == "blank"
    unit = result["semanticDetails"][0]
    assert [target["path"] for target in unit["scope"]] == [
        "/rows/0/amount",
        "/rows/1/amount",
        "/rows/2/amount",
    ]
    assert unit["executable"] is False


def test_full_table_mapping_accounts_for_cells_without_per_cell_model_output():
    doc, region, ir = _table()
    ir.dispositions = []
    region["requiredBindingIds"] = list(doc.bindings)
    compiled = compile_region(ir, doc, region)
    assert not compiled.issues, compiled.issues
    assert {item["sourceRef"] for item in compiled.dispositions} == set(doc.nodes)
    assert all(item["basis"] == "program_derived" for item in compiled.dispositions)
    assert compiled.dispositions[0]["role"] in {"data", "structural"}
    assert len(compiled.semantic_details[0]["scope"]) == 3


def test_missing_pdf_cell_is_not_observed_blank():
    doc, region, ir = _table()
    doc.tables["t"]["basis"] = "recognition"
    doc.tables["t"]["cells"] = [c for c in doc.tables["t"]["cells"] if c["sourceRef"] != "c3:1"]
    ir.repeats[0].rowRoles[3].sourceRefs = ["c3:0"]
    result = combine_regions([compile_region(ir, doc, region)])
    assert result["data"]["rows"][-1]["amount"] is None
    assert result["valueEvidence"][-1]["status"] == "uncertain"
    assert result["issues"]


def test_model_cannot_submit_values_or_offsets():
    with pytest.raises(ValueError):
        RegionInterpretation.model_validate({"regionId": "r", "data": {"invented": 1}})
    doc, region, ir = _table()
    ir.repeats[0].rowEnd = 100
    with pytest.raises(CompileError, match="outside_observed"):
        compile_region(ir, doc, region)


def test_target_schema_handles_are_program_generated():
    doc = observe_document(b"count: 0", "txt", {})
    region = prepare_regions(doc, context_chars=120000)[0]
    bid = next(k for k, b in doc.bindings.items() if b.get("candidateRole") == "value")
    ref = doc.bindings[bid]["sourceRef"]
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
    ir = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "fields": [
                {
                    "id": "f",
                    "key": "ignored-key",
                    "label": "Count",
                    "valueType": "integer",
                    "definitionRefs": [ref],
                    "bindingId": bid,
                    "targetHandle": "t1",
                }
            ],
            "dispositions": [{"sourceRef": ref, "role": "data", "explanation": "Count"}],
        }
    )
    result = combine_regions(
        [compile_region(ir, doc, region, target_schema=schema)], target_schema=schema
    )
    assert result["data"] == {"count": 0}
    assert result["dataSchema"] == schema
    assert not result["errors"]


@pytest.mark.parametrize(
    ("text", "value_type", "schema_type", "expected", "status"),
    [
        ("value: 0", "integer", "integer", 0, "present"),
        ("value: false", "boolean", "boolean", False, "present"),
        ("value: null", "null", "null", None, "present"),
        ("value: ", "string", "string", "", "blank"),
    ],
)
def test_scalar_target_root_preserves_falsey_observed_values(
    text, value_type, schema_type, expected, status
):
    class ScalarModel(ReferenceModel):
        def complete(self, messages, *, timeout):
            answer = json.loads(super().complete(messages, timeout=timeout))
            answer["fields"][0].update(targetHandle="t0", valueType=value_type, status=status)
            return json.dumps(answer)

    result = run(text.encode(), ScalarModel(), targetSchema={"type": schema_type})
    assert result["data"] == expected
    assert type(result["data"]) is type(expected)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["valueEvidence"][0]["target"]["path"] == ""
    assert result["schemaEvidence"][0]["target"]["path"] == ""


def test_repeat_can_populate_array_target_root():
    doc, region, ir = _table()
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "amount": {"type": "string"}},
            "required": ["name", "amount"],
        },
    }
    ir.repeats[0].targetHandle = "t0"
    for index, column in enumerate(ir.repeats[0].columns):
        column.targetHandle = f"t{index + 2}"
    result = combine_regions(
        [compile_region(ir, doc, region, target_schema=schema)], target_schema=schema
    )
    assert result["data"] == [
        {"name": "A", "amount": "0"},
        {"name": "B", "amount": "0"},
        {"name": "C", "amount": ""},
    ]
    assert not result["errors"]
    assert result["valueEvidence"][-1]["target"]["path"] == "/2/amount"


def test_native_ocr_conflict_never_becomes_a_present_repeat_value():
    doc, region, ir = _table()
    for binding in doc.bindings.values():
        if binding["sourceRef"] == "c1:1":
            binding["candidateStatus"] = "unresolved_conflict"
    compiled = compile_region(ir, doc, region)
    assert compiled.data["rows"][0]["amount"] is None
    evidence = next(
        item for item in compiled.value_evidence if item["target"]["path"] == "/rows/0/amount"
    )
    assert evidence["status"] == "uncertain"
    assert evidence["binding"] is None
    assert any(issue["code"] == "value_not_resolved" for issue in compiled.issues)


def test_unresolved_note_scope_preserves_already_bound_values():
    doc, region, ir = _table()
    ir.meanings[0].fieldIds = []
    compiled = compile_region(ir, doc, region)
    result = combine_regions([compiled])
    assert result["data"]["rows"][0]["amount"] == "0"
    assert result["semanticDetails"][0]["scope"] == []
    assert result["semanticDetails"][0]["interpretationStatus"] == "uncertain"
    assert any(i["code"] == "semantic_scope_unresolved" for i in result["issues"])
    assert result["semantics"][-1]["targets"][0]["space"] == "document"
    assert not result["errors"]


@pytest.mark.parametrize(
    "scope",
    [
        {"fieldIds": ["amount", "not-issued"]},
        {"fieldIds": [], "groupIds": ["amount"]},
        {"repeatIds": ["rows"], "rowStart": 1},
        {"repeatIds": ["rows"], "rowStart": 1, "rowEnd": 200},
    ],
)
def test_invalid_meaning_scope_does_not_discard_valid_values_or_apply_valid_half(scope):
    doc, region, ir = _table()
    ir.meanings[0] = ir.meanings[0].model_copy(update=scope)
    result = combine_regions([compile_region(ir, doc, region)])
    assert result["data"]["rows"][0]["amount"] == "0"
    detail = result["semanticDetails"][0]
    assert detail["scope"] == []
    assert detail["scopeErrors"]
    assert detail["interpretationStatus"] == "uncertain"
    assert any(i["code"] == "semantic_scope_unresolved" for i in result["issues"])
    assert not result["errors"]


def test_table_cells_cannot_disappear_behind_node_accounting():
    doc = observe_document(
        b"<table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>0</td></tr><tr><td>B</td><td>0</td></tr></table>",
        "html",
        {},
    )
    region = next(r for r in prepare_regions(doc, context_chars=120000) if r.get("tableRef"))
    # Declared header cells are definitions, not value candidates; data cells are required.
    assert len(region["requiredBindingIds"]) == 4
    assert len(region["bindingIds"]) == 4
    ir = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "dispositions": [
                {"sourceRef": ref, "role": "data", "explanation": "Read node"}
                for ref in region["nodeIds"]
            ],
        }
    )
    compiled = compile_region(ir, doc, region)
    assert sum(i["code"] == "value_candidate_unaccounted" for i in compiled.issues) == 4


def test_native_continuation_rewrites_evidence_not_source_observations():
    from copy import deepcopy

    from document_files.interpretation.compiler import join_continuations

    doc, region, ir = _table()
    original = deepcopy(doc.nodes)
    first = compile_region(ir, doc, region)
    next_region = {**region, "id": "next"}
    next_ir = ir.model_copy(update={"regionId": "next"})
    second = compile_region(next_ir, doc, next_region)
    # A compiled subsequent source-table view has disjoint absolute native rows.
    second.repeat_paths["rows"]["rowStart"] = 4
    second.repeat_paths["rows"]["rowEnd"] = 7
    fragments, issues, links = join_continuations(
        [first, second],
        [
            {
                "id": "join",
                "leftRegion": "r",
                "rightRegion": "next",
                "leftTable": "t",
                "rightTable": "t",
                "confirmed": True,
                "basis": "same_native_table",
                "sourceRefs": ["c0:0"],
            }
        ],
        {},
    )
    result = combine_regions(fragments)
    assert not issues and not result["errors"]
    assert len(result["data"]["rows"]) == 6
    assert result["valueEvidence"][-1]["target"]["path"] == "/rows/5/amount"
    assert links and doc.nodes == original


def test_progressive_recognition_is_durable_before_completion_and_resumes(monkeypatch):
    import hashlib

    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    page = canvas.Canvas(stream)
    page.drawString(50, 700, "first: 0")
    page.showPage()
    page.drawString(50, 700, "second: 0")
    page.save()
    content = stream.getvalue()
    job = AnalysisJob(job_id="pages", input=AnalysisInput.from_bytes(content, format_id="pdf"))
    observed = []
    states = []

    class Pages:
        identity = {"adapter": "paged-test", "version": "1"}
        supports_checkpoints = True
        interrupted = True

        def observe(self, data, *, restore=None, checkpoint=None, timeout_seconds=None):
            completed = list(restore["completedPages"]) if restore else []
            pages = list(restore["pageResults"]) if restore else []
            for index in (1, 2):
                if index in completed:
                    continue
                observed.append(index)
                pages.append(
                    {
                        "page": index,
                        "status": "complete",
                        "sourceSha256": hashlib.sha256(data).hexdigest(),
                        "document": {"pages": {}, "texts": [], "tables": []},
                    }
                )
                completed.append(index)
                checkpoint(
                    {
                        "version": "document-files.recognition-checkpoint.v1",
                        "sourceSha256": hashlib.sha256(data).hexdigest(),
                        "backendIdentity": self.identity,
                        "completedPages": list(completed),
                        "pageResults": list(pages),
                    }
                )
                if self.interrupted:
                    self.interrupted = False
                    raise KeyboardInterrupt
            return {
                "status": "complete",
                "pageResults": pages,
                "completedPages": completed,
                "processedPages": completed,
                "pageCount": 2,
            }

    backend = Pages()
    options = ExtractionOptions(reconstructionContext=False)
    with pytest.raises(KeyboardInterrupt):
        extract_schema_from_stream(
            job,
            io.BytesIO(content),
            options=options,
            model_client=ReferenceModel(),
            observation_backend=backend,
            checkpoint=states.append,
        )
    assert states[-1]["phase"] == "observing"
    assert states[-1]["result"]["document"]["recognitionProgress"]["completedPages"] == [1]
    monkeypatch.setattr(
        "document_files.interpretation.engine.analyze_document",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("reanalysis")),
    )
    result = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=ReferenceModel(),
        observation_backend=backend,
        restore=states[-1],
    )
    assert observed == [1, 2]
    assert result["data"] == {"first": "0", "second": "0"}


def test_recognition_failure_before_first_page_can_resume_without_reanalysis(monkeypatch):
    from reportlab.pdfgen import canvas

    stream = io.BytesIO()
    page = canvas.Canvas(stream)
    page.drawString(50, 700, "value: 0")
    page.save()
    content = stream.getvalue()
    job = AnalysisJob(job_id="early", input=AnalysisInput.from_bytes(content, format_id="pdf"))

    class FailsBeforePage:
        identity = {"adapter": "pre-page-failure-test", "version": "1"}
        supports_checkpoints = True
        calls = 0

        def observe(self, data, **kwargs):
            self.calls += 1
            return {"status": "partial", "issues": [{"code": "recognition_timeout"}]}

    backend, states = FailsBeforePage(), []
    options = ExtractionOptions(reconstructionContext=False)
    first = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=ReferenceModel(),
        observation_backend=backend,
        checkpoint=states.append,
    )
    assert first["extraction"]["stage"] == "recognizing"
    assert states[-1]["phase"] == "observing"
    assert states[-1]["recognitionResume"] is None

    def forbidden(*args, **kwargs):
        raise AssertionError("native projection already committed")

    monkeypatch.setattr("document_files.interpretation.engine.analyze_document", forbidden)
    extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=ReferenceModel(),
        observation_backend=backend,
        restore=states[-1],
    )
    assert backend.calls == 2


@pytest.mark.parametrize("scope_mode", ["column", "rows"])
@pytest.mark.parametrize("meaning_status", ["interpreted", "uncertain"])
def test_engine_integrates_unresolved_unit_once_and_reuses_committed_scope(
    scope_mode, meaning_status
):
    content = (
        b"<p>Lengths use millimeters.</p><table><tr><th>Length</th></tr>"
        b"<tr><td>0012.40</td></tr></table>"
    )
    job = AnalysisJob(job_id="scopes", input=AnalysisInput.from_bytes(content, format_id="html"))

    class ScopedModel:
        identity = {"adapter": "scope-regression", "model": "scripted"}
        calls = 0

        def complete(self, messages, **kwargs):
            self.calls += 1
            payload = json.loads(messages[-1]["content"])
            if "taskId" in payload:
                if scope_mode == "rows":
                    candidate = next(c for c in payload["candidates"] if "rowOptions" in c)
                    row = next(r for r in candidate["rowOptions"]["rows"] if r["role"] == "data")
                    assert candidate["targetHandle"].startswith("@container")
                    return json.dumps(
                        {
                            "taskId": payload["taskId"],
                            "decision": "apply",
                            "targetHandles": [],
                            "rowSelections": [
                                {
                                    "targetHandle": candidate["targetHandle"],
                                    "rowStart": row["row"],
                                    "rowEnd": row["row"],
                                    "columnIds": ["length"],
                                }
                            ],
                            "sourceRefs": list(
                                dict.fromkeys(
                                    [
                                        *payload["statement"]["sourceRefs"],
                                        *candidate["definitionRefs"],
                                        *row["sourceRefs"],
                                    ]
                                )
                            ),
                            "explanation": "Scripted applicability to one actual data row.",
                        }
                    )
                candidate = next(c for c in payload["candidates"] if c["label"] == "Length")
                return json.dumps(
                    {
                        "taskId": payload["taskId"],
                        "decision": "apply",
                        "targetHandles": [candidate["targetHandle"]],
                        "sourceRefs": [
                            *payload["statement"]["sourceRefs"],
                            *candidate["definitionRefs"],
                        ],
                        "explanation": "Explicit length unit statement applies to Length.",
                    }
                )
            if payload.get("meaningPhase") == "selection":
                return json.dumps(
                    {
                        "sourceDecisions": {
                            s["sourceRef"]: {
                                "decision": "no_additional_meaning",
                                "explanation": "Scripted plain values",
                            }
                            for s in payload["meaningSources"]
                        }
                    }
                )
            if payload.get("tableStage") == "meaning":
                return json.dumps(
                    {
                        "regionId": payload["regionId"],
                        "baseRevision": None,
                        "changes": [],
                        "meanings": [],
                        "remainderReviews": [],
                    }
                )
            answer = {
                "regionId": payload["regionId"],
                "dispositions": [
                    {
                        "sourceRef": ref,
                        "role": "data" if payload["tables"] else "note",
                        "explanation": "Source member",
                    }
                    for ref in payload["nodeIds"]
                ],
            }
            if not payload["tables"]:
                answer["meanings"] = [
                    {
                        "id": "unit",
                        "kind": "unit",
                        "description": "Length uses millimeters",
                        "sourceRefs": payload["nodeIds"],
                        "status": meaning_status,
                    }
                ]
            else:
                table_ref, table = next(iter(payload["tables"].items()))
                cells = table["cells"]
                if isinstance(cells, dict):
                    cells = [dict(zip(cells["columns"], row, strict=True)) for row in cells["rows"]]
                header = next(c["sourceRef"] for c in cells if c["row"] == 0)
                answer = {
                    "regionId": payload["regionId"],
                    "tableKind": "record_table",
                    "record": {
                        "id": "measurements",
                        "key": "measurements",
                        "label": "Measurements",
                        "tableRef": table_ref,
                        "rowStart": 0,
                        "rowEnd": 1,
                        "definitionRefs": [header],
                        "rowRoles": [{"row": 1, "role": "data"}],
                        "columns": [
                            {
                                "id": "length",
                                "key": "length",
                                "label": "Length",
                                "column": 0,
                                "definitionRefs": [header],
                                "valueType": "decimal",
                            }
                        ],
                    },
                }
            return json.dumps(answer)

    states, model = [], ScopedModel()
    kwargs = {
        "options": ExtractionOptions(
            reconstructionContext=False, maxModelCalls=3 if scope_mode == "rows" else 12
        ),
        "model_client": model,
    }
    result = extract_schema_from_stream(
        job, io.BytesIO(content), checkpoint=states.append, **kwargs
    )
    if scope_mode == "rows":
        assert model.calls == 3 and result["extraction"]["status"] == "partial"
        result = extract_schema_from_stream(
            job,
            io.BytesIO(content),
            restore=states[-1],
            checkpoint=states.append,
            additional_budget={"maxModelCalls": 1},
            **kwargs,
        )
        decisions = [
            s["decision"] for s in states[-1]["scopeDecisions"].values() if "decision" in s
        ]
        assert len(decisions) == 1
        assert decisions[0]["rowSelections"][0]["targetHandle"].startswith("scope-target-")
    assert result["data"] == {"measurements": [{"length": "0012.40"}]}
    expected_status = "complete" if meaning_status == "interpreted" else "partial"
    assert result["extraction"]["status"] == expected_status, result["issues"]
    assert result["semanticDetails"][0]["interpretationStatus"] == meaning_status
    assert any(i.get("code") == "semantic_interpretation_uncertain" for i in result["issues"]) == (
        meaning_status == "uncertain"
    )
    assert result["semanticDetails"][0]["scope"] == [
        {"space": "data", "path": "/measurements/0/length"}
    ]
    assert model.calls == 4
    before = model.calls
    resumed = extract_schema_from_stream(job, io.BytesIO(content), restore=states[-1], **kwargs)
    assert model.calls == before
    assert resumed["extraction"]["status"] == expected_status
    assert resumed["semanticDetails"] == result["semanticDetails"]


@pytest.mark.parametrize("invalid_sibling", [False, True])
def test_local_scope_batch_resumes_without_repeating_region_interpretation(invalid_sibling):
    class LocalScopeModel(ReferenceModel):
        def complete(self, messages, *, timeout):
            payload = json.loads(messages[-1]["content"])
            if "tasks" in payload or "taskId" in payload:
                self.calls += 1
                self.messages.append(messages)
                decisions = []
                for task in payload.get("tasks", [payload]):
                    chosen = next(
                        c
                        for c in task["candidates"]
                        if c["label"] == task["statement"]["description"]
                    )
                    decisions.append(
                        {
                            "taskId": task["taskId"],
                            "decision": "apply",
                            "targetHandles": [chosen["targetHandle"]],
                            "sourceRefs": list(
                                dict.fromkeys(
                                    [*task["statement"]["sourceRefs"], *chosen["definitionRefs"]]
                                )
                            ),
                            "explanation": "Scripted exact label definition applicability.",
                        }
                    )
                if invalid_sibling and self.calls == 2:
                    decisions[-1] = {"taskId": decisions[-1]["taskId"]}
                return json.dumps({"decisions": decisions} if "tasks" in payload else decisions[0])
            value = json.loads(super().complete(messages, timeout=timeout))
            value["meanings"] = [
                {
                    "id": "meaning-" + f["id"],
                    "kind": "definition",
                    "description": f["label"],
                    "sourceRefs": f["definitionRefs"],
                    "status": "interpreted",
                }
                for f in value["fields"]
            ]
            return json.dumps(value)

    content = b"identifier: 000123\ncount: 0\nflag: false\n"
    states, model = [], LocalScopeModel()
    first = run(content, model, maxModelCalls=1, checkpoint=states.append)
    assert first["extraction"]["status"] == "partial" and model.calls == 1
    assert len(first["semanticDetails"]) == 3
    result = run(
        content,
        model,
        maxModelCalls=1,
        restore=states[-1],
        checkpoint=states.append,
        additional_budget={"maxModelCalls": 1},
    )
    assert model.calls == 2 and len(json.loads(model.messages[-1][-1]["content"])["tasks"]) == 3
    for task in json.loads(model.messages[-1][-1]["content"])["tasks"]:
        assert all(c["targetHandle"].startswith("@") for c in task["candidates"])
    assert (
        states[-1]["identity"]["scopeReferenceWireVersion"]
        == "document-files.scope-reference-wire.v2"
    )
    assert all(
        handle.startswith("scope-target-")
        for stored in states[-1]["scopeDecisions"].values()
        for handle in stored.get("decision", {}).get("targetHandles", [])
    )
    assert (
        result["data"] == first["data"] == {"identifier": "000123", "count": "0", "flag": "false"}
    )
    if invalid_sibling:
        assert result["extraction"]["status"] == "partial"
        assert sum(bool(s["scope"]) for s in result["semanticDetails"]) == 2
        unchanged = run(content, model, maxModelCalls=1, restore=states[-1])
        assert model.calls == 2 and unchanged["extraction"]["status"] == "partial"
        result = run(
            content,
            model,
            maxModelCalls=1,
            restore=states[-1],
            checkpoint=states.append,
            additional_budget={"maxModelCalls": 1},
        )
        assert model.calls == 3
        assert "taskId" in json.loads(model.messages[-1][-1]["content"])
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert all(s["scope"] for s in result["semanticDetails"])
    repeated = run(content, model, maxModelCalls=1, restore=states[-1])
    assert model.calls == (3 if invalid_sibling else 2)
    assert repeated["extraction"]["status"] == "complete"


def test_unreported_usage_survives_timeout_and_legacy_checkpoint_resume():
    from copy import deepcopy

    from document_files.interpretation.backends import InferenceResponse

    class Reported(ReferenceModel):
        def infer(self, request):
            return InferenceResponse(
                self.complete(request.messages, timeout=request.timeout),
                {"prompt_tokens": 20, "completion_tokens": 10},
            )

    snapshots = []
    failed = run(model=ReferenceModel(fail=True), maxModelCalls=1, checkpoint=snapshots.append)
    assert failed["extraction"]["usage"]["unreportedUsageCalls"] == 1
    assert failed["extraction"]["usage"]["promptTokens"] == 0  # reported subtotal only
    legacy = deepcopy(snapshots[-1])
    del legacy["usage"]["unreportedUsageCalls"]
    result = run(
        model=Reported(), maxModelCalls=1, restore=legacy, additional_budget={"maxModelCalls": 1}
    )
    assert result["extraction"]["usage"]["unreportedUsageCalls"] == 1
    assert result["extraction"]["usage"]["promptTokens"] == 20
    assert run(model=Reported())["extraction"]["usage"]["unreportedUsageCalls"] == 0


HEADER_TABLE = (
    b"<table><tr><th>Name</th><th>Amount</th></tr>"
    b"<tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>"
)


def _payload_cells(payload):
    table_ref, table = next(iter(payload["tables"].items()))
    cells = table["cells"]
    if isinstance(cells, dict):
        cells = [dict(zip(cells["columns"], row, strict=True)) for row in cells["rows"]]
    return table_ref, cells


def _content_binding(payload, ref):
    return next(
        bid
        for bid, binding in payload["bindings"].items()
        if binding["sourceRef"] == ref and binding.get("candidateRole") == "content"
    )


class RedundantFieldsThenRepeatModel(ReferenceModel):
    """First maps the rows and also binds every data cell as a scalar field; the repair
    keeps only the repeat."""

    def complete(self, messages, *, timeout):
        self.calls += 1
        self.messages.append(messages)
        payload = json.loads(messages[-1]["content"])
        table_ref, cells = _payload_cells(payload)
        headers = {cell["col"]: cell["sourceRef"] for cell in cells if cell["isHeader"]}
        response = {
            "regionId": payload["regionId"],
            "repeats": [
                {
                    "id": "rows",
                    "key": "rows",
                    "label": "Rows",
                    "tableRef": table_ref,
                    "rowStart": 0,
                    "rowEnd": 2,
                    "definitionRefs": [headers[0]],
                    "rowRoles": [
                        {
                            "row": row,
                            "role": "header" if row == 0 else "data",
                            "sourceRefs": [c["sourceRef"] for c in cells if c["row"] == row],
                        }
                        for row in range(3)
                    ],
                    "columns": [
                        {
                            "id": key,
                            "key": key,
                            "label": key,
                            "valueType": "string",
                            "column": col,
                            "definitionRefs": [headers[col]],
                        }
                        for col, key in ((0, "name"), (1, "amount"))
                    ],
                }
            ],
        }
        if self.calls == 1:
            response["fields"] = [
                {
                    "id": f"cell{index}",
                    "key": f"cell{index}",
                    "label": payload["nodes"][cell["sourceRef"]]["text"],
                    "valueType": "string",
                    "definitionRefs": [headers[cell["col"]]],
                    "bindingId": _content_binding(payload, cell["sourceRef"]),
                    "status": "present",
                }
                for index, cell in enumerate(cells)
                if not cell["isHeader"]
            ]
        return json.dumps(response)


def test_declared_header_cell_bound_as_value_is_flagged_not_silently_accepted():
    doc, region, _ = _table()
    header = next(bid for bid, binding in doc.bindings.items() if binding["sourceRef"] == "c0:0")
    ir = RegionInterpretation.model_validate(
        {
            "regionId": "r",
            "fields": [
                {
                    "id": "name_header",
                    "key": "name_header",
                    "label": "Name",
                    "valueType": "string",
                    "definitionRefs": ["c0:0"],
                    "bindingId": header,
                    "status": "present",
                }
            ],
            "dispositions": [
                {"sourceRef": ref, "role": "structural", "explanation": "Table member"}
                for ref in doc.nodes
                if ref != "c0:0"
            ],
        }
    )
    compiled = compile_region(ir, doc, region)
    flagged = [i for i in compiled.issues if i["code"] == "header_cell_bound_as_value"]
    assert flagged == [
        {"code": "header_cell_bound_as_value", "sourceRef": "c0:0", "bindingId": header}
    ]
    assert compiled.header_value_bindings == {header}
    assert compiled.data == {"name_header": "Name"}


def test_redundant_cell_fields_are_dropped_by_compiler_for_existing_ir():
    model = RedundantFieldsThenRepeatModel()
    doc = observe_document(HEADER_TABLE, "html", {})
    region = next(r for r in prepare_regions(doc, context_chars=16000) if r.get("tableRef"))
    from document_files.interpretation.regions import region_payload

    response = model.complete([{"content": json.dumps(region_payload(doc, region))}], timeout=1)
    fragment = compile_region(RegionInterpretation.model_validate_json(response), doc, region)
    assert fragment.data == {"rows": [{"name": "A", "amount": "1"}, {"name": "B", "amount": "2"}]}
    assert not fragment.issues
    assert sorted(len(d.get("redundantFieldIds", [])) for d in fragment.dispositions) == [
        0,
        0,
        1,
        1,
        1,
        1,
    ]


def test_declared_header_and_caption_bindings_are_not_value_choices():
    html = b"<table><caption>Sizes</caption>" + HEADER_TABLE[len(b"<table>") :]
    doc = observe_document(html, "html", {})
    region = next(r for r in prepare_regions(doc, context_chars=16000) if r.get("tableRef"))
    table = doc.tables[region["tableRef"]]
    headers = {cell["sourceRef"] for cell in table["cells"] if cell.get("isHeader")}
    data = {cell["sourceRef"] for cell in table["cells"] if not cell.get("isHeader")}
    caption = next(n for n in doc.nodes if doc.nodes[n].get("semanticRole") == "caption")
    assert caption in region["nodeIds"]
    assert {doc.bindings[b]["sourceRef"] for b in region["requiredBindingIds"]} == data
    assert {doc.bindings[b]["sourceRef"] for b in region["bindingIds"]} == data
    assert headers <= set(region["nodeIds"])


def test_repeat_cannot_map_one_column_index_twice():
    doc, region, ir = _table()
    repeat = ir.repeats[0]
    duplicate = repeat.columns[0].model_copy(update={"id": "again", "key": "again"})
    ir = ir.model_copy(
        update={"repeats": [repeat.model_copy(update={"columns": [*repeat.columns, duplicate]})]}
    )
    with pytest.raises(CompileError, match="duplicate_repeat_column_index"):
        compile_region(ir, doc, region)


def test_data_rows_outside_every_repeat_are_reported():
    doc, region, ir = _table()
    ir = ir.model_copy(
        update={
            "repeats": [
                ir.repeats[0].model_copy(
                    update={"rowEnd": 1, "rowRoles": ir.repeats[0].rowRoles[:2]}
                )
            ]
        }
    )
    compiled = compile_region(ir, doc, region)
    flagged = [i for i in compiled.issues if i["code"] == "table_rows_outside_repeat"]
    assert flagged == [
        {"code": "table_rows_outside_repeat", "sourceRef": "t", "tableRef": "t", "rows": [2, 3]}
    ]


def test_column_definitions_follow_declared_header_geometry():
    doc, region, ir = _table()
    repeat = ir.repeats[0]
    swapped = [
        repeat.columns[0].model_copy(update={"definitionRefs": ["c0:1"]}),
        repeat.columns[1].model_copy(update={"definitionRefs": ["c0:0"]}),
    ]
    ir = ir.model_copy(update={"repeats": [repeat.model_copy(update={"columns": swapped})]})
    compiled = compile_region(ir, doc, region)
    codes = sorted((i["code"], i["sourceRef"], i["column"]) for i in compiled.issues)
    assert codes == [
        ("column_definition_not_above_column", "c0:0", 1),
        ("column_definition_not_above_column", "c0:1", 0),
        ("column_leaf_header_missing", "c0:0", 0),
        ("column_leaf_header_missing", "c0:1", 1),
    ]
    provenance = {item["target"]["path"]: item["sourceRefs"] for item in compiled.schema_evidence}
    assert provenance["/properties/rows/items/properties/name"] == ["c0:1", "c0:0"]
    assert provenance["/properties/rows/items/properties/amount"] == ["c0:0", "c0:1"]


def test_scalar_field_on_a_record_cell_is_dropped_and_recorded():
    doc, region, ir = _table()
    cell = next(bid for bid, binding in doc.bindings.items() if binding["sourceRef"] == "c1:0")
    field = {
        "id": "first_name",
        "key": "first_name",
        "label": "Name",
        "valueType": "string",
        "definitionRefs": ["c0:0"],
        "bindingId": cell,
        "status": "present",
    }
    meaning = {
        "id": "note",
        "kind": "note",
        "description": "Applies to the first name",
        "sourceRefs": ["c0:0"],
        "fieldIds": ["first_name"],
    }
    ir = RegionInterpretation.model_validate(
        {**ir.model_dump(), "fields": [field], "meanings": [*ir.meanings, meaning]}
    )
    compiled = compile_region(ir, doc, region)
    assert compiled.data == {
        "rows": [
            {"name": "A", "amount": "0"},
            {"name": "B", "amount": "0"},
            {"name": "C", "amount": ""},
        ]
    }
    assert compiled.dropped_fields == {"first_name": "c1:0"}
    ledger = {d["sourceRef"]: d for d in compiled.dispositions}
    assert ledger["c1:0"]["redundantFieldIds"] == ["first_name"]
    assert ledger["c1:0"]["role"] == "data"
    moved = next(d for d in compiled.semantic_details if d["id"].endswith(":note"))
    assert moved["scope"] == [] and moved["unresolvedScopeIds"] == ["first_name"]
    assert {"code": "semantic_scope_unresolved", "semanticId": moved["id"]} in compiled.issues
    assert not any(i["code"] == "cell_mapped_by_repeat_and_field" for i in compiled.issues)


def test_subtotal_row_fields_are_not_dropped_as_redundant():
    doc, region, ir = _table()
    subtotal = next(bid for bid, binding in doc.bindings.items() if binding["sourceRef"] == "c3:0")
    repeat = {
        **ir.repeats[0].model_dump(),
        "rowRoles": [
            *(role.model_dump() for role in ir.repeats[0].rowRoles if role.row != 3),
            {"row": 3, "role": "subtotal", "sourceRefs": ["c3:0", "c3:1"]},
        ],
    }
    field = {
        "id": "total_label",
        "key": "total_label",
        "label": "Subtotal label",
        "valueType": "string",
        "definitionRefs": ["c0:0"],
        "bindingId": subtotal,
        "status": "present",
    }
    ir = RegionInterpretation.model_validate(
        {**ir.model_dump(), "repeats": [repeat], "fields": [field]}
    )
    compiled = compile_region(ir, doc, region)
    assert compiled.dropped_fields == {}
    assert compiled.data["total_label"] == "C"
    assert compiled.data["rows"] == [{"name": "A", "amount": "0"}, {"name": "B", "amount": "0"}]


def test_present_field_without_a_source_degrades_to_uncertain_not_invalid():
    doc, region, ir = _table()
    field = {
        "id": "orphan",
        "key": "orphan",
        "label": "Orphan",
        "valueType": "string",
        "definitionRefs": ["c1:0"],
        "bindingId": None,
        "status": "present",
    }
    ir = RegionInterpretation.model_validate({**ir.model_dump(), "fields": [field]})
    compiled = compile_region(ir, doc, region)
    assert compiled.data["rows"][0] == {"name": "A", "amount": "0"}
    assert compiled.data["orphan"] is None
    codes = [i["code"] for i in compiled.issues]
    assert "field_binding_missing" in codes and "value_not_resolved" in codes
    evidence = next(e for e in compiled.value_evidence if e["target"]["path"] == "/orphan")
    assert evidence["status"] == "uncertain" and evidence["raw"] == ""


def test_header_label_fields_of_a_mapped_table_are_dropped_not_read():
    doc, region, ir = _table()
    fields = [
        {
            "id": "name_label",
            "key": "name_label",
            "label": "Name",
            "valueType": "string",
            "definitionRefs": ["c0:0"],
            "bindingId": None,
            "status": "absent",
        },
        {
            "id": "amount_label",
            "key": "amount_label",
            "label": "Amount",
            "valueType": "string",
            "definitionRefs": ["c0:1"],
            "bindingId": None,
            "status": "present",
        },
    ]
    ir = RegionInterpretation.model_validate(
        {**ir.model_dump(), "fields": fields, "dispositions": []}
    )
    compiled = compile_region(ir, doc, region)
    assert list(compiled.data) == ["rows"]
    assert compiled.dropped_fields == {"name_label": "c0:0", "amount_label": "c0:1"}
    assert not any(i["code"] == "field_binding_missing" for i in compiled.issues)
    ledger = {d["sourceRef"]: d for d in compiled.dispositions}
    assert ledger["c0:0"]["redundantFieldIds"] == ["name_label"]
    assert ledger["c0:1"]["redundantFieldIds"] == ["amount_label"]
    assert ledger["c0:1"]["role"] == "structural" and ledger["c0:1"]["basis"] == "program_derived"


def test_header_label_field_without_a_mapped_column_is_kept():
    doc, region, ir = _table()
    field = {
        "id": "name_label",
        "key": "name_label",
        "label": "Name",
        "valueType": "string",
        "definitionRefs": ["c0:0"],
        "bindingId": None,
        "status": "absent",
    }
    ir = RegionInterpretation.model_validate({**ir.model_dump(), "repeats": [], "fields": [field]})
    compiled = compile_region(ir, doc, region)
    assert compiled.dropped_fields == {}
    assert compiled.data == {"name_label": None}


def test_compiler_column_mapping_offers_group_scope_over_exact_record_targets():
    from document_files.interpretation.integration import apply_scope_decision, build_scope_tasks

    doc, region, ir = _table()
    doc.nodes["measurement-header"] = {"text": "Measurements"}
    doc.nodes["caption"] = {"text": "Measurements use mm. Name requires a second check."}
    for cell in doc.tables["t"]["cells"]:
        cell["row"] += 1
    doc.tables["t"].setdefault("headerCells", []).append(
        {
            "sourceRef": "measurement-header",
            "row": 0,
            "col": 0,
            "colSpan": 2,
            "rowSpan": 1,
            "isHeader": True,
        }
    )
    region["nodeIds"].extend(["measurement-header", "caption"])
    repeat = ir.repeats[0]
    ir = ir.model_copy(
        update={
            "meanings": [],
            "repeats": [
                repeat.model_copy(
                    update={
                        "rowStart": 1,
                        "rowEnd": 4,
                        "rowRoles": [
                            role.model_copy(update={"row": role.row + 1})
                            for role in repeat.rowRoles
                        ],
                    }
                )
            ],
        }
    )
    # Keep the actual compiler path, including generated IDs and record evidence.
    compiled = compile_region(ir, doc, region)
    compiled.semantics.append(
        {
            "id": "mm",
            "kind": "unresolved_unit",
            "description": "Measurements use mm",
            "sourceRefs": ["caption"],
            "scope": [],
            "targets": [],
            "status": "uncertain",
        }
    )
    compiled.semantic_details.append(
        {
            "id": "mm",
            "kind": "unit",
            "sourceRefs": ["caption"],
            "sourceText": [doc.nodes["caption"]["text"]],
            "scope": [],
            "interpretationStatus": "uncertain",
        }
    )
    compiled.issues.append({"code": "semantic_scope_unresolved", "semanticId": "mm"})
    task = build_scope_tasks(doc, [region], [compiled])[0]
    group = next(c for c in task.payload["candidates"] if c.get("candidateKind") == "headerGroup")
    assert [member["label"] for member in group["members"]] == ["Name", "Amount"]
    updated, changed = apply_scope_decision(
        [compiled],
        task,
        {
            "taskId": task.id,
            "decision": "apply",
            "targetHandles": [group["targetHandle"]],
            "sourceRefs": ["caption", "measurement-header"],
            "explanation": "Explicit measurement group unit.",
        },
    )
    assert changed and updated[0].data == compiled.data and updated[0].schema == compiled.schema
    assert updated[0].semantic_details[-1]["scope"] == [
        {"space": "data", "path": f"/rows/{row}/{column}"}
        for column in ("name", "amount")
        for row in range(3)
    ]
    assert all("mm" in e["semanticIds"] for e in updated[0].value_evidence)


def test_recognition_audit_traces_and_equivalent_sources_do_not_inflate_model_payload():
    from copy import deepcopy

    from document_files.interpretation.regions import region_payload

    doc, region, _ = _table()
    doc.regions = [region]
    for ref in list(doc.nodes):
        original = doc.nodes[ref]
        original["sourceStructure"] = {
            "page": 1,
            "tableRef": "t",
            "characters": [{"text": "audit"}] * 300,
            "bbox": {"left": 1, "top": 2, "right": 3, "bottom": 4, "sourceBox": {"audit": 1}},
            "provenance": [{"redundant": "coordinates"}] * 300,
        }
        original["semanticRole"] = "table_cell"
        original["semanticInput"] = {
            "role": "representative",
            "sourceSegments": [{"sourceRef": "audit", "characterIndex": i} for i in range(300)],
        }
        duplicate = f"ocr:{ref}"
        doc.node(
            duplicate,
            original["text"],
            role="recognition_source_cell",
            semanticInput={"role": "source_overlap_not_independent", "representativeRefs": [ref]},
        )
        region["contextNodeIds"].append(duplicate)
        doc.relations.extend(
            [
                {
                    "kind": "recognitionSourceSupport",
                    "sourceRef": duplicate,
                    "targetRef": ref,
                    "truthVerified": False,
                },
                {
                    "kind": "observationEquivalence",
                    "sourceRefs": [duplicate],
                    "targetRef": ref,
                    "sourceSegments": [{"characterIndex": i} for i in range(300)],
                },
            ]
        )
    before = deepcopy(doc.to_dict())
    planned = prepare_regions(doc, context_chars=16000)
    assert len(planned) == 1 and planned[0]["withinContextBudget"]
    payload = region_payload(doc, planned[0])
    assert not payload["contextNodeIds"]
    assert "characterIndex" not in json.dumps(payload)
    assert "recognitionSourceSupport" not in json.dumps(payload)
    for ref in region["nodeIds"]:
        assert payload["nodes"][ref]["text"] == before["nodes"][ref]["text"]
        assert payload["nodes"][ref]["semanticInput"]["role"] == "representative"
    assert doc.nodes == before["nodes"] and doc.relations == before["relations"]
    assert doc.bindings == before["bindings"]


def test_undeclared_first_row_is_retained_as_context_not_invented_header_on_later_slices():
    from document_files.interpretation.regions import region_payload

    content = (
        "<table>"
        + "".join(
            "<tr>" + "".join(f"<td>Record {r} column {c}</td>" for c in range(4)) + "</tr>"
            for r in range(40)
        )
        + "</table>"
    ).encode()
    doc = observe_document(content, "html", {})
    original_table = next(iter(doc.tables.values()))
    first_refs = {cell["sourceRef"] for cell in original_table["cells"] if cell["row"] == 0}
    regions = prepare_regions(doc, context_chars=16000)
    table_regions = [r for r in regions if r.get("tableRef")]
    assert len(table_regions) > 1
    for region in table_regions[1:]:
        table = doc.tables[region["tableRef"]]
        assert {cell["sourceRef"] for cell in table["leadingCells"]} == first_refs
        assert all(cell.get("isHeader") is not True for cell in table["leadingCells"])
        assert first_refs <= set(region["contextNodeIds"])
        assert not first_refs.intersection(region["nodeIds"])
        assert all(doc.bindings[b]["sourceRef"] not in first_refs for b in region["bindingIds"])
        payload = region_payload(doc, region)
        assert all(ref in payload["nodes"] for ref in first_refs)
    assert all(cell.get("isHeader") is not True for cell in original_table["cells"])


def test_uncompiled_continuation_is_not_sent_to_the_model(monkeypatch):
    monkeypatch.setattr(
        "document_files.interpretation.engine.continuation_candidates",
        lambda *args: [
            {
                "id": "pending",
                "leftRegion": "not-compiled",
                "rightRegion": "semantic-region:1",
                "leftTable": "left",
                "rightTable": "right",
                "confirmed": False,
                "basis": "adjacent_page_column_candidate",
                "sourceRefs": ["not-in-observation"],
            }
        ],
    )
    model = ReferenceModel()
    result = run(model=model)
    assert model.calls == 1
    assert any(i["code"] == "table_continuation_unresolved" for i in result["issues"])


def test_all_model_stages_honor_client_input_character_budget_before_dispatch():
    model = ReferenceModel()
    model.input_budget_chars = 2000
    result = run(model=model)
    assert model.calls == 0
    assert any(i["code"] == "region_context_budget_exceeded" for i in result["issues"])


def test_failed_decimal_record_read_does_not_delete_a_valid_scalar_representation():
    from document_files.interpretation.semantic_types import FieldLink

    doc, region, ir = _table()
    ref = "c1:1"
    doc.nodes[ref]["text"] = "12.5 mm"
    bid = next(k for k, b in doc.bindings.items() if b["sourceRef"] == ref)
    doc.bindings[bid].update(start=0, end=7)
    ir.repeats[0].columns[1].valueType = "decimal"
    ir.fields = [
        FieldLink(
            id="original",
            key="original",
            label="Original quantity",
            definitionRefs=[ref],
            bindingId=bid,
            valueType="string",
        )
    ]
    compiled = compile_region(ir, doc, region)
    assert compiled.data["original"] == "12.5 mm"
    assert compiled.data["rows"][0]["amount"] is None
    assert compiled.dropped_fields == {}
    assert any(i["code"] == "decimal_format_unresolved" for i in compiled.issues)


def test_additional_span_on_record_cell_is_not_dropped_as_a_duplicate_read():
    from document_files.interpretation.semantic_types import FieldLink

    doc, region, ir = _table()
    ref = "c1:0"
    doc.nodes[ref]["text"] = "A [note]"
    original = next(b for b in doc.bindings.values() if b["sourceRef"] == ref)
    original["end"] = 8
    bid = doc.bind(ref, start=2, end=8, candidateRole="content")
    region["bindingIds"].append(bid)
    ir.fields = [
        FieldLink(
            id="annotation",
            key="annotation",
            label="Annotation",
            definitionRefs=[ref],
            bindingId=bid,
            valueType="string",
        )
    ]
    compiled = compile_region(ir, doc, region)
    assert compiled.data["rows"][0]["name"] == "A [note]"
    assert compiled.data["annotation"] == "[note]"
    assert compiled.dropped_fields == {}
