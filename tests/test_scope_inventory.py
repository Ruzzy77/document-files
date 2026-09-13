"""Full eligible inventory and honest preflight; not real-model quality approval."""

import copy
import io
import json

import pytest
from test_scope_bound_provenance import LongScopeModel
from test_scope_context_wire import long_table

from document_files.api import (
    AnalysisInput,
    AnalysisJob,
    ExtractionOptions,
    extract_schema_from_stream,
)
from document_files.interpretation import scope_inventory
from document_files.interpretation.compiler import compile_region
from document_files.interpretation.integration import build_scope_inventory, build_scope_tasks
from document_files.interpretation.legacy_engine import contract_messages
from document_files.interpretation.scope_context_wire import expand_scope_context
from document_files.interpretation.scope_protocol import SYSTEM, scope_readiness
from document_files.interpretation.scope_selection_wire import prepare_scope_selection_wire
from document_files.interpretation.scope_values import ScalarOriginCatalog


@pytest.mark.parametrize("count", [10, 50, 96, 200])
def test_all_eligible_rows_and_exact_context_survive_before_request_sizing(count):
    doc, region, ir = long_table(count)
    compiled = compile_region(ir, doc, region)
    before = copy.deepcopy((doc, compiled, region))
    task = build_scope_inventory(doc, [region], [compiled])[0]
    inventory = task.payload["inventory"]
    assert task.complete_candidates and inventory["status"] == "complete"
    assert inventory["candidateCount"] == inventory["retainedCandidates"] == 3
    assert inventory["omittedCandidates"] == inventory["incompleteContexts"] == 0
    assert inventory["contentBytes"] <= scope_inventory.MAX_BYTES
    record = next(c for c in task.payload["candidates"] if "rowOptions" in c)
    assert [r["row"] for r in record["rowOptions"]["rows"]] == list(range(count + 1))
    assert {r["sourceRef"]: r["text"] for r in record["context"]} == {
        f"c{r}:{c}": doc.nodes[f"c{r}:{c}"]["text"] for r in range(count + 1) for c in (0, 1)
    }
    wire = prepare_scope_selection_wire([task])
    assert "inventory" not in wire.payload  # Operational diagnostics are not model context.
    assert len(expand_scope_context(wire.payload)["rowBoundaryCandidates"]) == count + 1
    size = sum(len(m["content"]) for m in contract_messages(SYSTEM, wire.payload, wire.contract))
    readiness = scope_readiness(task, input_chars=16000)
    assert readiness == {
        "status": "ready" if count <= 50 else "requires_partition",
        "requestChars": size,
        "inputLimitChars": 16000,
    }
    assert (doc, compiled, region) == before
    if count >= 96:
        old = build_scope_tasks(doc, [region], [compiled], context_chars=12000)[0]
        assert not old.complete_candidates and all(
            "rowOptions" not in c for c in old.payload["candidates"]
        )


def long_value(text):
    doc, region, ir = long_table(10)
    doc.nodes["c3:0"]["text"] = text
    for binding in doc.bindings.values():
        if binding["sourceRef"] == "c3:0":
            binding["end"] = len(text)
    compiled = compile_region(ir, doc, region)
    return doc, region, compiled


def test_inventory_does_not_reuse_the_legacy_24_candidate_display_limit():
    doc, region, ir = long_table(3)
    for column in range(2, 30):
        header = f"c0:{column}"
        for row in range(4):
            ref = f"c{row}:{column}"
            text = f"Field {column}" if row == 0 else f"{row}.000"
            doc.node(ref, text)
            if row:
                doc.bind(ref, start=0, end=len(text), candidateRole="content")
            doc.tables["t"]["cells"].append(
                {
                    "sourceRef": ref,
                    "row": row,
                    "col": column,
                    "rowSpan": 1,
                    "colSpan": 1,
                    "isHeader": row == 0,
                }
            )
            ir.repeats[0].rowRoles[row].sourceRefs.append(ref)
        ir.repeats[0].columns.append(
            type(ir.repeats[0].columns[0])(
                id=f"field{column}",
                key=f"field{column}",
                label=f"Field {column}",
                valueType="string",
                column=column,
                definitionRefs=[header],
            )
        )
    doc.tables["t"]["colCount"] = 30
    region.update(nodeIds=list(doc.nodes), bindingIds=list(doc.bindings))
    ir.dispositions = []
    compiled = compile_region(ir, doc, region)
    original = copy.deepcopy((doc, compiled))
    task = build_scope_inventory(doc, [region], [compiled])[0]
    assert task.complete_candidates and task.payload["inventory"]["candidateCount"] == 31
    assert len(task.target_map) == 31
    limited = build_scope_tasks(doc, [region], [compiled], context_chars=1000000)[0]
    assert len(limited.target_map) == 24 and not limited.complete_candidates
    assert (doc, compiled) == original


