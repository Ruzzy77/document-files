"""Small internal decisions, not public results or model-authored document values."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from ..result_types import Contract
from .document_outline import DocumentElement, constrain_schema

SEMANTIC_VERSION = "document-files.semantic-ir.v1"
COMPILER_VERSION = "document-files.result-compiler.v30"
ValueType = Literal["string", "decimal", "integer", "number", "boolean", "null", "native"]
Presence = Literal["present", "blank", "absent", "unreadable", "uncertain"]


class FieldDefinition(Contract):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    valueType: ValueType = "string"
    definitionRefs: list[str] = Field(min_length=1, max_length=50)
    targetHandle: str | None = None


class SourceQuote(Contract):
    sourceRef: str
    text: str = Field(min_length=1, max_length=16000)
    occurrence: int = Field(default=0, ge=0)


class FieldLink(FieldDefinition):
    bindingId: str | None = None
    sourceQuote: SourceQuote | None = None
    status: Presence = "present"
    groupId: str | None = None


class Group(Contract):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    sourceRefs: list[str] = Field(min_length=1, max_length=50)
    parentId: str | None = None
    targetHandle: str | None = None


class ColumnLink(FieldDefinition):
    column: int = Field(ge=0)
    bindingMode: Literal["source", "text", "formula", "cached"] = "source"


class RowRole(Contract):
    row: int = Field(ge=0)
    role: Literal["header", "data", "subtotal", "note", "blank", "unresolved"]
    sourceRefs: list[str] = Field(min_length=1)


class RepeatLink(Contract):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    tableRef: str
    rowStart: int = Field(ge=0)
    rowEnd: int = Field(ge=0, description="Inclusive last observed row, not an example count")
    columns: list[ColumnLink] = Field(min_length=1, max_length=200)
    rowRoles: list[RowRole] = Field(default_factory=list, max_length=1000)
    definitionRefs: list[str] = Field(min_length=1)
    groupId: str | None = None
    targetHandle: str | None = None


class LogicalValue(Contract):
    columnId: str = Field(min_length=1, max_length=120)
    bindingId: str | None = None
    sourceQuote: SourceQuote | None = None
    status: Presence
    sourceRefs: list[str] = Field(min_length=1, max_length=50)


class LogicalRow(Contract):
    id: str = Field(min_length=1, max_length=120)
    sourceQuotes: list[SourceQuote] = Field(min_length=1, max_length=50)
    values: list[LogicalValue] = Field(min_length=1, max_length=200)


class LogicalRecord(Contract):
    id: str = Field(min_length=1, max_length=120)
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=500)
    definitionRefs: list[str] = Field(min_length=1, max_length=50)
    columns: list[FieldDefinition] = Field(min_length=1, max_length=200)
    rows: list[LogicalRow] = Field(max_length=500)
    emptySourceQuotes: list[SourceQuote] = Field(default_factory=list, max_length=10)
    groupId: str | None = None
    targetHandle: str | None = None


class MeaningSourceRange(Contract):
    sourceRef: str
    path: Literal["/text"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1)
    textSHA256: str = Field(pattern="^[0-9a-f]{64}$")


class MeaningSourceReview(Contract):
    sourceRefs: list[str] = Field(min_length=1, max_length=1000)
    role: Literal["no_additional_meaning", "unresolved", "unreviewed"]
    explanation: str = Field(min_length=1, max_length=500)


class MeaningChange(Contract):
    previousIds: list[str] = Field(min_length=1, max_length=100)
    replacementIds: list[str] = Field(max_length=100)
    reviewSourceRefs: list[str] = Field(max_length=1000)
    reason: str = Field(min_length=1, max_length=500)


class TableMeaningState(Contract):
    version: Literal["document-files.table-meaning-review.v2"] = (
        "document-files.table-meaning-review.v2"
    )
    inventorySHA256: str = Field(pattern="^[0-9a-f]{64}$")
    revisionSHA256: str = Field(pattern="^[0-9a-f]{64}$")
    sourceReviews: list[MeaningSourceReview] = Field(default_factory=list, max_length=1000)
    baseRevision: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    changes: list[MeaningChange] = Field(default_factory=list, max_length=100)


class Meaning(Contract):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal["unit", "condition", "note", "definition", "reference", "relationship"]
    description: str = Field(min_length=1, max_length=2000)
    sourceRefs: list[str] = Field(min_length=1, max_length=100)
    # Program-resolved literal quotes in the table protocol, never model offsets.
    sourceRanges: list[MeaningSourceRange] = Field(default_factory=list, max_length=100)
    fieldIds: list[str] = Field(default_factory=list, max_length=200)
    groupIds: list[str] = Field(default_factory=list, max_length=100)
    repeatIds: list[str] = Field(default_factory=list, max_length=100)
    rowStart: int | None = Field(default=None, ge=0)
    rowEnd: int | None = Field(default=None, ge=0)
    status: Literal["interpreted", "uncertain"] = Field(
        default="interpreted",
        description="Certainty of the meaning's kind and content, independent of applicability",
    )


class Disposition(Contract):
    sourceRef: str
    role: Literal["data", "heading", "narrative", "note", "structural", "unresolved", "unsupported"]
    explanation: str = Field(min_length=1, max_length=500)


class BindingDisposition(Contract):
    bindingId: str
    role: Literal["label", "narrative", "structural", "unresolved"]
    explanation: str = Field(min_length=1, max_length=500)


class RegionInterpretation(Contract):
    """A region's references and meaning. There is deliberately no `data` field."""

    regionId: str
    fields: list[FieldLink] = Field(default_factory=list, max_length=500)
    groups: list[Group] = Field(default_factory=list, max_length=200)
    repeats: list[RepeatLink] = Field(default_factory=list, max_length=100)
    logicalRecords: list[LogicalRecord] = Field(default_factory=list, max_length=100)
    meanings: list[Meaning] = Field(default_factory=list, max_length=500)
    dispositions: list[Disposition] = Field(default_factory=list, max_length=5000)
    excludedBindings: list[BindingDisposition] = Field(default_factory=list, max_length=2000)
    unresolved: list[str] = Field(default_factory=list, max_length=100)
    tableMeaningState: TableMeaningState | None = None
    documentElements: list[DocumentElement] = Field(default_factory=list, max_length=5000)


