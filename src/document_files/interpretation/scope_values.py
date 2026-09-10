"""Bounded scalar-origin context, separate from labels and applicability decisions."""

from __future__ import annotations

import copy
import json


def scalar_value_evidence(region, definition):
    """Exact leaves only: never expand a container into a repeated value matrix."""
    scopes = {(t["space"], t["path"]) for t in definition["scope"]}
    return copy.deepcopy(
        [
            {k: e.get(k) for k in ("target", "status", "raw", "binding", "sourceRefs")}
            for e in region.value_evidence
            if (e["target"]["space"], e["target"]["path"]) in scopes
        ]
    )


class ScalarOriginCatalog:
    def __init__(self, observation, compiled):
        self.nodes = observation.nodes
        roles = {}
        for region in compiled:
            for mapping in region.row_scopes.values():
                for row, item in mapping["rows"].items():
                    role = {"row": int(row), "role": item["role"]}
                    for ref in item["sourceRefs"]:
                        entries = roles.setdefault((mapping["tableRef"], ref), [])
                        if role not in entries:
                            entries.append(role)
        self.cells = {}
        for table_ref, table in observation.tables.items():
            for cell in [*table.get("cells", []), *table.get("headerCells", [])]:
                ref = cell["sourceRef"]
                location = {
                    "tableRef": table_ref,
                    "row": cell["row"],
                    "column": cell["col"],
                    "rowSpan": cell.get("rowSpan", 1),
                    "columnSpan": cell.get("colSpan", 1),
                    "tableBasis": table.get("basis"),
                    # Keep every compiled interpretation if mappings disagree.
                    # Coordinates are observation; these roles are interpretation.
                    "rowRoles": sorted(
                        roles.get((table_ref, ref), []), key=lambda r: (r["row"], r["role"])
                    ),
                    "rowRoleBasis": "compiled_interpretation",
                }
                entries = self.cells.setdefault(ref, [])
                if location not in entries:
                    entries.append(location)

    def describe(self, evidence):
        origins, refs, complete = [], [], True
        for item in evidence:
            origin = {"observationStatus": item["status"], "valueSource": None}
            binding = item["binding"]
            if isinstance(binding, dict) and binding.get("sourceRef"):
                ref = binding["sourceRef"]
                raw = item["raw"]
                text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                origin.update(
                    valueSource=copy.deepcopy(binding),
                    valueText=text[:500],
                    valueTextTruncated=len(text) > 500,
                    tableLocations=copy.deepcopy(self.cells.get(ref, [])),
                )
                refs.append(ref)
                complete = complete and ref in self.nodes and len(text) <= 500
            origins.append(origin)
        return origins, refs, complete
