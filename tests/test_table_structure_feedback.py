"""Source-local diagnostics and scripted repair, not model-quality approval."""

import copy
import io
import json

import pytest
from openpyxl import Workbook
from test_native_merged_geometry import observe
from test_table_protocol import TEXT_RECORDS, TableModel, execute

from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.table_structure_feedback import structure_feedback


def native_cell():
    book = Workbook()
    sheet = book.active
    sheet.append(["Private heading", "Field"])
    sheet.append([None, "Private value"])
    sheet.merge_cells("A1:A2")
    output = io.BytesIO()
    book.save(output)
    book.close()
    doc = observe(output.getvalue(), "xlsx")
    region = prepare_regions(doc, context_chars=16000)[0]
    ref = next(
        ref
        for ref, node in doc.nodes.items()
        if node.get("semantic", {}).get("cell", {}).get("coordinate") == "A1"
    )
    bid = next(
        bid
        for bid, binding in doc.bindings.items()
        if bid in region["bindingIds"]
        and binding["sourceRef"] == ref
        and binding["path"] == "/semantic/value/value"
    )
    error = CompileError(
        "binding_cannot_represent_requested_type",
        selection={"bindingId": bid, "requestedType": "number"},
    )
    return doc, region, ref, bid, error


def test_native_feedback_uses_offered_source_and_anchor_geometry_without_values_or_mutation():
    doc, region, ref, bid, error = native_cell()
    before = copy.deepcopy((doc, region, error.selection))
    feedback = structure_feedback(error, doc, region)
    assert feedback[0] == str(error)
    detail = json.loads(feedback[1].split(":", 1)[1])
    assert detail == {
        "sourceRef": ref,
        "path": "/semantic/value/value",
        "requestedType": "number",
        "sourceCell": {"row": 0, "column": 0, "rowSpan": 2, "columnSpan": 1},
    }
    assert "Private" not in json.dumps(feedback) and "bindingId" not in json.dumps(feedback)
    assert (doc, region, error.selection) == before


@pytest.mark.parametrize("mutation", ["foreign_binding", "foreign_node", "foreign_table", "code"])
def test_outside_region_or_unrelated_error_never_acquires_a_cell_location(mutation):
    doc, region, ref, bid, error = native_cell()
    if mutation == "foreign_binding":
        region["bindingIds"].remove(bid)
    elif mutation == "foreign_node":
        region["nodeIds"].remove(ref)
    elif mutation == "foreign_table":
        region["tableRef"] = "not-offered"
    else:
        error = CompileError("duplicate_data_property", selection=error.selection)
    assert structure_feedback(error, doc, region) == [str(error)]
    assert structure_feedback(ValueError("private model text"), doc, region) == [
        "invalid_table_contract"
    ]


class WrongTypeModel(TableModel):
    def __init__(self, *, repair=True):
        super().__init__()
        self.repair = repair

    def infer(self, request):
        response = super().infer(request)
        payload = json.loads(request.messages[-1]["content"])
        if payload["tableStage"] != "structure":
            return response
        value = json.loads(response.text)
        for column in value["record"]["columns"]:
            column["valueType"] = "string"
        if len(self.requests) == 1 or not self.repair:
            value["record"]["columns"][1]["valueType"] = "number"
        return InferenceResponse(json.dumps(value), response.usage)


@pytest.mark.parametrize("repair", [True, False])
def test_actual_failed_read_reaches_bounded_repair_and_persists_if_unrepaired(repair):
    model = WrongTypeModel(repair=repair)
    states = []
    result = execute(model, content=TEXT_RECORDS, states=states, maxModelCalls=3)
    payload = json.loads(model.requests[1].messages[-1]["content"])
    feedback = payload["repairFeedback"]
    detail = json.loads(feedback[1].split(":", 1)[1])
    assert detail["sourceRef"] in payload["nodes"]
    assert detail["sourceCell"] == {"row": 1, "column": 1, "rowSpan": 1, "columnSpan": 1}
    assert detail["requestedType"] == "number" and detail["path"] == "/text"
    assert "active" not in json.dumps(feedback) and "paused" not in json.dumps(feedback)
    if repair:
        assert result["extraction"]["status"] == "complete"
        assert result["data"]["records"] == [
            {"code": "Alice", "size": "active"},
            {"code": "Bob", "size": "paused"},
        ]
        assert len(model.requests) == 3
    else:
        assert result["extraction"]["status"] == "partial" and result["data"] is None
        assert len(model.requests) == 2
        assert states[-1]["tableStages"][payload["regionId"]]["structure"]["feedback"] == feedback
        assert any(issue.get("errors") == feedback for issue in result["issues"])
