"""Public, format-neutral projection of source-addressed extraction units.

The extraction envelope remains the strict Corpus boundary.  This module adds
only a reusable read API: it normalizes explicit structure and values without
creating index identities, guessing adjacent-cell relationships, or evaluating
document code and formulas.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from .extraction_protocol import ExtractionEnvelope

STRUCTURED_EXTRACTION_SCHEMA_VERSION = "document-files.structured-extraction.v1"
MAX_PUBLIC_STRUCTURED_UNITS = 5_000
DEFAULT_PUBLIC_STRUCTURED_UNITS = 500

_ROLE_BY_UNIT_TYPE = {
    "caption": "caption",
    "chart_data": "data_value",
    "comment": "comment",
    "diagram_text": "diagram_text",
    "document_text": "paragraph",
    "embedded_object": "embedded_object",
    "endnote": "note",
    "field": "field",
    "footer": "footer",
    "footnote": "note",
    "header": "header",
    "heading": "heading",
    "list_item": "list_item",
    "page": "page",
    "paragraph": "paragraph",
    "section_paragraph": "paragraph",
    "sheet": "sheet",
    "sheet_cell": "sheet_cell",
    "slide_text": "paragraph",
    "speaker_notes": "note",
    "table": "table",
    "table_cell": "table_cell",
}


def _semantic_role(unit_type: str, structure: Mapping[str, Any]) -> str:
    if unit_type == "table":
        tag = structure.get("tag")
        if tag in {"td", "th"}:
            return "table_cell"
        if tag == "tr":
            return "table_row"
    return _ROLE_BY_UNIT_TYPE.get(unit_type, "content")


def _source_level(structure: Mapping[str, Any]) -> dict[str, Any] | None:
    marker = structure.get("computed_list_marker")
    if isinstance(marker, Mapping) and isinstance(marker.get("level"), int):
        return {"level": marker["level"], "indexBase": 1}
    if isinstance(structure.get("level"), int):
        return {"level": structure["level"], "indexBase": 1}
    if isinstance(structure.get("list_level"), int):
        return {"level": structure["list_level"], "indexBase": 0}
    return None


def _public_typed_value(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: item for key, item in value.items() if key not in {"cached_available", "cached_value"}
    }
    if "cached_available" in value:
        result["cachedAvailable"] = value["cached_available"]
    if "cached_value" in value:
        cached = value["cached_value"]
        result["cachedValue"] = (
            _public_typed_value(cached) if isinstance(cached, Mapping) else cached
        )
    return result


def _public_merged_ranges(value: Any) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    return [
        {
            "range": item.get("range"),
            "origin": {
                "row": item.get("origin", {}).get("row"),
                "column": item.get("origin", {}).get("col"),
            },
            "rowSpan": item.get("row_span"),
            "columnSpan": item.get("col_span"),
        }
        if isinstance(item, Mapping)
        else item
        for item in value
    ]


def _public_hidden_column_ranges(value: Any) -> Any:
    if not isinstance(value, (list, tuple)):
        return value
    return [
        {
            "minColumn": item.get("min_col"),
            "maxColumn": item.get("max_col"),
        }
        if isinstance(item, Mapping)
        else item
        for item in value
    ]


def _public_image_marker(value: Mapping[str, Any]) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "kind": "image",
        "basis": "source_image_reference",
    }
    if "binary_item_ref" in value:
        marker["sourceRef"] = value["binary_item_ref"]
    parts = value.get("image_parts")
    if isinstance(parts, (list, tuple)):
        marker["sourceParts"] = list(parts)
    if "fallback_text" in value:
        marker["fallbackText"] = value["fallback_text"]
    if "check_marker_text" in value:
        marker["checkText"] = value["check_marker_text"]
    rendering = {
        public: value[source]
        for source, public in (
            ("brightness", "brightness"),
            ("bright", "brightness"),
            ("contrast", "contrast"),
            ("effect", "effect"),
            ("alpha", "alpha"),
        )
        if source in value
    }
    if rendering:
        marker["rendering"] = rendering
    return marker


def _semantic_projection(
    unit_type: str,
    structure: Mapping[str, Any],
    content: str,
) -> tuple[str, dict[str, Any]]:
    role = _semantic_role(unit_type, structure)
    semantic: dict[str, Any] = {"basis": "source_structure"}

    position = {
        public: structure[source]
        for source, public in (
            ("page", "page"),
            ("slide", "slide"),
            ("section", "section"),
            ("paragraph", "paragraph"),
            ("line_start", "lineStart"),
            ("line_end", "lineEnd"),
            ("part", "part"),
        )
        if source in structure
    }
    if position:
        semantic["position"] = position

    if role == "heading":
        outline: dict[str, Any] = {}
        if level := _source_level(structure):
            outline.update(level)
        if isinstance(structure.get("heading_path"), (list, tuple)):
            outline["path"] = list(structure["heading_path"])
        if outline:
            semantic["outline"] = outline

    if role == "list_item":
        list_data: dict[str, Any] = {}
        if level := _source_level(structure):
            list_data.update(level)
        marker = structure.get("computed_list_marker")
        if isinstance(marker, Mapping):
            list_data["marker"] = dict(marker)
        elif "marker_text" in structure:
            list_data["marker"] = {
                "text": structure["marker_text"],
                "basis": "source_marker",
            }
        elif isinstance(structure.get("marker_image"), Mapping):
            list_data["marker"] = _public_image_marker(structure["marker_image"])
        if "numbering_ref" in structure:
            list_data["numberingRef"] = structure["numbering_ref"]
        if list_data:
            semantic["list"] = list_data

    if role in {"table", "table_cell"} or "table" in structure:
        table = {}
        if "table" in structure:
            table["sourceRef"] = structure["table"]
        if "rows" in structure:
            table["rows"] = structure["rows"]
        if "cols" in structure:
            table["columns"] = structure["cols"]
        if "container_path" in structure:
            table["containerPath"] = structure["container_path"]
        if table:
            semantic["table"] = table

    if role == "table_cell" or "cell" in structure:
        cell = {
            public: structure[source]
            for source, public in (
                ("cell", "sourceRef"),
                ("row", "row"),
                ("col", "column"),
                ("row_span", "rowSpan"),
                ("col_span", "columnSpan"),
                ("is_header", "isHeader"),
                ("merged_into", "mergedInto"),
            )
            if source in structure
        }
        if "row" in cell or "column" in cell:
            cell["indexBase"] = 0
        if cell:
            semantic["cell"] = cell
        if content:
            semantic["value"] = {
                "kind": "text",
                "value": content,
                "basis": "native_text_segment",
            }

    if role in {"sheet", "sheet_cell"}:
        sheet = {
            public: structure[source]
            for source, public in (
                ("sheet", "name"),
                ("sheet_index", "index"),
                ("sheet_state", "state"),
                ("max_row", "maxRow"),
                ("max_col", "maxColumn"),
                ("declared_dimension", "declaredDimension"),
                ("merged_ranges", "mergedRanges"),
                ("hidden_rows", "hiddenRows"),
                ("hidden_column_ranges", "hiddenColumnRanges"),
            )
            if source in structure
        }
        if "mergedRanges" in sheet:
            sheet["mergedRanges"] = _public_merged_ranges(sheet["mergedRanges"])
        if "hiddenColumnRanges" in sheet:
            sheet["hiddenColumnRanges"] = _public_hidden_column_ranges(sheet["hiddenColumnRanges"])
        if sheet:
            semantic["sheet"] = sheet
        if role == "sheet_cell":
            cell = {
                public: structure[source]
                for source, public in (
                    ("coordinate", "coordinate"),
                    ("row", "row"),
                    ("col", "column"),
                    ("row_span", "rowSpan"),
                    ("col_span", "columnSpan"),
                    ("merged_range", "mergedRange"),
                    ("number_format", "numberFormat"),
                    ("style_id", "styleId"),
                )
                if source in structure
            }
            cell["indexBase"] = 1
            semantic["cell"] = cell
            value = structure.get("value")
            if isinstance(value, Mapping):
                semantic["value"] = _public_typed_value(value)

    if role == "field":
        field = {
            public: structure[source]
            for source, public in (
                ("field_type", "type"),
                ("field_type_origin", "typeOrigin"),
                ("field_id", "id"),
                ("native_id", "nativeId"),
                ("name", "name"),
                ("evaluation", "evaluation"),
                ("field_range", "range"),
                ("instruction", "instruction"),
            )
            if source in structure
        }
        if field:
            semantic["field"] = field
        if isinstance(structure.get("stored_number"), int):
            semantic["value"] = {
                "kind": "integer",
                "value": structure["stored_number"],
                "basis": structure.get("number_origin", "stored_control_value"),
            }

    if role == "data_value":
        chart = {
            public: structure[source]
            for source, public in (
                ("series", "series"),
                ("point_index", "pointIndex"),
                ("data_role", "dataRole"),
                ("text_representation", "representation"),
            )
            if source in structure
        }
        if chart:
            semantic["chart"] = chart
        if content:
            semantic["value"] = {
                "kind": "text",
                "value": content,
                "basis": structure.get("text_representation", "native_text"),
            }

    if role == "note":
        note = {"kind": unit_type}
        if "note" in structure:
            note["sourceRef"] = structure["note"]
        if "note_number" in structure:
            note["number"] = structure["note_number"]
        semantic["note"] = note

    if role == "embedded_object":
        object_data = {
            public: structure[source]
            for source, public in (
                ("object", "sourceRef"),
                ("object_type", "type"),
                ("text_representation", "textRepresentation"),
            )
            if source in structure
        }
        if object_data:
            semantic["object"] = object_data

    return role, semantic


def project_structured_extraction(
    envelope: ExtractionEnvelope,
    *,
    source_format: str,
    unit_offset: int,
    max_units: int,
    include_text: bool,
) -> dict[str, Any]:
    """Project one immutable envelope into a bounded reusable public page."""

    total = len(envelope.units)
    end = min(total, unit_offset + max_units)
    projected = []
    role_counts: Counter[str] = Counter()
    unit_type_counts = Counter(unit.unit_type for unit in envelope.units)
    issue_counts: Counter[str] = Counter(issue.code for issue in envelope.issues)

    for ordinal, unit in enumerate(envelope.units, start=1):
        raw = unit.to_dict()
        structure = raw["structure_path"]
        role, semantic = _semantic_projection(
            unit.unit_type,
            structure,
            unit.content,
        )
        role_counts[role] += 1
        issue_counts.update(issue.code for issue in unit.issues)
        if not unit_offset <= ordinal - 1 < end:
            continue
        item = {
            "ordinal": ordinal,
            "sourceUnitType": unit.unit_type,
            "semanticRole": role,
            "sourceStructure": structure,
            "semantic": semantic,
            "derivation": {
                "method": unit.derivation_method,
                "confidence": unit.confidence,
                "geometry": raw["geometry"],
            },
            "quality": {
                "flags": list(unit.quality_flags),
                "issues": [issue.to_dict() for issue in unit.issues],
            },
        }
        if include_text:
            item["text"] = unit.content
        projected.append(item)

    has_more = end < total
    return {
        "schemaVersion": STRUCTURED_EXTRACTION_SCHEMA_VERSION,
        "manifestHash": envelope.manifest_hash,
        "sourceFormat": source_format,
        "completeness": envelope.completeness,
        "coverage": envelope.coverage.to_dict(),
        "semanticPolicy": {
            "sourceDeclaredOnly": True,
            "adjacentCellInference": False,
            "formulaEvaluation": False,
            "indexIdentityAssigned": False,
        },
        "summary": {
            "unitCount": total,
            "unitTypes": dict(sorted(unit_type_counts.items())),
            "semanticRoles": dict(sorted(role_counts.items())),
            "issueCounts": dict(sorted(issue_counts.items())),
        },
        "unitPage": {
            "offset": unit_offset,
            "limit": max_units,
            "returned": len(projected),
            "total": total,
            "hasMore": has_more,
            "nextOffset": end if has_more else None,
            "textIncluded": include_text,
        },
        "units": projected,
        "issues": [issue.to_dict() for issue in envelope.issues],
        "engine": {
            "name": envelope.descriptor.adapter_id,
            "version": envelope.descriptor.adapter_version,
        },
    }
