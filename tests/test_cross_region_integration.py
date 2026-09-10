"""Applicability compiler contracts; scripted decisions are not AI quality evidence."""

import copy
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.compiler import CompiledRegion, CompileError
from document_files.interpretation.integration import (
    ScopeDecision,
    apply_scope_decision,
    build_scope_tasks,
)
from document_files.interpretation.scope_reference_wire import prepare_scope_wire


def fixture():
    nodes = {
        "note": {"text": "Cost is USD. Length is mm."},
        "price": {"text": "Cost"},
        "length": {"text": "Length"},
        "far": {"text": "Unrelated amount"},
        "row": {"text": "DO_NOT_SEND_ENTIRE_EXISTING_VALUES"},
    }
    regions = [
        {"id": "r1", "nodeIds": ["note"]},
        {"id": "r2", "nodeIds": ["price", "length", "row"]},
        {"id": "r3", "nodeIds": ["far"]},
    ]
    observation = ObservationDocument(nodes=nodes, regions=regions)
    statement = CompiledRegion("r1")
    for identifier, description in [("usd", "Cost uses USD"), ("mm", "Length uses mm")]:
        target = {"space": "document", "path": "/note"}
        statement.semantics.append(
            {
                "id": identifier,
                "kind": "unresolved_unit",
                "description": description,
                "sourceRefs": ["note"],
                "targets": [target],
                "scope": [target],
                "basis": "ai_interpreted",
                "status": "uncertain",
            }
        )
        statement.semantic_details.append(
            {
                "id": identifier,
                "kind": "unit",
                "scope": [],
                "sourceRefs": ["note"],
                "sourceText": [nodes["note"]["text"]],
                "interpretationStatus": "uncertain",
                "executable": False,
            }
        )
        statement.issues.append({"code": "semantic_scope_unresolved", "semanticId": identifier})
    statement.issues.append({"code": "unrelated_issue"})
    targets = []
    for rid, names in [("r2", ["price", "length"]), ("r3", ["far"])]:
        region = CompiledRegion(rid, data={name: "original-value" for name in names}, has_data=True)
        for name in names:
            data = {"space": "data", "path": "/" + name}
            schema = {"space": "dataSchema", "path": "/properties/" + name}
            region.semantics.append(
                {
                    "id": rid + name,
                    "kind": "field_definition",
                    "description": nodes[name]["text"],
                    "sourceRefs": [name],
                    "scope": [data],
                    "targets": [schema],
                    "status": "interpreted",
                    "basis": "ai_interpreted",
                }
            )
            for destination, target in [
                (region.value_evidence, data),
                (region.schema_evidence, schema),
            ]:
                destination.append(
                    {
                        "target": target,
                        "semanticIds": [rid + name],
                        "sourceRefs": [name],
                        "raw": "original-value",
                    }
                )
        targets.append(region)
    return observation, regions, [statement, *targets]


def decision(task, label="Cost"):
    candidate = next(c for c in task.payload["candidates"] if c["label"] == label)
    return {
        "taskId": task.id,
        "decision": "apply",
        "targetHandles": [candidate["targetHandle"]],
        "sourceRefs": ["note", *candidate["definitionRefs"]],
        "explanation": "Explicit cost unit applies to the cost definition.",
    }


def test_adjacent_candidate_is_not_an_automatic_scope_and_never_sends_values():
    obs, regions, compiled = fixture()
    before = copy.deepcopy(compiled)
    tasks = build_scope_tasks(obs, regions, compiled)
    assert len(tasks) == 2 and tasks[0].id != tasks[1].id
    assert all(len(task.payload["candidates"]) == 2 for task in tasks)
    assert "original-value" not in json.dumps(tasks[0].payload)
    assert "DO_NOT_SEND" not in json.dumps(tasks[0].payload)
    assert "properties/" not in json.dumps(tasks[0].payload)
    assert compiled == before
    unresolved = {"taskId": tasks[0].id, "decision": "unresolved", "explanation": "Ambiguous."}
    assert apply_scope_decision(compiled, tasks[0], unresolved) == (compiled, False)


