"""Verified read causes repair types without revisiting correct source rows."""

import io
import json
import zipfile
from copy import deepcopy

import pytest
from openpyxl import Workbook
from test_native_merged_geometry import observe
from test_table_formula_contract import formula_table

from document_files.api import (
    AnalysisInput,
    AnalysisJob,
    ExtractionOptions,
    extract_schema_from_stream,
)
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.bindings import BindingReadError, resolve
from document_files.interpretation.compiler import CompileError, compile_region, preferred_binding
from document_files.interpretation.contracts import SourceBinding
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.table_protocol import structural_ir
from document_files.interpretation.table_selection_wire import encode_selection
from document_files.interpretation.table_source_wire import expand_table_sources
from document_files.interpretation.table_structure_feedback import (
    needs_layout_review,
    structure_feedback,
)

EXACT = "0.123456789012345678901"


def source_file(format_id):
    rows = [["Code", "Quantity"], ["00007", EXACT], ["00008", "4.5000"]]
    out = io.BytesIO()
    if format_id == "xlsx":
        book = Workbook()
        for row in rows:
            book.active.append(row)
        book.save(out)
        book.close()
    else:
        xml = '<sec><p><run><tbl rowCnt="3" colCnt="2">'
        for r, row in enumerate(rows):
            xml += "<tr>"
            for col, text in enumerate(row):
                xml += (
                    f'<tc><cellAddr rowAddr="{r}" colAddr="{col}"/>'
                    '<cellSpan rowSpan="1" colSpan="1"/>'
                    f"<subList><p><run><t>{text}</t></run></p></subList></tc>"
                )
            xml += "</tr>"
        xml += "</tbl></run></p></sec>"
        with zipfile.ZipFile(out, "w") as archive:
            archive.writestr("Contents/section0.xml", xml)
    return out.getvalue()


class RepairModel:
    identity = {"adapter": "scripted", "model": "precision-repair-not-quality"}

    def __init__(self, repair=True):
        self.repair = repair
        self.requests = []
        self.mapping_calls = 0

    def infer(self, request):
        self.requests.append(request)
        body = expand_table_sources(json.loads(request.messages[-1]["content"]))
        ref, table = next(iter(body.get("tables", {}).items()), (None, None))
        if body["tableStage"] == "layout":
            value = {
                "regionId": body["regionId"],
                "tableKind": "record_table",
                "rowRoles": ["header" if r == 0 else "data" for r in table["rowRoleOrder"]],
                "baseRevision": body.get("baseLayoutSHA256"),
            }
        elif body["tableStage"] == "structure":
            self.mapping_calls += 1
            columns = {}
            for col, key in enumerate(["code", "quantity"]):
                kind = "string" if col == 0 else "number"
                if col == 1 and self.mapping_calls > 1 and self.repair:
                    kind = "decimal"
                columns[str(col)] = {
                    "id": key,
                    "key": key,
                    "label": key,
                    "valueType": kind,
                    "definitionRefs": table["columnCandidates"][col]["headerRefs"],
                    "bindingMode": "source",
                }
            value = {
                "regionId": body["regionId"],
                "tableKind": "record_table",
                "record": {
                    "id": "records",
                    "key": "records",
                    "label": "Records",
                    "tableRef": ref,
                    "rowStart": 0,
                    "rowEnd": 2,
                    "columns": columns,
                    "definitionRefs": [r for c in columns.values() for r in c["definitionRefs"]],
                },
            }
        else:
            value = encode_selection(
                {
                    "sourceDecisions": {
                        s["sourceRef"]: {"decision": "no_additional_meaning", "explanation": None}
                        for s in body["meaningSources"]
                    }
                }
            )
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 1, "completion_tokens": 1})


