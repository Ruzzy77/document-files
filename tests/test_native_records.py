"""Source-grounded native prose fixtures; not independent AI quality approval."""

import copy
import hashlib

import pytest
from jsonschema import Draft202012Validator

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.compiler import CompileError, combine_regions, compile_region
from document_files.interpretation.native_records import loses_logical_content
from document_files.interpretation.semantic_types import RegionInterpretation


def quote(ref, text, **more):
    return {"sourceRef": ref, "text": text, **more}


def fixture():
    doc = ObservationDocument(provenance={"format": "hwpx"})
    for ref, text in {
        "meta": "Reference is 0007; unit is mm.",
        "a": "Basil — requested 8; received 8; length 12.5000.",
        "b": "Marigold — requested 5; received: ; length 2.000.",
        "note": "Keep both items dry. For Marigold only, check the label.",
    }.items():
        doc.node(ref, text)
    start = doc.nodes["b"]["text"].index("; length")
    blank = doc.bind("b", start=start, end=start, candidateRole="value", blank=True)
    region = {"id": "r", "nodeIds": list(doc.nodes), "bindingIds": list(doc.bindings)}
    columns = [
        {"id": c, "key": c, "label": c, "valueType": kind, "definitionRefs": ["a", "b"]}
        for c, kind in [
            ("name", "string"),
            ("requested", "integer"),
            ("received", "integer"),
            ("length", "decimal"),
            ("date", "string"),
        ]
    ]
    rows = []
    for ref, name, n, size in [("a", "Basil", "8", "12.5000"), ("b", "Marigold", "5", "2.000")]:
        values = [
            {"columnId": "name", "sourceQuote": quote(ref, name)},
            {"columnId": "requested", "sourceQuote": quote(ref, n, occurrence=0)},
            {
                "columnId": "received",
                **(
                    {"sourceQuote": quote(ref, n, occurrence=1)}
                    if ref == "a"
                    else {"bindingId": blank, "status": "blank"}
                ),
            },
            {"columnId": "length", "sourceQuote": quote(ref, size)},
            {"columnId": "date", "status": "absent"},
        ]
        rows.append(
            {
                "id": ref,
                "sourceQuotes": [quote(ref, doc.nodes[ref]["text"])],
                "values": [
                    {
                        "bindingId": None,
                        "sourceQuote": None,
                        "status": "present",
                        "sourceRefs": [ref],
                        **v,
                    }
                    for v in values
                ],
            }
        )
    value = {
        "regionId": "r",
        "fields": [
            {
                "id": "reference",
                "key": "reference",
                "label": "Reference",
                "definitionRefs": ["meta"],
                "bindingId": None,
                "status": "present",
                "valueType": "string",
                "sourceQuote": quote("meta", "0007"),
            }
        ],
        "logicalRecords": [
            {
                "id": "items",
                "key": "items",
                "label": "Items",
                "definitionRefs": ["a", "b"],
                "columns": columns,
                "rows": list(reversed(rows)),
            }
        ],
        "meanings": [
            {
                "id": "unit",
                "kind": "unit",
                "description": "mm",
                "sourceRefs": ["meta"],
                "fieldIds": ["length"],
                "repeatIds": [],
            },
            {
                "id": "condition",
                "kind": "condition",
                "description": "check the label",
                "sourceRefs": ["note"],
                "repeatIds": ["items"],
                "rowStart": 1,
                "rowEnd": 1,
            },
        ],
        "documentElements": [
            {
                "sourceRef": r,
                "role": "paragraph",
                "level": None,
                "captionOf": None,
                "status": "interpreted",
            }
            for r in doc.nodes
        ],
    }
    return doc, region, value


def compile_value(doc, region, value, **kw):
    return compile_region(RegionInterpretation.model_validate(value), doc, region, **kw)


