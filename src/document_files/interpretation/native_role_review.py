"""Review role/source overlaps without deciding roles or deleting attributes."""

import json

from .document_outline import structural_value_allowed

MAX_CONFLICTS = 64
MAX_BYTES = 16000


def effective_roles(state):
    """Original role response remains immutable; only a checked revision can replace it."""
    revision = state.get("revision", {})
    response = revision.get("response", {})
    if (
        revision.get("status") == "complete"
        and response.get("decision") == "replace"
        and "documentElements" in response
    ):
        return {
            "regionId": state["response"]["regionId"],
            "documentElements": response["documentElements"],
        }
    return state["response"]


def overlaps(structure, roles, observation, region):
    """Potential disagreement, not proof that an inner quote or the role is wrong.

    Known inner bindings keep their normal path. Otherwise ask one joint review
    before values. The reviewer may retain a legitimate role and choose an inner
    quote later; the actual value compiler still checks that quote independently.
    """
    from .native_structure import entries

    structural = {
        e["sourceRef"]: e["role"]
        for e in roles["documentElements"]
        if e["role"] in {"title", "section_heading", "caption"}
    }
    inner = set()
    for bid in region["bindingIds"]:
        binding = observation.bindings[bid]
        ref = binding["sourceRef"]
        if ref not in structural:
            continue
        node = observation.nodes[ref]
        window = region.get("nodeViews", {}).get(ref, {"start": 0, "end": len(node["text"])})
        if structural_value_allowed(binding, node, window):
            inner.add(ref)
    result = []
    for entry in entries(structure):
        refs = entry["sourceRefs"]
        if entry["status"] not in {"present", "blank"} or not all(
            ref in structural and ref not in inner for ref in refs
        ):
            continue
        result.append(
            {"valueHandle": entry["handle"], "sourceRoles": {r: structural[r] for r in refs}}
        )
        if len(result) > MAX_CONFLICTS or len(json.dumps(result).encode()) > MAX_BYTES:
            raise ValueError("native_role_review_budget_exceeded")
    return result


def role_inventory(roles):
    return {f"role:{i}": item for i, item in enumerate(roles["documentElements"], 1)}


def preserves_role_contexts(observation, regions, current_id, states, fragments):
    """Do not commit a role edit that would silently stale another saved request."""
    from .document_protocol import ROLE_SYSTEM, digest, role_request

    for other in regions:
        if other["id"] == current_id or other["id"] not in states:
            continue
        request = role_request(observation, other, fragments.values())
        if request is None or states[other["id"]]["requestHash"] != digest([ROLE_SYSTEM, *request]):
            return False
    return True
