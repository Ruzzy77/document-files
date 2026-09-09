"""Bounded source views: deterministic regression, not model-quality evidence."""

import copy
from collections import Counter

from document_files.document_model.model import ObservationDocument
from document_files.document_model.native import bind_spans
from document_files.interpretation.compiler import combine_regions, compile_region
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.semantic_types import RegionInterpretation


def document(texts, *, context=None):
    doc = ObservationDocument()
    for i, text in enumerate(texts):
        doc.node(f"n{i}", text, role="paragraph")
        bind_spans(doc, f"n{i}")
    doc.regions = [{"id": "native:1", "nodeIds": list(doc.nodes), "contextNodeIds": []}]
    if context is not None:
        doc.node("heading", context, role="heading")
        bind_spans(doc, "heading")
        doc.regions[0]["contextNodeIds"] = ["heading"]
    return doc


def assert_exact_partition(doc, regions):
    for ref in doc.regions[0]["nodeIds"]:
        windows = [r["nodeViews"][ref] for r in regions if ref in r["nodeIds"]]
        assert windows[0]["start"] == 0
        assert windows[-1]["end"] == len(doc.nodes[ref]["text"])
        assert all(a["end"] == b["start"] for a, b in zip(windows, windows[1:], strict=False))
        assert (
            "".join(doc.nodes[ref]["text"][w["start"] : w["end"]] for w in windows)
            == doc.nodes[ref]["text"]
        )


def interpretation(doc, region):
    fields = []
    for bid in region["requiredBindingIds"]:
        binding = doc.bindings[bid]
        label = doc.bindings[binding["labelRefs"][0]]
        # A scripted selector chooses issued handles; the compiler reads the
        # source values. No model computes or writes offsets or values.
        name = doc.nodes[label["sourceRef"]]["text"][label["start"] : label["end"]]
        fields.append(
            {
                "id": bid,
                "key": name,
                "label": name,
                "definitionRefs": [label["sourceRef"]],
                "bindingId": bid,
                "valueType": "string",
                "status": "blank" if binding.get("blank") else "present",
            }
        )
    return RegionInterpretation.model_validate({"regionId": region["id"], "fields": fields})


def test_one_giant_paragraph_keeps_exact_binding_ranges_and_single_value_ownership():
    text = "; ".join(f"항목{i}: {i}.00000000000000000001" for i in range(90)) + "; 빈칸: "
    doc = document([text])
    original = copy.deepcopy(doc.to_dict())
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) > 1 and all(r["withinContextBudget"] for r in regions)
    assert doc.nodes == original["nodes"] and doc.regions == original["regions"]
    assert all(doc.bindings[b] == value for b, value in original["bindings"].items())
    assert_exact_partition(doc, regions)
    expected = {
        b for b, value in original["bindings"].items() if value.get("candidateRole") == "value"
    }
    ownership = Counter(b for r in regions for b in r["requiredBindingIds"])
    assert set(ownership) == expected and set(ownership.values()) == {1}
    compiled = [compile_region(interpretation(doc, r), doc, r) for r in regions]
    projection = combine_regions(compiled)
    assert not projection["errors"] and not projection["issues"]
    assert projection["data"]["항목89"] == "89.00000000000000000001"
    assert projection["data"]["빈칸"] == ""
    assert len(projection["valueEvidence"]) == 91
    for region, item in zip(regions, compiled, strict=True):
        assert item.dispositions
        assert all(
            d["textRange"]["end"] <= region["nodeViews"][d["sourceRef"]]["end"]
            for d in item.dispositions
        )
        for evidence in item.value_evidence:
            binding = evidence["binding"]
            assert text[binding["start"] : binding["end"]] == evidence["raw"]


def test_many_nodes_in_one_region_are_bounded_with_neighbor_context_not_duplicate_values():
    doc = document(
        [f"field{i}: {i}" for i in range(150)],
        context="Annual report. Unit: kg. Applies only when approved.",
    )
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) > 1 and all(r["withinContextBudget"] for r in regions)
    assert_exact_partition(doc, regions)
    assert Counter(n for r in regions for n in r["nodeIds"]) == Counter(
        {f"n{i}": 1 for i in range(150)}
    )
    for region in regions:
        payload = region_payload(doc, region)
        assert payload["nodes"]["heading"]["text"] == doc.nodes["heading"]["text"]
        assert all(doc.bindings[b]["sourceRef"] in region["nodeIds"] for b in region["bindingIds"])
    assert any(set(r["contextNodeIds"]) - {"heading"} for r in regions)


