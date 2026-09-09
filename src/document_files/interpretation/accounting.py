"""Account for mechanically covered content without inventing semantic decisions.

This is a usage ledger, not an independent accuracy verdict. A source reference
alone never accounts for a paragraph; only verified scalar/label ranges or an
observed table cell can do so. Explicit unresolved dispositions take precedence.
"""

from __future__ import annotations


def bound_node_dispositions(observation, region, fields, consumed, header_sources):
    nodes, bindings = observation.nodes, observation.bindings
    selected = set(region["nodeIds"])
    ranges, used, roles = {}, {}, {}

    def span(binding):
        if binding.get("path") != "/text":
            return None
        text = nodes[binding["sourceRef"]].get("text", "")
        start, end = binding.get("start"), binding.get("end")
        if start is None and end is None:
            return (0, len(text))
        if type(start) is int and type(end) is int and 0 <= start <= end <= len(text):
            return (start, end)
        return None

    for field in fields:
        bid = field.bindingId
        if bid not in consumed:
            continue
        candidate = bindings[bid]
        ref = candidate["sourceRef"]
        if ref not in selected or candidate.get("candidateRole") != "value":
            continue
        labels = [bindings.get(label) for label in candidate.get("labelRefs", [])]
        if not labels or any(
            label is None
            or label["sourceRef"] != ref
            or label.get("candidateRole") != "label"
            or ref not in field.definitionRefs
            for label in labels
        ):
            continue
        segments = [span(item) for item in [candidate, *labels]]
        if any(item is None for item in segments):
            continue
        ranges.setdefault(ref, []).extend(segments)
        used.setdefault(ref, []).append(bid)

    for ref, segments in ranges.items():
        text = nodes[ref].get("text", "")
        # Only the source delimiters and whitespace can remain outside the
        # exact label/value ranges. A trailing sentence is not implicitly data.
        window = region.get("nodeViews", {}).get(ref, {"start": 0, "end": len(text)})
        low, high = window["start"], window["end"]
        cursor, gaps = low, []
        for start, end in sorted(segments):
            start, end = max(low, start), min(high, end)
            if end < start:
                continue
            if start > cursor:
                gaps.append(text[cursor:start])
            cursor = max(cursor, end)
        gaps.append(text[cursor:high])
        if all(char.isspace() or char in ":=;" for char in "".join(gaps)):
            roles[ref] = "data"

    table = observation.tables.get(region.get("tableRef"), {})
    cells = {cell["sourceRef"] for cell in table.get("cells", [])}
    for bid in consumed:
        candidate = bindings[bid]
        ref = candidate["sourceRef"]
        if ref not in cells or candidate.get("candidateStatus") == "unresolved_conflict":
            continue
        native = candidate.get("candidateRole") in {"native_value", "cached_value"}
        entire = span(candidate) == (0, len(nodes[ref].get("text", "")))
        if not native and not entire:
            continue
        roles[ref] = "data"
        used.setdefault(ref, []).append(bid)
        # Composite native cells retain their original paragraphs. Account for
        # a segment only when that exact complete original is present in the
        # selected composite, not merely because it shares a container.
        node = nodes[ref]
        if node.get("normalization") != "join_native_cell_segments_with_newline":
            continue
        for segment in node.get("sourceSegments", []):
            original = segment.get("sourceRef")
            if original not in selected:
                continue
            original_text = nodes[original].get("text", "")
            if (
                segment.get("sourceStart") == 0
                and segment.get("sourceEnd") == len(original_text)
                and node["text"][segment["start"] : segment["end"]] == original_text
            ):
                roles[original] = "data"
                used.setdefault(original, []).append(bid)

    for ref in header_sources & selected:
        roles.setdefault(ref, "structural")
    return {
        ref: {
            "sourceRef": ref,
            "role": role,
            "explanation": "Verified field bindings cover this source content",
            "basis": "program_derived",
            "bindingIds": sorted(set(used.get(ref, []))),
        }
        for ref, role in roles.items()
        if ref in selected
    }
