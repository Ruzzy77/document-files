"""Joined-array evidence regressions; scripted decisions are not quality approval."""

import json
from copy import deepcopy

import pytest
from test_table_duplicates import _page
from test_table_protocol import execute
from test_table_source_wire import LongMappingModel, long_html

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.compiler import (
    combine_regions,
    compile_region,
    join_continuations,
)

HEADERS = (("Name", "Amount"),)
ROWS = (*HEADERS, ("A", "001.2300"), ("A", "001.2300"), ("B", ""))


def fragments(row_sets, *, uncertain=(), root=False):
    doc, compiled, candidates = ObservationDocument(), [], []
    schema = (
        {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "amount": {"type": "string"}},
                "required": ["name", "amount"],
            },
        }
        if root
        else None
    )
    for i, rows in enumerate(row_sets):
        region, ir = _page(doc, f"p{i}", i + 1, rows, "pcs", key="unit", repeat_key=f"rows{i}")[-1]
        if i in uncertain:
            for role in ir.repeats[0].rowRoles:
                if role.row:
                    role.role = "unresolved"
        if root:
            ir.repeats[0].targetHandle = "t0"
            for j, col in enumerate(ir.repeats[0].columns):
                col.targetHandle = f"t{j + 2}"
        compiled.append(compile_region(ir, doc, region, target_schema=schema))
        if i:
            candidates.append(
                {
                    "id": f"join{i}",
                    "leftRegion": f"p{i - 1}table",
                    "rightRegion": region["id"],
                    "leftTable": f"p{i - 1}t",
                    "rightTable": region["tableRef"],
                    "confirmed": False,
                    "basis": "adjacent_page_candidate",
                    "sourceRefs": [f"p{i - 1}c0:0", f"p{i}c0:0"],
                }
            )
    return doc, compiled, candidates


@pytest.mark.parametrize("root", [False, True])
@pytest.mark.parametrize(
    "row_sets", [(HEADERS, ROWS), (ROWS, HEADERS), (HEADERS, HEADERS, ROWS), (ROWS, HEADERS, ROWS)]
)
def test_zero_record_fragment_does_not_claim_the_joined_array_is_blank(row_sets, root):
    doc, compiled, candidates = fragments(row_sets, root=root)
    original = deepcopy((doc, compiled))
    joined, issues, links = join_continuations(
        compiled, candidates, {c["id"]: "continue" for c in candidates}
    )
    result = combine_regions(joined)
    assert not issues and not result["errors"]
    expected = [{"name": name, "amount": amount} for rows in row_sets for name, amount in rows[1:]]
    assert (result["data"] if root else result["data"]["rows0"]) == expected
    assert all(e["target"]["path"] != ("" if root else "/rows0") for e in result["valueEvidence"])
    assert len(result["valueEvidence"]) == 2 * len(expected)
    assert sum(e["status"] == "blank" for e in result["valueEvidence"]) == sum(
        rows == ROWS for rows in row_sets
    )
    # Equal values remain separate records with their own source cells.
    amounts = [e for e in result["valueEvidence"] if e["raw"] == "001.2300"]
    assert len({e["binding"]["sourceRef"] for e in amounts}) == len(amounts)
    origins = [f for link in links for f in link.get("zeroRecordFragments", [])]
    assert [f["regionId"] for f in origins] == [
        f"p{i}table" for i, rows in enumerate(row_sets) if rows == HEADERS
    ]
    for origin in origins:
        before = next(c for c in compiled if c.id == origin["regionId"])
        assert origin["originalEvidence"] == before.value_evidence[0]
        assert (
            origin["sourceRowStart"] == origin["sourceRowEnd"] == origin["compiledRecordCount"] == 0
        )
    assert (doc, compiled) == original


@pytest.mark.parametrize("decision", ["continue", "duplicate"])
@pytest.mark.parametrize("uncertain", [(), (0,), (1,)])
def test_all_zero_record_fragments_keep_sources_and_uncertainty(decision, uncertain):
    row_sets = tuple(ROWS if i in uncertain else HEADERS for i in (0, 1))
    _, compiled, candidates = fragments(row_sets, uncertain=uncertain)
    joined, issues, links = join_continuations(compiled, candidates, {"join1": decision})
    result = combine_regions(joined)
    assert not issues and not result["errors"]
    assert result["data"] == {"rows0": []}
    assert len(result["valueEvidence"]) == 1
    evidence = result["valueEvidence"][0]
    assert evidence["status"] == ("uncertain" if uncertain else "blank")
    assert evidence["sourceRefs"] == ["p0c0:0", "p1c0:0"]
    assert set(evidence["semanticIds"]) <= {s["id"] for s in result["semantics"]}
    assert len(links[0]["zeroRecordFragments"]) == 2
    for i, origin in enumerate(links[0]["zeroRecordFragments"]):
        assert origin["originalEvidence"] == compiled[i].value_evidence[0]


