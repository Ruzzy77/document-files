"""Repeated tables and statements across pages: continue, duplicate, separate."""

import pytest

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.compiler import (
    combine_regions,
    compile_region,
    join_continuations,
)
from document_files.interpretation.engine import (
    integration_candidate,
    integration_contract,
    integration_request,
)
from document_files.interpretation.integration import SCOPE_VERSION, build_scope_tasks
from document_files.interpretation.legacy_engine import encode
from document_files.interpretation.regions import continuation_candidates
from document_files.interpretation.semantic_prompts import INTEGRATE, PROMPT_VERSION
from document_files.interpretation.semantic_types import (
    COMPILER_VERSION,
    ContinuationDecision,
    DocumentIntegration,
    RegionInterpretation,
)

ROWS = (("Name", "Amount"), ("A", "1"), ("B", "2"), ("C", "3"))


def _page(
    doc,
    prefix,
    page,
    rows,
    unit,
    *,
    key,
    extra_statement=False,
    repeat_key="rows",
    column_keys=("name", "amount"),
    amount_type="string",
):
    statement = doc.node(f"{prefix}stmt", f"Unit: {unit}", role="text", locator={"page": page})
    extra = []
    if extra_statement:
        # A second interpreted member with the same wording makes the text ambiguous.
        extra = [doc.node(f"{prefix}stmt2", f"Unit: {unit}", role="text", locator={"page": page})]
    unit_binding = doc.bind(statement, start=6, end=6 + len(unit), candidateRole="content")
    cells = []
    for row, values in enumerate(rows):
        for col, value in enumerate(values):
            ref = doc.node(
                f"{prefix}c{row}:{col}", value, role="table_cell", locator={"page": page}
            )
            doc.bind(ref, start=0, end=len(value), candidateRole="content")
            cells.append(
                {
                    "sourceRef": ref,
                    "row": row,
                    "col": col,
                    "rowSpan": 1,
                    "colSpan": 1,
                    "isHeader": row == 0,
                }
            )
    doc.tables[f"{prefix}t"] = {
        "id": f"{prefix}t",
        "basis": "native_structure",
        "page": page,
        "rowCount": len(rows),
        "colCount": 2,
        "cells": cells,
    }
    text_region = {
        "id": f"{prefix}text",
        "nodeIds": [statement, *extra],
        "bindingIds": [unit_binding],
        "contextNodeIds": [],
    }
    table_nodes = [c["sourceRef"] for c in cells]
    table_region = {
        "id": f"{prefix}table",
        "nodeIds": table_nodes,
        "bindingIds": [b for b, item in doc.bindings.items() if item["sourceRef"] in table_nodes],
        "contextNodeIds": [statement],
        "tableRef": f"{prefix}t",
    }
    text_ir = RegionInterpretation.model_validate(
        {
            "regionId": text_region["id"],
            "fields": [
                {
                    "id": "unit",
                    "key": key,
                    "label": "Unit",
                    "definitionRefs": [statement],
                    "bindingId": unit_binding,
                    "valueType": "string",
                }
            ],
            "meanings": [
                {
                    "id": "u",
                    "kind": "unit",
                    "description": "Declared unit",
                    "sourceRefs": [statement],
                    "fieldIds": ["unit"],
                }
            ],
            "dispositions": [
                {"sourceRef": ref, "role": "data", "explanation": "Unit statement"}
                for ref in [statement, *extra]
            ],
        }
    )
    table_ir = RegionInterpretation.model_validate(
        {
            "regionId": table_region["id"],
            "repeats": [
                {
                    "id": "rows",
                    "key": repeat_key,
                    "label": "Rows",
                    "tableRef": f"{prefix}t",
                    "rowStart": 0,
                    "rowEnd": len(rows) - 1,
                    "definitionRefs": [f"{prefix}c0:0"],
                    "rowRoles": [
                        {
                            "row": row,
                            "role": "header" if row == 0 else "data",
                            "sourceRefs": [f"{prefix}c{row}:0", f"{prefix}c{row}:1"],
                        }
                        for row in range(len(rows))
                    ],
                    "columns": [
                        {
                            "id": "name",
                            "key": column_keys[0],
                            "label": "Name",
                            "column": 0,
                            "definitionRefs": [f"{prefix}c0:0"],
                        },
                        {
                            "id": "amount",
                            "key": column_keys[1],
                            "label": "Amount",
                            "column": 1,
                            "valueType": amount_type,
                            "definitionRefs": [f"{prefix}c0:1"],
                        },
                    ],
                }
            ],
            "dispositions": [
                {"sourceRef": ref, "role": "data", "explanation": "Table member"}
                for ref in table_nodes
            ],
        }
    )
    return [(text_region, text_ir), (table_region, table_ir)]


