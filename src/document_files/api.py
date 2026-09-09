"""Supported, host-independent Document Files Python API.

``extract_schema_from_stream`` takes AnalysisJob v1 plus a binary stream and has
no retained-storage side effects. The path adapter optionally retains private
results and checkpoints; use ``retain=False`` for an in-process pipeline.
"""

from .analysis import (
    AnalysisBudgets,
    AnalysisInput,
    AnalysisJob,
    AnalysisResult,
    AnalyzerBackend,
    LocalAnalyzerBackend,
    analyze_document,
)
from .diagnostics import diagnose
from .document_model.model import ObservationDocument
from .document_model.observe import observe_document
from .engine import DocumentFilesError, extract_structure_from_stream
from .interpretation.backends import (
    ChatCompletionsClient,
    InferenceModelClient,
    InferenceRequest,
    InferenceResponse,
    ManagedPackClient,
    ModelClient,
    ModelError,
)
from .interpretation.contracts import RESULT_VERSION, ExtractionOptions
from .interpretation.engine import extract_schema_from_stream
from .interpretation.workflow import (
    delete_extraction,
    extract_schema,
    get_extraction,
    resume_extraction,
)
from .job_client import JobClient
from .jobs import JobService, JobStore, ModelProfile
from .result_types import Assertion, Evidence
from .runtime_packs import PackStore

__all__ = [
    "RESULT_VERSION",
    "AnalysisBudgets",
    "AnalysisInput",
    "AnalysisJob",
    "AnalysisResult",
    "AnalyzerBackend",
    "ChatCompletionsClient",
    "DocumentFilesError",
    "ExtractionOptions",
    "LocalAnalyzerBackend",
    "ModelClient",
    "ManagedPackClient",
    "InferenceRequest",
    "InferenceResponse",
    "InferenceModelClient",
    "JobService",
    "JobStore",
    "JobClient",
    "ModelProfile",
    "PackStore",
    "ObservationDocument",
    "observe_document",
    "ModelError",
    "analyze_document",
    "delete_extraction",
    "diagnose",
    "extract_schema",
    "extract_schema_from_stream",
    "extract_structure_from_stream",
    "get_extraction",
    "resume_extraction",
]


def extraction_result_schema() -> dict:
    """Return the additive v1 result schema (not the model proposal schema).

    Native source/coverage/provenance objects remain extensible. Schema and data
    are null when no validated candidate is available; partial is not success.
    """
    from pydantic import BaseModel

    class PublicEvidence(BaseModel):
        semantics: list[Assertion]
        schemaEvidence: list[Evidence]
        valueEvidence: list[Evidence]

    proposal = PublicEvidence.model_json_schema()
    properties = {
        "schemaVersion": {"const": RESULT_VERSION},
        "jobId": {"type": "string"},
        "source": {"type": "object"},
        "document": {
            "type": "object",
            "required": ["nodes"],
            "properties": {"nodes": {"type": "object"}},
        },
        "documentSchema": {"type": ["object", "null"]},
        "dataSchema": {"type": ["object", "null"]},
        "dataSchemaRevision": {"type": ["string", "null"]},
        "data": {},
        "extraction": {
            "type": "object",
            "required": ["status", "modelCalls"],
            "properties": {
                "status": {"enum": ["partial", "complete"]},
                "modelCalls": {"type": "integer", "minimum": 0},
            },
        },
        "coverage": {"type": "object"},
        "validation": {"type": "object"},
        "issues": {"type": "array", "items": {"type": "object"}},
        "provenance": {"type": "object"},
    }
    for name in ("semantics", "schemaEvidence", "valueEvidence"):
        properties[name] = proposal["properties"][name]
    required = list(properties)
    properties.update(
        {
            "resultRevision": {"type": "integer", "minimum": 0},
            "extractionStatus": {"enum": ["partial", "complete", None]},
            "semanticDetails": {"type": "array", "items": {"type": "object"}},
            "valueObservations": {"type": "array", "items": {"type": "object"}},
        }
    )
    properties["document"]["properties"].update(
        {
            "observationVersion": {"type": "string"},
            "bindings": {"type": "object"},
            "structure": {
                "type": "object",
                "properties": {
                    "schemaVersion": {"type": "string"},
                    "regions": {"type": "array"},
                    "tables": {"type": "object"},
                    "relations": {"type": "array"},
                },
            },
            "semanticRelations": {"type": "array"},
        }
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": RESULT_VERSION,
        "type": "object",
        "properties": properties,
        "required": required,
        "$defs": proposal.get("$defs", {}),
    }


__all__.append("extraction_result_schema")
