"""Model-confirmed non-record ownership; routing tests, not semantic qualification."""

import copy
import io
import json

import pytest
from test_table_layout import fixture, response

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation import table_layout
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.table_source_wire import expand_table_sources

NOTES = (
    b"<table><tr><td>Blank debit is not zero.</td></tr>"
    b"<tr><td>Release only after clearance.</td></tr></table>"
)


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (["note", "note"], True),
        (["subtotal", "blank"], True),
        (["note", "data"], False),
        (["note", "header"], False),
        (["note", "unresolved"], False),
        (["blank", "blank"], False),
        (["header", "header"], False),
    ],
)
def test_only_owned_confirmed_nonrecord_rows_use_scalar_route(roles, expected):
    doc, region, _ = fixture(NOTES)
    for cell in doc.tables[region["tableRef"]]["cells"]:
        if roles[cell["row"]] == "blank":
            doc.nodes[cell["sourceRef"]]["text"] = ""
    accepted = table_layout.accept(response(doc, region, roles), doc, region)
    before = copy.deepcopy((doc, region, accepted))
    assert table_layout.nonrecord_only(accepted, doc, region) is expected
    assert (doc, region, accepted) == before


def test_context_headers_do_not_change_owned_note_route_but_spanning_rows_do():
    doc, region, _ = fixture(NOTES)
    table = doc.tables[region["tableRef"]]
    for cell in table["cells"]:
        cell["row"] += 2
    table["headerCells"] = [{"sourceRef": "context", "row": 0, "col": 0, "isHeader": True}]
    accepted = table_layout.accept(response(doc, region, ["note", "note"]), doc, region)
    assert table_layout.nonrecord_only(accepted, doc, region)
    # A cell crossing an unclassified row must not disappear into the scalar route.
    table["cells"][-1]["rowSpan"] = 2
    accepted = table_layout.accept(
        response(doc, region, ["note", "note", "unresolved"]), doc, region
    )
    assert not table_layout.nonrecord_only(accepted, doc, region)


class NotesModel:
    identity = {"adapter": "notes-routing-test", "model": "scripted-not-qualified"}

    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail

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
        else:
            assert payload["tableKind"] == "scalar_form" and "tableStage" not in payload
            assert request.output_schema["properties"]["repeats"]["maxItems"] == 0
            if self.fail:
                raise ModelError("ai_cancelled")
            value = {
                "regionId": payload["regionId"],
                "fields": [
                    {
                        "id": ref,
                        "key": ref,
                        "label": "Source note",
                        "definitionRefs": [ref],
                        "bindingId": next(
                            b
                            for b, v in payload["bindings"].items()
                            if v["sourceRef"] == ref and v["path"] == "/text"
                        ),
                    }
                    for ref in payload["nodeIds"]
                ],
            }
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 10, "completion_tokens": 20})


def execute(model, states, restore=None, cancelled=None):
    return extract_schema_from_stream(
        AnalysisJob(job_id="notes-route", input=AnalysisInput.from_bytes(NOTES, format_id="html")),
        io.BytesIO(NOTES),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=2),
        checkpoint=states.append,
        restore=restore,
        cancelled=cancelled,
    )


def test_note_layout_skips_mapping_without_fabricating_record_or_dropping_values():
    model, states = NotesModel(), []
    result = execute(model, states)
    assert len(model.requests) == 2
    assert set(result["data"].values()) == {
        "Blank debit is not zero.",
        "Release only after clearance.",
    }
    stage = next(iter(states[-1]["tableStages"].values()))
    assert stage["kind"] == "scalar_form"
    assert stage["layout"]["history"][-1]["response"]["tableKind"] == "record_table"
    assert stage["structure"]["routing"] == "layout-nonrecord.v1"
    assert stage["structure"]["usage"]["modelCalls"] == stage["meaning"]["usage"]["modelCalls"] == 0
    assert all(not value["repeats"] for value in states[-1]["accepted"].values())
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert execute(model, [], states[-1])["data"] == result["data"]
    assert len(model.requests) == 2


@pytest.mark.parametrize("mutation", ["route", "kind", "layout_hash", "roles"])
def test_forged_route_is_rejected_before_new_model_call(mutation):
    model, states = NotesModel(), []
    execute(model, states)
    saved = copy.deepcopy(states[-1])
    stage = next(iter(saved["tableStages"].values()))
    if mutation == "route":
        stage["structure"]["routing"] = "future"
    elif mutation == "kind":
        stage["kind"] = "record_table"
    elif mutation == "layout_hash":
        stage["structure"]["layoutSHA256"] = "0" * 64
    else:
        stage["layout"]["history"][-1]["response"]["rowRoles"][0] = "data"
    with pytest.raises(ValueError, match="checkpoint is incompatible"):
        execute(model, [], saved)
    assert len(model.requests) == 2


def test_pause_after_layout_keeps_ownership_and_resumes_only_scalar_interpretation():
    model, states = NotesModel(), []

    def stop():
        return any(
            next(iter(s.get("tableStages", {}).values()), {}).get("structure", {}).get("routing")
            == "layout-nonrecord.v1"
            for s in states
        )

    partial = execute(model, states, cancelled=stop)
    assert len(model.requests) == 1 and partial["data"] is None
    before = copy.deepcopy(states[-1]["regions"])
    done = execute(model, [], states[-1])
    assert done["extraction"]["status"] == "complete", done["issues"]
    assert len(model.requests) == 2 and states[-1]["regions"] == before
