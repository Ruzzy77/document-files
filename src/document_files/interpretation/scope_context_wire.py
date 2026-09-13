"""Lossless columnar display of long applicability context lists.

Only typed model-display positions change. Discovery, selected targets, source
binding and stored compiler mappings still use the original complete objects.
"""

import json
from copy import deepcopy

from .source_dictionary import _same

VERSION = "document-files.scope-context-wire.v1"
MIN_RECORDS = 16
SYSTEM = """
scopeContextEncoding: context and rowBoundaryCandidates may use recordBlocks.
Read blocks in order. For each row, zip its values with columns and add shared
properties to recover one complete record. An empty columns row still counts.
All rows, source text, types, roles and missing properties are preserved, not summarized.
"""


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _pack(records):
    if len(records) < MIN_RECORDS:
        return records
    # Key shape changes start a new block, so missing and null stay distinct.
    # Contiguous blocks preserve order even when a previous shape recurs later.
    blocks = []
    for record in records:
        if not blocks or list(record) != list(blocks[-1][0]):
            blocks.append([record])
        else:
            blocks[-1].append(record)
    encoded = []
    for block in blocks:
        shared = {
            k: deepcopy(v)
            for k, v in block[0].items()
            if all(_same(record[k], v) for record in block)
        }
        columns = [k for k in block[0] if k not in shared]
        encoded.append(
            {
                "shared": shared,
                "columns": columns,
                "rows": [[deepcopy(record[k]) for k in columns] for record in block],
            }
        )
    result = {"recordBlocks": encoded}
    return result if _size(result) < _size(records) else records


def _unpack(value):
    if isinstance(value, list):
        return deepcopy(value)
    return [
        {**deepcopy(block["shared"]), **dict(zip(block["columns"], deepcopy(row), strict=True))}
        for block in value["recordBlocks"]
        for row in block["rows"]
    ]


def compact_scope_context(payload):
    """Encode only when total savings include the required decoding instruction."""
    if "scopeContextEncoding" in payload:
        raise ValueError("scope_context_already_encoded")
    result = deepcopy(payload)
    for task in result.get("tasks", [result]):
        if "rowBoundaryCandidates" in task:
            task["rowBoundaryCandidates"] = _pack(task["rowBoundaryCandidates"])
        for candidate in task["candidates"]:
            candidate["context"] = _pack(candidate["context"])
    result["scopeContextEncoding"] = VERSION
    return result if _size(result) + len(SYSTEM) < _size(payload) else payload


def expand_scope_context(payload):
    """Inspection helper, never a decoder for untrusted model output."""
    result = deepcopy(payload)
    if "scopeContextEncoding" not in result:
        return result
    if result.pop("scopeContextEncoding") != VERSION:
        raise ValueError("unknown_scope_context_encoding")
    for task in result.get("tasks", [result]):
        if "rowBoundaryCandidates" in task:
            task["rowBoundaryCandidates"] = _unpack(task["rowBoundaryCandidates"])
        for candidate in task["candidates"]:
            candidate["context"] = _unpack(candidate["context"])
    return result