def test_quotes_and_logical_occurrences_preserve_values_order_scopes_and_originals():
    doc, region, value = fixture()
    original = copy.deepcopy((doc, region, value))
    result = compile_value(doc, region, value)
    assert result.data == {
        "reference": "0007",
        "items": [
            {"name": "Basil", "requested": 8, "received": 8, "length": "12.5000", "date": None},
            {"name": "Marigold", "requested": 5, "received": "", "length": "2.000", "date": None},
        ],
    }
    assert (doc, region, value) == original and not doc.tables
    assert not result.issues
    assert combine_regions([result])["errors"] == []
    for evidence in result.value_evidence:
        if evidence["status"] == "present":
            binding = evidence["binding"]
            assert (
                doc.nodes[binding["sourceRef"]]["text"][binding["start"] : binding["end"]]
                == evidence["raw"]
            )
            assert evidence["transformation"] == "exact_source_quote"
    semantics = {s["id"].split(":")[-1]: s for s in result.semantic_details}
    assert [t["path"] for t in semantics["unit"]["scope"]] == ["/items/0/length", "/items/1/length"]
    assert {t["path"] for t in semantics["condition"]["scope"]} == {
        f"/items/1/{c}" for c in ["name", "requested", "received", "length", "date"]
    }
    assert result.row_scopes["items"]["tableRef"] is None
    assert result.row_scopes["items"]["recordRef"] == "r:items"
    assert result.logical_coverage["items"]["rows"][0]["occurrenceId"] == "a"
    assert all(v["basis"] == "verified_source_quote" for v in result.grounded_bindings.values())
    assert compile_value(doc, region, value) == result


@pytest.mark.parametrize(
    "bad,error",
    [
        ("ambiguous", "quote_occurrence"),
        ("invented", "quote_not_in_source"),
        ("unknown", "unknown_quote_source"),
        ("both", "no_binding_id"),
        ("nonpresent", "requires_present"),
        ("outside", "binding_outside_occurrence"),
        ("evidence", "evidence_outside_occurrence"),
        ("overlap", "occurrences_overlap"),
        ("duplicate_column", "duplicate_component_id"),
        ("missing_cell", "do_not_match_columns"),
        ("duplicate_cell", "do_not_match_columns"),
        ("empty_without_evidence", "rows_or_empty_evidence"),
    ],
)
def test_invalid_grounding_is_rejected_without_mutating_source(bad, error):
    doc, region, value = fixture()
    record = value["logicalRecords"][0]
    a, b = record["rows"][1], record["rows"][0]
    if bad == "ambiguous":
        a["values"][1]["sourceQuote"].pop("occurrence")
    elif bad == "invented":
        a["values"][0]["sourceQuote"]["text"] = "BASIL"
    elif bad == "unknown":
        a["values"][0]["sourceQuote"]["sourceRef"] = "foreign"
    elif bad == "both":
        a["values"][0]["bindingId"] = next(iter(doc.bindings))
    elif bad == "nonpresent":
        a["values"][0]["status"] = "absent"
    elif bad == "outside":
        a["values"][0]["sourceQuote"] = quote("b", "Marigold")
    elif bad == "evidence":
        a["values"][0]["sourceRefs"] = ["note"]
    elif bad == "overlap":
        b["sourceQuotes"].append(a["sourceQuotes"][0])
    elif bad == "duplicate_column":
        record["columns"].append(record["columns"][0])
    elif bad == "missing_cell":
        a["values"].pop()
    elif bad == "duplicate_cell":
        a["values"].append(a["values"][0])
    elif bad == "empty_without_evidence":
        record["rows"] = []
    original = copy.deepcopy(doc)
    with pytest.raises(CompileError, match=error):
        compile_value(doc, region, value)
    assert doc == original


def test_unicode_views_exact_occurrences_and_no_global_lexeme_enable():
    doc, region, value = fixture()
    text = "outside 0007 🙂가 0007 end"
    doc.nodes["meta"]["text"] = text
    region["nodeViews"] = {"meta": {"start": 13, "end": len(text)}}
    result = compile_value(doc, region, value)
    ev = result.value_evidence[0]
    assert ev["binding"]["start"] == text.rindex("0007")
    assert ev["raw"] == "0007" and result.data["reference"] == "0007"
    value["fields"][0]["sourceQuote"]["text"] = "outside"
    with pytest.raises(CompileError, match="quote_not_in_source"):
        compile_value(doc, region, value)
    assert set(region["bindingIds"]) == set(doc.bindings)


@pytest.mark.parametrize(
    "region_change,fmt",
    [({"tableRef": "t"}, "hwpx"), ({}, "xlsx"), ({"tableContextRef": "t"}, "hwp")],
)
def test_non_native_or_physical_table_path_cannot_use_logical_extension(region_change, fmt):
    doc, region, value = fixture()
    doc.provenance["format"] = fmt
    with pytest.raises(CompileError, match="requires_native_text_region"):
        compile_value(doc, region | region_change, value)


