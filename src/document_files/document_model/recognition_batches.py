"""Explicit, bounded independent-cell input membership; never composite images."""

from __future__ import annotations

import csv
import hashlib
import io
import json

BATCH_VERSION = "document-files.cell-image-batch.v1"
RAW_VERSION = "document-files.raw-ocr.v2"


def run_fingerprint(run):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in run.items() if k not in {"fingerprint", "page_no", "batch_page_no"}},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def batch_rows(raw, count):
    """Validate every original row; preserve page numbers and global ordinals."""
    if count not in (1, 2) or not raw:
        raise ValueError("OCR input membership unavailable")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t")
    required = {
        "level",
        "page_num",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    }
    if (
        reader.fieldnames is None
        or len(reader.fieldnames) != len(required)
        or set(reader.fieldnames) != required
    ):
        raise ValueError("OCR TSV header invalid")
    rows, pages, current = {i: [] for i in range(1, count + 1)}, [], None
    for ordinal, row in enumerate(reader):
        if ordinal >= 200000 or None in row or any(v is None for v in row.values()):
            raise ValueError("OCR TSV incomplete or over budget")
        page, level = int(row["page_num"]), int(row["level"])
        if page not in rows or not 1 <= level <= 5:
            raise ValueError("OCR TSV unexpected input page")
        if level == 1:
            pages.append(page)
            if pages != list(range(1, len(pages) + 1)):
                raise ValueError("OCR TSV duplicate or reordered input page")
            current = page
        if current != page:
            raise ValueError("OCR TSV input page marker missing")
        rows[page].append((ordinal, row))
    if pages != list(range(1, count + 1)):
        raise ValueError("OCR TSV input page missing")
    return rows


def validated_batch_capture(capture, runs, captures):
    matches = [r for r in runs if r.get("fingerprint") == capture.get("runFingerprint")]
    if len(matches) != 1:
        raise ValueError("OCR batch run missing or duplicated")
    run = matches[0]
    if (
        run.get("version") != BATCH_VERSION
        or run.get("status") != "complete"
        or run.get("exitCode") != 0
        or run.get("timedOut") is not False
        or run.get("outputTruncated") is not False
        or run.get("processStarted") is not True
        or run_fingerprint(run) != run["fingerprint"]
    ):
        raise ValueError("OCR batch run not complete")
    inputs = run["inputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 2:
        raise ValueError("OCR batch input count invalid")
    if (
        run.get("psm") not in (6, 7)
        or not run.get("languages")
        or len({i["transform"]["repairIndex"] for i in inputs}) != 1
        or len({i["transform"]["cellUnitIndex"] for i in inputs}) != len(inputs)
    ):
        raise ValueError("OCR batch settings or table membership invalid")
    raw = run["tsv"].encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != run["tsvSha256"]:
        raise ValueError("OCR batch TSV changed")
    rows = batch_rows(raw, len(inputs))
    members = [c for c in captures if c.get("runFingerprint") == run["fingerprint"]]
    if any(type(c.get("tsvInputPageNumber")) is not int for c in members) or sorted(
        c.get("tsvInputPageNumber", 0) for c in members
    ) != list(rows):
        raise ValueError("OCR batch captures incomplete or duplicated")
    for member in members:
        index = member["tsvInputPageNumber"]
        item = inputs[index - 1]
        header = rows[index][0][1]
        if (
            [int(header["width"]), int(header["height"])] != item["image"]["size"]
            or int(header["left"]) != 0
            or int(header["top"]) != 0
            or item.get("localPdfPageNumber") != run.get("batch_page_no", run["page_no"])
            or item["tsvInputPageNumber"] != index
            or member.get("image") != item["image"]
            or member.get("unitFingerprint") != item["unitFingerprint"]
            or member.get("transform") != item["transform"]
            or member.get("pixelFrame") != item.get("pixelFrame")
            or member.get("page_no") != run.get("page_no")
            or member.get("status") != "complete"
        ):
            raise ValueError("OCR batch input binding changed")
    return rows[capture["tsvInputPageNumber"]]
