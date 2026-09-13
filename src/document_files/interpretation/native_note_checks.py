"""Distinct declared note occurrences cannot become interchangeable value sources."""

from ..document_model.note_objects import note_context
from .compiler import CompileError


def validate_occurrences(structure, observation, region):
    from .native_structure import entries

    notes = note_context(observation, region)
    if notes["status"] != "complete":
        raise CompileError(notes["issue"])
    memberships, bodies = {}, {}
    for ref, note in notes["objects"].items():
        if note["status"] != "linked":
            continue
        for member in [ref, *note["memberRefs"]]:
            memberships.setdefault(member, set()).add(ref)
        for body in note["bodyRefs"]:
            s = observation.nodes[body]["sourceStructure"]
            bodies[body] = (s["section"], s["section_stream"], s["paragraph_record"])

    def note_ids(refs):
        return set().union(*(memberships.get(ref, set()) for ref in refs))

    for entry in entries(structure):
        if entry["status"] not in {"present", "blank"}:
            continue  # Uncertainty must not be silently promoted to a value.
        if len(note_ids(entry["sourceRefs"])) > 1:
            raise CompileError("native_structure_distinct_note_values:" + entry["handle"])
        if len({bodies[ref] for ref in entry["sourceRefs"] if ref in bodies}) > 1:
            raise CompileError("native_structure_distinct_body_values:" + entry["handle"])
    for meaning in structure.meanings:
        if (
            meaning.status == "interpreted"
            and meaning.kind in {"note", "unit", "condition"}
            and len(note_ids(q.sourceRef for q in meaning.sourceQuotes)) > 1
        ):
            raise CompileError("native_structure_distinct_note_meanings:" + meaning.id)