def two_pages(*, rows2=ROWS, unit2="pcs", extra_statement=False, **page2):
    doc = ObservationDocument()
    parts = [
        *_page(doc, "p1", 1, ROWS, "pcs", key="unit", extra_statement=extra_statement),
        *_page(doc, "p2", 2, rows2, unit2, key="unit_2", **page2),
    ]
    regions = [region for region, _ in parts]
    compiled = [compile_region(ir, doc, region) for region, ir in parts]
    return doc, regions, compiled


def test_versions_and_contract_name_the_duplicate_decision():
    assert PROMPT_VERSION == "document-files.semantic-prompts.v30"
    assert COMPILER_VERSION == "document-files.result-compiler.v27"
    assert SCOPE_VERSION == "document-files.scope-integration.v13"
    assert "duplicate" in INTEGRATE and "continue is not offered there" in INTEGRATE
    assert "Cite sourceRefs from the sourceNodes keys only" in INTEGRATE
    schema = DocumentIntegration.model_json_schema()
    assert schema["$defs"]["ContinuationDecision"]["properties"]["decision"]["enum"] == [
        "continue",
        "duplicate",
        "separate",
        "unresolved",
    ]
    item = ContinuationDecision(
        candidateId="continuation:1", decision="duplicate", sourceRefs=["x"], explanation="copy"
    )
    assert item.decision == "duplicate"


def test_candidates_carry_row_identity_whole_edge_rows_and_text_counterparts():
    doc, regions, _ = two_pages()
    (candidate,) = continuation_candidates(doc, regions)
    assert candidate["basis"] == "adjacent_page_column_candidate"
    assert (candidate["leftRows"], candidate["rightRows"]) == (4, 4)
    assert candidate["rightRepeatsLeft"] is True
    assert candidate["rightHeaderRepeatsLeft"] is True
    refs = candidate["sourceRefs"]
    # The same positions of both tables: first two rows and the last row, whole rows.
    for prefix in ("p1", "p2"):
        for ref in (
            f"{prefix}c0:0",
            f"{prefix}c0:1",
            f"{prefix}c1:0",
            f"{prefix}c1:1",
            f"{prefix}c3:0",
            f"{prefix}c3:1",
        ):
            assert ref in refs
        assert f"{prefix}c2:0" not in refs and f"{prefix}c2:1" not in refs
    contract = integration_contract([candidate])
    items = contract["properties"]["continuations"]
    assert (items["minItems"], items["maxItems"]) == (1, 1) and "$defs" not in contract
    (branch,) = items["items"]["anyOf"]
    decision = branch["properties"]
    assert branch["additionalProperties"] is False
    assert decision["candidateId"]["enum"] == ["continuation:1"]
    assert decision["decision"]["enum"] == ["duplicate", "separate", "unresolved"]
    assert decision["sourceRefs"]["items"]["enum"] == refs
    assert list(decision) == ["candidateId", "decision", "sourceRefs", "explanation"]
    assert candidate["nodeCounterparts"] == {"p2stmt": "p1stmt"}
    assert "nodeCounterparts" not in integration_candidate(candidate)
    assert integration_candidate(candidate)["rightRepeatsLeft"] is True

    doc, regions, _ = two_pages(rows2=(("Name", "Amount"), ("D", "4"), ("E", "5"), ("F", "6")))
    (candidate,) = continuation_candidates(doc, regions)
    assert candidate["rightRepeatsLeft"] is False
    assert candidate["rightHeaderRepeatsLeft"] is True
    doc, regions, _ = two_pages(rows2=(("Part", "Count"), ("D", "4"), ("E", "5"), ("F", "6")))
    assert continuation_candidates(doc, regions)[0]["rightHeaderRepeatsLeft"] is False
    (branch,) = integration_contract([candidate])["properties"]["continuations"]["items"]["anyOf"]
    assert branch["properties"]["decision"]["enum"] == ["continue", "separate", "unresolved"]

    doc, regions, _ = two_pages(extra_statement=True)
    (candidate,) = continuation_candidates(doc, regions)
    assert candidate["nodeCounterparts"] == {}

    # Other channels' copies of the same line (native lines, recognizer source cells,
    # superseded text) are not region members and do not make the wording ambiguous:
    # the fourteenth GPU run had no counterparts because of them.
    doc, regions, _ = two_pages()
    doc.node("p1line", "Unit: pcs", role="line", locator={"page": 1})
    doc.node("p1source", "Unit: pcs", role="recognition_source_cell", locator={"page": 1})
    doc.node("p2superseded", "Unit: pcs", role="section_header", locator={"page": 2})
    (candidate,) = continuation_candidates(doc, regions)
    assert candidate["nodeCounterparts"] == {"p2stmt": "p1stmt"}