@pytest.mark.parametrize("format_id", ["hwpx", "xlsx"])
@pytest.mark.parametrize("repair", [True, False])
def test_public_precision_repair_preserves_rows_values_limits_and_resume(format_id, repair):
    raw = source_file(format_id)
    model, states = RepairModel(repair), []

    def run(restore=None):
        return extract_schema_from_stream(
            AnalysisJob(
                job_id="precision", input=AnalysisInput.from_bytes(raw, format_id=format_id)
            ),
            io.BytesIO(raw),
            model_client=model,
            options=ExtractionOptions(maxModelCalls=5, reconstructionContext=False),
            checkpoint=states.append,
            restore=restore,
        )

    result = run()
    stages = [json.loads(r.messages[-1]["content"])["tableStage"] for r in model.requests]
    assert stages == ["layout", "structure", "structure"] + (["meaning"] if repair else [])
    feedback = json.loads(model.requests[2].messages[-1]["content"])["repairFeedback"]
    detail = json.loads(feedback[1].split(":", 1)[1])
    assert detail["readFailure"] == "number_precision_loss"
    assert detail["requestedType"] == "number" and EXACT not in json.dumps(feedback)
    state = next(iter(result["coverage"]["tableInterpretation"].values()))
    assert len(state["layout"]["history"]) == state["layout"]["attempts"] == 1
    assert state["structure"]["attempts"] == 2
    if repair:
        assert result["data"]["records"] == [
            {"code": "00007", "quantity": EXACT},
            {"code": "00008", "quantity": "4.5000"},
        ]
        assert result["extraction"]["status"] == "complete"
    else:
        assert result["data"] is None and result["extraction"]["status"] == "partial"
    used = len(model.requests)
    assert run(states[-1])["data"] == result["data"]
    assert len(model.requests) == used


@pytest.mark.parametrize("value_type", ["number", "integer", "boolean"])
@pytest.mark.parametrize("cache", [False, True])
def test_numeric_native_formula_errors_stay_in_mapping_without_evaluating_a_cache(
    value_type, cache
):
    doc, region, response = formula_table(cache=cache)
    response["record"]["columns"][1].update(bindingMode="source", valueType=value_type)
    before = deepcopy((doc, response))
    _, ir = structural_ir(response, doc, region)
    with pytest.raises(CompileError) as caught:
        compile_region(ir, doc, region)
    feedback = structure_feedback(caught.value, doc, region)
    assert (
        json.loads(feedback[1].split(":", 1)[1])["readFailure"]
        == "formula_expression_requires_text"
    )
    assert not needs_layout_review(caught.value, doc, region)
    assert (doc, response) == before


def test_verified_cause_cannot_be_forged_or_retained_after_the_source_read_changes():
    doc = observe(source_file("xlsx"), "xlsx")
    region = prepare_regions(doc, context_chars=16000)[0]
    ref = next(
        r
        for r, n in doc.nodes.items()
        if n.get("semantic", {}).get("cell", {}).get("coordinate") == "B2"
    )
    bid = preferred_binding(doc.bindings, ref)
    error = CompileError(
        "binding_cannot_represent_requested_type",
        selection={
            "bindingId": bid,
            "requestedType": "number",
            "readFailure": "formula_requires_text",
        },
    )

    def detail():
        return json.loads(structure_feedback(error, doc, region)[1].split(":", 1)[1])

    assert detail()["readFailure"] == "number_precision_loss"
    doc.nodes[ref]["semantic"]["value"]["value"] = "Private heading"
    assert "readFailure" not in detail() and needs_layout_review(error, doc, region)
    assert "Private" not in json.dumps(structure_feedback(error, doc, region))


def test_rounded_native_scalar_reports_its_source_problem_without_permitting_text_laundering():
    nodes = {
        "cell": {
            "semantic": {
                "value": {
                    "kind": "number",
                    "rawType": "n",
                    "raw": EXACT,
                    "value": float(EXACT),
                }
            }
        }
    }
    for representation in ["text", "native", "number"]:
        with pytest.raises(BindingReadError) as caught:
            resolve(
                SourceBinding(
                    sourceRef="cell", path="/semantic/value/value", representation=representation
                ),
                nodes,
            )
        assert caught.value.code == "native_number_precision_loss"
    assert resolve(
        SourceBinding(sourceRef="cell", path="/semantic/value/raw", representation="text"), nodes
    ) == (EXACT, EXACT)
