"""Prior decisions stay exact when property names are shared in a review request."""

import copy
import json

import pytest
from test_native_structure_revision import RevisionModel, run, saved_region

from document_files.interpretation.native_structure_history import VERSION, compact, expand


def exact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize(
    "value",
    [
        [],
        [{}, {}],
        [{"only": "one row"}],
        [{"x": "present"}, {"x": "present", "optional": None}],
        [
            {"a_long_key": True, "another_long_key": 1, "state": None},
            {"a_long_key": 1, "another_long_key": 1.0, "state": []},
        ],
        {
            "records": [
                {
                    "key": name,
                    "columns": [{"key": c, "label": c * 20} for c in ["left", "right"]],
                    "rows": [{"anchors": [r], "states": ["present", "blank"]} for r in ["a", "b"]],
                }
                for name in ["한글🙂", "second"]
            ]
        },
    ],
)
def test_history_round_trip_preserves_types_order_optional_keys_and_nested_records(value):
    before = copy.deepcopy(value)
    view = compact(value)
    restored = expand(view)
    assert exact(restored) == exact(before) and value == before
    assert len(exact(view)) <= len(exact(value))


@pytest.mark.parametrize(
    "value",
    [
        {"columns": ["x", "x"], "rows": [[1, 2]]},
        {"columns": ["x"], "rows": [[]]},
        {"columns": "x", "rows": [[1]]},
    ],
)
def test_invalid_display_tables_are_not_silently_reinterpreted(value):
    with pytest.raises(ValueError, match="invalid_native_history_table"):
        expand(value)
    with pytest.raises(ValueError, match="ambiguous_native_history_object"):
        compact(value)


def test_review_compacts_only_display_and_keeps_canonical_base_and_entity_coverage():
    model, states = RevisionModel(), []
    out = run(model, states=states)
    assert out["data"] == {"measure": "12.5000"}
    payload = model.revision_requests[0]
    record = saved_region(states[-1])["revision"]
    assert payload["historyEncoding"] == VERSION
    assert expand(payload["previousStructure"]) == record["base"]["structure"]["wireResponse"]
    assert expand(payload["acceptedRoles"]) == model.structure_requests[0]["acceptedRoles"]
    assert isinstance(record["base"]["structure"]["wireResponse"]["fields"], list)
    assert isinstance(payload["previousStructure"]["fields"], dict)
    assert record["base"]["content"]["usage"]["modelCalls"] == 2
    # Program-issued before references remain in the closed contract, not duplicated in payload.
    assert "previousEntityIds" not in payload
    schema = payload["outputContract"]
    assert "field:1" in exact(schema) and "field:2" in exact(schema)
    restored = run(model, restore=states[-1])
    assert restored["data"] == out["data"] and model.calls == 6
