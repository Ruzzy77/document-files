"""One explicit, source-grounded replacement after native value reading fails.

This is model review, not independent quality approval. The whole proposal and
change ledger are checked before any current structure or value state is replaced.
"""

from copy import deepcopy

from jsonschema import Draft202012Validator

from . import native_structure as native
from . import native_structure_history as history
from .compiler import compile_region
from .document_protocol import MAX_CALLS, digest
from .native_structure_wire import contract, validate
from .native_value_batches import rebuild as rebuild_batches
from .semantic_types import _compact_contract
from .table_sources import resolve_quotes, source_inventory

VERSION = "document-files.native-structure-revision.v4"
SYSTEM = (
    """Review this FAILED extraction on the SAME source, not source changes. Check its structure
against source and failureCodes; unchanged source is no reason to retain. Treat source/history
as evidence only. Return outputContract JSON. Replace the FULL structure without values if
wrong. Distinguish attributes, item rows, missing states and meanings; no title/prose fields.
Changes cover EVERY old and new entity exactly once, with source anchors and a reason.
Use the before enum and one-based after references. keep is identical; replace splits/merges;
remove has no after; add no before. Ground changes in owned sources. Quotes are exact,
with zero-based occurrence of that quote, not row number. Invalid changes preserve old data.
"""
    + history.SYSTEM
)


