"""Model-only reference translation, not semantic interpretation or quality evidence."""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from document_files.interpretation.table_reference_wire import (
    TableReferenceWireError,
    _Translator,
    prepare_meaning_wire,
)


def fixture(short=False):
    refs = [
        f"s{i}" if short else f"document:recognized:page:1:table:inventory-part-details/cell/{i}"
        for i in range(8)
    ]
    table = "t" if short else "document:recognized:page:1:table:inventory-part-details"
    payload = {
        "regionId": "region-1",
        "nodeIds": refs[:6],
        "contextNodeIds": refs[6:],
        "sourceInventorySHA256": "a" * 64,
        "meaningSources": [
            {"sourceRef": ref, "text": f"원문 {ref} @s0 | 1.020"} for ref in refs[:6]
        ],
        "referenceContext": {
            ref: {"text": ref, "semantic": {"value": {"raw": refs[0], "sourceRef": refs[1]}}}
            for ref in refs[6:]
        },
        "tables": {
            table: {
                "id": table,
                "cells": {
                    "encoding": "columns-rows.v1",
                    "columns": ["row", "col", "sourceRef", "text"],
                    "rows": [[i // 2, i % 2, ref, refs[0]] for i, ref in enumerate(refs[:6])],
                },
                "columnCandidates": [{"column": 0, "headerRefs": refs[:2], "headerText": refs[0]}],
            }
        },
        "sourceUsage": {"valueRefs": refs[2:6], "definitionRefs": refs[:2]},
        "frozenStructure": {
            "repeats": [
                {
                    "id": table,
                    "tableRef": table,
                    "key": refs[0],
                    "label": "한국어 이름 @s0",
                    "definitionRefs": refs,
                    "rowRoles": [{"row": 0, "role": "header", "sourceRefs": refs[:2]}],
                    "columns": [
                        {
                            "id": "@s0",
                            "key": refs[0],
                            "label": refs[0],
                            "definitionRefs": refs[:2],
                            "valueType": "decimal",
                            "column": 0,
                        }
                    ],
                }
            ],
            "compiledDefinitions": [
                {
                    "id": table,
                    "definitionRefs": refs,
                    "label": refs[0],
                    "schemaTargets": [{"path": f"/properties/{refs[0]}"}],
                }
            ],
        },
        "unaccountedBindings": {"binding-1": {"sourceRef": refs[0], "path": "/text"}},
        "relations": [
            {
                "kind": "contextCandidate",
                "sourceRef": refs[6],
                "tableRef": table,
                "description": refs[0],
            }
        ],
        "sameTableMapping": {"key": "k", "columns": [{"id": "@s0", "definitionRefs": refs[:2]}]},
    }
    contract = {
        "type": "object",
        "properties": {
            "meanings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "scope": {
                            "type": "object",
                            "properties": {
                                "columnIds": {"type": "array", "items": {"enum": ["@s0"]}}
                            },
                        },
                        "sourceQuotes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sourceRef": {"$ref": "#/$defs/SourceChoice"},
                                    "text": {"type": "string"},
                                    "occurrence": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
            },
            "sourceReviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sourceRefs": {"type": "array", "items": {"$ref": "#/$defs/SourceChoice"}},
                        "explanation": {"type": "string"},
                    },
                },
            },
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reviewSourceRefs": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/SourceChoice"},
                        }
                    },
                },
            },
            "dispositions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"sourceRef": {"type": "string", "enum": refs}},
                },
            },
        },
        "$defs": {"SourceChoice": {"enum": refs, "type": "string"}},
    }
    response = {
        "regionId": "region-1",
        "meanings": [
            {
                "id": "@s0",
                "description": refs[0],
                "kind": "note",
                "scope": {"kind": "columns", "columnIds": ["@s0"]},
                "sourceQuotes": [
                    {"sourceRef": refs[0], "text": "@s0", "occurrence": 0},
                    {"sourceRef": refs[1], "text": refs[1]},
                ],
            }
        ],
        "sourceReviews": [
            {"sourceRefs": refs, "role": "no_additional_meaning", "explanation": refs[0]}
        ],
        "changes": [
            {
                "previousIds": ["@s0"],
                "replacementIds": ["@s1"],
                "reviewSourceRefs": refs[:2],
                "reason": refs[0],
            }
        ],
        "dispositions": [{"sourceRef": refs[2], "role": "structural", "explanation": "@s0"}],
        "excludedBindings": [{"bindingId": "@s0", "role": "label", "explanation": refs[0]}],
        "unresolved": [refs[0]],
    }
    return payload, contract, response, refs, table


