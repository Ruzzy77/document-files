"""Binary HWP note ownership from stored control/paragraph records, not text."""

from __future__ import annotations

from collections import defaultdict

from .model import ObservationBudgetExceeded

_KINDS = {"fn  ": "footnote", "en  ": "endnote"}
MAX_NOTE_RELATIONS = 100000


def _section(structure):
    section, stream = structure.get("section"), structure.get("section_stream")
    if type(section) is int and section > 0 and isinstance(stream, str) and stream:
        return section, stream
    return None


def _membership(structure):
    """Each native container frame retains the owning note's type and record ID."""
    result = []
    for frame in structure.get("container_path", []) or []:
        if not isinstance(frame, dict):
            continue
        kind, note = frame.get("kind"), frame.get("note")
        if kind in _KINDS.values() and isinstance(note, str) and note:
            member = kind, note, frame.get("owner_paragraph_record")
            result = [member]  # A nested note belongs to its nearest note control.
    return result


def add_hwp_note_relationships(doc, nodes):
    """Preserve controls, body anchors, note paragraphs and stored number sources.

    A body anchor denotes its exact owning paragraph, not an inferred character
    offset. Record IDs are local to one section stream. Multiple text segments of
    that same paragraph are retained; no first/closest-paragraph heuristic is used.
    """
    paragraphs, controls, members = defaultdict(list), defaultdict(list), defaultdict(list)
    for ref, node in nodes.items():
        structure = node.get("sourceStructure", {})
        section = _section(structure)
        if section is None:
            if structure.get("control_type") in _KINDS or _membership(structure):
                doc.issue("native_hwp_note_section_unavailable", sourceRef=ref)
            continue
        kind = _KINDS.get(structure.get("control_type"))
        if kind:
            note = structure.get("note")
            if not isinstance(note, str) or not note or type(structure.get("record")) is not int:
                doc.issue("native_hwp_note_control_unavailable", sourceRef=ref)
                continue
            controls[(*section, kind, note)].append(ref)
        else:
            paragraph = structure.get("paragraph_record")
            if type(paragraph) is int and paragraph >= 0:
                paragraphs[(*section, paragraph)].append(ref)
        for note_kind, note, owner in _membership(structure):
            members[(*section, note_kind, note)].append((ref, owner))

    def key_for(value):
        return tuple(
            value.get(k)
            for k in (
                "kind",
                "sourceRef",
                "targetRef",
                "basis",
                "noteKind",
                "nativeNoteId",
                "controlRef",
            )
        )

    seen = {key_for(r) for r in doc.relations}
    added = 0

    def relation(kind, source, target, **details):
        nonlocal added
        value = {
            "kind": kind,
            "sourceRef": source,
            "targetRef": target,
            "basis": "native_hwp_control",
            **details,
        }
        key = key_for(value)
        if source != target and key not in seen:
            if added >= MAX_NOTE_RELATIONS:
                doc.issue("native_hwp_note_relation_budget_exceeded")
                raise ObservationBudgetExceeded("HWP note relationship budget exceeded")
            seen.add(key)
            added += 1
            doc.relations.append(value)

    for key, refs in controls.items():
        if len(refs) != 1:
            for ref in refs:
                doc.issue("native_hwp_note_control_ambiguous", sourceRef=ref)
            continue
        control = refs[0]
        section, stream, kind, note = key
        structure = nodes[control]["sourceStructure"]
        owner = structure.get("owner_paragraph_record")
        anchors = paragraphs.get((section, stream, owner), []) if type(owner) is int else []
        contents, numbers = [], []
        for member, recorded_owner in members.get(key, []):
            if member == control:
                continue
            if recorded_owner is not None and (
                type(recorded_owner) is not int or recorded_owner != owner
            ):
                doc.issue("native_hwp_note_membership_conflict", sourceRef=member)
                continue
            item = nodes[member]["sourceStructure"]
            # A container frame proves membership, including nested table/text
            # contents. The current leaf's owner may be a note paragraph rather
            # than the original body paragraph; never reuse that leaf as anchor.
            relation("contains", control, member, noteKind=kind, nativeNoteId=note)
            if type(item.get("paragraph_record")) is int:
                contents.append(member)
            if (
                item.get("field_type") == "auto_number"
                and item.get("number_type") == kind
                and item.get("number_origin") == "stored_control_value"
                and type(item.get("stored_number")) is int
            ):
                numbers.append(member)
        if not anchors:
            doc.issue("native_hwp_note_owner_unavailable", sourceRef=control)
        if not contents:
            doc.issue("native_hwp_note_content_unavailable", sourceRef=control)
        for anchor in anchors:
            relation("contains", anchor, control, noteKind=kind, nativeNoteId=note)
            for content in contents:
                relation(
                    "noteReference",
                    anchor,
                    content,
                    noteKind=kind,
                    nativeNoteId=note,
                    controlRef=control,
                    anchorGranularity="paragraph",
                    numberSourceRefs=list(numbers),
                )
    for key, refs in members.items():
        if key not in controls:
            for ref, _ in refs:
                doc.issue("native_hwp_note_control_missing", sourceRef=ref)
