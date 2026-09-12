"""Source-first table review wire, converted to the existing semantic IR contract.

Each owned source is considered once. A joint meaning has one mechanical anchor;
its exact quotes may span sources and clauses. No meaning or unit is inferred here.
"""

from __future__ import annotations

import copy
import json
import re

from .compiler import CompileError
from .semantic_types import _compact_contract
from .table_sources import SourceReviewError, resolve_quotes

VERSION = "document-files.table-source-decisions.v3"
_REVIEW_ROLES = {"no_additional_meaning", "unresolved", "unreviewed"}
# A bare number states no unit, condition, note or definition by itself; offering it a
# meaning branch let the pinned model select plain data values and then quote them for
# header definitions (continued-table development runs 9 to 13).
_BARE_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def bare_number(text):
    return isinstance(text, str) and bool(_BARE_NUMBER.match(text.strip())) and text != ""


def _require(condition, code):
    if not condition:
        raise CompileError(code)


def _refs(inventory):
    return [s["sourceRef"] for s in inventory["sources"]]


def source_decisions_schema(flat_schema, inventory):
    """Finish all source choices before generating any lengthy meaning details."""
    schema = copy.deepcopy(flat_schema)
    definitions = schema["$defs"]
    for name, roles in (
        ("SourceDecision", _REVIEW_ROLES | {"has_meaning"}),
        ("EmptySourceDecision", _REVIEW_ROLES),
        ("ValueOnlySourceDecision", _REVIEW_ROLES),
    ):
        definitions[name] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"decision": {"type": "string", "enum": sorted(roles)}},
            "required": ["decision"],
        }
    props = schema["properties"]
    schema["properties"] = {
        "regionId": props["regionId"],
        "sourceDecisions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                source["sourceRef"]: {
                    "$ref": "#/$defs/EmptySourceDecision"
                    if source["text"] == ""
                    else "#/$defs/ValueOnlySourceDecision"
                    if bare_number(source["text"])
                    else "#/$defs/SourceDecision"
                }
                for source in inventory["sources"]
            },
            "required": _refs(inventory),
        },
        **{k: props[k] for k in ("meanings", "sourceReviews", "baseRevision", "changes")},
        **{
            k: v
            for k, v in props.items()
            if k not in {"regionId", "meanings", "sourceReviews", "baseRevision", "changes"}
        },
    }
    schema["required"] = [
        "regionId",
        "sourceDecisions",
        "meanings",
        "sourceReviews",
        "baseRevision",
        "changes",
    ]
    return _compact_contract(schema)


def source_decisions_to_flat(value, inventory):
    """Require consistent selection, direct quotes and an explicit review per source."""
    _require(
        isinstance(value, dict)
        and {"sourceDecisions", "meanings", "sourceReviews"} <= value.keys(),
        "table_source_decisions_required",
    )
    refs = _refs(inventory)
    decisions = value["sourceDecisions"]
    _require(
        isinstance(decisions, dict) and set(decisions) == set(refs),
        "table_source_decision_inventory",
    )
    for decision in decisions.values():
        _require(
            isinstance(decision, dict)
            and set(decision) == {"decision"}
            and isinstance(decision["decision"], str)
            and decision["decision"] in _REVIEW_ROLES | {"has_meaning"},
            "table_source_decision_shape",
        )
    flat = {k: copy.deepcopy(v) for k, v in value.items() if k != "sourceDecisions"}
    _require(isinstance(flat["sourceReviews"], list), "table_source_review_shape")
    reviews = {}
    for item in flat["sourceReviews"]:
        _require(
            isinstance(item, dict)
            and set(item) == {"sourceRefs", "role", "explanation"}
            and isinstance(item["sourceRefs"], list)
            and 0 < len(item["sourceRefs"]) <= 1000,
            "table_source_review_shape",
        )
        review = _review({"role": item["role"], "explanation": item["explanation"]})
        for ref in item["sourceRefs"]:
            _require(isinstance(ref, str) and ref in refs, "unknown_review_source")
            _require(ref not in reviews, "duplicate_source_review")
            reviews[ref] = review
    _require(set(reviews) == set(refs), "table_source_review_inventory")
    _require(
        isinstance(flat["meanings"], list) and len(flat["meanings"]) <= 100,
        "table_meaning_count_limit",
    )
    for item in flat["meanings"]:
        _require(
            isinstance(item, dict)
            and not {"sourceRefs", "sourceRanges", "quotes", "additionalQuotes"} & item.keys(),
            "table_source_meaning_shape",
        )
    # Existing canonical grouping retains joint evidence and duplicate-content
    # checks. It never chooses which sources need meaning interpretation.
    checked = _flatten_source_quotes(_group_source_quotes(flat, inventory), inventory)
    quoted = {quote["sourceRef"] for item in checked["meanings"] for quote in item["sourceQuotes"]}
    for ref in refs:
        choice = decisions[ref]["decision"]
        _require(
            (choice == "has_meaning") == (ref in quoted),
            "table_source_decision_quote_mismatch",
        )
        if ref not in quoted:
            _require(choice == reviews[ref]["role"], "table_source_decision_review_mismatch")
    return checked


