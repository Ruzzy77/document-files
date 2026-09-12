"""Native logical-role contracts. Scripted decisions are not model quality approval."""

import copy
import io
import json
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from test_xlsx_candidates import PlainRegisterModel

from document_files.analysis import AnalysisInput, AnalysisJob
from document_files.document_model.observe import observe_document
from document_files.engine import create_hwpx
from document_files.interpretation.backends import InferenceResponse
from document_files.interpretation.bindings import resolve
from document_files.interpretation.compiler import compile_region
from document_files.interpretation.contracts import ExtractionOptions, SourceBinding
from document_files.interpretation.document_outline import (
    DocumentElement,
    compile_elements,
    project_outline,
    role_context,
)
from document_files.interpretation.engine import extract_schema_from_stream
from document_files.interpretation.regions import prepare_regions, region_payload
from document_files.interpretation.semantic_types import RegionInterpretation, region_output_schema


def make_file(tmp_path, *, table=True, blocks=None):
    plan = {
        "schemaVersion": "hwpx.document_plan.v1",
        "title": "Staff Register",
        "metadata": {},
        "blocks": blocks
        if blocks is not None
        else [
            {
                "type": "table",
                "caption": "Staff Register",
                "columns": [{"key": "name", "label": "Name"}, {"key": "state", "label": "State"}],
                "rows": [{"name": "Alice", "state": "active"}, {"name": "Bob", "state": "paused"}],
            }
        ]
        if table
        else [{"type": "paragraph", "text": "Ordinary prose, not a business field."}],
    }
    p = tmp_path / "outline.hwpx"
    create_hwpx(p, plan=plan)
    return p.read_bytes()


class OutlineModel(PlainRegisterModel):
    def __init__(self, *, bad=None):
        super().__init__()
        self.bad = bad
        self.outline_requests = []
        self.content_requests = []

    def infer(self, request):
        payload = json.loads(request.messages[-1]["content"])
        if payload.get("documentStage") == "roles":
            self.calls += 1
            self.outline_requests.append(payload)
            context = payload["documentContext"]
            elements = []
            for ref in context["ownedSourceRefs"]:
                text = payload["blocks"][ref]["text"]
                role, level, target = "paragraph", None, None
                if text == "Staff Register":
                    if context["sourceOrder"][ref] == 1:
                        role, level = "title", 0
                    else:
                        role, target = "caption", next(iter(context["captionCandidates"]))
                elif text.startswith("Section "):
                    role, level = "section_heading", 1
                elif text.startswith("Subsection "):
                    role, level = "section_heading", 2
                elif text.startswith("Count:"):
                    role = "field_group"
                elements.append(
                    {
                        "sourceRef": ref,
                        "role": role,
                        "level": level,
                        "captionOf": target,
                        "status": "interpreted",
                    }
                )
            value = {"regionId": payload["regionId"], "documentElements": elements}
            if self.bad == "missing":
                value.pop("documentElements")
            return InferenceResponse(json.dumps(value), {})
        if "documentContent" not in payload:
            return super().infer(request)
        self.calls += 1
        self.content_requests.append(payload)
        fields = []
        for bid, binding in payload["bindings"].items():
            ref = binding["sourceRef"]
            if (
                payload["nodes"][ref]["text"].startswith("Count:")
                and binding.get("candidateRole") == "value"
            ):
                fields.append(
                    {
                        "id": "count",
                        "key": "count",
                        "label": "Count",
                        "valueType": "string",
                        "definitionRefs": [ref],
                        "bindingId": bid,
                        "status": "present",
                    }
                )
        if self.bad == "conflict" and len(self.content_requests) == 1:
            title = payload["documentContent"]["roles"][0]["sourceRef"]
            bid = next(
                b
                for b, v in payload["bindings"].items()
                if v["sourceRef"] == title and v.get("candidateRole") == "content"
            )
            fields.append(
                {
                    "id": "wrong",
                    "key": "wrong",
                    "label": "Title",
                    "valueType": "string",
                    "definitionRefs": [title],
                    "bindingId": bid,
                    "status": "present",
                }
            )
        return InferenceResponse(
            json.dumps({"regionId": payload["regionId"], "fields": fields}), {}
        )


