"""Markdown syntax observations using parsed tokens, not typographic title guesses."""

from __future__ import annotations

from .model import ObservationDocument
from .native import bind_spans


def observe_markdown(doc: ObservationDocument, text: str) -> list[str]:
    try:
        from markdown_it import MarkdownIt
        from mdit_py_plugins.footnote import footnote_plugin
    except ImportError:
        doc.issue("markdown_structure_runtime_unavailable")
        return []
    parser = MarkdownIt("commonmark").enable("table").use(footnote_plugin)
    tokens = parser.parse(text)
    ids = []
    current_table = None
    row, col = -1, 0
    current_cell = None
    row_lines = None
    cell_header = False
    footnotes = {}
    pending_refs = []
    active_note = None
    note_content = {}
    stack = []
    for ordinal, token in enumerate(tokens):
        if token.type == "footnote_close":
            active_note = None
        if token.type == "table_open":
            current_table = f"md:table:{ordinal}"
            doc.tables[current_table] = {
                "id": current_table,
                "cells": [],
                "indexBase": 0,
                "basis": "markdown_table_tokens",
                "contextNodeIds": [],
            }
            row = -1
        elif token.type == "table_close":
            current_table = None
        elif token.type == "tr_open":
            row += 1
            col = 0
            row_lines = token.map
        elif token.type in {"th_open", "td_open"}:
            current_cell = f"md:cell:{ordinal}"
            cell_header = token.type == "th_open"
        elif token.type in {"th_close", "td_close"}:
            current_cell = None
            col += 1
        if token.nesting == 1:
            stack.append(token.type)
        elif token.nesting == -1 and stack:
            stack.pop()
        if token.type not in {"inline", "fence", "code_block"}:
            if token.type == "footnote_open":
                active_note = token.meta.get("id")
                footnotes[active_note] = f"md:footnote:{ordinal}"
                note_content[active_note] = []
                doc.node(
                    footnotes[token.meta.get("id")],
                    "",
                    role="footnote",
                    locator={"token": ordinal, "label": token.meta.get("label")},
                )
            continue
        node_id = current_cell or f"md:token:{ordinal}"
        role = (
            "table_cell" if current_cell else "heading" if "heading_open" in stack else "paragraph"
        )
        doc.node(
            node_id,
            token.content,
            role=role,
            locator={
                "lineRange": token.map or (row_lines if current_cell else None),
                "lineIndexBase": 0,
                "token": ordinal,
                "tokenType": token.type,
            },
            observationBasis="markdown_syntax",
            syntaxContext=list(stack),
        )
        bind_spans(doc, node_id)
        ids.append(node_id)
        if active_note is not None:
            note_content[active_note].append(node_id)
            doc.relations.append(
                {
                    "kind": "contains",
                    "sourceRef": footnotes[active_note],
                    "targetRef": node_id,
                    "basis": "markdown_footnote",
                }
            )
        if current_cell and current_table:
            doc.tables[current_table]["cells"].append(
                {
                    "sourceRef": node_id,
                    "row": row,
                    "col": col,
                    "rowSpan": 1,
                    "colSpan": 1,
                    "isHeader": cell_header,
                }
            )
        for child in token.children or []:
            if child.type == "footnote_ref":
                pending_refs.append((node_id, child.meta.get("id")))
    for node_id, target_id in pending_refs:
        if target_id in footnotes:
            for body_ref in note_content.get(target_id, []):
                doc.relations.append(
                    {
                        "kind": "noteReference",
                        "sourceRef": node_id,
                        "targetRef": body_ref,
                        "basis": "markdown_footnote",
                    }
                )
            doc.relations.append(
                {
                    "kind": "noteReference",
                    "sourceRef": node_id,
                    "targetRef": footnotes[target_id],
                    "basis": "markdown_footnote",
                }
            )
    doc.provenance["markdown"] = {
        "parser": "markdown-it-py",
        "footnotes": "mdit-py-plugins",
        "typographyInference": False,
    }
    return ids
