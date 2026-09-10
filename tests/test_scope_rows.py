"""Row applicability over real compiler mappings; scripted, not model quality evidence."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_regional_interpretation import _table

from document_files.interpretation.compiler import (
    CompileError,
    combine_regions,
    compile_region,
    join_continuations,
    target_catalog,
)
from document_files.interpretation.integration import (
    ScopeRows,
    _ScopeTargetIndex,
    apply_scope_decision,
    build_scope_tasks,
    parse_scope_choices,
)
from document_files.interpretation.scope_reference_wire import prepare_scope_wire


def fixture():
    doc, region, ir = _table()
    ir.meanings[0].fieldIds = []
    return doc, region, ir


def task_for(doc, regions, compiled, *, owner="r"):
    return next(t for t in build_scope_tasks(doc, regions, compiled) if t.region_id == owner)


def choice(task, *, start=2, end=2, columns=None, target_region="r", row_ref="c2:0"):
    handle = next(
        h for h, c in task.target_map.items() if c["regionId"] == target_region and "rowScope" in c
    )
    return {
        "taskId": task.id,
        "decision": "apply",
        "targetHandles": [],
        "rowSelections": [
            {
                "targetHandle": handle,
                "rowStart": start,
                "rowEnd": end,
                "columnIds": ["amount"] if columns is None else columns,
            }
        ],
        "sourceRefs": ["c0:1", "c0:0", row_ref],
        "explanation": "Scripted row/column applicability, not a model quality result.",
    }


def test_row_mapping_is_geometry_not_output_ordinal_and_preserves_values():
    doc, region, ir = fixture()
    original = copy.deepcopy(doc)
    compiled = compile_region(ir, doc, region)
    mapping = compiled.row_scopes["rows"]
    assert mapping["rows"]["0"]["role"] == "header"
    assert mapping["rows"]["0"]["targets"] == {}
    assert mapping["rows"]["2"]["targets"]["amount"]["path"] == "/rows/1/amount"
    task = task_for(doc, [region], [compiled])
    assert task.complete_candidates
    encoded = json.dumps(task.payload)
    assert "/rows/1/amount" not in encoded and "dataOwnerRegionId" not in encoded
    before = copy.deepcopy(compiled)
    result, changed = apply_scope_decision([compiled], task, choice(task))
    assert changed and doc == original and compiled == before
    after = result[0]
    assert after.data == before.data and after.schema == before.schema
    assert after.schema_evidence == before.schema_evidence
    assert after.value_observations == before.value_observations
    assert after.semantic_details[0]["scope"] == [{"space": "data", "path": "/rows/1/amount"}]
    assert [e["target"]["path"] for e in after.value_evidence if "r:unit" in e["semanticIds"]] == [
        "/rows/1/amount"
    ]
    assert apply_scope_decision(result, task, choice(task)) == (result, False)


@pytest.mark.parametrize("role", ["note", "subtotal", "blank", "unresolved", "unobserved"])
def test_row_range_never_creates_records_for_nondata_or_unknown_rows(role):
    doc, region, ir = fixture()
    if role == "unobserved":
        doc.tables["t"]["cells"] = [c for c in doc.tables["t"]["cells"] if c["row"] != 2]
        ir.repeats[0].rowRoles = [r for r in ir.repeats[0].rowRoles if r.row != 2]
    else:
        ir.repeats[0].rowRoles[2].role = role
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    result, changed = apply_scope_decision(
        [compiled], task, choice(task, start=1, end=3, columns=[], row_ref="c1:0")
    )
    assert changed and result[0].data == compiled.data
    assert [r["name"] for r in result[0].data["rows"]] == ["A", "C"]
    detail = result[0].semantic_details[0]
    assert len(detail["scope"]) == 4
    assert detail["interpretationStatus"] == (
        "uncertain" if role in {"unresolved", "unobserved"} else "interpreted"
    )
    assert any(i["code"] == "semantic_scope_unresolved" for i in result[0].issues) == (
        role in {"unresolved", "unobserved"}
    )


@pytest.mark.parametrize(
    "basis,status", [("native_structure", "absent"), ("recognition", "uncertain")]
)
def test_row_scope_does_not_turn_a_missing_cell_into_blank(basis, status):
    doc, region, ir = fixture()
    doc.tables["t"]["basis"] = basis
    doc.tables["t"]["cells"] = [c for c in doc.tables["t"]["cells"] if c["sourceRef"] != "c3:1"]
    ir.repeats[0].rowRoles[3].sourceRefs = ["c3:0"]
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    result, _ = apply_scope_decision([compiled], task, choice(task, start=3, end=3, row_ref="c3:0"))
    assert result[0].data["rows"][-1]["amount"] is None
    assert result[0].value_evidence[-1]["status"] == status
    assert result[0].value_observations == compiled.value_observations


@pytest.mark.parametrize(
    "mutation",
    [
        "outside",
        "backwards",
        "boolean",
        "column",
        "no-row-evidence",
        "no-definition",
        "header-only",
        "whole-column",
        "whole-record",
        "duplicate-range",
        "changed-target",
        "changed-role",
        "changed-label",
        "missing-definition",
    ],
)
def test_invalid_or_stale_row_selection_is_atomic(mutation):
    doc, region, ir = fixture()
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    value = choice(task)
    row = value["rowSelections"][0]
    if mutation == "outside":
        row["rowEnd"] = 100
    elif mutation == "backwards":
        row["rowStart"] = 3
    elif mutation == "boolean":
        row["rowStart"] = True
    elif mutation == "column":
        row["columnIds"] = ["not-a-column"]
    elif mutation == "no-row-evidence":
        value["sourceRefs"].remove("c2:0")
    elif mutation == "no-definition":
        value["sourceRefs"].remove("c0:0")
    elif mutation == "header-only":
        row.update(rowStart=0, rowEnd=0)
    elif mutation == "whole-record":
        value["targetHandles"] = [row["targetHandle"]]
    elif mutation == "whole-column":
        value["targetHandles"] = [
            next(c["targetHandle"] for c in task.payload["candidates"] if c["label"] == "Amount")
        ]
    elif mutation == "duplicate-range":
        value["rowSelections"].append(copy.deepcopy(row))
    elif mutation == "changed-target":
        compiled.row_scopes["rows"]["rows"]["2"]["targets"]["amount"]["path"] = "/elsewhere"
    elif mutation == "changed-role":
        compiled.row_scopes["rows"]["rows"]["2"]["role"] = "note"
    elif mutation == "changed-label":
        next(d for d in compiled.semantics if d["id"] == "r:amount")["description"] = "Changed"
    else:
        compiled.semantics = [d for d in compiled.semantics if d["id"] != "r:amount"]
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError):
        apply_scope_decision([compiled], task, value)
    assert compiled == before


def test_row_scope_alias_round_trip_preserves_source_and_literal_column_ids():
    doc, region, ir = fixture()
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    wire = prepare_scope_wire([task])
    value = choice(task)
    raw = copy.deepcopy(value)
    inverse = {h: a for a, h in wire.targets.items()}
    raw["rowSelections"][0]["targetHandle"] = inverse[value["rowSelections"][0]["targetHandle"]]
    assert raw["rowSelections"][0]["targetHandle"].startswith("@container")
    Draft202012Validator.check_schema(wire.contract)
    Draft202012Validator(wire.contract).validate(raw)
    assert wire.decode(raw) == value
    wrong = copy.deepcopy(raw)
    wrong["rowSelections"][0]["targetHandle"] = value["rowSelections"][0]["targetHandle"]
    valid, invalid = parse_scope_choices(wire.decode(wrong), [task])
    assert invalid and not valid
    wrong["rowSelections"][0]["targetHandle"] = next(
        a for a in wire.targets if a.startswith("@column")
    )
    valid, invalid = parse_scope_choices(wire.decode(wrong), [task])
    assert invalid and not valid


def test_row_scope_uses_actual_array_root_and_escaped_property_targets():
    doc, region, ir = fixture()
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"a/b": {"type": "string"}, "c~d": {"type": "string"}},
            "required": ["a/b", "c~d"],
            "additionalProperties": False,
        },
    }
    catalog = target_catalog(schema)
    ir.repeats[0].targetHandle = "t0"
    for column, key in zip(ir.repeats[0].columns, ["a/b", "c~d"], strict=True):
        column.targetHandle = next(h for h, t in catalog.items() if t["tokens"] == ["*", key])
    compiled = compile_region(ir, doc, region, target_schema=schema)
    task = task_for(doc, [region], [compiled])
    result, _ = apply_scope_decision([compiled], task, choice(task))
    assert result[0].semantic_details[0]["scope"] == [{"space": "data", "path": "/1/c~0d"}]
    assert result[0].data == compiled.data and result[0].schema_evidence == compiled.schema_evidence


def two_fragments():
    doc, region, ir = fixture()
    doc2, region2, ir2 = fixture()

    def refs(value):
        if isinstance(value, dict):
            return {
                k: (
                    "p2-" + v
                    if k == "sourceRef"
                    else ["p2-" + r for r in v]
                    if k in {"sourceRefs", "definitionRefs"}
                    else "t2"
                    if k == "tableRef"
                    else refs(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [refs(v) for v in value]
        return value

    doc.nodes.update({"p2-" + k: v for k, v in doc2.nodes.items()})
    doc.bindings.update({"p2-" + k: refs(v) for k, v in doc2.bindings.items()})
    doc.tables["t2"] = refs(doc2.tables["t"])
    region2 = {
        **region2,
        "id": "p2",
        "tableRef": "t2",
        "nodeIds": ["p2-" + r for r in region2["nodeIds"]],
        "bindingIds": ["p2-" + b for b in region2["bindingIds"]],
    }
    ir2 = type(ir2).model_validate(refs(ir2.model_dump()) | {"regionId": "p2", "meanings": []})
    return (
        doc,
        [region, region2],
        [compile_region(ir, doc, region), compile_region(ir2, doc, region2)],
    )


@pytest.mark.parametrize("joined", [False, True])
def test_row_scope_preserves_fragment_geometry_after_continuation_and_does_not_leak(joined):
    doc, regions, compiled = two_fragments()
    original = copy.deepcopy(compiled)
    if joined:
        compiled, issues, links = join_continuations(
            compiled,
            [
                {
                    "id": "join",
                    "leftRegion": "r",
                    "rightRegion": "p2",
                    "leftTable": "t",
                    "rightTable": "t2",
                    "confirmed": False,
                    "sourceRefs": ["c0:0", "p2-c0:0"],
                }
            ],
            {"join": "continue"},
        )
        assert not issues and links
        assert compiled[1].row_scopes["rows"]["rowStart"] == 0
        assert compiled[1].row_scopes["rows"]["dataOwnerRegionId"] == "r"
    task = task_for(doc, regions, compiled)
    value = choice(task, target_region="p2", row_ref="p2-c2:0")
    value["sourceRefs"] = ["c0:1", "p2-c2:0", "p2-c0:0", "p2-c0:1"]
    result, changed = apply_scope_decision(compiled, task, value)
    expected = "/rows/4/amount" if joined else "/rows/1/amount"
    assert changed and result[0].semantic_details[0]["scope"] == [
        {"space": "data", "path": expected}
    ]
    assert all("r:unit" not in e["semanticIds"] for e in result[0].value_evidence)
    assert [
        e["target"]["path"] for e in result[1].value_evidence if "r:unit" in e["semanticIds"]
    ] == [expected]
    if joined:
        assert len(combine_regions(result)["data"]["rows"]) == 6
        value["targetHandles"] = [
            next(h for h, c in task.target_map.items() if c["regionId"] == "r" and "rowScope" in c)
        ]
        with pytest.raises(CompileError, match="overlapping_scope_targets"):
            apply_scope_decision(compiled, task, value)
        assert (
            original[1].row_scopes["rows"]["rows"]["2"]["targets"]["amount"]["path"]
            == "/rows/1/amount"
        )


def test_scope_target_index_keeps_region_space_root_and_escaped_tokens_distinct():
    index = _ScopeTargetIndex()
    targets = [{"space": "data", "path": f"/rows/{i}/a~1b"} for i in range(10000)]
    index.add("r", targets)
    assert all(index.contains("r", t) for t in targets)
    assert not index.overlaps("r", targets[0], exact=False)
    assert index.overlaps("r", targets[0], exact=True)
    assert index.overlaps("r", {"space": "data", "path": "/rows"}, exact=False)
    assert not index.contains("r", {"space": "data", "path": "/rows"})
    assert not index.contains("other", targets[0])
    assert not index.contains("r", {"space": "dataSchema", "path": targets[0]["path"]})
    assert not index.contains("r", {"space": "data", "path": "/rows/0/a/b"})
    index.add("r", [{"space": "data", "path": ""}])
    assert index.contains("r", {"space": "data", "path": "/anything"})


def test_row_range_expansion_budget_is_checked_before_traversal():
    doc, region, ir = fixture()
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    value = choice(task, start=0, end=1000000, columns=[])
    with pytest.raises(CompileError, match="expansion_budget"):
        apply_scope_decision([compiled], task, value)
    with pytest.raises(ValueError):
        ScopeRows(targetHandle="x", rowStart=1, rowEnd=0)


def test_row_context_truncation_stays_partial_and_mapping_changes_fingerprint():
    doc, region, ir = fixture()
    doc.nodes["c2:0"]["text"] = "X" * 700
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    assert not task.complete_candidates
    result, changed = apply_scope_decision([compiled], task, choice(task))
    assert changed and result[0].semantic_details[0]["interpretationStatus"] == "uncertain"
    assert any(i["code"] == "semantic_scope_unresolved" for i in result[0].issues)
    assert result[0].data == compiled.data
    compiled.row_scopes["rows"]["rows"]["2"]["targets"]["amount"]["path"] = "/remapped"
    assert task_for(doc, [region], [compiled]).fingerprint != task.fingerprint


def test_row_column_id_that_looks_like_an_alias_is_literal():
    doc, region, ir = fixture()
    ir.repeats[0].columns[1].id = "@column0"
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    wire = prepare_scope_wire([task])
    value = choice(task, columns=["@column0"])
    raw = copy.deepcopy(value)
    inverse = {h: a for a, h in wire.targets.items()}
    raw["rowSelections"][0]["targetHandle"] = inverse[value["rowSelections"][0]["targetHandle"]]
    assert wire.decode(raw) == value
    result, changed = apply_scope_decision([compiled], task, wire.decode(raw))
    assert changed and result[0].semantic_details[0]["scope"] == [
        {"space": "data", "path": "/rows/1/amount"}
    ]


def test_bad_row_alias_preserves_valid_sibling_and_duplicate_task_detection():
    doc, region, ir = fixture()
    ir.meanings.append(ir.meanings[0].model_copy(update={"id": "other", "sourceRefs": ["c0:0"]}))
    compiled = compile_region(ir, doc, region)
    tasks = build_scope_tasks(doc, [region], [compiled])
    wire = prepare_scope_wire(tasks)
    inverse = {h: a for a, h in wire.targets.items()}
    choices = [choice(t) for t in tasks]
    for value in choices:
        value["rowSelections"][0]["targetHandle"] = inverse[
            value["rowSelections"][0]["targetHandle"]
        ]
    choices[1]["rowSelections"][0]["targetHandle"] = "@unoffered-row-table"
    valid, invalid = parse_scope_choices(wire.decode({"decisions": choices}), tasks)
    assert invalid and [v.taskId for v in valid] == [tasks[0].id]
    choices[1]["taskId"] = choices[0]["taskId"]
    valid, invalid = parse_scope_choices(wire.decode({"decisions": choices}), tasks)
    assert invalid and not valid


@pytest.mark.parametrize("mode", ["rows", "column"])
@pytest.mark.parametrize("meaning_status", ["interpreted", "uncertain", None])
def test_scope_evidence_never_promotes_the_meaning_own_uncertainty(mode, meaning_status):
    doc, region, ir = fixture()
    ir.meanings[0].status = meaning_status or "interpreted"
    compiled = compile_region(ir, doc, region)
    if meaning_status is None:
        compiled.meaning_statuses.clear()
    task = task_for(doc, [region], [compiled])
    chosen = choice(task)
    if mode == "column":
        chosen["rowSelections"] = []
        chosen["targetHandles"] = [
            next(
                h
                for h, c in task.target_map.items()
                if c.get("definition", {}).get("id") == "r:amount"
            )
        ]
        chosen["sourceRefs"] = ["c0:1"]
    before = copy.deepcopy(compiled)
    result, changed = apply_scope_decision([compiled], task, chosen)
    assert changed and compiled == before
    after = result[0]
    assert after.semantic_details[0]["scope"]
    expected = "interpreted" if meaning_status == "interpreted" else "uncertain"
    assert after.semantic_details[0]["interpretationStatus"] == expected
    assert next(s for s in after.semantics if s["id"] == "r:unit")["status"] == expected
    assert after.data == before.data and after.schema == before.schema
    assert after.value_observations == before.value_observations
    assert not any(i["code"] == "semantic_scope_unresolved" for i in after.issues)
    assert any(i["code"] == "semantic_interpretation_uncertain" for i in after.issues) == (
        meaning_status != "interpreted"
    )
    assert apply_scope_decision(result, task, chosen) == (result, False)
    public = json.dumps(combine_regions(result))
    assert "meaningStatus" not in public and "meaning_statuses" not in public


def test_meaning_status_changes_invalidate_scope_without_rewriting_model_context():
    doc, region, ir = fixture()
    compiled = compile_region(ir, doc, region)
    task = task_for(doc, [region], [compiled])
    compiled.meaning_statuses["r:unit"] = "uncertain"
    replacement = task_for(doc, [region], [compiled])
    assert task.payload == replacement.payload
    assert task.fingerprint != replacement.fingerprint
    before = copy.deepcopy(compiled)
    with pytest.raises(CompileError, match="stale_scope_statement"):
        apply_scope_decision([compiled], task, choice(task))
    assert compiled == before


def test_meaning_uncertainty_remains_visible_when_initial_scope_is_known():
    doc, region, ir = fixture()
    ir.meanings[0].status = "uncertain"
    ir.meanings[0].fieldIds = ["amount"]
    compiled = compile_region(ir, doc, region)
    assert compiled.semantic_details[0]["scope"]
    assert compiled.semantic_details[0]["interpretationStatus"] == "uncertain"
    assert compiled.meaning_statuses == {"r:unit": "uncertain"}
    assert any(i["code"] == "semantic_interpretation_uncertain" for i in compiled.issues)
