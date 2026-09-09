"""Observation orchestration independent of model inference and public v1 analysis."""

from __future__ import annotations

import hashlib
from copy import deepcopy

from .html import observe_html
from .markdown import observe_markdown
from .model import OBSERVATION_VERSION, ObservationBudgetExceeded, ObservationDocument
from .native import add_native_relationships, observe_lines
from .pdf import observe_pdf


def _regions(doc: ObservationDocument, preferred_ids: list[str]) -> None:
    binding_lookup = {}
    for binding_id, binding in doc.bindings.items():
        binding_lookup.setdefault(binding["sourceRef"], []).append(binding_id)
    table_nodes = set()
    for table_ref, table in doc.tables.items():
        cells = table["cells"]
        table["rowCount"] = max(
            table.get("declaredRowCount", 0),
            max((c["row"] + c["rowSpan"] for c in cells), default=0),
        )
        table["colCount"] = max(
            table.get("declaredColCount", 0),
            max((c["col"] + c["colSpan"] for c in cells), default=0),
        )
        table["indexBase"] = 0
        node_ids = list(
            dict.fromkeys(n for c in cells for n in c.get("sourceRefs", [c["sourceRef"]]))
        )
        table_nodes.update(node_ids)
        headers = [
            n for c in cells if c.get("isHeader") for n in c.get("sourceRefs", [c["sourceRef"]])
        ]
        context = list(dict.fromkeys([*table.get("contextNodeIds", []), *headers]))
        doc.regions.append(
            {
                "id": f"region:{len(doc.regions) + 1}",
                "tableRef": table_ref,
                "nodeIds": node_ids,
                "contextNodeIds": context,
                "bindingIds": [b for n in node_ids for b in binding_lookup.get(n, [])],
            }
        )
    context = []
    for node_id in preferred_ids:
        if node_id in table_nodes:
            continue
        node = doc.nodes[node_id]
        role = node.get("semanticRole", "")
        if role in {"heading", "title", "h1", "h2", "h3", "h4", "h5", "h6"}:
            context = [node_id]
        if role == "blank" or (not node.get("text") and role in {"table", "sheet", "page"}):
            continue
        doc.regions.append(
            {
                "id": f"region:{len(doc.regions) + 1}",
                "nodeIds": [node_id],
                "contextNodeIds": [c for c in context if c != node_id],
                "bindingIds": binding_lookup.get(node_id, []),
            }
        )
    inbound = {}
    notes = {}
    for relation in doc.relations:
        if relation.get("targetRef") in doc.nodes and relation.get("sourceRef") in doc.nodes:
            # Full-source containers preserve provenance, but are not semantic context:
            # inserting one into every region defeats bounded interpretation and pulls
            # every sibling containment edge into its inference payload.
            if doc.nodes[relation["sourceRef"]].get("semanticRole") != "source_text":
                inbound.setdefault(relation["targetRef"], []).append(relation["sourceRef"])
            if relation.get("kind") == "noteReference":
                notes.setdefault(relation["sourceRef"], []).append(relation["targetRef"])
    for region in doc.regions:
        related = [
            ref
            for node in region["nodeIds"]
            for ref in [*inbound.get(node, []), *notes.get(node, [])]
        ]
        region["contextNodeIds"] = list(dict.fromkeys([*region["contextNodeIds"], *related]))


