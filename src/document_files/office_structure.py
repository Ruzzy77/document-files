"""Native OOXML text in package order, with stable part and element locators.

No external relationships, linked workbooks, macros or field commands are opened.
XML order is not a claim about floating-object or slide visual reading order.
"""

from __future__ import annotations

import json
import posixpath
import zipfile
from collections import Counter, defaultdict

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
from .list_numbering import (
    OOXML_MARKER,
    OOXML_NUMBER_FORMATS,
    ListCounters,
    scheme_label,
)

DOCX_UNITS = (
    "heading",
    "paragraph",
    "list_item",
    "table",
    "table_cell",
    "header",
    "footer",
    "footnote",
    "endnote",
    "comment",
    "embedded_object",
    "field",
)
PPTX_UNITS = (
    "slide_text",
    "speaker_notes",
    "table",
    "table_cell",
    "chart_data",
    "diagram_text",
    "embedded_object",
)
_IMAGE_REFERENCES = {"blip", "imagedata"}
_GROUP_ELEMENTS = {"wgp", "grpSp", "group"}
_MAX_GROUP_IMAGES = 256
_ISSUE_MESSAGES = {
    "pptx_table_merge_content_observed": (
        "Stored continuation-cell text was linked to its explicit merge origin."
    ),
    "office_image_not_displayed_observed": (
        "A picture instance declares its own zero display area in the source."
    ),
}


def _attr(node, name, default=None):
    return next((v for k, v in node.attrib.items() if _local_name(k) == name), default)


def _child(node, name):
    return next((c for c in node if _local_name(c.tag) == name), None)


def _desc(node, name):
    return (c for c in node.iter() if _local_name(c.tag) == name)


def _integer(value, default=0):
    if value is None:
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ExtractionError("OOXML structural integer is invalid") from exc
    if not 0 <= result <= 1_000_000:
        raise ExtractionError("OOXML structural integer exceeds its bound")
    return result


def _value(node, child, default=None):
    element = _child(node, child) if node is not None else None
    return _attr(element, "val", default) if element is not None else default


def _addressed_path(node, address, names):
    """Follow one unambiguous child path, returning the element and its address."""
    for name in names:
        found = None
        for index, child in enumerate(node):
            if _local_name(child.tag) != name:
                continue
            if found is not None:
                return None, None
            found = child, f"{address}.{index}"
        if found is None:
            return None, None
        node, address = found
    return node, address


def _extent(node):
    """Return the stored display extent, or None when it is not a plain integer."""
    if node is None:
        return None
    values = {}
    for name in ("cx", "cy"):
        try:
            values[name] = int(_attr(node, name))
        except (TypeError, ValueError):
            return None
    return values


def _transform_value(node, address):
    return {
        "element": address,
        "attributes": dict(node.attrib),
        **{_local_name(child.tag): dict(child.attrib) for child in node},
        "coordinate_space": "parent_group_emu",
    }


def _own_transform(node, address):
    """Return only the transform this element declares for itself."""
    for index, child in enumerate(node):
        name = _local_name(child.tag)
        if name == "xfrm":
            return _transform_value(child, f"{address}.{index}")
        if name in {"spPr", "grpSpPr"}:
            for inner, grandchild in enumerate(child):
                if _local_name(grandchild.tag) == "xfrm":
                    return _transform_value(grandchild, f"{address}.{index}.{inner}")
    return None


def _own_descriptions(node):
    """Title and description from this element's own non-visual properties."""
    values = []
    properties = []
    for child in node:
        name = _local_name(child.tag)
        if name in {"docPr", "cNvPr"}:
            properties.append(child)
        elif name.startswith("nv"):
            properties.extend(
                c for c in child if _local_name(c.tag) in {"docPr", "cNvPr"}
            )
    for prop in properties:
        for key in ("title", "descr"):
            value = prop.get(key)
            if value and value not in values:
                values.append(value)
    return properties, values


def _crop_values(crop):
    """Keep only a crop the source actually declares as non-zero."""
    if crop is None:
        return []
    attributes = crop["attributes"]
    declared = any(value not in {"0", "0.0"} for value in attributes.values())
    return [attributes] if declared else []


def _level_definition(node):
    """Keep one numbering level exactly as the package declares it."""
    return {
        "marker_pattern": _value(node, "lvlText", ""),
        "number_format": _value(node, "numFmt", ""),
        "start": _value(node, "start"),
        "restart": _value(node, "lvlRestart"),
        "legal": _child(node, "isLgl") is not None,
    }


def _numbering_ambiguity(node):
    """Revision marks on the paragraph mark leave its list position uncertain."""
    direct = _child(node, "pPr")
    if direct is None:
        return False
    if _child(direct, "pPrChange") is not None:
        return True
    marks = _child(direct, "rPr")
    return marks is not None and any(
        _local_name(child.tag) in {"del", "numberingChange"} for child in marks
    )


def _children(node):
    selected = None
    if _local_name(node.tag) == "AlternateContent":
        selected = _child(node, "Choice")
        if selected is None:
            selected = _child(node, "Fallback")
        if selected is None:
            return
    for index, child in enumerate(node):
        if selected is None or child is selected:
            yield index, child


