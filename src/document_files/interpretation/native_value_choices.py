"""Offer only source-readable bindings, not guesses about a field's meaning."""

from __future__ import annotations

from .native_records import binding_within_occurrence
from .table_sources import resolve_quotes, source_inventory


class ValueChoices:
    """Reuse canonical reads and row anchors across handles without altering sources."""

    def __init__(self, observation, region, bindings, offered_ids):
        self.observation = observation
        self.region = region
        self.offered = set(offered_ids)
        self.bindings = bindings
        self.read_errors = {}
        self.anchors = {}
        self.inventories = {}

    def error(self, entry, binding_id):
        from .compiler import CompileError, _decimal_literal, _read

        binding = self.bindings.get(binding_id)
        if binding_id not in self.offered or binding is None:
            return "native_value_binding_not_offered"
        if binding["sourceRef"] not in entry["sourceRefs"]:
            return "native_value_binding_outside_sources"
        if binding.get("candidateStatus") == "unresolved_conflict":
            return "binding_has_unresolved_observation_conflict"
        if "sourceQuotes" in entry:
            key = (entry["recordId"], entry["rowId"])
            if key not in self.anchors:
                spans = []
                for quote in entry["sourceQuotes"]:
                    ref = quote["sourceRef"]
                    if ref not in self.inventories:
                        self.inventories[ref] = source_inventory(
                            self.observation, {**self.region, "nodeIds": [ref]}
                        )
                    spans.extend(resolve_quotes([quote], self.inventories[ref]))
                self.anchors[key] = spans
            if not binding_within_occurrence(binding, self.anchors[key]):
                return "logical_value_binding_outside_occurrence"
        key = (binding_id, entry["valueType"], entry["status"])
        if key not in self.read_errors:
            error = None
            try:
                # Read canonical observations, never the display-only exactText or
                # blank hint. These are the scalar compiler's unchanged rules.
                _, raw, _ = _read(
                    self.observation.bindings[binding_id],
                    "string" if entry["status"] == "blank" else entry["valueType"],
                    self.observation.nodes,
                )
                if (entry["status"] == "blank") != (raw == ""):
                    error = "blank_status_disagrees_with_observation"
                elif entry["valueType"] == "decimal" and raw and not _decimal_literal(raw):
                    error = "decimal_format_unresolved"
            except CompileError as exc:
                error = str(exc)
            self.read_errors[key] = error
        return self.read_errors[key]

    def binding_ids(self, entry):
        return [bid for bid in self.bindings if self.error(entry, bid) is None]
