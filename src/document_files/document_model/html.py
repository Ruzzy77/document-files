"""HTML element, form and span-aware table observations without browser execution."""

from __future__ import annotations

from html.parser import HTMLParser

from .model import ObservationBudgetExceeded, ObservationDocument
from .native import bind_spans

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}
_BLOCKS = {
    "p",
    "li",
    "dt",
    "dd",
    "td",
    "th",
    "caption",
    "label",
    "textarea",
    "option",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "input",
    "button",
}


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "children": [], "attrs": {}, "id": "html:root"}
        self.stack = [self.root]
        self.elements = []

    def handle_starttag(self, tag, attrs):
        if len(self.elements) >= 100000 or len(self.stack) >= 256:
            raise ObservationBudgetExceeded("HTML structure budget exceeded")
        element = {
            "tag": tag,
            "attrs": dict(attrs),
            "children": [],
            "parent": self.stack[-1],
            "id": f"html:element:{len(self.elements) + 1}",
            "position": self.getpos(),
        }
        self.stack[-1]["children"].append(element)
        self.elements.append(element)
        if tag not in _VOID:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def _text(element, *, root=True):
    if not root and element["tag"] == "table":
        return ""
    return "".join(
        child if isinstance(child, str) else _text(child, root=False)
        for child in element["children"]
    )


def _ancestor(element, tag):
    current = element.get("parent")
    while current is not None:
        if current["tag"] == tag:
            return current
        current = current.get("parent")
    return None


def observe_html(doc: ObservationDocument, text: str) -> list[str]:
    parser = _Tree()
    parser.feed(text)
    parser.close()
    ids = []
    html_ids = {}
    for element in parser.elements:
        tag, attrs = element["tag"], element["attrs"]
        # Preserve non-container text too; do not throw away unwrapped prose in body/div.
        own_text = "".join(c for c in element["children"] if isinstance(c, str))
        if tag not in _BLOCKS and not (
            tag in {"body", "div", "section", "form"} and own_text.strip()
        ):
            if tag in {"img", "svg", "canvas", "object", "embed"}:
                doc.issue("html_visual_content_uninterpreted", element=element["id"])
            continue
        value = (attrs.get("value") or "") if tag == "input" else _text(element)
        if tag not in _BLOCKS:
            value = own_text
        role = (
            "table_cell"
            if tag in {"td", "th"}
            else "field"
            if tag in {"input", "textarea"}
            else tag
        )
        node_id = doc.node(
            element["id"],
            value,
            role=role,
            locator={
                "tag": tag,
                "line": element["position"][0],
                "column": element["position"][1],
                "attributes": attrs,
            },
            observationBasis="native_html",
        )
        if tag == "input" and attrs.get("type", "text") in {"checkbox", "radio"}:
            doc.nodes[node_id]["semantic"] = {
                "value": {"kind": "boolean", "value": "checked" in attrs}
            }
            doc.bind(node_id, path="/semantic/value/value", candidateRole="native_value")
        bind_spans(doc, node_id)
        if attrs.get("id"):
            html_ids[attrs["id"]] = node_id
        ids.append(node_id)
    for element in parser.elements:
        if element["id"] not in doc.nodes:
            continue
        parent = element.get("parent")
        while parent and parent["id"] not in doc.nodes:
            parent = parent.get("parent")
        if parent:
            doc.relations.append(
                {
                    "kind": "contains",
                    "sourceRef": parent["id"],
                    "targetRef": element["id"],
                    "basis": "native_html",
                }
            )
        target = element["attrs"].get("for")
        if element["tag"] == "label" and target in html_ids:
            doc.relations.append(
                {
                    "kind": "labelFor",
                    "sourceRef": element["id"],
                    "targetRef": html_ids[target],
                    "basis": "html_for_attribute",
                }
            )
    for element in (e for e in parser.elements if e["tag"] == "table"):
        table_ref = element["id"]
        rows = [e for e in parser.elements if e["tag"] == "tr" and _ancestor(e, "table") is element]
        table = {
            "id": table_ref,
            "cells": [],
            "basis": "native_html",
            "indexBase": 0,
            "contextNodeIds": [
                e["id"]
                for e in parser.elements
                if e["tag"] == "caption"
                and _ancestor(e, "table") is element
                and e["id"] in doc.nodes
            ],
        }
        occupied = set()
        for row_number, row in enumerate(rows):
            cells = [
                e
                for e in parser.elements
                if e["tag"] in {"td", "th"}
                and _ancestor(e, "tr") is row
                and _ancestor(e, "table") is element
            ]
            column = 0
            for cell in cells:
                while (row_number, column) in occupied:
                    column += 1
                try:
                    rowspan = int(cell["attrs"].get("rowspan", "1"))
                    colspan = int(cell["attrs"].get("colspan", "1"))
                    if rowspan == 0:
                        rowspan = sum(
                            1 for r in rows[row_number:] if r.get("parent") is row.get("parent")
                        )
                    if not 1 <= rowspan <= 1000 or not 1 <= colspan <= 1000:
                        raise ValueError
                    if len(occupied) + rowspan * colspan > 100000:
                        raise ValueError
                except (TypeError, ValueError):
                    doc.issue("html_table_span_unresolved", sourceRef=cell["id"])
                    continue
                coverage = {
                    (r, c)
                    for r in range(row_number, row_number + rowspan)
                    for c in range(column, column + colspan)
                }
                if occupied & coverage:
                    doc.issue("html_table_span_conflict", sourceRef=cell["id"])
                occupied.update(coverage)
                table["cells"].append(
                    {
                        "sourceRef": cell["id"],
                        "row": row_number,
                        "col": column,
                        "rowSpan": rowspan,
                        "colSpan": colspan,
                        "isHeader": cell["tag"] == "th",
                        "headerScope": cell["attrs"].get("scope"),
                    }
                )
                for header in (cell["attrs"].get("headers") or "").split():
                    if header in html_ids:
                        doc.relations.append(
                            {
                                "kind": "headerFor",
                                "sourceRef": html_ids[header],
                                "targetRef": cell["id"],
                                "basis": "html_headers_attribute",
                            }
                        )
                column += colspan
        doc.tables[table_ref] = table
    doc.provenance["html"] = {
        "parser": "stdlib.html.parser",
        "scriptsExecuted": False,
        "externalResourcesFetched": False,
    }
    return ids
