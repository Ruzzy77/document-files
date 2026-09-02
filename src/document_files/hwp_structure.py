"""Source-addressed HWP5 structure, without layout or semantic inference.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
Record numbers
are one-based within the original section stream; cell coordinates are zero-based.
LIST_HEADER and its paragraphs are siblings in HWP5, not an XML-like subtree.
"""

from __future__ import annotations

import struct
from collections import Counter, defaultdict
from collections.abc import Mapping
from contextlib import suppress

if __package__:
    from .hwp_images import hwp_picture
else:
    from hwp_images import hwp_picture

STRUCTURAL_UNIT_TYPES = (
    "section_paragraph",
    "heading",
    "list_item",
    "table",
    "table_cell",
    "caption",
    "footnote",
    "endnote",
    "header",
    "footer",
    "embedded_object",
    "comment",
    "field",
)
_CONTROL_KINDS = {
    "tbl ": "table",
    "gso ": "embedded_object",
    "eqed": "embedded_object",
    "form": "embedded_object",
    "head": "header",
    "foot": "footer",
    "fn  ": "footnote",
    "en  ": "endnote",
    "tcmt": "comment",
}

_SHAPE_KINDS = {
    0x4E: "line",
    0x4F: "rectangle",
    0x50: "ellipse",
    0x51: "arc",
    0x52: "polygon",
    0x53: "curve",
    0x54: "ole",
    0x55: "picture",
    0x56: "group",
    0x5A: "textart",
    0x5B: "form",
    0x5F: "chart",
    0x62: "video",
}
_PRIMITIVE_SHAPES = {"line", "rectangle", "ellipse", "arc", "polygon", "curve", "group"}
_UNREAD_EMBEDDED_OBJECTS = {"ole", "textart", "form", "chart", "video"}
_OBJECT_ISSUE_MESSAGES = {
    "hwp_shape_layout_unverified": (
        "Native drawing records were retained, but their rendered layout was not verified."
    ),
    "hwp_embedded_object_content_unread": (
        "An embedded HWP object type was identified without readable native content."
    ),
    "hwp_equation_content_unread": (
        "An HWP equation control did not expose a readable stored equation script."
    ),
    "hwp_object_type_unresolved": (
        "An HWP object control did not expose a recognized native object record."
    ),
}

_FIELD_KINDS = {
    "%dte": "date",
    "%ddt": "document_date",
    "%pat": "path",
    "%bmk": "bookmark",
    "%mmg": "mail_merge",
    "%xrf": "cross_reference",
    "%fmu": "formula",
    "%clk": "click_here",
    "%smr": "summary",
    "%usr": "user_info",
    "%hlk": "hyperlink",
    "%%me": "memo",
}


def paragraph_properties(data: bytes, shapes: list[dict], styles: list[dict]) -> dict:
    if len(data) < 12:
        return {"properties_unavailable": True}
    shape_id = struct.unpack_from("<H", data, 8)[0]
    style_id = data[10]
    result = {"para_shape": shape_id, "style": style_id}
    if shape_id < len(shapes):
        result.update(shapes[shape_id])
    else:
        result["properties_unavailable"] = True
    if style_id < len(styles):
        result["style_name"] = styles[style_id].get("name", "")
    return result


