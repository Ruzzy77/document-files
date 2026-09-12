"""Native text values and logical occurrences, without inventing observed tables."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import ChainMap

from .table_sources import SourceReviewError, resolve_quotes, source_inventory


def enabled(observation, region):
    return observation.provenance.get("format") in {"hwp", "hwpx"} and not (
        region.get("tableRef") or region.get("tableContextRef")
    )


def prepare(ir, observation, region):
    """Create a compilation-local binding overlay; never mutate original observations."""
    from .compiler import CompileError

    quoted = any(f.sourceQuote is not None for f in ir.fields)
    if not quoted and not ir.logicalRecords:
        return ir, observation, region, {}, {}
    if not enabled(observation, region):
        raise CompileError("logical_content_requires_native_text_region")
    if (
        sum(len(r.rows) for r in ir.logicalRecords) > 1000
        or sum(len(row.values) for record in ir.logicalRecords for row in record.rows) > 10000
    ):
        raise CompileError("logical_record_grounding_budget_exceeded")
    inventory = source_inventory(observation, region)
    # Resolve against one owned source at a time. The shared resolver validates
    # the entire inventory on each call; never multiply that by every value.
    inventories = {}
    originals = {s["sourceRef"]: s for s in inventory["sources"]}
    scans = 0
    cache, derived, anchors = {}, {}, {}
    candidate_ids = set(region["bindingIds"])

    def ranges(quotes):
        nonlocal scans
        result = []
        for quote in quotes:
            value = quote.model_dump(exclude_unset=True)
            key = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if key not in cache:
                source = originals.get(value["sourceRef"])
                scans += len(source["text"]) if source else 0
                if scans > 5000000 or len(cache) >= 10000:
                    raise CompileError("native_quote_grounding_budget_exceeded")
                try:
                    ref = value["sourceRef"]
                    if ref not in originals:
                        raise SourceReviewError("unknown_quote_source")
                    if ref not in inventories:
                        inventories[ref] = source_inventory(
                            observation, {**region, "nodeIds": [ref]}
                        )
                    cache[key] = resolve_quotes([value], inventories[ref])[0]
                except SourceReviewError as exc:
                    raise CompileError(str(exc)) from None
            result.append(copy.deepcopy(cache[key]))
        positions = {(v["sourceRef"], v["start"], v["end"]) for v in result}
        if len(positions) != len(result):
            raise CompileError("duplicate_source_quote")
        return result

    def value_binding(value):
        if value.sourceQuote is None:
            return value
        if value.bindingId is not None or value.status != "present":
            raise CompileError("quoted_value_requires_present_and_no_binding_id")
        span = ranges([value.sourceQuote])[0]
        key = (
            "quote:"
            + hashlib.sha256(
                json.dumps(span, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        )
        derived[key] = {
            **{k: span[k] for k in ("sourceRef", "path", "start", "end")},
            "candidateRole": "value",
            "basis": "verified_source_quote",
            "textSHA256": span["textSHA256"],
        }
        candidate_ids.add(key)
        return value.model_copy(update={"bindingId": key})

    fields = [value_binding(f) for f in ir.fields]
    # Node insertion order is the observation's canonical source order. Native
    # paragraph ordinals can restart in another section and are not global.
    order = {ref: index for index, ref in enumerate(observation.nodes)}
    records = []
    for record in ir.logicalRecords:
        if bool(record.rows) == bool(record.emptySourceQuotes):
            raise CompileError("logical_record_requires_rows_or_empty_evidence")
        if len({r.id for r in record.rows}) != len(record.rows):
            raise CompileError("duplicate_logical_row_id")
        row_views, seen_ranges = [], []
        for row in record.rows:
            row_ranges = ranges(row.sourceQuotes)
            seen_ranges.extend(row_ranges)
            values = [value_binding(v) for v in row.values]
            row_refs = {span["sourceRef"] for span in row_ranges}
            for value in values:
                if not set(value.sourceRefs) <= row_refs:
                    raise CompileError("logical_value_evidence_outside_occurrence")
                if value.status in {"present", "blank"}:
                    binding = derived.get(value.bindingId) or (
                        observation.bindings.get(value.bindingId)
                        if value.bindingId in candidate_ids
                        else None
                    )
                    if binding is None or not any(
                        span["sourceRef"] == binding["sourceRef"]
                        and (
                            binding["path"] != "/text"
                            or (
                                type(binding.get("start")) is int
                                and span["start"]
                                <= binding["start"]
                                <= binding["end"]
                                <= span["end"]
                            )
                        )
                        for span in row_ranges
                    ):
                        raise CompileError("logical_value_binding_outside_occurrence")
                elif value.bindingId is not None:
                    raise CompileError("logical_missing_value_cannot_bind_source")
            anchors[(record.id, row.id)] = row_ranges
            row_views.append(row.model_copy(update={"values": values}))
        # Sorting keeps validation O(n log n), including many occurrences in
        # one source. Redundant overlapping anchors within a row are invalid too.
        previous = None
        for span in sorted(seen_ranges, key=lambda s: (s["sourceRef"], s["start"], s["end"])):
            if (
                previous is not None
                and span["sourceRef"] == previous["sourceRef"]
                and span["start"] < previous["end"]
            ):
                raise CompileError("logical_record_occurrences_overlap")
            previous = span
        row_views.sort(
            key=lambda row: min(
                (order[s["sourceRef"]], s["start"]) for s in anchors[(record.id, row.id)]
            )
        )
        if not row_views:
            anchors[(record.id, None)] = ranges(record.emptySourceQuotes)
        records.append(record.model_copy(update={"rows": row_views}))
    local = copy.copy(observation)
    local.bindings = ChainMap(derived, observation.bindings)
    view = {**region, "bindingIds": [*region["bindingIds"], *sorted(derived)]}
    return (
        ir.model_copy(update={"fields": fields, "logicalRecords": records}),
        local,
        view,
        derived,
        anchors,
    )


def loses_logical_content(previous, candidate):
    """Preserve every accepted occurrence and column, including unbound missing cells.

    Repairs may improve presence/meaning decisions, but not delete a record or
    rename its column IDs to make an unresolved value disappear. Source anchors,
    not model row IDs or output array positions, identify occurrences.
    """

    def inventory(fragment):
        result = {}
        for rid, record in fragment.logical_coverage.items():
            rows = {}
            for row in record["rows"]:
                ranges = frozenset(
                    (s["sourceRef"], s["start"], s["end"]) for s in row["sourceRanges"]
                )
                rows[ranges] = set(record["columnIds"])
            result[rid] = (rows, record["emptySourceRanges"])
        return result

    old, new = inventory(previous), inventory(candidate)
    for rid, (rows, empty) in old.items():
        if rid not in new:
            return True
        other, other_empty = new[rid]
        if empty and empty != other_empty:
            return True
        if any(key not in other or not columns <= other[key] for key, columns in rows.items()):
            return True
    return False