def test_giant_prose_is_not_truncated_even_when_no_value_candidates_exist():
    doc = document(["This sentence has an explicit condition when approved. " * 600])
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) > 1 and all(r["withinContextBudget"] for r in regions)
    assert_exact_partition(doc, regions)
    for region in regions:
        payload = region_payload(doc, region)
        window = payload["nodes"]["n0"]["textRange"]
        assert (
            payload["nodes"]["n0"]["text"]
            == doc.nodes["n0"]["text"][window["start"] : window["end"]]
        )
        assert all(
            doc.bindings[b]["start"] >= window["start"] and doc.bindings[b]["end"] <= window["end"]
            for b in region["bindingIds"]
        )


def test_unbroken_value_and_label_are_atomic_and_explicitly_over_budget():
    doc = document(["identifier: " + "x" * 12000])
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) == 1
    assert not regions[0]["withinContextBudget"]
    assert regions[0]["budgetReason"] == "atomic_candidate_or_context_exceeds_budget"
    assert region_payload(doc, regions[0])["nodes"]["n0"]["text"] == doc.nodes["n0"]["text"]


def test_oversized_explicit_condition_is_not_silently_dropped_to_fit_values():
    doc = document([f"field{i}: {i}" for i in range(40)], context="A condition. " * 1200)
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) == 1 and not regions[0]["withinContextBudget"]
    assert regions[0]["budgetReason"] == "explicit_context_exceeds_budget"
    assert (
        region_payload(doc, regions[0])["nodes"]["heading"]["text"] == doc.nodes["heading"]["text"]
    )


def test_plan_and_added_binding_ids_are_deterministic_and_idempotent():
    text = "; ".join(f"field{i}: {i}" for i in range(140))
    a, b = document([text]), document([text])
    first = prepare_regions(a, context_chars=16000)
    second = prepare_regions(b, context_chars=16000)
    assert first == second and a.bindings == b.bindings
    assert prepare_regions(a, context_chars=16000) == first
    assert a.bindings == b.bindings


def test_view_disposition_does_not_mark_whole_original_node_as_accounted():
    doc = document(["Observation only. " * 400])
    regions = prepare_regions(doc, context_chars=16000)
    region = regions[1]
    ir = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "dispositions": [
                {"sourceRef": "n0", "role": "structural", "explanation": "Observed view only"}
            ],
        }
    )
    compiled = compile_region(ir, doc, region)
    assert compiled.dispositions[0]["textRange"] == {"path": "/text", **region["nodeViews"]["n0"]}
    assert compiled.dispositions[0]["textRange"]["end"] < len(doc.nodes["n0"]["text"])


def test_huge_native_scalar_metadata_stays_atomic_instead_of_being_repeated_per_view():
    doc = document(["word " * 10000])
    doc.nodes["n0"]["semantic"] = {"value": {"raw": "x" * 10000}}
    doc.bind("n0", path="/semantic/value/raw", candidateRole="native_value")
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) == 1 and not regions[0]["withinContextBudget"]


def test_explicit_cross_node_label_binding_is_retained_without_neighbor_values():
    doc = document([f"field{i}: {i}" for i in range(80)])
    label = doc.bind("n79", start=0, end=7, candidateRole="label")
    value = next(
        b
        for b, c in doc.bindings.items()
        if c.get("candidateRole") == "value" and c["sourceRef"] == "n0"
    )
    doc.bindings[value]["labelRefs"] = [label]
    regions = prepare_regions(doc, context_chars=16000)
    owner = next(r for r in regions if value in r["requiredBindingIds"])
    assert label in owner["bindingIds"]
    assert "n79" in owner["contextNodeIds"]
    assert not any(
        doc.bindings[b].get("candidateRole") == "value" and doc.bindings[b]["sourceRef"] == "n79"
        for b in owner["bindingIds"]
    )


