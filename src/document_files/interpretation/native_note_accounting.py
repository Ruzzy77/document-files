"""Account for verified empty native note structure, not its prose or meaning."""


def declared_note_dispositions(observation, region, catalog):
    """Stored controls/containers/numbers remain in nativeNotes and original nodes.

    This does not classify paragraphs, infer applicability, or exempt value
    candidates. Incomplete graphs and explicit model uncertainties remain open.
    """
    if catalog["status"] != "complete":
        return {}
    owned = set(region["nodeIds"])
    result = {}
    for object_ref, note in catalog["objects"].items():
        if note["status"] != "linked":
            continue
        numbers = {n["sourceRef"] for n in note["numberSources"]}
        paragraphs = set(note["bodyRefs"]) | set(note["contentRefs"])
        for ref in [object_ref, *note["memberRefs"]]:
            if ref not in owned or ref in paragraphs:
                continue
            node = observation.nodes[ref]
            if node.get("text") != "":
                continue
            semantic = node.get("semantic", {})
            # An empty text string is not proof of an empty business field.
            if ref in numbers:
                kind = "stored note number"
            elif not any(k in semantic for k in ("value", "field")):
                if ref == object_ref:
                    kind = "note control"
                elif node.get("sourceStructure", {}).get("structural_only") is True:
                    kind = "note container"
                else:
                    continue
            else:
                continue
            entry = result.setdefault(
                ref,
                {
                    "sourceRef": ref,
                    "role": "structural",
                    "explanation": f"Verified {kind} retained in nativeNotes and original sources",
                    "basis": "program_derived",
                    "nativeNoteBasis": "native_hwp_control",
                    "nativeNoteObjects": [],
                },
            )
            entry["nativeNoteObjects"].append(object_ref)
    return result