def region_output_schema(observation, region, target_handles=None, *, compact=True):
    """Constrain model references to issued candidates, not unrestricted strings.

    This is a private per-request grammar contract. The compiler still validates
    every reference for clients whose server cannot enforce structured output.
    Component IDs remain local semantic decisions, not external JSON pointers.
    """
    schema = RegionInterpretation.model_json_schema()
    # These are compiler/checkpoint metadata, not the scalar interpretation wire.
    schema["properties"].pop("tableMeaningState")
    # Offered only by the native text-content protocol, not physical table stages.
    schema["properties"]["logicalRecords"]["maxItems"] = 0
    schema["$defs"]["FieldLink"]["properties"].pop("sourceQuote")
    schema["$defs"]["Meaning"]["properties"].pop("sourceRanges")
    # The typed IR can read historical compact decisions with defaults, while
    # new model responses must explicitly select a binding and presence state.
    schema["$defs"]["FieldLink"]["required"].extend(["bindingId", "status", "valueType"])
    schema["$defs"]["ColumnLink"]["required"].append("valueType")
    schema["$defs"]["Meaning"]["required"].extend(["fieldIds", "groupIds", "repeatIds", "status"])
    schema["properties"]["regionId"] = {"type": "string", "const": region["id"]}
    refs = list(dict.fromkeys([*region["nodeIds"], *region.get("contextNodeIds", [])]))
    bindings = region["bindingIds"]
    for definition in schema["$defs"].values():
        for name, value in definition.get("properties", {}).items():
            if name in {"sourceRefs", "definitionRefs"}:
                value["items"] = {"type": "string", "enum": refs}
            elif name == "sourceRef":
                value.update(type="string", enum=refs)
            elif name == "bindingId":
                choices = [{"type": "string", "enum": bindings}] if bindings else []
                if "anyOf" in value:
                    value["anyOf"] = [*choices, {"type": "null"}]
                elif bindings:
                    value.update(type="string", enum=bindings)
            elif name == "targetHandle":
                choices = (
                    [{"type": "string", "enum": list(target_handles)}] if target_handles else []
                )
                value["anyOf"] = [*choices, {"type": "null"}]
    table = observation.tables.get(region.get("tableRef"))
    if table:
        props = schema["$defs"]["RepeatLink"]["properties"]
        props["tableRef"] = {"type": "string", "const": region["tableRef"]}
        cells = table["cells"]
        low = min((c["row"] for c in cells), default=0)
        high = max((c["row"] + c.get("rowSpan", 1) - 1 for c in cells), default=0)
        for name in ("rowStart", "rowEnd"):
            props[name].update(minimum=low, maximum=high)
        schema["$defs"]["RowRole"]["properties"]["row"].update(minimum=low, maximum=high)
        max_col = max((c["col"] + c.get("colSpan", 1) - 1 for c in cells), default=0)
        schema["$defs"]["ColumnLink"]["properties"]["column"]["maximum"] = max_col
    else:
        schema["properties"]["repeats"]["maxItems"] = 0
    if not bindings:
        schema["properties"]["excludedBindings"]["maxItems"] = 0
    constrain_schema(schema, observation, region)
    return _compact_contract(schema) if compact else schema


