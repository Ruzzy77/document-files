"""Strict internal proposals; dynamic document schemas remain standard JSON Schema."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RESULT_VERSION = "document-files.schema-extraction-result.v1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Target(Contract):
    space: Literal["data", "dataSchema", "document"]
    path: str  # RFC 6901 JSON pointer; document paths resolve against its nodes map.


class Assertion(Contract):
    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    description: str = Field(min_length=1)
    targets: list[Target] = Field(min_length=1)
    scope: list[Target] = Field(min_length=1)
    sourceRefs: list[str] = Field(min_length=1)
    basis: Literal["observed", "normalized", "ai_interpreted"]
    status: Literal["interpreted", "uncertain"]


class SourceBinding(Contract):
    """An exact native scalar or Unicode-code-point text range, never executable code."""

    sourceRef: str
    path: str = "/text"
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    representation: Literal["text", "native", "integer", "number", "boolean", "null"] = "text"


class Evidence(Contract):
    target: Target
    sourceRefs: list[str] = Field(min_length=1)
    semanticIds: list[str]
    raw: str
    status: Literal["present", "blank", "absent", "unreadable", "uncertain"]
    transformation: str
    binding: SourceBinding | None = None


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
