"""Source-boundary fixtures, not model quality or release evidence."""

import copy
import hashlib

import pytest

from document_files.document_model.model import ObservationDocument
from document_files.interpretation.table_sources import (
    SourceReviewError,
    resolve_quotes,
    review_ranges,
    source_inventory,
)


def inventory(text="길이 12.50 mm; 재검사 필요", **region):
    observation = ObservationDocument(nodes={"n": {"text": text}})
    return source_inventory(observation, {"nodeIds": ["n"], **region})


def meaning(inv, text, mid="m", status="interpreted", **quote):
    return {
        "id": mid,
        "status": status,
        "sourceRanges": resolve_quotes([{"sourceRef": "n", "text": text, **quote}], inv),
    }


def review(role="no_additional_meaning", refs=None):
    return {"sourceRefs": refs or ["n"], "role": role, "explanation": "Explicit fixture review"}


def test_inventory_preserves_order_unicode_precision_empty_and_owned_context_overlap():
    observation = ObservationDocument(
        nodes={
            "a": {"text": "앞🙂가 12.5000 뒤"},
            "b": {"text": ""},
            "context": {"text": "Not owned"},
            "raw": {"text": "Excluded raw", "semanticRole": "source_text"},
            "nontext": {"text": 123},
        }
    )
    original = copy.deepcopy(observation)
    region = {
        "nodeIds": ["b", "a", "raw", "missing", "nontext"],
        "contextNodeIds": ["context", "a"],
        "nodeViews": {"a": {"start": 1, "end": 12}},
    }
    frozen = source_inventory(observation, region)
    assert [s["sourceRef"] for s in frozen["sources"]] == ["b", "a"]
    assert frozen["sources"][1]["text"] == observation.nodes["a"]["text"][1:12]
    assert (
        frozen["sources"][1]["textSHA256"]
        == hashlib.sha256(observation.nodes["a"]["text"][1:12].encode()).hexdigest()
    )
    assert observation == original
    assert frozen == source_inventory(observation.to_dict(), region)
    assert (
        review_ranges([], [], source_inventory(observation, {"nodeIds": ["b"]}))["unreviewed"] == []
    )


@pytest.mark.parametrize(
    "view",
    [
        None,
        {},
        {"start": 0},
        {"start": -1, "end": 2},
        {"start": 2, "end": 1},
        {"start": False, "end": 2},
        {"start": 0, "end": 999},
    ],
)
def test_invalid_views_fail(view):
    with pytest.raises(SourceReviewError):
        inventory(nodeViews={"n": view})


def test_repeated_quotes_require_occurrence_and_keep_original_offsets():
    frozen = inventory("prefix mm mm suffix", nodeViews={"n": {"start": 7, "end": 12}})
    with pytest.raises(SourceReviewError, match="occurrence"):
        resolve_quotes([{"sourceRef": "n", "text": "mm"}], frozen)
    resolved = resolve_quotes([{"sourceRef": "n", "text": "mm", "occurrence": 1}], frozen)
    assert resolved[0]["start"] == 10 and resolved[0]["end"] == 12
    assert meaning(inventory("aaa"), "aa", occurrence=1)["sourceRanges"][0]["start"] == 1


@pytest.mark.parametrize(
    "quote",
    [
        {"sourceRef": "context", "text": "mm"},
        {"sourceRef": "n", "text": "millimeters"},
        {"sourceRef": "n", "text": ""},
        {"sourceRef": "n", "text": "mm", "start": 9},
        {"sourceRef": "n", "text": "mm", "occurrence": True},
        {"sourceRef": "n", "text": "mm", "occurrence": -1},
        {"sourceRef": "n", "text": "mm", "occurrence": 1},
    ],
)
def test_invalid_or_paraphrased_quotes_fail(quote):
    with pytest.raises(SourceReviewError):
        resolve_quotes([quote], inventory())


def test_duplicate_quote_rejected_but_distinct_occurrences_allowed():
    quote = {"sourceRef": "n", "text": "mm"}
    with pytest.raises(SourceReviewError, match="duplicate"):
        resolve_quotes([quote, quote], inventory())
    result = resolve_quotes([quote | {"occurrence": n} for n in (0, 1)], inventory("mm mm"))
    assert [r["start"] for r in result] == [0, 3]


def test_overlapping_meanings_partition_evidence_without_loss_or_reclassification():
    frozen = inventory("abcdef")
    meanings = [meaning(frozen, "abcd", "a"), meaning(frozen, "cdef", "b", "uncertain")]
    result = review_ranges(meanings, [review()], frozen)
    assert [(r["text"], r["meaningIds"]) for r in result["ranges"]] == [
        ("ab", ["a"]),
        ("cd", ["a", "b"]),
        ("ef", ["b"]),
    ]
    assert all(r["role"] == "meaning" for r in result["ranges"])
    assert result["unreviewed"] == [] and result["unresolved"] == ["n"]
    assert result["inventorySHA256"] == frozen["sha256"]