def test_apply_preserves_data_and_other_unit_in_the_same_paragraph():
    obs, regions, compiled = fixture()
    before = copy.deepcopy(compiled)
    task = build_scope_tasks(obs, regions, compiled)[0]
    result, changed = apply_scope_decision(compiled, task, decision(task))
    assert changed and compiled == before
    assert result[1].data == compiled[1].data and result[1].schema == compiled[1].schema
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/price"}]
    assert result[0].semantic_details[0]["executable"] is False
    assert result[0].semantic_details[1] == compiled[0].semantic_details[1]
    assert result[0].semantics[0]["kind"] == "unit"
    assert {i.get("semanticId") for i in result[0].issues} == {"mm", None}
    assert "usd" in result[1].value_evidence[0]["semanticIds"]
    assert "usd" in result[1].schema_evidence[0]["semanticIds"]
    assert "usd" not in result[1].value_evidence[1]["semanticIds"]
    assert apply_scope_decision(result, task, decision(task)) == (result, False)


def test_scope_wire_changes_only_typed_target_references():
    from document_files.interpretation.integration import scope_batch_payload, scope_output_schema

    obs, regions, compiled = grouped_table_fixture()
    compiled[0].semantics[1]["description"] = "Literal @column0 and scope-target-unchanged"
    compiled[0].semantic_details[1]["sourceText"] = ["Literal @headerGroup2"]
    task = build_scope_tasks(obs, regions, compiled)[1]
    before = copy.deepcopy(task)
    wire = prepare_scope_wire([task])
    restored = copy.deepcopy(wire.payload)
    for candidate in restored["candidates"]:
        candidate["targetHandle"] = wire.targets[candidate["targetHandle"]]
        if "coversCandidates" in candidate:
            candidate["coversCandidates"] = [wire.targets[h] for h in candidate["coversCandidates"]]
    assert restored == scope_batch_payload([task]) and task == before
    contract = copy.deepcopy(wire.contract)
    handles = contract["properties"]["targetHandles"]["items"]["enum"]
    contract["properties"]["targetHandles"]["items"]["enum"] = [wire.targets[h] for h in handles]
    assert contract == scope_output_schema([task])
    assert len(json.dumps(wire.payload)) < len(json.dumps(task.payload))
    group = next(c for c in wire.payload["candidates"] if c["candidateKind"] == "headerGroup")
    assert group["targetHandle"].startswith("@headerGroup")
    assert all(h.startswith("@column") for h in group["coversCandidates"])
    choice = decision(task, "Measurements")
    encoded = copy.deepcopy(choice)
    encoded["targetHandles"] = [group["targetHandle"]]
    encoded["explanation"] = choice["explanation"] = "Literal @headerGroup2 and scope-target-text"
    assert wire.decode(encoded) == choice
    actual, changed = apply_scope_decision(compiled, task, wire.decode(encoded))
    expected, _ = apply_scope_decision(compiled, task, choice)
    assert changed and actual == expected


@pytest.mark.parametrize("bad_handle", ["@unknown", "canonical", None, ["@field0"]])
def test_scope_wire_invalid_sibling_does_not_discard_valid_decision(bad_handle):
    from document_files.interpretation.integration import parse_scope_choices

    obs, regions, compiled = fixture()
    tasks = build_scope_tasks(obs, regions, compiled)
    wire = prepare_scope_wire(tasks)
    choices = [decision(t) for t in tasks]
    inverse = {handle: alias for alias, handle in wire.targets.items()}
    for choice in choices:
        choice["targetHandles"] = [inverse[h] for h in choice["targetHandles"]]
    canonical = decision(tasks[1])["targetHandles"][0]
    choices[1]["targetHandles"] = [canonical if bad_handle == "canonical" else bad_handle]
    valid, invalid = parse_scope_choices(wire.decode({"decisions": choices}), tasks)
    assert invalid and [c.taskId for c in valid] == [tasks[0].id]
    assert valid[0].model_dump() == decision(tasks[0])
    # Bad alias decoding must not hide a duplicate task from batch validation.
    choices[1]["taskId"] = choices[0]["taskId"]
    valid, invalid = parse_scope_choices(wire.decode({"decisions": choices}), tasks)
    assert invalid and valid == []


