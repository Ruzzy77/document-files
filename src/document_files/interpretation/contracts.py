"""Strict internal proposals; dynamic document schemas remain standard JSON Schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..result_types import Assertion, Contract, Evidence
from ..result_types import SourceBinding as SourceBinding
from ..result_types import Target as Target

RESULT_VERSION = "document-files.schema-extraction-result.v1"


class NodeAccounting(Contract):
    sourceRef: str
    disposition: Literal["represented", "non_data", "unresolved"]
    explanation: str = Field(min_length=1)
    semanticIds: list[str]


class Proposal(Contract):
    documentSchema: dict[str, Any]
    dataSchema: dict[str, Any]
    data: Any
    semantics: list[Assertion]
    schemaEvidence: list[Evidence]
    valueEvidence: list[Evidence]
    accounting: list[NodeAccounting]
    issues: list[str]


class Step(Contract):
    action: Literal["read", "finish"]
    readIds: list[str] = Field(default_factory=list, max_length=100)
    proposal: Proposal | None = None


class ExtractionOptions(Contract):
    intent: str = Field(default="", max_length=10000)
    targetSchema: dict[str, Any] | None = None
    reconstructionContext: bool = True
    maxModelCalls: int = Field(default=12, ge=1, le=100)
    contextChars: int = Field(default=120000, ge=8000, le=1000000)
    completionSeconds: int = Field(default=300, ge=1, le=3600)
    maxInputBytes: int = Field(default=32 * 1024 * 1024, ge=1, le=128 * 1024 * 1024)


class Review(Contract):
    issues: list[str]