def test_duplicate_binds_the_copy_to_the_same_rows_and_adds_only_provenance():
    doc, regions, compiled = two_pages()
    candidates = continuation_candidates(doc, regions)
    joined, issues, links = join_continuations(
        compiled, candidates, {"continuation:1": "duplicate"}
    )
    assert not issues
    assert [link["kind"] for link in links] == ["tableDuplicate"]
    result = combine_regions(joined)
    assert not result["errors"]
    assert result["data"]["rows"] == [
        {"name": "A", "amount": "1"},
        {"name": "B", "amount": "2"},
        {"name": "C", "amount": "3"},
    ]
    evidence = {e["target"]["path"]: e for e in result["valueEvidence"]}
    # Cell and column-definition provenance of both presentations, earlier page first.
    assert evidence["/rows/0/name"]["sourceRefs"] == ["p1c1:0", "p1c0:0", "p2c1:0", "p2c0:0"]
    assert evidence["/rows/1/amount"]["sourceRefs"] == ["p1c2:1", "p1c0:1", "p2c2:1", "p2c0:1"]
    definitions = [
        s
        for s in result["semantics"]
        if s["kind"] == "field_definition"
        and s["targets"][0]["path"] == "/properties/rows/items/properties/name"
    ]
    # The assertion keeps the earlier definition's own references; the copy's
    # header joins the schema evidence and the correction record.
    assert len(definitions) == 1 and definitions[0]["sourceRefs"] == ["p1c0:0"]
    schema = {e["target"]["path"]: e for e in result["schemaEvidence"]}
    assert schema["/properties/rows/items/properties/name"]["sourceRefs"] == ["p1c0:0", "p2c0:0"]
    right = next(r for r in joined if r.id == "p2table")
    assert right.row_scopes == {} and right.repeat_paths == {} and right.data == {}
    codes = {c["code"] for c in result["corrections"]}
    assert "duplicate_table_definition_merged" in codes
    assert all(
        c["semanticId"] in {"p2table:rows", "p2table:name", "p2table:amount"}
        for c in result["corrections"]
        if c["code"] == "duplicate_table_definition_merged"
    )


def test_duplicate_with_different_rows_is_refused_not_joined():
    doc, regions, compiled = two_pages(
        rows2=(("Name", "Amount"), ("D", "4"), ("E", "5"), ("F", "6"))
    )
    candidates = continuation_candidates(doc, regions)
    joined, issues, links = join_continuations(
        compiled, candidates, {"continuation:1": "duplicate"}
    )
    assert issues == [{"code": "table_duplicate_rows_differ", "candidateId": "continuation:1"}]
    assert not links
    result = combine_regions(joined)
    assert result["data"]["unit_2"] == "pcs"


@pytest.mark.parametrize("decision", ["continue", "duplicate"])
def test_repeated_statement_fields_and_meanings_fold_into_the_earlier_page(decision):
    doc, regions, compiled = two_pages()
    candidates = continuation_candidates(doc, regions)
    joined, issues, _ = join_continuations(compiled, candidates, {"continuation:1": decision})
    assert not issues
    result = combine_regions(joined)
    assert not result["errors"]
    assert "unit_2" not in result["data"] and result["data"]["unit"] == "pcs"
    assert "unit_2" not in result["dataSchema"]["properties"]
    assert len(result["data"]["rows"]) == (3 if decision == "duplicate" else 6)
    evidence = {e["target"]["path"]: e for e in result["valueEvidence"]}
    assert evidence["/unit"]["sourceRefs"] == ["p1stmt", "p2stmt"]
    assert evidence["/unit"]["semanticIds"] == ["p1text:unit"]
    schema = {e["target"]["path"]: e for e in result["schemaEvidence"]}
    assert schema["/properties/unit"]["sourceRefs"] == ["p1stmt", "p2stmt"]
    assert schema["/properties/unit"]["semanticIds"] == ["p1text:unit"]
    units = [s for s in result["semantics"] if s["kind"] in {"unit", "unresolved_unit"}]
    assert [u["id"] for u in units] == ["p1text:u"]
    assert units[0]["sourceRefs"] == ["p1stmt"]
    definitions = [s for s in result["semantics"] if s["targets"][0]["path"] == "/properties/unit"]
    assert len(definitions) == 1 and definitions[0]["sourceRefs"] == ["p1stmt"]
    merged = [c for c in result["corrections"] if c["code"] == "repeated_meaning_merged"]
    assert merged[0]["sourceRefs"] == ["p2stmt"] and merged[0]["into"] == "p1text:u"
    assert [i for i in result["issues"] if i["code"] == "semantic_scope_unresolved"] == [
        {"code": "semantic_scope_unresolved", "semanticId": "p1text:u"}
    ]
    codes = [c["code"] for c in result["corrections"]]
    assert "repeated_statement_merged" in codes and "repeated_meaning_merged" in codes
    tasks = build_scope_tasks(doc, regions, joined)
    assert [t.region_id for t in tasks] == ["p1text"]
    # The repeated wording on page 2 is statement text, never an applicability candidate.
    offered = [item["definition"]["id"] for item in tasks[0].target_map.values()]
    assert "p1text:unit" not in offered
    # Candidates present each definition as it was interpreted on its own page.
    for candidate in tasks[0].payload["candidates"]:
        assert all(ref.startswith("p1") for ref in candidate["definitionRefs"])


