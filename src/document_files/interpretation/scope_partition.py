"""Bounded applicability windows over one frozen complete candidate inventory.

Windows change delivery, not observations or target definitions. Canonical row
selections are rebound against the original task, never a sliced compiler mapping.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, replace

from .compiler import CompileError
from .integration import apply_scope_decision
from .legacy_engine import contract_messages
from .scope_axis_wire import _decision_schema, prepare_scope_axis_wire
from .scope_selection_wire import selection_wire_from_axis
from .scope_source_binding import bind_scope_sources

VERSION = "document-files.scope-partition.v1"
MAX_WINDOWS = 128
MAX_STATE_BYTES = 8 * 1024 * 1024
SYSTEM_SUFFIX = """
This request reviews one explicit window of a larger applicability task.
Decide only over the offered candidates and row boundaries. Other windows are
unseen here, not excluded. allDataRows and direct column/group selection cover
only the offered row window, never rows outside it. Original row coordinates and
dataRowNumberInFragment retain their original meanings and do not restart here.
All offered candidates/rows must be reviewed: apply selects the applicable subset
and excludes the rest of this window; no_target explicitly excludes every offered
target; unresolved means the window cannot yet be fully decided. no_target and
unresolved require selections empty. Do not infer an exclusion from missing source
observations or unclear row roles. Reference context outside the selectable window
is evidence only, not permission to select those rows.
"""


def _encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


def policy():
    return {
        "version": VERSION,
        "maxWindows": MAX_WINDOWS,
        "maxStateBytes": MAX_STATE_BYTES,
        "systemSuffixSHA256": _digest(SYSTEM_SUFFIX),
    }


def _families(base, task):
    """Keep a record, its columns/groups and overlapping standalone context together."""
    handles = [c["targetHandle"] for c in base.payload["candidates"]]
    parent = {h: h for h in handles}

    def root(h):
        while parent[h] != h:
            h = parent[h]
        return h

    def join(a, b):
        parent[root(b)] = root(a)

    for candidate in base.payload["candidates"]:
        for covered in candidate.get("coversCandidates", []):
            join(candidate["targetHandle"], covered)
    for handle, record in base.records[task.id].items():
        for member in [*record["columnHandles"].values(), *record["groups"]]:
            join(handle, member)
    result = {}
    for h in handles:
        result.setdefault(root(h), []).append(h)
    return list(result.values())


def _window_wire(task, base, handles, ranges):
    payload = copy.deepcopy(base.payload)
    offered = set(handles)
    payload["candidates"] = [c for c in payload["candidates"] if c["targetHandle"] in offered]
    records = {h: copy.deepcopy(r) for h, r in base.records[task.id].items() if h in offered}
    boundaries = payload.get("rowBoundaryCandidates", [])
    for handle, (start, end) in ranges.items():
        record = records[handle]
        all_rows = [r for r in boundaries if r["targetHandle"] == handle]
        selected = [r for r in all_rows if start <= r["row"] <= end]
        if not selected:
            raise ValueError("scope_window_without_observed_rows")
        record.update(
            rows={r["rowRef"]: r["row"] for r in selected},
            rowStart=start,
            rowEnd=end,
            hasGaps=len(selected) != end - start + 1,
            windowed=True,
        )
        public = next(c for c in payload["candidates"] if c["targetHandle"] == handle)
        options = public["rowOptions"]
        options.update(rowStart=start, rowEnd=end, rowBoundaryRefs=list(record["rows"]))
        # Remove only exclusively out-of-window *data-row* source text. Shared
        # nodes, definitions, notes, unresolved rows and reference links stay exact.
        inside = {s for r in selected for s in r["sourceRefs"]}
        fixed = set(public["definitionRefs"])
        fixed.update(s for c in options["columns"] for s in c["definitionRefs"])
        fixed.update(s for r in all_rows if r["role"] != "data" for s in r["sourceRefs"])
        fixed.update(
            link[k]
            for link in public.get("referenceLinks", [])
            for k in ("sourceRef", "targetRef")
            if k in link
        )
        outside = (
            {
                s
                for r in all_rows
                if not start <= r["row"] <= end and r["role"] == "data"
                for s in r["sourceRefs"]
            }
            - inside
            - fixed
        )
        public["context"] = [c for c in public["context"] if c["sourceRef"] not in outside]
    if "rowBoundaryCandidates" in payload:
        payload["rowBoundaryCandidates"] = [
            r
            for r in boundaries
            if r["targetHandle"] in records and r["rowRef"] in records[r["targetHandle"]]["rows"]
        ]
    canonical_ranges = {base.targets[h]: list(bounds) for h, bounds in ranges.items()}
    spec = {"candidateHandles": [base.targets[h] for h in handles], "rowWindows": canonical_ranges}
    window_id = "scope-window-" + _digest([task.fingerprint, spec])[:24]
    payload["scopeWindow"] = {
        "id": window_id,
        "inventoryFingerprint": task.fingerprint,
        "candidateCoverage": "this_window_only",
        "wholeInventoryCandidates": len(task.target_map),
        "rowCoverage": "offered_boundaries_only" if ranges else "complete_offered_records",
    }
    # The original inventory was complete; the window never claims it was the
    # whole task. Its identity also freezes the exact definitions and row context.
    payload["candidateCoverage"] = "window"
    standalone = [h for h in base.standalone[task.id] if h in offered]
    contract = _decision_schema(task.id, records, standalone, base.contract["properties"])
    contract["properties"]["decision"]["enum"] = ["apply", "no_target", "unresolved"]
    axis = replace(
        base,
        payload=payload,
        contract=contract,
        records={task.id: records},
        standalone={task.id: standalone},
        fingerprint=_digest([VERSION, base.fingerprint, spec, payload, contract, records]),
    )
    return spec | {"id": window_id}, selection_wire_from_axis([task], axis)


@dataclass(frozen=True)
class PartitionPlan:
    fingerprint: str
    status: str
    windows: tuple
    wires: dict


def plan_scope_partitions(task, *, input_chars, system):
    """Partition only complete tasks. Fixed context that cannot fit stays explicit."""
    if (
        not task.complete_candidates
        or task.payload.get("inventory", {}).get("status") != "complete"
    ):
        raise ValueError("scope_partition_requires_complete_inventory")
    base = prepare_scope_axis_wire([task])
    families = _families(base, task)
    pending = [(families, {})]
    windows, wires = [], {}
    while pending:
        if len(windows) + len(pending) > MAX_WINDOWS:
            return PartitionPlan(
                _digest([VERSION, task.fingerprint, input_chars, policy()]),
                "resource_limited",
                (),
                {},
            )
        groups, ranges = pending.pop(0)
        handles = [h for group in groups for h in group]
        spec, wire = _window_wire(task, base, handles, ranges)
        chars = sum(
            len(m["content"])
            for m in contract_messages(system + SYSTEM_SUFFIX, wire.payload, wire.contract)
        )
        if chars > input_chars:
            if len(groups) > 1:
                middle = len(groups) // 2
                pending[:0] = [(groups[:middle], {}), (groups[middle:], {})]
                continue
            records = base.records[task.id]
            candidates = [h for h in handles if h in records]
            if len(candidates) == 1:
                h = candidates[0]
                family = {h, *records[h]["columnHandles"].values(), *records[h]["groups"]}
                low, high = ranges.get(h, [records[h]["rowStart"], records[h]["rowEnd"]])
                rows = [r for r in records[h]["rows"].values() if low <= r <= high]
                if set(handles) == family and len(rows) > 1:
                    middle = rows[len(rows) // 2]
                    pending[:0] = [(groups, {h: [low, middle - 1]}), (groups, {h: [middle, high]})]
                    continue
        entry = spec | {
            "status": "ready" if chars <= input_chars else "context_unavailable",
            "requestChars": chars,
            "inputLimitChars": input_chars,
            "wireFingerprint": wire.fingerprint,
        }
        windows.append(entry)
        if entry["status"] == "ready":
            wires[entry["id"]] = wire
        if (
            sum(
                len(_encode(w.payload).encode()) + len(_encode(w.contract).encode())
                for w in wires.values()
            )
            > MAX_STATE_BYTES
        ):
            return PartitionPlan(
                _digest([VERSION, task.fingerprint, input_chars, policy()]),
                "resource_limited",
                (),
                {},
            )
    fingerprint = _digest([VERSION, task.fingerprint, input_chars, policy(), windows])
    return PartitionPlan(
        fingerprint,
        "ready" if all(w["status"] == "ready" for w in windows) else "partial",
        tuple(windows),
        wires,
    )


def value_identity(task, compiled):
    """Negative reviews also depend on exact values, not just the selected bindings."""
    owners = {c["regionId"] for c in task.target_map.values()}
    owners.update(c.get("dataOwnerRegionId", c["regionId"]) for c in task.target_map.values())
    return _digest(
        [
            {
                "id": c.id,
                "data": c.data,
                "valueEvidence": [
                    {k: v for k, v in e.items() if k != "semanticIds"} for e in c.value_evidence
                ],
                "rowScopes": c.row_scopes,
                "repeatPaths": c.repeat_paths,
            }
            for c in compiled
            if c.id in owners
        ]
    )


def new_state(plan, task, compiled):
    return {
        "fingerprint": task.fingerprint,
        "planFingerprint": plan.fingerprint,
        "valueIdentity": value_identity(task, compiled),
        "windows": {},
    }


def record_window(state, identifier, record):
    updated = copy.deepcopy(state)
    updated["windows"][identifier] = record
    if len(_encode(updated).encode()) > MAX_STATE_BYTES:
        raise CompileError("scope_partition_state_budget_exceeded")
    return updated


def bind_window_response(response, wire, task, compiled):
    decoded = wire.decode(response)
    outcome = decoded.get("decision")
    if outcome not in {"apply", "no_target", "unresolved"}:
        raise CompileError("scope_window_invalid_response")
    selection = copy.deepcopy(decoded)
    if outcome == "no_target":
        selection["decision"] = "unresolved"
    # ScopeDecision rejects empty apply and populated negative/uncertain answers.
    canonical, trace = bind_scope_sources(
        selection, task, compiled, expected_fingerprint=task.fingerprint
    )
    apply_scope_decision(compiled, task, canonical)  # Atomic validity check only.
    record = {
        "status": "answered",
        "response": copy.deepcopy(response),
        "outcome": outcome,
        "decision": canonical.model_dump(),
        "sourceBinding": trace,
        "wireFingerprint": wire.fingerprint,
    }
    if len(_encode(record).encode()) > MAX_STATE_BYTES:
        raise CompileError("scope_partition_state_budget_exceeded")
    return record, selection


def _merge_selections(selections, task):
    handles, rows = [], {}
    for selection in selections:
        handles.extend(selection["targetHandles"])
        for r in selection["rowSelections"]:
            mapping = task.target_map[r["targetHandle"]]["rowScope"]["mapping"]
            columns = tuple(
                c for c in mapping["columns"] if not r["columnIds"] or c in r["columnIds"]
            )
            rows.setdefault((r["targetHandle"], columns), []).append((r["rowStart"], r["rowEnd"]))
    merged = []
    for (handle, columns), spans in rows.items():
        intervals = []
        for start, end in sorted(spans):
            if intervals and start <= intervals[-1][1] + 1:
                intervals[-1][1] = max(end, intervals[-1][1])
            else:
                intervals.append([start, end])
        merged.extend(
            {
                "targetHandle": handle,
                "rowStart": a,
                "rowEnd": b,
                "columnIds": []
                if columns == tuple(task.target_map[handle]["rowScope"]["mapping"]["columns"])
                else list(columns),
            }
            for a, b in intervals
        )
    return {
        "taskId": task.id,
        "decision": "apply",
        "targetHandles": list(dict.fromkeys(handles)),
        "rowSelections": merged,
        "sourceRefs": [],
        "explanation": (
            "Union of source-checked selections from explicitly reviewed windows; "
            "unreviewed windows remain unresolved."
        ),
    }


def replay_partitions(state, plan, task, compiled):
    """Rebuild every response/proof; public coverage and persisted outcomes are not authority."""
    try:
        expected = new_state(plan, task, compiled)
        if set(state) != set(expected) or any(
            state[k] != expected[k] for k in expected if k != "windows"
        ):
            raise ValueError
        records = state["windows"]
        if not isinstance(records, dict) or set(records) - set(plan.wires):
            raise ValueError
        if len(_encode(state).encode()) > MAX_STATE_BYTES:
            raise ValueError
        reviewed, positive = [], []
        for entry in plan.windows:
            identifier = entry["id"]
            stored = records.get(identifier)
            outcome = "unseen" if entry["status"] == "ready" else "context_unavailable"
            if stored is not None:
                if stored == {"status": "invalid"}:
                    outcome = "invalid"
                else:
                    rebuilt, selection = bind_window_response(
                        stored["response"], plan.wires[identifier], task, compiled
                    )
                    if rebuilt != stored:
                        raise ValueError
                    outcome = rebuilt["outcome"]
                    if outcome == "apply":
                        positive.append((identifier, selection))
            reviewed.append({**copy.deepcopy(entry), "outcome": outcome})
        complete = bool(reviewed) and all(w["outcome"] in {"apply", "no_target"} for w in reviewed)
        complete = complete and all(
            len(c["rowScope"]["mapping"]["rows"])
            == c["rowScope"]["mapping"]["rowEnd"] - c["rowScope"]["mapping"]["rowStart"] + 1
            and all(r["role"] != "unresolved" for r in c["rowScope"]["mapping"]["rows"].values())
            for c in task.target_map.values()
            if "rowScope" in c
        )
        aggregation_issue = None
        applied_windows = []
        if positive:
            aggregate = _merge_selections([s for _, s in positive], task)
            try:
                canonical, _ = bind_scope_sources(
                    aggregate, task, compiled, expected_fingerprint=task.fingerprint
                )
                compiled, _ = apply_scope_decision(
                    compiled, replace(task, complete_candidates=complete), canonical
                )
                applied_windows = [identifier for identifier, _ in positive]
            except CompileError as exc:
                # Individual checked answers remain replayable even if their
                # combined proof exceeds resources or overlaps across fragments.
                complete = False
                aggregation_issue = str(exc)
                # Preserve an admissible checked partial union rather than losing
                # earlier links when a later answer cannot join it. No skipped
                # positive is reported as applied; its response/proof stays saved.
                base, admitted = compiled, []
                for identifier, selection in positive:
                    try:
                        aggregate = _merge_selections([*admitted, selection], task)
                        canonical, _ = bind_scope_sources(
                            aggregate, task, base, expected_fingerprint=task.fingerprint
                        )
                        next_result, _ = apply_scope_decision(
                            base, replace(task, complete_candidates=False), canonical
                        )
                    except CompileError:
                        continue
                    compiled = next_result
                    admitted.append(selection)
                    applied_windows.append(identifier)
        else:
            complete = False  # All-negative review never invents an applicable target.
        return compiled, {
            "status": "reviewed" if complete else "unresolved",
            "planStatus": plan.status,
            "planFingerprint": plan.fingerprint,
            "windows": reviewed,
            "appliedWindows": applied_windows,
            "unappliedWindows": [
                identifier for identifier, _ in positive if identifier not in applied_windows
            ],
            **({"aggregationIssue": aggregation_issue} if aggregation_issue else {}),
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise CompileError("scope_partition_record_incompatible") from None
