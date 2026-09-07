"""Synchronous product-owned model/read/repair loop, callable by ordinary software."""

from __future__ import annotations

import hashlib
import io
import json
import time
from typing import BinaryIO

from pydantic import ValidationError

from ..analysis import AnalysisInput, AnalysisJob, AnalyzerBackend, analyze_document
from ..document_model.capture import capture
from ..structured_extraction import project_structured_extraction
from .backends import ChatCompletionsClient, ModelClient, ModelError
from .contracts import RESULT_VERSION, ExtractionOptions, Proposal, Review, Step
from .prompts import PROMPT_VERSION, REVIEW, SYSTEM
from .validation import check_schema, validate


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


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

    return json.loads(text, parse_constant=invalid_constant, object_pairs_hook=unique_object)


def extract_schema_from_stream(
    job: AnalysisJob,
    source: BinaryIO,
    *,
    options: ExtractionOptions | None = None,
    model_client: ModelClient | None = None,
    backend: AnalyzerBackend | None = None,
) -> dict:
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
    started = time.monotonic()
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
        result["issues"].append({"code": code})

    try:
        client = model_client or ChatCompletionsClient.from_environment()
    except ModelError as exc:
        issue(exc.code)
        return result
    result["provenance"]["model"] = client.identity
    seen: set[str] = set()
    selected: list[str] = []
    feedback: list[str] = []
    candidate: Proposal | None = None
    contract = Step.model_json_schema()
    history: list[dict] = []
    for _ in range(options.maxModelCalls):
        if result["extraction"]["modelCalls"] >= options.maxModelCalls:
            issue("model_call_budget_exceeded")
            break
        remaining = options.completionSeconds - (time.monotonic() - started)
        if remaining <= 0:
            issue("completion_budget_exceeded")
            break
        # The inventory exposes identifiers, not a misleading truncated document summary.
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
        budget = options.contextChars - len(SYSTEM) - len(encode(payload)) - len(encode(history))
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
            result["extraction"]["modelCalls"] += 1
            message = {"role": "user", "content": encode(payload)}
            answer = client.complete(
                [{"role": "system", "content": SYSTEM}, *history, message],
                timeout=min(remaining, 120),
            )
            if len(answer) > options.contextChars:
                issue("ai_response_budget_exceeded")
                break
            history.extend([message, {"role": "assistant", "content": answer}])
            seen.update(loaded)
            step = Step.model_validate(decode(answer))
        except (ValidationError, ValueError):
            feedback = ["Invalid step JSON. Follow stepContract exactly."]
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
            continue
        if step.proposal is None:
            feedback = ["finish requires proposal"]
            continue
        candidate = step.proposal
        feedback = validate(candidate, nodes, seen, options.targetSchema)
        result["validation"] = {
            "valid": not feedback,
            "errors": feedback,
            "semanticAccuracy": "unverified",
        }
        if feedback:
            selected = []
            continue
        review_payload = encode({"sourceNodes": nodes, "proposal": candidate.model_dump()})
        if len(review_payload) + len(REVIEW) > options.contextChars:
            issue("review_context_budget_exceeded")
            break
        remaining = options.completionSeconds - (time.monotonic() - started)
        if result["extraction"]["modelCalls"] >= options.maxModelCalls or remaining <= 0:
            issue("review_budget_exceeded")
            break
        try:
            result["extraction"]["modelCalls"] += 1
            review_answer = client.complete(
                [
                    {"role": "system", "content": REVIEW},
                    {"role": "user", "content": review_payload},
                ],
                timeout=min(remaining, 120),
            )
            if len(review_answer) > options.contextChars:
                raise ModelError("ai_response_budget_exceeded")
            review = Review.model_validate(decode(review_answer))
        except (ValidationError, ValueError):
            feedback = ["The internal verification response was invalid; repeat the proposal."]
            continue
        except ModelError as exc:
            issue(exc.code)
            break
        if review.issues:
            feedback = review.issues
            result["validation"]["reviewIssues"] = review.issues
            continue
        result["validation"]["semanticAccuracy"] = "ai_reviewed"
        result.update(
            {k: v for k, v in candidate.model_dump().items() if k not in {"accounting", "issues"}}
        )
        result["dataSchemaRevision"] = hashlib.sha256(
            encode(candidate.dataSchema).encode()
        ).hexdigest()
        result["coverage"]["semanticAccounting"] = [a.model_dump() for a in candidate.accounting]
        result["issues"].extend(
            {"code": "ai_reported_gap", "description": i} for i in candidate.issues
        )
        uncertain = (
            candidate.issues
            or any(a.disposition == "unresolved" for a in candidate.accounting)
            or any(a.status == "uncertain" for a in candidate.semantics)
            or any(e.status in {"uncertain", "unreadable"} for e in candidate.valueEvidence)
        )
        complete = observation["completeness"] == "complete" and not uncertain and bool(nodes)
        result["extraction"]["status"] = "complete" if complete else "partial"
        break
    else:
        issue("model_call_budget_exceeded")
    result["coverage"]["readNodes"] = len(seen)
    result["provenance"]["model"] = client.identity
    return result
