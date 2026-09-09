"""Exact, disjoint source windows for over-budget non-table inference regions.

Only private view descriptors and additional range bindings are generated. Native
nodes, native bindings and their offsets remain unchanged. No semantic boundary
or condition applicability is inferred from proximity.
"""

from __future__ import annotations

import bisect
import hashlib
import json


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _gaps(text, start, end, budget):
    """Split unbound prose at whitespace; an unbroken token remains atomic."""
    while end - start > budget:
        ceiling = start + budget
        cut = max(text.rfind(c, start + 1, ceiling + 1) for c in " \t\n\r")
        if cut < 0:
            positions = [text.find(c, ceiling, end) for c in " \t\n\r"]
            cut = min((p for p in positions if p >= 0), default=end)
        else:
            cut += 1
        if cut <= start:
            cut = end
        yield start, cut
        start = cut
    if start < end:
        yield start, end


def _node_units(observation, ref, binding_ids, budget):
    node = observation.nodes[ref]
    text = node.get("text", "")
    # Native scalar paths are atomic: splitting their display text would hide
    # part of the value that a source binding actually resolves.
    # The caller measures fixed model-view metadata. Original recognition audit
    # traces must not make otherwise splittable display text artificially atomic.
    if any(observation.bindings[b].get("path") != "/text" for b in binding_ids):
        return [{"ref": ref, "start": 0, "end": len(text), "bindings": binding_ids}]
    intervals, indexed = [], []
    for bid in binding_ids:
        binding = observation.bindings[bid]
        if binding.get("candidateRole") == "content":
            continue
        start, end = binding.get("start"), binding.get("end")
        if not (type(start) is int and type(end) is int and 0 <= start <= end <= len(text)):
            return [{"ref": ref, "start": 0, "end": len(text), "bindings": binding_ids}]
        indexed.append((start, end, bid))
        intervals.append((start, end))
        # A declared value and all of its labels must be visible together.
        for label_id in binding.get("labelRefs", []):
            label = observation.bindings.get(label_id, {})
            if label.get("sourceRef") != ref or label.get("path") != "/text":
                return [{"ref": ref, "start": 0, "end": len(text), "bindings": binding_ids}]
            if type(label.get("start")) is not int or type(label.get("end")) is not int:
                return [{"ref": ref, "start": 0, "end": len(text), "bindings": binding_ids}]
            intervals.append((min(start, label["start"]), max(end, label["end"])))
    merged = []
    for start, end in sorted(intervals):
        if merged and (start < merged[-1][1] or start == end == merged[-1][1]):
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    atoms, cursor = [], 0
    for start, end in merged:
        atoms.extend(_gaps(text, cursor, start, budget))
        atoms.append((start, end))
        cursor = end
    atoms.extend(_gaps(text, cursor, len(text), budget))
    if not atoms:
        atoms = [(0, len(text))]
    # Assign each binding exactly once, including zero-width blank values.
    ends = [end for _, end in atoms]
    atom_bindings = [[] for _ in atoms]
    for start, end, bid in indexed:
        i = min(bisect.bisect_left(ends, end), len(atoms) - 1)
        if atoms[i][0] > start:
            raise ValueError("invalid source window partition")
        atom_bindings[i].append(bid)
    units, current = [], None
    for (start, end), bids in zip(atoms, atom_bindings, strict=True):
        cost = end - start + sum(_size(observation.bindings[b]) + len(b) + 8 for b in bids)
        if current and current["cost"] + cost > budget:
            units.append(current)
            current = None
        if current is None:
            current = {"ref": ref, "start": start, "end": end, "bindings": [], "cost": 0}
        current["end"] = end
        current["bindings"].extend(bids)
        current["cost"] += cost
    if current:
        units.append(current)
    if len(units) == 1:
        units[0]["bindings"] = binding_ids
        return units
    additions = []
    for unit in units:
        # Stable IDs do not depend on temporary trial packing or binding count.
        identity = json.dumps([ref, unit["start"], unit["end"]], separators=(",", ":"))
        bid = "view:" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        candidate = {
            "sourceRef": ref,
            "path": "/text",
            "start": unit["start"],
            "end": unit["end"],
            "candidateRole": "content",
            "derivation": "bounded_source_view",
        }
        previous = observation.bindings.get(bid)
        if previous is not None and previous != candidate:
            raise ValueError("source window binding identity collision")
        additions.append((unit, bid, candidate))
    if (
        len(observation.bindings) + sum(bid not in observation.bindings for _, bid, _ in additions)
        > 200000
    ):
        return [{"ref": ref, "start": 0, "end": len(text), "bindings": binding_ids}]
    for unit, bid, candidate in additions:
        observation.bindings[bid] = candidate
        unit["bindings"].append(bid)
    return units


