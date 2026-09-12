"""Structure-first native controller contracts; scripted decisions are not AI quality."""

import copy

import pytest
from jsonschema import Draft202012Validator
from test_document_protocol import StagedModel, execute, raw_document
from test_native_records import fixture, structure_fixture

from document_files.interpretation import native_structure as native
from document_files.interpretation.backends import ModelError
from document_files.interpretation.compiler import CompileError, compile_region


def prepared():
    doc, region, old = fixture()
    roles = {"regionId": region["id"], "documentElements": old["documentElements"]}
    structure = native.NativeStructure.model_validate(structure_fixture(old))
    return doc, region, roles, structure, old


def choices(structure, old):
    result = {}
    for e in native.entries(structure):
        if e["status"] not in {"present", "blank"}:
            continue
        if "fieldId" in e:
            v = next(f for f in old["fields"] if f["id"] == e["fieldId"])
        else:
            r = next(r for r in old["logicalRecords"] if r["id"] == e["recordId"])
            row = next(r for r in r["rows"] if r["id"] == e["rowId"])
            v = next(v for v in row["values"] if v["columnId"] == e["columnId"])
        result[e["handle"]] = (
            {"kind": "quote", "quote": v["sourceQuote"]}
            if v.get("sourceQuote")
            else {"kind": "binding", "bindingId": v["bindingId"], "status": "blank"}
        )
    return {"regionId": structure.regionId, "selections": result, "excludedBindings": []}


def test_structure_stage_has_original_blocks_and_no_parser_value_choices():
    doc, region, roles, structure, _ = prepared()
    original = copy.deepcopy((doc, region))
    payload, contract = native.request(
        doc, region, roles, {"intent": "discover", "targetHandles": {}}
    )
    assert payload["acceptedRoles"] == roles["documentElements"]
    assert "bindings" not in payload and "requiredBindingIds" not in payload
    assert all(payload["blocks"][r]["text"] == n["text"] for r, n in doc.nodes.items())
    raw = structure.model_dump(exclude_unset=True)
    Draft202012Validator(contract).validate(raw)
    del raw["fields"][0]["valueType"]
    assert not Draft202012Validator(contract).is_valid(raw)
    assert (doc, region) == original


def test_frozen_record_structure_preserves_order_precision_blank_absent_and_sources():
    doc, region, roles, structure, old = prepared()
    before = copy.deepcopy((doc, region, structure))
    skeleton = compile_region(native.interpretation(structure, roles, doc, region), doc, region)
    assert len(skeleton.data["items"]) == 2
    assert skeleton.data["reference"] is None
    assert skeleton.data["items"][0]["requested"] is None
    assert skeleton.schema["properties"]["items"]["items"]["properties"]["requested"]
    ir = native.accept_values(choices(structure, old), structure, roles, doc, region)
    result = compile_region(ir, doc, region)
    assert result.data["reference"] == "0007"
    assert [r["name"] for r in result.data["items"]] == ["Basil", "Marigold"]
    assert [r["length"] for r in result.data["items"]] == ["12.5000", "2.000"]
    assert result.data["items"][1]["received"] == "" and result.data["items"][1]["date"] is None
    assert ir.meanings[0].sourceRanges and ir.meanings[0].description == "mm"
    assert (doc, region, structure) == before


@pytest.mark.parametrize(
    "damage",
    ["extra_field", "omit", "unknown", "status", "foreign_quote", "extra_type", "extra_source"],
)
def test_value_stage_cannot_mutate_or_bypass_frozen_structure(damage):
    doc, region, roles, structure, old = prepared()
    value = choices(structure, old)
    h = next(iter(value["selections"]))
    if damage == "extra_field":
        value["fields"] = []
    elif damage == "omit":
        del value["selections"][h]
    elif damage == "unknown":
        value["selections"]["@value999"] = {"kind": "unresolved"}
    elif damage == "status":
        value["selections"][h] = {
            "kind": "binding",
            "bindingId": next(iter(doc.bindings)),
            "status": "blank",
        }
    elif damage == "foreign_quote":
        value["selections"][h]["quote"]["sourceRef"] = "a"
    elif damage == "extra_type":
        value["selections"][h]["valueType"] = "string"
    else:
        value["selections"][h]["bindingId"] = next(iter(doc.bindings))
    with pytest.raises(ValueError, match="native_value"):
        native.accept_values(value, structure, roles, doc, region)


