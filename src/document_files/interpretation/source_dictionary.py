"""Lossless sharing of repeated request metadata, never source summarization."""

import json
from copy import deepcopy

SOURCE_SYSTEM = """
When sourceTemplate is present, recursively merge it with EACH blocks/nodes entry
to recover that source's complete metadata. Entry properties override shared ones;
arrays and null replace rather than merge. Text stays explicit in each entry.
Nothing is omitted by this representation; shared formatting is still source evidence.
"""


def _same(a, b):
    # Python treats True == 1 and 1 == 1.0; source types must not collapse.
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(v, b[k]) for k, v in a.items())
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def factor_reads(reads):
    """Share only properties present and equal in every row; arrays stay atomic."""

    def common(values):
        result = {}
        for key, first in values[0].items():
            if not all(key in v for v in values):
                continue
            column = [v[key] for v in values]
            if all(_same(v, first) for v in column):
                result[key] = deepcopy(first)
            elif all(isinstance(v, dict) for v in column):
                shared = common(column)
                if shared:
                    result[key] = shared
        return result

    def difference(value, template):
        return {
            k: difference(v, template[k])
            if isinstance(v, dict) and isinstance(template.get(k), dict)
            else deepcopy(v)
            for k, v in value.items()
            if k not in template or not _same(v, template[k])
        }

    template = common(list(reads.values())) if reads else {}
    return {"template": template, "rows": [[h, difference(v, template)] for h, v in reads.items()]}


def merge(template, patch):
    """Reconstruct a single source or verified read without modifying either input."""
    result = deepcopy(template)
    for key, value in patch.items():
        result[key] = (
            merge(result[key], value)
            if isinstance(value, dict) and isinstance(result.get(key), dict)
            else deepcopy(value)
        )
    return result


def compact_sources(payload, key):
    """Keep per-source text/role/range visible; share repeated metadata if smaller."""
    if len(payload[key]) < 2:
        return payload
    # A request has one source dictionary; do not silently re-factor a prior view.
    if "sourceTemplate" in payload:
        raise ValueError("source_dictionary_already_factored")
    explicit = {"text", "nativeRole", "textRange"}
    factored = factor_reads(
        {
            ref: {k: v for k, v in node.items() if k not in explicit}
            for ref, node in payload[key].items()
        }
    )
    if not factored["template"]:
        return payload
    nodes = {
        ref: {**{k: v for k, v in payload[key][ref].items() if k in explicit}, **patch}
        for ref, patch in factored["rows"]
    }
    result = {**payload, key: nodes, "sourceTemplate": factored["template"]}

    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    # Include the required decoding instruction, not just the shorter payload.
    return result if size(result) + len(SOURCE_SYSTEM) < size(payload) else payload


def source_nodes(payload, key):
    return {
        ref: merge(payload.get("sourceTemplate", {}), patch) for ref, patch in payload[key].items()
    }