def run(raw, model, *, states=None, restore=None, **options):
    return extract_schema_from_stream(
        AnalysisJob(job_id="native-outline", input=AnalysisInput.from_bytes(raw, format_id="hwpx")),
        io.BytesIO(raw),
        model_client=model,
        options=ExtractionOptions(reconstructionContext=False, maxModelCalls=6, **options),
        checkpoint=states.append if states is not None else None,
        restore=restore,
    )


def test_actual_hwpx_preserves_equal_title_and_caption_as_distinct_roles(tmp_path):
    raw = make_file(tmp_path)
    model = OutlineModel()
    result = run(raw, model)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["validation"]["valid"]
    assert result["data"] == {
        "staff": [{"name": "Alice", "state": "active"}, {"name": "Bob", "state": "paused"}]
    }
    outline = result["document"]["outline"]
    title, caption, table = outline["elements"]
    assert [e["role"] for e in outline["elements"]] == ["title", "caption", "table"]
    assert title["sourceRef"] != caption["sourceRef"]
    assert caption["parentId"] == table["parentId"] == title["id"]
    assert outline["relations"][0]["sourceId"] == caption["id"]
    assert outline["relations"][0]["targetId"] == table["id"]
    for element in (title, caption):
        assert (
            resolve(
                SourceBinding.model_validate(element["textBinding"]), result["document"]["nodes"]
            )[0]
            == "Staff Register"
        )
        # The native default remains an ordinary paragraph; no observation rewriting.
        assert result["document"]["nodes"][element["sourceRef"]]["semanticRole"] == "paragraph"
    assert model.calls == 4
    assert all(
        e["binding"]["sourceRef"] not in {title["sourceRef"], caption["sourceRef"]}
        for e in result["valueEvidence"]
    )


def test_titles_prose_sections_and_real_field_are_not_forced_into_one_data_shape(tmp_path):
    raw = make_file(
        tmp_path,
        blocks=[
            {"type": "heading", "level": 1, "text": "Section A"},
            {"type": "paragraph", "text": "Ordinary prose."},
            {"type": "heading", "level": 2, "text": "Subsection B"},
            {"type": "paragraph", "text": "Nested prose."},
            {"type": "heading", "level": 1, "text": "Section A"},
            {"type": "paragraph", "text": "Count: 0007"},
        ],
    )
    result = run(raw, OutlineModel())
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["data"] == {"count": "0007"}
    elements = result["document"]["outline"]["elements"]
    assert [e["role"] for e in elements] == [
        "title",
        "section_heading",
        "paragraph",
        "section_heading",
        "paragraph",
        "section_heading",
        "field_group",
    ]
    assert [e["parentId"] for e in elements] == [
        None,
        elements[0]["id"],
        elements[1]["id"],
        elements[1]["id"],
        elements[3]["id"],
        elements[0]["id"],
        elements[5]["id"],
    ]
    assert elements[1]["sourceRef"] != elements[5]["sourceRef"]


def test_document_with_no_business_values_can_complete_without_inventing_fields(tmp_path):
    result = run(make_file(tmp_path, table=False), OutlineModel())
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert result["validation"]["valid"], result["validation"]
    assert result["data"] == {} and result["valueEvidence"] == []
    assert [e["role"] for e in result["document"]["outline"]["elements"]] == ["title", "paragraph"]


def test_conflicting_title_value_is_rejected_then_repaired_without_silent_field_deletion(tmp_path):
    model = OutlineModel(bad="conflict")
    result = run(make_file(tmp_path), model)
    assert result["extraction"]["status"] == "complete", result["issues"]
    assert "wrong" not in result["data"]
    assert "document_role_value_conflict" in json.dumps(model.content_requests[1])
    assert model.calls == 5


def test_omitted_roles_remain_partial_even_when_values_are_extracted(tmp_path):
    result = run(make_file(tmp_path), OutlineModel(bad="missing"))
    assert result["extraction"]["status"] == "partial"
    assert "document_outline_unreviewed" in json.dumps(result["issues"])
    assert len(result["data"]["staff"]) == 2


def unit(text="Staff Register", *, count=1):
    nodes = {
        f"n{i}": {
            "ordinal": i,
            "text": text,
            "semanticRole": "paragraph",
            "sourceStructure": {"level": 1, "head_type": "none"},
        }
        for i in range(1, count + 1)
    }
    doc = observe_document(b"scripted native document", "hwpx", nodes)
    region = prepare_regions(doc, context_chars=16000)[0]
    return doc, region


