"""Exclusive native source grammar and independent decoding, not AI quality approval."""

import copy

import pytest
from jsonschema import Draft202012Validator
from test_document_outline import decision, unit

from document_files.interpretation import document_protocol as protocol
from document_files.interpretation.backends import _local_grammar_schema, _strict_wire_schema
from document_files.interpretation.native_value_wire import decode_content
from document_files.interpretation.regions import region_payload
from document_files.interpretation.semantic_types import RegionInterpretation, region_output_schema


def fixture():
    doc, region = unit("Count: 0007")
    roles = {"regionId": region["id"], "documentElements": [decision().model_dump()]}
    payload, schema = protocol.content_request(
        region_payload(doc, region),
        region_output_schema(doc, region, compact=False),
        roles,
        doc,
        region,
    )
    return doc, region, roles, payload, schema


@pytest.mark.parametrize("link_type", ["FieldLink", "LogicalValue"])
@pytest.mark.parametrize(
    "variant,valid",
    [
        ("binding", True),
        ("blank", True),
        ("quote", True),
        ("absent", True),
        ("unreadable", True),
        ("uncertain", True),
        ("both", False),
        ("null", False),
        ("quote_blank", False),
        ("missing_quote", False),
        ("missing_binding", False),
        ("binding_absent", False),
        ("missing_blank", False),
        ("legacy", False),
        ("foreign_binding", False),
        ("foreign_quote", False),
        ("quote_offsets", False),
    ],
)
def test_source_choices_in_prompt_and_transported_grammar(link_type, variant, valid):
    _, _, _, payload, schema = fixture()
    bid = payload["documentContent"]["valueBindingIds"][0]
    source = {"kind": "binding", "bindingId": bid, "status": "present"}
    quoted = {"sourceRef": "n1", "text": "0007", "occurrence": 0}
    if variant == "blank":
        source["status"] = "blank"
    elif variant in {"quote", "foreign_quote", "quote_offsets"}:
        source = {"kind": "quote", "quote": quoted}
        if variant == "foreign_quote":
            quoted["sourceRef"] = "foreign"
        if variant == "quote_offsets":
            quoted["start"] = 7
    elif variant in {"absent", "unreadable", "uncertain", "missing_blank"}:
        source = {"kind": "missing", "status": "blank" if variant == "missing_blank" else variant}
    elif variant == "both":
        source["quote"] = quoted
    elif variant == "null":
        source["bindingId"] = None
    elif variant == "quote_blank":
        source = {"kind": "quote", "quote": quoted, "status": "blank"}
    elif variant == "missing_quote":
        source = {"kind": "missing", "status": "absent", "quote": quoted}
    elif variant == "missing_binding":
        source = {"kind": "missing", "status": "absent", "bindingId": bid}
    elif variant == "binding_absent":
        source["status"] = "absent"
    elif variant == "foreign_binding":
        source["bindingId"] = "foreign"
    link = (
        {
            "id": "count",
            "key": "count",
            "label": "Count",
            "valueType": "string",
            "definitionRefs": ["n1"],
            "groupId": None,
            "targetHandle": None,
        }
        if link_type == "FieldLink"
        else {"columnId": "count", "sourceRefs": ["n1"]}
    )
    link["valueSource"] = source
    if variant == "legacy":
        link["bindingId"], link["sourceQuote"], link["status"] = bid, quoted, "present"
    for root in [schema, _strict_wire_schema(_local_grammar_schema(schema))]:
        contract = {"$defs": root["$defs"], "$ref": f"#/$defs/{link_type}"}
        Draft202012Validator.check_schema(contract)
        assert Draft202012Validator(contract).is_valid(link) is valid


@pytest.mark.parametrize(
    "source",
    [
        None,
        {},
        {"kind": "missing", "status": "blank"},
        {"kind": "quote", "quote": None},
        {"kind": "binding", "bindingId": "b", "status": "absent"},
        {"kind": "quote", "quote": {"text": "a"}, "bindingId": "b"},
        {"kind": "missing", "status": "absent", "quote": {}},
        {"kind": "binding", "bindingId": "b", "status": "present", "quote": {}},
    ],
)
def test_unconstrained_backend_cannot_mix_or_invent_source_choices(source):
    value = {"fields": [{"valueSource": source}]}
    with pytest.raises(ValueError, match="native_value"):
        decode_content(value)


def test_decode_preserves_missing_occurrence_for_compiler_ambiguity_check_and_source_immutability():
    value = {
        "fields": [
            {
                "id": "a",
                "valueSource": {"kind": "quote", "quote": {"sourceRef": "s", "text": "🙂가"}},
            }
        ],
        "logicalRecords": [
            {
                "rows": [
                    {
                        "values": [
                            {
                                "columnId": "b",
                                "valueSource": {"kind": "missing", "status": "absent"},
                            }
                        ]
                    }
                ]
            }
        ],
    }
    original = copy.deepcopy(value)
    decoded = decode_content(value)
    assert value == original and decoded["fields"][0]["sourceQuote"] == {
        "sourceRef": "s",
        "text": "🙂가",
    }
    assert decoded["logicalRecords"][0]["rows"][0]["values"][0] == {
        "columnId": "b",
        "bindingId": None,
        "sourceQuote": None,
        "status": "absent",
    }
    assert "occurrence" not in decoded["fields"][0]["sourceQuote"]
    value["fields"][0]["bindingId"] = None
    with pytest.raises(ValueError, match="single_choice"):
        decode_content(value)


def test_quote_remains_possible_when_no_original_binding_is_offered():
    from document_files.interpretation.compiler import compile_region

    doc, region, roles, _, _ = fixture()
    region["bindingIds"], region["requiredBindingIds"] = [], []
    payload, schema = protocol.content_request(
        region_payload(doc, region),
        region_output_schema(doc, region, compact=False),
        roles,
        doc,
        region,
    )
    assert payload["documentContent"]["valueBindingIds"] == []
    value = {
        "regionId": region["id"],
        "fields": [
            {
                "id": "c",
                "key": "count",
                "label": "Count",
                "definitionRefs": ["n1"],
                "valueType": "string",
                "valueSource": {"kind": "quote", "quote": {"sourceRef": "n1", "text": "0007"}},
            }
        ],
    }
    Draft202012Validator(schema).validate(value)
    compiled = compile_region(
        RegionInterpretation.model_validate(protocol.attach_content(value, roles)), doc, region
    )
    assert compiled.data == {"count": "0007"}
