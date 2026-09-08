"""Resolve explicit values from observed source, with no model-authored value rewriting."""

from __future__ import annotations

import copy
import math
import re
from decimal import Decimal, InvalidOperation

from .contracts import Proposal, SourceBinding
from .validation import leaves, pointer


def _assign(root, path, value):
    if not path:
        return value
    parent, _, key = path.rpartition("/")
    container = pointer(root, parent)
    key = key.replace("~1", "/").replace("~0", "~")
    if isinstance(container, list):
        container[int(key)] = value
    else:
        container[key] = value
    return root


def resolve(binding: SourceBinding, nodes: dict):
    # Values may come from text or native typed-value observations, not invented metadata.
    if binding.path != "/text" and not binding.path.startswith("/semantic/value/"):
        raise ValueError("binding path must address source text or native typed value")
    source = pointer(nodes[binding.sourceRef], binding.path)
    if (binding.start is None) != (binding.end is None):
        raise ValueError("binding range requires both start and end")
    if binding.start is not None:
        if not isinstance(source, str) or not binding.start <= binding.end <= len(source):
            raise ValueError("binding range is outside source text")
        source = source[binding.start : binding.end]
    if binding.path.endswith("/value") and isinstance(source, (int, float)):
        parent = pointer(nodes[binding.sourceRef], binding.path.rpartition("/")[0])
        if (
            parent.get("rawType") == "n"
            and "raw" in parent
            and Decimal(str(source)) != Decimal(parent["raw"])
        ):
            raise ValueError("native number lost precision; bind the exact raw scalar")
    if source is None:
        parent = pointer(nodes[binding.sourceRef], binding.path.rpartition("/")[0])
        if not (
            binding.path.endswith("/value")
            and isinstance(parent, dict)
            and parent.get("kind") == "null"
        ):
            raise ValueError("native null binding requires an explicitly null typed value")
        raw = "null"
    elif isinstance(source, (dict, list)):
        raise ValueError("binding must address an explicit scalar")
    else:
        raw = source if isinstance(source, str) else str(source).lower()
    if binding.representation == "null":
        if raw != "null":
            raise ValueError("null binding requires a literal or explicitly typed null")
        return None, raw
    if binding.representation == "native":
        return source, raw
    if binding.representation == "text":
        return raw, raw
    if binding.representation == "boolean":
        if raw not in {"true", "false"}:
            raise ValueError("boolean binding needs literal true or false")
        return raw == "true", raw
    if binding.representation == "integer":
        if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", raw):
            raise ValueError("integer binding needs an exact integer literal")
        return int(raw), raw
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", raw):
        raise ValueError("number binding needs a JSON number literal")
    number = float(raw)
    if not math.isfinite(number) or Decimal(str(number)) != Decimal(raw):
        raise ValueError("number loses precision; use a decimal string")
    return number, raw


def materialize(proposal: Proposal, nodes: dict, seen: set[str]) -> tuple[Proposal, list[str]]:
    """Legacy exact raw evidence remains accepted; its source range is made explicit.

    Unknown and missing values remain null (blank may be an empty string); they never become
    invented defaults. Bindings select values; data provides the intended output shape/type.
    """
    result = proposal.model_copy(deep=True)
    errors = []
    paths = leaves(result.data)
    targets = set()
    for evidence in result.valueEvidence:
        path = evidence.target.path
        try:
            if evidence.target.space != "data" or path not in paths or path in targets:
                raise ValueError("value evidence must uniquely address a data leaf")
            targets.add(path)
            expected = pointer(result.data, path)
            if evidence.status != "present":
                if evidence.binding is not None:
                    raise ValueError("non-present evidence cannot bind an explicit value")
                if expected is not None and not (evidence.status == "blank" and expected == ""):
                    raise ValueError("missing or uncertain values must not contain invented data")
                continue
            binding = evidence.binding
            if binding is None:
                # Compatibility with v1: resolve an exact raw substring, never copy model data.
                if not evidence.raw:
                    raise ValueError("present value requires nonempty source evidence")
                matches = []
                for ref in evidence.sourceRefs:
                    text = nodes.get(ref, {}).get("text", "")
                    start = text.find(evidence.raw)
                    if start >= 0:
                        matches.append((ref, start))
                if not matches:
                    raise ValueError("raw text not found in cited nodes")
                if len(matches) != 1 or nodes[matches[0][0]]["text"].count(evidence.raw) != 1:
                    raise ValueError("raw text is ambiguous; provide an explicit source binding")
                ref, start = matches[0]
                representation = "text"
                if isinstance(expected, bool):
                    representation = "boolean"
                elif isinstance(expected, int):
                    representation = "integer"
                elif isinstance(expected, float):
                    representation = "number"
                elif expected is None:
                    representation = "null"
                elif not isinstance(expected, str):
                    raise ValueError("present value requires a scalar output type")
                binding = SourceBinding(
                    sourceRef=ref,
                    start=start,
                    end=start + len(evidence.raw),
                    representation=representation,
                )
            if binding.sourceRef not in seen or binding.sourceRef not in evidence.sourceRefs:
                raise ValueError("binding must cite a read source node")
            value, raw = resolve(binding, nodes)
            if evidence.raw != raw:
                raise ValueError("binding and raw evidence disagree")
            result.data = _assign(result.data, path, copy.deepcopy(value))
            evidence.binding = binding
        except (
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            OverflowError,
            InvalidOperation,
        ) as exc:
            detail = (
                str(exc) if isinstance(exc, ValueError) else "invalid source reference or scalar"
            )
            errors.append(f"value binding {path}: {detail}")
    return result, list(dict.fromkeys(errors))[:30]