def test_payload_schema_and_response_round_trip_preserves_literals():
    payload, contract, response, refs, table = fixture()
    original = deepcopy((payload, contract, response))
    wire = prepare_meaning_wire(payload, contract)
    assert wire.identity is not None
    forward = _Translator(wire.identity["dictionary"])
    inverse = _Translator(wire.identity["dictionary"], decoding=True)
    restored_payload = wire.payload
    restored_payload.pop("referenceWire")
    assert inverse.payload(restored_payload) == payload
    encoded = forward.response(response)
    assert wire.decode(encoded) == response
    Draft202012Validator(contract).validate(response)
    Draft202012Validator(wire.contract).validate(encoded)
    assert (payload, contract, response) == original
    assert encoded["meanings"][0]["description"] == refs[0]
    assert encoded["meanings"][0]["sourceQuotes"][0]["text"] == "@s0"
    assert encoded["meanings"][0]["sourceQuotes"][1]["text"] == refs[1]
    assert encoded["meanings"][0]["scope"]["columnIds"] == ["@s0"]
    repeat = wire.payload["frozenStructure"]["repeats"][0]
    assert repeat["id"] == table
    assert repeat["columns"][0]["id"] == "@s0"
    assert repeat["columns"][0]["key"] == refs[0]
    assert repeat["columns"][0]["label"] == refs[0]
    assert wire.contract["$defs"]["SourceChoice"]["enum"] != refs
    assert (
        wire.contract["properties"]["meanings"]["items"]["properties"]["sourceQuotes"]["items"][
            "properties"
        ]["sourceRef"]["$ref"]
        == "#/$defs/SourceChoice"
    )


def test_columnar_only_reference_named_columns_translate():
    payload, contract, _, refs, table = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded_table = next(iter(wire.payload["tables"].values()))
    original = payload["tables"][table]["cells"]
    assert encoded_table["cells"]["columns"] == original["columns"]
    for old, new in zip(original["rows"], encoded_table["cells"]["rows"], strict=True):
        assert old[:2] == new[:2]
        assert new[2] != old[2]
        assert new[3] == refs[0]


@pytest.mark.parametrize(
    "alter",
    [
        lambda c: c["rows"][0].pop(),
        lambda c: c["columns"].append("sourceRef"),
        lambda c: c.update(encoding="unknown"),
    ],
)
def test_malformed_columnar_shape_rejected(alter):
    payload, contract, _, _, table = fixture()
    alter(payload["tables"][table]["cells"])
    with pytest.raises(TableReferenceWireError, match="columnar_shape"):
        prepare_meaning_wire(payload, contract)


def test_dict_cells_header_refs_and_source_structure():
    payload, contract, _, refs, table = fixture()
    payload["tables"][table]["cells"] = [
        {"sourceRef": refs[0], "sourceRefs": refs[:2], "text": refs[0]}
    ]
    payload["referenceContext"][refs[6]]["sourceStructure"] = {
        "sourceRef": refs[0],
        "tableRef": table,
    }
    wire = prepare_meaning_wire(payload, contract)
    restored = wire.payload
    restored.pop("referenceWire")
    assert _Translator(wire.identity["dictionary"], decoding=True).payload(restored) == payload


def test_feedback_translation_is_nested_and_literal_safe():
    payload, contract, response, refs, _ = fixture()
    feedback = {
        "acceptedResponse": response,
        "baseRevision": "a" * 64,
        "issues": [
            "code:" + refs[0],
            {"code": "problem", "sourceRef": refs[0], "description": refs[0]},
        ],
        "remainingSourceRanges": [
            {
                "sourceRef": refs[0],
                "path": "/text",
                "start": 1,
                "end": 4,
                "text": refs[0],
                "meaningIds": ["@s0"],
            }
        ],
        "instruction": refs[0],
    }
    before = deepcopy(feedback)
    first = prepare_meaning_wire(payload, contract)
    repaired = prepare_meaning_wire(payload, contract, feedback)
    assert first.identity == repaired.identity
    assert first.payload == repaired.payload and first.contract == repaired.contract
    assert repaired.decode(repaired.feedback["acceptedResponse"]) == response
    assert repaired.feedback["remainingSourceRanges"][0]["sourceRef"] != refs[0]
    assert repaired.feedback["remainingSourceRanges"][0]["text"] == refs[0]
    assert repaired.feedback["issues"][0] == "code:" + refs[0]
    assert feedback == before


