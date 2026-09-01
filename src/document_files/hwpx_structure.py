"""HWPX XML ownership and source-declared structure, with no conversion."""

from __future__ import annotations

import posixpath
import re
import zipfile
from collections import Counter

from .extraction_errors import ExtractionError
from .extractors import (
    ExtractionResult,
    UnitDraft,
    _bounded_unit,
    _local_name,
    _preflight_zip,
    _safe_archive_xml_root,
    normalize_text,
)
from .hwp_images import hwpx_binary_items, hwpx_picture
from .list_numbering import HWP_MARKER, HWP_NUMBER_FORMATS, ListCounters

_CONTAINERS = {
    "footNote": "footnote",
    "endNote": "endnote",
    "header": "header",
    "footer": "footer",
    "caption": "caption",
    "drawText": "textbox",
}
_OBJECTS = {
    "pic",
    "ole",
    "equation",
    "chart",
    "video",
    "textart",
    "container",
    "rect",
    "ellipse",
    "line",
    "polygon",
    "curve",
    "arc",
    "connectLine",
}
_PRIMITIVE_OBJECTS = {
    "container",
    "rect",
    "ellipse",
    "line",
    "polygon",
    "curve",
    "arc",
    "connectLine",
}
_UNREAD_EMBEDDED_OBJECTS = {"ole", "chart", "video", "textart"}
_OBJECT_ISSUE_MESSAGES = {
    "hwpx_shape_layout_unverified": (
        "Native HWPX drawing elements were retained, but their rendered layout was not verified."
    ),
    "hwpx_embedded_object_content_unread": (
        "An embedded HWPX object type was identified without readable native content."
    ),
    "hwpx_equation_content_unread": (
        "An HWPX equation did not expose a readable stored equation script."
    ),
}