def decision(ref="n1", **changes):
    return DocumentElement(
        sourceRef=ref, role="title", level=0, captionOf=None, status="interpreted"
    ).model_copy(update=changes)


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"sourceRef": "unknown"}, "sources"),
        ({"level": 1}, "level"),
        ({"role": "paragraph", "level": 0}, "level"),
        ({"role": "section_heading", "level": None}, "level"),
        ({"role": "caption", "level": None}, "target_required"),
        ({"captionOf": "unknown"}, "noncaption"),
        ({"role": "caption", "level": None, "captionOf": "unknown"}, "not_offered"),
    ],
)
def test_role_contract_references_and_levels_are_validated_without_a_decoder(changes, code):
    doc, region = unit()
    with pytest.raises(ValueError, match=code):
        compile_elements([decision(**changes)], doc, region, [])


def test_duplicate_owned_ref_cannot_cover_another_source():
    doc, region = unit(count=2)
    with pytest.raises(ValueError, match="duplicated"):
        compile_elements([decision(), decision()], doc, region, [])


def test_role_does_not_account_for_unread_inner_value_candidates():
    doc, region = unit("Count: 0007")
    candidate = RegionInterpretation(regionId=region["id"], documentElements=[decision()])
    fragment = compile_region(candidate, doc, region)
    assert any(i["code"] == "value_candidate_unaccounted" for i in fragment.issues)
    assert fragment.data == {}


def test_native_default_level_never_becomes_an_interpreted_heading_by_itself():
    doc, _ = unit(count=2)
    outline, issues = project_outline(doc, [])
    assert outline["status"] == "partial" and issues
    assert all(e["role"] == "unresolved" for e in outline["elements"])


def test_uncertain_heading_does_not_silently_assign_following_content_to_old_section():
    doc, region = unit(count=3)
    elements = compile_elements(
        [
            decision(),
            decision("n2", role="section_heading", level=1, status="uncertain"),
            decision("n3", role="paragraph", level=None),
        ],
        doc,
        region,
        [],
    )
    outline, issues = project_outline(doc, [SimpleNamespace(document_elements=elements)])
    assert issues and outline["elements"][-1]["parentId"] is None
    assert outline["elements"][-1]["parentStatus"] == "unresolved"


def test_partial_view_does_not_claim_the_entire_paragraph_has_one_interpreted_role():
    doc, region = unit("A long paragraph")
    region["nodeViews"] = {"n1": {"start": 0, "end": 4}}
    elements = compile_elements([decision(role="paragraph", level=None)], doc, region, [])
    outline, issues = project_outline(doc, [SimpleNamespace(document_elements=elements)])
    assert any(i["code"] == "document_outline_fragments_unjoined" for i in issues)
    assert outline["elements"][0]["textBinding"]["end"] == 4


def test_model_contract_requires_each_owned_role_not_context_roles():
    doc, region = unit(count=2)
    schema = region_output_schema(doc, region)
    payload = region_payload(doc, region)
    assert payload["documentContext"] == role_context(doc, region)
    good = {
        "regionId": region["id"],
        "documentElements": [decision().model_dump(), decision("n2").model_dump()],
    }
    validator = Draft202012Validator(schema)
    assert validator.is_valid(good)
    assert not validator.is_valid({"regionId": region["id"]})
    assert not validator.is_valid({**good, "documentElements": good["documentElements"][:1]})


def test_checkpoint_rebuilds_outline_and_rejects_old_policies_or_invalid_role_refs(tmp_path):
    raw = make_file(tmp_path)
    states = []
    model = OutlineModel()
    result = run(raw, model, states=states)
    checkpoint = copy.deepcopy(states[-1])
    checkpoint["result"]["document"]["outline"]["elements"][0]["role"] = "invented"
    restored = run(raw, model, restore=checkpoint)
    assert restored["document"]["outline"] == result["document"]["outline"]
    assert model.calls == 4
    for key, old in [
        ("compilerVersion", "document-files.result-compiler.v28"),
        ("promptVersion", "document-files.semantic-prompts.v33"),
        ("regionPlanVersion", "document-files.region-plan.v18"),
    ]:
        checkpoint = copy.deepcopy(states[-1])
        checkpoint["identity"][key] = old
        with pytest.raises(ValueError, match="incompatible"):
            run(raw, model, restore=checkpoint)
    checkpoint = copy.deepcopy(states[-1])
    accepted = next(v for v in checkpoint["accepted"].values() if v["documentElements"])
    accepted["documentElements"][0]["sourceRef"] = "unknown"
    with pytest.raises(ValueError, match="incompatible"):
        run(raw, model, restore=checkpoint)


