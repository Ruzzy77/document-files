"""Product-owned staged interpretation with compact references and durable regions."""

from __future__ import annotations

import copy
import hashlib
import io
import math
import time
from collections.abc import Callable
from typing import BinaryIO

from pydantic import ValidationError

from ..analysis import AnalysisInput, AnalysisJob, AnalyzerBackend, analyze_document
from ..document_model.capture import capture
from ..document_model.model import OBSERVATION_VERSION, ObservationDocument
from ..document_model.observe import observe_document
from ..structured_extraction import project_structured_extraction
from .backends import (
    ChatCompletionsClient,
    InferenceRequest,
    ManagedPackClient,
    ModelClient,
    ModelError,
)
from .compiler import (
    CompileError,
    combine_regions,
    compile_region,
    join_continuations,
    target_catalog,
)
from .contracts import RESULT_VERSION, ExtractionOptions
from .integration import (
    SCOPE_SYSTEM,
    SCOPE_VERSION,
    apply_scope_decision,
    build_scope_tasks,
    parse_scope_choices,
    scope_batch_payload,
    scope_batches,
    scope_output_schema,
)
from .legacy_engine import _has_unread_visuals as _has_unread_visuals
from .legacy_engine import decode, encode
from .pdf_image_read import candidate_summary
from .pdf_visual_runner import review_identity, review_pdf_pages
from .regions import (
    REGION_PLAN_VERSION,
    continuation_candidates,
    model_node,
    prepare_regions,
    region_payload,
    route_table_values,
)
from .semantic_prompts import INTEGRATE, PROMPT_VERSION, SYSTEM
from .semantic_types import (
    COMPILER_VERSION,
    SEMANTIC_VERSION,
    DocumentIntegration,
    RegionInterpretation,
    _compact_contract,
    region_output_schema,
)
from .table_protocol import (
    MEANING_REVIEW_MAX_CALLS,
    MEANING_SYSTEM,
    STAGE_INITIAL_MAX_CALLS,
    STAGE_MAX_OUTPUT_TOKENS,
    STRUCTURE_SYSTEM,
    TABLE_PROTOCOL_VERSION,
    meaning_payload,
    structural_ir,
    structure_payload,
    structure_schema,
)
from .table_protocol import (
    meaning_decision_ir as meaning_ir,
)
from .table_protocol import (
    meaning_decision_response as meaning_response,
)
from .table_protocol import (
    meaning_decision_schema as meaning_schema,
)
from .table_reference_wire import (
    VERSION as TABLE_REFERENCE_WIRE_VERSION,
)
from .table_reference_wire import prepare_meaning_wire, validate_meaning_wire_identity
from .table_revisions import (
    MeaningRevisionError,
    meaning_snapshot,
    preserve_reviewed_ranges,
    validate_revision,
)
from .table_sources import source_inventory

CHECKPOINT_VERSION = "document-files.regional-checkpoint.v2"


def _feedback_code(issue):
    """Name the affected source, binding, field or statement; never document text."""
    for key in ("sourceRef", "bindingId", "fieldId", "semanticId"):
        if issue.get(key):
            return f"{issue['code']}:{issue[key]}"
    return issue["code"]


def _table_meaning_issues(fragment):
    # These can be fixed by a meaning/disposition response, unlike OCR values,
    # record geometry, or applicability handled by the separate scope protocol.
    return [
        i
        for i in fragment.issues
        if i.get("code")
        in {
            "node_semantics_unaccounted",
            "note_scope_unresolved",
            "table_meaning_source_unreviewed",
            "table_meaning_source_unresolved",
        }
    ]


def _meaning_feedback(ir, fragment, inventory, extra=()):
    uncertain = {meaning.id for meaning in ir.meanings if meaning.status == "uncertain"}
    return {
        "issues": [*extra, *[_feedback_code(i) for i in _table_meaning_issues(fragment)]],
        "baseRevision": ir.tableMeaningState.revisionSHA256,
        "acceptedResponse": meaning_response(ir, inventory),
        "remainingSourceRanges": [
            copy.deepcopy(item)
            for item in fragment.meaning_review.get("ranges", [])
            if item["role"] in {"unreviewed", "unresolved"}
            or uncertain.intersection(item.get("meaningIds", []))
        ],
        "instruction": (
            "Review the reported source gaps and the prior interpretation together. "
            "Correct mistaken kind, description, scope or status; split, merge or withdraw "
            "mistaken meanings with explicit changes. Keep source text and reviewed ranges. "
            "For a withdrawal, review its source as no_additional_meaning or unresolved. "
            "Return the full replacement, not a patch. Do not change frozen structure or values."
        ),
    }


def _table_call_available(stage, progress):
    # A rejected initial response must not consume the one review of an accepted
    # meaning. Both allowances remain subordinate to the document-wide budget.
    if stage == "meaning" and progress.get("acceptedResponse"):
        return progress["reviewAttempts"] < MEANING_REVIEW_MAX_CALLS
    return progress["attempts"] < STAGE_INITIAL_MAX_CALLS


def _meaning_repair_improves(before_ir, before, after_ir, after):
    # Fewer issues are not proof of correctness. An explicit correction may expose
    # uncertainty, but may not lose source review or alter established value facts.
    ignored = {
        "node_semantics_unaccounted",
        "note_scope_unresolved",
        "semantic_scope_unresolved",
        "semantic_scope_uncertain",
        "table_meaning_source_unreviewed",
        "table_meaning_source_unresolved",
        "semantic_relation_unresolved",
    }
    try:
        validate_revision(before_ir, after_ir)
        preserve_reviewed_ranges(before.meaning_review, after.meaning_review)
    except MeaningRevisionError as exc:
        raise CompileError(str(exc)) from None
    return {encode(i) for i in after.issues if i.get("code") not in ignored} <= {
        encode(i) for i in before.issues if i.get("code") not in ignored
    }


def _validate_meaning_history(progress, current, observation, region, target_schema):
    history = progress.get("revisions", [])
    if not progress.get("acceptedResponse"):
        if history or current.tableMeaningState is not None:
            raise ValueError("invalid_table_meaning_history")
        return
    if not isinstance(history, list) or not 1 <= len(history) <= progress["usage"]["modelCalls"]:
        raise ValueError("invalid_table_meaning_history")
    previous, before = None, None
    for snapshot in history:
        if not isinstance(snapshot, dict) or snapshot.keys() != meaning_snapshot(current).keys():
            raise ValueError("invalid_table_meaning_history")
        restored = RegionInterpretation.model_validate(current.model_dump() | snapshot)
        validate_revision(previous, restored)
        fragment = compile_region(restored, observation, region, target_schema=target_schema)
        if before is not None:
            preserve_reviewed_ranges(before.meaning_review, fragment.meaning_review)
        previous, before = restored, fragment
    if meaning_snapshot(previous) != meaning_snapshot(current):
        raise ValueError("invalid_table_meaning_history")


