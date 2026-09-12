"""Exact native candidate display and bounded corrective feedback, not AI quality."""

import copy
import json

import pytest
from test_document_outline import decision, unit
from test_document_protocol import StagedModel, execute, raw_document

from document_files.interpretation import document_protocol as protocol
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.bindings import resolve
from document_files.interpretation.regions import region_payload
from document_files.interpretation.semantic_types import region_output_schema
from document_files.result_types import SourceBinding


def content(doc, region):
    roles = {
        "regionId": region["id"],
        "documentElements": [decision(role="paragraph", level=None).model_dump()],
    }
    return protocol.content_request(
        region_payload(doc, region),
        region_output_schema(doc, region, compact=False),
        roles,
        doc,
        region,
    )[0]


@pytest.mark.parametrize(
    "text",
    ["Count: requested 5", "Count: 0007", "Count: 1.2300", "Count:", "Count: 🙂가", "Count: A\t B"],
)
def test_candidates_show_exact_selected_text_without_changing_observations(text):
    doc, region = unit(text)
    before = copy.deepcopy(doc.bindings), copy.deepcopy(doc.nodes)
    original_payload = region_payload(doc, region)
    shown = content(doc, region)
    assert set(shown["bindings"]) == set(original_payload["bindings"])
    for bid, candidate in shown["bindings"].items():
        original = original_payload["bindings"][bid]
        binding = SourceBinding(
            **{k: original.get(k) for k in ("sourceRef", "path", "start", "end")}
        )
        _, exact = resolve(binding, doc.nodes)
        assert candidate == {**original, "exactText": exact}
    assert (doc.bindings, doc.nodes) == before
    assert all("exactText" not in b for b in original_payload["bindings"].values())


def test_empty_and_compound_values_are_not_normalized_to_numbers():
    doc, region = unit("Count: requested 5; received:")
    values = [
        b for b in content(doc, region)["bindings"].values() if b.get("candidateRole") == "value"
    ]
    assert [b["exactText"] for b in values] == ["requested 5", ""]
    assert [b.get("blank") for b in values] == [False, True]


@pytest.mark.parametrize("outside", [False, True])
def test_candidate_text_never_reads_outside_an_owned_window(outside):
    doc, region = unit("hidden A🙂B hidden")
    doc.bindings = {
        "b": {
            "sourceRef": "n1",
            "path": "/text",
            "start": 0 if outside else 7,
            "end": 10,
            "candidateRole": "value",
        }
    }
    region.update(
        bindingIds=["b"], requiredBindingIds=["b"], nodeViews={"n1": {"start": 7, "end": 10}}
    )
    if outside:
        with pytest.raises(ValueError, match="native_binding_outside_owned_view"):
            content(doc, region)
    else:
        shown = content(doc, region)
        assert shown["bindings"]["b"]["exactText"] == "A🙂B"
        assert "hidden" not in shown["nodes"]["n1"]["text"]


class WrongCandidate(StagedModel):
    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("documentStage") == "roles":
            return super().infer(request)
        self.calls += 1
        self.content_requests.append(payload)
        bid = next(b for b, c in payload["bindings"].items() if c.get("candidateRole") == "value")
        self.bad_id = bid
        choice = {"kind": "binding", "bindingId": bid, "status": "present"}
        excluded = []
        if payload.get("repairFeedback"):
            choice = {"kind": "quote", "quote": {"sourceRef": "n1", "text": "5"}}
            excluded = [
                {
                    "bindingId": bid,
                    "role": "structural",
                    "explanation": "Compound candidate represented by the exact inner quote.",
                }
            ]
        value = {
            "regionId": payload["regionId"],
            "fields": [
                {
                    "id": "count",
                    "key": "count",
                    "label": "Count",
                    "definitionRefs": ["n1"],
                    "valueType": "integer",
                    "valueSource": choice,
                }
            ],
            "excludedBindings": excluded,
        }
        return InferenceResponse(json.dumps(value), {})


def test_type_repair_identifies_the_offered_choice_without_raw_source_in_diagnostic():
    model = WrongCandidate()
    result = execute(model, raw=raw_document(("Count: requested 5",)))
    assert result["data"] == {"count": 5} and result["extraction"]["status"] == "complete"
    assert model.calls == 3
    feedback = model.content_requests[1]["repairFeedback"]
    assert feedback[0] == "binding_cannot_represent_requested_type"
    assert json.loads(feedback[1].removeprefix("invalid_value_selection:")) == {
        "bindingId": model.bad_id,
        "requestedType": "integer",
    }
    assert "requested 5" not in json.dumps(feedback)
    assert all("exactText" not in b for b in result["document"]["bindings"].values())


def test_exact_text_display_is_counted_in_the_existing_request_limit():
    first = StagedModel()
    raw = raw_document(("Ordinary source text.",))
    execute(first, raw=raw)
    payload = first.content_requests[0]
    chars = len(protocol.CONTENT_SYSTEM) + len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )
    limited = StagedModel()
    limited.input_budget_chars = chars - 1
    result = execute(limited, raw=raw, contextChars=16000)
    assert limited.calls == 1 and result["extraction"]["status"] == "partial"
    assert any(i["code"] == "region_context_budget_exceeded" for i in result["issues"])
    assert result["document"]["nodes"]["n1"]["text"] == "Ordinary source text."


def test_invalid_candidate_view_keeps_roles_and_stops_before_content_call(monkeypatch):
    def invalid(*args, **kwargs):
        raise ValueError("private source text must not appear in diagnostics")

    monkeypatch.setattr(protocol, "content_request", invalid)
    model = StagedModel()
    result = execute(model)
    assert model.calls == 1 and result["extraction"]["status"] == "partial"
    assert result["document"]["outline"]["elements"]
    assert "native_candidate_view_invalid" in json.dumps(result["issues"])
    assert "private source" not in json.dumps(result["issues"])
