"""Keep zero-record fragment evidence separate from the value of a joined array."""

from collections import defaultdict
from copy import deepcopy

from .validation import pointer


class ZeroRecordFragments:
    """Compilation-local origins; no model judgment or changes to source observations."""

    def __init__(self, regions):
        self.regions = regions
        self.entries = []
        for region in regions.values():
            for repeat_id, repeat in region.repeat_paths.items():
                if repeat.get("tableRef") is None or pointer(region.data, repeat["path"]) != []:
                    continue
                for evidence in region.value_evidence:
                    if evidence["target"] != {"space": "data", "path": repeat["path"]} or evidence[
                        "transformation"
                    ] not in {"observed_empty_repeat", "unresolved_repeat_rows"}:
                        continue
                    self.entries.append(
                        {
                            "region": region,
                            "evidence": evidence,
                            "owner": region.id,
                            "path": repeat["path"],
                            "reported": False,
                            "origin": {
                                "regionId": region.id,
                                "repeatId": repeat_id,
                                "tableRef": repeat["tableRef"],
                                "sourceRowStart": repeat["rowStart"],
                                "sourceRowEnd": repeat["rowEnd"],
                                "compiledRecordCount": 0,
                                "originalEvidence": deepcopy(evidence),
                            },
                        }
                    )

    def joined(self, left_id, right_id, left_path, right_path):
        """Describe each origin once, on its first accepted relation, before projection."""
        result = []
        for entry in self.entries:
            if entry["owner"] == right_id and entry["path"] == right_path:
                entry.update(owner=left_id, path=left_path)
            if not entry["reported"] and entry["owner"] == left_id and entry["path"] == left_path:
                result.append(deepcopy(entry["origin"]))
                entry["reported"] = True
        return result

    def reconcile(self):
        """A fragment may have zero rows while the joined result is nonempty.

        Original evidence is retained on the accepted relation, not erased. If all
        joined fragments still have zero rows, their aggregate keeps any uncertainty.
        Explicit blank cells are neither captured nor removed here.
        """
        groups = defaultdict(list)
        for entry in self.entries:
            if entry["reported"]:
                groups[(entry["owner"], entry["path"])].append(entry)
        for (owner, path), entries in groups.items():
            rows = pointer(self.regions[owner].data, path)
            active = [
                e for e in entries if any(v is e["evidence"] for v in e["region"].value_evidence)
            ]
            if not rows and active:
                kept = active[0]["evidence"]
                for key in ("sourceRefs", "semanticIds"):
                    kept[key] = list(dict.fromkeys(v for e in active for v in e["evidence"][key]))
                if any(e["origin"]["originalEvidence"]["status"] == "uncertain" for e in entries):
                    kept.update(status="uncertain", transformation="unresolved_repeat_rows")
                active = active[1:]
            for entry in active:
                region, evidence = entry["region"], entry["evidence"]
                region.value_evidence[:] = [v for v in region.value_evidence if v is not evidence]
