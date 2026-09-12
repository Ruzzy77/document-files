"""Oversized native reads retain every field and source; scripted, not AI quality."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator
from test_document_protocol import execute, raw_document
from test_native_structure import choices, prepared

from document_files.interpretation import native_structure as native
from document_files.interpretation import native_value_batches as batches
from document_files.interpretation.backends import InferenceResponse, ModelError
from document_files.interpretation.compiler import compile_region


class ManyValues:
    identity = {"test": "many-native-values"}

    def __init__(self, fail_batch=None):
        self.calls = []
        self.fail_batch = fail_batch
        self.limit = 16000

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        assert sum(len(m["content"]) for m in request.messages) <= self.limit
        self.calls.append(payload)
        stage = payload["documentStage"]
        if stage == "roles":
            value = {
                "regionId": payload["regionId"],
                "documentElements": [
                    {
                        "sourceRef": ref,
                        "role": "field_group",
                        "level": None,
                        "captionOf": None,
                        "status": "interpreted",
                    }
                    for ref in payload["documentContext"]["ownedSourceRefs"]
                ],
            }
        elif stage == "structure":
            ref = next(iter(payload["blocks"]))
            value = {
                "regionId": payload["regionId"],
                "fields": [
                    {
                        "key": f"reading{i:02d}",
                        "label": f"Reading {i:02d}",
                        "valueType": "integer",
                        "sourceRefs": [ref],
                        "status": "present",
                    }
                    for i in range(32)
                ],
            }
        elif stage == "values":
            if payload.get("batchId") == self.fail_batch:
                raise ModelError("ai_test_transport_failure")
            value = {
                "regionId": payload["regionId"],
                "selections": {
                    h: {
                        "kind": "quote",
                        "quote": {
                            "sourceRef": entry["sourceRefs"][0],
                            "text": str(1000 + int(entry["label"].split()[-1])),
                        },
                    }
                    for h, entry in payload["handles"].items()
                },
                "excludedBindings": [],
            }
        else:
            assert stage == "valueAccounting"
            value = {
                "regionId": payload["regionId"],
                "excludedBindings": [
                    {
                        "bindingId": b,
                        "role": "structural",
                        "explanation": "All labeled measurements read by exact inner quotes.",
                    }
                    for b in payload["requiredBindingIds"]
                ],
            }
        if "batchId" in payload:
            value["batchId"] = payload["batchId"]
        Draft202012Validator(request.output_schema).validate(value)
        return InferenceResponse(json.dumps(value), {"prompt_tokens": 2, "completion_tokens": 3})


def many_raw():
    return raw_document(("; ".join(f"Reading {i:02d}: {1000 + i}" for i in range(32)),))


def run(model, **kwargs):
    return execute(model, raw=many_raw(), contextChars=model.limit, **kwargs)


def test_oversized_hwpx_values_complete_without_dropping_sources_or_accounting():
    model, states = ManyValues(), []
    result = run(model, states=states, budget=12)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {f"reading{i:02d}": 1000 + i for i in range(32)}
    content = next(iter(states[-1]["documentStages"].values()))["content"]
    batch_state = content["batches"]
    assert len(batch_state["values"]) > 1 and batch_state["accounting"]
    assert content["usage"]["modelCalls"] == sum(
        r["usage"]["modelCalls"] for r in [*batch_state["values"], *batch_state["accounting"]]
    )
    readings = [p for p in model.calls if p["documentStage"] in {"values", "valueAccounting"}]
    assert all(
        p["nodes"] == readings[0]["nodes"] and p["bindings"] == readings[0]["bindings"]
        for p in readings
    )
    assert all(not p["requiredBindingIds"] for p in readings if p["documentStage"] == "values")
    prior_calls = len(model.calls)
    restored = run(model, restore=copy.deepcopy(states[-1]), budget=12)
    assert len(model.calls) == prior_calls
    for key in ("data", "dataSchema", "valueEvidence", "schemaEvidence"):
        assert restored[key] == result[key]


def test_record_batch_recombination_matches_unbatched_precision_blank_and_exact_evidence():
    doc, region, roles, structure, old = prepared()
    payload, schema = native.value_request(structure, roles, doc, region)
    original = copy.deepcopy((payload, schema, doc, region))
    wanted = choices(structure, old)
    state = {"values": [], "accounting": []}
    keys = list(payload["handles"])
    for group in [keys[:4], keys[4:8], keys[8:]]:
        request = batches.request_for(payload, schema, "values", group, state)
        value = {
            "regionId": region["id"],
            "batchId": request[1]["batchId"],
            "selections": {h: wanted["selections"][h] for h in group},
            "excludedBindings": [],
        }
        batches.accept(value, request, "values", group)
        state["values"].append({"response": value})
    _, ir, fragment = batches.compile_aggregate(payload, state, structure, roles, doc, region, None)
    single = compile_region(
        native.accept_values(wanted, structure, roles, doc, region), doc, region
    )
    assert fragment == single
    assert len(fragment.data["items"]) == 2 and fragment.data["items"][1]["received"] == ""
    assert fragment.data["items"][0]["length"] == "12.5000"
    assert ir.fields and (payload, schema, doc, region) == original


def test_indivisible_context_is_not_trimmed_or_sent_over_limit():
    doc, region, roles, structure, _ = prepared()
    payload, schema = native.value_request(structure, roles, doc, region)
    before = copy.deepcopy(payload)
    with pytest.raises(batches.BatchError, match="indivisible"):
        batches.initial(payload, schema, "source-hash", 100)
    assert payload == before


def test_explicit_budget_resume_preserves_read_values_and_never_replays_completed_batches():
    model, states = ManyValues(), []
    partial = run(model, states=states, budget=3)
    assert partial["extraction"]["status"] == "partial" and len(model.calls) == 3
    before = {k: v for k, v in partial["data"].items() if v is not None}
    assert before and len(before) < 32 and partial["coverage"]["unprocessedRegions"]
    saved = copy.deepcopy(states[-1])
    unchanged = run(model, restore=copy.deepcopy(saved), budget=3)
    assert unchanged["data"] == partial["data"] and len(model.calls) == 3
    completed = run(model, restore=saved, budget=3, grant={"maxModelCalls": 9})
    assert completed["extraction"]["status"] == "complete", completed["issues"]
    assert all(completed["data"][k] == v for k, v in before.items())
    assert len(model.calls) <= 12
    assert len([p for p in model.calls if p["documentStage"] == "roles"]) == 1
    assert len([p for p in model.calls if p["documentStage"] == "structure"]) == 1
    batches_called = [p["batchId"] for p in model.calls if "batchId" in p]
    assert len(batches_called) == len(set(batches_called))


def test_unknown_batch_exchange_requires_explicit_grant_and_keeps_previous_reads():
    model, states = ManyValues(), []
    partial = run(model, states=states, budget=3)
    saved = copy.deepcopy(states[-1])
    content = next(iter(saved["documentStages"].values()))["content"]
    from document_files.interpretation.document_protocol import digest

    model.fail_batch = digest([batches.VERSION, "values", content["batches"]["valueKeys"][1]])
    failed = run(model, restore=saved, states=states, budget=3, grant={"maxModelCalls": 9})
    assert failed["data"] == partial["data"] and failed["extraction"]["status"] == "partial"
    calls = len(model.calls)
    saved = copy.deepcopy(states[-1])
    model.fail_batch = None
    again = run(model, restore=copy.deepcopy(saved), budget=3)
    assert len(model.calls) == calls and again["data"] == failed["data"]
    recovered = run(model, restore=saved, budget=3, grant={"maxModelCalls": 1})
    assert recovered["extraction"]["status"] == "complete", recovered["issues"]
    assert len(model.calls) == recovered["extraction"]["usage"]["modelCalls"]


@pytest.mark.parametrize(
    "damage", ["plan", "request", "response", "aggregate", "usage", "accounting"]
)
def test_batch_checkpoint_revalidates_plan_requests_reads_accounting_and_costs(damage):
    from document_files.interpretation.document_protocol import digest

    model, states = ManyValues(), []
    run(model, states=states, budget=12)
    saved = copy.deepcopy(states[-1])
    content = next(iter(saved["documentStages"].values()))["content"]
    batch_state = content["batches"]
    if damage == "plan":
        batch_state["valueKeys"][0].pop()
    elif damage == "request":
        batch_state["values"][0]["requestHash"] = "0" * 64
    elif damage == "response":
        record = batch_state["values"][0]
        record["response"]["selections"]["@value1"]["quote"]["text"] = "foreign text"
        record["responseHash"] = digest(record["response"])
    elif damage == "aggregate":
        content["response"]["selections"]["@value1"] = {"kind": "unresolved"}
    elif damage == "usage":
        batch_state["values"][0]["usage"]["promptTokens"] += 1
    else:
        record = batch_state["accounting"][0]
        record["response"]["excludedBindings"][0]["bindingId"] = "foreign"
        record["responseHash"] = digest(record["response"])
    calls = len(model.calls)
    with pytest.raises(ValueError, match="incompatible"):
        run(model, restore=saved, budget=12)
    assert len(model.calls) == calls


def test_derived_read_template_roundtrips_all_definition_and_selection_details():
    reads = {
        "@value1": {
            "definition": {
                "fieldId": "first",
                "label": "Count",
                "sourceRefs": ["n1"],
                "status": "present",
                "valueType": "integer",
            },
            "selection": {
                "kind": "quote",
                "quote": {"sourceRef": "n1", "text": "8", "occurrence": 0},
            },
        },
        "@value2": {
            "definition": {
                "fieldId": "second",
                "label": "Count",
                "sourceRefs": ["n1"],
                "status": "present",
                "valueType": "integer",
            },
            "selection": {
                "kind": "quote",
                "quote": {"sourceRef": "n1", "text": "8", "occurrence": 1},
            },
        },
        "@value3": {
            "definition": {
                "recordId": "record",
                "rowId": "second",
                "sourceRefs": ["n2"],
                "label": "Empty",
                "status": "blank",
                "valueType": "string",
            },
            "selection": {"kind": "binding", "bindingId": "b2", "status": "blank"},
        },
    }
    before = copy.deepcopy(reads)
    factored = batches.factor_reads(reads)

    def merge(template, patch):
        value = copy.deepcopy(template)
        for k, v in patch.items():
            value[k] = merge(value.get(k, {}), v) if isinstance(v, dict) else v
        return value

    assert {h: merge(factored["template"], p) for h, p in factored["rows"]} == reads == before


def test_accounting_partition_never_offers_consumed_blank_and_rejects_foreign_exclusions():
    doc, region, roles, structure, old = prepared()
    payload, schema = native.value_request(structure, roles, doc, region)
    wanted = choices(structure, old)
    fragment = compile_region(
        native.accept_values(wanted, structure, roles, doc, region), doc, region
    )
    keys = batches.accounting_keys(fragment, payload)
    assert not set(keys).intersection(fragment.consumed_bindings)
    assert fragment.consumed_bindings
    request = batches.request_for(
        payload,
        schema,
        "accounting",
        keys[:1],
        {"values": [{"response": wanted}], "accounting": []},
    )
    value = {
        "regionId": region["id"],
        "batchId": request[1]["batchId"],
        "excludedBindings": [
            {
                "bindingId": next(iter(fragment.consumed_bindings)),
                "role": "structural",
                "explanation": "Invalid exclusion",
            }
        ],
    }
    with pytest.raises(batches.BatchError):
        batches.accept(value, request, "accounting", keys[:1])


def test_inflight_batch_checkpoint_cannot_be_replayed_without_explicit_grant():
    model, states = ManyValues(), []
    run(model, states=states, budget=3)
    inflight = next(
        copy.deepcopy(s)
        for s in states
        if any(
            r["status"] == "running"
            for d in s["documentStages"].values()
            for r in d["content"].get("batches", {}).get("values", [])
        )
    )
    calls = len(model.calls)
    stopped = run(model, restore=inflight, budget=3)
    assert len(model.calls) == calls and stopped["extraction"]["status"] == "partial"
    assert "document_content_response_unavailable" in str(stopped["issues"])


def test_completed_content_flag_cannot_hide_unread_batches():
    model, states = ManyValues(), []
    run(model, states=states, budget=3)
    saved = copy.deepcopy(states[-1])
    next(iter(saved["documentStages"].values()))["content"]["status"] = "complete"
    with pytest.raises(ValueError, match="incompatible"):
        run(model, restore=saved, budget=3)
    assert len(model.calls) == 3


def test_value_batch_repair_cannot_change_a_previously_read_value():
    class ChangesKnownRead(ManyValues):
        def infer(self, request):
            response = super().infer(request)
            p = self.calls[-1]
            if p["documentStage"] == "values":
                if not hasattr(self, "first_batch"):
                    self.first_batch = p["batchId"]
                    value = json.loads(response.text)
                    for h in list(value["selections"])[1:]:
                        value["selections"][h] = {"kind": "unresolved"}
                    return InferenceResponse(json.dumps(value), {})
                if p["batchId"] == self.first_batch:
                    value = json.loads(response.text)
                    value["selections"]["@value1"]["quote"]["text"] = "1001"
                    return InferenceResponse(json.dumps(value), {})
            return response

    model, states = ChangesKnownRead(), []
    result = run(model, states=states, budget=12)
    assert result["extraction"]["status"] == "partial"
    assert result["data"]["reading00"] == 1000 and result["data"]["reading01"] is None
    assert "native_value_batch_read_changed" in str(result["issues"])
    assert not any(p["documentStage"] == "valueAccounting" for p in model.calls)
    calls = len(model.calls)
    restored = run(model, restore=copy.deepcopy(states[-1]), budget=12)
    assert restored["data"] == result["data"] and len(model.calls) == calls


def test_all_values_are_not_completion_until_remaining_source_accounting_is_done():
    model, states = ManyValues(), []
    partial = run(model, states=states, budget=7)
    assert len(model.calls) == 7 and all(v is not None for v in partial["data"].values())
    assert partial["extraction"]["status"] == "partial"
    assert "value_candidate_unaccounted" in str(partial["issues"])
    before = copy.deepcopy(partial["valueEvidence"])
    completed = run(model, restore=copy.deepcopy(states[-1]), budget=7, grant={"maxModelCalls": 1})
    assert completed["extraction"]["status"] == "complete" and len(model.calls) == 8
    assert completed["valueEvidence"] == before
    assert len([p for p in model.calls if p["documentStage"] == "values"]) == 4