def test_scope_wire_rejects_another_tasks_alias_and_preserves_boundedness():
    from document_files.interpretation.integration import parse_scope_choices

    obs, regions, compiled = fixture()
    tasks = build_scope_tasks(obs, regions, compiled, max_candidates=1)
    full = build_scope_tasks(obs, regions, compiled)[1]
    assert not tasks[0].complete_candidates
    other = next(
        c for c in full.payload["candidates"] if c["targetHandle"] not in tasks[0].target_map
    )
    payload = copy.deepcopy(full.payload)
    payload["candidates"] = [other]
    tasks[1] = replace(
        full,
        payload=payload,
        target_map={other["targetHandle"]: full.target_map[other["targetHandle"]]},
    )
    wire = prepare_scope_wire(tasks)
    assert wire.payload["tasks"][0]["candidateCoverage"] == "bounded"
    other_alias = next(a for a, h in wire.targets.items() if h == other["targetHandle"])
    choice = decision(full, other["label"])
    choice.update(taskId=tasks[0].id, targetHandles=[other_alias])
    valid, invalid = parse_scope_choices(wire.decode({"decisions": [choice]}), tasks)
    assert invalid and not valid


def test_scope_wire_escapes_canonical_alias_collision_without_changing_literals():
    obs, regions, compiled = fixture()
    task = build_scope_tasks(obs, regions, compiled, max_candidates=1)[0]
    payload = copy.deepcopy(task.payload)
    original = payload["candidates"][0]["targetHandle"]
    payload["candidates"][0].update(targetHandle="@field0", label="Literal @field0")
    task = replace(task, payload=payload, target_map={"@field0": task.target_map[original]})
    wire = prepare_scope_wire([task])
    assert wire.targets == {"@@field0": "@field0"}
    assert wire.payload["candidates"][0]["label"] == "Literal @field0"


def test_explicit_note_reference_offers_nonadjacent_definition():
    obs, regions, compiled = fixture()
    obs.relations.append(
        {
            "kind": "noteReference",
            "sourceRef": "far",
            "targetRef": "note",
            "basis": "native_reference",
        }
    )
    task = build_scope_tasks(obs, regions, compiled)[0]
    far = next(c for c in task.payload["candidates"] if c["label"] == "Unrelated amount")
    assert far["candidateBasis"] == "noteReference"
    assert far["referenceLinks"][0]["targetRef"] == "note"
    assert compiled[0].semantic_details[0]["scope"] == []


@pytest.mark.parametrize(
    "mutation", ["handle", "task", "source", "no-definition-evidence", "stale"]
)
def test_invalid_or_stale_decisions_fail_atomically(mutation):
    obs, regions, compiled = fixture()
    task = build_scope_tasks(obs, regions, compiled)[0]
    choice = decision(task)
    if mutation == "handle":
        choice["targetHandles"] = ["/user/written/pointer"]
    elif mutation == "task":
        choice["taskId"] = "other"
    elif mutation == "source":
        choice["sourceRefs"] += ["far"]
    elif mutation == "no-definition-evidence":
        choice["sourceRefs"] = ["note"]
    else:
        compiled[1].semantics[0]["scope"][0]["path"] = "/changed"
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError):
        apply_scope_decision(compiled, task, choice)
    assert compiled == before