def test_short_synthetic_refs_remain_unchanged_even_with_large_feedback():
    payload, contract, response, _, _ = fixture(short=True)
    feedback = {"acceptedResponse": response, "instruction": "long " * 10000}
    wire = prepare_meaning_wire(payload, contract, feedback)
    assert wire.identity is None
    assert wire.payload == payload and wire.contract == contract and wire.feedback == feedback
    assert wire.decode(response) == response


def test_dictionary_is_sorted_and_independent_of_source_order_or_feedback():
    payload, contract, response, _, _ = fixture()
    first = prepare_meaning_wire(payload, contract)
    payload["nodeIds"].reverse()
    payload["contextNodeIds"].reverse()
    other = prepare_meaning_wire(payload, contract, {"acceptedResponse": response})
    assert first.identity == other.identity


def test_shared_schema_definition_does_not_translate_literal_enum():
    payload, contract, response, refs, _ = fixture()
    contract["properties"]["literalChoice"] = {"$ref": "#/$defs/SourceChoice"}
    contract["properties"]["quotedExample"] = {
        "type": "string",
        "default": refs[0],
        "examples": [refs[1]],
    }
    response["literalChoice"] = refs[0]
    wire = prepare_meaning_wire(payload, contract)
    assert wire.contract["$defs"]["SourceChoice"]["enum"] == refs
    assert wire.contract["$defs"]["WireSourceSourceChoice"]["enum"] != refs
    assert wire.contract["properties"]["literalChoice"]["$ref"] == "#/$defs/SourceChoice"
    assert wire.contract["properties"]["quotedExample"] == contract["properties"]["quotedExample"]
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    Draft202012Validator(wire.contract).validate(encoded)
    assert wire.decode(encoded) == response


@pytest.mark.parametrize("bad", ["@s9999", "@t0", "not-offered"])
def test_unknown_or_wrong_role_response_alias_rejected(bad):
    payload, contract, response, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    encoded["meanings"][0]["sourceQuotes"][0]["sourceRef"] = bad
    with pytest.raises(TableReferenceWireError, match="unknown_or_conflicting_reference"):
        wire.decode(encoded)


def test_original_ref_not_mixed_into_active_response():
    payload, contract, response, refs, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    encoded["sourceReviews"][0]["sourceRefs"][0] = refs[0]
    with pytest.raises(TableReferenceWireError, match="unknown_or_conflicting_reference"):
        wire.decode(encoded)


def test_literal_alias_collision_is_not_rejected_or_modified():
    payload, contract, response, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    encoded["meanings"][0]["sourceQuotes"][0]["text"] = "@t0 @s9999"
    encoded["meanings"][0]["description"] = "@s9999"
    restored = wire.decode(encoded)
    assert restored["meanings"][0]["sourceQuotes"][0]["text"] == "@t0 @s9999"
    assert restored["meanings"][0]["description"] == "@s9999"


def test_actual_original_identifier_alias_collision_gets_distinct_namespace():
    payload, contract, _, _, _ = fixture()
    payload["contextNodeIds"].append("@s0")
    wire = prepare_meaning_wire(payload, contract)
    assert "@s0" not in wire.identity["dictionary"]["sources"]
    assert "@s0" in wire.identity["dictionary"]["sources"].values()


def test_same_spelling_source_table_and_column_have_separate_roles():
    payload, contract, _, refs, table = fixture()
    payload["nodeIds"].append(table)
    payload["meaningSources"].append({"sourceRef": table, "text": table})
    wire = prepare_meaning_wire(payload, contract)
    dictionary = wire.identity["dictionary"]
    source_handle = next(k for k, v in dictionary["sources"].items() if v == table)
    table_handle = next(k for k, v in dictionary["tables"].items() if v == table)
    assert source_handle != table_handle
    assert wire.payload["meaningSources"][-1]["sourceRef"] == source_handle
    assert table_handle in wire.payload["tables"]


