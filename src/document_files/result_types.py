"""Public v1 evidence contracts, independent of the model's private protocol."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