def test_document_only_does_not_bypass_the_callers_target_schema(tmp_path):
    result = run(
        make_file(tmp_path, table=False),
        OutlineModel(),
        targetSchema={
            "type": "object",
            "properties": {"required_value": {"type": "string"}},
            "required": ["required_value"],
            "additionalProperties": False,
        },
    )
    assert result["extraction"]["status"] == "partial"
    assert not result["validation"]["valid"]


def test_preceding_headings_are_bounded_context_not_new_owned_sources():
    from document_files.interpretation.document_outline import preceding_headings

    doc, region = unit(count=3)
    first = {**region, "nodeIds": ["n1", "n2"]}
    elements = compile_elements(
        [decision(), decision("n2", role="section_heading", level=1)], doc, first, []
    )
    later = {**region, "nodeIds": ["n3"]}
    context = preceding_headings(doc, later, [SimpleNamespace(document_elements=elements)])
    assert [(e["sourceRef"], e["level"]) for e in context] == [("n1", 0), ("n2", 1)]
    assert role_context(doc, later)["ownedSourceRefs"] == ["n3"]
    with pytest.raises(ValueError, match="sources"):
        compile_elements([decision()], doc, later, [])


def test_native_caption_inner_candidates_remain_available_for_explicit_accounting():
    doc, _ = unit("Units: mm")
    doc.nodes["n1"]["semanticRole"] = "caption"
    region = prepare_regions(doc, context_chars=16000)[0]
    values = [b for b, v in doc.bindings.items() if v.get("candidateRole") == "value"]
    assert values and set(values) <= set(region["bindingIds"])
    assert set(values) <= set(region["requiredBindingIds"])


def test_clear_subheading_does_not_resolve_an_uncertain_parent_heading():
    doc, region = unit(count=5)
    elements = compile_elements(
        [
            decision(),
            decision("n2", role="section_heading", level=1, status="uncertain"),
            decision("n3", role="section_heading", level=2),
            decision("n4", role="paragraph", level=None),
            decision("n5", role="section_heading", level=1),
        ],
        doc,
        region,
        [],
    )
    outline, issues = project_outline(doc, [SimpleNamespace(document_elements=elements)])
    assert issues
    assert all(e["parentStatus"] == "unresolved" for e in outline["elements"][1:4])
    assert outline["elements"][4]["parentId"] == outline["elements"][0]["id"]
    assert outline["elements"][4]["parentStatus"] == "resolved"


@pytest.mark.parametrize(
    ("role", "level", "caption", "status", "valid"),
    [
        ("title", 0, None, "interpreted", True),
        ("title", None, None, "interpreted", False),
        ("section_heading", 1, None, "interpreted", True),
        ("section_heading", 0, None, "interpreted", False),
        ("paragraph", None, None, "interpreted", True),
        ("paragraph", 1, None, "interpreted", False),
        ("caption", None, None, "interpreted", False),
        ("caption", None, None, "uncertain", True),
        ("paragraph", None, "unknown", "interpreted", False),
    ],
)
def test_local_wire_grammar_rejects_invalid_role_level_pairs(role, level, caption, status, valid):
    doc, region = unit()
    schema = region_output_schema(doc, region)
    Draft202012Validator.check_schema(schema)
    value = {
        "regionId": region["id"],
        "documentElements": [
            {
                "sourceRef": "n1",
                "role": role,
                "level": level,
                "captionOf": caption,
                "status": status,
            }
        ],
    }
    assert Draft202012Validator(schema).is_valid(value) is valid


def test_model_role_context_distinguishes_native_container_from_logical_role():
    doc, region = unit()
    before = copy.deepcopy(doc.nodes)
    payload = region_payload(doc, region)
    assert "semanticRole" not in payload["nodes"]["n1"]
    assert payload["nodes"]["n1"]["nativeRole"] == "paragraph"
    assert doc.nodes == before