@pytest.mark.parametrize("decisions", [{}, {"join1": "separate"}, {"join1": "duplicate"}])
def test_unaccepted_join_keeps_original_empty_evidence(decisions):
    _, compiled, candidates = fragments((HEADERS, ROWS))
    joined, _, links = join_continuations(compiled, candidates, decisions)
    assert not links
    assert [c.value_evidence for c in joined] == [c.value_evidence for c in compiled]
    assert [c.data for c in joined] == [c.data for c in compiled]


def test_duplicate_empty_fragments_then_continuation_preserve_both_origins():
    _, compiled, candidates = fragments((HEADERS, HEADERS, ROWS))
    joined, issues, links = join_continuations(
        compiled, candidates, {"join1": "duplicate", "join2": "continue"}
    )
    result = combine_regions(joined)
    assert not issues and not result["errors"]
    assert len(result["data"]["rows0"]) == 3
    assert all(e["target"]["path"] != "/rows0" for e in result["valueEvidence"])
    assert [f["regionId"] for link in links for f in link.get("zeroRecordFragments", [])] == [
        "p0table",
        "p1table",
    ]


def test_unrelated_empty_repeat_in_the_right_region_is_not_relocated():
    _, compiled, candidates = fragments((ROWS, HEADERS, HEADERS))
    left, right, other = compiled
    # A distinct repeat happens to use the left array's key. The selected right
    # repeat still has its own key; joining it must not erase the unrelated one.
    right.data["rows0"] = []
    right.schema["properties"]["rows0"] = deepcopy(other.schema["properties"]["rows2"])
    right.schema["required"].append("rows0")
    right.repeat_paths["other"] = deepcopy(other.repeat_paths["rows"])
    right.repeat_paths["other"].update(path="/rows0", schemaPath="/properties/rows0")
    evidence = deepcopy(other.value_evidence[0])
    evidence["target"]["path"] = "/rows0"
    right.value_evidence.append(evidence)
    joined, issues, links = join_continuations([left, right], candidates[:1], {"join1": "continue"})
    assert not issues
    assert joined[1].data == {"rows0": []}
    assert evidence in joined[1].value_evidence
    assert [f["tableRef"] for f in links[0]["zeroRecordFragments"]] == ["p1t"]
    assert "cross_region_data_conflict" in combine_regions(joined)["errors"]


def test_engine_rebuilds_fragment_provenance_on_resume_without_reusing_forged_projection():
    class UnresolvedFirstView(LongMappingModel):
        first_region = None

        def infer(self, request):
            response = super().infer(request)
            payload = json.loads(request.messages[-1]["content"])
            if payload.get("tableStage") == "structure":
                if self.first_region is None:
                    self.first_region = payload["regionId"]
                if payload["regionId"] == self.first_region:
                    value = json.loads(response.text)
                    for row in value["record"]["rowRoles"]:
                        if row["role"] != "header":
                            row["role"] = "unresolved"
                    return InferenceResponse(json.dumps(value), response.usage)
            return response

    model, states = UnresolvedFirstView(), []
    result = execute(
        model, content=long_html(), contextChars=11000, maxModelCalls=60, states=states
    )
    assert result["extraction"]["status"] == "partial"  # Unresolved source rows stay unresolved.
    assert result["data"]["records"]
    assert not any(e["target"]["path"] == "/records" for e in result["valueEvidence"])
    links = result["document"]["semanticRelations"]
    origin = next(f for link in links for f in link.get("zeroRecordFragments", []))
    assert origin["regionId"] == model.first_region
    assert origin["originalEvidence"]["status"] == "uncertain"
    checkpoint, calls = deepcopy(states[-1]), len(model.requests)
    stored = next(
        f
        for link in checkpoint["result"]["document"]["semanticRelations"]
        for f in link.get("zeroRecordFragments", [])
    )
    stored["originalEvidence"]["status"] = "blank"
    restored = execute(
        model, content=long_html(), contextChars=11000, maxModelCalls=60, restore=checkpoint
    )
    assert restored["data"] == result["data"] and len(model.requests) == calls
    assert restored["document"]["semanticRelations"] == links
    assert restored["valueEvidence"] == result["valueEvidence"]
