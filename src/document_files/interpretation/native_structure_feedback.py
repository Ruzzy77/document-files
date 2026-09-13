"""Locate colliding native properties without renaming or merging source items."""

from collections import defaultdict


def property_collisions(wire, catalog):
    """Only called after validated structure fails duplicate-property compilation.

    Paths are internal comparison keys, not diagnostic text. The feedback contains
    program-issued positions and schema-validated source references, never labels,
    key spellings, values or user-supplied group identifiers.
    """
    targets = defaultdict(list)
    groups = {None: ()}
    pending = list(enumerate(wire.get("groups", []), 1))

    def location(item, parent):
        handle = item.get("targetHandle")
        if handle is not None:
            target = catalog.get(handle)
            return tuple(target["tokens"]) if target is not None else None
        if parent not in groups:
            return None
        return (*groups[parent], item["key"])

    def remember(path, ref, sources):
        if path is not None:
            targets[path].append((ref, list(dict.fromkeys(sources))))

    for _ in range(len(pending) + 1):
        unresolved = []
        for index, item in pending:
            path = location(item, item.get("parentId"))
            if path is None:
                unresolved.append((index, item))
                continue
            groups[item["id"]] = path
            remember(path, f"group:{index}", item["sourceRefs"])
        if len(unresolved) == len(pending):
            break
        pending = unresolved
    for kind, key in [("field", "fields"), ("record", "records")]:
        for index, item in enumerate(wire.get(key, []), 1):
            remember(
                location(item, item.get("groupId")),
                f"{kind}:{index}",
                item["sourceRefs" if kind == "field" else "definitionRefs"],
            )
    result = []
    collision = 0
    for entries in targets.values():
        if len(entries) < 2:
            continue
        collision += 1
        # Show all conflicting positions up to the diagnostic bound; the complete
        # original source remains in the request. This never changes the response.
        for ref, sources in entries:
            result.append(
                f"native_structure_property_collision:{collision}:{ref}:sources="
                + ",".join(sources[:50])
            )
            if len(result) == 10:
                return ["duplicate_data_property", *result]
    return ["duplicate_data_property", *result]