def test_bounded_candidates_stay_partial_and_fingerprints_track_new_compilation():
    obs, regions, compiled = fixture()
    task = build_scope_tasks(obs, regions, compiled, max_candidates=1)[0]
    assert not task.complete_candidates
    chosen = task.payload["candidates"][0]
    result, changed = apply_scope_decision(compiled, task, decision(task, chosen["label"]))
    assert changed and result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    assert any(i.get("semanticId") == "usd" for i in result[0].issues)
    assert build_scope_tasks(obs, regions, result)  # Later wider context remains eligible.
    fingerprint = build_scope_tasks(obs, regions, compiled)[0].fingerprint
    assert build_scope_tasks(obs, regions, compiled)[0].fingerprint == fingerprint
    assert build_scope_tasks(obs, regions, compiled[:1]) == []
    compiled[1].semantics[0]["description"] = "New cost definition"
    assert build_scope_tasks(obs, regions, compiled)[0].fingerprint != fingerprint
    obs.nodes["note"]["text"] = "Huge"
    compiled[0].semantic_details[0]["sourceText"] = ["x" * 20000]
    assert all(t.semantic_id != "usd" for t in build_scope_tasks(obs, regions, compiled))


def test_strict_contract_forbids_values_and_pointers():
    with pytest.raises(ValidationError):
        ScopeDecision.model_validate(
            {
                "taskId": "t",
                "decision": "apply",
                "targetHandles": ["h"],
                "sourceRefs": ["s"],
                "explanation": "x",
                "data": {"invented": "value"},
            }
        )
    with pytest.raises(ValidationError):
        ScopeDecision.model_validate(
            {"taskId": "t", "decision": "unresolved", "targetHandles": ["h"], "explanation": "x"}
        )


def test_repeat_or_group_handle_attaches_to_descendant_evidence_not_other_rows():
    obs, regions, compiled = fixture()
    definition = compiled[1].semantics[0]
    definition["scope"] = [{"space": "data", "path": "/items"}]
    definition["targets"] = [{"space": "dataSchema", "path": "/properties/items"}]
    compiled[1].value_evidence[0]["target"]["path"] = "/items/0/cost"
    compiled[1].schema_evidence[0]["target"]["path"] = "/properties/items/items/properties/cost"
    task = build_scope_tasks(obs, regions, compiled)[0]
    result, changed = apply_scope_decision(compiled, task, decision(task))
    assert changed
    assert "usd" in result[1].value_evidence[0]["semanticIds"]
    assert "usd" in result[1].schema_evidence[0]["semanticIds"]
    assert "usd" not in result[1].value_evidence[1]["semanticIds"]
    assert result[1].data == compiled[1].data


@pytest.mark.parametrize("omission", ["text-tail", "reference-budget", "missing-node"])
def test_truncated_or_omitted_definition_context_cannot_resolve_statement(omission):
    obs, regions, compiled = fixture()
    if omission == "text-tail":
        obs.nodes["price"]["text"] = "Cost " + "x" * 500 + " only if condition applies"
    elif omission == "reference-budget":
        for n in range(40):
            ref = f"context-{n}"
            obs.nodes[ref] = {"text": "Additional definition context" + "x" * 460}
            compiled[1].semantics[0]["sourceRefs"].append(ref)
    else:
        compiled[1].semantics[0]["sourceRefs"].append("missing-definition")
    task = build_scope_tasks(obs, regions, compiled)[0]
    assert task.payload["candidateCoverage"] == "bounded" and not task.complete_candidates
    label = "Length" if omission == "reference-budget" else "Cost"
    result, changed = apply_scope_decision(compiled, task, decision(task, label))
    assert changed and result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    assert any(
        i.get("code") == "semantic_scope_unresolved" and i.get("semanticId") == "usd"
        for i in result[0].issues
    )


