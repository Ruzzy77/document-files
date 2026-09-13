"""Formula-read consistency in the table wire; no model-quality claim."""

import copy
import io
import json
import zipfile

import pytest
from jsonschema import Draft202012Validator
from openpyxl import Workbook
from test_native_merged_geometry import observe

from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.regions import prepare_regions
from document_files.interpretation.table_protocol import structural_ir, structure_schema
from document_files.interpretation.table_structure_feedback import structure_feedback


def formula_table(*, cache=False):
    book = Workbook()
    book.active.append(["Input", "Expression"])
    book.active.append([1, "=A2+1"])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    raw = stream.getvalue()
    if cache:
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, "w") as target:
            for item in source.infolist():
                data = source.read(item)
                if item.filename == "xl/worksheets/sheet1.xml":
                    assert b"<f>A2+1</f><v></v>" in data
                    data = data.replace(b"<f>A2+1</f><v></v>", b"<f>A2+1</f><v>2.0000</v>")
                target.writestr(item, data)
        raw = output.getvalue()
    doc = observe(raw, "xlsx")
    region = prepare_regions(doc, context_chars=16000)[0]
    cells = doc.tables[region["tableRef"]]["cells"]
    headers = {c["col"]: c["sourceRef"] for c in cells if c["row"] == 0}
    response = {
        "regionId": region["id"],
        "tableKind": "record_table",
        "record": {
            "id": "records",
            "key": "records",
            "label": "Records",
            "tableRef": region["tableRef"],
            "rowStart": 0,
            "rowEnd": 1,
            "definitionRefs": list(headers.values()),
            "rowRoles": [{"row": 0, "role": "header"}, {"row": 1, "role": "data"}],
            "columns": [
                {
                    "id": "input",
                    "key": "input",
                    "label": "Input",
                    "column": 0,
                    "valueType": "integer",
                    "definitionRefs": [headers[0]],
                },
                {
                    "id": "expression",
                    "key": "expression",
                    "label": "Expression",
                    "column": 1,
                    "bindingMode": "formula",
                    "valueType": "string",
                    "definitionRefs": [headers[1]],
                },
            ],
        },
    }
    return doc, region, response


@pytest.mark.parametrize("mode", ["source", "text", "formula", "cached"])
@pytest.mark.parametrize(
    "value_type", ["string", "native", "decimal", "integer", "number", "boolean", "null"]
)
def test_wire_and_parser_agree_on_only_the_formula_type_restriction(mode, value_type):
    doc, region, value = formula_table()
    column = value["record"]["columns"][1]
    column.update(bindingMode=mode, valueType=value_type)
    expected = mode != "formula" or value_type in {"string", "native"}
    assert Draft202012Validator(structure_schema(doc, region)).is_valid(value) == expected
    before = copy.deepcopy((doc, value))
    if expected:
        _, candidate = structural_ir(value, doc, region)
        assert candidate.repeats[0].columns[1].valueType == value_type
        # Other modes still need actual source validation, not automatic approval.
    else:
        with pytest.raises(CompileError, match="table_structure_formula_requires_text") as caught:
            structural_ir(value, doc, region)
        feedback = structure_feedback(caught.value, doc, region)
        assert json.loads(feedback[1].split(":", 1)[1]) == {
            "column": 1,
            "requestedType": value_type,
            "bindingMode": "formula",
        }
        assert "Expression" not in json.dumps(feedback) and "sourceCell" not in feedback[1]
    assert (doc, value) == before


@pytest.mark.parametrize("cache", [False, True])
@pytest.mark.parametrize("value_type", ["string", "native"])
def test_formula_expression_is_preserved_and_never_substituted_by_a_cache(cache, value_type):
    doc, region, value = formula_table(cache=cache)
    value["record"]["columns"][1]["valueType"] = value_type
    _, candidate = structural_ir(value, doc, region)
    result = compile_region(candidate, doc, region)
    assert result.data == {"records": [{"input": 1, "expression": "=A2+1"}]}
    evidence = next(e for e in result.value_evidence if e["target"]["path"].endswith("/expression"))
    assert evidence["binding"]["path"] == "/semantic/value/formula"
    assert evidence["raw"] == "=A2+1"
    if cache:
        value["record"]["columns"][1].update(bindingMode="cached", valueType="decimal")
        _, cached = structural_ir(value, doc, region)
        assert compile_region(cached, doc, region).data["records"][0]["expression"] == "2.0000"


def test_omitted_binding_mode_keeps_ordinary_source_default():
    doc, region, value = formula_table()
    value["record"]["columns"][1].pop("bindingMode")
    assert Draft202012Validator(structure_schema(doc, region)).is_valid(value)
    _, candidate = structural_ir(value, doc, region)
    assert candidate.repeats[0].columns[1].bindingMode == "source"
