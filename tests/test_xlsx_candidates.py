"""Native spreadsheet candidate boundaries; scripted replies are not AI qualification."""

import io
import json
from copy import deepcopy
from datetime import datetime

import pytest
from openpyxl import Workbook

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.observe import observe_document
from document_files.engine import extract_structure
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.bindings import resolve
from document_files.interpretation.compiler import preferred_binding
from document_files.interpretation.contracts import ExtractionOptions, SourceBinding
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions


def sheet(tmp_path, rows):
    path = tmp_path / "register.xlsx"
    book = Workbook()
    book.active.title = "Register"
    for row in rows:
        book.active.append(row)
    book.save(path)
    book.close()
    original = path.read_bytes()
    native = extract_structure(path)
    nodes = {f"n{n['ordinal']}": n for n in native["units"]}
    doc = observe_document(original, "xlsx", nodes)
    assert path.read_bytes() == original
    assert all(doc.nodes[ref] == node for ref, node in nodes.items())
    return original, doc


def read(doc, binding):
    return resolve(
        SourceBinding.model_validate(
            {k: binding[k] for k in ("sourceRef", "path", "start", "end")}
        ),
        doc.nodes,
    )[0]


def cell_ref(doc, coordinate):
    return next(
        ref
        for ref, node in doc.nodes.items()
        if node.get("semantic", {}).get("cell", {}).get("coordinate") == coordinate
    )


class PlainRegisterModel:
    identity = {"adapter": "xlsx-boundary-test", "model": "scripted-not-qualified"}

    def __init__(self):
        self.calls = 0

    def infer(self, request):
        self.calls += 1
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("tableStage") == "structure":
            table_ref, table = next(iter(payload["tables"].items()))
            cells = table["cells"]
            if isinstance(cells, dict):
                cells = [dict(zip(cells["columns"], r, strict=True)) for r in cells["rows"]]
            headers = {c["col"]: c["sourceRef"] for c in cells if c["row"] == 0}
            value = {
                "regionId": payload["regionId"],
                "tableKind": "record_table",
                "record": {
                    "id": "staff",
                    "key": "staff",
                    "label": "Staff",
                    "tableRef": table_ref,
                    "rowStart": 0,
                    "rowEnd": 2,
                    "definitionRefs": list(headers.values()),
                    "rowRoles": [
                        {"row": row, "role": "header" if row == 0 else "data"} for row in range(3)
                    ],
                    "columns": [
                        {
                            "id": key,
                            "key": key,
                            "label": label,
                            "column": col,
                            "valueType": "string",
                            "definitionRefs": [headers[col]],
                        }
                        for col, key, label in [(0, "name", "Name"), (1, "state", "State")]
                    ],
                },
            }
        else:
            assert payload["meaningPhase"] == "selection"
            value = {
                "sourceDecisions": {
                    s["sourceRef"]: {
                        "decision": "no_additional_meaning",
                        "explanation": "Scripted plain labels and cell values",
                    }
                    for s in payload["meaningSources"]
                }
            }
        return InferenceResponse(json.dumps(value), {})


