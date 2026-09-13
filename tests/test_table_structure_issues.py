"""Complete safe structural feedback and finite repair, not model quality."""

import copy
import json

import pytest
from test_table_protocol import TEXT_RECORDS, ContentCitationModel, execute
from test_table_structure_feedback import native_cell

from document_files.interpretation.compiler import CompileError
from document_files.interpretation.table_structure_feedback import (
    check_structure,
    structure_feedback,
)


def feedback_for(issues, doc, region):
    with pytest.raises(CompileError) as caught:
        check_structure(issues, doc, region)
    return structure_feedback(caught.value, doc, region)


def test_column_findings_union_distinct_sources_without_values_names_or_mutation():
    doc, region, first, _, _ = native_cell()
    other = next(c["sourceRef"] for c in doc.tables[region["tableRef"]]["cells"] if c["col"] == 1)
    issues = [
        {
            "code": "column_definition_conflicts_with_content",
            "column": 1,
            "sourceRefs": [first],
            "columnId": "private-model-name",
            "text": "private value",
        },
        {
            "code": "column_definition_conflicts_with_content",
            "column": 1,
            "sourceRefs": [other, first],
            "tableRef": region["tableRef"],
        },
        {"code": "column_leaf_header_missing", "column": 1, "sourceRef": first},
    ]
    before = copy.deepcopy((doc, region, issues))
    feedback = feedback_for(issues, doc, region)
    assert json.loads(feedback[0].split(":", 1)[1]) == {"1": sorted([first, other])}
    assert feedback == feedback_for(list(reversed(issues)) + issues, doc, region)
    assert "private" not in json.dumps(feedback).lower()
    assert (doc, region, issues) == before


def test_row_and_header_locations_preserve_expanded_rows_and_discard_foreign_details():
    doc, region, ref, _, _ = native_cell()
    issues = [
        {"code": "repeat_row_roles_incomplete", "rows": [1, 0, 1, True, 999]},
        {"code": "table_rows_outside_repeat", "rows": [1], "tableRef": region["tableRef"]},
        *[
            {"code": "header_cell_bound_as_value", "sourceRef": ref, "row": row, "column": 0}
            for row in [0, 1]
        ],
        {"code": "header_cell_bound_as_value", "sourceRef": "private-foreign-value", "column": 0},
        {"code": "column_definition_not_above_column", "sourceRef": ref, "column": 100},
        {
            "code": "column_leaf_header_missing",
            "sourceRef": ref,
            "column": 1,
            "tableRef": "foreign table",
        },
    ]
    feedback = feedback_for(issues, doc, region)
    details = {f.split(":", 1)[0]: json.loads(f.split(":", 1)[1]) for f in feedback if ":" in f}
    assert details["repeat_row_roles_incomplete"] == {"rows": [0, 1]}
    assert details["table_rows_outside_repeat"] == {"rows": [1]}
    assert details["header_cell_bound_as_value"] == {
        "cells": [
            {"sourceRefs": [ref], "row": 0, "column": 0},
            {"sourceRefs": [ref], "row": 1, "column": 0},
        ]
    }
    # Invalid detail cannot remove the original blocking code.
    assert "column_definition_not_above_column" in feedback
    assert "column_leaf_header_missing" in feedback
    assert "private" not in json.dumps(feedback) and "999" not in json.dumps(feedback)


def test_diagnostics_do_not_silently_cap_distinct_source_findings():
    doc, region, _, _, _ = native_cell()
    refs = [f"extra-{n}" for n in range(75)]
    for i, ref in enumerate(refs):
        doc.nodes[ref] = {"text": "do not copy"}
        region["nodeIds"].append(ref)
        doc.tables[region["tableRef"]]["cells"].append({"sourceRef": ref, "row": i + 2, "col": 0})
    issues = [
        {"code": "column_definition_conflicts_with_content", "column": 0, "sourceRefs": [r]}
        for r in refs
    ]
    detail = json.loads(feedback_for(issues, doc, region)[0].split(":", 1)[1])
    assert detail == {"0": sorted(refs)}
    assert check_structure([{"code": "decimal_format_unresolved"}], doc, region) is None


class AlwaysContentModel(ContentCitationModel):
    def infer(self, request):
        # Keep choosing content as definitions, including on the second call.
        previous = self.requests
        self.requests = []
        response = super().infer(request)
        previous.extend(self.requests)
        self.requests = previous
        return response


def test_actual_column_feedback_reaches_repair_and_survives_cancel_resume_without_extra_attempts():
    model = AlwaysContentModel(only_content=True)
    states = []
    stopped = execute(
        model, content=TEXT_RECORDS, states=states, cancelled=lambda: len(model.requests) == 1
    )
    assert stopped["data"] is None and len(model.requests) == 1
    stored = states[-1]["tableStages"]["semantic-region:1"]["structure"]["feedback"]
    assert stored[0].startswith("column_definition_conflicts_with_content:{")
    assert any(i.get("errors") == stored for i in stopped["issues"])
    resumed = execute(model, content=TEXT_RECORDS, restore=states[-1], states=states)
    assert resumed["extraction"]["status"] == "partial" and resumed["data"] is None
    assert len(model.requests) == 2
    payload = json.loads(model.requests[1].messages[-1]["content"])
    assert payload["repairFeedback"] == stored
    refs = json.loads(stored[0].split(":", 1)[1])
    assert set(refs) == {"0", "1"} and all(
        r in payload["nodes"] for rs in refs.values() for r in rs
    )
    execute(model, content=TEXT_RECORDS, restore=states[-1])
    assert len(model.requests) == 2


def test_oversized_repair_keeps_complete_feedback_without_dispatch_or_attempt_consumption():
    class Limited(ContentCitationModel):
        def infer(self, request):
            response = super().infer(request)
            self.input_budget_chars = 1
            return response

    model, states = Limited(only_content=True), []
    result = execute(model, content=TEXT_RECORDS, states=states, contextChars=16000)
    state = states[-1]["tableStages"]["semantic-region:1"]["structure"]
    assert result["data"] is None and len(model.requests) == 1
    assert state["attempts"] == state["usage"]["modelCalls"] == 1
    assert state["inputPreflight"]["phase"] == "repair"
    assert state["inputPreflight"]["withinBudget"] is False
    assert state["inputPreflight"]["feedbackCharacters"] == len(
        json.dumps(state["feedback"], ensure_ascii=False, separators=(",", ":"))
    )
    assert set(json.loads(state["feedback"][0].split(":", 1)[1])) == {"0", "1"}
    execute(model, content=TEXT_RECORDS, restore=states[-1], contextChars=16000)
    assert len(model.requests) == 1