def _source_order_and_context(doc: ObservationDocument, preferred_ids: list[str]) -> None:
    """Attach nearby source candidates, never infer their semantic applicability."""
    order = {ref: index for index, ref in enumerate(preferred_ids)}
    fallback = {ref: index + len(order) for index, ref in enumerate(doc.nodes)}

    def region_key(region):
        refs = [*region["nodeIds"], *region.get("contextNodeIds", [])]
        rank = min(
            (order.get(ref, fallback.get(ref, len(fallback))) for ref in refs),
            default=len(fallback),
        )
        # PDF parser exports separate text/table arrays; page geometry establishes
        # their positional order without pretending that geometry proves semantics.
        if doc.provenance.get("format") == "pdf":
            locators = [doc.nodes[ref].get("sourceStructure", {}) for ref in region["nodeIds"]]
            if region.get("tableRef"):
                locators.append(doc.tables[region["tableRef"]].get("locator", {}))
            positions = [
                (loc["page"], loc["bbox"].get("top", 0), loc["bbox"].get("left", 0))
                for loc in locators
                if type(loc.get("page")) is int
                and isinstance(loc.get("bbox"), dict)
                and loc["bbox"].get("origin") == "TOPLEFT"
            ]
            if positions:
                return (0, *min(positions), rank)
            return (1, 0, 0, 0, rank)
        return (0, 0, 0, 0, rank)

    doc.regions.sort(key=region_key)
    headings = {"heading", "title", "h1", "h2", "h3", "h4", "h5", "h6"}
    latest_heading = None
    for index, region in enumerate(doc.regions):
        if not region.get("tableRef"):
            for ref in region["nodeIds"]:
                if doc.nodes[ref].get("semanticRole") in headings:
                    latest_heading = ref
            continue
        candidates = [latest_heading] if latest_heading else []
        for direction in (-1, 1):
            for distance in (1, 2):
                neighbor_index = index + direction * distance
                if not 0 <= neighbor_index < len(doc.regions):
                    break
                neighbor = doc.regions[neighbor_index]
                if neighbor.get("tableRef"):
                    break
                candidates.extend(neighbor["nodeIds"])
        candidates = [
            ref
            for ref in dict.fromkeys(candidates)
            if ref not in region["nodeIds"]
            and doc.nodes[ref].get("semanticRole") not in {"source_text", "page", "sheet", "table"}
        ]
        region["contextNodeIds"] = list(dict.fromkeys([*region["contextNodeIds"], *candidates]))
        for ref in candidates:
            doc.relations.append(
                {
                    "kind": "contextCandidate",
                    "sourceRef": ref,
                    "tableRef": region["tableRef"],
                    "basis": "source_adjacency",
                    "applicability": "not_assessed",
                }
            )
        doc.tables[region["tableRef"]]["contextNodeIds"] = list(region["contextNodeIds"])
    for index, region in enumerate(doc.regions, 1):
        region["id"] = f"region:{index}"


def observe_document(
    content: bytes,
    format_id: str,
    legacy_nodes: dict,
    *,
    recognition=None,
) -> ObservationDocument:
    doc = ObservationDocument(nodes=deepcopy(legacy_nodes))
    doc.provenance = {
        "schemaVersion": OBSERVATION_VERSION,
        "sourceSha256": hashlib.sha256(content).hexdigest(),
        "format": format_id,
        "legacyNodeIds": list(legacy_nodes),
        "originalNodesPreserved": True,
    }
    preferred = list(legacy_nodes)
    if len(content) > 128 * 1024 * 1024:
        doc.issue("observation_input_budget_exceeded")
        doc.coverage = {"status": "partial", "semanticUnderstanding": "not_assessed"}
        return doc
    try:
        add_native_relationships(doc, legacy_nodes)
        if format_id in {"txt", "md", "html"}:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                doc.issue("source_text_encoding_unresolved")
            else:
                source_ref = doc.node(
                    "source:text",
                    text,
                    role="source_text",
                    locator={"encoding": "utf-8", "format": format_id},
                    observationBasis="native_text",
                )
                lines = observe_lines(doc, text, parent_ref=source_ref, prefix="source:text")
                preferred = lines
                if format_id == "md":
                    structured = observe_markdown(doc, text)
                    if structured:
                        preferred = structured
                elif format_id == "html":
                    preferred = observe_html(doc, text)
        elif format_id == "pdf":
            preferred = observe_pdf(doc, content, recognition=recognition) or preferred
    except ObservationBudgetExceeded:
        doc.issue("observation_budget_exceeded")
    except (ValueError, TypeError, RecursionError):
        doc.issue("native_structure_observation_failed")
    _regions(doc, preferred)
    _source_order_and_context(doc, preferred)
    doc.coverage.update(
        {
            "status": "partial" if doc.issues else "observed",
            "nativeNodeCount": len(legacy_nodes),
            "nodeCount": len(doc.nodes),
            "bindingCount": len(doc.bindings),
            "tableCount": len(doc.tables),
            "semanticUnderstanding": "not_assessed",
        }
    )
    return doc
