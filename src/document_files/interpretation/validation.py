"""Check schema, value and source-reference closure without fetching schema resources."""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry

from .contracts import Proposal


def pointer(value: Any, path: str) -> Any:
    if path == "":
        return value
    if not path.startswith("/") or re.search(r"~(?![01])", path):
        raise ValueError("invalid JSON pointer")
    for token in path[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise ValueError("invalid array index")
            value = value[int(token)]
        else:
            value = value[token]
    return value


def escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def leaves(value: Any, path: str = "") -> set[str]:
    if isinstance(value, dict) and value:
        return set().union(*(leaves(v, f"{path}/{escape(k)}") for k, v in value.items()))
    if isinstance(value, list) and value:
        return set().union(*(leaves(v, f"{path}/{i}") for i, v in enumerate(value)))
    return {path}


def check_schema(schema: dict[str, Any]) -> None:
    """No external resolution or executable regex; local acyclic references only."""

    visits = 0

    def walk(value, ancestors=(), depth=0):
        nonlocal visits
        visits += 1
        if visits > 10000 or depth > 100:
            raise ValueError("schema complexity budget exceeded")
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$id", "$dynamicRef", "$recursiveRef", "pattern", "patternProperties"}:
                    raise ValueError(f"unsupported schema keyword: {key}")
                if key == "$ref":
                    if not isinstance(item, str) or not item.startswith("#/"):
                        raise ValueError("only local JSON pointer schema references are supported")
                    if item in ancestors:
                        raise ValueError("recursive schema reference")
                    walk(pointer(schema, item[1:]), (*ancestors, item), depth + 1)
                else:
                    walk(item, ancestors, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, ancestors, depth + 1)

    walk(schema)
    Draft202012Validator.check_schema(schema)


def schema_definitions(schema: dict) -> set[str]:
    """Every declared field, including optional/nested/definition fields, needs provenance."""
    paths = set()

    def walk(value, path=""):
        if not isinstance(value, dict):
            return
        for key, child in value.items():
            child_path = f"{path}/{escape(key)}"
            if key in {"properties", "$defs", "dependentSchemas"} and isinstance(child, dict):
                for name, definition in child.items():
                    definition_path = f"{child_path}/{escape(name)}"
                    if key == "properties":
                        paths.add(definition_path)
                    walk(definition, definition_path)
            elif key in {"items", "contains", "additionalProperties", "not", "if", "then", "else"}:
                walk(child, child_path)
            elif key in {"allOf", "anyOf", "oneOf", "prefixItems"} and isinstance(child, list):
                for index, definition in enumerate(child):
                    walk(definition, f"{child_path}/{index}")

    walk(schema)
    return paths or {""}


def validate(proposal: Proposal, nodes: dict, seen: set[str], target_schema=None) -> list[str]:
    errors: list[str] = []
    roots = {"data": proposal.data, "dataSchema": proposal.dataSchema, "document": nodes}
    for name, schema, instance in (
        ("dataSchema", proposal.dataSchema, proposal.data),
        ("documentSchema", proposal.documentSchema, nodes),
    ):
        try:
            check_schema(schema)
            validator = Draft202012Validator(schema, registry=Registry())
            for error in validator.iter_errors(instance):
                errors.append(f"{name}: invalid instance at /{'/'.join(map(str, error.path))}")
                if len(errors) >= 30:
                    break
        except Exception as exc:
            errors.append(f"{name}: invalid or unsupported schema ({type(exc).__name__})")
    if target_schema is not None and proposal.dataSchema != target_schema:
        errors.append("dataSchema differs from caller targetSchema")
    ids = [a.id for a in proposal.semantics]
    if len(ids) != len(set(ids)):
        errors.append("duplicate semantic IDs")
    semantic_ids = set(ids)
    for assertion in proposal.semantics:
        if not set(assertion.sourceRefs) <= seen:
            errors.append(f"semantic {assertion.id}: unknown or unread sourceRefs")
        for target in [*assertion.targets, *assertion.scope]:
            try:
                pointer(roots[target.space], target.path)
            except (ValueError, KeyError, IndexError, TypeError):
                errors.append(f"semantic {assertion.id}: unresolved target or scope")
    covered = set()
    schema_covered = set()
    for category, evidence_list in (
        ("value", proposal.valueEvidence),
        ("schema", proposal.schemaEvidence),
    ):
        for evidence in evidence_list:
            if evidence.target.space != ("data" if category == "value" else "dataSchema"):
                errors.append(f"{category} evidence has wrong target space")
            try:
                pointer(roots[evidence.target.space], evidence.target.path)
            except (ValueError, KeyError, IndexError, TypeError):
                errors.append(f"{category} evidence: unresolved target")
            if not set(evidence.sourceRefs) <= seen:
                errors.append(f"{category} evidence: unknown or unread sourceRefs")
            if not set(evidence.semanticIds) <= semantic_ids:
                errors.append(f"{category} evidence: unknown semantic IDs")
            if category == "value":
                if evidence.target.path in covered:
                    errors.append("duplicate value evidence target")
                covered.add(evidence.target.path)
                if evidence.binding is not None:
                    from .bindings import resolve

                    try:
                        actual, raw = resolve(evidence.binding, nodes)
                        if (
                            evidence.binding.sourceRef not in evidence.sourceRefs
                            or evidence.binding.sourceRef not in seen
                            or evidence.raw != raw
                            or pointer(proposal.data, evidence.target.path) != actual
                        ):
                            errors.append("value evidence: binding does not match value or source")
                    except (ValueError, KeyError, IndexError, TypeError, OverflowError):
                        errors.append("value evidence: invalid source binding")
                if (
                    evidence.status == "present"
                    and evidence.raw
                    and evidence.binding is None
                    and not any(
                        evidence.raw in nodes.get(ref, {}).get("text", "")
                        for ref in evidence.sourceRefs
                    )
                ):
                    errors.append("value evidence: raw text not found in cited nodes")
            else:
                schema_covered.add(evidence.target.path)
    if leaves(proposal.data) - covered:
        errors.append("data leaves missing valueEvidence")
    missing_definitions = schema_definitions(proposal.dataSchema) - schema_covered
    if missing_definitions:
        errors.append(
            "schema definitions missing schemaEvidence: "
            + ", ".join(sorted(missing_definitions)[:20])
        )
    accounting = [a.sourceRef for a in proposal.accounting]
    if len(accounting) != len(set(accounting)) or set(accounting) != set(nodes):
        errors.append("accounting must cover every source node exactly once")
    if set(nodes) - seen:
        errors.append("source nodes have not all been read")
    for account in proposal.accounting:
        if not set(account.semanticIds) <= semantic_ids:
            errors.append("accounting refers to unknown semantics")
        if account.disposition == "represented" and not account.semanticIds:
            errors.append("represented node must link to semantics")
    return list(dict.fromkeys(errors))[:30]