def test_repeated_wording_with_a_different_value_or_separate_tables_is_kept():
    doc, regions, compiled = two_pages(unit2="kg")
    candidates = continuation_candidates(doc, regions)
    assert candidates[0]["nodeCounterparts"] == {}
    joined, _, _ = join_continuations(compiled, candidates, {"continuation:1": "continue"})
    result = combine_regions(joined)
    assert result["data"]["unit"] == "pcs" and result["data"]["unit_2"] == "kg"

    doc, regions, compiled = two_pages()
    candidates = continuation_candidates(doc, regions)
    joined, issues, links = join_continuations(compiled, candidates, {"continuation:1": "separate"})
    assert not issues and not links
    result = combine_regions(joined)
    assert result["data"]["unit"] == "pcs" and result["data"]["unit_2"] == "pcs"


def test_integration_request_sends_bounded_position_views_of_whole_rows():
    doc, regions, _ = two_pages()
    doc.nodes["p1c3:1"]["semanticInput"] = {"role": "representative", "conflicts": ["x"] * 50}
    doc.nodes["p1c3:1"]["sourceStructure"]["bbox"] = {
        "left": 1.23456,
        "top": 2.0,
        "right": 3.5,
        "bottom": 4.0,
        "sourceBox": [0, 0, 1, 1],
    }
    doc.nodes["p1c3:1"]["sourceStructure"]["tableRef"] = "p1t"
    candidates = continuation_candidates(doc, regions)
    request = integration_request(doc, candidates)
    assert "nodeCounterparts" not in request["candidates"][0]
    assert request["candidates"][0]["rightRepeatsLeft"] is True
    node = request["sourceNodes"]["p1c3:1"]
    assert node == {"text": "3", "tableRef": "p1t", "row": 3, "col": 1}
    doc.nodes["p1stmt"]["sourceStructure"]["bbox"] = {
        "left": 1.6,
        "top": 2.2,
        "right": 3.5,
        "bottom": 4,
    }
    line = integration_request(doc, candidates)["sourceNodes"]["p1stmt"]
    assert line["bbox"] == {"left": 2, "top": 2, "right": 4, "bottom": 4}
    assert request["sourceNodes"]["p1stmt"] == {
        "text": "Unit: pcs",
        "semanticRole": "text",
        "page": 1,
    }
    # The twelfth GPU run could not send whole-row evidence in the full observation view.
    assert len(encode(request)) < 3200


