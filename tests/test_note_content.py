"""Literal note content and deferred business scope, not independent AI quality."""

import copy
import io
import json

import pytest
from openpyxl import Workbook
from test_native_merged_geometry import observe

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import note_content, table_layout
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import CompileError, compile_region
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.table_source_wire import expand_table_sources
from document_files.interpretation.table_sources import source_inventory

TEXTS = ("Blank demand is not zero.", "For held orders only: approval is required.")


def source():
    book = Workbook()
    sheet = book.active
    for text in TEXTS:
        sheet.append([text])
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()


def fixture():
    doc = observe(source(), "xlsx")
    region = prepare_regions(doc, context_chars=16000)[0]
    layout = table_layout.accept(
        {
            "regionId": region["id"],
            "tableKind": "record_table",
            "rowRoles": ["note", "note"],
            "baseRevision": None,
        },
        doc,
        region,
    )
    inventory = source_inventory(doc, region)
    return doc, region, layout, inventory


def response(inventory, rid):
    return {
        "regionId": rid,
        "sourceDecisions": {
            s["sourceRef"]: {
                "meanings": [
                    {
                        "kind": "condition",
                        "quotes": [{"text": s["text"]}],
                        "status": "interpreted",
                    }
                ],
                "remainder": "no_additional_meaning",
                "explanation": "Whole note quoted.",
            }
            for s in inventory["sources"]
        },
    }


def test_notes_keep_full_native_text_and_source_without_inventing_scalar_values_or_scope():
    doc, region, layout, inventory = fixture()
    before = copy.deepcopy((doc, region, layout))
    value = response(inventory, region["id"])
    ir, reviewed = note_content.accept(value, layout, doc, region)
    out = compile_region(ir, doc, region)
    assert not out.has_data and not ir.fields and not ir.repeats
    assert [m.description for m in ir.meanings] == list(TEXTS)
    assert [d["sourceText"] for d in out.semantic_details] == [[t] for t in TEXTS]
    assert all(not d["scope"] for d in out.semantic_details)
    assert all(m.sourceRanges[0].path == "/semantic/value/value" for m in ir.meanings)
    assert {i["code"] for i in out.issues} == {"semantic_scope_unresolved"}
    assert not reviewed["unreviewed"] and not reviewed["unresolved"]
    assert (doc, region, layout) == before and ir.noteContentState.response == value
    payload, contract = note_content.request(region_payload(doc, region), doc, region)
    assert "bindings" not in payload and "sameTableMapping" not in payload
    assert {s["text"] for s in payload["meaningSources"]} == set(TEXTS)
    assert "fields" not in contract["properties"]


@pytest.mark.parametrize("mutation", ["self_scope", "value", "partial_sources", "wrong_quote"])
def test_output_cannot_invent_fields_apply_itself_omit_sources_or_quote_context(mutation):
    doc, region, layout, inventory = fixture()
    value = response(inventory, region["id"])
    entry = next(iter(value["sourceDecisions"].values()))
    if mutation == "self_scope":
        entry["meanings"][0]["fieldIds"] = ["own-note"]
    elif mutation == "value":
        value["fields"] = []
    elif mutation == "partial_sources":
        value["sourceDecisions"].pop(next(iter(value["sourceDecisions"])))
    else:
        entry["meanings"][0]["quotes"][0]["text"] = "invented conclusion"
    with pytest.raises(CompileError):
        note_content.accept(value, layout, doc, region)


@pytest.mark.parametrize("mutation", ["scope", "binding", "inventory", "layout", "source"])
def test_compiler_replays_complete_note_provenance_and_rejects_forged_metadata(mutation):
    doc, region, layout, inventory = fixture()
    ir, _ = note_content.accept(response(inventory, region["id"]), layout, doc, region)
    if mutation == "scope":
        ir.meanings[0].fieldIds = ["fake"]
    elif mutation == "binding":
        ir.excludedBindings.pop()
    elif mutation == "inventory":
        ir.noteContentState.inventorySHA256 = "0" * 64
    elif mutation == "layout":
        ir.noteContentState.layout["response"]["rowRoles"][0] = "subtotal"
    else:
        doc.nodes[region["nodeIds"][0]]["semantic"]["value"]["value"] += " changed"
    with pytest.raises(CompileError):
        compile_region(ir, doc, region)


@pytest.mark.parametrize("deferred", ["unresolved", "unreviewed"])
def test_fully_quoted_but_deferred_review_still_exposes_the_missing_work(deferred):
    doc, region, layout, inventory = fixture()
    value = response(inventory, region["id"])
    next(iter(value["sourceDecisions"].values()))["remainder"] = deferred
    ir, _ = note_content.accept(value, layout, doc, region)
    out = compile_region(ir, doc, region)
    assert any(i["code"] == "note_content_source_" + deferred for i in out.issues)
    assert not out.has_data and len(out.semantic_details) == 2


def test_subtotals_forms_and_unresolved_or_blank_only_tables_keep_value_paths():
    doc, region, layout, inventory = fixture()
    for roles in [["subtotal", "note"], ["data", "note"], ["unresolved", "note"]]:
        current = table_layout.accept({**layout["response"], "rowRoles": roles}, doc, region)
        assert not table_layout.notes_only(current, doc, region)
        with pytest.raises(CompileError, match="requires_owned_note_rows"):
            note_content.accept(response(inventory, region["id"]), current, doc, region)