def _node_read_coverage(regions, accepted):
    """A node split across views is fully read only after every owning view."""
    owners, seen = {}, set()
    accepted = set(accepted)
    for region in regions:
        for ref in region["nodeIds"]:
            owners.setdefault(ref, set()).add(region["id"])
        if region["id"] in accepted:
            seen.update([*region["nodeIds"], *region.get("contextNodeIds", [])])
    complete = {ref for ref in seen if owners.get(ref, set()) <= accepted}
    return {"readNodes": len(complete), "partiallyReadNodes": len(seen - complete)}


def observation_identity(backend):
    if backend is None:
        return {"adapter": "native-observation", "version": OBSERVATION_VERSION}
    identity = getattr(backend, "identity", None)
    if not isinstance(identity, dict):
        raise ValueError("observation backend must provide a stable identity")
    return copy.deepcopy(identity)


def validate_additional_budget(value):
    if value is None:
        return {"maxModelCalls": 0, "completionSeconds": 0}
    if not isinstance(value, dict) or set(value) - {"maxModelCalls", "completionSeconds"}:
        raise ValueError("invalid additional extraction budget")
    result = {
        "maxModelCalls": value.get("maxModelCalls", 0),
        "completionSeconds": value.get("completionSeconds", 0),
    }
    if any(
        type(v) is not int or not 0 <= v <= (100 if k == "maxModelCalls" else 3600)
        for k, v in result.items()
    ) or not any(result.values()):
        raise ValueError("invalid additional extraction budget")
    return result


def _restored_usage(value):
    if not isinstance(value, dict):
        raise ValueError("invalid checkpoint usage")
    try:
        if any(
            type(value[key]) is not int or value[key] < 0
            for key in ("modelCalls", "promptTokens", "completionTokens")
        ) or (
            type(value["elapsedSeconds"]) not in (int, float)
            or not math.isfinite(value["elapsedSeconds"])
            or value["elapsedSeconds"] < 0
        ):
            raise ValueError
        restored = dict(value)
        # Old checkpoints cannot establish how many calls reported both totals.
        unknown = restored.setdefault("unreportedUsageCalls", restored["modelCalls"])
        if type(unknown) is not int or not 0 <= unknown <= restored["modelCalls"]:
            raise ValueError
        return restored
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid checkpoint usage") from None


def _initial_result(job, observation, analyzer, selected, client, content):
    result = {
        "schemaVersion": RESULT_VERSION,
        "jobId": job.job_id,
        "source": job.input.to_dict(),
        "document": {
            "nodes": observation.nodes,
            "observationVersion": OBSERVATION_VERSION,
            "bindings": observation.bindings,
            "structure": {
                "schemaVersion": "document-files.structure.v1",
                "regions": observation.regions,
                "tables": observation.tables,
                "relations": observation.relations,
            },
        },
        "documentSchema": {"type": "object", "additionalProperties": {"type": "object"}},
        "dataSchema": None,
        "dataSchemaRevision": None,
        "data": None,
        "semantics": [],
        "schemaEvidence": [],
        "valueEvidence": [],
        "semanticDetails": [],
        "semanticDetailsVersion": "document-files.semantic-details.v1",
        "valueObservations": [],
        "extraction": {"status": "partial", "modelCalls": 0, "stage": "observed"},
        "coverage": {
            "observation": observation.coverage,
            "readNodes": 0,
            "totalNodes": len(observation.nodes),
            "semanticAccounting": [],
            "regions": [],
        },
        "validation": {"valid": False, "errors": [], "semanticAccuracy": "unverified"},
        "issues": [],
        "provenance": {
            "analyzer": analyzer,
            "observation": observation.provenance,
            "promptVersion": PROMPT_VERSION,
            "compilerVersion": COMPILER_VERSION,
            "semanticVersion": SEMANTIC_VERSION,
            "regionPlanVersion": REGION_PLAN_VERSION,
            "model": client.identity if client else None,
        },
    }
    if selected.reconstructionContext:
        result["reconstructionContext"] = capture(
            content, job.input.format_id, max_expanded_bytes=selected.maxInputBytes * 4
        )
    return result