class _Package:
    def __init__(self, archive):
        self.archive = archive
        self.names = set(archive.namelist())
        if len(self.names) != len(archive.namelist()):
            raise ExtractionError("OOXML package contains duplicate members")
        self.relationships = {}

    def xml(self, part):
        return _safe_archive_xml_root(self.archive, part)

    def rels(self, part):
        if part not in self.relationships:
            name = (
                posixpath.join(
                    posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels"
                )
                if part
                else "_rels/.rels"
            )
            result = {}
            if name in self.names:
                for node in self.xml(name):
                    if _local_name(node.tag) != "Relationship":
                        continue
                    target = node.get("Target", "")
                    resolved = posixpath.normpath(
                        target.lstrip("/")
                        if target.startswith("/")
                        else posixpath.join(posixpath.dirname(part), target)
                    )
                    external = (
                        node.get("TargetMode") == "External"
                        or ":" in target
                        or "\\" in target
                        or resolved.startswith("../")
                    )
                    result[node.get("Id")] = {
                        "type": node.get("Type", "").rsplit("/", 1)[-1],
                        "part": resolved
                        if not external and resolved in self.names
                        else None,
                    }
            self.relationships[part] = result
        return self.relationships[part]

    def related(self, part, identifier):
        return self.rels(part).get(identifier, {}).get("part")

    def main(self, fallback):
        return next(
            (
                r["part"]
                for r in self.rels("").values()
                if r["type"] == "officeDocument" and r["part"]
            ),
            fallback,
        )


class _Reader:
    def __init__(self, package):
        self.package = package
        self.units = []
        self.issues = Counter()
        self.characters = 0
        self.nodes = 0

    def emit(self, kind, location, text=""):
        text = normalize_text(text)
        self.characters += len(text)
        if self.characters > 50_000_000 or len(self.units) >= 200_000:
            raise ExtractionError("OOXML extraction exceeds its output budget")
        unit = UnitDraft(kind, location, text)
        self.units.extend(_bounded_unit(unit) if text else [unit])
        if len(self.units) > 200_000:
            raise ExtractionError("OOXML extraction exceeds its unit budget")

    def guard(self, depth):
        self.nodes += 1
        if depth > 64 or self.nodes > 2_000_000:
            raise ExtractionError("OOXML extraction exceeds its traversal budget")

    def addressed(self, node, address, depth):
        self.guard(depth)
        yield node, address, depth
        for index, child in enumerate(node):
            yield from self.addressed(child, f"{address}.{index}", depth + 1)

    def drawing_images(self, node, location, depth):
        """Collect each stored image reference with only its own shape context.

        Explicit `AlternateContent` selection is preserved, so the primary and
        the fallback representation of one drawing are never both extracted.
        """
        found, records = [], []

        def record(element, address, *, root=False):
            properties, descriptions = _own_descriptions(element)
            if not properties and not root:
                return None
            item = {
                "element": address,
                "descriptions": descriptions,
                "transform": _own_transform(element, address),
                "count": 0,
            }
            records.append(item)
            return item

        def scan(element, address, level, chain, groups, crop):
            self.guard(level)
            name = _local_name(element.tag)
            if name in _IMAGE_REFERENCES:
                identifier = _attr(element, "embed") or _attr(element, "id")
                found.append(
                    {
                        "element": address,
                        "relationship": identifier,
                        "part": self.package.related(location["part"], identifier),
                        "chain": chain,
                        "groups": list(groups),
                        "crop": crop,
                    }
                )
                for item in chain:
                    item["count"] += 1
                return
            if name == "blipFill":
                crop = next(
                    (
                        {
                            "element": f"{address}.{index}",
                            "attributes": dict(child.attrib),
                        }
                        for index, child in enumerate(element)
                        if _local_name(child.tag) == "srcRect"
                    ),
                    None,
                )
            elif element is not node:
                item = record(element, address)
                if item is not None:
                    chain = (*chain, item)
            if name in _GROUP_ELEMENTS:
                groups = (*groups, address)
            for index, child in _children(element):
                scan(child, f"{address}.{index}", level + 1, chain, groups, crop)

        address = location["element"]
        scan(node, address, depth, (record(node, address, root=True),), (), None)
        return found, records

    def image(self, node, location, depth=0):
        found, records = self.drawing_images(node, location, depth)
        if len(found) > _MAX_GROUP_IMAGES:
            self.issues["office_image_group_structure_partial"] += 1
            found = found[:_MAX_GROUP_IMAGES]
        if len(found) > 1:
            self.group_image(location, found, records)
            return
        reference = found[0] if found else None
        chain = reference["chain"] if reference is not None else records
        self.emit_image(
            {
                **location,
                "image_parts": [reference["part"]]
                if reference is not None and reference["part"]
                else [],
                "source_crops": _crop_values(
                    reference["crop"] if reference is not None else None
                ),
                **(
                    {"image_reference_element": reference["element"]}
                    if reference is not None
                    else {}
                ),
            },
            [text for item in chain for text in item["descriptions"]],
        )

    def group_image(self, location, found, records):
        """Expose each child image separately while keeping the group object."""
        self.emit(
            "embedded_object",
            {
                **location,
                "object_type": "image_group",
                "image_count": len(found),
                "text_representation": "source_alternative_text",
            },
            "\n".join(
                dict.fromkeys(
                    text
                    for item in records
                    if item["count"] > 1
                    for text in item["descriptions"]
                )
            ),
        )
        for reference in found:
            shape = reference["chain"][-1]
            self.emit_image(
                {
                    **location,
                    "element": reference["element"],
                    "image_parts": [reference["part"]] if reference["part"] else [],
                    "source_crops": _crop_values(reference["crop"]),
                    "image_reference_element": reference["element"],
                    "image_shape": shape["element"],
                    "image_group": location["element"],
                    **(
                        {"image_group_path": reference["groups"]}
                        if reference["groups"]
                        else {}
                    ),
                    **(
                        {"source_transform": shape["transform"]}
                        if shape["transform"]
                        else {}
                    ),
                },
                [
                    text
                    for item in reference["chain"]
                    if item["count"] == 1
                    for text in item["descriptions"]
                ],
            )

    def emit_image(self, location, descriptions):
        self.emit(
            "embedded_object",
            {
                **location,
                "object_type": "image",
                "text_representation": "source_alternative_text",
            },
            "\n".join(dict.fromkeys(descriptions)),
        )
        if location.get("display_state") == "not_displayed":
            self.issues["office_image_not_displayed_observed"] += 1
        else:
            self.issues["office_image_content_unread"] += 1

    def finish(self):
        default = "A source-declared Office feature needs additional extraction."
        issues = [
            {
                "code": code,
                "severity": "info" if code.endswith("_observed") else "warning",
                "message": _ISSUE_MESSAGES.get(code, default),
                "details": {"occurrences": count},
            }
            for code, count in sorted(self.issues.items())
        ]
        if not any(u.content for u in self.units):
            issues.append(
                {
                    "code": "no_extractable_text",
                    "severity": "warning",
                    "message": "No native text was found.",
                }
            )
        return ExtractionResult(self.units, issues)