class Model:
    identity = {"adapter": "note-content-scripted", "model": "not-quality-evidence"}

    def __init__(self):
        self.requests = []

    def infer(self, request):
        payload = expand_table_sources(json.loads(request.messages[-1]["content"]))
        self.requests.append(payload)
        if payload.get("tableStage") == "layout":
            value = {
                "regionId": payload["regionId"],
                "tableKind": "record_table",
                "rowRoles": ["note", "note"],
                "baseRevision": None,
            }
        elif payload.get("tableStage") == "note_content":
            value = response({"sources": payload["meaningSources"]}, payload["regionId"])
        else:
            raise ModelError("ai_unavailable")
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 1, "completion_tokens": 1})


def run(model, states=None, restore=None, cancelled=None):
    raw = source()
    # Job bytes are stable within a test; ZIP timestamps otherwise change per save.
    if not hasattr(model, "raw"):
        model.raw = raw
    return extract_schema_from_stream(
        AnalysisJob(job_id="notes", input=AnalysisInput.from_bytes(model.raw, format_id="xlsx")),
        io.BytesIO(model.raw),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=2),
        checkpoint=states.append if states is not None else None,
        restore=restore,
        cancelled=cancelled,
    )


def test_actual_engine_uses_note_content_not_scalar_fields_and_restores_without_reinterpretation():
    model, states = Model(), []
    result = run(model, states)
    assert [r["tableStage"] for r in model.requests] == ["layout", "note_content"]
    assert result["data"] == {} and len(result["semanticDetails"]) == 2
    before = copy.deepcopy(states[-1])
    restored = run(model, restore=before)
    assert restored["semanticDetails"] == result["semanticDetails"] and len(model.requests) == 2
    assert states[-1] == before
    tampered = copy.deepcopy(before)
    next(iter(tampered["accepted"].values()))["noteContentState"]["layout"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        run(model, restore=tampered)
    assert len(model.requests) == 2


def test_repair_may_refine_kind_but_cannot_drop_qualifiers_or_downgrade_accepted_content():
    doc, region, layout, inventory = fixture()
    value = response(inventory, region["id"])
    before, _ = note_content.accept(value, layout, doc, region)
    first = next(iter(value["sourceDecisions"].values()))
    first["meanings"][0]["kind"] = "note"
    after, _ = note_content.accept(value, layout, doc, region)
    assert note_content.preserves(before, after)
    first["meanings"][0]["status"] = "uncertain"
    uncertain, _ = note_content.accept(value, layout, doc, region)
    assert not note_content.preserves(before, uncertain)
    first["meanings"][0]["status"] = "interpreted"
    last = list(value["sourceDecisions"].values())[-1]
    last["meanings"][0]["quotes"][0]["text"] = "approval is required."
    shortened, _ = note_content.accept(value, layout, doc, region)
    assert not note_content.preserves(before, shortened)


def test_cancellation_after_layout_resumes_note_content_only():
    model, states = Model(), []
    run(
        model,
        states,
        cancelled=lambda: any(
            s["tableStages"]
            and next(iter(s["tableStages"].values()))["structure"].get("routing")
            == "layout-nonrecord.v1"
            for s in states
        ),
    )
    assert len(model.requests) == 1
    result = run(model, restore=states[-1])
    assert len(model.requests) == 2 and len(result["semanticDetails"]) == 2


def test_current_note_wire_supports_strict_transport_without_sending_checkpoint_metadata():
    from document_files.interpretation.backends import _strict_wire_schema

    _, region, _, inventory = fixture()
    schema = note_content.schema(inventory, region["id"])
    original = copy.deepcopy(schema)
    wire = _strict_wire_schema(schema)
    assert wire["additionalProperties"] is False
    assert "noteContentState" not in json.dumps(wire)
    assert schema == original


def test_note_child_of_mixed_record_table_uses_same_content_path_and_keeps_record_values():
    from test_table_protocol import HTML, TableModel, execute, layout_fixture, record_response

    content = HTML.replace(
        b"</table>",
        b'<tr><td colspan="2">For held orders only: approval is required.</td></tr></table>',
    )

    class Routed(TableModel):
        def layout_response(self, payload):
            table = next(iter(payload["tables"].values()))
            roles = ["data"] * len(table["rowRoleOrder"])
            roles[-1] = "note"
            return layout_fixture(payload, roles=roles)

        def infer(self, request):
            payload = expand_table_sources(json.loads(request.messages[-1]["content"]))
            if payload.get("tableStage") == "structure":
                self.requests.append(request)
                value = record_response(payload)
                value["record"]["rowRoles"][-1]["role"] = "note"
                return InferenceResponse(
                    json.dumps(value), {"prompt_tokens": 1, "completion_tokens": 1}
                )
            if payload.get("tableStage") == "note_content":
                self.requests.append(request)
                return InferenceResponse(
                    json.dumps(
                        response({"sources": payload["meaningSources"]}, payload["regionId"])
                    ),
                    {"prompt_tokens": 1, "completion_tokens": 1},
                )
            return super().infer(request)

    model, states = Routed(), []
    result = execute(model, content=content, states=states, maxModelCalls=4)
    assert result["data"] == {
        "records": [{"code": "0007", "size": "1.2300"}, {"code": "0008", "size": "0.00"}]
    }
    assert len(result["semanticDetails"]) == 1
    assert result["semanticDetails"][0]["sourceText"] == [TEXTS[1]]
    assert not result["semanticDetails"][0]["scope"]
    child = next(r for r in states[-1]["regions"] if r.get("parentRegionId"))
    note = states[-1]["accepted"][child["id"]]["noteContentState"]
    assert note["layoutRegion"]["id"] == child["parentRegionId"]
    before = len(model.requests)
    done = execute(model, content=content, restore=states[-1], maxModelCalls=4)
    assert len(model.requests) == before and done["data"] == result["data"]