def extract_schema_from_stream(
    job: AnalysisJob,
    source: BinaryIO,
    *,
    options: ExtractionOptions | None = None,
    model_client: ModelClient | None = None,
    backend: AnalyzerBackend | None = None,
    observation_backend=None,
    restore: dict | None = None,
    checkpoint: Callable[[dict], None] | None = None,
    additional_budget: dict | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Interpret internally; only explicitly selected legacy integrations use Proposal v1."""
    if getattr(model_client, "interpretation_protocol", "compact") == "legacy":
        if observation_backend is not None or additional_budget is not None:
            raise ValueError("legacy interpretation does not support new stage or budget options")
        from .legacy_engine import extract_schema_from_stream as legacy

        return legacy(
            job,
            source,
            options=options,
            model_client=model_client,
            backend=backend,
            restore=restore,
            checkpoint=checkpoint,
        )
    started = time.monotonic()
    selected = options or ExtractionOptions()
    if job.input.byte_size > selected.maxInputBytes:
        raise ValueError("schema extraction input budget exceeded")
    grant = validate_additional_budget(additional_budget)
    if restore is None and additional_budget is not None:
        raise ValueError("additional budget requires an existing checkpoint")
    chunks, size = [], 0
    while chunk := source.read(min(1024 * 1024, selected.maxInputBytes + 1 - size)):
        size += len(chunk)
        if size > selected.maxInputBytes:
            raise ValueError("schema extraction input budget exceeded")
        chunks.append(chunk)
    content = b"".join(chunks)
    if AnalysisInput.from_bytes(content, format_id=job.input.format_id) != job.input:
        raise ValueError("schema extraction bytes do not match input identity")
    try:
        client = model_client or ChatCompletionsClient.from_environment()
        client_issue = None
    except ModelError as exc:
        client, client_issue = None, exc.code
    model_identity = (
        {k: v for k, v in client.identity.items() if k != "returnedModel"}
        if client
        else {"available": False}
    )
    identity = {
        "source": job.input.to_dict(),
        "options": selected.model_dump(),
        "observationVersion": OBSERVATION_VERSION,
        "observationBackend": observation_identity(observation_backend),
        "promptVersion": PROMPT_VERSION,
        "compilerVersion": COMPILER_VERSION,
        "scopeVersion": SCOPE_VERSION,
        "tableProtocolVersion": TABLE_PROTOCOL_VERSION,
        "tableReferenceWireVersion": TABLE_REFERENCE_WIRE_VERSION,
        "regionPlanVersion": REGION_PLAN_VERSION,
        "model": model_identity,
    }
    visual_policy = review_identity(client) if job.input.format_id == "pdf" else None
    if visual_policy is not None:
        identity["pdfVisualReview"] = visual_policy
    visual_restore = None
    if restore is not None and restore.get("phase") == "reviewing_pdf":
        visual_restore = restore
        restore = {
            **restore,
            "regions": [],
            "accepted": {},
            "decisions": {},
            "scopeDecisions": {},
            "tableStages": {},
            "failures": {},
            "repairDiagnostics": {},
        }
    accepted, decisions, failures = {}, {}, {}
    repair_diagnostics = {}
    scope_decisions = {}
    table_states = {}
    usage = {
        "modelCalls": 0,
        "elapsedSeconds": 0.0,
        "promptTokens": 0,
        "completionTokens": 0,
        "unreportedUsageCalls": 0,
    }
    grants = []
    observing_restore = None
    if restore is not None and restore.get("phase") == "observing":
        if (
            restore.get("version") != CHECKPOINT_VERSION
            or restore.get("identity") != identity
            or restore.get("result", {}).get("jobId") != job.job_id
            or restore.get("result", {}).get("source") != job.input.to_dict()
        ):
            raise ValueError("observation checkpoint is incompatible with input or configuration")
        observing_restore, restore = restore, None
        usage = _restored_usage(observing_restore.get("usage"))
        grants = copy.deepcopy(observing_restore["grants"])
        for prior in grants:
            validate_additional_budget(prior)
        if additional_budget is not None:
            grants.append(grant)
    if restore is not None:
        try:
            if restore["version"] != CHECKPOINT_VERSION or restore["identity"] != identity:
                raise ValueError
            result = copy.deepcopy(restore["result"])
            if (
                result["schemaVersion"] != RESULT_VERSION
                or result["jobId"] != job.job_id
                or result["source"] != job.input.to_dict()
            ):
                raise ValueError
            doc = result["document"]
            structure = doc["structure"]
            observation = ObservationDocument(
                nodes=doc["nodes"],
                bindings=doc["bindings"],
                regions=structure["regions"],
                tables=structure["tables"],
                relations=structure["relations"],
                issues=restore["observationIssues"],
                coverage=result["coverage"]["observation"],
                provenance=result["provenance"]["observation"],
            )
            regions = copy.deepcopy(restore["regions"])
            accepted = {
                key: RegionInterpretation.model_validate(value)
                for key, value in restore["accepted"].items()
            }
            decisions = dict(restore["decisions"])
            scope_decisions = copy.deepcopy(restore.get("scopeDecisions", {}))
            table_states = copy.deepcopy(restore["tableStages"])
            if not isinstance(table_states, dict):
                raise ValueError
            for rid, state in table_states.items():
                if not isinstance(state, dict):
                    raise ValueError
                if rid not in {r["id"] for r in regions if r.get("tableRef")}:
                    raise ValueError
                if state.get("kind") not in {None, "record_table", "scalar_form", "unresolved"}:
                    raise ValueError
                for stage in ("structure", "meaning"):
                    record = state.get(stage, {})
                    if not isinstance(record, dict):
                        raise ValueError
                    if record.get("status", "pending") not in {
                        "pending",
                        "running",
                        "complete",
                        "failed",
                    }:
                        raise ValueError
                    if type(record.get("acceptedResponse", False)) is not bool:
                        raise ValueError
                    _restored_usage(record["usage"])
                    attempts = record.get("attempts", 0)
                    reviews = record.get("reviewAttempts")
                    if (
                        type(attempts) is not int
                        or type(reviews) is not int
                        or not 0 <= reviews <= MEANING_REVIEW_MAX_CALLS
                        or not 0 <= attempts - reviews <= STAGE_INITIAL_MAX_CALLS
                        or reviews > attempts
                        or attempts > record["usage"]["modelCalls"]
                        or (reviews and (stage != "meaning" or not record.get("acceptedResponse")))
                    ):
                        raise ValueError
                if state.get("kind") == "record_table" and rid not in accepted:
                    raise ValueError
                meaning = state.get("meaning", {})
                if (
                    meaning.get("attempts") or "inputPreflight" in meaning
                ) and "referenceWire" not in meaning:
                    raise ValueError
                if "referenceWire" in meaning:
                    region = next(r for r in regions if r["id"] == rid)
                    validate_meaning_wire_identity(
                        region_payload(observation, region), meaning["referenceWire"]
                    )
            failures = dict(restore["failures"])
            repair_diagnostics = copy.deepcopy(restore.get("repairDiagnostics", {}))
            if not isinstance(repair_diagnostics, dict) or any(
                not isinstance(key, str)
                or not isinstance(value, list)
                or len(value) > 20
                or any(not isinstance(item, str) or len(item) > 500 for item in value)
                for key, value in repair_diagnostics.items()
            ):
                raise ValueError
            usage = _restored_usage(restore["usage"])
            grants = list(restore["grants"])
            for prior in grants:
                validate_additional_budget(prior)
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                "checkpoint is incompatible with input, options, observation, model or engine"
            ) from None
        if additional_budget is not None:
            grants.append(grant)
    else:
        if observing_restore is not None:
            legacy = copy.deepcopy(observing_restore["nativeProjection"])
            analysis_descriptor = copy.deepcopy(
                observing_restore["result"]["provenance"]["analyzer"]
            )
        else:
            analysis = analyze_document(job, io.BytesIO(content), backend=backend)
            analysis_descriptor = analysis.analyzer.to_dict()
            legacy = project_structured_extraction(
                analysis.extraction,
                source_format=job.input.format_id,
                unit_offset=0,
                max_units=len(analysis.extraction.units),
                include_text=True,
            )
        legacy_nodes = {f"n{unit['ordinal']}": unit for unit in legacy["units"]}
        early = _initial_result(
            job,
            ObservationDocument(nodes=legacy_nodes),
            analysis_descriptor,
            selected,
            client,
            content,
        )
        recognition_state = (
            observing_restore.get("recognitionResume") if observing_restore else None
        )
        elapsed_before = usage["elapsedSeconds"]
        observed_backend = observation_backend

        def save_recognition(state, public_result=None):
            nonlocal recognition_state
            recognition_state = state
            body = public_result if public_result is not None else early
            progress = state.get("completedPages", []) if state else []
            body["document"]["recognitionProgress"] = {"completedPages": progress}
            body["extraction"].update(
                stage="recognizing", status="partial", modelCalls=usage["modelCalls"]
            )
            consumed = {
                **usage,
                "elapsedSeconds": elapsed_before + max(0.0, time.monotonic() - started),
            }
            body["extraction"]["usage"] = consumed
            if checkpoint:
                checkpoint(
                    copy.deepcopy(
                        {
                            "version": CHECKPOINT_VERSION,
                            "phase": "observing",
                            "identity": identity,
                            "result": body,
                            "nativeProjection": legacy,
                            "recognitionResume": state,
                            "usage": consumed,
                            "grants": grants,
                        }
                    )
                )

        if observation_backend is not None and getattr(
            observation_backend, "supports_checkpoints", False
        ):

            class CheckpointRecognition:
                def observe(self, data):
                    available = (
                        selected.completionSeconds
                        + sum(g["completionSeconds"] for g in grants)
                        - elapsed_before
                        - (time.monotonic() - started)
                    )
                    if available <= 0:
                        return {
                            "status": "partial",
                            "issues": [{"code": "completion_budget_exceeded"}],
                            "pageResults": recognition_state.get("pageResults", [])
                            if recognition_state
                            else [],
                        }
                    save_recognition(recognition_state)
                    return observation_backend.observe(
                        data,
                        restore=recognition_state,
                        checkpoint=save_recognition,
                        timeout_seconds=available,
                    )

            observed_backend = CheckpointRecognition()
        observation = observe_document(
            content, job.input.format_id, legacy_nodes, recognition=observed_backend
        )
        observation.issues = [*legacy["issues"], *observation.issues]
        observation.coverage["legacyAnalysis"] = legacy["coverage"]
        try:
            planned_catalog = target_catalog(selected.targetSchema)
        except CompileError:
            planned_catalog = {}  # The shared validation below reports the error.
        regions = (
            []
            if visual_policy is not None
            else prepare_regions(
                observation,
                request_metadata={"intent": selected.intent, "targetHandles": planned_catalog},
                context_chars=min(
                    selected.contextChars,
                    getattr(client, "input_budget_chars", selected.contextChars),
                ),
            )
        )
        result = _initial_result(job, observation, analysis_descriptor, selected, client, content)
        if (
            job.input.format_id == "pdf"
            and getattr(observation_backend, "supports_checkpoints", False)
            and observation.coverage.get("recognition") != "complete"
        ):
            result["issues"] = list(observation.issues)
            save_recognition(recognition_state, result)
            return result
    if visual_policy is not None and (restore is None or visual_restore is not None):
        visual_elapsed_before = usage["elapsedSeconds"]
        visual_max_calls = selected.maxModelCalls + sum(g["maxModelCalls"] for g in grants)
        visual_max_seconds = selected.completionSeconds + sum(
            g["completionSeconds"] for g in grants
        )

        def save_visual(state):
            consumed = {
                **usage,
                "elapsedSeconds": visual_elapsed_before + max(0.0, time.monotonic() - started),
            }
            result["extraction"].update(
                status="partial",
                stage="reviewing_pdf",
                modelCalls=usage["modelCalls"],
                usage=consumed,
                budget={"maxModelCalls": visual_max_calls, "completionSeconds": visual_max_seconds},
            )
            result["coverage"]["pdfPageReview"] = [
                {"page": int(page), "status": value["status"]}
                for page, value in state["pages"].items()
            ]
            candidates = [
                candidate_summary(value["imageRead"])
                for value in state["pages"].values()
                if value.get("imageRead", {}).get("status") in {"read", "unresolved"}
            ]
            if candidates or "pdfImageReadCandidates" in result["provenance"]["observation"]:
                result["provenance"]["observation"]["pdfImageReadCandidates"] = candidates
            result["issues"] = [
                *observation.issues,
                *([{"code": state["haltReason"]}] if state.get("haltReason") else []),
            ]
            if checkpoint:
                checkpoint(
                    copy.deepcopy(
                        {
                            "version": CHECKPOINT_VERSION,
                            "phase": "reviewing_pdf",
                            "identity": identity,
                            "result": result,
                            "pdfVisualReview": state,
                            "observationIssues": observation.issues,
                            "usage": consumed,
                            "grants": grants,
                        }
                    )
                )

        reviewed, visual_state = review_pdf_pages(
            content,
            observation,
            client=client,
            usage=usage,
            max_calls=visual_max_calls,
            deadline=started + visual_max_seconds - visual_elapsed_before,
            context_chars=min(
                selected.contextChars, getattr(client, "input_budget_chars", selected.contextChars)
            ),
            checkpoint=save_visual,
            restore=visual_restore["pdfVisualReview"] if visual_restore else None,
            cancelled=cancelled,
        )
        if reviewed is None:
            return result
        observation = reviewed
        try:
            planned_catalog = target_catalog(selected.targetSchema)
        except CompileError:
            planned_catalog = {}
        regions = prepare_regions(
            observation,
            request_metadata={"intent": selected.intent, "targetHandles": planned_catalog},
            context_chars=min(
                selected.contextChars, getattr(client, "input_budget_chars", selected.contextChars)
            ),
        )
        result = _initial_result(
            job, observation, result["provenance"]["analyzer"], selected, client, content
        )
        result["coverage"]["pdfPageReview"] = [
            {"page": int(page), "status": value["status"]}
            for page, value in visual_state["pages"].items()
        ]
    if additional_budget is not None and grant["maxModelCalls"] > 0:
        for state in table_states.values():
            for stage in ("structure", "meaning"):
                record = state.get(stage, {})
                if record.get("status") != "complete":
                    record["attempts"] = 0
                    record["reviewAttempts"] = 0
    prior_elapsed = usage["elapsedSeconds"]
    max_calls = selected.maxModelCalls + sum(g["maxModelCalls"] for g in grants)
    max_seconds = selected.completionSeconds + sum(g["completionSeconds"] for g in grants)
    issues = list(observation.issues)
    issues.extend(
        {"code": "region_interpretation_invalid", "regionId": key, "errors": value}
        for key, value in repair_diagnostics.items()
    )
    candidates = continuation_candidates(observation, regions)
    compiled = {}
    for region in regions:
        if region["id"] in accepted:
            try:
                compiled[region["id"]] = compile_region(
                    accepted[region["id"]], observation, region, target_schema=selected.targetSchema
                )
                if region["id"] in table_states:
                    _validate_meaning_history(
                        table_states[region["id"]]["meaning"],
                        accepted[region["id"]],
                        observation,
                        region,
                        selected.targetSchema,
                    )
            except (ValueError, TypeError, KeyError):
                raise ValueError("checkpoint is incompatible with source review history") from None
    try:
        catalog = target_catalog(selected.targetSchema)
    except CompileError as exc:
        issues.append({"code": str(exc)})
        catalog = None
    if restore is not None and catalog is not None:
        for region in regions:
            rid = region["id"]
            progress = table_states.get(rid, {}).get("meaning", {})
            if "referenceWire" not in progress:
                continue
            try:
                payload = region_payload(observation, region) | {
                    "intent": selected.intent,
                    "targetHandles": catalog,
                }
                expected = prepare_meaning_wire(
                    meaning_payload(
                        payload, accepted[rid], compiled[rid], source_inventory(observation, region)
                    ),
                    meaning_schema(observation, region, accepted[rid], catalog),
                ).identity
                if expected != progress["referenceWire"]:
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise ValueError("checkpoint is incompatible with table reference wire") from None

    def issue(code, **details):
        item = {"code": code, **details}
        if item not in issues:
            issues.append(item)

    def linked_regions():
        linked, join_issues, links = join_continuations(
            list(compiled.values()), candidates, decisions
        )
        scope_tasks = build_scope_tasks(
            observation,
            regions,
            linked,
            context_chars=min(
                12000,
                max(
                    1024,
                    min(
                        selected.contextChars,
                        getattr(client, "input_budget_chars", selected.contextChars),
                    )
                    - 4000,
                ),
            ),
        )
        for task in scope_tasks:
            stored = scope_decisions.get(task.id, {})
            if stored.get("fingerprint") == task.fingerprint and "decision" in stored:
                try:
                    linked, _ = apply_scope_decision(linked, task, stored["decision"])
                except CompileError:
                    issue("scope_decision_stale", taskId=task.id)
        return linked, join_issues, links, scope_tasks

    def refresh(stage):
        linked, join_issues, links, scope_tasks = linked_regions()
        projection = combine_regions(linked, target_schema=selected.targetSchema)
        errors = projection.pop("errors")
        projection_issues = projection.pop("issues")
        result.update(projection)
        result["dataSchemaRevision"] = (
            hashlib.sha256(encode(result["dataSchema"]).encode()).hexdigest()
            if result["dataSchema"] is not None
            else None
        )
        result["issues"] = [*issues, *projection_issues, *join_issues]
        result["document"]["semanticRelations"] = links
        result["coverage"]["semanticAccounting"] = [
            d for c in compiled.values() for d in c.dispositions
        ]
        result["coverage"]["semanticSourceReviews"] = [
            copy.deepcopy(c.meaning_review)
            for c in compiled.values()
            if c.meaning_review is not None
        ]
        result["coverage"]["regions"] = [
            {
                "id": r["id"],
                "status": (
                    "structure_compiled"
                    if table_states.get(r["id"], {}).get("kind") == "record_table"
                    and table_states[r["id"]].get("meaning", {}).get("status") != "complete"
                    else "interpreted"
                    if r["id"] in compiled
                    else "pending"
                ),
                "nodeIds": r["nodeIds"],
                "inputChars": r["inputChars"],
                **({"nodeViews": r["nodeViews"]} if r.get("nodeViews") else {}),
            }
            for r in regions
        ]
        result["coverage"]["tableInterpretation"] = copy.deepcopy(table_states)
        result["coverage"].update(_node_read_coverage(regions, compiled))
        result["coverage"]["unprocessedRegions"] = [
            r["id"] for r in regions if r["id"] not in compiled
        ]
        result["coverage"]["scopeIntegration"] = [
            {
                "taskId": task.id,
                "semanticId": task.semantic_id,
                "candidateCoverage": task.payload["candidateCoverage"],
                "status": "interpreted"
                if task.complete_candidates
                and scope_decisions.get(task.id, {}).get("fingerprint") == task.fingerprint
                and scope_decisions.get(task.id, {}).get("decision", {}).get("decision") == "apply"
                else "unresolved",
            }
            for task in scope_tasks
        ]
        result["validation"].update(valid=bool(compiled) and not errors, errors=errors)
        usage["elapsedSeconds"] = prior_elapsed + max(0.0, time.monotonic() - started)
        result["extraction"].update(
            modelCalls=usage["modelCalls"],
            stage=stage,
            usage=dict(usage),
            budget={"maxModelCalls": max_calls, "completionSeconds": max_seconds},
        )
        result["provenance"]["model"] = client.identity if client else None
        result["provenance"]["scopeIntegrationVersion"] = SCOPE_VERSION
        result["provenance"]["tableProtocolVersion"] = TABLE_PROTOCOL_VERSION
        result["provenance"]["tableReferenceWireVersion"] = TABLE_REFERENCE_WIRE_VERSION
        complete = (
            any(c.has_data for c in compiled.values())
            and bool(compiled)
            and len(compiled) == len(regions)
            and all(
                state.get("kind") == "scalar_form"
                or (
                    state.get("kind") == "record_table"
                    and state.get("meaning", {}).get("status") == "complete"
                )
                for state in table_states.values()
            )
            and not result["issues"]
            and not errors
        )
        result["extraction"]["status"] = "complete" if complete else "partial"

    def save(stage):
        refresh(stage)
        if checkpoint:
            checkpoint(
                copy.deepcopy(
                    {
                        "version": CHECKPOINT_VERSION,
                        "identity": identity,
                        "result": result,
                        "regions": regions,
                        "accepted": {key: value.model_dump() for key, value in accepted.items()},
                        "decisions": decisions,
                        "scopeDecisions": scope_decisions,
                        "tableStages": table_states,
                        "failures": failures,
                        "repairDiagnostics": repair_diagnostics,
                        "usage": usage,
                        "grants": grants,
                        "observationIssues": observation.issues,
                    }
                )
            )

    def remaining():
        return max_seconds - prior_elapsed - (time.monotonic() - started)

    def invoke(system, payload, contract, feedback=None, *, table_stage=None, meaning_wire=None):
        if cancelled and cancelled():
            raise ModelError("ai_cancelled")
        if usage["modelCalls"] >= max_calls:
            raise ModelError("model_call_budget_exceeded")
        timeout = remaining()
        if timeout <= 0:
            raise ModelError("completion_budget_exceeded")
        content = {**payload, "outputContract": contract}
        if feedback is not None:
            # Feedback follows the unchanged region and contract, so a repair call
            # shares its whole prompt prefix with the original call.
            content["repairFeedback"] = feedback
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": encode(content)},
        ]
        total_chars = sum(len(m["content"]) for m in messages)
        input_limit = min(
            selected.contextChars, getattr(client, "input_budget_chars", selected.contextChars)
        )
        if table_stage is not None:
            table_stage["inputPreflight"] = {
                "stage": payload["tableStage"],
                "phase": "repair" if feedback is not None else "initial",
                "systemCharacters": len(system),
                "payloadCharacters": len(encode(payload)),
                "outputContractCharacters": len(encode(contract)),
                "feedbackCharacters": len(encode(feedback)) if feedback is not None else 0,
                "totalMessageContentCharacters": total_chars,
                "limitCharacters": input_limit,
                "withinBudget": total_chars <= input_limit,
            }
        if total_chars > input_limit:
            raise ModelError("region_context_budget_exceeded")
        stage_started = time.monotonic()
        stage_before = dict(usage)
        try:
            usage["modelCalls"] += 1
            usage["unreportedUsageCalls"] += 1
            if table_stage is not None:
                table_stage["attempts"] = table_stage.get("attempts", 0) + 1
                if table_stage.get("acceptedResponse"):
                    table_stage["reviewAttempts"] += 1
                table_stage["status"] = "running"
                table_stage.setdefault("usage", {})["modelCalls"] = (
                    table_stage.get("usage", {}).get("modelCalls", 0) + 1
                )
                table_stage["usage"]["unreportedUsageCalls"] += 1
            save("interpreting")
            if hasattr(client, "infer"):
                result["extraction"].pop("lastInferenceDiagnostics", None)
                try:
                    response = client.infer(
                        InferenceRequest(
                            messages=messages,
                            output_schema=contract,
                            max_output_tokens=(
                                min(
                                    getattr(client, "max_output_tokens", None) or 8192,
                                    STAGE_MAX_OUTPUT_TOKENS,
                                )
                                if table_stage is not None
                                else getattr(client, "max_output_tokens", None) or 8192
                            ),
                            timeout=timeout,
                            cancelled=cancelled,
                        )
                    )
                finally:
                    if isinstance(client, ManagedPackClient):
                        result["extraction"]["lastInferenceDiagnostics"] = client.last_diagnostics
                if all(
                    type(response.usage.get(key)) is int and response.usage[key] >= 0
                    for key in ("prompt_tokens", "completion_tokens")
                ):
                    usage["unreportedUsageCalls"] -= 1
                for src, dest in (
                    ("prompt_tokens", "promptTokens"),
                    ("completion_tokens", "completionTokens"),
                ):
                    count = response.usage.get(src, 0)
                    if type(count) is int and count >= 0:
                        usage[dest] += count
                if response.finish_reason != "stop":
                    raise ModelError("ai_response_incomplete")
                value = decode(response.text)
            else:
                value = decode(client.complete(messages, timeout=timeout))
            return meaning_wire.decode(value) if meaning_wire is not None else value
        finally:
            if table_stage is not None:
                stage_usage = table_stage.setdefault("usage", {})
                for key in ("promptTokens", "completionTokens", "unreportedUsageCalls"):
                    stage_usage[key] = (
                        stage_usage.get(key, 0)
                        + usage[key]
                        - stage_before[key]
                        - (1 if key == "unreportedUsageCalls" else 0)
                    )
                stage_usage["elapsedSeconds"] = stage_usage.get("elapsedSeconds", 0.0) + max(
                    0.0, time.monotonic() - stage_started
                )

    save("observed" if not accepted else "interpreting")
    if not client:
        issue(client_issue or "ai_unavailable")
        save("paused")
        return result
    if catalog is None:
        save("paused")
        return result

    def local_issues(fragment):
        return [i for i in fragment.issues if i.get("code") != "semantic_scope_unresolved"]

    def interpret_table(region, payload):
        rid = region["id"]
        state = table_states.setdefault(rid, {})
        for stage in ("structure", "meaning"):
            progress = state.setdefault(
                stage,
                {
                    "status": "pending",
                    "attempts": 0,
                    "reviewAttempts": 0,
                    "usage": {
                        "modelCalls": 0,
                        "promptTokens": 0,
                        "completionTokens": 0,
                        "elapsedSeconds": 0.0,
                        "unreportedUsageCalls": 0,
                    },
                },
            )
        for stage in ("structure", "meaning"):
            progress = state[stage]
            if progress["status"] == "complete":
                if stage == "structure" and state["kind"] != "record_table":
                    if state["kind"] == "unresolved":
                        issue("table_kind_unresolved", regionId=rid)
                    return state["kind"] != "scalar_form"
                continue
            if stage == "meaning" and state.get("kind") != "record_table":
                return True
            system = STRUCTURE_SYSTEM if stage == "structure" else MEANING_SYSTEM
            contract = (
                structure_schema(observation, region, catalog)
                if stage == "structure"
                else meaning_schema(observation, region, accepted[rid], catalog)
            )
            # Routing can transfer non-record cells after structure compilation.
            # Rebuild the request so meaning never sees their value bindings.
            payload = region_payload(observation, region) | {
                "intent": selected.intent,
                "targetHandles": catalog,
                **(
                    {"sameTableMapping": payload["sameTableMapping"]}
                    if "sameTableMapping" in payload
                    else {}
                ),
            }
            inventory = source_inventory(observation, region) if stage == "meaning" else None
            request = (
                structure_payload(payload)
                if stage == "structure"
                else meaning_payload(payload, accepted[rid], compiled[rid], inventory)
            )
            while _table_call_available(stage, progress):
                try:
                    try:
                        wire = (
                            prepare_meaning_wire(request, contract, progress.get("feedback"))
                            if stage == "meaning"
                            else None
                        )
                    except (ValueError, TypeError, KeyError):
                        # Preparation is not a model response. Repeating it cannot
                        # spend a model attempt or repair a deterministic failure.
                        raise ModelError("table_reference_wire_preparation_failed") from None
                    if wire is not None:
                        if (
                            "referenceWire" in progress
                            and progress["referenceWire"] != wire.identity
                        ):
                            raise ModelError("table_reference_wire_checkpoint_mismatch")
                        progress["referenceWire"] = copy.deepcopy(wire.identity)
                    value = invoke(
                        system,
                        wire.payload if wire is not None else request,
                        wire.contract if wire is not None else contract,
                        wire.feedback if wire is not None else progress.get("feedback"),
                        table_stage=progress,
                        meaning_wire=wire,
                    )
                    if stage == "structure":
                        decision, candidate = structural_ir(value, observation, region)
                        if candidate is None:
                            state["kind"] = decision.tableKind
                            progress["status"] = "complete"
                            if decision.tableKind == "unresolved":
                                issue("table_kind_unresolved", regionId=rid)
                            save("interpreting")
                            return decision.tableKind != "scalar_form"
                    else:
                        candidate = meaning_ir(value, accepted[rid], inventory)
                    fragment = compile_region(
                        candidate, observation, region, target_schema=selected.targetSchema
                    )
                    if stage == "structure":
                        structural_errors = sorted(
                            {
                                i["code"]
                                for i in fragment.issues
                                if i["code"]
                                in {
                                    "column_definition_not_above_column",
                                    "column_leaf_header_missing",
                                    "table_rows_outside_repeat",
                                    "header_cell_bound_as_value",
                                    "repeat_row_roles_incomplete",
                                }
                            }
                        )
                        if structural_errors:
                            raise CompileError(",".join(structural_errors))
                    # Meaning can add assertions/accounting, never change committed cells.
                    if stage == "meaning" and (
                        fragment.data != compiled[rid].data
                        or fragment.schema != compiled[rid].schema
                        or candidate.repeats != accepted[rid].repeats
                        or candidate.fields != accepted[rid].fields
                        or candidate.groups != accepted[rid].groups
                        or fragment.consumed_bindings != compiled[rid].consumed_bindings
                    ):
                        raise CompileError("table_meaning_changed_structure")
                    if (
                        stage == "meaning"
                        and progress.get("acceptedResponse")
                        and not _meaning_repair_improves(
                            accepted[rid], compiled[rid], candidate, fragment
                        )
                    ):
                        raise CompileError("table_meaning_repair_no_progress")
                    accepted[rid], compiled[rid] = candidate, fragment
                    if stage == "meaning":
                        progress["acceptedResponse"] = True
                        progress.setdefault("revisions", []).append(meaning_snapshot(candidate))
                    if stage == "structure":
                        state["kind"] = "record_table"
                        child, routes = route_table_values(
                            observation,
                            region,
                            candidate,
                            fragment,
                            context_chars=min(
                                selected.contextChars,
                                getattr(client, "input_budget_chars", selected.contextChars),
                            ),
                            metadata={"intent": selected.intent, "targetHandles": catalog},
                        )
                        progress["valueRoutes"] = routes
                        if child is not None:
                            regions.append(child)
                            compiled[rid] = compile_region(
                                candidate, observation, region, target_schema=selected.targetSchema
                            )
                    progress["status"] = "complete"
                    progress.pop("feedback", None)
                    issues[:] = [
                        i
                        for i in issues
                        if not (
                            i.get("code") == "table_stage_invalid"
                            and i.get("regionId") == rid
                            and i.get("tableStage") == stage
                        )
                    ]
                    if stage == "meaning" and _table_meaning_issues(fragment):
                        progress.update(
                            status="pending",
                            feedback=_meaning_feedback(candidate, fragment, inventory),
                        )
                    save("interpreting")
                    if progress["status"] == "pending":
                        continue
                    break
                except ModelError:
                    progress["status"] = "failed"
                    raise
                except (ValidationError, CompileError, ValueError, TypeError, KeyError) as exc:
                    # No values or arbitrary model member names in persisted feedback.
                    feedback = (
                        str(exc) if isinstance(exc, CompileError) else "invalid_table_contract"
                    )
                    repair = (
                        _meaning_feedback(accepted[rid], compiled[rid], inventory, [feedback])
                        if stage == "meaning" and progress.get("acceptedResponse")
                        else [feedback]
                    )
                    progress.update(status="failed", feedback=repair)
                    issue("table_stage_invalid", regionId=rid, tableStage=stage, errors=[feedback])
                    save("interpreting")
            if progress["status"] != "complete":
                return True
        return True

    for region in regions:
        rid = region["id"]
        if (
            rid in compiled
            and not local_issues(compiled[rid])
            and (
                not region.get("tableRef")
                or table_states.get(rid, {}).get("kind") == "scalar_form"
                or table_states.get(rid, {}).get("meaning", {}).get("status") == "complete"
            )
        ):
            continue
        if not region["withinContextBudget"]:
            issue(
                "region_context_budget_exceeded",
                regionId=rid,
                reason=region.get("budgetReason", "region_exceeds_budget"),
            )
            continue
        if not region["nodeIds"]:
            issue("region_has_no_observed_content", regionId=rid)
            continue
        payload = region_payload(observation, region)
        payload.update(intent=selected.intent, targetHandles=catalog)
        candidate_schema = region_output_schema(observation, region, catalog)
        if region.get("tableRef"):
            table = observation.tables[region["tableRef"]]
            original_table = table.get("sourceTableRef", region["tableRef"])
            for prior_ir in accepted.values():
                if prior_ir.regionId == rid:
                    continue
                prior = next(
                    (
                        r
                        for r in prior_ir.repeats
                        if observation.tables[r.tableRef].get("sourceTableRef", r.tableRef)
                        == original_table
                    ),
                    None,
                )
                if prior is not None:
                    payload["sameTableMapping"] = {
                        "key": prior.key,
                        "label": prior.label,
                        "columns": [c.model_dump() for c in prior.columns],
                        "instruction": (
                            "Reuse these field keys/types for the same observed columns unless "
                            "this region explicitly changes their meaning; report such changes, "
                            "do not silently alter the mapping."
                        ),
                    }
                    break
        if region.get("tableRef"):
            try:
                if interpret_table(region, payload):
                    continue
                payload["tableKind"] = "scalar_form"
                candidate_schema["properties"]["repeats"] = {
                    "type": "array",
                    "maxItems": 0,
                    "items": {},
                }
                candidate_schema = _compact_contract(candidate_schema)
            except ModelError as exc:
                issue(exc.code, regionId=rid)
                save("paused")
                return result
        feedback = (
            [i["code"] for i in compiled[rid].issues[:20]]
            if rid in compiled
            else repair_diagnostics.get(rid, [])
        )
        last_response = (
            hashlib.sha256(encode(accepted[rid].model_dump()).encode()).hexdigest()
            if rid in accepted
            else None
        )
        # Local repair only; unchanged responses and previously exhausted failures do not loop.
        for attempt in range(2):
            try:
                value = invoke(SYSTEM, payload, candidate_schema, feedback)
                response_hash = hashlib.sha256(encode(value).encode()).hexdigest()
                if response_hash == last_response or response_hash == failures.get(rid):
                    issue("region_repair_no_progress", regionId=rid)
                    break
                last_response = response_hash
                candidate = RegionInterpretation.model_validate(value)
                if candidate.tableMeaningState is not None or any(
                    m.sourceRanges for m in candidate.meanings
                ):
                    raise CompileError("scalar_response_cannot_set_table_review_metadata")
                if (
                    payload.get("tableKind") in {"scalar_form", "nonrecord_values"}
                    and candidate.repeats
                ):
                    raise CompileError("scalar_form_cannot_regenerate_records")
                fragment = compile_region(
                    candidate, observation, region, target_schema=selected.targetSchema
                )
                # Never discard committed content in exchange for a smaller-looking partial answer.
                previous = compiled.get(rid)
                # Values read from declared header cells are flagged misuse; a
                # repair that stops reading them does not lose committed content.
                regresses = previous is not None and (
                    not previous.consumed_bindings - previous.header_value_bindings
                    <= fragment.consumed_bindings
                    or len(local_issues(fragment)) >= len(local_issues(previous))
                )
                if regresses:
                    issue("region_repair_no_progress", regionId=rid)
                    break
                accepted[rid], compiled[rid] = candidate, fragment
                failures.pop(rid, None)
                repair_diagnostics.pop(rid, None)
                issues[:] = [
                    item
                    for item in issues
                    if not (
                        item.get("code") == "region_interpretation_invalid"
                        and item.get("regionId") == rid
                    )
                ]
                save("interpreting")
                if not local_issues(fragment) or attempt == 1:
                    break
                feedback = [_feedback_code(i) for i in fragment.issues[:20]]
                continue
            except ModelError as exc:
                issue(exc.code, regionId=rid)
                save("paused")
                return result
            except ValidationError as exc:
                member_names = set(candidate_schema["properties"])
                for definition in candidate_schema.get("$defs", {}).values():
                    member_names.update(definition.get("properties", {}))
                feedback = [
                    "invalid_internal_contract:"
                    + "/".join(
                        str(part) if type(part) is int or part in member_names else "unknown_member"
                        for part in e["loc"]
                    )
                    for e in exc.errors(include_input=False)[:12]
                ]
            except CompileError as exc:
                feedback = [str(exc)]
            except (ValueError, TypeError, KeyError):
                feedback = ["invalid_model_json"]
            # Record safe diagnostics before another call can exhaust a budget
            # or fail. Never leave an invalid first response indistinguishable
            # from an unexplained empty result; do not log its source or values.
            issue("region_interpretation_invalid", regionId=rid, errors=feedback)
            repair_diagnostics[rid] = feedback
            failures[rid] = last_response
            save("interpreting")
    pending = [
        c
        for c in candidates
        if not c["confirmed"]
        and c["id"] not in decisions
        and c["leftRegion"] in compiled
        and c["rightRegion"] in compiled
    ]
    batches, batch = [], []
    integration_contract = DocumentIntegration.model_json_schema()
    input_limit = min(
        selected.contextChars, getattr(client, "input_budget_chars", selected.contextChars)
    )

    def integration_payload(items):
        refs = list(dict.fromkeys(r for c in items for r in c["sourceRefs"]))
        return {
            "candidates": items,
            "sourceNodes": {r: model_node(observation.nodes[r]) for r in refs},
        }

    for candidate in pending:
        trial = [*batch, candidate]
        size = len(INTEGRATE) + len(
            encode({**integration_payload(trial), "outputContract": integration_contract})
        )
        if batch and (len(trial) > 8 or size > input_limit):
            batches.append(batch)
            batch = [candidate]
        else:
            batch = trial
    if batch:
        batches.append(batch)
    for batch in batches:
        refs = {r for c in batch for r in c["sourceRefs"]}
        payload = integration_payload(batch)
        try:
            integrated = DocumentIntegration.model_validate(
                invoke(INTEGRATE, payload, integration_contract)
            )
            ids = [c.candidateId for c in integrated.continuations]
            if set(ids) != {c["id"] for c in batch} or len(ids) != len(set(ids)):
                raise ValueError
            for item in integrated.continuations:
                if not set(item.sourceRefs) <= refs:
                    raise ValueError
                decisions[item.candidateId] = item.decision
            save("integrating")
        except ModelError as exc:
            issue(exc.code)
            if exc.code == "region_context_budget_exceeded":
                continue
            break
        except (ValueError, TypeError):
            issue("document_integration_invalid")
    linked, _, _, tasks = linked_regions()
    pending_tasks = [
        task
        for task in tasks
        if scope_decisions.get(task.id, {}).get("fingerprint") != task.fingerprint
        or (
            scope_decisions.get(task.id, {}).get("invalid") is True
            and additional_budget is not None
            and grant["maxModelCalls"] > 0
        )
    ]
    for batch in scope_batches(
        pending_tasks,
        context_chars=min(
            selected.contextChars, getattr(client, "input_budget_chars", selected.contextChars)
        ),
    ):
        try:
            response = invoke(SCOPE_SYSTEM, scope_batch_payload(batch), scope_output_schema(batch))
            choices, invalid = parse_scope_choices(response, batch)
            if invalid:
                issue("scope_batch_invalid_decision")
        except ModelError as exc:
            issue(exc.code, taskIds=[t.id for t in batch])
            break
        except (ValueError, TypeError):
            choices = []
        for task in batch:
            try:
                decision = next(c for c in choices if c.taskId == task.id)
                apply_scope_decision(linked, task, decision)
                scope_decisions[task.id] = {
                    "fingerprint": task.fingerprint,
                    "decision": decision.model_dump(),
                }
            except (StopIteration, ValueError, TypeError):
                scope_decisions[task.id] = {"fingerprint": task.fingerprint, "invalid": True}
                issue("scope_decision_invalid", taskId=task.id)
        save("integrating")
    save("finished")
    return result
