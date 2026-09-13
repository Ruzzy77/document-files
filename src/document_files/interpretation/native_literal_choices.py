"""Optional exact numeral selectors for frozen native fields, never semantic guesses.

A selector is only an easier spelling of an existing source quote. Original text,
source/row ownership, scalar conversion and required-candidate accounting stay intact.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .native_records import binding_within_occurrence
from .table_sources import resolve_quotes, source_inventory

# Keep punctuation-separated digit runs together so a failed locale/precision read
# cannot become a shorter interior number. Sentence-final punctuation stays outside.
# This is an optional source-choice aid, not a complete numeric syntax recognizer.
_NUMERAL = re.compile(
    r"(?<![\w.,])[+-]?(?:[0-9]+(?:[.,][0-9]+)*|\.[0-9]+)"
    r"(?:[eE][+-]?[0-9]+)?(?!\w|[.,][0-9])"
)
MAX_LITERALS = 256
MAX_SOURCE_CHARS = 100_000
CONTEXT_CHARS = 24
NUMERIC_TYPES = frozenset({"integer", "number", "decimal"})
SOURCE_SYSTEM = """
When a handle offers literalIds, kind literal selects the correct literalId's original
numeral. Its before/after excerpts distinguish equal values in different attributes;
they are context, not replacement values. Complete source remains in nodes. Code resolves
the exact quote and occurrence, not a row number. An accepted literal selection in prior
reads has the same source meaning as that catalogue quote. This optional aid never
permits omission of a field or source, nor does it change type, status or item ownership.
"""


class LiteralChoices:
    def __init__(self, observation, region, entries):
        self.observation = observation
        self.region = region
        self.catalog = {}
        self.allowed = {}
        self.status = "not_applicable"
        relevant = [
            e for e in entries if e["status"] == "present" and e["valueType"] in NUMERIC_TYPES
        ]
        if not relevant:
            return
        inventory = source_inventory(observation, region)
        needed_refs = {ref for entry in relevant for ref in entry["sourceRefs"]}
        sources = [s for s in inventory["sources"] if s["sourceRef"] in needed_refs]
        if sum(len(observation.nodes[s["sourceRef"]]["text"]) for s in sources) > MAX_SOURCE_CHARS:
            self.status = "source_limit"
            return
        candidates = []
        conflicting = {
            b["sourceRef"]
            for b in observation.bindings.values()
            if b.get("candidateStatus") == "unresolved_conflict"
        }
        for source in sources:
            if source["sourceRef"] in conflicting:
                continue
            # Scan the original node, then apply ownership. A clipped source view
            # must not turn the interior of 001.2300 into a new numeral 1.23.
            original = observation.nodes[source["sourceRef"]]["text"]
            for match in _NUMERAL.finditer(original):
                if not (source["start"] <= match.start() < match.end() <= source["end"]):
                    continue
                if len(candidates) == MAX_LITERALS:
                    # Omit the whole optional aid, not a misleading first N values.
                    # All original source and the ordinary quote path remain available.
                    self.status = "candidate_limit"
                    return
                candidates.append(
                    (source, match.start() - source["start"], match.end() - source["start"])
                )
        all_candidates = {}
        for index, (source, start, end) in enumerate(candidates, 1):
            text = source["text"][start:end]
            # SourceQuote occurrence counts ALL exact substring matches, including
            # overlapping matches and text inside other numerals. Never let the
            # model confuse this with row ordinals or the numeral scanner's index.
            occurrence, cursor = 0, 0
            while True:
                at = source["text"].find(text, cursor)
                if at < 0 or at >= start:
                    break
                occurrence += 1
                cursor = at + 1
            all_candidates[f"@literal{index}"] = {
                "quote": {"sourceRef": source["sourceRef"], "text": text, "occurrence": occurrence},
                "binding": {
                    "sourceRef": source["sourceRef"],
                    "path": "/text",
                    "start": source["start"] + start,
                    "end": source["start"] + end,
                },
                "before": source["text"][max(0, start - CONTEXT_CHARS) : start],
                "after": source["text"][end : end + CONTEXT_CHARS],
            }
        anchors, reads = {}, {}
        for entry in relevant:
            owned = set(entry["sourceRefs"])
            row_spans = None
            if "sourceQuotes" in entry:
                key = (entry["recordId"], entry["rowId"])
                if key not in anchors:
                    anchors[key] = resolve_quotes(entry["sourceQuotes"], inventory)
                row_spans = anchors[key]
            ids = []
            for lid, candidate in all_candidates.items():
                binding = candidate["binding"]
                if binding["sourceRef"] not in owned or (
                    row_spans is not None and not binding_within_occurrence(binding, row_spans)
                ):
                    continue
                key = (lid, entry["valueType"])
                if key not in reads:
                    from .compiler import CompileError, _decimal_literal, _read

                    try:
                        _, raw, _ = _read(binding, entry["valueType"], observation.nodes)
                        reads[key] = entry["valueType"] != "decimal" or _decimal_literal(raw)
                    except CompileError:
                        reads[key] = False
                if reads[key]:
                    ids.append(lid)
                    self.catalog[lid] = {
                        **deepcopy(candidate["quote"]),
                        "before": candidate["before"],
                        "after": candidate["after"],
                    }
            self.allowed[entry["handle"]] = ids
        # Stable original source order, independent of field enumeration order.
        self.catalog = {lid: self.catalog[lid] for lid in all_candidates if lid in self.catalog}
        self.status = "available"

    def ids(self, entry):
        return self.allowed.get(entry["handle"], [])


def selection_schema(ids):
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "literal"},
            "literalId": {"type": "string", "enum": ids},
        },
        "required": ["kind", "literalId"],
        "additionalProperties": False,
    }


def as_quote(selection, catalog):
    """Called only after the regenerated per-handle source-choice schema accepts it."""
    if selection.get("kind") != "literal":
        return deepcopy(selection)
    literal = catalog[selection["literalId"]]
    return {
        "kind": "quote",
        "quote": {key: literal[key] for key in ("sourceRef", "text", "occurrence")},
    }
