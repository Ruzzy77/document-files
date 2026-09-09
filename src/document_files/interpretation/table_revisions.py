"""Auditable full replacements of meaning, not immutable model conclusions.

Hashes identify accepted decisions and their source inventory. They do not prove
that an interpretation is true; independent source comparison remains required.
"""

from __future__ import annotations

import hashlib
import json


class MeaningRevisionError(ValueError):
    pass


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def meaning_snapshot(ir):
    return {
        key: ir.model_dump()[key]
        for key in (
            "meanings",
            "dispositions",
            "excludedBindings",
            "unresolved",
            "tableMeaningState",
        )
    }


def _meaning(value):
    value = value.model_dump()
    for key in ("sourceRefs", "fieldIds", "groupIds", "repeatIds"):
        value[key] = sorted(value[key])
    value["sourceRanges"] = sorted(value["sourceRanges"], key=_encoded)
    return value


def _content(ir, *, reasons=True):
    state = ir.tableMeaningState
    return {
        "inventorySHA256": state.inventorySHA256,
        "meanings": sorted((_meaning(m) for m in ir.meanings), key=_encoded),
        "sourceReviews": sorted(
            [
                {
                    "sourceRef": ref,
                    "role": review.role,
                    **({"explanation": review.explanation} if reasons else {}),
                }
                for review in state.sourceReviews
                for ref in review.sourceRefs
            ],
            key=_encoded,
        ),
        "dispositions": sorted((d.model_dump() for d in ir.dispositions), key=_encoded),
        "excludedBindings": sorted((d.model_dump() for d in ir.excludedBindings), key=_encoded),
        "unresolved": sorted(ir.unresolved),
    }


def meaning_revision(ir):
    return hashlib.sha256(
        _encoded(
            {
                "content": _content(ir),
                "baseRevision": ir.tableMeaningState.baseRevision,
                "changes": [change.model_dump() for change in ir.tableMeaningState.changes],
            }
        ).encode()
    ).hexdigest()


def validate_revision(previous, current):
    """Allow corrected kind/text/scope, splits and withdrawals with explicit reasons."""
    state = current.tableMeaningState
    if state is None or meaning_revision(current) != state.revisionSHA256:
        raise MeaningRevisionError("table_meaning_revision_mismatch")
    before = previous.tableMeaningState if previous is not None else None
    if before is None:
        if state.baseRevision is not None or state.changes:
            raise MeaningRevisionError("table_meaning_initial_revision_has_history")
        return
    if state.inventorySHA256 != before.inventorySHA256:
        raise MeaningRevisionError("table_meaning_source_inventory_changed")
    if state.baseRevision != before.revisionSHA256:
        raise MeaningRevisionError("table_meaning_stale_base_revision")
    if _content(previous, reasons=False) == _content(current, reasons=False):
        raise MeaningRevisionError("table_meaning_repair_no_progress")
    old, new = {m.id: m for m in previous.meanings}, {m.id: m for m in current.meanings}
    changed = {key for key in old if key not in new or _meaning(old[key]) != _meaning(new[key])}
    accounted = set()
    reviewed = {ref for r in state.sourceReviews for ref in r.sourceRefs}
    for change in state.changes:
        prior, replacements, refs = (
            set(change.previousIds),
            set(change.replacementIds),
            set(change.reviewSourceRefs),
        )
        if (
            not change.reason.strip()
            or len(prior) != len(change.previousIds)
            or len(replacements) != len(change.replacementIds)
            or len(refs) != len(change.reviewSourceRefs)
            or not prior <= changed
            or prior & accounted
            or not replacements <= new.keys()
            or not refs <= reviewed
        ):
            raise MeaningRevisionError("table_meaning_invalid_change_record")
        replaced_refs = {ref for key in replacements for ref in new[key].sourceRefs}
        if not {ref for key in prior for ref in old[key].sourceRefs} <= replaced_refs | refs:
            raise MeaningRevisionError("table_meaning_withdrawal_loses_source_review")
        accounted.update(prior)
    if accounted != changed:
        raise MeaningRevisionError("table_meaning_change_record_missing")


def preserve_reviewed_ranges(before, after):
    """A correction may become uncertain, but must not silently become unreviewed."""
    if before["inventorySHA256"] != after["inventorySHA256"]:
        raise MeaningRevisionError("table_meaning_source_inventory_changed")
    gaps = [r for r in after["ranges"] if r["role"] == "unreviewed"]
    for old in before["ranges"]:
        if old["role"] == "unreviewed":
            continue
        for gap in gaps:
            if old["sourceRef"] != gap["sourceRef"]:
                continue
            low, high = max(old["start"], gap["start"]), min(old["end"], gap["end"])
            if low < high and gap["text"][low - gap["start"] : high - gap["start"]].strip():
                raise MeaningRevisionError("table_meaning_review_coverage_regressed")