def test_source_text_is_not_cut_at_500_and_tail_changes_invalidate_identity():
    text = "원문 recordBlocks @column0 " * 30
    doc, region, compiled = long_value(text + "꼬리 하나")
    task = build_scope_inventory(doc, [region], [compiled])[0]
    assert task.complete_candidates
    record = next(c for c in task.payload["candidates"] if "rowOptions" in c)
    item = next(c for c in record["context"] if c["sourceRef"] == "c3:0")
    assert item["text"] == text + "꼬리 하나" and not item["truncated"]
    other_doc, other_region, other_compiled = long_value(text + "꼬리 둘")
    other = build_scope_inventory(other_doc, [other_region], [other_compiled])[0]
    assert other.fingerprint != task.fingerprint
    assert task.target_map.keys() == other.target_map.keys()
    original = build_scope_tasks(doc, [region], [compiled])[0]
    assert not original.complete_candidates


def test_scalar_value_origin_can_retain_the_full_literal_without_mutating_sources():
    doc, region, compiled = long_value("원문" * 300)
    evidence = [
        {
            "target": {"space": "data", "path": "/rows/2/name"},
            "status": "present",
            "raw": "원문" * 300,
            "binding": {"sourceRef": "c3:0", "path": "/text"},
        }
    ]
    before = copy.deepcopy((doc, evidence))
    catalog = ScalarOriginCatalog(doc, [compiled])
    limited, _, complete = catalog.describe(evidence)
    assert not complete and limited[0]["valueTextTruncated"]
    full, refs, complete = catalog.describe(evidence, text_chars=None)
    assert complete and refs == ["c3:0"]
    assert full[0]["valueText"] == evidence[0]["raw"] and not full[0]["valueTextTruncated"]
    assert (doc, evidence) == before


@pytest.mark.parametrize("limit", ["candidate", "bytes", "base"])
def test_resource_boundaries_never_authorize_the_retained_candidate_prefix(monkeypatch, limit):
    doc, region, ir = long_table(96)
    compiled = compile_region(ir, doc, region)
    before = copy.deepcopy((doc, compiled))
    if limit == "candidate":
        monkeypatch.setattr(scope_inventory, "MAX_CANDIDATES", 2)
    else:
        monkeypatch.setattr(scope_inventory, "MAX_BYTES", 1024 if limit == "base" else 16000)
    task = build_scope_inventory(doc, [region], [compiled])[0]
    assert not task.complete_candidates and task.payload["candidateCoverage"] == "bounded"
    assert task.payload["inventory"]["status"] == "resource_limited"
    assert scope_readiness(task, input_chars=1000000) == {
        "status": "inventory_unavailable",
        "reason": "resource_limited",
    }
    if limit == "base":
        assert task.payload["inventory"]["candidateCount"] is None
        assert task.target_map == {}
    else:
        assert task.payload["inventory"]["omittedCandidates"] > 0
    assert (doc, compiled) == before


def test_missing_original_context_stays_unavailable_even_with_more_bytes():
    doc, region, ir = long_table(10)
    compiled = compile_region(ir, doc, region)
    del doc.nodes["c3:0"]
    task = build_scope_inventory(doc, [region], [compiled])[0]
    assert task.payload["inventory"]["status"] == "incomplete_context"
    assert not task.complete_candidates
    assert scope_readiness(task, input_chars=1000000)["status"] == "inventory_unavailable"