class _WordReader(_Reader):
    def __init__(self, package, main):
        super().__init__(package)
        self.main = main
        self.styles = {}
        self.numberings = {}
        self.references = {}
        self.numbering_events = []
        for relation in package.rels(main).values():
            if relation["type"] == "styles" and relation["part"]:
                for style in _desc(package.xml(relation["part"]), "style"):
                    self.styles[_attr(style, "styleId")] = style
            elif relation["type"] == "numbering" and relation["part"]:
                self._numbering(package.xml(relation["part"]))

    def _numbering(self, root):
        abstract = {}
        for node in root:
            if _local_name(node.tag) == "abstractNum":
                abstract[_attr(node, "abstractNumId")] = {
                    "levels": {
                        _integer(_attr(level, "ilvl")) + 1: _level_definition(level)
                        for level in node
                        if _local_name(level.tag) == "lvl"
                    },
                    "linked": _child(node, "numStyleLink") is not None
                    or _child(node, "styleLink") is not None,
                }
        for node in root:
            if _local_name(node.tag) != "num":
                continue
            reference = _value(node, "abstractNumId")
            definition = abstract.get(reference)
            levels = dict(definition["levels"]) if definition is not None else {}
            overrides = False
            for child in node:
                if _local_name(child.tag) != "lvlOverride":
                    continue
                overrides = True
                index = _integer(_attr(child, "ilvl")) + 1
                replacement = _child(child, "lvl")
                start = _value(child, "startOverride")
                level = (
                    _level_definition(replacement)
                    if replacement is not None
                    else dict(levels.get(index, {}))
                )
                if start is not None:
                    level.update({"start": start, "start_origin": "start_override"})
                levels[index] = level
            self.numberings[_attr(node, "numId")] = {
                "abstract": reference,
                "levels": levels,
                "linked": definition is None or definition["linked"],
                "overrides": overrides,
            }

    def paragraph_properties(self, node):
        direct = _child(node, "pPr")
        style_id = _value(direct, "pStyle")
        properties = [direct] if direct is not None else []
        current, seen = style_id, set()
        while current in self.styles and current not in seen and len(seen) < 32:
            seen.add(current)
            style = self.styles[current]
            prop = _child(style, "pPr")
            if prop is not None:
                properties.append(prop)
            current = _value(style, "basedOn")
        result = {"style": style_id} if style_id else {}
        outline = next(
            (
                _value(p, "outlineLvl")
                for p in properties
                if _value(p, "outlineLvl") is not None
            ),
            None,
        )
        if outline is not None and _integer(outline) < 9:
            result.update({"head_type": "outline", "level": _integer(outline) + 1})
        num_props = [_child(p, "numPr") for p in properties]
        num_id = next(
            (_value(p, "numId") for p in num_props if _value(p, "numId") is not None),
            None,
        )
        level = next(
            (_value(p, "ilvl") for p in num_props if _value(p, "ilvl") is not None), "0"
        )
        if num_id not in {None, "0"}:
            result.update({"numbering_ref": num_id, "list_level": _integer(level)})
            definition = self.numberings.get(num_id, {}).get("levels", {})
            declared = definition.get(_integer(level) + 1, {})
            result.update(
                {
                    key: declared[key]
                    for key in ("marker_pattern", "number_format")
                    if key in declared
                }
            )
            if result.get("number_format") == "bullet":
                result["marker_text"] = result.get("marker_pattern", "")
        return result

    def paragraph(self, node, location, depth):
        props = self.paragraph_properties(node)
        location = {**location, **props, "paragraph_element": location["element"]}
        kind = (
            "heading"
            if props.get("head_type") == "outline"
            else (
                "list_item"
                if "numbering_ref" in props
                else location.get("container_kind", "paragraph")
            )
        )
        if kind not in DOCX_UNITS:
            kind = "paragraph"
        chunks, segment = [], 0
        event = None
        if "numbering_ref" in props:
            event = {
                "numbering_ref": props["numbering_ref"],
                "level": props.get("list_level", 0) + 1,
                "bullet": props.get("number_format") == "bullet",
                # Only the main story is walked in its own displayed order here.
                "resolvable": location["part"] == self.main
                and "object" not in location
                and not _numbering_ambiguity(node),
                "units": [],
            }
            self.numbering_events.append(event)

        def flush():
            nonlocal segment
            if normalize_text("".join(chunks)):
                segment += 1
                emitted = len(self.units)
                self.emit(kind, {**location, "segment": segment}, "".join(chunks))
                if event is not None:
                    event["units"].extend(self.units[emitted:])
            chunks.clear()

        def inline(element, address, level):
            self.guard(level)
            name = _local_name(element.tag)
            if name == "t":
                chunks.append(element.text or "")
                return
            if name in {"tab", "br", "cr", "noBreakHyphen", "softHyphen"}:
                chunks.append(
                    {
                        "tab": "\t",
                        "br": "\n",
                        "cr": "\n",
                        "noBreakHyphen": "\u2011",
                        "softHyphen": "\u00ad",
                    }[name]
                )
                return
            if name in {"del", "moveFrom"}:
                self.issues["docx_tracked_changes_present"] += 1
                return
            if name in {
                "drawing",
                "pict",
                "object",
                "txbxContent",
                "oMath",
                "oMathPara",
            }:
                flush()
                self.walk(
                    element,
                    {
                        **location,
                        "element": address,
                        "owner_paragraph": location["paragraph_element"],
                    },
                    level,
                )
                return
            if name in {"instrText", "fldChar", "fldSimple"}:
                flush()
                instruction = (
                    (element.text or "")
                    if name == "instrText"
                    else _attr(element, "instr", "")
                )
                if len(instruction) > 16_384:
                    self.issues["docx_field_instruction_truncated"] += 1
                self.emit(
                    "field",
                    {
                        **location,
                        "element": address,
                        "field_type": name,
                        "instruction": instruction[:16_384],
                        "after_segment": segment,
                        "boundary": _attr(element, "fldCharType"),
                        "evaluation": "stored_result_only",
                    },
                )
                if name != "fldSimple":
                    return
            if name in {"footnoteReference", "endnoteReference", "commentReference"}:
                flush()
                identifier = _attr(element, "id")
                self.emit(
                    "embedded_object",
                    {
                        **location,
                        "element": address,
                        "reference_type": name,
                        "after_segment": segment,
                        "note": identifier,
                        "owner_paragraph": location["paragraph_element"],
                    },
                )
                return
            for index, child in _children(element):
                inline(child, f"{address}.{index}", level + 1)

        for index, child in enumerate(node):
            if _local_name(child.tag) != "pPr":
                inline(child, f"{location['element']}.{index}", depth + 1)
            else:
                for pi, prop in enumerate(child):
                    if _local_name(prop.tag) == "sectPr":
                        self.walk(
                            prop,
                            {
                                **location,
                                "element": f"{location['element']}.{index}.{pi}",
                            },
                            depth + 1,
                        )
        flush()

    def _counters(self, events):
        """Counters exist only when one explicit definition governs every event."""
        references = {event["numbering_ref"] for event in events}
        definitions = [self.numberings.get(reference) for reference in references]
        shared = len(definitions) > 1
        if (
            not all(event["resolvable"] for event in events)
            or any(item is None or item["linked"] for item in definitions)
            or any(item["levels"] != definitions[0]["levels"] for item in definitions)
            # Sharing an appearance definition does not prove that independent
            # list instances continue one counter. Leave that relation unresolved.
            or shared
        ):
            return None
        levels = {}
        for index, declared in definitions[0]["levels"].items():
            restart = declared.get("restart")
            supported = (
                not declared.get("legal")
                and restart in {None, "0"}
                and declared.get("number_format") in OOXML_NUMBER_FORMATS
            )
            try:
                # OOXML defines zero, not one, when w:start is omitted.
                start = 0 if declared.get("start") is None else int(declared["start"])
            except (TypeError, ValueError):
                start = None
            levels[index] = {
                "style": OOXML_NUMBER_FORMATS.get(declared.get("number_format"))
                if supported
                else None,
                "start": start,
                "restarts": restart != "0",
                "pattern": declared.get("marker_pattern"),
            }
        return ListCounters(levels, OOXML_MARKER)

    def _resolve_numbering(self):
        """Label a paragraph only when its whole sequence is source-determined."""
        groups = defaultdict(list)
        for event in self.numbering_events:
            definition = self.numberings.get(event["numbering_ref"])
            abstract = definition["abstract"] if definition else None
            groups[abstract or ("num", event["numbering_ref"])].append(event)
        unresolved = 0
        for events in groups.values():
            counters = self._counters(events)
            for event in events:
                value = None if counters is None else counters.advance(event["level"])
                if event["bullet"]:
                    continue
                text = None if counters is None else counters.label(event["level"])
                if text is None:
                    unresolved += 1
                    continue
                for unit in event["units"]:
                    unit.structure_path["computed_list_marker"] = {
                        "text": text,
                        "value": value,
                        "level": event["level"],
                        "basis": "source_numbering_definition_and_body_order",
                    }
        if unresolved:
            self.issues["docx_list_marker_partial"] += unresolved

    def finish(self):
        self._resolve_numbering()
        return super().finish()

    def table(self, node, location, depth):
        parent = {k: location[k] for k in ("table", "cell") if k in location}
        context = {
            k: v
            for k, v in location.items()
            if k not in {"cell", "row", "col", "row_span", "col_span", "is_header"}
        }
        context.update({"table": location["element"], "container_kind": "table"})
        if parent:
            context["container_path"] = [*context.get("container_path", []), parent]
        rows = [(i, r) for i, r in enumerate(node) if _local_name(r.tag) == "tr"]
        grid = _child(node, "tblGrid")
        cols = len(grid) if grid is not None else 0
        self.emit("table", {**context, "rows": len(rows), "cols": cols})
        active, cells = {}, []
        for row_index, (ri, row) in enumerate(rows):
            row_props = _child(row, "trPr")
            col = _integer(_value(row_props, "gridBefore", "0"))
            next_active = {}
            for ci, cell in enumerate(row):
                if _local_name(cell.tag) != "tc":
                    continue
                prop = _child(cell, "tcPr")
                span = _integer(_value(prop, "gridSpan", "1"))
                if span < 1:
                    raise ExtractionError("OOXML table has a zero-width cell")
                vm = _child(prop, "vMerge") if prop is not None else None
                merge = _attr(vm, "val", "continue") if vm is not None else None
                loc = {
                    **context,
                    "element": f"{location['element']}.{ri}.{ci}",
                    "row": row_index,
                    "col": col,
                    "row_span": 1,
                    "col_span": span,
                    "is_header": _child(row_props, "tblHeader") is not None
                    if row_props is not None
                    else False,
                    "container_kind": "table_cell",
                }
                loc["cell"] = loc["element"]
                if (
                    merge == "continue"
                    and col in active
                    and active[col]["col_span"] == span
                ):
                    origin = active[col]
                    origin["row_span"] += 1
                    next_active[col] = origin
                    loc["merged_into"] = origin["cell"]
                elif merge == "continue":
                    self.issues["docx_table_merge_partial"] += 1
                elif merge == "restart":
                    next_active[col] = loc
                if prop is not None and _child(prop, "hMerge") is not None:
                    self.issues["docx_table_merge_partial"] += 1
                cells.append((cell, loc))
                col += span
            active = next_active
        for cell, loc in cells:
            if "merged_into" not in loc:
                self.emit("table_cell", {**loc, "structural_only": True})
            elif any((n.text or "").strip() for n in _desc(cell, "t")):
                self.issues["docx_table_merge_content_present"] += 1
            for index, child in enumerate(cell):
                if _local_name(child.tag) != "tcPr":
                    self.walk(
                        child,
                        {**loc, "element": f"{loc['element']}.{index}"},
                        depth + 1,
                    )

    def walk(self, node, location, depth=0):
        self.guard(depth)
        name = _local_name(node.tag)
        if name == "p":
            self.paragraph(node, location, depth)
            return
        if name == "tbl":
            self.table(node, location, depth)
            return
        if name in {"del", "moveFrom"}:
            self.issues["docx_tracked_changes_present"] += 1
            return
        if name in {"oMath", "oMathPara"}:
            structure = []
            structure_bytes = 0
            for element, address, _ in self.addressed(node, location["element"], depth):
                item = {
                    "element": address,
                    "qualified_name": element.tag,
                    "attributes": dict(element.attrib),
                    **({"text": element.text} if element.text else {}),
                }
                structure_bytes += len(
                    json.dumps(item, ensure_ascii=False).encode("utf-8")
                )
                if len(structure) >= 512 or structure_bytes > 65_536:
                    self.issues["docx_math_structure_limit"] += 1
                    structure = []
                    break
                structure.append(item)
            self.emit(
                "embedded_object",
                {
                    **location,
                    "text_representation": "native_math_tokens",
                    **({"math_structure": structure} if structure else {}),
                },
                " ".join(n.text or "" for n in _desc(node, "t")),
            )
            self.issues["docx_math_layout_partial"] += 1
            return
        if name in {"drawing", "pict", "object"}:
            location = {**location, "object": location["element"]}
            if any(_local_name(n.tag) in _IMAGE_REFERENCES for n in node.iter()):
                self.image(node, location, depth)
            if name == "object":
                self.issues["office_embedded_object_content_partial"] += 1
            self._textboxes(node, location, depth)
            if any(_local_name(n.tag) in {"chart", "relIds"} for n in node.iter()):
                self.issues["docx_drawing_content_partial"] += 1
            return
        if name == "txbxContent":
            location = {**location, "container_kind": "textbox"}
        if name in {"footnote", "endnote", "comment"}:
            identifier = _attr(node, "id", "")
            if identifier.startswith("-") or _attr(node, "type") in {
                "separator",
                "continuationSeparator",
            }:
                return
            location = {**location, "container_kind": name, "note": identifier}
        if name == "altChunk":
            self.issues["docx_imported_content_unread"] += 1
            return
        if name in {"headerReference", "footerReference"}:
            part = self.package.related(location["part"], _attr(node, "id"))
            if part:
                self.references.setdefault(part, []).append(location["element"])
        for index, child in _children(node):
            self.walk(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
            )

    def _textboxes(self, node, location, depth):
        self.guard(depth)
        if _local_name(node.tag) == "txbxContent":
            self.walk(node, location, depth + 1)
            return
        for index, child in _children(node):
            self._textboxes(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
            )


