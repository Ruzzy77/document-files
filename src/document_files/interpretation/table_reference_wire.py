"""Reversible model-only source/table handles; literal text and public IDs stay intact."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field

VERSION = "document-files.table-reference-wire.v2"
_SOURCE_ONE = {"sourceRef"}
_SOURCE_MANY = {"sourceRefs", "definitionRefs", "headerRefs", "reviewSourceRefs"}
_TABLE_ONE = {"tableRef", "sourceTableRef"}
_SCHEMA_CHILDREN = {
    "items",
    "additionalProperties",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
}
_SCHEMA_LISTS = {"allOf", "anyOf", "oneOf", "prefixItems"}


class TableReferenceWireError(ValueError):
    pass


def _require(value, code="table_reference_wire_invalid"):
    if not value:
        raise TableReferenceWireError(code)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _copy(value):
    try:
        result = deepcopy(value)
        _encoded(result)
        return result
    except (TypeError, ValueError, RecursionError):
        raise TableReferenceWireError("table_reference_wire_invalid_json") from None


def _refs(value):
    _require(isinstance(value, list) and all(isinstance(v, str) and v for v in value))
    return value


def _dictionary(payload):
    sources = sorted(
        set(_refs(payload.get("nodeIds", [])) + _refs(payload.get("contextNodeIds", [])))
    )
    tables = payload.get("tables", {})
    _require(isinstance(tables, dict) and all(isinstance(k, str) and k for k in tables))
    tables = sorted(tables)
    original = set(sources) | set(tables)
    prefix = "@"
    while any(
        f"{prefix}{kind}{i}" in original
        for kind, refs in (("s", sources), ("t", tables))
        for i in range(len(refs))
    ):
        prefix += "@"
    return {
        "sources": {f"{prefix}s{i}": ref for i, ref in enumerate(sources)},
        "tables": {f"{prefix}t{i}": ref for i, ref in enumerate(tables)},
    }


def _identity(dictionary):
    return {
        "version": VERSION,
        "dictionary": dictionary,
        "dictionarySha256": hashlib.sha256(
            json.dumps(
                dictionary, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest(),
    }


def validate_meaning_wire_identity(payload, identity):
    """Validate saved identity from current refs without reselecting active mode.

    None represents an inactive codec; required checkpoint-field presence is the
    caller's responsibility. Only nodeIds/contextNodeIds/tables are needed here.
    """
    if identity is None:
        return
    payload, identity = _copy(payload), _copy(identity)
    _require(isinstance(payload, dict) and isinstance(identity, dict))
    _require(identity == _identity(_dictionary(payload)), "table_reference_wire_identity_mismatch")


class _Translator:
    def __init__(self, dictionary, *, decoding=False):
        self.decoding = decoding
        self.dictionary = dictionary
        self.maps = (
            dictionary
            if decoding
            else {role: {v: k for k, v in values.items()} for role, values in dictionary.items()}
        )
        self.aliases = set(dictionary["sources"]) | set(dictionary["tables"])

    def ref(self, value, role="sources", *, schema=False):
        _require(isinstance(value, str), "table_reference_wire_reference_shape")
        if value in self.maps[role]:
            return self.maps[role][value]
        if self.decoding or value in self.aliases or schema:
            raise TableReferenceWireError("table_reference_wire_unknown_or_conflicting_reference")
        # Existing non-offered provenance refs are preserved, not silently added
        # to the stable dictionary. They cannot become new response quote choices.
        return value

    def refs(self, values, role="sources"):
        return [self.ref(v, role) for v in _refs(values)]

    def direct(
        self, item, *, source_one=_SOURCE_ONE, source_many=_SOURCE_MANY, table_one=_TABLE_ONE
    ):
        _require(isinstance(item, dict), "table_reference_wire_reference_shape")
        result = deepcopy(item)
        for key in source_one:
            if key in result:
                result[key] = self.ref(result[key])
        for key in source_many:
            if key in result:
                result[key] = self.refs(result[key])
        for key in table_one:
            if key in result and result[key] is not None:
                result[key] = self.ref(result[key], "tables")
        return result

    def items(self, value, function):
        _require(isinstance(value, list), "table_reference_wire_reference_shape")
        return [function(item) for item in value]

    def source_decisions(self, value):
        _require(isinstance(value, dict), "table_reference_wire_response_shape")
        result = {}
        for ref, decision in value.items():
            alias = self.ref(ref, schema=True)
            _require(alias not in result, "table_reference_wire_reference_collision")
            _require(isinstance(decision, dict), "table_reference_wire_response_shape")
            out = deepcopy(decision)
            if "meanings" in out:

                def meaning(item):
                    _require(isinstance(item, dict), "table_reference_wire_response_shape")
                    item = deepcopy(item)
                    if "additionalQuotes" in item:
                        item["additionalQuotes"] = self.items(
                            item["additionalQuotes"],
                            lambda q: self.direct(q, source_many=set(), table_one=set()),
                        )
                    # Own quotes have no reference slots. Their text is never rewritten.
                    return item

                out["meanings"] = self.items(out["meanings"], meaning)
            result[alias] = out
        return result

    def response(self, value):
        _require(isinstance(value, dict), "table_reference_wire_response_shape")
        result = deepcopy(value)
        if "sourceDecisions" in result:
            result["sourceDecisions"] = self.source_decisions(result["sourceDecisions"])
        if "meanings" in result:

            def meaning(item):
                _require(isinstance(item, dict), "table_reference_wire_response_shape")
                out = deepcopy(item)
                if "sourceQuotes" in out:
                    out["sourceQuotes"] = self.items(
                        out["sourceQuotes"],
                        lambda q: self.direct(q, source_many=set(), table_one=set()),
                    )
                return out

            result["meanings"] = self.items(result["meanings"], meaning)
        for key, many in (("sourceReviews", {"sourceRefs"}), ("changes", {"reviewSourceRefs"})):
            if key in result:
                result[key] = self.items(
                    result[key],
                    lambda item, many=many: self.direct(
                        item, source_one=set(), source_many=many, table_one=set()
                    ),
                )
        if "dispositions" in result:
            result["dispositions"] = self.items(
                result["dispositions"],
                lambda item: self.direct(item, source_many=set(), table_one=set()),
            )
        return result

    def feedback(self, value):
        if value is None:
            return None
        if isinstance(value, list):
            _require(
                all(isinstance(item, str) for item in value), "table_reference_wire_feedback_shape"
            )
            return list(value)
        _require(isinstance(value, dict), "table_reference_wire_feedback_shape")
        result = deepcopy(value)
        if "acceptedResponse" in result:
            result["acceptedResponse"] = self.response(result["acceptedResponse"])
        if "remainingSourceRanges" in result:
            result["remainingSourceRanges"] = self.items(
                result["remainingSourceRanges"], self.direct
            )
        # Human-readable/composite issue strings are not parsed as reference slots.
        if isinstance(result.get("issues"), list):
            result["issues"] = [
                self.direct(i) if isinstance(i, dict) else i for i in result["issues"]
            ]
        return result

    def cell_collection(self, value):
        if isinstance(value, list):
            return self.items(value, self.direct)
        _require(
            isinstance(value, dict) and value.get("encoding") == "columns-rows.v1",
            "table_reference_wire_columnar_shape",
        )
        columns, rows = value.get("columns"), value.get("rows")
        _require(
            isinstance(columns, list)
            and all(isinstance(k, str) for k in columns)
            and len(columns) == len(set(columns))
            and isinstance(rows, list),
            "table_reference_wire_columnar_shape",
        )
        out = deepcopy(value)
        for row in out["rows"]:
            _require(
                isinstance(row, list) and len(row) == len(columns),
                "table_reference_wire_columnar_shape",
            )
            for index, name in enumerate(columns):
                if name == "sourceRef":
                    row[index] = self.ref(row[index])
                elif name == "sourceRefs":
                    row[index] = self.refs(row[index])
        return out

    def payload(self, value):
        result = deepcopy(value)
        for key in ("nodeIds", "contextNodeIds"):
            if key in result:
                result[key] = self.refs(result[key])
        for key in ("nodes", "referenceContext"):
            if key in result:
                _require(isinstance(result[key], dict))
                nodes = {}
                for ref, node in result[key].items():
                    node = self.direct(node)
                    if isinstance(node.get("sourceStructure"), dict):
                        node["sourceStructure"] = self.direct(node["sourceStructure"])
                    # Semantic/typed values and all their nested properties are literals.
                    nodes[self.ref(ref)] = node
                result[key] = nodes
        if "tables" in result:
            tables = {}
            for ref, table in result["tables"].items():
                table = self.direct(table)
                if "id" in table:
                    table["id"] = self.ref(table["id"], "tables")
                for key in ("cells", "headerCells", "leadingCells"):
                    if key in table:
                        table[key] = self.cell_collection(table[key])
                if "columnCandidates" in table:
                    table["columnCandidates"] = self.items(table["columnCandidates"], self.direct)
                tables[self.ref(ref, "tables")] = table
            result["tables"] = tables
        for key in ("meaningSources", "boundaryContext"):
            if key in result:
                result[key] = self.items(result[key], self.direct)
        if "relations" in result:
            result["relations"] = self.items(
                result["relations"],
                lambda item: self.direct(item, source_one={"sourceRef", "targetRef"}),
            )
        if "sourceUsage" in result:
            result["sourceUsage"] = self.direct(
                result["sourceUsage"], source_many={"valueRefs", "definitionRefs"}
            )
        if "unaccountedBindings" in result:
            result["unaccountedBindings"] = {
                bid: self.direct(binding) for bid, binding in result["unaccountedBindings"].items()
            }
        if "frozenStructure" in result:
            frozen = result["frozenStructure"]
            _require(isinstance(frozen, dict))
            if "repeats" in frozen:

                def repeat(item):
                    item = self.direct(item)
                    for name in ("columns", "rowRoles"):
                        if name in item:
                            item[name] = self.items(item[name], self.direct)
                    # repeat.id and column.id are semantic IDs, not table/source IDs.
                    return item

                frozen["repeats"] = self.items(frozen["repeats"], repeat)
            if "compiledDefinitions" in frozen:
                frozen["compiledDefinitions"] = self.items(
                    frozen["compiledDefinitions"], self.direct
                )
        if "sameTableMapping" in result and "columns" in result["sameTableMapping"]:
            result["sameTableMapping"]["columns"] = self.items(
                result["sameTableMapping"]["columns"], self.direct
            )
        return result


def _schema(contract, translator):
    """Specialize shared definitions only when reference and literal uses differ."""
    definitions = contract.get("$defs", {})
    _require(isinstance(definitions, dict))
    usages, visited = {}, set()

    def reference_name(value):
        if not isinstance(value, str) or not value.startswith("#/$defs/"):
            return None
        name = value[len("#/$defs/") :].replace("~1", "/").replace("~0", "~")
        _require(name in definitions, "table_reference_wire_schema_reference_missing")
        return name

    def role_for(name):
        if name in _SOURCE_ONE | _SOURCE_MANY:
            return "sources"
        if name in _TABLE_ONE:
            return "tables"
        return None

    def children(node, role, visit, *, top_level=False):
        if isinstance(node.get("properties"), dict):
            for name, child in node["properties"].items():
                child_role = (
                    "sourceKeys"
                    if top_level and name == "sourceDecisions"
                    else None
                    if role == "sourceKeys"
                    else role_for(name)
                )
                visit(child, child_role)
        for name in _SCHEMA_CHILDREN:
            if isinstance(node.get(name), dict):
                visit(node[name], role)
        for name in _SCHEMA_LISTS:
            if isinstance(node.get(name), list):
                for child in node[name]:
                    visit(child, role)

    def collect(node, role=None, *, top_level=False):
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            name = reference_name(node["$ref"])
            if name is not None:
                usages.setdefault(name, set()).add(role)
                if (name, role) not in visited:
                    visited.add((name, role))
                    collect(definitions[name], role)
            elif role is not None:
                raise TableReferenceWireError("table_reference_wire_external_reference_schema")
        children(node, role, collect, top_level=top_level)

    collect(contract, top_level=True)
    chosen, extra = {}, {}
    for name, roles in usages.items():
        primary = None if None in roles else sorted(roles)[0]
        for role in roles:
            if role == primary:
                chosen[name, role] = name
            else:
                label = {"sources": "Source", "tables": "Table", "sourceKeys": "SourceKeys"}[role]
                new = "Wire" + label + name
                _require(
                    new not in definitions and new not in extra,
                    "table_reference_wire_schema_name_collision",
                )
                chosen[name, role] = new
                extra[new] = (name, role)

    def rewrite(node, role=None, *, top_level=False):
        if not isinstance(node, dict):
            return deepcopy(node)
        out = deepcopy(node)
        if "$ref" in node:
            name = reference_name(node["$ref"])
            if name is not None:
                target = chosen.get((name, role), name)
                out["$ref"] = "#/$defs/" + target.replace("~", "~0").replace("/", "~1")
        if role in {"sources", "tables"}:
            for key in ("enum", "const", "default"):
                if key not in node:
                    continue
                if key == "enum":
                    _require(isinstance(node[key], list))
                    out[key] = [
                        translator.ref(v, role, schema=True) if v is not None else v
                        for v in node[key]
                    ]
                elif isinstance(node[key], str):
                    out[key] = translator.ref(node[key], role, schema=True)
        if isinstance(node.get("properties"), dict):
            properties = {}
            for name, child in node["properties"].items():
                key = translator.ref(name, schema=True) if role == "sourceKeys" else name
                _require(key not in properties, "table_reference_wire_schema_key_collision")
                child_role = (
                    "sourceKeys"
                    if top_level and name == "sourceDecisions"
                    else None
                    if role == "sourceKeys"
                    else role_for(name)
                )
                properties[key] = rewrite(child, child_role)
            out["properties"] = properties
        if role == "sourceKeys" and "required" in node:
            required = _refs(node["required"])
            _require(
                len(required) == len(set(required)), "table_reference_wire_schema_key_collision"
            )
            out["required"] = [translator.ref(name, schema=True) for name in required]
        for name in _SCHEMA_CHILDREN:
            if isinstance(node.get(name), dict):
                out[name] = rewrite(node[name], role)
        for name in _SCHEMA_LISTS:
            if isinstance(node.get(name), list):
                out[name] = [rewrite(child, role) for child in node[name]]
        return out

    out = rewrite(contract, top_level=True)
    if "$defs" in contract:
        out["$defs"] = {}
        for name, definition in definitions.items():
            roles = usages.get(name, {None})
            primary = None if None in roles else sorted(roles)[0]
            out["$defs"][name] = rewrite(definition, primary)
        for new, (name, role) in extra.items():
            out["$defs"][new] = rewrite(definitions[name], role)
    return out


@dataclass(frozen=True)
class MeaningWire:
    _payload: dict = field(repr=False)
    _contract: dict = field(repr=False)
    _feedback: dict | list[str] | None = field(repr=False)
    _identity: dict | None = field(repr=False)

    @property
    def payload(self):
        return deepcopy(self._payload)

    @property
    def contract(self):
        return deepcopy(self._contract)

    @property
    def feedback(self):
        return deepcopy(self._feedback)

    @property
    def identity(self):
        return deepcopy(self._identity)

    def decode(self, value):
        value = _copy(value)
        if self._identity is None:
            return value
        return _Translator(self._identity["dictionary"], decoding=True).response(value)


def prepare_meaning_wire(payload, contract, feedback=None):
    """Use aliases only when baseline payload+schema including wire instructions shrinks.

    Feedback never controls activation or dictionary generation. Unknown response
    references fail before compiler validation; callers retain all existing schema,
    source-range and revision checks after decode. This function does not infer
    meaning, change field names or mutate any original source/checkpoint value.
    """
    payload, contract, feedback = _copy(payload), _copy(contract), _copy(feedback)
    _require(isinstance(payload, dict) and isinstance(contract, dict))
    _require(
        "referenceDictionary" not in payload and "referenceWire" not in payload,
        "table_reference_wire_already_encoded",
    )
    dictionary = _dictionary(payload)
    translator = _Translator(dictionary)
    encoded_payload = translator.payload(payload)
    encoded_contract = _schema(contract, translator)
    encoded_payload["referenceWire"] = {
        "version": VERSION,
        "instruction": (
            "Use source handles in sourceDecisions keys and source-reference fields; "
            "table handles only in table-reference fields. "
            "Column/meaning IDs and literal quotations remain unchanged."
        ),
    }
    baseline = len(_encoded(payload | {"outputContract": contract}))
    proposed = len(_encoded(encoded_payload | {"outputContract": encoded_contract}))
    if proposed >= baseline:
        return MeaningWire(payload, contract, feedback, None)
    encoded_feedback = translator.feedback(feedback)
    identity = _identity(dictionary)
    return MeaningWire(encoded_payload, encoded_contract, encoded_feedback, identity)
