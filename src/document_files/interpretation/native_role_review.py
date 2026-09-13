"""Apply checked role replacements without changing native observations."""

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