def test_short_definition_context_is_not_dropped_after_eight_references():
    obs, regions, compiled = fixture()
    for n in range(10):
        ref = f"context-{n}"
        obs.nodes[ref] = {"text": f"Additional definition {n}"}
        compiled[1].semantics[0]["sourceRefs"].append(ref)
    task = build_scope_tasks(obs, regions, compiled)[0]
    candidate = next(c for c in task.payload["candidates"] if c["label"] == "Cost")
    assert task.complete_candidates and candidate["contextComplete"]
    assert [c["sourceRef"] for c in candidate["context"]] == candidate["definitionRefs"]
    assert len(candidate["context"]) == 11
    changed, applied = apply_scope_decision(compiled, task, decision(task))
    assert applied and not any(i.get("semanticId") == "usd" for i in changed[0].issues)
    obs.nodes["context-9"]["text"] = "Changed definition"
    assert build_scope_tasks(obs, regions, compiled)[0].fingerprint != task.fingerprint


def test_same_region_candidates_are_offered_without_automatic_assignment():
    obs, regions, compiled = fixture()
    local = compiled[0]
    local.semantics.extend(compiled[1].semantics)
    local.value_evidence.extend(compiled[1].value_evidence)
    local.schema_evidence.extend(compiled[1].schema_evidence)
    regions[0]["nodeIds"].extend(regions[1]["nodeIds"])
    before = copy.deepcopy(local)
    tasks = build_scope_tasks(obs, regions[:1], [local])
    assert len(tasks) == 2
    assert all(c["candidateBasis"] == "sameRegion" for t in tasks for c in t.payload["candidates"])
    assert local == before
    result, changed = apply_scope_decision([local], tasks[0], decision(tasks[0]))
    assert changed and result[0].semantic_details[0]["scope"]
    assert not result[0].semantic_details[1]["scope"]


def test_scope_batch_is_bounded_and_valid_siblings_survive_bad_decisions():
    from dataclasses import replace

    from jsonschema import Draft202012Validator

    from document_files.interpretation.integration import (
        parse_scope_choices,
        scope_batches,
        scope_output_schema,
    )

    obs, regions, compiled = fixture()
    tasks = build_scope_tasks(obs, regions, compiled)
    # Both statements come from one note: they are decided in separate requests.
    assert list(scope_batches(tasks, context_chars=120000)) == [[t] for t in tasks]
    assert list(scope_batches(tasks, context_chars=1024)) == [[t] for t in tasks]
    distinct = [
        replace(
            task,
            id=f"{task.id}-{index}",
            payload={
                **task.payload,
                "statement": {**task.payload["statement"], "sourceRefs": [f"note-{index}"]},
            },
        )
        for index, task in enumerate(tasks * 5)
    ]
    assert [len(b) for b in scope_batches(distinct, context_chars=1000000)] == [8, 2]
    Draft202012Validator.check_schema(scope_output_schema(tasks))
    good = decision(tasks[0])
    for bad in (None, {"taskId": tasks[1].id}, dict(decision(tasks[1]), taskId="unknown")):
        choices, invalid = parse_scope_choices({"decisions": [good, bad]}, tasks)
        assert invalid and [c.taskId for c in choices] == [tasks[0].id]
    choices, invalid = parse_scope_choices({"decisions": [good, good, decision(tasks[1])]}, tasks)
    assert invalid and [c.taskId for c in choices] == [tasks[1].id]
    with pytest.raises(ValueError):
        parse_scope_choices({"decisions": [], "extra": "untrusted"}, tasks)


