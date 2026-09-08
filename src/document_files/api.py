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
from .engine import DocumentFilesError, extract_structure_from_stream
from .interpretation.backends import ChatCompletionsClient, ModelClient, ModelError
from .interpretation.contracts import RESULT_VERSION, ExtractionOptions
from .interpretation.contracts import Proposal as _Proposal
from .interpretation.engine import extract_schema_from_stream
from .interpretation.workflow import (
    delete_extraction,
    extract_schema,
    get_extraction,
    resume_extraction,
)

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
    proposal = _Proposal.model_json_schema()
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
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": RESULT_VERSION,
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "$defs": proposal.get("$defs", {}),
    }


__all__.append("extraction_result_schema")