def test_missing_value_preserves_field_type_and_record_occurrence():
    doc, region, roles, structure, old = prepared()
    value = choices(structure, old)
    for h in value["selections"]:
        value["selections"][h] = {"kind": "unresolved"}
    ir = native.accept_values(value, structure, roles, doc, region)
    result = compile_region(ir, doc, region)
    assert ir.fields[0].valueType == "string" and len(ir.logicalRecords[0].rows) == 2
    assert ir.logicalRecords[0].columns[1].valueType == "integer"
    assert set(result.data["items"][0]) == {"name", "requested", "received", "length", "date"}
    assert ir.unresolved and all(v is None for v in result.data["items"][0].values())


def test_label_and_value_blocks_remain_distinct_and_do_not_collapse():
    doc, region, roles, _, _ = prepared()
    structure = native.NativeStructure.model_validate(
        {
            "regionId": region["id"],
            "fields": [
                {
                    "id": "a",
                    "key": "reference",
                    "label": "Reference",
                    "valueType": "string",
                    "definitionRefs": ["note"],
                    "sourceRefs": ["meta"],
                    "status": "present",
                },
                {
                    "id": "b",
                    "key": "length",
                    "label": "Length",
                    "valueType": "decimal",
                    "definitionRefs": ["meta"],
                    "sourceRefs": ["a"],
                    "status": "present",
                },
            ],
        }
    )
    value = {
        "regionId": region["id"],
        "selections": {
            "@value1": {"kind": "quote", "quote": {"sourceRef": "meta", "text": "0007"}},
            "@value2": {"kind": "quote", "quote": {"sourceRef": "a", "text": "12.5000"}},
        },
        "excludedBindings": [],
    }
    ir = native.accept_values(value, structure, roles, doc, region)
    result = compile_region(ir, doc, region)
    assert result.data == {"reference": "0007", "length": "12.5000"}
    assert ir.fields[0].definitionRefs == ["note"] and ir.fields[0].valueSourceRefs == ["meta"]


@pytest.mark.parametrize("damage", ["field", "record", "column", "row", "cell", "missing_cell"])
def test_duplicate_or_incomplete_semantic_identity_is_rejected(damage):
    doc, region, roles, structure, _ = prepared()
    raw = structure.model_dump(exclude_unset=True)
    if damage in {"field", "record"}:
        key = "fields" if damage == "field" else "records"
        raw[key].append(copy.deepcopy(raw[key][0]))
    elif damage == "column":
        raw["records"][0]["columns"].append(copy.deepcopy(raw["records"][0]["columns"][0]))
    elif damage == "row":
        raw["records"][0]["rows"].append(copy.deepcopy(raw["records"][0]["rows"][0]))
    else:
        cells = raw["records"][0]["rows"][0]["cells"]
        cells.pop() if damage == "missing_cell" else cells.append(copy.deepcopy(cells[0]))
    with pytest.raises((CompileError, ValueError)):
        ir = native.interpretation(native.NativeStructure.model_validate(raw), roles, doc, region)
        compile_region(ir, doc, region)


def test_value_budget_pause_preserves_structure_and_resumes_only_value_stage():
    raw = raw_document(("Count: 0007",))
    model, states = StagedModel(), []
    partial = execute(model, raw=raw, states=states, budget=2)
    assert partial["extraction"]["status"] == "partial" and partial["data"] == {"count": None}
    state = next(iter(partial["coverage"]["documentInterpretation"].values()))
    assert state["structureStatus"] == "complete" and state["contentStatus"] == "pending"
    resumed = execute(
        model, raw=raw, budget=2, restore=copy.deepcopy(states[-1]), grant={"maxModelCalls": 1}
    )
    assert resumed["data"] == {"count": "0007"} and resumed["extraction"]["status"] == "complete"
    assert model.calls == 3 and len(model.structure_requests) == 1


