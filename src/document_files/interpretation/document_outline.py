"""Source-linked logical blocks, separate from native nodes and business values.

The model chooses block roles and outline levels. Code retains exact source ranges,
orders occurrences and resolves section/caption links. Equal text is never identity.
This first native outline path covers HWP/HWPX; other formats keep their existing
observation contracts until their logical-block paths are implemented and checked.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import Field

from ..result_types import Contract

VERSION = "document-files.document-outline.v1"
FORMATS = {"hwp", "hwpx"}


class DocumentElement(Contract):
    sourceRef: str
    role: Literal[
        "title",
        "section_heading",
        "caption",
        "paragraph",
        "field_group",
        "list_item",
        "note",
        "header",
        "footer",
        "unresolved",
    ]
    # These are interpreted outline levels, not a native paragraph's default level.
    level: int | None = Field(ge=0, le=12)
    captionOf: str | None
    status: Literal["interpreted", "uncertain"]


def enabled(observation):
    return observation.provenance.get("format") in FORMATS


def block_refs(observation, refs):
    if not enabled(observation):
        return []
    return [
        ref
        for ref in refs
        if observation.nodes[ref].get("text", "").strip()
        and observation.nodes[ref].get("semanticRole")
        not in {
            "table",
            "sheet",
            "page",
            "source_text",
            "table_cell",
            "sheet_cell",
        }
        and not observation.nodes[ref].get("semantic", {}).get("cell")
    ]


def _ordinal(observation, ref):
    # Native unit order is not inferred from text, IDs or default paragraph levels.
    ordinal = observation.nodes[ref].get("ordinal")
    return ordinal if type(ordinal) is int else list(observation.nodes).index(ref) + 1


def table_anchors(observation):
    anchors = {}
    for tid, table in observation.tables.items():
        if table.get("sourceTableRef", tid) != tid:
            continue  # Bounded processing slices are not new document occurrences.
        refs = [
            ref
            for ref in table.get("contextNodeIds", [])
            if observation.nodes.get(ref, {}).get("semanticRole") == "table"
        ]
        if not refs:
            refs = [c["sourceRef"] for c in table.get("cells", [])[:1]]
        if refs:
            ref = min(refs, key=lambda r: _ordinal(observation, r))
            anchors[tid] = {"sourceRef": ref, "ordinal": _ordinal(observation, ref)}
    return anchors


def role_context(observation, region):
    refs = block_refs(observation, region["nodeIds"])
    if not refs:
        return None
    anchors = table_anchors(observation)
    low, high = (
        min(_ordinal(observation, r) for r in refs),
        max(_ordinal(observation, r) for r in refs),
    )
    nearby = {t for t, a in anchors.items() if low <= a["ordinal"] <= high}
    for before in (True, False):
        candidates = [
            (t, a)
            for t, a in anchors.items()
            if (a["ordinal"] < low if before else a["ordinal"] > high)
        ]
        if candidates:
            chosen = (max if before else min)(candidates, key=lambda item: item[1]["ordinal"])
            nearby.add(chosen[0])
    return {
        "version": VERSION,
        "ownedSourceRefs": refs,
        "sourceOrder": {ref: _ordinal(observation, ref) for ref in refs},
        "captionCandidates": {
            tid: {
                **anchors[tid],
                "rowCount": observation.tables[tid].get("rowCount"),
                "columnCount": observation.tables[tid].get("colCount"),
            }
            for tid in sorted(nearby, key=lambda tid: anchors[tid]["ordinal"])
        },
    }


def constrain_schema(schema, observation, region):
    context = role_context(observation, region)
    if not context:
        schema["properties"].pop("documentElements", None)
        return
    refs = context["ownedSourceRefs"]
    schema["properties"]["documentElements"].update(minItems=len(refs), maxItems=len(refs))
    if "documentElements" not in schema.setdefault("required", []):
        schema["required"].append("documentElements")
    definition = schema["$defs"]["DocumentElement"]
    props = definition["properties"]
    props["sourceRef"] = {"type": "string", "enum": refs}
    choices = list(context["captionCandidates"])
    # Concrete alternatives work in the local decoder too. Independent role and
    # level enums allowed invalid combinations that wasted bounded repair calls.
    branches = []

    def branch(role, level, target, status=None):
        item = deepcopy(definition)
        item["properties"].update(role=role, level=level, captionOf=target)
        if status:
            item["properties"]["status"] = {"type": "string", "const": status}
        branches.append(item)

    for role, level in [
        ("title", {"const": 0, "type": "integer"}),
        ("section_heading", {"type": "integer", "minimum": 1, "maximum": 12}),
    ]:
        branch({"type": "string", "const": role}, level, {"type": "null"})
    branch(
        {
            "type": "string",
            "enum": [
                r for r in props["role"]["enum"] if r not in {"title", "section_heading", "caption"}
            ],
        },
        {"type": "null"},
        {"type": "null"},
    )
    if choices:
        branch(
            {"type": "string", "const": "caption"},
            {"type": "null"},
            {"type": "string", "enum": choices},
        )
    branch(
        {"type": "string", "const": "caption"},
        {"type": "null"},
        {"type": "null"},
        status="uncertain",
    )
    schema["$defs"]["DocumentElement"] = {"anyOf": branches}


def preceding_headings(observation, region, compiled):
    refs = block_refs(observation, region["nodeIds"])
    if not refs:
        return []
    low = min(_ordinal(observation, ref) for ref in refs)
    stack = []
    prior = sorted(
        (e for c in compiled for e in c.document_elements if e["sourceOrder"] < low),
        key=lambda e: (e["sourceOrder"], e["textBinding"]["start"]),
    )
    for element in prior:
        if element["status"] != "interpreted" or element["role"] == "unresolved":
            stack.clear()
        elif element["role"] in {"title", "section_heading"}:
            while stack and stack[-1]["level"] >= element["level"]:
                stack.pop()
            binding = element["textBinding"]
            stack.append(
                {
                    "sourceRef": element["sourceRef"],
                    "role": element["role"],
                    "level": element["level"],
                    "status": element["status"],
                    "text": observation.nodes[element["sourceRef"]]["text"][
                        binding["start"] : binding["end"]
                    ],
                }
            )
    return stack


def structural_value_allowed(binding, node, window):
    """A real inner value can coexist with a title/caption, not its whole text."""
    start, end = binding.get("start"), binding.get("end")
    path = binding.get("path")
    same_text = path == "/text" or (
        path == "/semantic/value/value"
        and node.get("semantic", {}).get("value", {}).get("value") == node.get("text")
    )
    return (
        binding.get("candidateRole") == "value"
        and same_text
        and type(start) is int
        and type(end) is int
        and window["start"] <= start <= end <= window["end"]
        and (start, end) != (window["start"], window["end"])
    )


def compile_elements(elements, observation, region, fields):
    """Validate decisions even without decoder grammar; never erase conflicting data."""
    context = role_context(observation, region)
    expected = set(context["ownedSourceRefs"]) if context else set()
    if len(elements) != len(expected) or {e.sourceRef for e in elements} != expected:
        raise ValueError("document_element_sources_incomplete_or_duplicated")
    result = []
    for element in elements:
        ref = element.sourceRef
        text = observation.nodes[ref]["text"]
        window = region.get("nodeViews", {}).get(ref, {"start": 0, "end": len(text)})
        if (
            (element.role == "title" and element.level != 0)
            or (element.role == "section_heading" and (element.level is None or element.level < 1))
            or (element.role not in {"title", "section_heading"} and element.level is not None)
        ):
            raise ValueError("document_element_level_invalid")
        if element.role == "caption":
            if element.captionOf is None and element.status == "interpreted":
                raise ValueError("document_caption_target_required")
            if (
                element.captionOf is not None
                and element.captionOf not in context["captionCandidates"]
            ):
                raise ValueError("document_caption_target_not_offered")
        elif element.captionOf is not None:
            raise ValueError("document_caption_target_on_noncaption")
        if element.role in {"title", "section_heading", "caption"} and any(
            f.bindingId in observation.bindings
            and observation.bindings[f.bindingId]["sourceRef"] == ref
            and not structural_value_allowed(
                observation.bindings[f.bindingId], observation.nodes[ref], window
            )
            for f in fields
        ):
            raise ValueError("document_role_value_conflict")
        result.append(
            {
                **element.model_dump(),
                "id": f"block:{ref}:{window['start']}:{window['end']}",
                "textBinding": {
                    "sourceRef": ref,
                    "path": "/text",
                    **window,
                    "representation": "text",
                },
                "sourceRefs": [ref],
                "regionId": region["id"],
                "basis": "ai_interpreted",
                "sourceOrder": _ordinal(observation, ref),
            }
        )
    return result


def project_outline(observation, compiled):
    """Resolve interpreted levels over native order, not from the coverage ledger."""
    if not enabled(observation):
        return None, []
    elements = [deepcopy(e) for c in compiled for e in c.document_elements]
    issues = []
    by_ref = {}
    for element in elements:
        by_ref.setdefault(element["sourceRef"], []).append(element)
    for ref in block_refs(observation, observation.nodes):
        entries = by_ref.get(ref, [])
        if not entries:
            elements.append(
                {
                    "id": f"block:{ref}",
                    "sourceRef": ref,
                    "sourceRefs": [ref],
                    "sourceOrder": _ordinal(observation, ref),
                    "role": "unresolved",
                    "level": None,
                    "captionOf": None,
                    "status": "unreviewed",
                    "basis": "pending_interpretation",
                    "textBinding": {
                        "sourceRef": ref,
                        "path": "/text",
                        "start": 0,
                        "end": len(observation.nodes[ref]["text"]),
                        "representation": "text",
                    },
                }
            )
            issues.append({"code": "document_outline_unreviewed", "sourceRef": ref})
        elif (
            len(entries) != 1
            or entries[0]["textBinding"]["start"] != 0
            or entries[0]["textBinding"]["end"] != len(observation.nodes[ref]["text"])
        ):
            # A reviewed fragment cannot silently become the whole paragraph or
            # repeated headings. Keep exact fragments and expose the pending join.
            issues.append({"code": "document_outline_fragments_unjoined", "sourceRef": ref})
    table_ids = {}
    for tid, anchor in table_anchors(observation).items():
        eid = f"table:{tid}"
        table_ids[tid] = eid
        elements.append(
            {
                "id": eid,
                "sourceRef": anchor["sourceRef"],
                "sourceRefs": [anchor["sourceRef"]],
                "sourceOrder": anchor["ordinal"],
                "role": "table",
                "tableRef": tid,
                "level": None,
                "captionOf": None,
                "status": "observed",
                "basis": "native_table_membership",
            }
        )
    elements.sort(
        key=lambda e: (e["sourceOrder"], e.get("textBinding", {}).get("start", 0), e["id"])
    )
    stack, links = [], []
    ambiguous_parent = False
    ambiguous_level = 0
    for element in elements:
        role = element["role"]
        certain = element["status"] in {"interpreted", "observed"} and role != "unresolved"
        if not certain:
            issues.append({"code": "document_outline_uncertain", "sourceRef": element["sourceRef"]})
            if role in {"title", "section_heading", "unresolved"}:
                uncertain_level = element["level"] if element["level"] is not None else 0
                ambiguous_level = (
                    min(ambiguous_level, uncertain_level) if ambiguous_parent else uncertain_level
                )
                ambiguous_parent = True
        level = element["level"]
        if certain and role in {"title", "section_heading"}:
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            if ambiguous_parent and level <= ambiguous_level:
                ambiguous_parent = False
        # Running headers/footers do not become section children or reset the stack.
        parent = (
            stack[-1]["id"]
            if stack and not ambiguous_parent and role not in {"header", "footer"}
            else None
        )
        element["parentId"] = parent
        element["parentStatus"] = "unresolved" if ambiguous_parent else "resolved"
        element["hierarchyBasis"] = "interpreted_levels_and_native_order"
        if certain and role in {"title", "section_heading"}:
            stack.append(element)
        if element.get("captionOf") in table_ids:
            links.append(
                {
                    "kind": "captionOf",
                    "sourceId": element["id"],
                    "targetId": table_ids[element["captionOf"]],
                    "basis": "ai_interpreted",
                    "status": element["status"],
                    "sourceRefs": element["sourceRefs"],
                }
            )
    # Native nested-table membership takes precedence over section-level placement.
    by_id = {e["id"]: e for e in elements}
    for relation in observation.relations:
        if relation.get("kind") != "nestedTableInCell" or relation.get("tableRef") not in table_ids:
            continue
        outer = next(
            (
                tid
                for tid, table in observation.tables.items()
                if tid in table_ids
                and any(
                    relation["sourceRef"] in cell.get("sourceRefs", [cell["sourceRef"]])
                    for cell in table.get("cells", [])
                )
            ),
            None,
        )
        if outer is not None and outer != relation["tableRef"]:
            child = by_id[table_ids[relation["tableRef"]]]
            if by_id[table_ids[outer]]["sourceOrder"] >= child["sourceOrder"]:
                issues.append(
                    {
                        "code": "document_outline_native_parent_invalid",
                        "tableRef": relation["tableRef"],
                    }
                )
                child.update(parentId=None, parentStatus="unresolved")
                continue
            child.update(
                parentId=table_ids[outer],
                parentCellRef=relation["sourceRef"],
                parentStatus="resolved",
                hierarchyBasis="native_nested_table_membership",
            )
    return {
        "version": VERSION,
        "status": "partial" if issues else "interpreted",
        "orderBasis": "native_unit_order",
        "elements": elements,
        "relations": links,
    }, issues
