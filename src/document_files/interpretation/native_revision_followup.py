"""One remaining review after an early retain meets a real role/value conflict."""

from copy import deepcopy

from . import native_structure as native
from . import native_value_batches as batches
from .compiler import CompileError, compile_region
from .document_protocol import MAX_CALLS, digest

CODE = "document_role_value_conflict"


def failure_record(response, payload, schema, *, batch=None):
    result = {
        "code": CODE,
        "response": deepcopy(response),
        "responseHash": digest(response),
        "requestBaseHash": digest([native.VALUE_SYSTEM, payload, schema]),
    }
    if batch is not None:
        result["batch"] = deepcopy(batch)
    return result


def can_reopen(review, content):
    return bool(
        review
        and "priorReview" not in review
        and review["status"] == "complete"
        and review.get("decision") == "retain"
        and 0 < review["attempts"] < MAX_CALLS
        and review["base"]["content"].get("roleSourceReview")
        and review["base"]["content"]["attempts"] == 0
        and content["status"] == "failed"
        and content["attempts"] > 0
        and not content.get("halted")
        and content.get("roleValueFailure", {}).get("code") == CODE
    )


def reopen(current, accepted):
    from .native_structure_revision import initial

    prior = current["revision"]
    if not can_reopen(prior, current["content"]):
        raise ValueError("native_revision_followup_not_eligible")
    # The early snapshot remains immutable; the later base contains actual reads.
    current["content"].pop("roleSourceReview", None)
    result = initial(current, accepted, [CODE], deepcopy(prior["usage"]))
    result.update(attempts=prior["attempts"], priorReview=deepcopy(prior))
    return result


def validate_failure(content, structure, roles, observation, region, target_schema):
    """Replay the attempted choices; a code/hash alone cannot authorize review."""
    failure = content["roleValueFailure"]
    if (
        set(failure) - {"code", "response", "responseHash", "requestBaseHash", "batch"}
        or failure["code"] != CODE
        or content["usage"]["modelCalls"] <= 0
        or failure["responseHash"] != digest(failure["response"])
    ):
        raise ValueError("native_revision_value_failure_changed")
    payload, schema = native.value_request(structure, roles, observation, region)
    if failure["requestBaseHash"] != digest([native.VALUE_SYSTEM, payload, schema]):
        raise ValueError("native_revision_value_request_changed")
    if "batches" in content:
        source = failure["batch"]
        phase, index = source["phase"], source["index"]
        if phase not in {"values", "accounting"} or type(index) is not int or index < 0:
            raise ValueError("native_revision_failure_batch_changed")
        state = content["batches"]
        if index >= len(state[phase]):
            raise ValueError("native_revision_failure_batch_changed")
        record = state[phase][index]
        keys = state["valueKeys" if phase == "values" else "accountingKeys"][index]
        request = batches.request_for(payload, schema, phase, keys, state)
        if (
            source["requestHash"] != digest(list(request))
            or record["lastResponseHash"] != digest(source["response"])
            or record["usage"]["modelCalls"] <= 0
        ):
            raise ValueError("native_revision_failure_batch_changed")
        batches.accept(source["response"], request, phase, keys)
        proposed = deepcopy(state)
        proposed[phase][index]["response"] = source["response"]
        if batches.aggregate(payload, proposed) != failure["response"]:
            raise ValueError("native_revision_failure_aggregate_changed")
    elif "batch" in failure:
        raise ValueError("native_revision_failure_batch_changed")
    ir = native.accept_values(failure["response"], structure, roles, observation, region)
    try:
        compile_region(ir, observation, region, target_schema=target_schema)
    except CompileError as error:
        if str(error) == CODE:
            return
        raise ValueError("native_revision_failure_not_reproduced") from error
    raise ValueError("native_revision_failure_not_reproduced")