def source_decisions_from_flat(value, inventory):
    """Encode a full accepted snapshot; absent reviews stay explicitly deferred."""
    _require(
        isinstance(value, dict) and "sourceDecisions" not in value,
        "table_source_decisions_already_encoded",
    )
    grouped = _group_source_quotes(value, inventory)
    reviews = []
    for ref, item in grouped["sourceDecisions"].items():
        review = (
            item["remainderReview"]
            if item["decision"] == "has_meaning"
            else {"role": item["decision"], "explanation": item["explanation"]}
        )
        reviews.append({"sourceRefs": [ref], **copy.deepcopy(review)})
    quoted = {
        quote["sourceRef"]
        for meaning in value.get("meanings", [])
        for quote in meaning["sourceQuotes"]
    }
    decisions = {
        ref: {"decision": "has_meaning" if ref in quoted else review["role"]}
        for ref, review in zip(_refs(inventory), reviews, strict=True)
    }
    return (
        {"regionId": value["regionId"], "sourceDecisions": decisions}
        | {k: copy.deepcopy(v) for k, v in value.items() if k not in {"regionId", "sourceReviews"}}
        | {"sourceReviews": reviews}
    )


def _review(value):
    _require(
        isinstance(value, dict) and set(value) == {"role", "explanation"},
        "table_source_review_shape",
    )
    _require(
        isinstance(value["role"], str)
        and value["role"] in _REVIEW_ROLES
        and isinstance(value["explanation"], str)
        and 0 < len(value["explanation"]) <= 500
        and bool(value["explanation"].strip()),
        "table_source_review_invalid",
    )
    return copy.deepcopy(value)