class _SlideReader(_Reader):
    def __init__(self, package):
        super().__init__(package)
        self.animated = set()
        self.animation_unresolved = False

    def observe_part(self, root):
        """Shapes an animation targets keep an uncertain displayed geometry."""
        self.animated = set()
        self.animation_unresolved = False
        for node in root.iter():
            if _local_name(node.tag) not in {"spTgt", "inkTgt"}:
                continue
            identifier = _attr(node, "spid")
            if identifier is None:
                self.animation_unresolved = True
            else:
                self.animated.add(identifier)
        if next(_desc(root, "timing"), None) is not None and not self.animated:
            self.animation_unresolved = True

    def group_geometry(self, node, location):
        """Observe a finite, explicit group mapping, never an inherited one."""
        transform, address = _addressed_path(
            node, location["element"], ("grpSpPr", "xfrm")
        )
        if transform is None or location.get("shape_id") is None:
            return None
        values = {}
        for name, coordinates in (
            ("off", ("x", "y")),
            ("chOff", ("x", "y")),
            ("ext", ("cx", "cy")),
            ("chExt", ("cx", "cy")),
        ):
            child, child_address = _addressed_path(transform, address, (name,))
            if child is None:
                return None
            try:
                pair = {key: int(_attr(child, key)) for key in coordinates}
            except (TypeError, ValueError):
                return None
            if any(abs(value) >= 2**63 for value in pair.values()):
                return None
            if name in {"ext", "chExt"} and any(v <= 0 for v in pair.values()):
                return None
            values[name] = {**pair, "element": child_address}
        try:
            int(transform.get("rot", "0"))
        except ValueError:
            return None
        if any(
            transform.get(key, "0") not in {"0", "1", "true", "false"}
            for key in ("flipH", "flipV")
        ):
            return None
        return {"shape_id": location["shape_id"], "element": address, **values}

    def display_observation(self, node, location):
        """Only an instance's own explicit zero extent proves it is not shown.

        A missing transform inherits its size and is never treated as zero.
        """
        if (
            self.animation_unresolved
            or location.get("shape_id") is None
            or location["shape_id"] in self.animated
            or next(_desc(node, "ph"), None) is not None
        ):
            return {}
        extent, address = _addressed_path(
            node, location["element"], ("spPr", "xfrm", "ext")
        )
        values = _extent(extent)
        if (
            values is None
            or any(value < 0 for value in values.values())
            or all(value > 0 for value in values.values())
        ):
            return {}
        groups = location.get("group_geometry", [])
        if location.get("group_path") and (
            len(groups) != len(location["group_path"])
            or any(
                group is None or group["shape_id"] in self.animated for group in groups
            )
            or any(values.values())
        ):
            return {}
        return {
            "display_state": "not_displayed",
            "display_basis": "own_explicit_zero_extent",
            "display_extent": {**values, "element": address, "unit": "emu"},
        }

    def autonumber_labels(self, node):
        """Number the source-determined prefix of one stored text body."""
        entries = []
        for index, child in enumerate(node):
            if _local_name(child.tag) != "p":
                continue
            prop = _child(child, "pPr")
            declared = (
                any(
                    _local_name(c.tag) in {"buNone", "buChar", "buAutoNum"}
                    for c in prop
                )
                if prop is not None
                else False
            )
            automatic = _child(prop, "buAutoNum") if prop is not None else None
            entries.append((index, prop, automatic))
            if not declared:
                # An inherited bullet makes this and later counters uncertain,
                # but cannot change a preceding, explicitly declared sequence.
                break
        if not any(automatic is not None for _, _, automatic in entries):
            return {}
        style = _child(node, "lstStyle")
        if style is not None and any(
            _local_name(inner.tag) == "buAutoNum" for inner in style.iter()
        ):
            return {}
        levels = {}
        for _, prop, automatic in entries:
            if automatic is None:
                continue
            level = _integer(prop.get("lvl", "0")) + 1
            scheme = automatic.get("type")
            if levels.setdefault(level, scheme) != scheme:
                levels[level] = None
        definitions = {level: {"style": None, "start": 1} for level in levels}
        counters = ListCounters(definitions)
        labels = {}
        for index, prop, automatic in entries:
            if automatic is None:
                continue
            level = _integer(prop.get("lvl", "0")) + 1
            start = automatic.get("startAt")
            try:
                explicit = None if start is None else int(start)
            except ValueError:
                explicit = None
            value = counters.advance(level, start=explicit)
            text = None if levels[level] is None else scheme_label(levels[level], value)
            if text is not None:
                labels[index] = {
                    "text": text,
                    "value": value,
                    "level": level,
                    "basis": "source_autonumber_scheme_and_text_body_order",
                }
        return labels

    def paragraphs(self, node, location, kind="slide_text"):
        labels = self.autonumber_labels(node)
        for index, child in enumerate(node):
            if _local_name(child.tag) != "p":
                continue
            location_p = {**location, "element": f"{location['element']}.{index}"}
            prop = _child(child, "pPr")
            if prop is not None:
                location_p["list_level"] = _integer(prop.get("lvl", "0"))
                bullet, automatic = _child(prop, "buChar"), _child(prop, "buAutoNum")
                if bullet is not None:
                    location_p["marker_text"] = bullet.get("char", "")
                if automatic is not None:
                    location_p["numbering"] = dict(automatic.attrib)
                    if index in labels:
                        location_p["computed_list_marker"] = labels[index]
                    else:
                        self.issues["pptx_list_marker_partial"] += 1
            text = "".join(
                n.text or "" if _local_name(n.tag) == "t" else "\n"
                for n in child.iter()
                if _local_name(n.tag) in {"t", "br"}
            )
            if text.strip():
                self.emit(kind, location_p, text)

    def table(self, node, location):
        rows = [(i, r) for i, r in enumerate(node) if _local_name(r.tag) == "tr"]
        grid = _child(node, "tblGrid")
        context = {**location, "table": location["element"]}
        self.emit(
            "table",
            {
                **context,
                "rows": len(rows),
                "cols": len(grid) if grid is not None else 0,
            },
        )
        origins = []
        for row_index, (ri, row) in enumerate(rows):
            origins = [
                origin
                for origin in origins
                if origin["row"] + origin["row_span"] > row_index
            ]
            col = 0
            for ci, cell in enumerate(row):
                if _local_name(cell.tag) != "tc":
                    continue
                loc = {
                    **context,
                    "element": f"{location['element']}.{ri}.{ci}",
                    "row": row_index,
                    "col": col,
                    "row_span": _integer(cell.get("rowSpan", "1")),
                    "col_span": _integer(cell.get("gridSpan", "1")),
                }
                loc["cell"] = loc["element"]
                spanned = any(
                    cell.get(k) in {"1", "true"} for k in ("hMerge", "vMerge")
                )
                if not spanned:
                    self.emit("table_cell", {**loc, "structural_only": True})
                    if (
                        grid is not None
                        and 1 <= loc["row_span"] <= len(rows) - row_index
                        and 1 <= loc["col_span"] <= len(grid) - col
                    ):
                        origins.append(loc)
                    else:
                        self.issues["pptx_table_merge_structure_partial"] += 1
                else:
                    owners = []
                    for origin in origins:
                        self.guard(0)
                        if (
                            origin["col"] <= col < origin["col"] + origin["col_span"]
                            and (cell.get("hMerge") in {"1", "true"})
                            == (col > origin["col"])
                            and (cell.get("vMerge") in {"1", "true"})
                            == (row_index > origin["row"])
                            and loc["row_span"]
                            == (origin["row_span"] if row_index == origin["row"] else 1)
                            and loc["col_span"]
                            == (origin["col_span"] if col == origin["col"] else 1)
                        ):
                            owners.append(origin)
                    loc["merge_continuation"] = True
                    if len(owners) == 1:
                        loc["merged_into"] = owners[0]["cell"]
                        if any((n.text or "").strip() for n in _desc(cell, "t")):
                            self.issues["pptx_table_merge_content_observed"] += 1
                    else:
                        self.issues["pptx_table_merge_structure_partial"] += 1
                for ti, body in enumerate(cell):
                    if _local_name(body.tag) == "txBody":
                        self.paragraphs(
                            body,
                            {**loc, "element": f"{loc['element']}.{ti}"},
                            "table_cell",
                        )
                col += 1

    def diagram_structure(self, root, location):
        structure = {"points": [], "connections": []}
        size = 0
        namespace = root.tag.rsplit("}", 1)[0] + "}" if "}" in root.tag else ""
        for index, container in enumerate(root):
            if container.tag not in {f"{namespace}ptLst", f"{namespace}cxnLst"}:
                continue
            is_points = _local_name(container.tag) == "ptLst"
            target = structure["points" if is_points else "connections"]
            for child_index, element in enumerate(container):
                self.guard(2)
                if element.tag != f"{namespace}{'pt' if is_points else 'cxn'}":
                    continue
                item = {
                    "element": f"0.{index}.{child_index}",
                    "attributes": dict(element.attrib),
                }
                size += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
                if (
                    sum(len(items) for items in structure.values()) >= 512
                    or size > 65536
                ):
                    self.issues["pptx_diagram_structure_limit"] += 1
                    return
                target.append(item)
        if not any(structure.values()):
            return
        points = {}
        for point in structure["points"]:
            identifier = point["attributes"].get("modelId")
            if identifier:
                points.setdefault(identifier, []).append(point["element"])
        for connection in structure["connections"]:
            for key, relation in (
                ("srcId", "source_point"),
                ("destId", "destination_point"),
            ):
                matches = points.get(connection["attributes"].get(key), [])
                if len(matches) == 1:
                    connection[relation] = matches[0]
                else:
                    self.issues["pptx_diagram_connection_unresolved"] += 1
        if len(json.dumps(structure, ensure_ascii=False).encode("utf-8")) > 65_536:
            self.issues["pptx_diagram_structure_limit"] += 1
            return
        self.emit(
            "embedded_object",
            {
                **location,
                "object_type": "diagram",
                "structural_only": True,
                "diagram_structure": structure,
            },
        )

    def related_text(self, node, location, kind):
        identifier = _attr(node, "id") if kind == "chart" else _attr(node, "dm")
        part = self.package.related(location["part"], identifier)
        if not part:
            self.issues[f"pptx_{kind}_content_unread"] += 1
            return
        root = self.package.xml(part)
        base = {
            **location,
            "owner_part": location["part"],
            "owner_element": location["element"],
            "part": part,
            "element": "0",
            "text_representation": "native_cached_data",
        }
        if kind == "diagram":
            base["diagram"] = "0"
            self.diagram_structure(root, base)
        emitted_before = len(self.units)

        def walk(element, loc, depth=0):
            self.guard(depth)
            name = _local_name(element.tag)
            if kind == "diagram" and name == "pt":
                loc = {**loc, "diagram_point": element.get("modelId")}
            if kind == "chart" and name == "ser":
                index = _child(element, "idx")
                loc = {
                    **loc,
                    "series": index.get("val") if index is not None else loc["element"],
                }
            if name in {"cat", "val", "xVal", "yVal", "bubbleSize", "tx"}:
                loc = {**loc, "data_role": name}
            if kind == "chart" and name == "pt":
                value = _child(element, "v")
                if value is not None and value.text:
                    self.emit(
                        "chart_data",
                        {**loc, "point_index": element.get("idx")},
                        value.text,
                    )
                return
            if name in {"txBody", "rich"} or (kind == "diagram" and name == "t"):
                self.paragraphs(
                    element, loc, "diagram_text" if kind == "diagram" else "chart_data"
                )
                return
            if name == "v" and element.text:
                self.emit("chart_data", loc, element.text)
                return
            for index, child in enumerate(element):
                walk(child, {**loc, "element": f"{loc['element']}.{index}"}, depth + 1)

        walk(root, base)
        if len(self.units) == emitted_before:
            self.issues[f"pptx_{kind}_content_unread"] += 1

    def ole_fallback_preview(self, node, location, depth, notes):
        choice, fallback = _child(node, "Choice"), _child(node, "Fallback")
        if choice is None or fallback is None:
            return
        chosen = list(_desc(choice, "oleObj"))
        alternatives = list(_desc(fallback, "oleObj"))
        if len(chosen) != 1 or len(alternatives) != 1:
            return
        identifier = _attr(chosen[0], "id")
        if (
            not identifier
            or identifier != _attr(alternatives[0], "id")
            or any(True for _ in _desc(choice, "pic"))
            or _child(chosen[0], "embed") is None
            or _child(alternatives[0], "embed") is None
            or not self.package.related(location["part"], identifier)
        ):
            return
        pictures = [c for c in alternatives[0] if _local_name(c.tag) == "pic"]
        if len(pictures) != 1:
            return
        chosen_address = preview = None
        for element, address, level in self.addressed(node, location["element"], depth):
            if element is chosen[0]:
                chosen_address = address
            elif element is pictures[0]:
                preview = (element, address, level)
        if chosen_address is None or preview is None:
            return
        picture, address, level = preview
        self.walk(
            picture,
            {
                **location,
                "element": address,
                "preview_for": chosen_address,
                "preview_relationship": identifier,
                "source_view": "stored_ole_fallback_preview",
            },
            level,
            notes,
        )

    def walk(self, node, location, depth=0, notes=False):
        self.guard(depth)
        name = _local_name(node.tag)
        if name in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}:
            location = {
                k: v
                for k, v in location.items()
                if k not in {"shape_id", "shape_name", "source_transform"}
            }
            nonvisual = next(
                (c for c in node if _local_name(c.tag).startswith("nv")), None
            )
            prop = _child(nonvisual, "cNvPr") if nonvisual is not None else None
            if prop is not None:
                location = {
                    **location,
                    "shape_id": prop.get("id"),
                    "shape_name": prop.get("name", ""),
                    "object": location["element"],
                }
            transform_parent = _child(node, "grpSpPr" if name == "grpSp" else "spPr")
            transform = (
                _child(transform_parent, "xfrm")
                if transform_parent is not None
                else _child(node, "xfrm")
            )
            if transform is not None:
                location = {
                    **location,
                    "source_transform": {
                        "attributes": dict(transform.attrib),
                        **{_local_name(c.tag): dict(c.attrib) for c in transform},
                        "coordinate_space": "parent_group_emu",
                    },
                }
            if name == "grpSp":
                location = {
                    **location,
                    "group_geometry": [
                        *location.get("group_geometry", []),
                        self.group_geometry(node, location),
                    ],
                    "group_path": [
                        *location.get("group_path", []),
                        location["element"],
                    ],
                }
            if notes and name == "sp":
                placeholder = next(_desc(node, "ph"), None)
                if placeholder is not None and placeholder.get("type") in {
                    "sldImg",
                    "sldNum",
                    "dt",
                    "hdr",
                    "ftr",
                }:
                    return
            if name == "pic":
                display = self.display_observation(node, location)
                self.image(node, {**location, **display}, depth)
                return
        if name == "txBody":
            self.paragraphs(node, location, "speaker_notes" if notes else "slide_text")
            return
        if name == "tbl":
            self.table(node, location)
            return
        if name in {"chart", "relIds"}:
            self.related_text(node, location, "chart" if name == "chart" else "diagram")
            return
        if name in {"oleObj", "videoFile", "audioFile"}:
            self.emit("embedded_object", {**location, "object_type": name})
            self.issues["office_embedded_object_content_partial"] += 1
            if name == "oleObj":
                for index, child in enumerate(node):
                    if _local_name(child.tag) == "pic":
                        self.walk(
                            child,
                            {
                                **location,
                                "element": f"{location['element']}.{index}",
                                "preview_for": location["element"],
                                "source_view": "stored_ole_preview",
                            },
                            depth + 1,
                            notes,
                        )
            return
        for index, child in _children(node):
            self.walk(
                child,
                {**location, "element": f"{location['element']}.{index}"},
                depth + 1,
                notes,
            )
        if name == "AlternateContent":
            self.ole_fallback_preview(node, location, depth, notes)


