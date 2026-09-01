"""Bounded HWP5 native text and record-ownership extraction."""

from __future__ import annotations

import json
import os
import stat
import struct
import sys
import zlib
from collections import Counter

import olefile

if __package__:
    from .hwp_images import hwp_binary_items
    from .hwp_structure import (
        SectionStructure,
        doc_info_properties,
        link_document_memos,
    )
else:
    from hwp_images import hwp_binary_items
    from hwp_structure import SectionStructure, doc_info_properties, link_document_memos

REQUEST_SCHEMA_VERSION = "document-files.extraction-request.v2"
RESULT_SCHEMA_VERSION = "document-files.extraction-result.v2"
HWP_SIGNATURE = b"HWP Document File"
PARA_HEADER = 0x42
PARA_TEXT = 0x43
LIST_HEADER = 0x48
TABLE = 0x4D
EXTENDED_CONTROLS = frozenset({1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23})
INLINE_CONTROLS = frozenset({4, 5, 6, 7, 8, 9, 19, 20})
SINGLE_CONTROLS = frozenset({0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31})


class HWPAdapterError(Exception):
    pass


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise HWPAdapterError("invalid configuration")
    if value < minimum or value > maximum:
        raise HWPAdapterError("configuration is outside its safety bounds")
    return value


def _inflate_raw_deflate(data: bytes, *, limit: int) -> bytes:
    inflater = zlib.decompressobj(wbits=-15)
    result = inflater.decompress(data, limit + 1)
    if len(result) > limit or inflater.unconsumed_tail:
        raise HWPAdapterError("inflated HWP section exceeds its byte budget")
    remaining = inflater.flush(limit + 1 - len(result))
    result += remaining
    if len(result) > limit or not inflater.eof:
        raise HWPAdapterError("HWP section is truncated or exceeds its byte budget")
    return result


def _records(data: bytes, *, max_records: int):
    offset = 0
    record_index = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise HWPAdapterError("HWP section ends with a truncated record header")
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag_id = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if len(data) - offset < 4:
                raise HWPAdapterError("HWP section ends with a truncated extended size")
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        end = offset + size
        if end > len(data):
            raise HWPAdapterError("HWP record exceeds its section boundary")
        record_index += 1
        if record_index > max_records:
            raise HWPAdapterError("HWP record count exceeds its configured budget")
        yield record_index, tag_id, level, data[offset:end]
        offset = end


def _decode_paragraph(
    data: bytes, *, field_markers: list[dict] | None = None
) -> tuple[str, Counter[int], list[str]]:
    if len(data) % 2:
        raise HWPAdapterError("HWP paragraph text has an odd byte length")
    output: list[str] = []
    controls: Counter[int] = Counter()
    anomalies: list[str] = []
    offset = 0
    text_offset = 0
    while offset < len(data):
        code = struct.unpack_from("<H", data, offset)[0]
        if code >= 32:
            start = offset
            offset += 2
            if 0xD800 <= code <= 0xDBFF and offset + 2 <= len(data):
                follower = struct.unpack_from("<H", data, offset)[0]
                if 0xDC00 <= follower <= 0xDFFF:
                    offset += 2
            output.append(data[start:offset].decode("utf-16-le", errors="strict"))
            text_offset += len(output[-1])
            continue

        controls[code] += 1
        if code in EXTENDED_CONTROLS | INLINE_CONTROLS:
            end = offset + 16
            if end > len(data):
                anomalies.append("truncated_control")
                break
            closing = struct.unpack_from("<H", data, offset + 14)[0]
            if closing != code:
                anomalies.append("mismatched_control")
                break
            if field_markers is not None and code in {3, 4}:
                field_markers.append(
                    {
                        "code": code,
                        "control_type": data[offset + 2 : offset + 6][::-1].decode(
                            "ascii", errors="replace"
                        ),
                        "offset_utf16": offset // 2,
                        "content_offset": text_offset,
                        "end_token": struct.unpack_from("<I", data, offset + 10)[0],
                    }
                )
            if code == 9:
                output.append("\t")
                text_offset += 1
            offset = end
            continue
        if code in SINGLE_CONTROLS:
            if code == 10:
                output.append("\n")
            elif code == 24:
                output.append("-")
            elif code in {30, 31}:
                output.append(" ")
            if code in {10, 24, 30, 31}:
                text_offset += 1
            offset += 2
            continue
        anomalies.append("unknown_control")
        offset += 2
    raw_text = "".join(output)
    text = raw_text.strip()
    if field_markers is not None:
        leading = len(raw_text) - len(raw_text.lstrip())
        for marker in field_markers:
            marker["content_offset"] = min(
                len(text), max(0, marker["content_offset"] - leading)
            )
        if anomalies:
            field_markers.clear()
    return text, controls, anomalies


def _section_number(name: str) -> tuple[int, str]:
    suffix = name.removeprefix("Section")
    try:
        return int(suffix), name
    except ValueError:
        return sys.maxsize, name


