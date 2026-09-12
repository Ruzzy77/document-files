"""Conservative scalar identity: equal source values do not identify equal fields."""


def exact_field_identity(field, candidate, nodes, destination):
    """Fold only an identical definition/read at an identical compiler destination.

    No text normalization, label translation or lexical comparison decides identity.
    Different labels, definition sources, types, presence or destinations remain
    separate even if their values happen to use the same source binding.
    """
    start, end = candidate.get("start"), candidate.get("end")
    if candidate["path"] == "/text" and start is None and end is None:
        start, end = 0, len(nodes[candidate["sourceRef"]]["text"])
    return (
        tuple(destination),
        field.label,
        tuple(sorted(set(field.definitionRefs))),
        field.valueType,
        field.status,
        candidate["sourceRef"],
        candidate["path"],
        start,
        end,
    )
