"""One source choice per native text value, translated to the private compiler IR."""

from __future__ import annotations

from copy import deepcopy


def source_schema(bindings):
    def closed(properties):
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    choices = []
    if bindings:
        choices.append(
            closed(
                {
                    "kind": {"type": "string", "const": "binding"},
                    "bindingId": {"type": "string", "enum": bindings},
                    "status": {"type": "string", "enum": ["present", "blank"]},
                }
            )
        )
    choices.extend(
        [
            closed(
                {
                    "kind": {"type": "string", "const": "quote"},
                    "quote": {"$ref": "#/$defs/SourceQuote"},
                }
            ),
            closed(
                {
                    "kind": {"type": "string", "const": "missing"},
                    "status": {"type": "string", "enum": ["absent", "unreadable", "uncertain"]},
                }
            ),
        ]
    )
    return {"anyOf": choices}


def constrain(schema, bindings):
    schema["$defs"]["NativeValueSource"] = source_schema(bindings)
    for name in ["FieldLink", "LogicalValue"]:
        definition = schema["$defs"][name]
        old = {"bindingId", "sourceQuote", "status"}
        for key in old:
            definition["properties"].pop(key, None)
        definition["required"] = [k for k in definition["required"] if k not in old]
        definition["properties"]["valueSource"] = {"$ref": "#/$defs/NativeValueSource"}
        definition["required"].append("valueSource")


def _decode_link(link):
    if not isinstance(link, dict) or {"bindingId", "sourceQuote", "status"} & link.keys():
        raise ValueError("native_value_source_requires_single_choice")
    source = link.get("valueSource")
    if not isinstance(source, dict):
        raise ValueError("native_value_source_required")
    binding, quote, status = None, None, "present"
    kind = source.get("kind")
    if (
        kind == "binding"
        and set(source) == {"kind", "bindingId", "status"}
        and isinstance(source["bindingId"], str)
        and source["bindingId"]
        and source["status"] in ("present", "blank")
    ):
        binding, status = source["bindingId"], source["status"]
    elif kind == "quote" and set(source) == {"kind", "quote"}:
        # Preserve omitted occurrence: ambiguous matches must still be rejected.
        # The typed IR and compiler validate the quote and its actual source view.
        quote = source["quote"]
        if not isinstance(quote, dict):
            raise ValueError("native_value_quote_required")
    elif (
        kind == "missing"
        and set(source) == {"kind", "status"}
        and source["status"] in ("absent", "unreadable", "uncertain")
    ):
        status = source["status"]
    else:
        raise ValueError("native_value_source_invalid_choice")
    return {
        **{k: v for k, v in link.items() if k != "valueSource"},
        "bindingId": binding,
        "sourceQuote": quote,
        "status": status,
    }


def decode_content(value):
    """Reject old/ambiguous links even when a backend does not enforce the grammar."""
    result = deepcopy(value)
    fields, records = result.get("fields", []), result.get("logicalRecords", [])
    if not isinstance(fields, list) or not isinstance(records, list):
        raise ValueError("native_content_invalid_collection")
    result["fields"] = [_decode_link(f) for f in fields]
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("rows"), list):
            raise ValueError("native_content_invalid_record")
        for row in record["rows"]:
            if not isinstance(row, dict) or not isinstance(row.get("values"), list):
                raise ValueError("native_content_invalid_row")
            row["values"] = [_decode_link(v) for v in row["values"]]
    return result