def split_text_region(observation, region, binding_by_node, limit, payload):
    """Return bounded views, retaining oversized atomic/context views explicitly.

    Work is O(bindings log bindings + source length); trial serialization is
    bounded by the request budget except for a single indivisible atom/context.
    """
    explicit_context = region.get("contextNodeIds", [])
    if explicit_context:
        context_only = {**region, "nodeIds": [], "bindingIds": [], "requiredBindingIds": []}
        if _size(payload(observation, context_only)) > limit:
            return [{**region, "budgetReason": "explicit_context_exceeds_budget"}]
    units = []
    for ref in region["nodeIds"]:
        fixed = {
            "id": region["id"],
            "nodeIds": [ref],
            "contextNodeIds": [],
            "nodeViews": {ref: {"start": 0, "end": 0}},
            "bindingIds": [],
        }
        if _size(payload(observation, fixed)) > limit // 2:
            units.append(
                {
                    "ref": ref,
                    "start": 0,
                    "end": len(observation.nodes[ref].get("text", "")),
                    "bindings": binding_by_node.get(ref, []),
                }
            )
            continue
        units.extend(
            _node_units(observation, ref, binding_by_node.get(ref, []), max(64, limit // 4))
        )
    required = set(region.get("requiredBindingIds", []))

    def view(start, stop):
        owned = units[start:stop]
        node_ids = list(dict.fromkeys(u["ref"] for u in owned))
        node_views = {}
        for unit in owned:
            window = node_views.setdefault(
                unit["ref"], {"start": unit["start"], "end": unit["end"]}
            )
            window["end"] = unit["end"]
        context = [ref for ref in region.get("contextNodeIds", []) if ref not in node_ids]
        boundary = []
        # Neighbor windows retain local continuation/condition context. They
        # carry no selectable value bindings and never become duplicate owners.
        for i in (start - 1, stop):
            if not 0 <= i < len(units):
                continue
            neighbor = units[i]
            ref = neighbor["ref"]
            if ref in context:
                continue  # Original explicit context already retains full text.
            window = {"start": neighbor["start"], "end": neighbor["end"]}
            if ref not in node_ids:
                context.append(ref)
                node_views[ref] = window
            else:
                boundary.append({"sourceRef": ref, "textRange": window})
        bids = list(dict.fromkeys(b for u in owned for b in u["bindings"]))
        # Explicit label links can cross native nodes. Retain their definition
        # context without exposing that context node's values as new owners.
        label_ids = list(
            dict.fromkeys(
                label
                for bid in bids
                for label in observation.bindings[bid].get("labelRefs", [])
                if label in observation.bindings
            )
        )
        for label in label_ids:
            ref = observation.bindings[label]["sourceRef"]
            if ref not in node_ids:
                if ref not in context:
                    context.append(ref)
                node_views.pop(ref, None)  # Explicit linkage needs full context.
        bids = list(dict.fromkeys([*bids, *label_ids]))
        unshown_definition = any(
            (window := node_views.get(observation.bindings[label]["sourceRef"])) is not None
            and observation.bindings[label].get("path") == "/text"
            and (
                observation.bindings[label].get("start") is None
                or observation.bindings[label].get("end") is None
                or observation.bindings[label]["start"] < window["start"]
                or observation.bindings[label]["end"] > window["end"]
            )
            for label in label_ids
        )
        return {
            "unshownDefinition": unshown_definition,
            "id": region["id"],
            "sourceRegionId": region["id"],
            "nodeIds": node_ids,
            "contextNodeIds": context,
            "nodeViews": node_views,
            "boundaryContext": boundary,
            "bindingIds": bids,
            "requiredBindingIds": [b for b in bids if b in required],
            "derivation": "bounded_source_view",
        }

    result, start = [], 0
    for stop in range(1, len(units) + 1):
        candidate = view(start, stop)
        if stop - start > 1 and (
            candidate["unshownDefinition"] or _size(payload(observation, candidate)) > limit
        ):
            result.append(view(start, stop - 1))
            start = stop - 1
    if units:
        result.append(view(start, len(units)))
    return result or [region]