def grouped_table_fixture():
    obs, regions, compiled = fixture()
    obs.nodes.update({"measurements": {"text": "Measurements"}, "width": {"text": "Width"}})
    target = compiled[1]
    target.semantics[0]["description"] = "Sample ID"
    width = copy.deepcopy(target.semantics[1])
    width.update(
        id="r2width",
        description="Width",
        sourceRefs=["width", "measurements"],
        scope=[{"space": "data", "path": "/rows/0/width"}],
        targets=[{"space": "dataSchema", "path": "/properties/rows/items/properties/width"}],
    )
    target.semantics[1]["sourceRefs"].append("measurements")
    target.semantics.append(width)
    target.repeat_paths["rows"] = {
        "tableRef": "table",
        "rowStart": 2,
        "rowEnd": 3,
        "columns": [0, 1, 2],
        "columnDefinitions": {"0": "r2price", "1": "r2length", "2": "r2width"},
    }
    obs.tables["table"] = {
        "basis": "native_structure",
        "headerCells": [
            {"sourceRef": "price", "row": 0, "col": 0, "isHeader": True},
            {"sourceRef": "measurements", "row": 0, "col": 1, "colSpan": 2, "isHeader": True},
            {"sourceRef": "length", "row": 1, "col": 1, "isHeader": True},
            {"sourceRef": "width", "row": 1, "col": 2, "isHeader": True},
        ],
    }
    return obs, regions, compiled


def test_group_header_scope_maps_only_child_columns_and_separates_statement_context():
    obs, regions, compiled = grouped_table_fixture()
    tasks = build_scope_tasks(obs, regions, compiled)
    task = next(t for t in tasks if t.semantic_id == "mm")
    group = next(c for c in task.payload["candidates"] if c.get("candidateKind") == "headerGroup")
    assert [m["label"] for m in group["members"]] == ["Length", "Width"]
    assert [h["label"] for h in group["members"][0]["headerPath"]] == ["Measurements", "Length"]
    assert "sourceText" not in task.payload["statement"]
    assert task.payload["statement"]["description"] == "Length uses mm"
    assert task.payload["surroundingContext"]["sourceText"] == ["Cost is USD. Length is mm."]
    before = copy.deepcopy(compiled)
    result, changed = apply_scope_decision(compiled, task, decision(task, "Measurements"))
    assert changed and compiled == before
    assert result[0].semantic_details[1]["scope"] == [
        {"space": "data", "path": "/length"},
        {"space": "data", "path": "/rows/0/width"},
    ]
    assert result[0].semantic_details[0] == compiled[0].semantic_details[0]
    assert result[1].data == compiled[1].data and result[1].schema == compiled[1].schema
    assert "mm" not in result[1].value_evidence[0]["semanticIds"]
    # Selecting a leaf still selects only that column, not its enclosing group.
    result, changed = apply_scope_decision(compiled, task, decision(task, "Length"))
    assert changed and result[0].semantic_details[1]["scope"] == [
        {"space": "data", "path": "/length"}
    ]


def test_group_and_covered_column_repeat_only_exact_destinations():
    obs, regions, compiled = grouped_table_fixture()
    task = next(t for t in build_scope_tasks(obs, regions, compiled) if t.semantic_id == "mm")
    group = next(c for c in task.payload["candidates"] if c.get("candidateKind") == "headerGroup")
    leaf = next(c for c in task.payload["candidates"] if c["label"] == "Length")
    assert leaf["targetHandle"] in group["coversCandidates"]
    choice = decision(task, "Measurements")
    choice["targetHandles"].append(leaf["targetHandle"])
    group_only, _ = apply_scope_decision(compiled, task, decision(task, "Measurements"))
    result, changed = apply_scope_decision(compiled, task, choice)
    assert changed
    assert result[0].semantic_details[1]["scope"] == group_only[0].semantic_details[1]["scope"]


def test_container_and_nested_column_scope_is_rejected_atomically():
    obs, regions, compiled = grouped_table_fixture()
    # A row container and a cell below it are different semantic granularities.
    container = copy.deepcopy(compiled[1].semantics[-1])
    container.update(id="record", description="Record", scope=[{"space": "data", "path": "/rows"}])
    compiled[1].semantics.append(container)
    task = next(t for t in build_scope_tasks(obs, regions, compiled) if t.semantic_id == "mm")
    choice = decision(task, "Record")
    choice["targetHandles"] += decision(task, "Width")["targetHandles"]
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError, match="overlapping_scope_targets"):
        apply_scope_decision(compiled, task, choice)
    assert compiled == before


