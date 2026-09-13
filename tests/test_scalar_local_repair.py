"""Stage-local repair evidence, including resume; no model quality claim."""

import copy
import io
import json

import pytest
from test_table_nonrecord_routing import NOTES, NotesModel, execute

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.contracts import ExtractionOptions
from document_files.interpretation.engine import extract_schema_from_stream


class RepairModel(NotesModel):
    def __init__(self):
        super().__init__()
        self.content = []

    def infer(self, request):
        response = super().infer(request)
        payload = self.requests[-1]
        if payload.get("tableStage"):
            return response
        value = json.loads(response.text)
        self.content.append(copy.deepcopy(payload))
        if len(self.content) == 1:
            value["fields"] = value["fields"][:1]
        value["meanings"] = [
            {
                "id": "restriction",
                "kind": "condition",
                "description": "Release requires clearance.",
                "sourceRefs": [payload["nodeIds"][-1]],
            }
        ]
        return InferenceResponse(json.dumps(value), response.usage)


@pytest.mark.parametrize("resume", [False, True])
def test_only_local_issues_identify_the_original_binding_in_first_and_resumed_repair(resume):
    model, states = RepairModel(), []
    # execute has a 2-call allowance (layout + first scalar). The saved fragment
    # contains both unresolved applicability and a genuinely unread source value.
    partial = execute(model, states)
    original = copy.deepcopy(states[-1])
    issues = partial["issues"]
    assert any(i["code"] == "semantic_scope_unresolved" for i in issues)
    expected = [
        i["code"] + ":" + i.get("sourceRef", i.get("bindingId", ""))
        for i in issues
        if i["code"] in {"node_semantics_unaccounted", "value_candidate_unaccounted"}
    ]
    assert any(v.startswith("value_candidate_unaccounted:") for v in expected)
    assert len(model.content) == 1
    if not resume:
        model = RepairModel()
    done = extract_schema_from_stream(
        AnalysisJob(job_id="notes-route", input=AnalysisInput.from_bytes(NOTES, format_id="html")),
        io.BytesIO(NOTES),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=2 if resume else 3),
        restore=states[-1] if resume else None,
        additional_budget={"maxModelCalls": 1} if resume else None,
    )
    assert len(model.content) == 2
    first, repaired = copy.deepcopy(model.content)
    assert repaired.pop("repairFeedback") == expected
    first.pop("repairFeedback", None)
    assert repaired == first  # no source, context or output contract was trimmed
    assert states[-1] == original
    assert set(done["data"].values()) == {
        "Blank debit is not zero.",
        "Release only after clearance.",
    }
    assert not any(i["code"] == "value_candidate_unaccounted" for i in done["issues"])
    assert any(i["code"] == "semantic_scope_unresolved" for i in done["issues"])