def test_empty_records_keep_exact_evidence_and_cannot_vanish_in_repair():
    doc, region, value = fixture()
    value["meanings"] = []
    record = value["logicalRecords"][0]
    record["rows"] = []
    record["emptySourceQuotes"] = [quote("b", doc.nodes["b"]["text"])]
    result = compile_value(doc, region, value)
    assert result.data["items"] == [] and not result.row_scopes
    assert (
        result.logical_coverage["items"]["emptySourceRanges"][0]["textSHA256"]
        == hashlib.sha256(doc.nodes["b"]["text"].encode()).hexdigest()
    )
    assert combine_regions([result])["errors"] == []
    value["logicalRecords"] = []
    assert loses_logical_content(result, compile_value(doc, region, value))


def test_unbound_missing_row_or_column_cannot_vanish_in_repair():
    doc, region, value = fixture()
    for row in value["logicalRecords"][0]["rows"]:
        for cell in row["values"]:
            cell.update(status="absent", bindingId=None, sourceQuote=None)
    result = compile_value(doc, region, value)
    assert not loses_logical_content(result, copy.deepcopy(result))
    changed = copy.deepcopy(value)
    changed["logicalRecords"][0]["rows"].pop()
    assert loses_logical_content(result, compile_value(doc, region, changed))
    changed = copy.deepcopy(value)
    changed["logicalRecords"][0]["columns"].pop()
    for row in changed["logicalRecords"][0]["rows"]:
        row["values"].pop()
    assert loses_logical_content(result, compile_value(doc, region, changed))


def test_native_roles_cannot_be_bypassed_by_logical_value_quote():
    doc, region, value = fixture()
    value["documentElements"][1].update(role="title", level=0)
    value["logicalRecords"][0]["rows"][1]["values"][0]["sourceQuote"] = quote(
        "a", doc.nodes["a"]["text"]
    )
    with pytest.raises(CompileError, match="document_role_value_conflict"):
        compile_value(doc, region, value)


def test_scope_wire_selects_source_ordered_logical_record_without_table_geometry():
    from document_files.interpretation.integration import apply_scope_decision, build_scope_tasks
    from document_files.interpretation.scope_axis_wire import prepare_scope_axis_wire
    from document_files.interpretation.scope_source_binding import bind_scope_sources

    doc, region, value = fixture()
    value["meanings"] = [value["meanings"][1] | {"repeatIds": [], "rowStart": None, "rowEnd": None}]
    result = compile_value(doc, region, value)
    task = build_scope_tasks(doc, [region], [result])[0]
    wire = prepare_scope_axis_wire([task])
    handle, record = next(iter(wire.records[task.id].items()))
    boundary = next(r for r in record["rows"] if r.endswith("dataRow2"))
    choice = {
        "taskId": task.id,
        "decision": "apply",
        "explanation": "The second item only.",
        "recordScopes": [
            {
                "recordHandle": handle,
                "parts": [
                    {
                        "rowCoverage": {
                            "kind": "rowRange",
                            "rowStartRef": boundary,
                            "rowEndRef": boundary,
                        },
                        "columnCoverage": {"kind": "allMappedColumns"},
                    }
                ],
            }
        ],
    }
    if wire.standalone[task.id]:
        choice["targetHandles"] = []
    Draft202012Validator(wire.contract).validate(choice)
    decision, _ = bind_scope_sources(
        wire.decode(choice), task, [result], expected_fingerprint=task.fingerprint
    )
    changed, applied = apply_scope_decision([result], task, decision)
    assert applied and changed[0].data == result.data
    assert all(t["path"].startswith("/items/1/") for t in changed[0].semantic_details[0]["scope"])
    boundaries = wire.payload["rowBoundaryCandidates"]
    assert all(r["tableRef"] is None and r["recordRef"] == "r:items" for r in boundaries)


