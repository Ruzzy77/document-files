"""Immutable Unicode source ranges for revisable table meaning decisions.

This module checks source identity and coverage, never semantic correctness.
Offsets address the original Python string; text is never normalized.
"""

from __future__ import annotations

import hashlib
import json

SOURCE_INVENTORY_VERSION = "document-files.table-source-inventory.v1"
_RANGE_KEYS = {"sourceRef", "path", "start", "end", "text", "textSHA256"}


class SourceReviewError(ValueError):
    """Invalid source inventory, quotation or review reference."""


def _require(condition, message):
    if not condition:
        raise SourceReviewError(message)


def _hash(text):
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except (AttributeError, UnicodeError) as exc:
        raise SourceReviewError("source_text_not_utf8") from exc


def _inventory_hash(sources):
    return _hash(
        json.dumps(
            {"version": SOURCE_INVENTORY_VERSION, "sources": sources},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _range(ref, start, text):
    return {
        "sourceRef": ref,
        "path": "/text",
        "start": start,
        "end": start + len(text),
        "text": text,
        "textSHA256": _hash(text),
    }


def source_inventory(observation, region):
    """Freeze owned text views, excluding context-only and source_text nodes."""
    nodes = (
        observation.get("nodes")
        if isinstance(observation, dict)
        else getattr(observation, "nodes", None)
    )
    _require(isinstance(nodes, dict) and isinstance(region, dict), "invalid_source_region")
    refs, views = region.get("nodeIds", []), region.get("nodeViews", {})
    _require(isinstance(refs, list) and isinstance(views, dict), "invalid_source_region")
    sources, seen = [], set()
    for ref in refs:
        _require(isinstance(ref, str) and bool(ref), "invalid_source_ref")
        _require(ref not in seen, "duplicate_source_ref")
        seen.add(ref)
        node = nodes.get(ref)
        if not isinstance(node, dict) or node.get("semanticRole") == "source_text":
            continue
        text = node.get("text")
        if not isinstance(text, str):
            continue
        low, high = 0, len(text)
        if ref in views:
            view = views[ref]
            _require(isinstance(view, dict), "invalid_source_view")
            low, high = view.get("start"), view.get("end")
            _require(
                type(low) is int and type(high) is int and 0 <= low <= high <= len(text),
                "invalid_source_view",
            )
        sources.append(_range(ref, low, text[low:high]))
    return {
        "version": SOURCE_INVENTORY_VERSION,
        "sha256": _inventory_hash(sources),
        "sources": sources,
    }


def _sources(inventory):
    _require(isinstance(inventory, dict), "invalid_source_inventory")
    _require(inventory.get("version") == SOURCE_INVENTORY_VERSION, "source_inventory_version")
    sources = inventory.get("sources")
    _require(isinstance(sources, list), "invalid_source_inventory")
    by_ref = {}
    for item in sources:
        _require(isinstance(item, dict) and set(item) == _RANGE_KEYS, "invalid_source_range")
        ref, low, high, text = (item[k] for k in ("sourceRef", "start", "end", "text"))
        _require(isinstance(ref, str) and bool(ref) and ref not in by_ref, "invalid_source_ref")
        _require(
            item["path"] == "/text"
            and type(low) is int
            and type(high) is int
            and 0 <= low <= high
            and isinstance(text, str)
            and len(text) == high - low,
            "invalid_source_range",
        )
        _require(item["textSHA256"] == _hash(text), "source_text_checksum_mismatch")
        by_ref[ref] = item
    _require(inventory.get("sha256") == _inventory_hash(sources), "source_inventory_checksum")
    return by_ref


def resolve_quotes(quotes, inventory):
    """Resolve exact nonempty quotes; repeated/overlapping matches use occurrence."""
    sources = _sources(inventory)
    _require(isinstance(quotes, list), "invalid_source_quotes")
    resolved, seen = [], set()
    for quote in quotes:
        _require(
            isinstance(quote, dict)
            and {"sourceRef", "text"} <= set(quote) <= {"sourceRef", "text", "occurrence"},
            "invalid_source_quote",
        )
        ref, text = quote["sourceRef"], quote["text"]
        _require(isinstance(ref, str) and ref in sources, "unknown_quote_source")
        _require(isinstance(text, str) and bool(text), "empty_or_invalid_source_quote")
        source, positions, offset = sources[ref], [], 0
        while True:
            found = source["text"].find(text, offset)
            if found < 0:
                break
            positions.append(found)
            offset = found + 1
        _require(bool(positions), "quote_not_in_source")
        occurrence = quote.get("occurrence", 0)
        _require(
            type(occurrence) is int
            and 0 <= occurrence < len(positions)
            and (len(positions) == 1 or "occurrence" in quote),
            "quote_occurrence_required_or_invalid",
        )
        start = source["start"] + positions[occurrence]
        key = (ref, start, start + len(text))
        _require(key not in seen, "duplicate_source_quote")
        seen.add(key)
        resolved.append(_range(ref, start, text))
    return resolved


def _checked_range(item, sources):
    _require(isinstance(item, dict) and set(item) == _RANGE_KEYS, "invalid_meaning_source_range")
    ref = item["sourceRef"]
    _require(isinstance(ref, str) and ref in sources, "unknown_meaning_source")
    source, low, high = sources[ref], item["start"], item["end"]
    _require(
        item["path"] == "/text"
        and type(low) is int
        and type(high) is int
        and source["start"] <= low < high <= source["end"],
        "meaning_source_range_outside_view",
    )
    expected = _range(ref, low, source["text"][low - source["start"] : high - source["start"]])
    _require(item == expected, "meaning_source_range_mismatch")
    return ref, low, high


def review_ranges(meanings, reviews, inventory):
    """Partition owned sources; reviews affect only text outside direct quotations.

    Overlapping quotations retain all referring meaning IDs. Whitespace-only gaps
    need no review and are not emitted. Empty sources remain in the inventory.
    Uncertain meanings preserve their direct evidence and flag its source unresolved.
    """
    sources = _sources(inventory)
    _require(isinstance(meanings, list) and isinstance(reviews, list), "invalid_source_reviews")
    quoted = {ref: [] for ref in sources}
    ids, uncertain = set(), set()
    for meaning in meanings:
        _require(isinstance(meaning, dict), "invalid_source_meaning")
        mid = meaning.get("id")
        _require(isinstance(mid, str) and bool(mid) and mid not in ids, "invalid_meaning_id")
        _require(
            isinstance(meaning.get("status"), str)
            and meaning["status"] in {"interpreted", "uncertain"},
            "invalid_meaning_status",
        )
        ranges = meaning.get("sourceRanges")
        _require(isinstance(ranges, list) and bool(ranges), "meaning_source_ranges_required")
        ids.add(mid)
        seen = set()
        for item in ranges:
            ref, low, high = _checked_range(item, sources)
            _require((ref, low, high) not in seen, "duplicate_meaning_source_range")
            seen.add((ref, low, high))
            quoted[ref].append((low, high, mid))
            if meaning["status"] == "uncertain":
                uncertain.add(ref)
    decisions = {}
    for review in reviews:
        _require(
            isinstance(review, dict) and set(review) == {"sourceRefs", "role", "explanation"},
            "invalid_source_review",
        )
        refs, role, explanation = (review[k] for k in ("sourceRefs", "role", "explanation"))
        _require(isinstance(refs, list) and bool(refs), "source_review_refs_required")
        _require(
            isinstance(role, str)
            and role in {"no_additional_meaning", "unresolved", "unreviewed"}
            and isinstance(explanation, str)
            and bool(explanation.strip()),
            "invalid_source_review_role_or_explanation",
        )
        for ref in refs:
            _require(isinstance(ref, str) and ref in sources, "unknown_review_source")
            _require(ref not in decisions, "duplicate_source_review")
            decisions[ref] = (role, explanation)
    result, unreviewed, unresolved = [], [], []
    for ref, source in sources.items():
        intervals = quoted[ref]
        boundaries = sorted(
            {source["start"], source["end"], *(v for q in intervals for v in q[:2])}
        )
        # An explicit deferred review remains pending even when another anchor
        # quotes the whole source or the source is empty. Never erase that state.
        missing = decisions.get(ref, (None,))[0] == "unreviewed"
        unclear = ref in uncertain
        for low, high in zip(boundaries, boundaries[1:], strict=False):
            text = source["text"][low - source["start"] : high - source["start"]]
            meaning_ids = list(
                dict.fromkeys(mid for a, b, mid in intervals if a <= low < high <= b)
            )
            item = _range(ref, low, text)
            if meaning_ids:
                item.update(role="meaning", meaningIds=meaning_ids)
            elif not text.strip():
                continue
            elif ref in decisions:
                role, explanation = decisions[ref]
                item.update(role=role, explanation=explanation)
                unclear |= role == "unresolved"
            else:
                item.update(role="unreviewed")
                missing = True
            result.append(item)
        if missing:
            unreviewed.append(ref)
        if unclear:
            unresolved.append(ref)
    return {
        "inventorySHA256": inventory["sha256"],
        "ranges": result,
        "unreviewed": unreviewed,
        "unresolved": unresolved,
    }