def test_continuation_fragment_keys_follow_the_earlier_fragment_by_column_position():
    # The twelfth continued-table run decided continue, but the later page had named
    # its repeat and columns differently and the join was refused as a column conflict.
    later = (("Name", "Amount"), ("D", "4"), ("E", "5"), ("F", "6"))
    doc, regions, compiled = two_pages(rows2=later, repeat_key="items", column_keys=("item", "amt"))
    candidates = continuation_candidates(doc, regions)
    joined, issues, links = join_continuations(compiled, candidates, {"continuation:1": "continue"})
    assert not issues and links
    result = combine_regions(joined)
    assert not result["errors"] and "items" not in result["data"]
    assert result["data"]["rows"] == [
        {"name": "A", "amount": "1"},
        {"name": "B", "amount": "2"},
        {"name": "C", "amount": "3"},
        {"name": "D", "amount": "4"},
        {"name": "E", "amount": "5"},
        {"name": "F", "amount": "6"},
    ]
    evidence = {e["target"]["path"]: e for e in result["valueEvidence"]}
    assert evidence["/rows/5/amount"]["sourceRefs"][0] == "p2c3:1"
    later_amount = next(
        s
        for s in result["semantics"]
        if s["kind"] == "field_definition" and s["sourceRefs"] == ["p2c0:1"]
    )
    assert later_amount["targets"][0]["path"] == "/properties/rows/items/properties/amount"
    assert {t["path"] for t in later_amount["scope"]} == {f"/rows/{i}/amount" for i in (3, 4, 5)}
    renamed = next(c for c in result["corrections"] if c["code"] == "continuation_columns_renamed")
    assert renamed["renamed"] == {"item": "name", "amt": "amount"}
    # The earlier column definition now governs the appended rows as well, so its
    # column handle means every data row; the fragment keeps its own definition.
    earlier_amount = next(
        s
        for s in result["semantics"]
        if s["kind"] == "field_definition" and s["sourceRefs"] == ["p1c0:1"]
    )
    assert {t["path"] for t in earlier_amount["scope"]} == {f"/rows/{i}/amount" for i in range(6)}
    extended = [c for c in result["corrections"] if c["code"] == "continuation_definition_extended"]
    assert {c["semanticId"] for c in extended} == {"p1table:name", "p1table:amount"}
    # A value type that differs at the same column still conflicts.
    doc, regions, compiled = two_pages(rows2=later, amount_type="integer")
    candidates = continuation_candidates(doc, regions)
    _, issues, _ = join_continuations(compiled, candidates, {"continuation:1": "continue"})
    assert [i["code"] for i in issues] == ["table_continuation_column_conflict"]


def test_one_later_statement_over_several_earlier_statements_folds_into_them():
    # The nineteenth mixed PDF regression read the raster page's two condition lines
    # as one meaning while the native page had interpreted them separately; the later
    # meaning lost its fields to the fold and stayed as an unresolved statement.
    from document_files.interpretation.compiler import _merge_repeated_statements

    doc = ObservationDocument()
    parts = []
    for prefix, page in (("p1", 1), ("p2", 2)):
        refs, bindings = [], []
        for name, text in (("ko", "조건: 수량 0"), ("en", "Condition: zero")):
            ref = doc.node(f"{prefix}{name}", text, role="text", locator={"page": page})
            refs.append(ref)
            bindings.append(doc.bind(ref, start=0, end=len(text), candidateRole="content"))
        region = {"id": f"{prefix}text", "nodeIds": refs, "bindingIds": bindings}
        fields = [
            {
                "id": name,
                "key": f"{name}_{page}",
                "label": name,
                "definitionRefs": [ref],
                "bindingId": binding,
                "valueType": "string",
            }
            for name, ref, binding in zip(("ko", "en"), refs, bindings, strict=True)
        ]
        meanings = (
            [
                {
                    "id": f"m_{name}",
                    "kind": "condition",
                    "description": name,
                    "sourceRefs": [ref],
                    "fieldIds": [name],
                }
                for name, ref in zip(("ko", "en"), refs, strict=True)
            ]
            if page == 1
            else [
                {
                    "id": "both",
                    "kind": "condition",
                    "description": "Do not ship a zero count.",
                    "sourceRefs": refs,
                    "fieldIds": ["ko", "en"],
                }
            ]
        )
        ir = RegionInterpretation.model_validate(
            {
                "regionId": region["id"],
                "fields": fields,
                "meanings": meanings,
                "dispositions": [
                    {"sourceRef": ref, "role": "data", "explanation": "Statement"} for ref in refs
                ],
            }
        )
        parts.append((region, ir))
    compiled = [compile_region(ir, doc, region) for region, ir in parts]
    aliases = {}
    _merge_repeated_statements(
        compiled, {"p2ko": "p1ko", "p2en": "p1en"}, "continuation:1", aliases
    )
    assert aliases["p2text:both"] == "p1text:m_ko"
    assert [d["id"] for d in compiled[1].semantic_details] == []
    merged = [c for c in compiled[1].corrections if c["code"] == "repeated_meaning_merged"]
    assert merged[0]["into"] == "p1text:m_ko" and merged[0]["alsoInto"] == ["p1text:m_en"]
    assert not [i for i in compiled[1].issues if i["code"] == "semantic_scope_unresolved"]
    result = combine_regions(compiled)
    assert set(result["data"]) == {"ko_1", "en_1"}
    assert [s["id"] for s in result["semantics"] if s["kind"].endswith("condition")] == [
        "p1text:m_ko",
        "p1text:m_en",
    ]