def _compact_contract(schema):
    """Drop unreachable array branches and cosmetic titles, not accepted decisions.

    A maxItems=0 array can never contain an item. Keeping its whole item graph
    unnecessarily teaches the text-only interpreter about tables it cannot emit.
    The small contract is still sent in the prompt: a decoder grammar alone does
    not explain the output contract to the model.
    """

    def visit(value):
        if not isinstance(value, dict):
            return
        value.pop("title", None)
        if value.get("type") == "array" and value.get("maxItems") == 0:
            value["items"] = {"type": "null"}
        for key in ("properties", "$defs"):
            for child in value.get(key, {}).values():
                visit(child)
        for key in ("items", "additionalProperties", "not"):
            visit(value.get(key))
        for key in ("allOf", "anyOf", "oneOf"):
            for child in value.get(key, []):
                visit(child)

    visit(schema)
    definitions = schema.pop("$defs", {})
    reachable = set()

    def references(value):
        if isinstance(value, dict):
            ref = value.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.removeprefix("#/$defs/")
                if name not in reachable:
                    reachable.add(name)
                    references(definitions[name])
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references(schema)
    if reachable:
        schema["$defs"] = {name: body for name, body in definitions.items() if name in reachable}
    _share_repeated_constraints(schema)
    return schema


def _share_repeated_constraints(schema):
    """Factor identical schema subtrees only when the serialized contract shrinks.

    Keep standard JSON Schema in both prompt and decoder, not a second shorthand
    language. Visit schema positions only: enum/default document data is never
    rewritten. Local references remain references to the same root definitions.
    """

    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def walk(value):
        if not isinstance(value, dict):
            return
        yield value
        for key in ("properties", "$defs"):
            for child in value.get(key, {}).values():
                yield from walk(child)
        for key in ("items", "additionalProperties", "not"):
            yield from walk(value.get(key))
        for key in ("allOf", "anyOf", "oneOf"):
            for child in value.get(key, []):
                yield from walk(child)

    while True:
        candidates = {}
        for node in walk(schema):
            # Only product-generated validation keywords; do not relocate scopes
            # containing local IDs, anchors, nested definitions or unknown dialects.
            allowed = {
                "type",
                "enum",
                "const",
                "anyOf",
                "items",
                "minimum",
                "maximum",
                "$ref",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
                "default",
                "description",
            }
            if not node or any(not set(part) <= allowed for part in walk(node)):
                continue
            candidates.setdefault(encoded(node), []).append(node)
        shared = schema.get("$defs", {})
        name = f"Constraint{len(shared)}"
        while name in shared:
            name += "_"
        reference = {"$ref": f"#/$defs/{name}"}
        # Include definition key/comma and the possible new $defs container.
        overhead = len(encoded(name)) + 2 + (10 if "$defs" not in schema else 0)
        ranked = [
            (
                (len(nodes) - 1) * len(key) - len(nodes) * len(encoded(reference)) - overhead,
                key,
                nodes,
            )
            for key, nodes in candidates.items()
            if len(nodes) > 1
        ]
        if not ranked:
            return
        savings, key, nodes = max(ranked, key=lambda item: (item[0], item[1]))
        if savings < 32:
            return
        schema.setdefault("$defs", {})[name] = json.loads(key)
        for node in nodes:
            node.clear()
            node.update(reference)


class ContinuationDecision(Contract):
    candidateId: str
    decision: Literal["continue", "duplicate", "separate", "unresolved"]
    sourceRefs: list[str] = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=1000)


class DocumentIntegration(Contract):
    continuations: list[ContinuationDecision] = Field(default_factory=list, max_length=100)
