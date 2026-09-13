"""Effective column context never replays discarded proposals or drops stored evidence."""

from copy import deepcopy

import pytest
from test_regional_interpretation import _table
from test_table_protocol import TableModel, execute, record_response
from test_table_source_wire import long_html

from document_files.interpretation.compiler import compile_region
from document_files.interpretation.regions import compiled_table_mapping
from document_files.interpretation.table_source_wire import expand_table_sources


def test_context_uses_compiler_corrected_columns_and_keeps_raw_record_provenance():
    doc, region, ir = _table()
    repeat = ir.repeats[0]
    # Data-cell references are already handled by the compiler's content-row rule.
    data_ref = doc.tables[repeat.tableRef]["cells"][-1]["sourceRef"]
    repeat.definitionRefs = [*repeat.definitionRefs, data_ref]
    repeat.columns[-1].definitionRefs = [*repeat.columns[-1].definitionRefs, data_ref]
    before = deepcopy(ir)
    compiled = compile_region(ir, doc, region)
    raw_compiled = deepcopy(compiled)
    mapping = compiled_table_mapping(repeat, compiled)
    assert mapping["key"] == repeat.key and mapping["label"] == repeat.label
    assert data_ref not in mapping["columns"][-1]["definitionRefs"]
    assert data_ref in repeat.definitionRefs and data_ref in repeat.columns[-1].definitionRefs
    assert any(c["code"] == "column_definition_content_cells_dropped" for c in compiled.corrections)
    assert any(
        d["id"] == region["id"] + ":" + repeat.id and data_ref in d["sourceRefs"]
        for d in compiled.semantics
    )
    assert ir == before and compiled == raw_compiled


def test_mapping_keeps_every_effective_reference_without_a_count_or_header_filter():
    doc, region, ir = _table()
    repeat = ir.repeats[0]
    compiled = compile_region(ir, doc, region)
    definition = next(
        d for d in compiled.semantics if d["id"] == region["id"] + ":" + repeat.columns[0].id
    )
    definition["sourceRefs"] = [f"prior-source-{i}" for i in range(150)]
    mapping = compiled_table_mapping(repeat, compiled)
    assert mapping["columns"][0]["definitionRefs"] == definition["sourceRefs"]
    mapping["columns"][0]["definitionRefs"].clear()
    assert len(definition["sourceRefs"]) == 150


def test_missing_compiled_definition_does_not_fall_back_to_an_unchecked_proposal():
    doc, region, ir = _table()
    compiled = compile_region(ir, doc, region)
    compiled.semantics.clear()
    with pytest.raises(KeyError):
        compiled_table_mapping(ir.repeats[0], compiled)


def test_engine_preserves_all_rows_without_readding_discarded_definition_sources():
    import json

    from document_files.interpretation.backends import InferenceResponse

    class BroadProvenance(TableModel):
        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if payload.get("tableStage") != "structure":
                return super().infer(request)
            self.requests.append(request)
            restored = expand_table_sources(payload)
            table = next(iter(restored["tables"].values()))
            cells = table["cells"]
            if isinstance(cells, dict):
                cells = [dict(zip(cells["columns"], row, strict=True)) for row in cells["rows"]]
            headers = table.get("headerCells", [])
            if isinstance(headers, dict):
                headers = [
                    dict(zip(headers["columns"], row, strict=True)) for row in headers["rows"]
                ]
            table["cells"] = [*cells, *headers]
            value = record_response(restored)
            value["record"]["rowStart"] = min(c["row"] for c in cells)
            value["record"]["definitionRefs"] = list(restored["nodeIds"])
            data_refs = [c["sourceRef"] for c in cells if not c.get("isHeader")]
            for column in value["record"]["columns"]:
                column["definitionRefs"] += data_refs
            if "sameTableMapping" in payload:
                proposed = {
                    r for c in payload["sameTableMapping"]["columns"] for r in c["definitionRefs"]
                }
                assert proposed <= {c["sourceRef"] for c in headers}
            return InferenceResponse(json.dumps(value), {})

    model, states = BroadProvenance(), []
    result = execute(
        model, content=long_html(), states=states, contextChars=11000, maxModelCalls=60
    )
    assert result["data"]["records"] == [{"code": f"{i:04}", "size": "1.2300"} for i in range(50)]
    assert not any(i["code"] == "region_context_budget_exceeded" for i in result["issues"])
    assert any("sameTableMapping" in json.loads(r.messages[-1]["content"]) for r in model.requests)


def test_output_contract_is_built_after_preceding_table_context_is_added(monkeypatch):
    import json

    import document_files.interpretation.engine as engine

    events = []
    original_add, original_schema = engine.add_table_definition_context, engine.region_output_schema

    def add(observation, region, refs):
        original_add(observation, region, refs)
        events.append((region["id"], "context"))

    def schema(observation, region, *args, **kwargs):
        events.append((region["id"], "schema"))
        return original_schema(observation, region, *args, **kwargs)

    class Checked(TableModel):
        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if "sameTableMapping" in payload:
                rid = payload["regionId"]
                relevant = [kind for key, kind in events if key == rid]
                assert "context" in relevant and relevant[-1] == "schema"
            if payload.get("tableStage") == "structure":
                from document_files.interpretation.backends import InferenceResponse

                self.requests.append(request)
                restored = expand_table_sources(payload)
                table = next(iter(restored["tables"].values()))
                cells = table["cells"]
                if isinstance(cells, dict):
                    cells = [dict(zip(cells["columns"], r, strict=True)) for r in cells["rows"]]
                headers = table.get("headerCells", [])
                if isinstance(headers, dict):
                    headers = [
                        dict(zip(headers["columns"], r, strict=True)) for r in headers["rows"]
                    ]
                table["cells"] = [*cells, *headers]
                value = record_response(restored)
                value["record"]["rowStart"] = min(c["row"] for c in cells)
                return InferenceResponse(json.dumps(value), {})
            return super().infer(request)

    monkeypatch.setattr(engine, "add_table_definition_context", add)
    monkeypatch.setattr(engine, "region_output_schema", schema)
    model = Checked()
    execute(model, content=long_html(), contextChars=11000, maxModelCalls=60)
    assert any(kind == "context" for _, kind in events)
