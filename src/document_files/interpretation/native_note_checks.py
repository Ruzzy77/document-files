"""Distinct declared note occurrences cannot become interchangeable value sources."""

import json

from ..document_model.note_objects import note_context
from .compiler import CompileError

MAX_CONFLICTS = 64
MAX_FEEDBACK_BYTES = 16000


class OccurrenceError(CompileError):
    def __init__(self, diagnostics):
        super().__init__(diagnostics[0])
        self.diagnostics = diagnostics


def ownership(observation, region):
    """Exact native owners, shared by response constraints and acceptance checks."""
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
            identity = (s["section"], s["section_stream"], s["paragraph_record"])
            bodies[body] = identity
    # Use a source ID for feedback, not a source-supplied label or paragraph number.
    first = {}
    for ref in observation.nodes:
        if ref in bodies:
            first.setdefault(bodies[ref], ref)
    return memberships, {ref: first[key] for ref, key in bodies.items()}


def validate_occurrences(structure, observation, region):
    from .native_structure import entries

    memberships, bodies = ownership(observation, region)
    diagnostics = []

    def conflict(code, handle, refs, owners):
        used = {ref: sorted(owners[ref]) for ref in refs if ref in owners}
        if len(set().union(*map(set, used.values()))) <= 1:
            return
        diagnostics.append(code + ":" + handle + ":" + json.dumps(used, separators=(",", ":")))
        if (
            len(diagnostics) > MAX_CONFLICTS
            or len(json.dumps(diagnostics, ensure_ascii=False).encode()) > MAX_FEEDBACK_BYTES
        ):
            # Never return a successful-looking partial list of conflicts.
            raise OccurrenceError(["native_structure_occurrence_feedback_budget_exceeded"])

    for entry in entries(structure):
        if entry["status"] not in {"present", "blank"}:
            continue  # Uncertainty must not be silently promoted to a value.
        conflict(
            "native_structure_distinct_note_values",
            entry["handle"],
            entry["sourceRefs"],
            memberships,
        )
        conflict(
            "native_structure_distinct_body_values",
            entry["handle"],
            entry["sourceRefs"],
            {ref: {owner} for ref, owner in bodies.items()},
        )
    for ordinal, meaning in enumerate(structure.meanings, 1):
        if meaning.status == "interpreted" and meaning.kind in {"note", "unit", "condition"}:
            conflict(
                "native_structure_distinct_note_meanings",
                f"@meaning{ordinal}",
                [q.sourceRef for q in meaning.sourceQuotes],
                memberships,
            )
    if diagnostics:
        raise OccurrenceError(diagnostics)


def wire_feedback(value, observation, region):
    """Only decoded, known source ownership is quoted back; never raw model prose."""
    from .native_structure_wire import decode

    try:
        structure = decode(value, observation, region)
        validate_occurrences(structure, observation, region)
    except OccurrenceError as exc:
        return exc.diagnostics
    except (ValueError, TypeError, KeyError):
        pass  # Malformed wire replies keep their schema-owned diagnostics.
    return []