def test_mutable_inputs_and_return_values_do_not_mutate_decode_dictionary():
    payload, contract, response, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    payload.clear()
    contract.clear()
    wire.identity["dictionary"]["sources"].clear()
    wire.payload.clear()
    wire.contract.clear()
    assert wire.decode(encoded) == response
    assert json.loads(json.dumps(wire.decode(encoded), ensure_ascii=False)) == response


def test_double_encoding_and_invalid_json_rejected():
    payload, contract, _, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    with pytest.raises(TableReferenceWireError, match="already_encoded"):
        prepare_meaning_wire(wire.payload, wire.contract)
    payload["value"] = float("nan")
    with pytest.raises(TableReferenceWireError, match="invalid_json"):
        prepare_meaning_wire(payload, contract)


def test_identity_validate_from_minimal_payload_without_activation_reselection():
    from document_files.interpretation.table_reference_wire import validate_meaning_wire_identity

    payload, contract, _, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    minimal = {key: deepcopy(payload[key]) for key in ("nodeIds", "contextNodeIds", "tables")}
    minimal["tables"] = {key: {} for key in minimal["tables"]}
    assert validate_meaning_wire_identity(minimal, wire.identity) is None
    assert validate_meaning_wire_identity(minimal, None) is None
    assert validate_meaning_wire_identity({}, None) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", "old-version"),
        ("dictionarySha256", "0" * 64),
        ("dictionary", {"sources": {}, "tables": {}}),
        ("extra", True),
    ],
)
def test_identity_tampering_rejected(field, value):
    from document_files.interpretation.table_reference_wire import validate_meaning_wire_identity

    payload, contract, _, _, _ = fixture()
    identity = prepare_meaning_wire(payload, contract).identity
    identity[field] = value
    with pytest.raises(TableReferenceWireError, match="identity_mismatch"):
        validate_meaning_wire_identity(payload, identity)


def test_identity_source_or_table_change_rejected():
    from document_files.interpretation.table_reference_wire import validate_meaning_wire_identity

    payload, contract, _, _, _ = fixture()
    identity = prepare_meaning_wire(payload, contract).identity
    changed = deepcopy(payload)
    changed["contextNodeIds"].append("newly offered source")
    with pytest.raises(TableReferenceWireError, match="identity_mismatch"):
        validate_meaning_wire_identity(changed, identity)
    changed = deepcopy(payload)
    changed["tables"]["new table"] = {}
    with pytest.raises(TableReferenceWireError, match="identity_mismatch"):
        validate_meaning_wire_identity(changed, identity)


def test_initial_contract_failure_feedback_list_remains_literal():
    payload, contract, _, refs, _ = fixture()
    feedback = ["invalid_table_contract", "error:" + refs[0], "@s0"]
    wire = prepare_meaning_wire(payload, contract, feedback)
    assert wire.identity is not None
    assert wire.feedback == feedback
    feedback.clear()
    assert wire.feedback == ["invalid_table_contract", "error:" + refs[0], "@s0"]
    assert prepare_meaning_wire(*fixture(short=True)[:2], ["unchanged"]).feedback == ["unchanged"]


def test_relation_target_refs_translate_only_at_relation_positions():
    payload, contract, _, refs, _ = fixture()
    payload["relations"][0]["targetRef"] = refs[1]
    payload["referenceContext"][refs[6]]["semantic"]["value"]["targetRef"] = refs[1]
    payload["boundaryContext"] = [
        {
            "sourceRef": refs[0],
            "text": refs[1],
            "role": "context_only",
            "textRange": {"path": "/text", "start": 0, "end": 5},
        }
    ]
    wire = prepare_meaning_wire(payload, contract)
    assert wire.payload["relations"][0]["targetRef"] != refs[1]
    assert wire.payload["boundaryContext"][0]["sourceRef"] != refs[0]
    assert wire.payload["boundaryContext"][0]["text"] == refs[1]
    restored = wire.payload
    restored.pop("referenceWire")
    assert _Translator(wire.identity["dictionary"], decoding=True).payload(restored) == payload


