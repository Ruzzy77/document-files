"""Record eligibility preserves physical tables and independently owned notes."""

import copy

import pytest

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.regions import (
    continuation_candidates,
    record_continuation_candidates,
)


def fixture():
    nodes, tables, regions = {}, {}, []
    for i in range(4):
        ref, table = f"n{i}", f"slice-{i}"
        nodes[ref] = {"text": "same exact value" if i != 1 else "a note"}
        tables[table] = {
            "id": table,
            "sourceTableRef": "original",
            "colCount": 1,
            "cells": [{"sourceRef": ref, "row": i, "col": 0}],
        }
        regions.append({"id": f"r{i}", "tableRef": table, "nodeIds": [ref]})
    doc = ObservationDocument(nodes=nodes, tables=tables)
    candidates = continuation_candidates(doc, regions)
    states = {
        "r1": {
            "kind": "scalar_form",
            "structure": {
                "status": "complete",
                "routing": "layout-nonrecord.v1",
            },
        }
    }
    return doc, regions, candidates, states


def test_note_slice_is_not_a_record_and_does_not_sever_two_actual_record_slices():
    args = fixture()
    before = copy.deepcopy(args)
    result = record_continuation_candidates(*args)
    assert [(c["leftRegion"], c["rightRegion"]) for c in result] == [("r0", "r2"), ("r2", "r3")]
    bridge, unchanged = result
    assert bridge["confirmed"] and bridge["basis"] == "same_native_table"
    assert bridge["interveningNonrecordRegions"] == ["r1"] and "n1" in bridge["sourceRefs"]
    assert unchanged == args[2][-1]  # unrelated decision IDs do not move
    assert record_continuation_candidates(*args) == result
    assert args == before  # physical source, note ownership and proposal history survive


@pytest.mark.parametrize("change", ["missing", "pending", "ordinary_scalar", "record"])
def test_missing_records_and_unresolved_or_other_scalar_paths_are_not_skipped(change):
    args = fixture()
    state = args[-1]["r1"]
    if change == "missing":
        args[-1].clear()
    elif change == "pending":
        state["structure"]["status"] = "pending"
    elif change == "ordinary_scalar":
        state["structure"].pop("routing")
    else:
        state["kind"] = "record_table"
    assert record_continuation_candidates(*args) == args[2]


def test_nonrecord_slice_from_an_unrelated_table_cannot_prove_a_bridge():
    doc, regions, candidates, states = fixture()
    doc.tables["slice-1"]["sourceTableRef"] = "unrelated"
    candidates = continuation_candidates(doc, regions)
    result = record_continuation_candidates(doc, regions, candidates, states)
    assert [(c["leftRegion"], c["rightRegion"]) for c in result] == [("r2", "r3")]


def test_trailing_note_removes_only_its_record_join_not_its_source():
    doc, regions, candidates, states = fixture()
    states = {"r3": states["r1"]}
    assert record_continuation_candidates(doc, regions, candidates, states) == candidates[:2]
    assert regions[-1]["nodeIds"] == ["n3"]
