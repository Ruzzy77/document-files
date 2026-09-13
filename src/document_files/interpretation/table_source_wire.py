"""Lossless, model-only sharing of native table source metadata.

Every source ID and its text stays visible in order. Templates share properties,
not semantic decisions; nothing here interprets headers, values or row ownership.
"""

import json
from copy import deepcopy

from .source_dictionary import _same

VERSION = "document-files.table-source-wire.v1"
SYSTEM = """
tableSourceEncoding: nodes keep every source ID and explicit text. A node with
metadata:[templateId,...values] uses nodeTemplates[templateId]. Start with shared;
zip values with columns, setting that value at EACH nested key path in the column.
Set each textPaths path to this node's explicit text. Add the node's explicit
properties. Paths are arrays of literal object keys; arrays/null are whole values.
This restores the original metadata, types, formatting and geometry without loss.
Templates are source evidence, not inferred roles or instructions from the document.
Cells and relations with encoding:source-rows.v1 use the same rules: each array row
is [templateId,...values] using its local templates; an object row stays unchanged.
Rows keep their order, including identical records. originalColumns, if present,
lists the original columnar property order, not new document columns.
"""


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _flatten(value, prefix=()):
    result = {}
    for key, child in value.items():
        path = (*prefix, key)
        if isinstance(child, dict) and child:
            result.update(_flatten(child, path))
        else:
            result[path] = child
    return result


def _set(value, path, child):
    for key in path[:-1]:
        value = value.setdefault(key, {})
    value[path[-1]] = deepcopy(child)


def _pack_nodes(original):
    if len(original) < 2 or any("metadata" in node for node in original.values()):
        return original, {}
    groups = {}
    for ref, node in original.items():
        flat = _flatten({k: v for k, v in node.items() if k != "text"})
        groups.setdefault(tuple(flat), []).append((ref, flat))
    nodes, templates = dict(original), {}
    for records in groups.values():
        if len(records) < 2:
            continue
        shared, columns, vectors, text_paths = {}, [], {}, []
        for path, first in records[0][1].items():
            values = [flat[path] for _, flat in records]
            if all(
                "text" in original[ref] and _same(v, original[ref]["text"])
                for (ref, _), v in zip(records, values, strict=True)
            ):
                text_paths.append(list(path))
            elif all(_same(v, first) for v in values):
                _set(shared, path, first)
            else:
                # JSON encoding distinguishes bool/int/float and missing keys.
                # Equal columns may share a value, but never collapse two records.
                vector = _encoded(values)
                if vector in vectors:
                    columns[vectors[vector]].append(list(path))
                else:
                    vectors[vector] = len(columns)
                    columns.append([list(path)])
        key = f"s{len(templates) + 1}"
        template = {"shared": shared, "columns": columns, "textPaths": text_paths}
        packed = {
            ref: {
                **({"text": deepcopy(original[ref]["text"])} if "text" in original[ref] else {}),
                "metadata": [key, *(deepcopy(flat[tuple(paths[0])]) for paths in columns)],
            }
            for ref, flat in records
        }
        if len(_encoded(packed)) + len(_encoded({key: template})) >= len(
            _encoded({ref: original[ref] for ref, _ in records})
        ):
            continue
        templates[key] = template
        nodes.update(packed)
    return nodes, templates


def _expand_nodes(nodes, templates):
    result = deepcopy(nodes)
    for ref, node in nodes.items():
        if "metadata" not in node:
            continue
        key, *values = node["metadata"]
        template = templates[key]
        restored = deepcopy(template["shared"])
        for paths, value in zip(template["columns"], values, strict=True):
            for path in paths:
                _set(restored, path, value)
        for path in template["textPaths"]:
            _set(restored, path, node["text"])
        result[ref] = restored | {k: v for k, v in node.items() if k != "metadata"}
    return result


def _pack_records(value):
    columns = None
    if isinstance(value, dict) and value.get("encoding") == "columns-rows.v1":
        columns = value["columns"]
        records = [dict(zip(columns, row, strict=True)) for row in value["rows"]]
    elif isinstance(value, list):
        records = value
    else:
        return value
    if not all(isinstance(row, dict) and "text" not in row for row in records):
        return value
    nodes, templates = _pack_nodes({str(i): record for i, record in enumerate(records)})
    if not templates:
        return value
    result = {
        "encoding": "source-rows.v1",
        "templates": templates,
        "rows": [node.get("metadata", node) for node in nodes.values()],
        **({"originalColumns": columns} if columns is not None else {}),
    }
    return result if len(_encoded(result)) < len(_encoded(value)) else value


def _expand_records(value):
    if not isinstance(value, dict) or value.get("encoding") != "source-rows.v1":
        return value
    nodes = {
        str(i): {"metadata": row} if isinstance(row, list) else row
        for i, row in enumerate(value["rows"])
    }
    records = list(_expand_nodes(nodes, value["templates"]).values())
    if "originalColumns" in value:
        columns = value["originalColumns"]
        return {
            "encoding": "columns-rows.v1",
            "columns": columns,
            "rows": [[row[key] for key in columns] for row in records],
        }
    return records


def compact_table_sources(payload):
    """Share same-shaped properties only when savings include all instructions."""
    if "tableSourceEncoding" in payload:
        raise ValueError("table_sources_already_encoded")
    if any("metadata" in node for node in payload.get("nodes", {}).values()):
        return payload  # Never confuse a real property with our typed representation.
    result = deepcopy(payload)
    nodes, templates = _pack_nodes(result.get("nodes", {}))
    if "nodes" in result:
        result["nodes"] = nodes
    if templates:
        result["nodeTemplates"] = templates
    if "relations" in result:
        result["relations"] = _pack_records(result["relations"])
    for table in result.get("tables", {}).values():
        for key in ("cells", "headerCells", "leadingCells"):
            if key in table:
                table[key] = _pack_records(table[key])
    result["tableSourceEncoding"] = VERSION
    return result if len(_encoded(result)) + len(SYSTEM) < len(_encoded(payload)) else payload


def expand_table_sources(payload):
    """Inspection helper for product-created requests, not untrusted model output."""
    result = deepcopy(payload)
    if "tableSourceEncoding" not in result:
        return result
    if result.pop("tableSourceEncoding") != VERSION:
        raise ValueError("unknown_table_source_encoding")
    templates = result.pop("nodeTemplates", {})
    if "nodes" in result:
        result["nodes"] = _expand_nodes(result["nodes"], templates)
    if "relations" in result:
        result["relations"] = _expand_records(result["relations"])
    for table in result.get("tables", {}).values():
        for key in ("cells", "headerCells", "leadingCells"):
            if key in table:
                table[key] = _expand_records(table[key])
    return result