def doc_info_properties(records, *, version: int = 0) -> tuple[list[dict], list[dict]]:
    shapes: list[dict] = []
    styles: list[dict] = []
    numberings: list[list[dict]] = []
    bullets: list[dict] = []
    for record, tag, _level, data in records:
        if tag == 0x17:
            levels = []
            offset = 0
            try:
                for _index in range(7):
                    flags = struct.unpack_from("<I", data, offset)[0]
                    length = struct.unpack_from("<H", data, offset + 12)[0]
                    end = offset + 14 + length * 2
                    if end > len(data):
                        raise ValueError("truncated numbering")
                    levels.append(
                        {
                            "marker_pattern": data[offset + 14 : end].decode(
                                "utf-16-le"
                            ),
                            # Only bits 0--4 of these flags have a verified interpretation.
                            # Preserve the rest without inventing a numbering style.
                            "numbering_flags": flags,
                            "numbering_record": record,
                        }
                    )
                    offset = end
                if len(levels) == 7 and offset + 2 <= len(data):
                    start = struct.unpack_from("<H", data, offset)[0]
                    for index, item in enumerate(levels):
                        item["numbering_start_number"] = start
                        if version >= 0x05000205 and offset + 2 + 4 * len(
                            levels
                        ) <= len(data):
                            item["level_start_number"] = struct.unpack_from(
                                "<I", data, offset + 2 + index * 4
                            )[0]
            except (struct.error, UnicodeError, ValueError):
                levels = []
            numberings.append(levels)
        elif tag == 0x18:
            marker = {"numbering_record": record}
            marker_text = ""
            if len(data) >= 14:
                with suppress(UnicodeError):
                    marker_text = data[12:14].decode("utf-16-le")
            if len(data) >= 18:
                image_bullet_flag = struct.unpack_from("<I", data, 14)[0]
                if image_bullet_flag == 0:
                    if marker_text:
                        marker["marker_text"] = marker_text
                elif len(data) >= 22:
                    marker_image = {
                        "image_bullet_flag": image_bullet_flag,
                        "contrast": data[18],
                        "brightness": data[19],
                        "effect": data[20],
                        "binary_item_ref": data[21],
                    }
                    if marker_text:
                        marker_image["fallback_text"] = marker_text
                    if len(data) >= 24:
                        with suppress(UnicodeError):
                            check_text = data[22:24].decode("utf-16-le")
                            if check_text and check_text != "\0":
                                marker_image["check_marker_text"] = check_text
                    marker["marker_image"] = marker_image
            bullets.append(marker)
        elif tag == 0x19:
            if len(data) < 32:
                shapes.append({"properties_unavailable": True})
                continue
            flags = struct.unpack_from("<I", data)[0]
            kind = (flags >> 23) & 3
            level = (flags >> 25) & 7
            # Newer HWP5 writers also store outline levels 8--10 in the tail.
            if len(data) >= 58:
                tail = struct.unpack_from("<I", data, 54)[0]
                if 7 <= tail <= 9 and level == 6:
                    level = tail
            shapes.append(
                {
                    "head_type": ("none", "outline", "number", "bullet")[kind],
                    "level": level + 1,
                    "numbering_ref": struct.unpack_from("<H", data, 30)[0],
                    "properties_record": record,
                }
            )
        elif tag == 0x1A:
            try:
                length = struct.unpack_from("<H", data)[0]
                end = 2 + length * 2
                if end > len(data):
                    raise ValueError("truncated style")
                name = data[2:end].decode("utf-16-le")
                styles.append({"name": name})
            except (struct.error, UnicodeError, ValueError):
                styles.append({})
    for shape in shapes:
        ref = shape.get("numbering_ref", 0) - 1
        level = shape.get("level", 1) - 1
        if shape.get("head_type") == "bullet" and 0 <= ref < len(bullets):
            shape.update(bullets[ref])
        elif (
            shape.get("head_type") in {"outline", "number"}
            and 0 <= ref < len(numberings)
            and level < len(numberings[ref])
        ):
            shape.update(numberings[ref][level])
    return shapes, styles