def _flatten_source_quotes(value, inventory):
    """Restore explicit ownership before existing quote, revision and compiler checks."""
    _require(
        isinstance(value, dict)
        and "sourceDecisions" in value
        and not {"meanings", "sourceReviews"}.intersection(value),
        "table_source_decisions_required",
    )
    refs = _refs(inventory)
    decisions = value["sourceDecisions"]
    _require(
        isinstance(decisions, dict) and set(decisions) == set(refs),
        "table_source_decision_inventory",
    )
    rank = {ref: i for i, ref in enumerate(refs)}
    meanings, reviews, fingerprints, ids = [], [], set(), set()
    for ref in refs:
        decision = decisions[ref]
        _require(isinstance(decision, dict), "table_source_decision_shape")
        kind = decision.get("decision")
        _require(isinstance(kind, str), "table_source_decision_shape")
        if kind in _REVIEW_ROLES:
            _require(set(decision) == {"decision", "explanation"}, "table_source_decision_shape")
            review = _review({"role": kind, "explanation": decision["explanation"]})
        else:
            _require(
                kind == "has_meaning"
                and set(decision) == {"decision", "meanings", "remainderReview"}
                and isinstance(decision["meanings"], list)
                and 0 < len(decision["meanings"]) <= 100,
                "table_source_decision_shape",
            )
            review = _review(decision["remainderReview"])
            for item in decision["meanings"]:
                _require(
                    isinstance(item, dict)
                    and "quotes" in item
                    and not {"sourceQuotes", "sourceRefs", "sourceRanges"}.intersection(item),
                    "table_source_meaning_shape",
                )
                own, extra = item["quotes"], item.get("additionalQuotes", [])
                _require(
                    isinstance(own, list)
                    and bool(own)
                    and isinstance(extra, list)
                    and len(own) + len(extra) <= 100,
                    "table_source_meaning_quotes",
                )
                quotes = []
                for quote in own:
                    _require(
                        isinstance(quote, dict)
                        and "text" in quote
                        and set(quote) <= {"text", "occurrence"},
                        "table_owned_quote_shape",
                    )
                    quotes.append({"sourceRef": ref, **quote})
                for quote in extra:
                    _require(
                        isinstance(quote, dict)
                        and isinstance(quote.get("sourceRef"), str)
                        and quote.get("sourceRef") in rank
                        and rank[quote["sourceRef"]] > rank[ref],
                        "table_meaning_anchor_not_first_source",
                    )
                    quotes.append(copy.deepcopy(quote))
                # Reject missing, ambiguous and repeated exact quotes now; never
                # salvage or normalize a model's evidence into another source.
                try:
                    ranges = resolve_quotes(quotes, inventory)
                except SourceReviewError as exc:
                    raise CompileError(str(exc)) from None
                mid = item.get("id")
                _require(
                    isinstance(mid, str) and mid and mid not in ids,
                    "duplicate_or_invalid_meaning_id",
                )
                ids.add(mid)
                canonical = {
                    k: copy.deepcopy(v)
                    for k, v in item.items()
                    if k not in {"quotes", "additionalQuotes"}
                } | {"sourceQuotes": quotes}
                metadata = {
                    k: copy.deepcopy(v)
                    for k, v in canonical.items()
                    if k not in {"id", "sourceQuotes"}
                }
                metadata.setdefault("status", "interpreted")
                scope = metadata.get("scope")
                if (
                    isinstance(scope, dict)
                    and isinstance(scope.get("columnIds"), list)
                    and all(isinstance(column, str) for column in scope["columnIds"])
                ):
                    scope["columnIds"] = sorted(scope["columnIds"])
                fingerprint = json.dumps(
                    metadata
                    | {
                        "sourceRanges": sorted(
                            ranges, key=lambda r: (r["sourceRef"], r["start"], r["end"])
                        )
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                _require(fingerprint not in fingerprints, "duplicate_table_meaning_content")
                fingerprints.add(fingerprint)
                meanings.append(canonical)
        reviews.append({"sourceRefs": [ref], **review})
    _require(len(meanings) <= 100, "table_meaning_count_limit")
    return {k: copy.deepcopy(v) for k, v in value.items() if k != "sourceDecisions"} | {
        "meanings": meanings,
        "sourceReviews": reviews,
    }


def _group_source_quotes(value, inventory):
    """Encode accepted feedback without inventing reviews of still-unreviewed text."""
    refs = _refs(inventory)
    rank = {ref: i for i, ref in enumerate(refs)}
    decisions = {
        ref: {"decision": "unreviewed", "explanation": "Not reviewed in this response."}
        for ref in refs
    }
    reviewed = set()
    for review in value.get("sourceReviews", []):
        for ref in review["sourceRefs"]:
            _require(ref in rank, "unknown_review_source")
            _require(ref not in reviewed, "duplicate_source_review")
            reviewed.add(ref)
            decisions[ref] = {"decision": review["role"], "explanation": review["explanation"]}
    grouped = {ref: [] for ref in refs}
    for meaning in value.get("meanings", []):
        quotes = meaning["sourceQuotes"]
        _require(
            isinstance(quotes, list)
            and quotes
            and all(isinstance(q, dict) and q.get("sourceRef") in rank for q in quotes),
            "unknown_quote_source",
        )
        anchor = min((q["sourceRef"] for q in quotes), key=rank.__getitem__)
        grouped[anchor].append(
            {k: copy.deepcopy(v) for k, v in meaning.items() if k != "sourceQuotes"}
            | {
                "quotes": [
                    {k: v for k, v in q.items() if k != "sourceRef"}
                    for q in quotes
                    if q["sourceRef"] == anchor
                ],
                "additionalQuotes": [copy.deepcopy(q) for q in quotes if q["sourceRef"] != anchor],
            }
        )
    for ref, items in grouped.items():
        if items:
            decisions[ref] = {
                "decision": "has_meaning",
                "meanings": items,
                "remainderReview": {
                    "role": decisions[ref]["decision"],
                    "explanation": decisions[ref]["explanation"],
                },
            }
    return {
        k: copy.deepcopy(v) for k, v in value.items() if k not in {"meanings", "sourceReviews"}
    } | {"sourceDecisions": decisions}
