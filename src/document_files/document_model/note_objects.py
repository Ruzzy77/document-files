"""Source-declared HWP note objects, not AI fields or inferred text groups."""

from __future__ import annotations

import json
from collections import defaultdict

from .hwp_notes import _KINDS, _membership, _section

VERSION = "document-files.native-note-objects.v1"
MAX_OBJECTS = 1000
MAX_RELATIONS = 200000
MAX_BYTES = 2 * 1024 * 1024


def note_objects(observation):
    """Read the verified control graph; never infer a note from words or numbering.

    The original nodes/graph are retained even when this bounded derived inventory
    cannot be built. A partial inventory never returns a successful prefix.
    """
    empty = {"version": VERSION, "status": "complete", "objects": {}}
    if observation.provenance.get("format") != "hwp":
        return empty

    def incomplete(code):
        return {**empty, "status": "incomplete", "issue": code}

    if len(observation.relations) > MAX_RELATIONS:
        return incomplete("native_note_objects_budget_exceeded")
    nodes = observation.nodes
    controls, identities = {}, defaultdict(list)
    for ref, node in nodes.items():
        s = node.get("sourceStructure", {})
        kind, section = _KINDS.get(s.get("control_type")), _section(s)
        if kind and section and isinstance(s.get("note"), str) and s["note"]:
            controls[ref] = (section, kind, s["note"], s.get("owner_paragraph_record"))
            identities[(*section, kind, s["note"])].append(ref)
            if len(controls) > MAX_OBJECTS:
                return incomplete("native_note_objects_budget_exceeded")
    members, anchors, paragraphs = defaultdict(set), defaultdict(set), defaultdict(set)
    for edge in observation.relations:
        if edge.get("basis") != "native_hwp_control":
            continue
        source, target = edge.get("sourceRef"), edge.get("targetRef")
        control = source if edge.get("kind") == "contains" else edge.get("controlRef")
        if control not in controls or source not in nodes or target not in nodes:
            continue
        section, kind, note, owner = controls[control]
        child = nodes[target].get("sourceStructure", {})
        membership = _membership(child)
        if not (
            _section(child) == section
            and membership
            and membership[0][:2] == (kind, note)
            and (membership[0][2] is None or membership[0][2] == owner)
            and edge.get("noteKind") == kind
            and edge.get("nativeNoteId") == note
        ):
            return incomplete("native_note_objects_graph_conflict")
        if edge.get("kind") == "contains":
            members[control].add(target)
        elif edge.get("kind") == "noteReference":
            body = nodes[source].get("sourceStructure", {})
            if not (
                _section(body) == section
                and type(owner) is int
                and type(body.get("paragraph_record")) is int
                and body["paragraph_record"] == owner
                and type(child.get("paragraph_record")) is int
            ):
                return incomplete("native_note_objects_graph_conflict")
            anchors[control].add(source)
            paragraphs[control].add(target)
    order = {ref: i for i, ref in enumerate(nodes)}
    result = {}
    for control, (section, kind, note, _owner) in controls.items():
        contained = sorted(members[control], key=order.__getitem__)
        if not paragraphs[control] <= members[control]:
            return incomplete("native_note_objects_graph_conflict")
        numbers = []
        for ref in contained:
            s = nodes[ref].get("sourceStructure", {})
            if (
                s.get("field_type") == "auto_number"
                and s.get("number_type") == kind
                and s.get("number_origin") == "stored_control_value"
                and type(s.get("stored_number")) is int
            ):
                value = nodes[ref].get("semantic", {}).get("value", {})
                if (
                    value.get("kind") != "integer"
                    or type(value.get("value")) is not int
                    or (value["value"] != s["stored_number"])
                ):
                    return incomplete("native_note_objects_number_conflict")
                numbers.append(
                    {"sourceRef": ref, "path": "/semantic/value/value", "value": value["value"]}
                )
        linked = len(identities[(*section, kind, note)]) == 1 and bool(
            anchors[control] and paragraphs[control]
        )
        result[control] = {
            "kind": kind,
            "status": "linked" if linked else "unresolved",
            "bodyRefs": sorted(anchors[control], key=order.__getitem__),
            "contentRefs": sorted(paragraphs[control], key=order.__getitem__),
            "memberRefs": contained,
            "numberSources": numbers,
        }
    value = {**empty, "objects": result}
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_BYTES:
        return incomplete("native_note_objects_budget_exceeded")
    return value


def note_context(observation, region):
    """Keep whole object identity; context does not grant another region's values."""
    value = note_objects(observation)
    owned = set(region["nodeIds"])
    visible = owned | set(region.get("contextNodeIds", []))
    value["objects"] = {
        ref: {**obj, "ownedRefs": [n for n in [ref, *obj["memberRefs"]] if n in owned]}
        for ref, obj in value["objects"].items()
        if visible.intersection([ref, *obj["memberRefs"], *obj["bodyRefs"]])
    }
    if value["objects"]:
        value["use"] = (
            "Stored note objects, not inferred fields. bodyRefs are referring body paragraphs; "
            "contentRefs are paragraphs inside one note. Different object IDs stay distinct, "
            "even with equal text/numbers. Several paragraphs in one object are not several notes. "
            "ownedRefs grants value ownership; other refs are context only. Preserve actual "
            "attributes and applicability, not generic body/note-text fields just to copy content. "
            "Do not offer different notes or different body paragraphs as one scalar's alternative "
            "value sources; definition references can be shared. Keep separate note/unit/condition "
            "meanings for separate notes. Empty controls/containers and stored automatic note "
            "numbers are structural, not missing business values: account for their owned refs "
            "in dispositions and unread number bindings as structural in excludedBindings. "
            "A note type does not imply a heading."
        )
    return value
