"""Exact run partitions for review context, never automatic text/edge approval."""

from __future__ import annotations

from .pdf_visual_grid import MIN_LINE_LENGTH, SEARCH_RADIUS
from .pdf_visual_plan import _bounds, _inside_run, digest, require


def _connected(runs, spend):
    """Eight-neighbor components after removal of measured rule pixels."""
    merged = []
    for y, left, right in sorted(runs):
        spend()
        if merged and merged[-1][0] == y and merged[-1][2] == left:
            merged[-1][2] = right
        else:
            merged.append([y, left, right])
    parents = list(range(len(merged)))

    def root(i):
        while parents[i] != i:
            spend()
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    rows = {}
    for i, (y, left, right) in enumerate(merged):
        for j in rows.get(y - 1, []):
            spend()
            if merged[j][1] <= right and left <= merged[j][2]:
                parents[root(i)] = root(j)
        rows.setdefault(y, []).append(i)
    groups = {}
    for i, run in enumerate(merged):
        groups.setdefault(root(i), []).append(run)
    return list(groups.values())


def partition_units(
    pixels, sources, slots, grid, band_rows, *, proposal, spend, max_runs, max_units
):
    """All pixels remain; bounds/adjacency offer context, not a semantic label."""
    source_boxes = []
    for source in sources:
        source_boxes.append((source["id"], source["bounds"]))
        if "additionalObservation" in source:
            source_boxes.append((source["id"], source["additionalObservation"]["bounds"]))
    edge_boxes = []
    if proposal and grid:
        width, height = pixels["pixelSize"]
        for measured in grid["grids"]:
            for band in measured["bands"]:
                if band["status"] != "candidate":
                    continue
                x, y, r, b = band["bounds"]
                edge_boxes.append(
                    (
                        measured["tableRef"],
                        [
                            max(0, x - SEARCH_RADIUS),
                            max(0, y - SEARCH_RADIUS),
                            min(width, r + SEARCH_RADIUS),
                            min(height, b + SEARCH_RADIUS),
                        ],
                    )
                )
    groups, split_count = {}, 0
    for component in pixels["components"]:
        # Detached dots cannot acquire rule-edge context just by being nearby.
        supported = set()
        if edge_boxes:
            for y, x, r in component["runs"]:
                for left, right, ref in band_rows.get(y, []):
                    spend()
                    if x < right and left < r:
                        supported.add(ref)
        partitions = {}
        for y, x, r in component["runs"]:
            edges = [
                (ref, box) for ref, box in edge_boxes if ref in supported and box[1] <= y < box[3]
            ]
            missing = [s for s in slots if s["bounds"][1] <= y < s["bounds"][3]]
            bands = band_rows.get(y, [])
            spend(len(source_boxes) + len(edge_boxes) + len(slots) + len(bands) + 1)
            cuts = {x, r}
            for _, box in edges:
                cuts.update(v for v in (box[0], box[2]) if x < v < r)
            for s in missing:
                cuts.update(v for v in (s["bounds"][0], s["bounds"][2]) if x < v < r)
            for left, right, _ in bands:
                cuts.update(v for v in (left, right) if x < v < r)
            cuts = sorted(cuts)
            for left, right in zip(cuts, cuts[1:], strict=False):
                spend(len(edges) + len(missing) + len(bands) + 1)
                run = [y, left, right]
                core = tuple(sorted({ref for a, b, ref in bands if a <= left and right <= b}))
                edge_refs = (
                    tuple(sorted({ref for ref, box in edges if _inside_run(run, box)}))
                    if not core
                    else ()
                )
                slot_ids = tuple(s["id"] for s in missing if _inside_run(run, s["bounds"]))
                partitions.setdefault((core, edge_refs, slot_ids), []).append(run)
                split_count += 1
                require(split_count <= max_runs, "visual_run_budget")
        for (core, edge_refs, slot_ids), runs in partitions.items():
            fragments = _connected(runs, spend) if not core else [runs]
            for fragment in fragments:
                bounds = _bounds(fragment)
                # Short detached/attached marks stay ordinary/unresolved pixels.
                eligible = (
                    edge_refs
                    if max(bounds[2] - bounds[0], bounds[3] - bounds[1]) >= MIN_LINE_LENGTH
                    else ()
                )
                # Bounding boxes are observed locations, not exact glyph masks.
                # Offer only sources that intersect this residual component; never
                # attach all cell strings through the original connected grid.
                source_ids = set()
                if not core:
                    for ref, box in source_boxes:
                        spend()
                        if not (
                            bounds[0] < box[2]
                            and box[0] < bounds[2]
                            and bounds[1] < box[3]
                            and box[1] < bounds[3]
                        ):
                            continue
                        for y, left, right in fragment:
                            spend()
                            if box[1] <= y < box[3] and left < box[2] and box[0] < right:
                                source_ids.add(ref)
                                break
                source_ids = tuple(sorted(source_ids))
                key = (core, source_ids, eligible, slot_ids)
                group = groups.setdefault(
                    key,
                    {
                        "sourceIds": list(source_ids),
                        "tableRefs": list(core),
                        "ruleEdgeTableRefs": list(eligible),
                        "slotIds": list(slot_ids),
                        "onlyBoundaryPixels": bool(core),
                        "parts": [],
                    },
                )
                group["parts"].append(
                    {
                        "componentId": component["id"],
                        "runs": fragment,
                        "runsSha256": digest(fragment),
                    }
                )
                require(len(groups) <= max_units, "visual_unit_budget")
    return groups, split_count
