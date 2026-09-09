"""Exact lines, candidate spans and relationships already declared by native formats."""

from __future__ import annotations

import re
from copy import deepcopy

from .model import ObservationDocument

# Candidate boundaries only. Role, type and applicability are interpreted by the internal AI.
_LEXEME = re.compile(r"[^\s;,:=]+")
_FIELD = re.compile(r"(?:^|[;\n])\s*([^;\n:=]+?)\s*[:=]([^;\n]*)")


def bind_spans(doc: ObservationDocument, node_id: str) -> list[str]:
    text = doc.nodes[node_id].get("text", "")
    ids = [doc.bind(node_id, start=0, end=len(text), candidateRole="content")]
    for match in _FIELD.finditer(text):
        label_start, label_end = match.span(1)
        raw_start, raw_end = match.span(2)
        while raw_start < raw_end and text[raw_start].isspace():
            raw_start += 1
        while raw_end > raw_start and text[raw_end - 1].isspace():
            raw_end -= 1
        label = doc.bind(
            node_id,
            start=label_start,
            end=label_end,
            candidateRole="label",
        )
        value = doc.bind(
            node_id,
            start=raw_start,
            end=raw_end,
            candidateRole="value",
            labelRefs=[label],
            blank=raw_start == raw_end,
        )
        ids.extend([label, value])
        doc.relations.append(
            {
                "kind": "labelValueCandidate",
                "labelBinding": label,
                "valueBinding": value,
                "basis": "source_delimiter",
            }
        )
    if len(text) <= 50000:
        for token in _LEXEME.finditer(text):
            if token.span() == (0, len(text)):
                continue
            ids.append(
                doc.bind(
                    node_id,
                    start=token.start(),
                    end=token.end(),
                    candidateRole="lexeme",
                )
            )
    return ids


def observe_lines(
    doc: ObservationDocument, text: str, *, parent_ref: str, prefix: str
) -> list[str]:
    line_ids = []
    offset = 0
    for ordinal, raw in enumerate(text.splitlines(keepends=True), 1):
        ending = "\r\n" if raw.endswith("\r\n") else raw[-1:] if raw.endswith(("\n", "\r")) else ""
        body = raw[: -len(ending)] if ending else raw
        node_id = doc.node(
            f"{prefix}/line/{ordinal}",
            body,
            role="blank" if not body.strip() else "line",
            locator={
                "parentRef": parent_ref,
                "line": ordinal,
                "start": offset,
                "end": offset + len(body),
                "lineEnding": ending,
            },
            observationBasis="native_text",
            parentRef=parent_ref,
        )
        doc.relations.append({"kind": "contains", "sourceRef": parent_ref, "targetRef": node_id})
        bind_spans(doc, node_id)
        line_ids.append(node_id)
        offset += len(raw)
    return line_ids


