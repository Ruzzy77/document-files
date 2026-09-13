"""Routed table-content discovery is not an automatic applicability decision."""

from copy import deepcopy

import pytest
from test_cross_region_integration import fixture

from document_files.interpretation.integration import build_scope_tasks


def routed(*, reverse=False):
    obs, original, compiled = fixture()
    owner, target, far = deepcopy(original)
    obs.tables["physical"] = {"id": "physical", "cells": []}
    parent, child = (owner, target) if reverse else (target, owner)
    parent["tableRef"] = "physical"
    child.update(tableContextRef="physical", parentRegionId=parent["id"])
    regions = [target, *[{"id": f"pending-{i}", "nodeIds": []} for i in range(40)], owner, far]
    return obs, regions, compiled, parent, child


@pytest.mark.parametrize("reverse", [False, True])
def test_routed_parent_and_child_remain_scope_candidates_across_unprocessed_regions(reverse):
    obs, regions, compiled, parent, child = routed(reverse=reverse)
    before = deepcopy((obs, regions, compiled))
    tasks = build_scope_tasks(obs, regions, compiled)
    assert tasks
    for task in tasks:
        candidates = [c for c in task.payload["candidates"] if c["label"] in {"Cost", "Length"}]
        assert {c["label"] for c in candidates} == {"Cost", "Length"}
        for candidate in candidates:
            assert candidate["candidateBasis"] == "tableValueRouting"
            assert candidate["tableRelationship"] == {
                "parentRegionId": parent["id"],
                "childRegionId": child["id"],
                "tableRef": "physical",
                "basis": "program_value_routing",
            }
    assert (obs, regions, compiled) == before  # Discovery applies nothing and clears no issues.
    assert any(i["code"] == "semantic_scope_unresolved" for i in compiled[0].issues)


@pytest.mark.parametrize("mutation", ["no_parent", "wrong_parent", "wrong_table", "missing_table"])
def test_same_names_or_table_hints_do_not_establish_a_routing_relationship(mutation):
    obs, regions, compiled, parent, child = routed()
    if mutation == "no_parent":
        child.pop("parentRegionId")
    elif mutation == "wrong_parent":
        child["parentRegionId"] = "somewhere-else"
    elif mutation == "wrong_table":
        child["tableContextRef"] = "different"
        obs.tables["different"] = {"id": "different", "cells": []}
    else:
        obs.tables.clear()
    tasks = build_scope_tasks(obs, regions, compiled)
    assert tasks  # Existing adjacent unrelated candidates may still be offered.
    assert all(c["label"] not in {"Cost", "Length"} for t in tasks for c in t.payload["candidates"])


def test_routing_change_invalidates_scope_identity_without_changing_source_words():
    obs, regions, compiled, parent, child = routed()
    previous = {t.id: t.fingerprint for t in build_scope_tasks(obs, regions, compiled)}
    child.pop("parentRegionId")
    current = {t.id: t.fingerprint for t in build_scope_tasks(obs, regions, compiled)}
    assert previous.keys() == current.keys()
    assert all(previous[key] != current[key] for key in previous)


def test_routed_candidates_still_obey_the_same_discovery_budget():
    obs, regions, compiled, _, _ = routed()
    tasks = build_scope_tasks(obs, regions, compiled, max_candidates=1)
    assert tasks and all(not task.complete_candidates for task in tasks)
    assert all(len(task.payload["candidates"]) == 1 for task in tasks)
    assert all(task.payload["candidateCoverage"] == "bounded" for task in tasks)