def test_unknown_value_response_never_replays_without_explicit_grant():
    class Model(StagedModel):
        fail = True

        def infer(self, request):
            import json

            if (
                self.fail
                and json.loads(request.messages[-1]["content"]).get("documentStage") == "values"
            ):
                self.calls += 1
                raise ModelError("ai_test_transport_failure")
            return super().infer(request)

    model, states = Model(), []
    raw = raw_document(("Count: 0007",))
    partial = execute(model, raw=raw, states=states)
    assert partial["data"] == {"count": None} and model.calls == 3
    model.fail = False
    assert (
        execute(model, raw=raw, restore=copy.deepcopy(states[-1]))["extraction"]["status"]
        == "partial"
    )
    assert model.calls == 3
    done = execute(model, raw=raw, restore=copy.deepcopy(states[-1]), grant={"maxModelCalls": 1})
    assert done["extraction"]["status"] == "complete" and model.calls == 4
    assert len(model.outline_requests) == len(model.structure_requests) == 1


@pytest.mark.parametrize(
    "damage", ["hash", "request", "type", "response", "version", "source_choice"]
)
def test_checkpoint_rebuilds_structure_and_value_selection_identity(damage):
    model, states = StagedModel(), []
    raw = raw_document(("Count: 0007",))
    execute(model, raw=raw, states=states)
    state = copy.deepcopy(states[-1])
    saved = next(iter(state["documentStages"].values()))
    if damage == "hash":
        saved["structure"]["structureHash"] = "0" * 64
    elif damage == "request":
        saved["structure"]["requestHash"] = "0" * 64
    elif damage == "type":
        saved["structure"]["response"]["fields"][0]["valueType"] = "integer"
        from document_files.interpretation.document_protocol import digest

        saved["structure"]["structureHash"] = digest(saved["structure"]["response"])
    elif damage == "response":
        del saved["structure"]["response"]
    elif damage == "source_choice":
        saved["content"]["response"]["selections"]["@value1"] = {"kind": "unresolved"}
    else:
        state["identity"]["nativeStructureVersion"] = "old"
    with pytest.raises(ValueError, match="incompatible"):
        execute(model, raw=raw, restore=state)
    assert model.calls == 3


def test_native_planning_measures_stage_contract_not_a_fixed_reserve():
    from document_files.document_model.observe import observe_document
    from document_files.interpretation.regions import prepare_regions, region_payload

    texts = [
        "Section Alpha",
        *["Detail " + str(i) + " with contextual prose " * 20 for i in range(7)],
    ]
    doc = observe_document(
        b"scripted native", "hwpx", {f"n{i}": {"text": t} for i, t in enumerate(texts)}
    )
    before = copy.deepcopy(doc.nodes)
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) == 1
    region = regions[0]
    assert len(__import__("json").dumps(region_payload(doc, region))) > 4000
    assert region["withinContextBudget"] and set(region["nativeRequestChars"]) == {
        "roles",
        "structure",
    }
    estimate = native.planned_request_sizes(
        doc, region, {"intent": "discover", "targetHandles": {}}
    )
    assert region["requestChars"] == max(estimate["roles"], estimate["structure"])
    assert doc.nodes == before


def test_long_native_source_views_stay_disjoint_and_original_text_is_unchanged():
    from document_files.document_model.observe import observe_document
    from document_files.interpretation.regions import prepare_regions

    text = "Detailed source content with Unicode 한글 and ordered prose. " * 350
    doc = observe_document(b"scripted native", "hwpx", {"n1": {"text": text}})
    before = copy.deepcopy(doc.nodes)
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) > 1 and all(r["withinContextBudget"] for r in regions)
    windows = [r["nodeViews"]["n1"] for r in regions]
    assert windows[0]["start"] == 0 and windows[-1]["end"] == len(text)
    assert all(a["end"] == b["start"] for a, b in zip(windows, windows[1:], strict=False))
    assert doc.nodes == before


def test_value_compilation_does_not_prune_a_frozen_field_that_shares_a_record_source():
    doc, region, roles, structure, old = prepared()
    raw = structure.model_dump(exclude_unset=True)
    raw["fields"].append(
        {
            "id": "first",
            "key": "first_item",
            "label": "First item",
            "valueType": "string",
            "definitionRefs": ["a"],
            "sourceRefs": ["a"],
            "status": "present",
        }
    )
    structure = native.NativeStructure.model_validate(raw)
    old["fields"].append({"id": "first", "sourceQuote": {"sourceRef": "a", "text": "Basil"}})
    result = compile_region(
        native.accept_values(choices(structure, old), structure, roles, doc, region), doc, region
    )
    assert result.data["first_item"] == result.data["items"][0]["name"] == "Basil"
    assert not result.dropped_fields