class SectionStructure:
    """Track record ownership while preserving the native text observations."""

    def __init__(
        self,
        section: int,
        stream: str,
        shapes: list[dict],
        styles: list[dict],
        *,
        images: list[dict] | None = None,
        version: int = 0,
    ):
        self.base = {"section": section, "section_stream": stream}
        self.shapes = shapes
        self.styles = styles
        self.images = images or []
        self.version = version
        self.paragraphs: dict[int, dict] = {}
        self.controls: dict[int, dict] = {}
        self.lists: dict[int, dict] = {}
        self.units: list[dict] = []
        self.issues: Counter[str] = Counter()
        self.tables: list[dict] = []
        self.counts: Counter[str] = Counter()
        self.pending_memo: tuple[int, int, int] | None = None
        self.field_events: list[dict] = []
        self.field_units: list[dict] = []
        self.objects: list[dict] = []

    def _unit(self, kind: str, locator: dict, text: str = "") -> dict:
        unit = {
            "unit_type": kind,
            "structure_path": {**self.base, **locator},
            "content": text,
            "derivation_method": "native_text",
            "quality_flags": ["binary_hwp", "reading_order_unverified"],
            "issues": [],
        }
        self.units.append(unit)
        return unit

    def _context(self, level: int) -> dict:
        containers = [
            item["locator"] for key, item in sorted(self.lists.items()) if key <= level
        ]
        result: dict = {"container_path": containers} if containers else {}
        for container in containers:
            for key in (
                "table",
                "cell",
                "row",
                "col",
                "row_span",
                "col_span",
                "is_header",
                "note",
                "memo_list_record",
                "memo_header_value",
                "object",
                "owner_paragraph_record",
            ):
                if key in container:
                    result[key] = container[key]
            result["container_kind"] = container["kind"]
        return result

    def observe(self, record: int, tag: int, level: int, data: bytes) -> None:
        if level > 64:
            raise ValueError("HWP structure exceeds its nesting budget")
        memo = self.pending_memo if tag == 0x48 else None
        self.pending_memo = None
        if tag == 0x5D:
            self.counts["memo_header"] += 1
            # Preserve the stored value. Attachment is resolved only by a unique
            # matching MEMO end token, never by list order or record proximity.
            if len(data) == 4:
                self.pending_memo = (record, level, struct.unpack_from("<I", data)[0])
            else:
                self.issues["hwp_memo_structure_partial"] += 1
        if tag == 0x42:
            self.paragraphs = {k: v for k, v in self.paragraphs.items() if k < level}
            self.controls = {k: v for k, v in self.controls.items() if k < level}
            self.lists = {k: v for k, v in self.lists.items() if k <= level}
            context = self._context(level)
            self.paragraphs[level] = {
                "paragraph_record": record,
                **context,
                **paragraph_properties(data, self.shapes, self.styles),
            }
            if self.paragraphs[level].get("properties_unavailable"):
                self.issues["hwp_paragraph_properties_partial"] += 1
        elif tag == 0x47:
            self.controls = {k: v for k, v in self.controls.items() if k < level}
            self.lists = {k: v for k, v in self.lists.items() if k <= level}
            if len(data) < 4:
                self.issues["hwp_control_structure_partial"] += 1
                return
            name = data[:4][::-1].decode("ascii", errors="replace")
            owner = self.paragraphs.get(level - 1, {})
            kind = _CONTROL_KINDS.get(name)
            context = {
                **self._context(level),
                "record": record,
                "control_type": name,
                "owner_paragraph_record": owner.get("paragraph_record"),
            }
            control = {
                "name": name,
                "record": record,
                "level": level,
                "context": context,
                "table": None,
                "object_kinds": Counter(),
                "content_observed": False,
            }
            self.controls[level] = control
            if name in {"atno", "nwno"}:
                if len(data) < 10:
                    self.issues["hwp_number_control_partial"] += 1
                else:
                    flags, number = struct.unpack_from("<IH", data, 4)
                    number_kind = flags & 15
                    location = {
                        **context,
                        "field_type": "auto_number" if name == "atno" else "new_number",
                        "stored_number": number,
                        "number_type_code": number_kind,
                        "number_origin": "stored_control_value",
                    }
                    kinds = (
                        "page",
                        "footnote",
                        "endnote",
                        "picture",
                        "table",
                        "equation",
                    )
                    if number_kind < len(kinds):
                        location["number_type"] = kinds[number_kind]
                    if name == "atno":
                        location["number_format_code"] = (flags >> 4) & 255
                        if len(data) >= 16:
                            location["number_decoration_codepoints"] = list(
                                struct.unpack_from("<3H", data, 10)
                            )
                    self._unit("field", location)
                    self.counts["stored_number"] += 1
            elif kind:
                context["object"] = f"r{record}"
                if kind == "table":
                    context["table"] = f"r{record}"
                elif kind in {"footnote", "endnote"}:
                    context["note"] = f"r{record}"
                control["unit"] = self._unit(
                    kind if kind != "comment" else "embedded_object", context
                )
                self.counts[kind] += 1
                if kind == "embedded_object":
                    self.objects.append(control)
            elif name.startswith("%"):
                self.counts["field"] += 1
                try:
                    length = struct.unpack_from("<H", data, 9)[0]
                    end = 11 + length * 2
                    if end + 4 > len(data):
                        raise ValueError("truncated field")
                    command = data[11:end].decode("utf-16-le")
                    field_type = _FIELD_KINDS.get(name, "unknown")
                    type_origin = "control_id"
                    if name == "%unk" and command.partition("/")[0] == "MEMO":
                        field_type = "memo"
                        type_origin = "stored_command"
                    if len(command) > 16_384:
                        self.issues["hwp_field_metadata_truncated"] += 1
                    field = self._unit(
                        "field",
                        {
                            **context,
                            "field_type": field_type,
                            "field_type_origin": type_origin,
                            "field_id": struct.unpack_from("<I", data, end)[0],
                            "field_flags": struct.unpack_from("<I", data, 4)[0],
                            "instruction": command[:16_384],
                            "evaluation": "stored_result_only",
                        },
                    )
                    if end + 8 <= len(data):
                        field["structure_path"]["field_header_tail_value"] = (
                            struct.unpack_from("<I", data, end + 4)[0]
                        )
                    self.field_units.append(field)
                    if field_type == "unknown":
                        self.issues["hwp_field_semantics_partial"] += 1
                except (struct.error, UnicodeError, ValueError):
                    self.issues["hwp_field_structure_partial"] += 1
        elif tag in _SHAPE_KINDS:
            control = next(
                (
                    v
                    for k, v in sorted(self.controls.items(), reverse=True)
                    if k < level
                ),
                None,
            )
            if control and "unit" in control:
                shape_kind = _SHAPE_KINDS[tag]
                control["object_kinds"][shape_kind] += 1
                observations = control["unit"]["structure_path"].setdefault(
                    "object_records", []
                )
                if len(observations) < 256:
                    observations.append({"record": record, "kind": shape_kind})
                else:
                    self.issues["hwp_object_structure_partial"] += 1
            if tag == 0x55:
                if control:
                    control["content_observed"] = True
                self._unit(
                    "embedded_object",
                    {
                        **(control["context"] if control else self._context(level)),
                        "record": record,
                        "image_record": record,
                        **hwp_picture(data, self.images, version=self.version),
                    },
                )
        elif tag == 0x58:
            control = next(
                (
                    v
                    for k, v in sorted(self.controls.items(), reverse=True)
                    if k < level
                ),
                None,
            )
            if control and control["name"] == "eqed" and len(data) >= 6:
                length = struct.unpack_from("<H", data, 4)[0]
                if 6 + length * 2 <= len(data):
                    try:
                        script = data[6 : 6 + length * 2].decode("utf-16-le")
                        control["content_observed"] = True
                        self._unit(
                            "embedded_object",
                            {
                                **control["context"],
                                "record": record,
                                "object": f"r{control['record']}",
                                "text_representation": "hwp_equation_script",
                            },
                            script,
                        )
                    except UnicodeError:
                        self.issues["hwp_equation_text_partial"] += 1
                else:
                    self.issues["hwp_equation_text_partial"] += 1
        elif tag == 0x4D:
            control = next(
                (
                    v
                    for k, v in sorted(self.controls.items(), reverse=True)
                    if k < level and v["name"] == "tbl "
                ),
                None,
            )
            if control is None or len(data) < 8:
                self.issues["hwp_table_structure_partial"] += 1
                return
            rows, cols = struct.unpack_from("<HH", data, 4)
            table = {"rows": rows, "cols": cols, "cells": [], "record": record}
            control["table"] = table
            self.tables.append(table)
            control["unit"]["structure_path"].update(
                {"rows": rows, "cols": cols, "table_record": record}
            )
            if not rows or not cols:
                self.issues["hwp_table_structure_partial"] += 1
        elif tag == 0x48:
            self.paragraphs = {k: v for k, v in self.paragraphs.items() if k < level}
            self.controls = {k: v for k, v in self.controls.items() if k < level}
            self.lists = {k: v for k, v in self.lists.items() if k < level}
            control = next(
                (
                    v
                    for k, v in sorted(self.controls.items(), reverse=True)
                    if k < level
                ),
                None,
            )
            kind = (
                _CONTROL_KINDS.get(control["name"], "unknown") if control else "unknown"
            )
            if control and control["name"] == "secd":
                # HWP5 table 129: section-owned paragraph lists are master pages.
                # Keep ownership only; the list does not establish rendered pages.
                kind = "master_page"
                self.counts["master_page"] += 1
            locator = {"kind": kind, "list_record": record}
            if control is None and memo is not None and memo[1] == level:
                kind = "comment"
                locator.update(
                    {
                        "kind": kind,
                        "memo_list_record": memo[0],
                        "memo_header_value": memo[2],
                        "note": f"memo.r{memo[0]}",
                    }
                )
                self.counts["memo"] += 1
                self.issues["hwp_memo_attachment_unresolved"] += 1
            if control:
                locator["object"] = f"r{control['record']}"
                locator["owner_paragraph_record"] = control["context"].get(
                    "owner_paragraph_record"
                )
            if kind == "table":
                locator["table"] = f"r{control['record']}"
                table = control["table"]
                if table is None:
                    locator["kind"] = "caption"
                elif len(data) >= 34:
                    col, row, col_span, row_span = struct.unpack_from("<HHHH", data, 8)
                    locator.update(
                        {
                            "kind": "table_cell",
                            "cell": f"r{record}",
                            "row": row,
                            "col": col,
                            "row_span": row_span,
                            "col_span": col_span,
                            "is_header": bool(struct.unpack_from("<H", data, 6)[0] & 4),
                        }
                    )
                    table["cells"].append(locator)
                    self.counts["cell"] += 1
                else:
                    locator["kind"] = "unknown"
                    self.issues["hwp_table_structure_partial"] += 1
            elif kind in {"footnote", "endnote"}:
                locator["note"] = f"r{control['record']}"
            elif kind == "embedded_object":
                locator["kind"] = "caption" if len(data) == 22 else "textbox"
            elif kind == "unknown":
                self.issues["hwp_container_structure_partial"] += 1
            self.lists[level] = {"locator": locator}
            self._unit(
                locator["kind"]
                if locator["kind"] in {"table_cell", "comment"}
                else "embedded_object",
                {**self._context(level), "record": record, "structural_only": True},
            )

    def fields(self, record: int, level: int, markers: list[dict]) -> None:
        properties = self.paragraphs.get(level - 1, {})
        flow = tuple(
            item["list_record"] for item in properties.get("container_path", [])
        )
        for marker in markers:
            self.field_events.append(
                {
                    **marker,
                    "record": record,
                    "paragraph_record": properties.get("paragraph_record"),
                    "flow": flow,
                }
            )

    def _resolve_field_ranges(self) -> None:
        headers = defaultdict(list)
        for unit in self.field_units:
            location = unit["structure_path"]
            kind = location["control_type"]
            if location["field_type"] == "memo":
                kind = "%%me"
            headers[(location["owner_paragraph_record"], kind)].append(unit)
        starts = Counter(
            (event["paragraph_record"], event["control_type"])
            for event in self.field_events
            if event["code"] == 3
        )
        stacks = defaultdict(list)
        unresolved_markers = 0

        def position(event, *, after_control=False):
            return {
                "record": event["record"],
                "paragraph_record": event["paragraph_record"],
                "offset_utf16": event["offset_utf16"] + (8 if after_control else 0),
                "content_offset": event["content_offset"],
            }

        for event in self.field_events:
            stack = stacks[event["flow"]]
            if event["code"] == 3:
                key = (event["paragraph_record"], event["control_type"])
                candidates = headers[key]
                # Repeated same-type headers have no inline instance ID. Leave
                # them unresolved rather than assigning IDs by adjacency/order.
                unit = (
                    candidates[0]
                    if key[0] is not None and starts[key] == len(candidates) == 1
                    else None
                )
                stack.append((event, unit))
            elif (
                not stack
                or stack[-1][0]["control_type"][-3:] != event["control_type"][-3:]
            ):
                unresolved_markers += 1
                stack.clear()
            else:
                start, unit = stack.pop()
                if unit is None:
                    unresolved_markers += 1
                    continue
                location = unit["structure_path"]
                location["field_range"] = {
                    "start": position(start, after_control=True),
                    "end": position(event),
                    "start_control": position(start),
                    "basis": "unique_paragraph_control_and_balanced_native_markers",
                    "content_offset_unit": "unicode_codepoint_in_normalized_paragraph",
                }
                if location["field_type"] == "memo" and event["end_token"] > 0:
                    location["memo_end_token"] = event["end_token"]
                self.counts["field_range"] += 1
        unresolved = sum(
            "field_range" not in unit["structure_path"] for unit in self.field_units
        )
        unresolved += unresolved_markers + sum(len(stack) for stack in stacks.values())
        if unresolved:
            self.issues["hwp_field_range_partial"] += unresolved

    def text(self, record: int, level: int, paragraph: int, text: str) -> None:
        properties = dict(self.paragraphs.get(level - 1, {}))
        if not properties:
            self.issues["hwp_paragraph_structure_partial"] += 1
        kind = properties.get("container_kind", "section_paragraph")
        if kind not in STRUCTURAL_UNIT_TYPES:
            kind = "section_paragraph"
        head_type = properties.get("head_type")
        if head_type == "outline":
            kind = "heading"
        elif head_type in {"number", "bullet"}:
            kind = "list_item"
            marker_image = properties.get("marker_image")
            if isinstance(marker_image, Mapping):
                marker_image = dict(marker_image)
                reference = marker_image.get("binary_item_ref")
                if isinstance(reference, int) and 0 < reference <= len(self.images):
                    marker_image.update(self.images[reference - 1])
                properties["marker_image"] = marker_image
            # Numbering definitions are referenced, never guessed into source text.
            if not properties.get("marker_text") and not (
                isinstance(marker_image, Mapping) and marker_image.get("image_parts")
            ):
                self.issues["hwp_list_marker_partial"] += 1
        self._unit(
            kind,
            {
                **properties,
                "record": record,
                "record_level": level,
                "paragraph": paragraph,
            },
            text,
        )

    def finish(self) -> tuple[list[dict], list[dict]]:
        self._resolve_field_ranges()
        for control in self.objects:
            kinds = set(control["object_kinds"])
            if control["name"] == "eqed":
                if not control["content_observed"]:
                    self.issues["hwp_equation_content_unread"] += 1
                continue
            if kinds & _UNREAD_EMBEDDED_OBJECTS or control["name"] == "form":
                self.issues["hwp_embedded_object_content_unread"] += 1
            if kinds & _PRIMITIVE_SHAPES:
                self.issues["hwp_shape_layout_unverified"] += 1
            if not kinds and control["name"] == "gso ":
                self.issues["hwp_object_type_unresolved"] += 1
        if self.counts["memo_header"] != self.counts["memo"]:
            self.issues["hwp_memo_structure_partial"] += abs(
                self.counts["memo_header"] - self.counts["memo"]
            )
        if self.counts["table"] != len(self.tables):
            self.issues["hwp_table_structure_partial"] += abs(
                self.counts["table"] - len(self.tables)
            )
        for table in self.tables:
            comparisons = 0
            active: list[dict] = []
            for cell in sorted(table["cells"], key=lambda c: (c["row"], c["col"])):
                row, col, height, width = (
                    cell[k] for k in ("row", "col", "row_span", "col_span")
                )
                if (
                    height < 1
                    or width < 1
                    or row + height > table["rows"]
                    or col + width > table["cols"]
                ):
                    self.issues["hwp_table_geometry_partial"] += 1
                active = [c for c in active if c["row"] + c["row_span"] > row]
                comparisons += len(active)
                if comparisons > 1_000_000:
                    self.issues["hwp_table_geometry_partial"] += 1
                    break
                if any(
                    col < c["col"] + c["col_span"] and col + width > c["col"]
                    for c in active
                ):
                    self.issues["hwp_table_geometry_partial"] += 1
                active.append(cell)
            if not table["cells"]:
                self.issues["hwp_table_structure_partial"] += 1
        issues = [
            {
                "code": code,
                "severity": "warning",
                "message": _OBJECT_ISSUE_MESSAGES.get(
                    code,
                    "Some HWP structure could not be fully reconstructed.",
                ),
                "details": {**self.base, "occurrences": count},
            }
            for code, count in sorted(self.issues.items())
        ]
        issues.append(
            {
                "code": "hwp_structure_observed",
                "severity": "info",
                "message": (
                    "Native HWP record ownership was observed without inferring page layout."
                ),
                "details": {**self.base, **dict(self.counts)},
            }
        )
        return self.units, issues


