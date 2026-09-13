"""Source contradictions in blank row choices; never infer another row role."""

from ..document_model.table_headers import observed_rows
from .table_sources import _source_text

BLANK_ROW_ERROR = "table_blank_row_conflicts_with_source"


def blank_row_conflicts(observation, table, roles):
    """Keep exact content and uncertainty distinct from an observed empty string.

    A row role covers its whole occupied cells, not a possibly empty text excerpt.
    A missing grid slot is not a cell and cannot supply evidence of blankness.
    Multiple paragraph sources and cells spanning several rows are all inspected.
    The result contains source IDs only, never copied values or replacement roles.
    """
    blank_rows = {row for row, role in roles.items() if role == "blank"}
    if not blank_rows:
        return []
    conflicts = {
        binding["sourceRef"]
        for binding in observation.bindings.values()
        if binding.get("candidateStatus") == "unresolved_conflict"
    }
    result = []
    for row, cells in observed_rows(table["cells"]).items():
        if row not in blank_rows:
            continue
        nonempty, unverified = set(), set()
        for cell in cells:
            for ref in dict.fromkeys([cell["sourceRef"], *cell.get("sourceRefs", [])]):
                node = observation.nodes.get(ref)
                if not isinstance(node, dict):
                    unverified.add(ref)
                    continue
                text = _source_text(node)[1]
                ambiguous = (node.get("semanticInput") or {}).get("role") == "unresolved_conflict"
                if ref in conflicts or ambiguous or not isinstance(text, str):
                    unverified.add(ref)
                elif text != "":
                    # Whitespace, zero/false spellings and formulas are content.
                    nonempty.add(ref)
        if nonempty or unverified:
            result.append(
                {
                    "row": row,
                    "nonemptySourceRefs": sorted(nonempty),
                    "unverifiedSourceRefs": sorted(unverified),
                }
            )
    return result