def test_same_pointer_in_two_regions_is_not_containment():
    obs, regions, compiled = fixture()
    regions = [regions[1], regions[0], regions[2]]
    compiled[2].semantics[0]["scope"] = [{"space": "data", "path": "/price"}]
    task = build_scope_tasks(obs, regions, compiled)[0]
    choice = decision(task, "Cost")
    other = decision(task, "Unrelated amount")
    choice["targetHandles"] += other["targetHandles"]
    choice["sourceRefs"] += other["sourceRefs"]
    assert all(not c.get("coversCandidates") for c in task.payload["candidates"])
    _, changed = apply_scope_decision(compiled, task, choice)
    assert changed


@pytest.mark.parametrize("mutation", ["mapping", "definition", "status", "missing-header-evidence"])
def test_group_scope_rejects_stale_membership_and_requires_header_evidence(mutation):
    obs, regions, compiled = grouped_table_fixture()
    task = build_scope_tasks(obs, regions, compiled)[0]
    choice = decision(task, "Measurements")
    if mutation == "mapping":
        compiled[1].repeat_paths["rows"]["columnDefinitions"]["2"] = "r2price"
    elif mutation == "definition":
        compiled[1].semantics[-1]["scope"][0]["path"] = "/changed"
    elif mutation == "status":
        compiled[1].semantics[-1]["status"] = "uncertain"
    else:
        choice["sourceRefs"].remove("measurements")
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError):
        apply_scope_decision(compiled, task, choice)
    assert compiled == before


def test_group_geometry_changes_fingerprint_and_missing_children_are_not_grouped():
    obs, regions, compiled = grouped_table_fixture()
    before = build_scope_tasks(obs, regions, compiled)[0]
    obs.tables["table"]["headerCells"][1]["rowSpan"] = 1
    after = build_scope_tasks(obs, regions, compiled)[0]
    assert before.fingerprint != after.fingerprint
    del compiled[1].repeat_paths["rows"]["columnDefinitions"]["2"]
    task = build_scope_tasks(obs, regions, compiled)[0]
    assert all(c.get("candidateKind") != "headerGroup" for c in task.payload["candidates"])


def test_truncated_group_context_and_ambiguous_decisions_remain_partial():
    obs, regions, compiled = grouped_table_fixture()
    obs.nodes["measurements"]["text"] = "Measurements " + "x" * 500
    task = build_scope_tasks(obs, regions, compiled)[0]
    group = next(c for c in task.payload["candidates"] if c.get("candidateKind") == "headerGroup")
    assert not task.complete_candidates and not group["contextComplete"]
    choice = decision(task, group["label"])
    result, changed = apply_scope_decision(compiled, task, choice)
    assert changed and result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    assert any(i.get("semanticId") == "usd" for i in result[0].issues)
    unresolved = {"taskId": task.id, "decision": "unresolved", "explanation": "Ambiguous group."}
    assert apply_scope_decision(compiled, task, unresolved) == (compiled, False)


def test_header_group_uses_compiled_header_roles_not_recognizer_flags():
    obs, regions, compiled = grouped_table_fixture()
    table = obs.tables["table"]
    table["basis"] = "docling_table_cells"
    # Positive predictions alone are not confirmed header membership.
    tasks = build_scope_tasks(obs, regions, compiled)
    assert not any(
        c.get("candidateKind") == "headerGroup" for t in tasks for c in t.payload["candidates"]
    )
    refs = [c["sourceRef"] for c in table["headerCells"]]
    for c in table["headerCells"]:
        c["isHeader"] = False
    compiled[1].repeat_paths["rows"]["headerSourceRefs"] = refs
    tasks = build_scope_tasks(obs, regions, compiled)
    groups = [
        c for t in tasks for c in t.payload["candidates"] if c.get("candidateKind") == "headerGroup"
    ]
    assert groups
    assert all(
        c["label"] == "Measurements" and "measurements" in c["definitionRefs"] for c in groups
    )