def test_plain_native_xlsx_completes_without_coordinate_value_aliases(tmp_path):
    raw, doc = sheet(tmp_path, [["Name", "State"], ["Alice", "active"], ["Bob", "paused"]])
    table_region = next(r for r in prepare_regions(doc, context_chars=16000) if r.get("tableRef"))
    for coordinate in ("A2", "B2", "A3", "B3"):
        ref = cell_ref(doc, coordinate)
        assert [
            b for b in table_region["requiredBindingIds"] if doc.bindings[b]["sourceRef"] == ref
        ] == [preferred_binding(doc.bindings, ref)]
    model = PlainRegisterModel()
    result = extract_schema_from_stream(
        AnalysisJob(
            job_id="xlsx-candidates", input=AnalysisInput.from_bytes(raw, format_id="xlsx")
        ),
        io.BytesIO(raw),
        model_client=model,
        options=ExtractionOptions(maxModelCalls=2, reconstructionContext=False),
    )
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["validation"]["valid"]
    assert result["data"] == {
        "staff": [{"name": "Alice", "state": "active"}, {"name": "Bob", "state": "paused"}]
    }
    assert model.calls == 2
    assert len(result["valueEvidence"]) == 4
    for item in result["valueEvidence"]:
        binding = SourceBinding.model_validate(item["binding"])
        assert binding.path == "/semantic/value/value"
        assert resolve(binding, result["document"]["nodes"])[1] == item["raw"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Alice", []),
        ("A2=Alice", [("A2", "Alice")]),  # This address-like label really is cell content.
        (
            "  길이: 001.2300 ; 메모: 앞=뒤\n빈칸:  \n식: =SUM(A1:A2)",
            [("길이", "001.2300"), ("메모", "앞=뒤"), ("빈칸", ""), ("식", "=SUM(A1:A2)")],
        ),
        ("지역: 서울\t동부\n이름: 가\u0301", [("지역", "서울\t동부"), ("이름", "가\u0301")]),
    ],
)
def test_real_cell_string_candidates_use_exact_native_content(tmp_path, text, expected):
    _, doc = sheet(tmp_path, [[text]])
    ref = cell_ref(doc, "A1")
    values = [
        b
        for b in doc.bindings.values()
        if b["sourceRef"] == ref and b.get("candidateRole") == "value"
    ]
    assert [(read(doc, doc.bindings[b["labelRefs"][0]]), read(doc, b)) for b in values] == expected
    assert all(b["path"] == "/semantic/value/value" for b in values)
    assert all((b["start"] == b["end"]) == b["blank"] for b in values)
    region = next(r for r in prepare_regions(doc, context_chars=16000) if r.get("tableRef"))
    required = [doc.bindings[bid] for bid in region["requiredBindingIds"]]
    assert all(b in required for b in values)  # Genuine inner values are not silently ignored.


@pytest.mark.parametrize(
    "value", [0, False, 0.125, datetime(2026, 1, 2, 12, 30), "=SUM(A2:A3)", "#DIV/0!"]
)
def test_nonstring_native_scalars_do_not_acquire_display_delimiter_candidates(tmp_path, value):
    _, doc = sheet(tmp_path, [[value]])
    ref = cell_ref(doc, "A1")
    candidates = [b for b in doc.bindings.values() if b["sourceRef"] == ref]
    assert not any(b.get("candidateRole") in {"value", "label", "lexeme"} for b in candidates)
    assert any(b.get("candidateRole") == "native_value" for b in candidates)
    if value == "=SUM(A2:A3)":
        assert read(doc, doc.bindings[preferred_binding(doc.bindings, ref, "formula")]) == value


def test_exact_number_formula_and_cache_bindings_survive_candidate_change():
    nodes = {
        "number": {
            "text": "A1=0.1",
            "semantic": {
                "sheet": {"name": "Ledger", "index": 1},
                "cell": {"row": 1, "column": 1, "indexBase": 1},
                "value": {
                    "kind": "number",
                    "value": 0.1,
                    "rawType": "n",
                    "raw": "0.10000000000000000001",
                },
            },
        },
        "formula": {
            "text": "A2==SUM(A3:A4)",
            "semantic": {
                "sheet": {"name": "Ledger", "index": 1},
                "cell": {"row": 2, "column": 1, "indexBase": 1},
                "value": {
                    "kind": "formula",
                    "formula": "=SUM(A3:A4)",
                    "cachedValue": {
                        "kind": "number",
                        "value": 0.1,
                        "raw": "0.1000",
                        "rawType": "n",
                    },
                },
            },
        },
    }
    before = deepcopy(nodes)
    doc = observe_document(b"scripted native observations", "xlsx", nodes)
    assert nodes == before
    assert (
        read(doc, doc.bindings[preferred_binding(doc.bindings, "number")])
        == "0.10000000000000000001"
    )
    assert (
        read(doc, doc.bindings[preferred_binding(doc.bindings, "formula", "formula")])
        == "=SUM(A3:A4)"
    )
    assert read(doc, doc.bindings[preferred_binding(doc.bindings, "formula", "cached")]) == "0.1000"


def test_coordinate_like_plain_text_is_not_treated_as_generated_sheet_metadata():
    doc = observe_document(b"A2=Alice", "txt", {})
    values = [b for b in doc.bindings.values() if b.get("candidateRole") == "value"]
    assert len(values) == 1
    assert read(doc, values[0]) == "Alice"
    assert values[0]["path"] == "/text"
