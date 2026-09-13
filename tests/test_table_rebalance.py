"""Packing mechanics preserve ownership and attempted work, not model quality."""

import json
from copy import deepcopy

import pytest
from test_table_protocol import execute
from test_table_source_wire import LongMappingModel, long_html

from document_files.document_model.observe import observe_document
from document_files.interpretation.backends import ModelError
from document_files.interpretation.regions import prepare_regions, rebalance_table_pair


def pair():
    doc = observe_document(long_html(), "html", {})
    regions = prepare_regions(doc, context_chars=11000)
    metadata = {
        "intent": "discover",
        "targetHandles": {},
        "sameTableMapping": {"label": "column context " * 150},
    }
    return doc, regions, metadata


def test_pair_repacking_preserves_all_sources_bindings_and_context_without_nested_ids():
    doc, regions, metadata = pair()
    first, second = regions[1:3]
    before = deepcopy(doc), deepcopy((first, second))
    planned = rebalance_table_pair(
        doc, first, second, context_chars=11000, request_metadata=metadata
    )
    assert planned
    for key in ("nodeIds", "bindingIds", "requiredBindingIds"):
        assert [i for r in planned for i in r[key]] == [i for r in (first, second) for i in r[key]]
    assert all(r["withinContextBudget"] and r["requestChars"] <= 11000 for r in planned)
    assert doc.nodes == before[0].nodes and doc.bindings == before[0].bindings
    assert (first, second) == before[1]
    root = doc.tables[first["tableRef"]]["sourceTableRef"]
    assert doc.tables[root] == before[0].tables[root]
    assert [c for r in planned for c in doc.tables[r["tableRef"]]["cells"]] == [
        c for r in (first, second) for c in before[0].tables[r["tableRef"]]["cells"]
    ]
    # Once those exact bounds fit, inspecting them again is a no-op, not a loop
    # of longer region IDs, changed source ownership or extra observation views.
    snapshot = deepcopy(doc)
    assert (
        rebalance_table_pair(doc, *planned[:2], context_chars=11000, request_metadata=metadata)
        is None
    )
    assert doc == snapshot


@pytest.mark.parametrize(
    "change",
    [
        "other_table",
        "gap",
        "owned_overlap",
        "view",
        "changed_cell",
        "span_overlap",
        "invalid_bounds",
    ],
)
def test_unsafe_pairs_remain_unchanged(change):
    doc, regions, metadata = pair()
    first, second = regions[1:3]
    right = doc.tables[second["tableRef"]]
    if change == "other_table":
        right["sourceTableRef"] = "another physical table"
    elif change == "gap":
        right["viewRowStart"] += 1
    elif change == "owned_overlap":
        second["nodeIds"].append(first["nodeIds"][0])
    elif change == "view":
        first["nodeViews"] = {first["nodeIds"][0]: {"start": 0, "end": 1}}
    elif change == "changed_cell":
        right["cells"] = deepcopy(right["cells"])
        right["cells"][0]["rowSpan"] = 9
    elif change == "span_overlap":
        right["cells"] = [doc.tables[first["tableRef"]]["cells"][-1], *right["cells"]]
    else:
        right["viewRowEnd"] = None
    snapshot = deepcopy(doc), deepcopy(regions)
    assert (
        rebalance_table_pair(doc, first, second, context_chars=11000, request_metadata=metadata)
        is None
    )
    assert (doc, regions) == snapshot


def test_attempted_pending_structure_keeps_its_identity_and_completed_rows_on_resume():
    class PausingModel(LongMappingModel):
        paused = False

        def infer(self, request):
            payload = json.loads(request.messages[-1]["content"])
            if len(self.requests) == 2 and not self.paused:
                self.paused = True
                self.pending_id = payload["regionId"]
                self.requests.append(request)
                raise ModelError("ai_cancelled")
            return super().infer(request)

    model, states = PausingModel(), []
    execute(model, content=long_html(), contextChars=11000, maxModelCalls=60, states=states)
    checkpoint = deepcopy(states[-1])
    assert checkpoint["tableStages"][model.pending_id]["structure"]["attempts"] == 1
    old_id = next(iter(checkpoint["accepted"]))
    used = checkpoint["usage"]["modelCalls"]
    result = execute(
        model, content=long_html(), contextChars=11000, maxModelCalls=60, restore=checkpoint
    )
    assert result["data"]["records"] == [{"code": f"{i:04}", "size": "1.2300"} for i in range(50)]
    assert json.loads(model.requests[used].messages[-1]["content"])["regionId"] == model.pending_id
    assert all(
        json.loads(r.messages[-1]["content"])["regionId"] != old_id for r in model.requests[used:]
    )