def _number(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    try:
        number = int(value)
    except (ValueError, TypeError) as exc:
        raise ExtractionError("HWPX structural number is invalid") from exc
    if not 0 <= number <= 1_000_000:
        raise ExtractionError("HWPX structural number exceeds its bound")
    return number


def _sections(archive: zipfile.ZipFile) -> tuple[list[str], list[dict]]:
    names = [
        name
        for name in archive.namelist()
        if re.fullmatch(r"Contents/section\d+\.xml", name, re.IGNORECASE)
    ]
    if len(names) != len(set(names)):
        raise ExtractionError("HWPX contains duplicate section members")
    fallback = sorted(
        names, key=lambda name: int(re.search(r"(\d+)\.xml$", name, re.IGNORECASE)[1])
    )
    if "Contents/content.hpf" not in archive.namelist():
        return fallback, []
    package = _safe_archive_xml_root(archive, "Contents/content.hpf")
    manifest = {}
    order = []
    for node in package.iter():
        if _local_name(node.tag) == "item":
            href = node.get("href", "")
            if href.startswith("/") or ":" in href or ".." in href.split("/"):
                continue
            resolved = href if href in names else posixpath.join("Contents", href)
            if resolved in names:
                manifest[node.get("id")] = resolved
        elif _local_name(node.tag) == "itemref":
            order.append(node.get("idref"))
    selected = [manifest[key] for key in order if key in manifest]
    if len(selected) == len(names) and set(selected) == set(names):
        return selected, []
    return fallback, [
        {
            "code": "hwpx_section_order_partial",
            "severity": "warning",
            "message": "Package section order is incomplete; numeric member order was retained.",
        }
    ]


def _numbering_counters(levels: dict) -> ListCounters:
    """Build counters from the numbering definition the package declares."""
    definitions = {}
    for level, declared in levels.items():
        start = declared.get("level_start_number")
        overall = declared.get("numbering_start_number")
        if start is None and (overall == "1" or (level == 1 and overall is not None)):
            start = overall
        try:
            value = None if start is None else _number(start)
        except ExtractionError:
            value = None
        definitions[level] = {
            "style": HWP_NUMBER_FORMATS.get(declared.get("number_format")),
            "start": value,
            "restarts": True,
            "pattern": declared.get("marker_pattern"),
        }
    return ListCounters(definitions, HWP_MARKER)


class _Reader:
    def __init__(
        self,
        shapes: dict,
        styles: dict,
        images: dict | None = None,
        numberings: dict | None = None,
    ):
        self.shapes = shapes
        self.styles = styles
        self.images = images or {}
        self.numberings = numberings or {}
        self.counters: dict[str, ListCounters] = {}
        self.contaminated: set[str] = set()
        self.units: list[UnitDraft] = []
        self.issues: Counter[str] = Counter()
        self.base: dict = {}
        self.paragraph = 0
        self.characters = 0
        self.nodes = 0
        self.tables: dict[str, dict] = {}
        self.fields: dict[str, dict] = {}
        self.active_fields: list[str] = []

    def list_marker(self, properties: dict, main_flow: bool) -> dict | None:
        """Advance the declared list and return its label when it is provable.

        Numbering continues in package section order. A paragraph outside the
        body flow leaves every later paragraph of that list unresolved instead
        of guessing where its number belongs.
        """
        reference = properties.get("numbering_ref")
        level = properties.get("level")
        if reference not in self.numberings or not isinstance(level, int):
            return None
        if not main_flow:
            self.contaminated.add(reference)
            return None
        if reference in self.contaminated:
            return None
        counters = self.counters.get(reference)
        if counters is None:
            counters = _numbering_counters(self.numberings[reference])
            self.counters[reference] = counters
        value = counters.advance(level)
        text = counters.label(level)
        if text is None:
            return None
        return {
            "text": text,
            "value": value,
            "level": level,
            "basis": "source_numbering_definition_and_section_order",
        }

    def emit(self, kind: str, location: dict, text: str = "") -> None:
        location = dict(location)
        if kind != "list_item":
            location.pop("computed_list_marker", None)
        text = normalize_text(text)
        self.characters += len(text)
        if self.characters > 50_000_000 or len(self.units) >= 200_000:
            raise ExtractionError("HWPX structure exceeds its output budget")
        draft = UnitDraft(kind, {**self.base, **location}, text)
        self.units.extend(_bounded_unit(draft) if text else [draft])

    def geometry(self, value: str | None) -> int | None:
        try:
            if value is not None:
                return _number(value)
        except ExtractionError:
            pass
        self.issues["hwpx_table_geometry_partial"] += 1
        return None

    def walk(self, node, address: str, context: dict, depth: int = 0) -> None:
        self.nodes += 1
        if depth > 64 or self.nodes > 2_000_000:
            raise ExtractionError("HWPX structure exceeds its traversal budget")
        tag = _local_name(node.tag)
        context = dict(context)
        if tag == "p":
            context.pop("format_markers", None)
            context.pop("computed_list_marker", None)
            self.paragraph += 1
            context.update({"paragraph": self.paragraph, "paragraph_element": address})
            properties = self.shapes.get(node.get("paraPrIDRef"), {})
            context.update(properties)
            if "styleIDRef" in node.attrib:
                context["style"] = node.get("styleIDRef")
                context["style_name"] = self.styles.get(node.get("styleIDRef"), "")
            head = properties.get("head_type", "none")
            kind = (
                "heading"
                if head == "outline"
                else "list_item"
                if head in {"number", "bullet"}
                else context.get("container_kind", "section_paragraph")
            )
            if kind == "textbox":
                kind = "section_paragraph"
            if head in {"number", "bullet"} and not properties.get("marker_text"):
                marker = (
                    self.list_marker(properties, "container_kind" not in context)
                    if head == "number"
                    else None
                )
                if marker is None:
                    self.issues["hwpx_list_marker_partial"] += 1
                else:
                    context["computed_list_marker"] = marker
            chunks: list[str] = []
            markers: list[dict] = []
            segment = 0

            def flush():
                nonlocal segment
                if normalize_text("".join(chunks)):
                    segment += 1
                    self.emit(
                        kind,
                        {
                            **context,
                            "element": address,
                            "segment": segment,
                            **({"format_markers": list(markers)} if markers else {}),
                            **(
                                {"field_path": list(self.active_fields)}
                                if self.active_fields
                                else {}
                            ),
                        },
                        "".join(chunks),
                    )
                chunks.clear()

            def inline(element, element_address, inline_depth):
                if inline_depth > 64:
                    raise ExtractionError(
                        "HWPX inline structure exceeds its depth budget"
                    )
                name = _local_name(element.tag)
                if name == "t":
                    chunks.append(element.text or "")
                    text_offset = len(element.text or "")
                    for index, child in enumerate(element):
                        child_name = _local_name(child.tag)
                        chunks.append(
                            "\t"
                            if child_name == "tab"
                            else "\n"
                            if child_name == "lineBreak"
                            else " "
                            if child_name in {"nbSpace", "fwSpace"}
                            else ""
                        )
                        if child_name in {"markpenBegin", "markpenEnd"}:
                            markers.append(
                                {
                                    "element": f"{element_address}.{index}",
                                    "kind": child_name,
                                    "attributes": dict(child.attrib),
                                    "text_element_offset": text_offset,
                                    "offset_unit": "unicode_code_points",
                                }
                            )
                        elif child_name not in {
                            "tab",
                            "lineBreak",
                            "nbSpace",
                            "fwSpace",
                        }:
                            self.issues["hwpx_inline_content_partial"] += 1
                        chunks.append(child.tail or "")
                        text_offset += len(child.tail or "")
                    return
                if (
                    name == "p"
                    or name == "tbl"
                    or name in _CONTAINERS
                    or name in _OBJECTS
                ):
                    flush()
                    self.walk(element, element_address, context, inline_depth)
                    return
                if name == "fieldBegin":
                    flush()
                    identifier, field_type = element.get("id"), element.get("type")
                    metadata = {
                        **context,
                        "element": element_address,
                        "field_id": element.get("fieldid"),
                        "native_id": identifier,
                        "field_type": field_type,
                        "name": element.get("name", ""),
                        "evaluation": "stored_result_only",
                        "parameters": [],
                    }
                    if field_type:
                        metadata["field_type_origin"] = "declared_type"
                    parameter_characters = 0
                    for pi, param_group in enumerate(element):
                        if _local_name(param_group.tag) != "parameters":
                            continue
                        for qi, param in enumerate(param_group):
                            if len(metadata["parameters"]) >= 128:
                                self.issues["hwpx_field_metadata_truncated"] += 1
                                break
                            value = param.text or ""
                            name_value = param.get("name", "")
                            available = max(0, 16_384 - parameter_characters)
                            if len(value) > available or len(name_value) > 256:
                                self.issues["hwpx_field_metadata_truncated"] += 1
                            if len(param):
                                self.issues["hwpx_field_metadata_partial"] += 1
                            value = value[:available]
                            parameter_characters += len(value)
                            metadata["parameters"].append(
                                {
                                    "element": f"{element_address}.{pi}.{qi}",
                                    "name": name_value[:256],
                                    "kind": _local_name(param.tag),
                                    "value": value,
                                }
                            )
                    if not field_type:
                        command = (
                            element.get("command")
                            or element.get("Command")
                            or next(
                                (
                                    item["value"]
                                    for item in metadata["parameters"]
                                    if item["name"].casefold() == "command"
                                ),
                                "",
                            )
                        )
                        # A stored MEMO command names the field without being
                        # executed. No other command is interpreted here.
                        if command.partition("/")[0] == "MEMO":
                            field_type = "MEMO"
                            metadata.update(
                                field_type=field_type,
                                field_type_origin="stored_command",
                                stored_command=command[:16_384],
                            )
                    self.emit("field", metadata)
                    if not identifier or identifier in self.fields:
                        self.issues["hwpx_field_structure_partial"] += 1
                    else:
                        self.fields[identifier] = self.units[-1].structure_path
                        self.active_fields.append(identifier)
                    if field_type not in {
                        "BOOKMARK",
                        "CLICK_HERE",
                        "FORMULA",
                        "MEMO",
                        "HYPERLINK",
                        "DATE",
                        "DOCDATE",
                        "DOC_DATE",
                        "PATH",
                        "SUMMARY",
                        "SUMMERY",
                        "USERINFO",
                        "USER_INFO",
                        "CROSSREF",
                        "MAILMERGE",
                        "TABLEOFCONTENTS",
                        "CITATION",
                        "BIBLIOGRAPHY",
                        "METADATA",
                    }:
                        self.issues["hwpx_field_semantics_partial"] += 1
                    for ci, child in enumerate(element):
                        if _local_name(child.tag) == "subList":
                            child_context = {
                                **context,
                                "owner_paragraph": address,
                                "field": identifier,
                            }
                            if field_type == "MEMO":
                                child_context.update(
                                    {"container_kind": "comment", "note": identifier}
                                )
                            self.walk(
                                child,
                                f"{element_address}.{ci}",
                                child_context,
                                inline_depth + 1,
                            )
                    return
                if name in {"autoNum", "newNum"}:
                    if name == "newNum":
                        # A restart whose relation to paragraph numbering has
                        # not been established prevents computed list labels.
                        self.contaminated.update(self.numberings)
                    try:
                        number = _number(element.get("num"), default=-1)
                        if number < 0:
                            raise ExtractionError("HWPX stored number is missing")
                    except ExtractionError:
                        self.issues["hwpx_number_control_partial"] += 1
                        return
                    self.emit(
                        "field",
                        {
                            **context,
                            "element": element_address,
                            "field_type": "auto_number"
                            if name == "autoNum"
                            else "new_number",
                            "stored_number": number,
                            "number_type": element.get("numType"),
                            "number_origin": "stored_control_value",
                            "number_format": [
                                dict(child.attrib)
                                for child in element
                                if _local_name(child.tag) == "autoNumFormat"
                            ],
                        },
                    )
                    return
                if name == "fieldEnd":
                    flush()
                    identifier = element.get("beginIDRef")
                    if identifier not in self.active_fields:
                        self.issues["hwpx_field_range_partial"] += 1
                    else:
                        self.fields[identifier]["end_element"] = element_address
                        self.active_fields.remove(identifier)
                    return
                for index, child in enumerate(element):
                    inline(child, f"{element_address}.{index}", inline_depth + 1)

            for index, child in enumerate(node):
                inline(child, f"{address}.{index}", depth + 1)
            flush()
            return
        if tag == "tbl":
            parent = {
                key: context[key]
                for key in ("table", "cell", "note", "object")
                if key in context
            }
            for key in ("cell", "row", "col", "row_span", "col_span", "is_header"):
                context.pop(key, None)
            context.update(
                {"table": address, "object": address, "container_kind": "table"}
            )
            if parent:
                context["container_path"] = [*context.get("container_path", []), parent]
            table = {
                "rows": self.geometry(node.get("rowCnt")),
                "cols": self.geometry(node.get("colCnt")),
                "cells": [],
            }
            self.tables[address] = table
            self.emit(
                "table",
                {
                    **context,
                    "element": address,
                    **{k: table[k] for k in ("rows", "cols") if table[k] is not None},
                },
            )
        elif tag == "tc":
            values = {_local_name(child.tag): child for child in node}
            cell_address, span = values.get("cellAddr"), values.get("cellSpan")
            for key in ("row", "col", "row_span", "col_span", "is_header"):
                context.pop(key, None)
            cell = {
                "cell": address,
                "is_header": node.get("header", "0") in {"1", "true"},
            }
            if (
                cell_address is None
                or span is None
                or context.get("table") not in self.tables
            ):
                self.issues["hwpx_table_structure_partial"] += 1
            else:
                geometry = {
                    "row": self.geometry(cell_address.get("rowAddr")),
                    "col": self.geometry(cell_address.get("colAddr")),
                    "row_span": self.geometry(span.get("rowSpan")),
                    "col_span": self.geometry(span.get("colSpan")),
                }
                cell.update({k: v for k, v in geometry.items() if v is not None})
                if all(value is not None for value in geometry.values()):
                    self.tables[context["table"]]["cells"].append(cell)
            context.update({**cell, "container_kind": "table_cell"})
            self.emit(
                "table_cell",
                {**context, "element": address, "structural_only": True},
            )
        elif tag in _CONTAINERS:
            kind = _CONTAINERS[tag]
            context["container_kind"] = kind
            context["owner_paragraph"] = context.get("paragraph_element")
            if kind in {"footnote", "endnote"}:
                context["note"] = address
                context["note_number"] = node.get("number")
            self.emit(
                kind if kind != "textbox" else "embedded_object",
                {**context, "element": address, "structural_only": True},
            )
        elif tag in _OBJECTS:
            context["object"] = address
            self.emit(
                "embedded_object",
                {
                    **context,
                    "element": address,
                    "object_type": tag,
                    "owner_paragraph": context.get("paragraph_element"),
                    **(hwpx_picture(node, self.images) if tag == "pic" else {}),
                },
            )
            if tag == "equation":
                script_observed = False
                for index, child in enumerate(node):
                    if _local_name(child.tag) == "script" and child.text:
                        script_observed = True
                        self.emit(
                            "embedded_object",
                            {
                                **context,
                                "element": f"{address}.{index}",
                                "text_representation": "hwp_equation_script",
                            },
                            child.text,
                        )
                if not script_observed:
                    self.issues["hwpx_equation_content_unread"] += 1
            elif tag in _PRIMITIVE_OBJECTS:
                self.issues["hwpx_shape_layout_unverified"] += 1
            elif tag in _UNREAD_EMBEDDED_OBJECTS:
                self.issues["hwpx_embedded_object_content_unread"] += 1
        for index, child in enumerate(node):
            self.walk(child, f"{address}.{index}", context, depth + 1)

    def finish_section(self) -> None:
        if self.active_fields:
            self.issues["hwpx_field_range_partial"] += len(self.active_fields)
        self.active_fields.clear()
        self.fields.clear()
        for table in self.tables.values():
            active = []
            comparisons = 0
            if not table["rows"] or not table["cols"] or not table["cells"]:
                self.issues["hwpx_table_structure_partial"] += 1
            for cell in sorted(table["cells"], key=lambda c: (c["row"], c["col"])):
                row, col, height, width = (
                    cell[k] for k in ("row", "col", "row_span", "col_span")
                )
                if (
                    height < 1
                    or width < 1
                    or (table["rows"] is not None and row + height > table["rows"])
                    or (table["cols"] is not None and col + width > table["cols"])
                ):
                    self.issues["hwpx_table_geometry_partial"] += 1
                active = [c for c in active if c["row"] + c["row_span"] > row]
                comparisons += len(active)
                if comparisons > 1_000_000:
                    self.issues["hwpx_table_geometry_partial"] += 1
                    break
                if any(
                    col < c["col"] + c["col_span"] and col + width > c["col"]
                    for c in active
                ):
                    self.issues["hwpx_table_geometry_partial"] += 1
                active.append(cell)
        self.tables.clear()


def extract_structured_hwpx(path) -> ExtractionResult:
    _preflight_zip(path)
    with zipfile.ZipFile(path) as archive:
        sections, issues = _sections(archive)
        shapes, styles, bullets, numberings = {}, {}, {}, {}
        if "Contents/header.xml" in archive.namelist():
            header = _safe_archive_xml_root(archive, "Contents/header.xml")
            for node in header.iter():
                tag = _local_name(node.tag)
                if tag == "paraPr":
                    heading = next(
                        (
                            child
                            for child in node
                            if _local_name(child.tag) == "heading"
                        ),
                        None,
                    )
                    if heading is not None:
                        shapes[node.get("id")] = {
                            "head_type": heading.get("type", "NONE").lower(),
                            "level": _number(heading.get("level")) + 1,
                            "numbering_ref": heading.get("idRef"),
                        }
                elif tag == "style":
                    styles[node.get("id")] = node.get("name", "")
                elif tag == "bullet" and node.get("useImage", "0") in {"0", "false"}:
                    bullets[node.get("id")] = node.get("char", "")
                elif tag == "numbering":
                    numberings[node.get("id")] = {
                        _number(child.get("level")): {
                            "marker_pattern": child.text or "",
                            "number_format": child.get("numFormat"),
                            "start_number": child.get("start", node.get("start")),
                            "numbering_start_number": node.get("start"),
                            "level_start_number": child.get("start"),
                        }
                        for child in node
                        if _local_name(child.tag) == "paraHead"
                    }
            for shape in shapes.values():
                ref = shape.get("numbering_ref")
                if shape.get("head_type") == "bullet" and ref in bullets:
                    shape["marker_text"] = bullets[ref]
                elif shape.get("head_type") in {"number", "outline"}:
                    shape.update(numberings.get(ref, {}).get(shape["level"], {}))
        images = (
            hwpx_binary_items(
                _safe_archive_xml_root(archive, "Contents/content.hpf"),
                archive.namelist(),
            )
            if "Contents/content.hpf" in archive.namelist()
            else {}
        )
        reader = _Reader(shapes, styles, images, numberings)
        if issues:
            reader.contaminated.update(numberings)
        for index, name in enumerate(sections, 1):
            reader.base = {"section": index, "section_file": name}
            reader.paragraph = 0
            reader.walk(_safe_archive_xml_root(archive, name), "0", {})
            reader.finish_section()
        invalidated = set()
        for unit in reader.units:
            location = unit.structure_path
            if (
                location.get("numbering_ref") in reader.contaminated
                and "computed_list_marker" in location
            ):
                del location["computed_list_marker"]
                invalidated.add(
                    (location.get("section_file"), location.get("paragraph"))
                )
        if invalidated:
            reader.issues["hwpx_list_marker_partial"] += len(invalidated)
        issues.extend(
            {
                "code": code,
                "severity": "warning",
                "message": _OBJECT_ISSUE_MESSAGES.get(
                    code,
                    "Some HWPX structure could not be fully reconstructed.",
                ),
                "details": {"occurrences": count},
            }
            for code, count in sorted(reader.issues.items())
        )
        if not any(unit.content for unit in reader.units):
            issues.append(
                {
                    "code": "no_extractable_text",
                    "severity": "warning",
                    "message": "No native text was found.",
                }
            )
        return ExtractionResult(reader.units, issues)