@pytest.mark.parametrize("where", ["sourceReviews", "changes", "dispositions"])
def test_unknown_alias_rejected_in_every_response_reference_collection(where):
    payload, contract, response, _, _ = fixture()
    wire = prepare_meaning_wire(payload, contract)
    encoded = _Translator(wire.identity["dictionary"]).response(response)
    if where == "sourceReviews":
        encoded[where][0]["sourceRefs"][0] = "@s999"
    elif where == "changes":
        encoded[where][0]["reviewSourceRefs"][0] = "@s999"
    else:
        encoded[where][0]["sourceRef"] = "@s999"
    with pytest.raises(TableReferenceWireError, match="unknown_or_conflicting_reference"):
        wire.decode(encoded)


def test_alias_dictionary_overhead_is_part_of_activation_size():
    payload = {
        "nodeIds": ["a-long-source-reference-but-used-once"],
        "contextNodeIds": [],
        "tables": {},
    }
    contract = {"type": "object", "properties": {}}
    wire = prepare_meaning_wire(payload, contract)
    assert wire.identity is None
    assert "referenceWire" not in wire.payload


def test_large_literal_only_feedback_cannot_switch_alias_activation():
    payload, contract, _, refs, _ = fixture(short=True)
    feedback = {"issues": [refs[0]] * 3000, "remainingSourceRanges": []}
    assert prepare_meaning_wire(payload, contract, feedback).identity is None


def source_first_fixture(*, short=False):
    payload, contract, _, refs, _ = fixture(short=short)
    quote = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "occurrence": {"type": "integer"},
        },
    }
    contract["$defs"]["SourceQuote"] = {
        **deepcopy(quote),
        "properties": {
            **deepcopy(quote["properties"]),
            "sourceRef": {"$ref": "#/$defs/SourceChoice"},
        },
    }
    decision = {
        "type": "object",
        "properties": {
            "decision": {"enum": ["has_meaning", "unreviewed"]},
            "explanation": {"type": "string"},
            "meanings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "description": {"type": "string"},
                        "quotes": {"type": "array", "items": quote},
                        "additionalQuotes": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/SourceQuote"},
                        },
                    },
                },
            },
        },
    }
    contract["$defs"]["Decision"] = decision
    contract["properties"]["sourceDecisions"] = {
        "type": "object",
        "properties": {ref: {"$ref": "#/$defs/Decision"} for ref in refs[:6]},
        "required": refs[:6],
        "additionalProperties": False,
    }
    response = {
        "sourceDecisions": {
            ref: {"decision": "unreviewed", "explanation": ref} for ref in refs[:6]
        },
        "baseRevision": None,
        "changes": [{"reviewSourceRefs": refs[:2]}],
        "metadata": {"sourceDecisions": {refs[0]: "@s0"}},
    }
    response["sourceDecisions"][refs[0]] = {
        "decision": "has_meaning",
        "meanings": [
            {
                "id": "@s0",
                "kind": "condition",
                "description": refs[1],
                "status": "uncertain",
                "scope": {"kind": "columns", "columnIds": ["@s0"]},
                "quotes": [{"text": "@s0", "occurrence": 1}, {"text": refs[0]}],
                "additionalQuotes": [{"sourceRef": refs[1], "text": "한국어 @s0 1.020"}],
            }
        ],
        "remainderReview": {"role": "unreviewed", "explanation": refs[1]},
    }
    return payload, contract, response, refs


def test_source_first_round_trip_literals_feedback_and_identity():
    payload, contract, response, refs = source_first_fixture()
    feedback = {"acceptedResponse": response, "baseRevision": "r", "issues": ["@s0"]}
    before = deepcopy((payload, contract, response, feedback))
    wire = prepare_meaning_wire(payload, contract, feedback)
    assert wire.identity["version"] == "document-files.table-reference-wire.v2"
    assert set(wire.payload["referenceWire"]) == {"version", "instruction"}
    assert "referenceDictionary" not in wire.payload
    assert refs[0] in wire.identity["dictionary"]["sources"].values()
    translated = wire.feedback["acceptedResponse"]
    assert wire.decode(translated) == response
    Draft202012Validator(contract).validate(response)
    Draft202012Validator(wire.contract).validate(translated)
    assert list(translated["sourceDecisions"]) == [f"@s{i}" for i in range(6)]
    meaning = translated["sourceDecisions"]["@s0"]["meanings"][0]
    assert meaning["quotes"] == response["sourceDecisions"][refs[0]]["meanings"][0]["quotes"]
    assert meaning["additionalQuotes"] == [{"sourceRef": "@s1", "text": "한국어 @s0 1.020"}]
    assert meaning["id"] == "@s0"
    assert meaning["description"] == refs[1]
    assert meaning["scope"]["columnIds"] == ["@s0"]
    assert translated["metadata"] == response["metadata"]
    assert wire.contract["properties"]["sourceDecisions"]["required"] == [
        f"@s{i}" for i in range(6)
    ]
    assert (payload, contract, response, feedback) == before
    # Mutating snapshots returned to a caller cannot mutate the saved mapping.
    translated["sourceDecisions"].clear()
    assert wire.decode(wire.feedback["acceptedResponse"]) == response


