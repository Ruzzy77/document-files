"""Read-only views of one analysis; edit selectors are separately byte-checked."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, BinaryIO
from xml.parsers import expat
from zipfile import ZipFile

from .analysis import AnalysisResult
from .extraction_errors import ExtractionError
from .extraction_protocol import ExtractedUnit
from .extractors import MAX_XML_MEMBER_BYTES


def analysis_record(analysis: AnalysisResult) -> dict[str, Any]:
    return {
        "schemaVersion": analysis.schema_version,
        "jobId": analysis.job_id,
        "input": {
            "formatId": analysis.input.format_id,
            "mediaType": analysis.input.media_type,
            "byteSize": analysis.input.byte_size,
            "sha256": analysis.input.sha256,
        },
        "analyzer": analysis.analyzer.to_dict(),
    }


def _scope(structure: Mapping[str, Any]) -> tuple:
    return tuple(
        structure.get(key) for key in ("part", "section_file", "section_stream", "section", "slide")
    )


def _table_key(structure: Mapping[str, Any]) -> tuple | None:
    return (*_scope(structure), structure["table"]) if "table" in structure else None


def _cell_key(structure: Mapping[str, Any]) -> tuple | None:
    table = _table_key(structure)
    return (*table, structure["cell"]) if table is not None and "cell" in structure else None


def _continued(previous: ExtractedUnit | None, unit: ExtractedUnit) -> bool:
    """Only declared consecutive chunks of one source unit concatenate directly."""

    if previous is None or previous.unit_type != unit.unit_type:
        return False
    before, after = previous.structure_path, unit.structure_path
    chunk = after.get("chunk")
    return (
        isinstance(chunk, int)
        and chunk > 1
        and before.get("chunk") == chunk - 1
        and {key: value for key, value in before.items() if key != "chunk"}
        == {key: value for key, value in after.items() if key != "chunk"}
    )


def _escape(text: str) -> str:
    value = re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", text)
    return re.sub(r"(?m)^(\s*)([-+] |\d+[.)] )", r"\1\\\2", value)


def _read_parts(analysis: AnalysisResult, *, markdown: bool) -> Iterator[str]:
    previous: ExtractedUnit | None = None
    previous_cell = None
    source_markdown = analysis.input.format_id in {"md", "markdown"}
    for unit in analysis.extraction.units:
        location = unit.structure_path
        if not markdown:
            if unit.content:
                yield (
                    "" if _continued(previous, unit) or previous is None else "\n\n"
                ) + unit.content
                previous = unit
            continue

        text = unit.content if source_markdown else _escape(unit.content)
        if unit.unit_type == "table" and "table" in location and not unit.content:
            label = "/".join(
                str(value) for value in (*_scope(location), location["table"]) if value is not None
            )
            dimensions = ", ".join(
                f"{key}: {location[key]}" for key in ("rows", "cols") if key in location
            )
            yield f"\n\n### Table {_escape(label)}" + (f" ({dimensions})" if dimensions else "")
            previous_cell = None
        cell = _cell_key(location)
        if cell is not None and cell != previous_cell:
            coordinates = ", ".join(
                f"{key}: {location[key]}"
                for key in ("row", "col", "row_span", "col_span")
                if key in location
            )
            yield f"\n\n**Cell {_escape(str(location['cell']))}" + (
                f" ({coordinates}; zero-based)**" if coordinates else "**"
            )
            previous_cell = cell
        elif cell is None and unit.content:
            previous_cell = None
        if unit.unit_type == "sheet":
            yield f"\n\n### Sheet {_escape(str(location.get('sheet', '')))}"
        if not text:
            continue
        prefix = "\n\n"
        if _continued(previous, unit):
            prefix = ""
        elif unit.unit_type == "heading":
            level = location.get("level")
            if level is None and re.fullmatch(r"h[1-6]", str(location.get("tag", ""))):
                level = int(location["tag"][1])
            if isinstance(level, int) and 1 <= level <= 6:
                prefix += "#" * level + " "
        elif unit.unit_type == "list_item" or location.get("tag") == "li":
            marker = location.get("computed_list_marker", {})
            marker_text = marker.get("text") if isinstance(marker, Mapping) else None
            marker_text = marker_text or location.get("marker_text")
            prefix += "- " + (f"{_escape(str(marker_text))} " if marker_text else "")
        yield prefix + text
        previous = unit


def project_read_text(
    analysis: AnalysisResult, *, output_format: str, max_chars: int
) -> tuple[str, bool]:
    """Bound display text, not extraction; keep every distinct source occurrence."""

    parts = []
    size = 0
    for part in _read_parts(analysis, markdown=output_format == "markdown"):
        if size == 0:
            part = part.lstrip("\n")
        remaining = max_chars - size
        parts.append(part[:remaining])
        size += min(len(part), remaining)
        if len(part) > remaining:
            return "".join(parts), True
    return "".join(parts), False


def _xml_table_addresses(content: bytes) -> dict[str, int] | None:
    """Verify the pinned table_patch lexical selector against actual XML elements.

    table_patch 6.3.0 indexes all tbl opening tags per section, including nested
    tables. A comment, CDATA, self-closing table or unusual encoding can make its
    lexical scan disagree with XML traversal. Omit selectors in that case.
    """

    parser = expat.ParserCreate()
    stack: list[list[Any]] = []
    opens: list[int] = []
    closes: list[int] = []
    addresses: dict[str, int] = {}
    nodes = 0

    def start(name, _attributes):
        nonlocal nodes
        nodes += 1
        if len(stack) >= 64 or nodes > 2_000_000:
            raise ValueError("XML selector traversal exceeds its bound")
        if stack:
            address = f"{stack[-1][0]}.{stack[-1][1]}"
            stack[-1][1] += 1
        else:
            address = "0"
        stack.append([address, 0])
        if name.rsplit(":", 1)[-1] == "tbl":
            addresses[address] = len(opens)
            opens.append(parser.CurrentByteIndex)

    def end(name):
        if name.rsplit(":", 1)[-1] == "tbl":
            closes.append(parser.CurrentByteIndex)
        stack.pop()

    def forbid(*_args):
        raise ValueError("XML declarations are not permitted for edit selectors")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = forbid
    parser.EntityDeclHandler = forbid
    parser.ExternalEntityRefHandler = forbid
    try:
        parser.Parse(content, True)
    except (expat.ExpatError, ValueError):
        return None
    lexical_opens = [
        match.start() for match in re.finditer(rb"<(?:[A-Za-z_][\w.-]*:)?tbl\b", content)
    ]
    lexical_closes = [
        match.start() for match in re.finditer(rb"</(?:[A-Za-z_][\w.-]*:)?tbl>", content)
    ]
    return addresses if opens == lexical_opens and closes == lexical_closes else None


def _attach_hwpx_selectors(source: Path | BinaryIO, tables: list[dict]) -> None:
    by_section: dict[str, list[dict]] = defaultdict(list)
    for table in tables:
        location = table["sourceStructure"]
        if isinstance(location.get("section_file"), str):
            by_section[location["section_file"]].append(table)
    if not by_section:
        return
    with ZipFile(source) as archive:
        for section, entries in by_section.items():
            info = archive.getinfo(section)
            if info.file_size > MAX_XML_MEMBER_BYTES:
                raise ExtractionError("HWPX selector XML exceeds its byte budget")
            with archive.open(section) as stream:
                content = stream.read(MAX_XML_MEMBER_BYTES + 1)
            if len(content) > MAX_XML_MEMBER_BYTES:
                raise ExtractionError("HWPX selector XML exceeds its byte budget")
            selectors = _xml_table_addresses(content)
            for table in entries:
                element = table["sourceStructure"].get("element")
                if selectors is not None and element in selectors:
                    table.update(
                        sectionPath=section,
                        tableIndex=selectors[element],
                        selectorBasis="verified-section-xml-table-order",
                    )
                else:
                    table["selectorUnavailableReason"] = "xml-table-order-unverified"


def project_tables_and_fields(
    analysis: AnalysisResult,
    *,
    source: Path | BinaryIO,
    include_cells: bool,
    max_cells: int,
    max_chars: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    """Group by declared source identities, never by equal displayed values."""

    tables: dict[tuple, dict] = {}
    cells: dict[tuple, dict] = {}
    cell_units: dict[tuple, list[ExtractedUnit]] = defaultdict(list)
    fields: list[dict] = []
    for unit in analysis.extraction.units:
        location = unit.to_dict()["structure_path"]
        table_key = _table_key(location)
        cell_key = _cell_key(location)
        if unit.unit_type == "table" and table_key is not None and table_key not in tables:
            tables[table_key] = {
                "sourceRef": location["table"],
                "sourceStructure": location,
                **{key: location[key] for key in ("rows", "cols") if key in location},
                "cells": [],
            }
        if cell_key is not None:
            if cell_key not in cells:
                cells[cell_key] = {
                    "sourceRef": location["cell"],
                    "sourceStructure": location,
                    **{
                        public: location[key]
                        for key, public in (
                            ("row", "row"),
                            ("col", "col"),
                            ("row_span", "rowSpan"),
                            ("col_span", "colSpan"),
                            ("is_header", "isHeader"),
                        )
                        if key in location
                    },
                }
            if unit.content:
                cell_units[cell_key].append(unit)
        if unit.unit_type == "field":
            fields.append(
                {
                    "sourceStructure": location,
                    **{
                        public: location[key]
                        for key, public in (
                            ("field_id", "fieldId"),
                            ("native_id", "nativeId"),
                            ("field_type", "fieldType"),
                            ("name", "name"),
                            ("parameters", "parameters"),
                            ("field_range", "range"),
                        )
                        if key in location
                    },
                    **(
                        {"hasEnd": True}
                        if "end_element" in location or "field_range" in location
                        else {}
                    ),
                    **({"value": unit.content} if unit.content else {}),
                    **(
                        {"storedNumber": location["stored_number"]}
                        if "stored_number" in location
                        else {}
                    ),
                }
            )

    def bounded_units(units, limit):
        pieces, count, previous = [], 0, None
        for unit in units:
            value = (
                "" if previous is None or _continued(previous, unit) else "\n\n"
            ) + unit.content
            pieces.append(value[: max(0, limit - count)])
            count += len(value)
            previous = unit
        return "".join(pieces), count > limit

    remaining_cells, remaining_chars = max_cells, max_chars
    truncated = False
    for key, cell in cells.items():
        table = tables.get(key[:-1])
        if table is None:
            continue
        table["cellCount"] = table.get("cellCount", 0) + 1
        if not include_cells:
            continue
        if remaining_cells <= 0:
            truncated = True
            continue
        text, text_truncated = bounded_units(cell_units[key], remaining_chars)
        cell.update(text=text, textTruncated=text_truncated)
        remaining_chars -= len(text)
        remaining_cells -= 1
        truncated |= text_truncated
        table["cells"].append(cell)
    table_list = list(tables.values())
    for table in table_list:
        table.setdefault("cellCount", 0)
        if not include_cells:
            table.pop("cells")
    if analysis.input.format_id == "hwpx" and not any(
        issue.code == "hwpx_contains_binary_hwp" for issue in analysis.extraction.issues
    ):
        _attach_hwpx_selectors(source, table_list)
    field_chars = max_chars
    for field in fields:
        # A field's range and its stored display value are different evidence.
        # Do not fold nearby paragraphs or memo bodies into an inferred value.
        if "value" in field:
            value = field["value"]
            field.update(value=value[:field_chars], valueTruncated=len(value) > field_chars)
            field_chars -= min(len(value), field_chars)
    return (
        {
            "tables": table_list,
            "cellsIncluded": include_cells,
            "cellIndexBase": 0,
            "maxCellsReturned": max_cells,
            "maxCellTextChars": max_chars,
        },
        fields,
        truncated,
    )
