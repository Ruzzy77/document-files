"""Short typed target IDs at the model boundary; canonical scope decisions persist.

Only target-reference fields are translated. Labels, quotations, source references,
task IDs and explanations are literal data, even when they look like an alias.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from .integration import scope_batch_payload, scope_output_schema

VERSION = "document-files.scope-reference-wire.v1"
_KINDS = {"field", "column", "container", "headerGroup"}


@dataclass(frozen=True)
class ScopeReferenceWire:
    payload: dict
    contract: dict
    targets: dict[str, str]
    task_targets: dict[str, set[str]]
    batched: bool

    def _decision(self, value):
        if not isinstance(value, dict):
            return copy.deepcopy(value)
        result = copy.deepcopy(value)
        handles = result.get("targetHandles", [])
        task_id = result.get("taskId")
        allowed = self.task_targets.get(task_id, set()) if isinstance(task_id, str) else set()
        if not isinstance(handles, list) or any(
            not isinstance(h, str) or h not in allowed for h in handles
        ):
            # Keep taskId so duplicate-task validation still invalidates every
            # duplicate, but never accept a raw canonical ID as an alias bypass.
            # A malformed sibling must not discard the other valid decisions.
            result["targetHandles"] = None
        elif "targetHandles" in result:
            result["targetHandles"] = [self.targets[h] for h in handles]
        return result

    def decode(self, response):
        if not self.batched:
            return self._decision(response)
        result = copy.deepcopy(response)
        if isinstance(result, dict) and isinstance(result.get("decisions"), list):
            result["decisions"] = [self._decision(v) for v in result["decisions"]]
        return result


def prepare_scope_wire(tasks):
    """Offer every original candidate and invert only compiler-issued target IDs.

    Scope task fingerprints and checkpoint identity remain canonical. Batch order
    can change aliases, so only decoded decisions may be persisted or replayed.
    """
    if not 1 <= len(tasks) <= 8:
        raise ValueError("invalid_scope_wire_batch")
    kinds = {}
    for task in tasks:
        candidates = task.payload["candidates"]
        handles = [c["targetHandle"] for c in candidates]
        if len(handles) != len(set(handles)) or set(handles) != set(task.target_map):
            raise ValueError("scope_wire_candidate_mismatch")
        for candidate in candidates:
            handle, kind = candidate["targetHandle"], candidate["candidateKind"]
            if kind not in _KINDS or kinds.get(handle, kind) != kind:
                raise ValueError("scope_wire_candidate_kind_mismatch")
            kinds[handle] = kind
    if len({t.id for t in tasks}) != len(tasks):
        raise ValueError("scope_wire_duplicate_task")
    prefix = "@"
    while any(f"{prefix}{kind}{i}" in kinds for i, kind in enumerate(kinds.values())):
        prefix += "@"
    aliases = {handle: f"{prefix}{kind}{i}" for i, (handle, kind) in enumerate(kinds.items())}
    payload = copy.deepcopy(scope_batch_payload(tasks))
    for task_payload in payload.get("tasks", [payload]):
        for candidate in task_payload["candidates"]:
            candidate["targetHandle"] = aliases[candidate["targetHandle"]]
            if "coversCandidates" in candidate:
                candidate["coversCandidates"] = [aliases[h] for h in candidate["coversCandidates"]]
    contract = scope_output_schema(tasks)
    decision = contract if len(tasks) == 1 else contract["$defs"]["ScopeDecision"]
    decision["properties"]["targetHandles"]["items"]["enum"] = list(aliases.values())
    return ScopeReferenceWire(
        payload,
        contract,
        {alias: handle for handle, alias in aliases.items()},
        {t.id: {aliases[h] for h in t.target_map} for t in tasks},
        len(tasks) > 1,
    )
