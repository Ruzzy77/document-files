"""Engine protocol boundaries; deterministic fixtures do not qualify model quality."""

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_cross_region_integration import fixture

from document_files.interpretation.backends import ManagedPackClient
from document_files.interpretation.compiler import CompileError
from document_files.interpretation.integration import build_scope_tasks
from document_files.interpretation.legacy_engine import contract_messages
from document_files.interpretation.scope_protocol import (
    SYSTEM,
    replay_scope_record,
    scope_axis_batches,
    scope_policy,
    scope_request_identity,
)
from document_files.interpretation.scope_selection_wire import prepare_scope_selection_wire
from document_files.interpretation.scope_source_binding import bind_scope_choices


def independent_tasks(count):
    obs, regions, compiled = fixture()
    base = build_scope_tasks(obs, regions, compiled)[0]
    result = []
    for i in range(count):
        payload, signature = copy.deepcopy(base.payload), copy.deepcopy(base.source_signature)
        payload["taskId"] = f"task{i}"
        signature["sourceRefs"] = payload["statement"]["sourceRefs"] = [f"source{i}"]
        result.append(
            replace(
                base,
                id=f"task{i}",
                fingerprint=f"fp{i}",
                payload=payload,
                source_signature=signature,
            )
        )
    return result


def message_size(tasks):
    wire = prepare_scope_selection_wire(tasks)
    return sum(len(m["content"]) for m in contract_messages(SYSTEM, wire.payload, wire.contract))


def test_batches_use_exact_new_contract_size_not_the_old_schema():
    tasks = independent_tasks(3)
    limit = message_size(tasks[:2])
    assert [len(b) for b in scope_axis_batches(tasks, context_chars=limit)] == [2, 1]
    assert [len(b) for b in scope_axis_batches(tasks, context_chars=limit - 1)] == [1, 1, 1]
    assert [len(b) for b in scope_axis_batches(tasks, context_chars=1)] == [1, 1, 1]
    assert [len(b) for b in scope_axis_batches(independent_tasks(10), context_chars=1000000)] == [
        8,
        2,
    ]
    obs, regions, compiled = fixture()
    same_caption = build_scope_tasks(obs, regions, compiled)
    assert list(scope_axis_batches(same_caption, context_chars=1000000)) == [
        [t] for t in same_caption
    ]


def test_policy_does_not_guess_managed_capabilities_or_change_client_defaults():
    generic = SimpleNamespace(infer=lambda r: None, max_output_tokens=4096)
    assert scope_policy(generic)["reasoningBudgetTokens"] is None
    assert scope_policy(generic)["maxOutputTokens"] == 3072
    smaller = SimpleNamespace(infer=lambda r: None, max_output_tokens=1536)
    assert scope_policy(smaller)["maxOutputTokens"] == 1536
    assert scope_policy(SimpleNamespace())["outputLimitOwner"] == "client"
    assert scope_policy(SimpleNamespace())["maxOutputTokens"] is None
    managed = object.__new__(ManagedPackClient)
    managed.max_output_tokens = 3072
    managed.reasoning_budget_tokens = None
    policy = scope_policy(managed)
    assert policy["version"] == "document-files.scope-axis-protocol.v6"
    # Bounded thinking for applicability: 512 truncated the model's reasoning on the
    # merged-header unit case and 1,024 left the raster-page unit unresolved on the
    # mixed PDF (development probes); 2,048 must stay below the managed output cap.
    assert policy["reasoningBudgetTokens"] == 2048 and policy["maxOutputTokens"] == 3072
    assert policy["reasoningBudgetTokens"] < policy["maxOutputTokens"]
    assert managed.reasoning_budget_tokens is None


@pytest.mark.parametrize("mutation", ["none", "sibling", "binding", "task_order", "task_count"])
def test_saved_batch_rebinds_before_reuse_and_checks_full_context(mutation):
    obs, regions, compiled = fixture()
    tasks = build_scope_tasks(obs, regions, compiled)
    wire = prepare_scope_selection_wire(tasks)
    policy = scope_policy(SimpleNamespace())
    raw = {
        "decisions": [
            {
                "taskId": t.id,
                "decision": "apply",
                "selections": [
                    {"kind": "standalone", "targetHandle": wire.base.standalone[t.id][0]}
                ],
                "explanation": "Scripted scope",
            }
            for t in tasks
        ]
    }
    decoded = wire.decode(raw)
    bound, traces = bind_scope_choices(decoded, tasks, compiled)
    stored = {
        "fingerprint": tasks[0].fingerprint,
        "selection": decoded["decisions"][0],
        "decision": bound["decisions"][0],
        "sourceBinding": traces[tasks[0].id],
        "request": scope_request_identity(tasks, wire, policy),
    }
    if mutation == "sibling":
        tasks[1] = replace(tasks[1], fingerprint="changed")
    elif mutation == "binding":
        stored["sourceBinding"]["bindings"][0]["sourceRefs"] = ["forged"]
    elif mutation == "task_order":
        stored["request"]["tasks"].reverse()
    elif mutation == "task_count":
        stored["request"]["tasks"] = stored["request"]["tasks"] * 5
    if mutation == "none":
        assert (
            replay_scope_record(stored, tasks[0], tasks, compiled, policy).model_dump()
            == stored["decision"]
        )
    else:
        with pytest.raises(CompileError, match="scope_record_incompatible"):
            replay_scope_record(stored, tasks[0], tasks, compiled, policy)