def _extract(request: dict) -> dict:
    if (
        request.get("schema_version") != REQUEST_SCHEMA_VERSION
        or request.get("operation") != "extract"
    ):
        raise HWPAdapterError("invalid request")
    input_value = request.get("input")
    if not isinstance(input_value, dict):
        raise HWPAdapterError("invalid input")
    file_descriptor = input_value.get("file_descriptor")
    path_value = input_value.get("path")
    if (
        input_value.get("kind") != "read_only_file_descriptor"
        or input_value.get("format_id") != "hwp"
        or isinstance(file_descriptor, bool)
        or not isinstance(file_descriptor, int)
        or not 0 <= file_descriptor <= 1_000_000
        or not isinstance(path_value, str)
        or path_value != f"/dev/fd/{file_descriptor}"
    ):
        raise HWPAdapterError("invalid input")
    try:
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise HWPAdapterError("adapter input is not a regular file")
    except OSError as exc:
        raise HWPAdapterError("adapter file descriptor is unavailable") from exc
    config = request.get("config", {})
    if not isinstance(config, dict):
        raise HWPAdapterError("invalid configuration")
    max_sections = _bounded_int(
        config.get("max_sections"),
        default=512,
        minimum=1,
        maximum=4_096,
    )
    max_records = _bounded_int(
        config.get("max_records_per_section"),
        default=500_000,
        minimum=1,
        maximum=2_000_000,
    )
    max_inflated_section_bytes = _bounded_int(
        config.get("max_inflated_section_bytes"),
        default=128 * 1024 * 1024,
        minimum=1,
        maximum=512 * 1024 * 1024,
    )
    max_total_inflated_bytes = _bounded_int(
        config.get("max_total_inflated_bytes"),
        default=512 * 1024 * 1024,
        minimum=1,
        maximum=2 * 1024 * 1024 * 1024,
    )
    max_total_records = _bounded_int(
        config.get("max_total_records"),
        default=2_000_000,
        minimum=1,
        maximum=10_000_000,
    )
    request_budgets = request.get("budgets", {})
    if not isinstance(request_budgets, dict):
        raise HWPAdapterError("invalid request budgets")
    max_units = _bounded_int(
        request_budgets.get("max_units"),
        default=200_000,
        minimum=1,
        maximum=1_000_000,
    )
    max_unit_content_chars = _bounded_int(
        request_budgets.get("max_unit_content_chars"),
        default=5_000_000,
        minimum=1,
        maximum=50_000_000,
    )
    max_total_content_chars = _bounded_int(
        request_budgets.get("max_total_content_chars"),
        default=50_000_000,
        minimum=1,
        maximum=500_000_000,
    )

    try:
        compound_source = os.fdopen(os.dup(file_descriptor), "rb")
    except OSError as exc:
        raise HWPAdapterError("adapter file descriptor cannot be duplicated") from exc
    try:
        compound = olefile.OleFileIO(
            compound_source,
            raise_defects=olefile.DEFECT_INCORRECT,
        )
    except OSError as exc:
        compound_source.close()
        raise HWPAdapterError("input is not a readable HWP compound file") from exc
    with compound_source, compound:
        if not compound.exists("FileHeader"):
            raise HWPAdapterError("HWP FileHeader stream is missing")
        file_header = compound.openstream("FileHeader").read()
        if len(file_header) < 40 or not file_header.startswith(HWP_SIGNATURE):
            raise HWPAdapterError("HWP signature is invalid")
        flags = struct.unpack_from("<I", file_header, 36)[0]
        compressed = bool(flags & 0x01)
        encrypted = bool(flags & 0x02)
        distributed = bool(flags & 0x04)
        if encrypted or distributed:
            raise HWPAdapterError("encrypted or distribution HWP is not supported")
        section_names = [
            parts[1]
            for parts in compound.listdir(streams=True, storages=False)
            if len(parts) == 2
            and parts[0] == "BodyText"
            and parts[1].startswith("Section")
        ]
        section_names.sort(key=_section_number)
        if not section_names:
            raise HWPAdapterError("HWP BodyText sections are missing")
        if len(section_names) > max_sections:
            raise HWPAdapterError("HWP section count exceeds its configured budget")

        shapes, styles, images = [], [], []
        version = struct.unpack_from("<I", file_header, 32)[0]
        doc_info_bytes = 0
        if compound.exists("DocInfo"):
            raw_info = compound.openstream("DocInfo").read(
                max_inflated_section_bytes + 1
            )
            if len(raw_info) > max_inflated_section_bytes:
                raise HWPAdapterError("HWP DocInfo exceeds its byte budget")
            info = (
                _inflate_raw_deflate(raw_info, limit=max_inflated_section_bytes)
                if compressed
                else raw_info
            )
            shapes, styles = doc_info_properties(
                _records(info, max_records=max_records),
                version=version,
            )
            images = hwp_binary_items(
                _records(info, max_records=max_records),
                ["/".join(p) for p in compound.listdir(streams=True, storages=False)],
                compressed=compressed,
            )
            doc_info_bytes = len(info)
        units: list[dict] = []
        structure_issues: list[dict] = []
        total_records = 0
        total_paragraph_headers = 0
        total_paragraph_text = 0
        total_empty_paragraphs = 0
        total_list_headers = 0
        total_tables = 0
        total_inflated_bytes = doc_info_bytes
        total_content_chars = 0
        all_controls: Counter[int] = Counter()
        all_anomalies: Counter[str] = Counter()
        total_field_markers = 0
        for section_ordinal, section_name in enumerate(section_names, start=1):
            raw = compound.openstream(["BodyText", section_name]).read()
            data = (
                _inflate_raw_deflate(raw, limit=max_inflated_section_bytes)
                if compressed
                else raw
            )
            if len(data) > max_inflated_section_bytes:
                raise HWPAdapterError("HWP section exceeds its byte budget")
            total_inflated_bytes += len(data)
            if total_inflated_bytes > max_total_inflated_bytes:
                raise HWPAdapterError("HWP sections exceed their aggregate byte budget")
            paragraph_ordinal = 0
            structure = SectionStructure(
                section_ordinal,
                section_name,
                shapes,
                styles,
                images=images,
                version=version,
            )
            for record_index, tag_id, level, payload in _records(
                data,
                max_records=max_records,
            ):
                total_records += 1
                if total_records > max_total_records:
                    raise HWPAdapterError(
                        "HWP records exceed their aggregate count budget"
                    )
                try:
                    structure.observe(record_index, tag_id, level, payload)
                except ValueError as exc:
                    raise HWPAdapterError(str(exc)) from exc
                if tag_id == PARA_HEADER:
                    total_paragraph_headers += 1
                elif tag_id == LIST_HEADER:
                    total_list_headers += 1
                elif tag_id == TABLE:
                    total_tables += 1
                elif tag_id == PARA_TEXT:
                    total_paragraph_text += 1
                    paragraph_ordinal += 1
                    field_markers = []
                    text, controls, anomalies = _decode_paragraph(
                        payload, field_markers=field_markers
                    )
                    total_field_markers += len(field_markers)
                    if total_field_markers > max_units:
                        raise HWPAdapterError(
                            "HWP field positions exceed the unit budget"
                        )
                    structure.fields(record_index, level, field_markers)
                    all_controls.update(controls)
                    all_anomalies.update(anomalies)
                    if not text:
                        total_empty_paragraphs += 1
                        continue
                    if len(text) > max_unit_content_chars:
                        raise HWPAdapterError(
                            "HWP paragraph exceeds its content budget"
                        )
                    if len(units) >= max_units:
                        raise HWPAdapterError("HWP unit count exceeds its budget")
                    total_content_chars += len(text)
                    if total_content_chars > max_total_content_chars:
                        raise HWPAdapterError(
                            "HWP content exceeds its aggregate character budget"
                        )
                    structure.text(record_index, level, paragraph_ordinal, text)
            section_units, section_issues = structure.finish()
            if len(units) + len(section_units) > max_units:
                raise HWPAdapterError("HWP unit count exceeds its budget")
            units.extend(section_units)
            if any(
                len(unit["content"]) > max_unit_content_chars for unit in section_units
            ):
                raise HWPAdapterError("HWP unit exceeds its content budget")
            if sum(len(unit["content"]) for unit in units) > max_total_content_chars:
                raise HWPAdapterError(
                    "HWP content exceeds its aggregate character budget"
                )
            structure_issues.extend(section_issues)

    issues = link_document_memos(units, structure_issues)
    if not any(unit["content"] for unit in units):
        issues.append(
            {
                "code": "no_extractable_text",
                "severity": "warning",
                "message": "The document contains no extractable native text.",
            }
        )
    if all_anomalies:
        issues.append(
            {
                "code": "hwp_control_sequence_anomaly",
                "message": "One or more HWP paragraph control sequences were malformed.",
                "severity": "warning",
                "details": dict(sorted(all_anomalies.items())),
            }
        )
    if any(code in all_controls for code in (30, 31)):
        issues.append(
            {
                "code": "hwp_special_space_normalized",
                "message": "HWP non-breaking or fixed-width spaces were normalized to spaces.",
                "severity": "info",
                "details": {
                    "nonbreaking_space": all_controls[30],
                    "fixed_width_space": all_controls[31],
                },
            }
        )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "completeness": "partial"
        if any(issue["severity"] != "info" for issue in issues)
        else "complete",
        "units": units,
        "issues": issues,
    }


def main() -> int:
    try:
        line = sys.stdin.buffer.readline()
        request = json.loads(line.decode("utf-8"))
        if not isinstance(request, dict):
            raise HWPAdapterError("invalid request")
        result = _extract(request)
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sys.stdout.write(encoded + "\n")
        return 0
    # The adapter boundary must fail without emitting an implementation traceback.
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"HWP extraction adapter failed: {type(exc).__name__}.\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