def test_source_decision_schema_role_does_not_rewrite_shared_literal_properties():
    payload, contract, _, refs = source_first_fixture()
    decisions = contract["properties"]["sourceDecisions"]
    contract["$defs"]["KeyedObject"] = decisions
    contract["properties"]["sourceDecisions"] = {"$ref": "#/$defs/KeyedObject"}
    contract["properties"]["metadata"] = {
        "type": "object",
        "properties": {"sourceDecisions": {"$ref": "#/$defs/KeyedObject"}},
    }
    wire = prepare_meaning_wire(payload, contract)
    assert wire.contract["$defs"]["KeyedObject"]["required"] == refs[:6]
    assert wire.contract["properties"]["metadata"] == contract["properties"]["metadata"]
    special = wire.contract["properties"]["sourceDecisions"]["$ref"].split("/")[-1]
    assert special != "KeyedObject"
    assert wire.contract["$defs"][special]["required"] == [f"@s{i}" for i in range(6)]


@pytest.mark.parametrize("location", ["key", "additionalQuote"])
@pytest.mark.parametrize("unknown", ["@unknown", "@t0", "original"])
def test_source_first_decode_rejects_unknown_or_wrong_role(location, unknown):
    payload, contract, response, refs = source_first_fixture()
    wire = prepare_meaning_wire(payload, contract, {"acceptedResponse": response})
    encoded = wire.feedback["acceptedResponse"]
    bad = refs[0] if unknown == "original" else unknown
    if location == "key":
        encoded["sourceDecisions"][bad] = encoded["sourceDecisions"].pop("@s0")
    else:
        encoded["sourceDecisions"]["@s0"]["meanings"][0]["additionalQuotes"][0]["sourceRef"] = bad
    with pytest.raises(TableReferenceWireError, match="unknown_or_conflicting"):
        wire.decode(encoded)


def test_source_decision_schema_alias_collision_and_duplicate_required_rejected():
    payload, contract, _, refs = source_first_fixture()
    contract["properties"]["sourceDecisions"]["properties"]["@s0"] = {"type": "object"}
    with pytest.raises(TableReferenceWireError, match="unknown_or_conflicting"):
        prepare_meaning_wire(payload, contract)
    del contract["properties"]["sourceDecisions"]["properties"]["@s0"]
    contract["properties"]["sourceDecisions"]["required"].append(refs[0])
    with pytest.raises(TableReferenceWireError, match="schema_key_collision"):
        prepare_meaning_wire(payload, contract)


def test_source_first_short_baseline_stays_unencoded_even_with_large_feedback():
    payload, contract, response, _ = source_first_fixture(short=True)
    feedback = {"acceptedResponse": response, "issues": ["large" * 1000]}
    wire = prepare_meaning_wire(payload, contract, feedback)
    assert wire.identity is None
    assert wire.payload == payload
    assert wire.contract == contract
    assert wire.feedback == feedback
    assert wire.decode(response) == response


def test_reference_wire_instruction_cost_controls_activation_without_dictionary_payload():
    payload, contract, _, _ = source_first_fixture()
    wire = prepare_meaning_wire(payload, contract)
    baseline = len(
        json.dumps(
            payload | {"outputContract": contract}, ensure_ascii=False, separators=(",", ":")
        )
    )
    proposed = len(
        json.dumps(
            wire.payload | {"outputContract": wire.contract},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    assert proposed < baseline
    assert set(wire.payload["referenceWire"]) == {"version", "instruction"}
    assert set(wire.identity) == {"version", "dictionary", "dictionarySha256"}
    with pytest.raises(TableReferenceWireError, match="already_encoded"):
        prepare_meaning_wire(wire.payload, wire.contract)