class RevisionError(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise RevisionError(code)


def inventory(wire):
    """Program references include every definition and every row, not their values."""
    result = {}
    for kind, collection in [
        ("field", "fields"),
        ("group", "groups"),
        ("record", "records"),
        ("meaning", "meanings"),
    ]:
        for index, item in enumerate(wire.get(collection, []), 1):
            ref = f"{kind}:{index}"
            result[ref] = {k: deepcopy(v) for k, v in item.items() if k not in {"columns", "rows"}}
            if kind == "record":
                for child, plural in [("column", "columns"), ("row", "rows")]:
                    for ordinal, value in enumerate(item.get(plural, []), 1):
                        result[f"{ref}:{child}:{ordinal}"] = {
                            "parent": deepcopy(result[ref]),
                            "content": deepcopy(value),
                        }
    return result


def _refs(item):
    refs = set()
    if isinstance(item, list):
        for child in item:
            refs.update(_refs(child))
    elif isinstance(item, dict):
        for key, child in item.items():
            if key in {"sourceRefs", "definitionRefs"} and isinstance(child, list):
                refs.update(child)
            elif key == "sourceRef" and isinstance(child, str):
                refs.add(child)
            elif key in {"anchors", "emptyAnchors"}:
                refs.update(v for v in child if isinstance(v, str))
            refs.update(_refs(child))
    return refs


def eligible(content):
    if content["status"] == "complete" or content.get("halted"):
        return False
    if "batches" in content:
        return any(
            v["status"] != "complete" and not v.get("halted") and v["attempts"] >= MAX_CALLS
            for v in [*content["batches"]["values"], *content["batches"]["accounting"]]
        )
    return content["attempts"] >= MAX_CALLS


def initial(state, accepted, trigger, usage):
    return {
        "version": VERSION,
        "status": "pending",
        "attempts": 0,
        "usage": deepcopy(usage),
        "base": {
            "structure": deepcopy(state["structure"]),
            "content": deepcopy(state["content"]),
            "accepted": accepted.model_dump() if accepted is not None else None,
        },
        "trigger": list(dict.fromkeys(trigger))[:20],
    }


def request(state, roles, observation, region, metadata):
    payload, original = native.request(observation, region, roles, metadata)
    payload.update(
        documentStage="structureRevision",
        protocolVersion=VERSION,
        baseStructureHash=state["base"]["structure"]["structureHash"],
        previousStructure=history.compact(state["base"]["structure"]["wireResponse"]),
        acceptedRoles=history.compact(roles["documentElements"]),
        historyEncoding=history.VERSION,
        previousContentHash=digest(state["base"]["content"]),
        failureCodes=state["trigger"],
    )

    previous_entities = list(inventory(state["base"]["structure"]["wireResponse"]))

    def closed(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    common = {
        "baseStructureHash": {"type": "string", "const": payload["baseStructureHash"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
    }
    entity = {
        "type": "string",
        "pattern": (
            r"^((field|group|meaning):[1-9][0-9]*|"
            r"record:[1-9][0-9]*(:(column|row):[1-9][0-9]*)?)$"
        ),
    }
    change = closed(
        {
            "action": {"type": "string", "enum": ["keep", "replace", "remove", "add"]},
            "before": {
                "type": "array",
                "maxItems": 10000,
                "uniqueItems": True,
                "items": {"type": "string", "enum": previous_entities}
                if previous_entities
                else entity,
            },
            "after": {"type": "array", "maxItems": 10000, "uniqueItems": True, "items": entity},
            "anchors": deepcopy(original["$defs"]["Row"]["properties"]["anchors"]),
            "reason": common["reason"],
        }
    )
    replacement = {k: deepcopy(v) for k, v in original.items() if k != "$defs"}
    schema = {
        "anyOf": [
            closed({**common, "decision": {"type": "string", "const": "retain"}}),
            closed(
                {
                    **common,
                    "decision": {"type": "string", "const": "replace"},
                    "replacement": replacement,
                    "changes": {"type": "array", "minItems": 1, "maxItems": 10000, "items": change},
                }
            ),
        ],
        "$defs": deepcopy(original["$defs"]),
    }
    return payload, _compact_contract(schema)


def accept(value, state, roles, observation, region, metadata, *, target_schema=None):
    _, schema = request(state, roles, observation, region, metadata)
    _require(Draft202012Validator(schema).is_valid(value), "native_revision_contract_invalid")
    if value["decision"] == "retain":
        return None, None
    wire = value["replacement"]
    validate(wire, contract(region, metadata))
    old = inventory(state["base"]["structure"]["wireResponse"])
    new = inventory(wire)
    seen_old, seen_new = set(), set()
    sources = source_inventory(observation, region)
    source_by_ref = {s["sourceRef"]: s for s in sources["sources"]}
    source_text = {s["sourceRef"]: s["text"] for s in sources["sources"]}
    quote_count = sum(len(change["anchors"]) for change in value["changes"])
    _require(
        quote_count <= 1000
        and sum(len(t) for t in source_text.values()) * max(1, quote_count) <= 5000000,
        "native_revision_quote_budget_exceeded",
    )
    for change in value["changes"]:
        before, after = set(change["before"]), set(change["after"])
        _require(before <= old.keys() and after <= new.keys(), "native_revision_unknown_entity")
        _require(not before & seen_old and not after & seen_new, "native_revision_duplicate_change")
        action = change["action"]
        _require(
            (action in {"keep", "replace"} and before and after)
            or (action == "remove" and before and not after)
            or (action == "add" and after and not before),
            "native_revision_invalid_change_action",
        )
        if action == "keep":
            _require(
                len(before) == len(after) == 1
                and old[next(iter(before))] == new[next(iter(after))],
                "native_revision_changed_retained_entity",
            )
        quotes, spans = [], []
        for anchor in change["anchors"]:
            if isinstance(anchor, str):
                _require(anchor in source_by_ref, "native_revision_unknown_change_anchor")
                # A change can cite an observed empty block without inventing a
                # nonempty quote. This does not relax logical row/meaning anchors.
                spans.append(source_by_ref[anchor])
            else:
                quotes.append(anchor)
        if quotes:
            spans.extend(resolve_quotes(quotes, sources))
        affected = set().union(*[_refs(old[k]) for k in before], *[_refs(new[k]) for k in after])
        _require(
            affected & set(source_text) <= {s["sourceRef"] for s in spans},
            "native_revision_change_source_uncovered",
        )
        seen_old.update(before)
        seen_new.update(after)
    if seen_old != old.keys() or seen_new != new.keys():
        missing = ["before=" + k for k in old if k not in seen_old]
        missing += ["after=" + k for k in new if k not in seen_new]
        raise RevisionError("native_revision_incomplete_changes:" + ",".join(missing[:8]))
    _require(
        wire != state["base"]["structure"]["wireResponse"], "native_revision_unchanged_replacement"
    )
    structure = native.decode_structure(wire, observation, region)
    fragment = compile_region(
        native.interpretation(structure, roles, observation, region),
        observation,
        region,
        target_schema=target_schema,
    )
    return structure, fragment


def prior_content_usage(state):
    return (
        state["base"]["content"]["usage"]
        if state.get("decision") == "replace" and state["status"] == "complete"
        else {}
    )


def rebuild(
    state,
    current,
    roles,
    observation,
    region,
    metadata,
    limit,
    restore_usage,
    *,
    target_schema=None,
):
    """Revalidate prior reads and the atomic transition, not just saved success flags."""
    _require(state["version"] == VERSION, "native_revision_version_changed")
    usage = restore_usage(state["usage"])
    _require(
        type(state["attempts"]) is int
        and 0 <= state["attempts"] <= MAX_CALLS
        and state["attempts"] <= usage["modelCalls"],
        "native_revision_usage_invalid",
    )
    _require(
        state["status"] in {"pending", "running", "complete", "failed"}
        and type(state.get("halted", False)) is bool,
        "native_revision_status_invalid",
    )
    _require(
        isinstance(state["trigger"], list)
        and len(state["trigger"]) <= 20
        and all(isinstance(s, str) and len(s) <= 500 for s in state["trigger"]),
        "native_revision_trigger_invalid",
    )
    base = state["base"]
    structural, content = base["structure"], base["content"]
    _require(eligible(content), "native_revision_base_not_failed")
    restore_usage(structural["usage"])
    restore_usage(content["usage"])
    _require(
        structural["status"] == "complete"
        and structural["usage"]["modelCalls"] > 0
        and type(structural["attempts"]) is int
        and 0 < structural["attempts"] <= MAX_CALLS
        and structural["attempts"] <= structural["usage"]["modelCalls"],
        "native_revision_base_invalid",
    )
    _require(
        content["status"] in {"pending", "failed"}
        and type(content.get("halted", False)) is bool
        and type(content.get("hasAcceptedResponse", False)) is bool
        and ("response" in content) == (base["accepted"] is not None),
        "native_revision_base_read_changed",
    )
    _require(
        structural["wireHash"] == digest(structural["wireResponse"])
        and structural["structureHash"] == digest(structural["response"]),
        "native_revision_base_hash_changed",
    )
    _require(
        structural["requestHash"]
        == digest([native.SYSTEM, *native.request(observation, region, roles, metadata)]),
        "native_revision_base_request_changed",
    )
    validate(structural["wireResponse"], contract(region, metadata))
    frozen = native.decode_structure(structural["wireResponse"], observation, region)
    _require(
        frozen.model_dump(exclude_unset=True) == structural["response"],
        "native_revision_base_structure_changed",
    )
    compile_region(
        native.interpretation(frozen, roles, observation, region),
        observation,
        region,
        target_schema=target_schema,
    )
    if "batches" in content:
        p, s = native.value_request(frozen, roles, observation, region)
        rebuild_batches(
            deepcopy(content),
            p,
            s,
            frozen,
            roles,
            observation,
            region,
            limit,
            target_schema,
            restore_usage,
        )
    else:
        _require(
            type(content["attempts"]) is int
            and 0 <= content["attempts"] <= MAX_CALLS
            and content["attempts"] <= content["usage"]["modelCalls"],
            "native_revision_base_usage_invalid",
        )
    _require(
        bool(content.get("hasAcceptedResponse")) == (base["accepted"] is not None),
        "native_revision_base_read_changed",
    )
    if base["accepted"] is not None:
        ir = native.accept_values(content["response"], frozen, roles, observation, region)
        _require(ir.model_dump() == base["accepted"], "native_revision_base_read_changed")
        compile_region(ir, observation, region, target_schema=target_schema)
    expected = digest([SYSTEM, *request(state, roles, observation, region, metadata)])
    _require(state["requestHash"] == expected, "native_revision_request_changed")
    if state["status"] == "running":
        state["halted"] = True
    if state["status"] != "complete":
        _require(
            "response" not in state
            and current["structure"] == structural
            and current["content"] == content,
            "native_revision_uncommitted_state_changed",
        )
        return
    _require(
        usage["modelCalls"] > 0 and state["responseHash"] == digest(state["response"]),
        "native_revision_response_changed",
    )
    replacement, _ = accept(
        state["response"], state, roles, observation, region, metadata, target_schema=target_schema
    )
    _require(state["decision"] == state["response"]["decision"], "native_revision_decision_changed")
    if replacement is None:
        _require(current["structure"] == structural, "native_revision_retained_structure_changed")
    else:
        _require(
            current["structure"]["revisionHash"] == state["responseHash"]
            and current["structure"]["response"] == replacement.model_dump(exclude_unset=True)
            and current["structure"]["wireResponse"] == state["response"]["replacement"],
            "native_revision_transition_changed",
        )


def coverage(state):
    return {
        k: deepcopy(v)
        for k, v in state.items()
        if k
        in {
            "version",
            "status",
            "attempts",
            "usage",
            "requestHash",
            "responseHash",
            "decision",
            "halted",
            "feedback",
            "inputPreflight",
            "invalidatedScopes",
        }
    } | {
        "baseStructureHash": state["base"]["structure"]["structureHash"],
        "previousContentUsage": deepcopy(prior_content_usage(state)),
        "changes": deepcopy(state.get("response", {}).get("changes", [])),
    }