def test_embedded_note_requires_review_of_remaining_text_and_cannot_be_erased():
    frozen = inventory()
    item = meaning(frozen, "재검사 필요")
    result = review_ranges([item], [], frozen)
    assert result["unreviewed"] == ["n"]
    assert result["ranges"][0]["text"] == "길이 12.50 mm; "
    assert result["ranges"][1]["role"] == "meaning"
    revised = review_ranges([item], [review()], frozen)
    assert revised["unreviewed"] == []
    assert revised["ranges"][0]["role"] == "no_additional_meaning"
    assert revised["ranges"][1] == result["ranges"][1]
    unclear = review_ranges([item], [review("unresolved")], frozen)
    assert unclear["unresolved"] == ["n"]


def test_empty_and_whitespace_only_gaps_need_no_additional_review():
    assert review_ranges([], [], inventory(" \t\n"))["ranges"] == []
    frozen = inventory("  mm\n")
    result = review_ranges([meaning(frozen, "mm")], [], frozen)
    assert result["unreviewed"] == [] and [r["text"] for r in result["ranges"]] == ["mm"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "mm changed"),
        ("start", -1),
        ("end", 999),
        ("textSHA256", "0" * 64),
        ("path", "/value"),
        ("sourceRef", "context"),
    ],
)
def test_forged_ranges_are_rejected(field, value):
    frozen = inventory()
    item = meaning(frozen, "mm")
    item["sourceRanges"][0][field] = value
    with pytest.raises(SourceReviewError):
        review_ranges([item], [], frozen)


@pytest.mark.parametrize(
    "reviews",
    [
        [review(refs=["unknown"])],
        [review(), review()],
        [review(refs=["n", "n"])],
        [review("heading")],
        [review() | {"explanation": "  "}],
        [review() | {"sourceRanges": []}],
        [review() | {"role": []}],
    ],
)
def test_invalid_or_forged_reviews_fail(reviews):
    with pytest.raises(SourceReviewError):
        review_ranges([], reviews, inventory())


def test_corrupt_inventory_and_duplicate_meaning_ranges_fail():
    frozen = inventory()
    corrupted = copy.deepcopy(frozen)
    corrupted["sources"][0]["text"] = "not the original"
    with pytest.raises(SourceReviewError):
        resolve_quotes([], corrupted)
    item = meaning(frozen, "mm")
    item["sourceRanges"] *= 2
    with pytest.raises(SourceReviewError, match="duplicate"):
        review_ranges([item], [], frozen)
    with pytest.raises(SourceReviewError):
        review_ranges([{"id": "m", "status": []}], [], frozen)


def test_source_review_is_pure_and_every_nonwhitespace_character_is_preserved():
    frozen = inventory()
    meanings = [meaning(frozen, "mm")]
    reviews = [review()]
    before = copy.deepcopy((frozen, meanings, reviews))
    result = review_ranges(meanings, reviews, frozen)
    covered = set()
    for part in result["ranges"]:
        covered.update(range(part["start"], part["end"]))
    assert all(
        i in covered for i, char in enumerate(frozen["sources"][0]["text"]) if not char.isspace()
    )
    assert (frozen, meanings, reviews) == before


def test_quotes_do_not_normalize_unicode_or_escape_the_owned_view():
    frozen = inventory("가 가 outside", nodeViews={"n": {"start": 2, "end": 4}})
    selected = resolve_quotes([{"sourceRef": "n", "text": "가"}], frozen)
    assert selected[0]["start"] == 2 and selected[0]["end"] == 4
    for text in ("가", "outside"):
        with pytest.raises(SourceReviewError, match="not_in_source"):
            resolve_quotes([{"sourceRef": "n", "text": text}], frozen)


def test_duplicate_node_ids_and_unencodable_source_use_uniform_errors():
    with pytest.raises(SourceReviewError, match="duplicate"):
        source_inventory({"nodes": {"n": {"text": "x"}}}, {"nodeIds": ["n", "n"]})
    with pytest.raises(SourceReviewError, match="utf8"):
        inventory("\ud800")
    with pytest.raises(SourceReviewError):
        source_inventory(None, {})


def test_unreviewed_and_unresolved_order_follows_original_sources():
    frozen = source_inventory(
        {"nodes": {"a": {"text": "A"}, "b": {"text": "B"}, "c": {"text": "C"}}},
        {"nodeIds": ["c", "b", "a"]},
    )
    assert review_ranges([], [], frozen)["unreviewed"] == ["c", "b", "a"]
    result = review_ranges([], [review("unresolved", ["a", "c"])], frozen)
    assert result["unreviewed"] == ["b"]
    assert result["unresolved"] == ["c", "a"]