def add_native_relationships(doc: ObservationDocument, legacy_nodes: dict) -> None:
    note_nodes = {}
    pending_notes = []
    cell_index = {}
    cell_addresses = {}
    for node_id, node in legacy_nodes.items():
        bind_spans(doc, node_id)
        semantic = node.get("semantic", {})
        value = semantic.get("value", {})
        if isinstance(value, dict):
            for key in ("raw", "value", "formula"):
                if key in value and not isinstance(value[key], (dict, list)):
                    doc.bind(node_id, path=f"/semantic/value/{key}", candidateRole="native_value")
            cache = value.get("cachedValue")
            if isinstance(cache, dict):
                for key in ("raw", "value"):
                    if key in cache and not isinstance(cache[key], (dict, list)):
                        doc.bind(
                            node_id,
                            path=f"/semantic/value/cachedValue/{key}",
                            candidateRole="cached_value",
                        )
        structure = node.get("sourceStructure", {})
        if node.get("semanticRole") in {"paragraph", "line"} and "\n" in node.get("text", ""):
            observe_lines(doc, node["text"], parent_ref=node_id, prefix=node_id)
        table = semantic.get("table", {})
        cell = semantic.get("cell", {})
        sheet = semantic.get("sheet", {})
        if sheet:
            table_ref = f"native:sheet:{sheet.get('index', '')}:{sheet.get('name', '')}"
        elif "sourceRef" in table or "table" in structure:
            # Table indices are local to a part/section. Never merge same indices across parts.
            part = structure.get("part", structure.get("section", structure.get("slide", "")))
            container = structure.get("container_path", "")
            table_ref = (
                f"native:table:{part}:{container}:{table.get('sourceRef', structure.get('table'))}"
            )
        else:
            table_ref = None
        if table_ref:
            target = doc.tables.setdefault(
                table_ref,
                {
                    "id": table_ref,
                    "basis": "native_structure",
                    "cells": [],
                    "contextNodeIds": [],
                },
            )
            if cell and "row" in cell and "column" in cell:
                base = cell.get("indexBase", 0)
                record = {
                    "sourceRef": node_id,
                    "sourceRefs": [node_id],
                    "row": cell["row"] - base,
                    "col": cell["column"] - base,
                    "rowSpan": cell.get("rowSpan", 1),
                    "colSpan": cell.get("colSpan", 1),
                    "isHeader": cell.get("isHeader", False),
                    "sourceCell": deepcopy(cell),
                }
                index_key = (table_ref, record["row"], record["col"])
                existing = cell_index.get(index_key)
                if existing:
                    existing["sourceRefs"].append(node_id)
                    if not doc.nodes[existing["sourceRef"]].get("text") and node.get("text"):
                        existing["sourceRef"] = node_id
                else:
                    target["cells"].append(record)
                    cell_index[index_key] = record
                cell_address = structure.get("cell")
                if cell_address is not None:
                    cell_addresses[
                        (structure.get("part", structure.get("section", "")), str(cell_address))
                    ] = existing or record
            else:
                target["contextNodeIds"].append(node_id)
        note = semantic.get("note", {})
        note_id = note.get("sourceRef", structure.get("note"))
        note_kind = note.get("kind", structure.get("container_kind", ""))
        reference_kind = structure.get("reference_type", "")
        if reference_kind in {"footnoteReference", "endnoteReference", "commentReference"}:
            pending_notes.append((node_id, reference_kind.removesuffix("Reference"), str(note_id)))
        elif note_id is not None and note_kind in {"footnote", "endnote", "comment"}:
            note_nodes.setdefault((note_kind, str(note_id)), []).append(node_id)
        for key in ("footnote_refs", "endnote_refs", "note_refs"):
            for ref in structure.get(key, []):
                doc.relations.append(
                    {
                        "kind": "noteReference",
                        "sourceRef": node_id,
                        "nativeTarget": str(ref),
                        "basis": "native_reference",
                    }
                )
        if semantic.get("field"):
            doc.relations.append(
                {
                    "kind": "fieldDeclaration",
                    "sourceRef": node_id,
                    "field": deepcopy(semantic["field"]),
                    "basis": "native_field",
                }
            )
    for source_ref, kind, note_id in pending_notes:
        targets = note_nodes.get((kind, note_id), [])
        for target in targets:
            doc.relations.append(
                {
                    "kind": "noteReference",
                    "sourceRef": source_ref,
                    "targetRef": target,
                    "basis": "native_reference",
                }
            )
        if not targets:
            doc.issue("native_note_reference_unresolved", sourceRef=source_ref)
    for table_ref, table in doc.tables.items():
        cells = table["cells"]
        for cell in cells:
            segments = [ref for ref in cell["sourceRefs"] if doc.nodes[ref].get("text")]
            if len(segments) > 1:
                source_segments = []
                offset = 0
                for ref in segments:
                    text = doc.nodes[ref]["text"]
                    source_segments.append(
                        {
                            "sourceRef": ref,
                            "sourceStart": 0,
                            "sourceEnd": len(text),
                            "start": offset,
                            "end": offset + len(text),
                        }
                    )
                    offset += len(text) + 1
                composite = doc.node(
                    f"{table_ref}/cell/{cell['row']}/{cell['col']}",
                    "\n".join(doc.nodes[ref]["text"] for ref in segments),
                    role="table_cell",
                    locator={"tableRef": table_ref, "row": cell["row"], "col": cell["col"]},
                    observationBasis="program_normalized",
                    normalization="join_native_cell_segments_with_newline",
                    sourceSegments=source_segments,
                )
                bind_spans(doc, composite)
                cell["sourceRef"] = composite
                cell["sourceRefs"].append(composite)
                for ref in segments:
                    doc.relations.append(
                        {
                            "kind": "composedFrom",
                            "sourceRef": composite,
                            "targetRef": ref,
                            "basis": "native_cell_membership",
                        }
                    )
            first = doc.nodes[cell["sourceRefs"][0]].get("sourceStructure", {})
            container = first.get("container_path", [])
            if container and isinstance(container[-1], dict) and "cell" in container[-1]:
                address = (first.get("part", first.get("section", "")), str(container[-1]["cell"]))
                outer = cell_addresses.get(address)
                if outer:
                    doc.relations.append(
                        {
                            "kind": "nestedTableInCell",
                            "sourceRef": outer["sourceRef"],
                            "tableRef": table_ref,
                            "basis": "native_container_path",
                        }
                    )
            doc.relations.append(
                {
                    "kind": "tableCell",
                    "sourceRef": cell["sourceRef"],
                    "tableRef": table_ref,
                    "row": cell["row"],
                    "col": cell["col"],
                }
            )