def test_actual_hwpx_product_wire_schema_evidence_and_checkpoint(tmp_path):
    import io
    import json

    from test_document_outline import OutlineModel, make_file, native_wire

    from document_files.api import (
        AnalysisInput,
        AnalysisJob,
        ExtractionOptions,
        extract_schema_from_stream,
    )
    from document_files.interpretation.backends import InferenceResponse

    doc, _, value = fixture()
    raw = make_file(
        tmp_path, blocks=[{"type": "paragraph", "text": n["text"]} for n in doc.nodes.values()]
    )

    class Model(OutlineModel):
        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if "documentContent" not in payload:
                return super().infer(request)
            self.calls += 1
            assert len(payload["nodeIds"]) == 5
            mapping = {
                r: next(
                    n for n in payload["nodeIds"] if payload["nodes"][n]["text"] == node["text"]
                )
                for r, node in doc.nodes.items()
            }

            def remap(obj):
                if isinstance(obj, list):
                    return [remap(v) for v in obj]
                if isinstance(obj, dict):
                    return {
                        k: (
                            [mapping[r] for r in v]
                            if k in {"sourceRefs", "definitionRefs"}
                            else mapping[v]
                            if k == "sourceRef"
                            else remap(v)
                        )
                        for k, v in obj.items()
                    }
                return obj

            response = remap(value)
            response.pop("documentElements")
            for meaning in response["meanings"]:
                for key in ["fieldIds", "groupIds", "repeatIds"]:
                    meaning.setdefault(key, [])
                meaning.setdefault("status", "interpreted")
            response["regionId"] = payload["regionId"]
            blank = next(b for b, v in payload["bindings"].items() if v.get("blank"))
            response["logicalRecords"][0]["rows"][0]["values"][2]["bindingId"] = blank
            response["excludedBindings"] = [
                {
                    "bindingId": b,
                    "role": "narrative",
                    "explanation": "Coarse prose, exact inner values quoted separately.",
                }
                for b in payload["requiredBindingIds"]
                if b != blank
            ]
            response = native_wire(response)
            Draft202012Validator(payload["outputContract"]).validate(response)
            return InferenceResponse(json.dumps(response), {})

    model, states = Model(), []

    def run(restore=None):
        return extract_schema_from_stream(
            AnalysisJob(
                job_id="native-records", input=AnalysisInput.from_bytes(raw, format_id="hwpx")
            ),
            io.BytesIO(raw),
            model_client=model,
            options=ExtractionOptions(reconstructionContext=False, maxModelCalls=6),
            checkpoint=states.append,
            restore=restore,
        )

    result = run()
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["validation"]["valid"]
    assert result["data"] == compile_value(doc, fixture()[1], value).data
    assert model.calls == 2
    ledger = result["coverage"]["nativeContentGrounding"]
    assert sum(len(v["logicalOccurrences"]["items"]["rows"]) for v in ledger.values()) == 2
    frozen = copy.deepcopy(states[-1])
    accepted = next(iter(frozen["accepted"].values()))
    assert accepted["fields"][0]["bindingId"] is None
    assert accepted["fields"][0]["sourceQuote"]["text"] == "0007"
    restored = run(copy.deepcopy(frozen))
    assert model.calls == 2
    for key in ["data", "dataSchema", "valueEvidence", "schemaEvidence"]:
        assert restored[key] == result[key]
    for key in ["logicalRecords", "fields"]:
        broken = copy.deepcopy(frozen)
        item = next(iter(broken["accepted"].values()))
        if key == "fields":
            item[key][0]["sourceQuote"]["text"] = "not in source"
        else:
            item[key][0]["rows"][0]["sourceQuotes"][0]["sourceRef"] = "foreign"
        with pytest.raises(ValueError, match="incompatible"):
            run(broken)
    assert model.calls == 2


def test_logical_records_honor_target_schema_handles_without_new_geometry():
    from document_files.interpretation.compiler import target_catalog

    doc, region, value = fixture()
    target = {
        "type": "object",
        "properties": {
            "ref": {"type": "string"},
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "plant": {"type": "string"},
                        "requested": {"type": "integer"},
                        "received": {"type": ["integer", "string"]},
                        "length": {"type": "string"},
                        "date": {"type": ["string", "null"]},
                    },
                },
            },
        },
    }
    handles = {tuple(v["tokens"]): h for h, v in target_catalog(target).items()}
    value["fields"][0]["targetHandle"] = handles[("ref",)]
    record = value["logicalRecords"][0]
    record["targetHandle"] = handles[("entries",)]
    for c in record["columns"]:
        c["targetHandle"] = handles[("entries", "*", "plant" if c["key"] == "name" else c["key"])]
    result = compile_value(doc, region, value, target_schema=target)
    assert result.data["ref"] == "0007" and result.data["entries"][1]["plant"] == "Marigold"
    assert combine_regions([result], target_schema=target)["errors"] == []
    assert not doc.tables


def test_logical_value_budget_fails_before_compilation_or_source_mutation():
    doc, region, value = fixture()
    row = value["logicalRecords"][0]["rows"][0]
    value["logicalRecords"][0]["rows"] = [row] * 500
    value["logicalRecords"] *= 5
    original = copy.deepcopy(doc)
    with pytest.raises(CompileError, match="grounding_budget_exceeded"):
        compile_value(doc, region, value)
    assert doc == original