@pytest.mark.parametrize("error", [ValueError, KeyError])
def test_preflight_retains_data_when_the_candidate_contract_is_invalid(monkeypatch, error):
    doc, region, ir = long_table(10)
    compiled = compile_region(ir, doc, region)
    task = build_scope_inventory(doc, [region], [compiled])[0]
    before = copy.deepcopy((compiled, task))

    def invalid(*args):
        raise error("PRIVATE SOURCE TEXT MUST NOT BE REPORTED")

    monkeypatch.setattr(
        "document_files.interpretation.scope_protocol.prepare_scope_selection_wire", invalid
    )
    assert scope_readiness(task, input_chars=16000) == {
        "status": "invalid_catalog",
        "reason": "candidate_validation_failed",
    }
    assert (compiled, task) == before


def test_empty_eligibility_is_visible_but_is_not_absence_of_document_values():
    doc, region, ir = long_table(10)
    compiled = compile_region(ir, doc, region)
    for definition in compiled.semantics:
        if definition["kind"] == "field_definition":
            definition["status"] = "uncertain"
    task = build_scope_inventory(doc, [region], [compiled])[0]
    assert task.payload["inventory"]["status"] == "no_candidates"
    assert not task.complete_candidates and len(compiled.data["rows"]) == 10
    assert scope_readiness(task, input_chars=16000) == {
        "status": "inventory_unavailable",
        "reason": "no_candidates",
    }


def test_changed_omitted_context_is_still_part_of_the_inventory_fingerprint(monkeypatch):
    monkeypatch.setattr(scope_inventory, "MAX_BYTES", 16000)
    doc, region, ir = long_table(96)
    compiled = compile_region(ir, doc, region)
    first = build_scope_inventory(doc, [region], [compiled])[0]
    assert not any("rowScope" in c for c in first.target_map.values())
    doc.nodes["c96:0"]["text"] += " changed beyond retained definitions"
    second = build_scope_inventory(doc, [region], [compiled])[0]
    assert first.target_map == second.target_map
    assert first.fingerprint != second.fingerprint


def test_engine_does_not_call_a_model_on_a_resource_limited_inventory_or_trust_saved_coverage(
    monkeypatch,
):
    monkeypatch.setattr(scope_inventory, "MAX_CANDIDATES", 2)
    content = (
        b"<p>Lengths use mm.</p><table><tr><th>Length</th><th>Width</th></tr>"
        b"<tr><td>01.00</td><td></td></tr></table>"
    )
    job = AnalysisJob(job_id="inventory", input=AnalysisInput.from_bytes(content, format_id="html"))
    options = ExtractionOptions(reconstructionContext=False)

    class NoScopePrefix(LongScopeModel):
        def complete(self, messages, **kwargs):
            assert "taskId" not in json.loads(messages[-1]["content"])
            return super().complete(messages, **kwargs)

    model, states = NoScopePrefix(), []
    result = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, checkpoint=states.append
    )
    assert result["extraction"]["status"] == "partial" and result["data"]["rows"]
    item = result["coverage"]["scopeIntegration"][0]
    assert item["inventory"]["status"] == "resource_limited"
    assert item["requestPlanning"]["status"] == "inventory_unavailable"
    assert any(i["code"] == "scope_inventory_unavailable" for i in result["issues"])
    assert states[-1]["scopeDecisions"] == {}
    checkpoint, calls = copy.deepcopy(states[-1]), model.calls
    checkpoint["result"]["coverage"]["scopeIntegration"][0]["status"] = "interpreted"
    restored = extract_schema_from_stream(
        job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
    )
    assert (
        model.calls == calls
        and restored["coverage"]["scopeIntegration"] == result["coverage"]["scopeIntegration"]
    )
    assert restored["data"] == result["data"]
    checkpoint["identity"]["scopeProtocol"]["inventory"]["maxCandidates"] += 1
    with pytest.raises(ValueError, match="incompatible"):
        extract_schema_from_stream(
            job, io.BytesIO(content), options=options, model_client=model, restore=checkpoint
        )
    assert model.calls == calls