def test_compiled_semantic_source_text_is_the_visible_view_not_full_native_node():
    doc = document(["A condition only for approved items. " * 300])
    region = prepare_regions(doc, context_chars=16000)[1]
    ir = RegionInterpretation.model_validate(
        {
            "regionId": region["id"],
            "meanings": [
                {
                    "id": "condition",
                    "kind": "condition",
                    "description": "Approval required",
                    "sourceRefs": ["n0"],
                }
            ],
            "dispositions": [
                {"sourceRef": "n0", "role": "note", "explanation": "Condition context"}
            ],
        }
    )
    compiled = compile_region(ir, doc, region)
    detail = compiled.semantic_details[0]
    window = region["nodeViews"]["n0"]
    assert detail["sourceText"] == [doc.nodes["n0"]["text"][window["start"] : window["end"]]]
    assert detail["sourceRanges"] == [{"sourceRef": "n0", "path": "/text", **window}]
    assert detail["interpretationStatus"] == "uncertain"
    assert any(i["code"] == "semantic_scope_unresolved" for i in compiled.issues)


def test_stream_checkpoint_resume_preserves_view_identity_and_exact_value_ownership():
    import io
    import json

    from document_files.analysis import AnalysisInput, AnalysisJob
    from document_files.interpretation.contracts import ExtractionOptions
    from document_files.interpretation.engine import extract_schema_from_stream

    class Selector:
        identity = {"adapter": "view-regression", "model": "scripted-not-quality"}

        def complete(self, messages, *, timeout):
            payload = json.loads(messages[-1]["content"])
            return json.dumps(
                {
                    "regionId": payload["regionId"],
                    "fields": [
                        {
                            "id": bid,
                            "key": bid,
                            "label": "Source field",
                            "definitionRefs": [payload["bindings"][bid]["sourceRef"]],
                            "bindingId": bid,
                            "valueType": "string",
                            "status": "blank"
                            if payload["bindings"][bid].get("blank")
                            else "present",
                        }
                        for bid in payload["requiredBindingIds"]
                    ],
                }
            )

    content = "; ".join(f"field{i}: {i}" for i in range(55)).encode()
    job = AnalysisJob(
        job_id="source-window-resume", input=AnalysisInput.from_bytes(content, format_id="txt")
    )
    options = ExtractionOptions(reconstructionContext=False, contextChars=16000, maxModelCalls=1)
    snapshots = []
    partial = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=Selector(),
        checkpoint=lambda value: snapshots.append(copy.deepcopy(value)),
    )
    assert partial["extraction"]["status"] == "partial"
    assert partial["coverage"]["unprocessedRegions"]
    assert partial["coverage"]["readNodes"] == 0
    resumed = extract_schema_from_stream(
        job,
        io.BytesIO(content),
        options=options,
        model_client=Selector(),
        restore=snapshots[-1],
        additional_budget={"maxModelCalls": 99},
    )
    assert resumed["extraction"]["status"] == "complete", resumed["issues"]
    assert len(resumed["data"]) == 55
    assert len(resumed["valueEvidence"]) == 55
    assert (
        len(
            {
                (e["binding"]["sourceRef"], e["binding"]["start"], e["binding"]["end"])
                for e in resumed["valueEvidence"]
            }
        )
        == 55
    )


def test_cross_node_label_outside_owned_view_cannot_be_treated_as_visible():
    doc = document(["1", "words " * 2000 + "Label"])
    text = doc.nodes["n1"]["text"]
    label = doc.bind("n1", start=len(text) - 5, end=len(text), candidateRole="label")
    value = doc.bind("n0", start=0, end=1, candidateRole="value", labelRefs=[label])
    regions = prepare_regions(doc, context_chars=16000)
    owner = next(r for r in regions if value in r["requiredBindingIds"])
    assert not owner["withinContextBudget"]
    assert "n1" in owner["contextNodeIds"]
    assert region_payload(doc, owner)["nodes"]["n1"]["text"] == text
    assert not any(r.get("unshownDefinition") for r in regions)


def test_long_pdf_text_splits_by_model_view_not_original_character_audit_size():
    text = "; ".join(f"item{i}: {i}" for i in range(100))
    doc = document([text])
    doc.nodes["n0"]["sourceStructure"] = {
        "page": 1,
        "characters": [{"text": character, "geometry": [1, 2, 3, 4]} for character in text],
    }
    original = copy.deepcopy(doc.nodes)
    regions = prepare_regions(doc, context_chars=16000)
    assert len(regions) > 1 and all(region["withinContextBudget"] for region in regions)
    assert_exact_partition(doc, regions)
    assert doc.nodes == original
