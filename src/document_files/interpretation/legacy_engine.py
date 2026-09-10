"""Synchronous product-owned model/read/repair loop, callable by ordinary software."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from typing import BinaryIO

from pydantic import ValidationError

from ..analysis import AnalysisInput, AnalysisJob, AnalyzerBackend, analyze_document
from ..document_model.capture import capture
from ..structured_extraction import project_structured_extraction
from .backends import ChatCompletionsClient, ModelClient, ModelError
from .bindings import materialize
from .contracts import RESULT_VERSION, ExtractionOptions, Proposal, Review, Step
from .prompts import PROMPT_VERSION, REVIEW, SYSTEM
from .validation import check_schema, validate


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def contract_messages(system, payload, contract, feedback=None):
    """Use the same visible contract and stable repair prefix in product and probes.

    A transport grammar does not make its schema visible to the model. Preserve
    the original payload, and put repair feedback after the unchanged contract.
    """
    content = {**payload, "outputContract": contract}
    if feedback is not None:
        content["repairFeedback"] = feedback
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": encode(content)},
    ]


def decode(text: str):
    def invalid_constant(value):
        raise ValueError("non-finite JSON number")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return json.loads(text, parse_constant=invalid_constant, object_pairs_hook=unique_object)
    except RecursionError:
        raise ValueError("JSON nesting budget exceeded") from None


def _has_unread_visuals(content: bytes, format_id: str, nodes: dict) -> bool:
    if any(
        n.get("sourceUnitType") in {"embedded_object", "diagram_text"}
        or n.get("semantic", {}).get("list", {}).get("marker", {}).get("kind") == "image"
        for n in nodes.values()
    ):
        return True
    if format_id == "html":
        return bool(re.search(rb"<(?:img|svg|canvas|object|embed)(?:\s|>)", content, re.I))
    if format_id in {"docx", "xlsx", "pptx", "hwpx"}:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            return any(
                "/media/" in member.filename.lower()
                or "/charts/" in member.filename.lower()
                or member.filename.lower().startswith("bindata/")
                for member in package.infolist()
            )
    return False


def _check_restore_shape(state: dict) -> None:
    """A damaged private checkpoint must fail safely before history enters a model request."""
    try:
        if not isinstance(state, dict) or not isinstance(state["identity"], dict):
            raise ValueError
        result = state["result"]
        if not isinstance(result, dict) or result["schemaVersion"] != RESULT_VERSION:
            raise ValueError
        if not isinstance(result["document"], dict):
            raise ValueError
        extraction = result["extraction"]
        if (
            extraction["status"] not in {"partial", "complete"}
            or type(extraction["modelCalls"]) is not int
            or extraction["modelCalls"] < 0
            or not isinstance(result["coverage"], dict)
            or not isinstance(result["validation"], dict)
            or not isinstance(result["provenance"], dict)
            or not isinstance(result["issues"], list)
            or not all(
                isinstance(i, dict) and isinstance(i.get("code"), str) for i in result["issues"]
            )
        ):
            raise ValueError
        validation = result["validation"]
        if (
            type(validation["valid"]) is not bool
            or validation["semanticAccuracy"] not in {"unverified", "ai_reviewed"}
            or not isinstance(validation["errors"], list)
            or not all(isinstance(v, str) for v in validation["errors"])
        ):
            raise ValueError
        for key in ("semantics", "schemaEvidence", "valueEvidence"):
            if not isinstance(result[key], list):
                raise ValueError
        for key in ("documentSchema", "dataSchema"):
            if result[key] is not None and not isinstance(result[key], dict):
                raise ValueError
        if "data" not in result or "dataSchemaRevision" not in result:
            raise ValueError
        for key in ("readNodes", "totalNodes"):
            if type(result["coverage"][key]) is not int or result["coverage"][key] < 0:
                raise ValueError
        if not isinstance(result["coverage"]["semanticAccounting"], list):
            raise ValueError
        for key in ("seen", "selected", "feedback"):
            if not isinstance(state[key], list) or not all(isinstance(v, str) for v in state[key]):
                raise ValueError
        if type(state["reviewPending"]) is not bool or not isinstance(state["history"], list):
            raise ValueError
        for message in state["history"]:
            if (
                not isinstance(message, dict)
                or set(message) != {"role", "content"}
                or message["role"] not in {"user", "assistant"}
                or not isinstance(message["content"], str)
            ):
                raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("checkpoint structure is invalid") from None


def extract_schema_from_stream(
    job: AnalysisJob,
    source: BinaryIO,
    *,
    options: ExtractionOptions | None = None,
    model_client: ModelClient | None = None,
    backend: AnalyzerBackend | None = None,
    restore: dict | None = None,
    checkpoint: Callable[[dict], None] | None = None,
) -> dict:
    started = time.monotonic()
    options = options or ExtractionOptions()
    if job.input.byte_size > options.maxInputBytes:
        raise ValueError("schema extraction input budget exceeded")
    chunks = []
    byte_count = 0
    while True:
        chunk = source.read(min(1024 * 1024, options.maxInputBytes + 1 - byte_count))
        if not chunk:
            break
        chunks.append(chunk)
        byte_count += len(chunk)
        if byte_count > options.maxInputBytes:
            raise ValueError("schema extraction input budget exceeded")
    content = b"".join(chunks)
    if AnalysisInput.from_bytes(content, format_id=job.input.format_id) != job.input:
        raise ValueError("schema extraction bytes do not match input identity")
    if options.targetSchema is not None:
        check_schema(options.targetSchema)
    analysis = analyze_document(job, io.BytesIO(content), backend=backend)
    observation = project_structured_extraction(
        analysis.extraction,
        source_format=job.input.format_id,
        unit_offset=0,
        max_units=len(analysis.extraction.units),
        include_text=True,
    )
    nodes = {f"n{u['ordinal']}": u for u in observation["units"]}
    result = {
        "schemaVersion": RESULT_VERSION,
        "jobId": job.job_id,
        "source": job.input.to_dict(),
        "document": {"nodes": nodes},
        "documentSchema": None,
        "dataSchema": None,
        "dataSchemaRevision": None,
        "data": None,
        "semantics": [],
        "schemaEvidence": [],
        "valueEvidence": [],
        "extraction": {"status": "partial", "modelCalls": 0},
        "coverage": {
            "observation": observation["coverage"],
            "readNodes": 0,
            "totalNodes": len(nodes),
            "semanticAccounting": [],
        },
        "validation": {"valid": False, "errors": [], "semanticAccuracy": "unverified"},
        "issues": list(observation["issues"]),
        "provenance": {
            "analyzer": analysis.analyzer.to_dict(),
            "promptVersion": PROMPT_VERSION,
            "model": None,
        },
    }
    if options.reconstructionContext:
        result["reconstructionContext"] = capture(
            content, job.input.format_id, max_expanded_bytes=options.maxInputBytes * 4
        )

    def issue(code):
        entry = {"code": code}
        if entry not in result["issues"]:
            result["issues"].append(entry)

    try:
        client = model_client or ChatCompletionsClient.from_environment()
    except ModelError as exc:
        issue(exc.code)
        return result
    identity = {
        "source": job.input.to_dict(),
        "options": options.model_dump(),
        "promptVersion": PROMPT_VERSION,
        "model": {k: v for k, v in client.identity.items() if k != "returnedModel"},
    }
    result["provenance"]["model"] = client.identity
    seen: set[str] = set()
    selected: list[str] = []
    feedback: list[str] = []
    candidate: Proposal | None = None
    contract = Step.model_json_schema()
    history: list[dict] = []
    review_pending = False
    if restore is not None:
        _check_restore_shape(restore)
        if (
            restore.get("version") != "document-files.checkpoint.v1"
            or restore.get("identity") != identity
            or restore.get("result", {}).get("jobId") != job.job_id
            or restore.get("result", {}).get("document", {}).get("nodes") != nodes
        ):
            raise ValueError("checkpoint does not match input, options, model or prompt version")
        result = copy.deepcopy(restore["result"])
        seen = set(restore["seen"])
        if not seen <= set(nodes):
            raise ValueError("checkpoint has unknown source nodes")
        selected = list(restore["selected"])
        if not set(selected) <= set(nodes):
            raise ValueError("checkpoint has unknown selected source nodes")
        feedback = list(restore["feedback"])
        history = copy.deepcopy(restore["history"])
        if restore.get("candidate") is not None:
            try:
                candidate = Proposal.model_validate(restore["candidate"])
            except (ValidationError, ValueError, TypeError):
                raise ValueError("checkpoint proposal structure is invalid") from None
        review_pending = bool(restore["reviewPending"])
        if review_pending and (candidate is None or validate(candidate, nodes, seen)):
            raise ValueError("checkpoint review proposal is invalid")
        if result["extraction"]["status"] == "complete":
            return result
        # Earlier interruption remains historical information, not a new completion gap.
        retry_codes = {
            "model_call_budget_exceeded",
            "completion_budget_exceeded",
            "review_budget_exceeded",
            "ai_timeout",
            "ai_connection_failed",
            "ai_rate_limited",
            "ai_server_error",
            "ai_response_invalid",
            "ai_response_incomplete",
            "ai_authentication_failed",
        }
        result["issues"] = [i for i in result["issues"] if i.get("code") not in retry_codes]
    invocation_calls = 0

    def save():
        result["coverage"]["readNodes"] = len(seen)
        result["provenance"]["model"] = client.identity
        if checkpoint is not None:
            checkpoint(
                copy.deepcopy(
                    {
                        "version": "document-files.checkpoint.v1",
                        "identity": identity,
                        "result": result,
                        "seen": sorted(seen),
                        "selected": selected,
                        "feedback": feedback,
                        "history": history,
                        "candidate": candidate.model_dump() if candidate is not None else None,
                        "reviewPending": review_pending,
                    }
                )
            )

    def retain():
        # Only mechanically validated candidates enter the public result, before AI review.
        result.update(
            {k: v for k, v in candidate.model_dump().items() if k not in {"accounting", "issues"}}
        )
        result["dataSchemaRevision"] = hashlib.sha256(
            encode(candidate.dataSchema).encode()
        ).hexdigest()
        result["coverage"]["semanticAccounting"] = [a.model_dump() for a in candidate.accounting]
        result["issues"] = [i for i in result["issues"] if i.get("code") != "ai_reported_gap"]
        result["issues"].extend(
            {"code": "ai_reported_gap", "description": i} for i in candidate.issues
        )
        result["validation"] = {"valid": True, "errors": [], "semanticAccuracy": "unverified"}
        result["extraction"]["status"] = "partial"

    save()
    while True:
        remaining = options.completionSeconds - (time.monotonic() - started)
        if invocation_calls >= options.maxModelCalls:
            issue("review_budget_exceeded" if review_pending else "model_call_budget_exceeded")
            break
        if remaining <= 0:
            issue("completion_budget_exceeded")
            break
        if not review_pending:
            payload = {
                "intent": options.intent,
                "targetSchema": options.targetSchema,
                "nodeIds": list(nodes),
                "unreadIds": [n for n in nodes if n not in seen],
                "nodes": {},
                "feedback": feedback,
                "stepContract": contract,
            }
            if candidate is not None:
                payload["previousProposal"] = candidate.model_dump()
            budget = (
                options.contextChars - len(SYSTEM) - len(encode(payload)) - len(encode(history))
            )
            if budget <= 0:
                issue("context_budget_exceeded")
                break
            wanted = list(dict.fromkeys([*selected, *[n for n in nodes if n not in seen]]))
            loaded = set()
            for node_id in wanted:
                cost = len(encode({node_id: nodes[node_id]})) + 2
                if cost <= budget:
                    payload["nodes"][node_id] = nodes[node_id]
                    loaded.add(node_id)
                    budget -= cost
            if not loaded and set(nodes) - seen and not selected:
                issue("source_node_exceeds_context_budget")
                break
            try:
                invocation_calls += 1
                result["extraction"]["modelCalls"] += 1
                save()
                message = {"role": "user", "content": encode(payload)}
                remaining = options.completionSeconds - (time.monotonic() - started)
                if remaining <= 0:
                    invocation_calls -= 1
                    result["extraction"]["modelCalls"] -= 1
                    issue("completion_budget_exceeded")
                    break
                answer = client.complete(
                    [{"role": "system", "content": SYSTEM}, *history, message],
                    timeout=remaining,
                )
                if time.monotonic() - started >= options.completionSeconds:
                    issue("completion_budget_exceeded")
                    break
                if len(answer) > options.contextChars:
                    issue("ai_response_budget_exceeded")
                    break
                history.extend([message, {"role": "assistant", "content": answer}])
                seen.update(loaded)
                step = Step.model_validate(decode(answer))
            except (ValidationError, ValueError):
                feedback = ["Invalid step JSON. Follow stepContract exactly."]
                save()
                continue
            except ModelError as exc:
                issue(exc.code)
                break
            if step.action == "read":
                if not step.readIds or not set(step.readIds) <= set(nodes):
                    feedback = ["readIds must contain existing source node IDs"]
                else:
                    selected = step.readIds
                    feedback = []
                save()
                continue
            if step.proposal is None:
                feedback = ["finish requires proposal"]
                save()
                continue
            candidate, binding_errors = materialize(step.proposal, nodes, seen)
            feedback = binding_errors + validate(candidate, nodes, seen, options.targetSchema)
            if feedback:
                if result["dataSchema"] is None:
                    result["validation"] = {
                        "valid": False,
                        "errors": feedback,
                        "semanticAccuracy": "unverified",
                    }
                else:
                    result["validation"]["repairErrors"] = feedback
                selected = []
                save()
                continue
            retain()
            review_pending = True
            save()
            # Re-enter the budget check so a just-validated candidate is always persisted.
            continue

        review_payload = encode({"sourceNodes": nodes, "proposal": candidate.model_dump()})
        if len(review_payload) + len(REVIEW) > options.contextChars:
            issue("review_context_budget_exceeded")
            break
        try:
            invocation_calls += 1
            result["extraction"]["modelCalls"] += 1
            save()
            remaining = options.completionSeconds - (time.monotonic() - started)
            if remaining <= 0:
                invocation_calls -= 1
                result["extraction"]["modelCalls"] -= 1
                issue("completion_budget_exceeded")
                break
            review_answer = client.complete(
                [
                    {"role": "system", "content": REVIEW},
                    {"role": "user", "content": review_payload},
                ],
                timeout=remaining,
            )
            if time.monotonic() - started >= options.completionSeconds:
                issue("completion_budget_exceeded")
                break
            if len(review_answer) > options.contextChars:
                raise ModelError("ai_response_budget_exceeded")
            review = Review.model_validate(decode(review_answer))
        except (ValidationError, ValueError):
            feedback = ["The internal verification response was invalid; repeat the proposal."]
            review_pending = False
            save()
            continue
        except ModelError as exc:
            issue(exc.code)
            break
        review_pending = False
        if review.issues:
            feedback = review.issues
            result["validation"]["reviewIssues"] = review.issues
            save()
            continue
        result["validation"].pop("reviewIssues", None)
        result["validation"].pop("repairErrors", None)
        result["validation"]["semanticAccuracy"] = "ai_reviewed"
        uncertain = (
            candidate.issues
            or any(a.disposition == "unresolved" for a in candidate.accounting)
            or any(a.status == "uncertain" for a in candidate.semantics)
            or any(
                e.status in {"uncertain", "unreadable"}
                for e in [*candidate.valueEvidence, *candidate.schemaEvidence]
            )
        )
        # Observation accounting is not a proof about unseen visual content.
        unsupported_visual = _has_unread_visuals(content, job.input.format_id, nodes)
        if unsupported_visual:
            issue("visual_interpretation_unverified")
        complete = (
            observation["completeness"] == "complete"
            and not uncertain
            and bool(nodes)
            and not unsupported_visual
        )
        if time.monotonic() - started >= options.completionSeconds:
            issue("completion_budget_exceeded")
            complete = False
        result["extraction"]["status"] = "complete" if complete else "partial"
        break
    save()
    return result
