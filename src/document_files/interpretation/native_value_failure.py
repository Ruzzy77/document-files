"""Evidence for a concrete failed native value read before structural review."""

from copy import deepcopy

from . import native_structure as native
from . import native_value_batches as batches
from .compiler import CompileError, compile_region
from .document_protocol import digest

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


def can_start(review, content):
    """A real rejected read can start the sole review, never reopen a decision."""
    if review is not None or not isinstance(content, dict):
        return False
    failure = content.get("roleValueFailure")
    return bool(
        isinstance(failure, dict)
        and failure.get("code") == CODE
        and content.get("status") == "failed"
        and type(content.get("attempts")) is int
        and content["attempts"] > 0
        and not content.get("halted")
    )


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