def link_document_memos(units: list[dict], issues: list[dict]) -> list[dict]:
    """Join only unique positive stored tokens, including across section streams."""
    fields = defaultdict(list)
    memos = defaultdict(dict)
    for unit in units:
        location = unit["structure_path"]
        if unit["unit_type"] == "field" and location.get("memo_end_token", 0) > 0:
            fields[location["memo_end_token"]].append(location)
        if location.get("memo_header_value", 0) > 0:
            key = (location["section"], location["memo_list_record"])
            memos[location["memo_header_value"]].setdefault(key, []).append(location)
    resolved = Counter()
    for token, targets in memos.items():
        if len(targets) != 1 or len(fields[token]) != 1:
            continue
        (section, record), locations = next(iter(targets.items()))
        field = fields[token][0]
        attachment = {
            "section": field["section"],
            "section_stream": field["section_stream"],
            "field_record": field["record"],
            "field_id": field["field_id"],
            "field_range": field["field_range"],
            "basis": "unique_memo_end_token_matches_memo_header_value",
        }
        for location in locations:
            location["memo_attachment"] = attachment
        field["memo_body"] = {"section": section, "memo_list_record": record}
        resolved[section] += 1
    result = []
    for issue in issues:
        if issue["code"] == "hwp_memo_attachment_unresolved":
            details = issue["details"]
            remaining = details["occurrences"] - resolved[details["section"]]
            if remaining <= 0:
                continue
            issue = {**issue, "details": {**details, "occurrences": remaining}}
        result.append(issue)
    if resolved:
        result.append(
            {
                "code": "hwp_memo_attachment_observed",
                "severity": "info",
                "message": (
                    "Unique native MEMO tokens link the field range to its stored memo body."
                ),
                "details": {"occurrences": sum(resolved.values())},
            }
        )
    return result