def extract_structured_docx(path):
    _preflight_zip(path)
    with zipfile.ZipFile(path) as archive:
        package = _Package(archive)
        main = package.main("word/document.xml")
        reader = _WordReader(package, main)
        reader.walk(package.xml(main), {"part": main, "element": "0"})
        seen = {main}
        for relation in package.rels(main).values():
            part, kind = relation["part"], relation["type"]
            if (
                not part
                or part in seen
                or kind not in {"header", "footer", "footnotes", "endnotes", "comments"}
                or (kind in {"header", "footer"} and part not in reader.references)
            ):
                continue
            seen.add(part)
            reader.walk(
                package.xml(part),
                {
                    "part": part,
                    "element": "0",
                    "container_kind": kind,
                    "section_references": reader.references.get(part, []),
                },
            )
        return reader.finish()


def extract_structured_pptx(path):
    _preflight_zip(path)
    with zipfile.ZipFile(path) as archive:
        package = _Package(archive)
        main = package.main("ppt/presentation.xml")
        reader = _SlideReader(package)
        slide_list = _child(package.xml(main), "sldIdLst")
        for slide_index, slide in enumerate(
            slide_list if slide_list is not None else [], 1
        ):
            identifier = next(
                (v for k, v in slide.attrib.items() if k.endswith("}id")), None
            )
            part = package.related(main, identifier)
            if not part:
                reader.issues["pptx_slide_unavailable"] += 1
                continue
            root = package.xml(part)
            reader.observe_part(root)
            reader.walk(
                root,
                {
                    "slide": slide_index,
                    "slide_id": slide.get("id"),
                    "part": part,
                    "element": "0",
                },
            )
            for relation in package.rels(part).values():
                if relation["type"] == "notesSlide" and relation["part"]:
                    notes_root = package.xml(relation["part"])
                    reader.observe_part(notes_root)
                    reader.walk(
                        notes_root,
                        {
                            "slide": slide_index,
                            "part": relation["part"],
                            "element": "0",
                        },
                        notes=True,
                    )
        return reader.finish()
