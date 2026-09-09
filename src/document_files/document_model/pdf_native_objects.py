"""Bounded source-object inventory, not rendering, OCR truth, or blank-cell approval.

The caller must serialize PDFium use and supply its normal process/time envelope.
Limits below bound materialized inputs/streams/operators/objects and cooperatively
check elapsed time; they cannot preempt a native parser inside a C call.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import io
import math
import re
import time
import zlib
from contextlib import closing
from copy import deepcopy

from .recognition_coordinates import fingerprint

VERSION = "document-files.pdf-native-objects.v1"
DEFAULT_LIMITS = {
    "maxInputBytes": 16_000_000,
    "maxPages": 16,
    "maxStreams": 128,
    "maxDecodedBytes": 8_000_000,
    "maxOperators": 50_000,
    "maxObjects": 20_000,
    "maxSegments": 50_000,
    "maxTextBytes": 800_000,
    "maxSeconds": 10,
}
# Operand counts for the deliberately narrow supported content syntax.
ARITY = {
    **dict.fromkeys(
        [
            "q",
            "Q",
            "h",
            "S",
            "s",
            "f",
            "F",
            "f*",
            "B",
            "B*",
            "b",
            "b*",
            "n",
            "BT",
            "ET",
            "T*",
            "EMC",
            "BX",
            "EX",
        ],
        0,
    ),
    **dict.fromkeys(
        [
            "w",
            "J",
            "j",
            "M",
            "ri",
            "i",
            "G",
            "g",
            "Tc",
            "Tw",
            "Tz",
            "TL",
            "Tr",
            "Ts",
            "Do",
            "sh",
            "BMC",
            "MP",
        ],
        1,
    ),
    **dict.fromkeys(["m", "l", "Td", "TD", "Tf", "d", "DP", "BDC"], 2),
    **dict.fromkeys(["RG", "rg"], 3),
    **dict.fromkeys(["v", "y", "re", "K", "k"], 4),
    "c": 6,
    "cm": 6,
    "Tm": 6,
    "Tj": 1,
    "TJ": 1,
    "'": 1,
    '"': 3,
    "W": 0,
    "W*": 0,
    "gs": 1,
    "CS": 1,
    "cs": 1,
}
PAINT = set(["S", "s", "f", "F", "f*", "B", "B*", "b", "b*"])
TEXT = {"Tj", "TJ", "'", '"'}
UNSUPPORTED = {"W", "W*", "gs", "CS", "cs", "sh", "BX", "EX", "MP", "DP", "BDC", "BMC", "EMC"}


def _issue(page, code, **details):
    page["issues"].append({"code": code, **details})


def _finite(values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def _bound(usage, limits, key, amount=1):
    usage[key] += amount
    if usage[key] > limits[key]:
        raise ValueError(f"{key}_exceeded")


def _operand(value, depth=0):
    from pdfminer.psparser import PSLiteral, literal_name

    if depth > 8:
        raise ValueError("operand_depth_exceeded")
    if isinstance(value, bytes):
        return {"bytesHex": value.hex()}
    if isinstance(value, PSLiteral):
        return {"name": literal_name(value)}
    if type(value) in (int, float, bool) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite_operand")
        return value
    if isinstance(value, list):
        return [_operand(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): _operand(v, depth + 1) for k, v in value.items()}
    raise ValueError("unsupported_operand")


def _decoded(stream, maximum):
    from pdfminer.psparser import literal_name

    if stream.decipher:
        raise ValueError("encrypted_stream_unsupported")
    filters = stream.get_filters()
    names = [literal_name(f) for f, _ in filters]
    if any(
        f not in {"FlateDecode", "Fl", "ASCII85Decode", "A85"} or p not in ({}, None)
        for (f, p) in zip(names, [p for _, p in filters], strict=True)
    ):
        raise ValueError("stream_filter_or_parameters_unsupported")
    raw = stream.get_rawdata()
    data = raw if raw is not None else stream.data
    if not isinstance(data, bytes) or len(data) > maximum:
        raise ValueError("decoded_stream_budget_exceeded")
    if raw is not None:
        for name in names:
            if name in {"ASCII85Decode", "A85"}:
                data = base64.a85decode(data, adobe=True)
            else:
                decoder = zlib.decompressobj()
                data = decoder.decompress(data, maximum + 1)
                if len(data) > maximum or not decoder.eof or decoder.unused_data:
                    raise ValueError("decoded_stream_budget_or_trailing_data")
            if len(data) > maximum:
                raise ValueError("decoded_stream_budget_exceeded")
    return data, names, hashlib.sha256(raw).hexdigest() if raw is not None else None


def _valid_operation(operation):
    op, args = operation["operator"], operation["operands"]
    if op not in ARITY or len(args) != ARITY[op]:
        return False

    def name(value):
        return isinstance(value, dict) and set(value) == {"name"} and isinstance(value["name"], str)

    def string(value):
        if not isinstance(value, dict) or set(value) != {"bytesHex"}:
            return False
        try:
            return (
                isinstance(value["bytesHex"], str)
                and bytes.fromhex(value["bytesHex"]).hex() == value["bytesHex"]
            )
        except ValueError:
            return False

    if op in {"Tj", "'"}:
        return string(args[0])
    if op == "TJ":
        return isinstance(args[0], list) and all(string(v) or _finite([v]) for v in args[0])
    if op == '"':
        return _finite(args[:2]) and string(args[2])
    if op == "Tf":
        return name(args[0]) and _finite(args[1:])
    if op == "d":
        return isinstance(args[0], list) and _finite(args[0] + args[1:])
    if op in {"Do", "ri", "gs", "CS", "cs", "sh", "BMC", "MP"}:
        return name(args[0])
    if op in {"BDC", "DP"}:
        return False
    return _finite(args)


def _source_inventory(source_page, output, usage, limits, deadline):
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.pdftypes import PDFStream, resolve1, stream_value
    from pdfminer.psparser import PSEOF, PSKeyword, keyword_name, literal_name

    resources = resolve1(source_page.resources)
    if source_page.annots:
        _issue(output, "annotations_unsupported")
    if source_page.rotate != 0:
        _issue(output, "page_rotation_unsupported")
    streams = source_page.contents
    output["declaredSourceStreamCount"] = len(streams)
    state = {"graphicsDepth": 0, "textOpen": False}
    for stream_index, obj in enumerate(streams):
        _bound(usage, limits, "maxStreams")
        item = {"index": stream_index, "status": "unexamined"}
        output["sourceStreams"].append(item)
        try:
            stream = stream_value(obj)
            data, filters, raw_hash = _decoded(
                stream, limits["maxDecodedBytes"] - usage["maxDecodedBytes"]
            )
            _bound(usage, limits, "maxDecodedBytes", len(data))
            item.update(
                objectNumber=stream.objid,
                generation=stream.genno,
                encodedSha256=raw_hash,
                decodedSha256=hashlib.sha256(data).hexdigest(),
                decodedBytes=len(data),
                filters=filters,
            )
            parser = PDFContentParser([PDFStream({}, data)])
            operands = []
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("time_budget_exceeded")
                try:
                    position, token = parser.nextobject()
                except PSEOF:
                    if operands or parser.context or parser.curstack:
                        raise ValueError("truncated_content_operands") from None
                    break
                if not isinstance(token, PSKeyword):
                    operands.append(token)
                    if len(operands) > 64:
                        raise ValueError("operand_budget_exceeded")
                    continue
                _bound(usage, limits, "maxOperators")
                op = keyword_name(token)
                operation = {
                    "sequence": len(output["sourceOperators"]),
                    "streamIndex": stream_index,
                    "decodedOffset": position,
                    "operator": op,
                    "operands": [_operand(v) for v in operands],
                }
                output["sourceOperators"].append(operation)
                if not _valid_operation(operation):
                    _issue(
                        output,
                        "unsupported_or_invalid_operator",
                        sequence=operation["sequence"],
                        operator=op,
                    )
                if op in UNSUPPORTED:
                    _issue(output, "unsupported_content_operator", operator=op)
                if op == "q":
                    state["graphicsDepth"] += 1
                    if state["graphicsDepth"] > 64:
                        raise ValueError("graphics_depth_exceeded")
                elif op == "Q":
                    state["graphicsDepth"] -= 1
                    if state["graphicsDepth"] < 0:
                        raise ValueError("unbalanced_graphics_state")
                elif op == "BT":
                    if state["textOpen"]:
                        raise ValueError("nested_text_object")
                    state["textOpen"] = True
                elif op == "ET":
                    if not state["textOpen"]:
                        raise ValueError("unbalanced_text_object")
                    state["textOpen"] = False
                elif op in TEXT and not state["textOpen"]:
                    raise ValueError("text_show_outside_text_object")
                elif op == "Tr" and operands and operands[0] not in (0, 1, 2, 3):
                    _issue(output, "text_clipping_or_invalid_mode")
                if op == "Do" and len(operands) == 1:
                    name = literal_name(operands[0])
                    xobjects = resolve1(resources.get("XObject", {}))
                    xobject = stream_value(xobjects[name])
                    subtype = literal_name(xobject.get("Subtype"))
                    operation["xobjectSubtype"] = subtype
                    operation["xobjectNumber"] = xobject.objid
                    if subtype != "Image":
                        _issue(output, "form_or_unknown_xobject_unsupported", subtype=subtype)
                if op == "Tf" and len(operands) == 2:
                    font = resolve1(resolve1(resources.get("Font", {}))[literal_name(operands[0])])
                    operation["fontSubtype"] = literal_name(font.get("Subtype"))
                    if operation["fontSubtype"] == "Type3":
                        _issue(output, "type3_font_content_unsupported")
                    # Embedded font/CMap programs have their own potentially
                    # unbounded streams. This first source-only inventory does
                    # not decode them or claim their content fully inspected.
                    fonts = [font]
                    descendants = resolve1(font.get("DescendantFonts", []))
                    if not isinstance(descendants, list) or len(descendants) > 16:
                        raise ValueError("font_descendants_unsupported")
                    fonts.extend(resolve1(v) for v in descendants)
                    for selected_font in fonts:
                        descriptor = resolve1(selected_font.get("FontDescriptor", {}))
                        if any(k in descriptor for k in ("FontFile", "FontFile2", "FontFile3")):
                            _issue(output, "embedded_font_program_not_inspected")
                        for key in ("Encoding", "ToUnicode"):
                            if isinstance(resolve1(selected_font.get(key)), PDFStream):
                                _issue(output, "font_mapping_stream_not_inspected", field=key)
                operands = []
            item["status"] = "inspected"
        except Exception as error:
            item.update(status="unresolved", errorType=type(error).__name__, reason=str(error))
            _issue(output, "source_stream_unresolved", streamIndex=stream_index)
    if state["graphicsDepth"] or state["textOpen"]:
        _issue(output, "unbalanced_content_state")


def _color(raw, obj, stroke):
    values = [ctypes.c_uint() for _ in range(4)]
    fn = raw.FPDFPageObj_GetStrokeColor if stroke else raw.FPDFPageObj_GetFillColor
    if not fn(obj, *values):
        raise ValueError("object_color_unavailable")
    return [v.value for v in values]


def _primitive(obj):
    segments = obj.get("segments", [])
    matrix = obj.get("matrix", [])
    return (
        obj.get("pdfiumType") == 2
        and len(segments) == 1
        and obj.get("pathSegmentCount") == 2
        and len(obj.get("pathPoints", [])) == 2
        and obj["pathPoints"][0].get("type") == 2
        and obj["pathPoints"][1].get("type") == 0
        and all(p.get("closed") is False for p in obj["pathPoints"])
        and segments[0]
        == obj["pathPoints"][0].get("point", []) + obj["pathPoints"][1].get("point", [])
        and obj.get("stroke") is True
        and obj.get("fill") is False
        and obj.get("clipPathCount") == 0
        and obj.get("hasTransparency") is False
        and obj.get("dash") == []
        and obj.get("lineCap") == 0
        and _finite([obj.get("strokeWidth")])
        and obj["strokeWidth"] > 0
        and obj.get("strokeColor", [0])[-1] == 255
        and len(matrix) == 6
        and _finite(matrix)
        and matrix[1] == matrix[2] == 0
        and abs(matrix[0]) == abs(matrix[3]) > 0
        and _finite(segments[0])
        and len(segments[0]) == 4
        and ((segments[0][0] == segments[0][2]) != (segments[0][1] == segments[0][3]))
    )


def _pdfium_objects(page, output, usage, limits, deadline):
    import pypdfium2.raw as raw

    height = output["pageSize"][1]
    count = raw.FPDFPage_CountObjects(page)
    if count < 0:
        raise ValueError("object_count_unavailable")
    output["declaredPDFiumObjectCount"] = count
    annot_count = raw.FPDFPage_GetAnnotCount(page)
    output["annotationCount"] = annot_count
    if annot_count != 0:
        _issue(output, "annotations_or_count_unavailable")
    with closing(page.get_textpage()) as textpage:
        for sequence, obj in enumerate(page.get_objects(max_depth=1, textpage=textpage)):
            if time.monotonic() > deadline:
                raise ValueError("time_budget_exceeded")
            _bound(usage, limits, "maxObjects")
            bounds = list(obj.get_bounds())
            matrix = list(obj.get_matrix().get())
            if not _finite(bounds + matrix):
                raise ValueError("object_geometry_nonfinite")
            left, bottom, right, top = bounds
            item = {
                "id": f"pdf-native:page:{output['page']}:object:{sequence}",
                "sequence": sequence,
                "pdfiumType": obj.type,
                "kind": "other",
                "bounds": [left, height - top, right, height - bottom],
                "rawBoundsBottomLeft": bounds,
                "matrix": matrix,
                "segments": [],
                "hasTransparency": bool(raw.FPDFPageObj_HasTransparency(obj)),
            }
            output["objects"].append(item)
            clip = raw.FPDFPageObj_GetClipPath(obj)
            raw_clip_count = raw.FPDFClipPath_CountPaths(clip) if clip else None
            item["rawClipPathCount"] = raw_clip_count
            # PDFium returns -1 for an existing empty CPDF_ClipPath with no ref.
            # Distinguish that case using the independently inspected source
            # stream; a missing handle or any unsupported source cannot prove it.
            source_has_no_clip = (
                not output["issues"]
                and len(output["sourceStreams"]) == output.get("declaredSourceStreamCount")
                and all(s["status"] == "inspected" for s in output["sourceStreams"])
            )
            if clip and raw_clip_count == -1 and source_has_no_clip:
                item["clipPathCount"] = 0
                item["clipBasis"] = "source_no_clip_operators_and_pdfium_no_ref"
            else:
                item["clipPathCount"] = raw_clip_count
                item["clipBasis"] = "pdfium_path_count"
            if item["clipPathCount"] != 0:
                _issue(output, "object_clipping_unsupported", objectRef=item["id"])
            if item["hasTransparency"]:
                _issue(output, "object_transparency_unsupported", objectRef=item["id"])
            if obj.type == raw.FPDF_PAGEOBJ_TEXT:
                item["kind"] = "text"
                required = raw.FPDFTextObj_GetText(obj, textpage, None, 0)
                _bound(usage, limits, "maxTextBytes", required)
                buffer = (ctypes.c_ushort * ((required + 1) // 2))()
                actual = raw.FPDFTextObj_GetText(obj, textpage, buffer, required)
                if required != actual or required < 2:
                    raise ValueError("native_text_unavailable")
                item["text"] = bytes(buffer)[: required - 2].decode("utf-16-le")
                item["textRenderMode"] = raw.FPDFTextObj_GetTextRenderMode(obj)
                if item["textRenderMode"] not in (0, 1, 2, 3):
                    _issue(output, "text_clipping_or_mode_unavailable", objectRef=item["id"])
            elif obj.type == raw.FPDF_PAGEOBJ_IMAGE:
                item["kind"] = "image"
            elif obj.type == raw.FPDF_PAGEOBJ_FORM:
                item["kind"] = "form"
                _issue(output, "form_children_not_inspected", objectRef=item["id"])
            elif obj.type == raw.FPDF_PAGEOBJ_PATH:
                width, fill, stroke = ctypes.c_float(), ctypes.c_int(), ctypes.c_int()
                if not raw.FPDFPageObj_GetStrokeWidth(obj, width) or not raw.FPDFPath_GetDrawMode(
                    obj, fill, stroke
                ):
                    raise ValueError("path_paint_unavailable")
                n = raw.FPDFPath_CountSegments(obj)
                if n < 0:
                    raise ValueError("path_segments_unavailable")
                _bound(usage, limits, "maxSegments", n)
                dash_count = raw.FPDFPageObj_GetDashCount(obj)
                if not 0 <= dash_count <= 128:
                    raise ValueError("dash_pattern_unavailable_or_excessive")
                dash = (ctypes.c_float * dash_count)()
                if dash_count and not raw.FPDFPageObj_GetDashArray(obj, dash, dash_count):
                    raise ValueError("dash_pattern_unavailable")
                phase = ctypes.c_float()
                if not raw.FPDFPageObj_GetDashPhase(obj, phase):
                    raise ValueError("dash_phase_unavailable")
                item.update(
                    stroke=bool(stroke.value),
                    fill=fill.value != 0,
                    fillMode=fill.value,
                    strokeWidth=width.value * abs(matrix[0]),
                    rawStrokeWidth=width.value,
                    strokeColor=_color(raw, obj, True),
                    fillColor=_color(raw, obj, False),
                    lineCap=raw.FPDFPageObj_GetLineCap(obj),
                    lineJoin=raw.FPDFPageObj_GetLineJoin(obj),
                    dash=list(dash),
                    dashPhase=phase.value,
                    pathSegmentCount=n,
                    pathPoints=[],
                )
                previous = None
                a, b, c, d, e, f = matrix
                for index in range(n):
                    segment = raw.FPDFPath_GetPathSegment(obj, index)
                    x, y = ctypes.c_float(), ctypes.c_float()
                    if not segment or not raw.FPDFPathSegment_GetPoint(segment, x, y):
                        raise ValueError("path_point_unavailable")
                    point = [
                        a * x.value + c * y.value + e,
                        height - (b * x.value + d * y.value + f),
                    ]
                    kind = raw.FPDFPathSegment_GetType(segment)
                    closed = bool(raw.FPDFPathSegment_GetClose(segment))
                    item["pathPoints"].append({"point": point, "type": kind, "closed": closed})
                    if kind == raw.FPDF_SEGMENT_LINETO and previous is not None:
                        item["segments"].append(previous + point)
                    previous = (
                        point
                        if kind in (raw.FPDF_SEGMENT_MOVETO, raw.FPDF_SEGMENT_LINETO)
                        else None
                    )
                if _primitive(item) and not any(p["closed"] for p in item["pathPoints"]):
                    item["kind"] = "primitive_line"
            else:
                _issue(output, "object_type_unsupported", objectRef=item["id"], pdfiumType=obj.type)
            item["fingerprint"] = fingerprint(item)


def _counts(page):
    source = {"path": 0, "text": 0, "image": 0, "form": 0}
    for operation in page["sourceOperators"]:
        op = operation["operator"]
        if op in PAINT:
            source["path"] += 1
        elif op in TEXT:
            # Empty text paint operations may be omitted by the native decoder.
            operands = operation["operands"]
            strings = operands[-1] if op == "TJ" and operands else operands[-1:]
            if any(isinstance(s, dict) and s.get("bytesHex") for s in strings):
                source["text"] += 1
        elif op == "Do":
            subtype = operation.get("xobjectSubtype")
            if subtype in ("Image", "Form"):
                source[subtype.lower()] += 1
    native = {
        name: sum(o["pdfiumType"] == typ for o in page["objects"])
        for name, typ in (("path", 2), ("text", 1), ("image", 3), ("form", 5))
    }
    return {"sourcePaint": source, "pdfiumObjects": native, "matched": source == native}


def _operator_structure_valid(page):
    depth, text_open = 0, False
    previous = (-1, -1)
    for sequence, op in enumerate(page["sourceOperators"]):
        index, offset = op["streamIndex"], op["decodedOffset"]
        if (
            type(op["sequence"]) is not int
            or op["sequence"] != sequence
            or type(index) is not int
            or not 0 <= index < len(page["sourceStreams"])
            or type(offset) is not int
            or not 0 <= offset < page["sourceStreams"][index]["decodedBytes"]
            or (index, offset) <= previous
            or not _valid_operation(op)
        ):
            return False
        previous = (index, offset)
        name = op["operator"]
        if name == "q":
            depth += 1
        if name == "Q":
            depth -= 1
        if not 0 <= depth <= 64:
            return False
        if name == "BT":
            if text_open:
                return False
            text_open = True
        elif name == "ET":
            if not text_open:
                return False
            text_open = False
        elif name in TEXT and not text_open:
            return False
    return depth == 0 and not text_open


def _finish(page):
    page["paintCounts"] = _counts(page)
    complete = {
        "sourceStreamsComplete": len(page["sourceStreams"]) == page.get("declaredSourceStreamCount")
        and all(s["status"] == "inspected" for s in page["sourceStreams"]),
        "sourceOperatorsComplete": _operator_structure_valid(page)
        and not any(
            i["code"]
            in {
                "source_stream_unresolved",
                "unsupported_or_invalid_operator",
                "unbalanced_content_state",
            }
            for i in page["issues"]
        ),
        "pdfiumObjectsComplete": len(page["objects"]) == page.get("declaredPDFiumObjectCount")
        and all("fingerprint" in o for o in page["objects"]),
        "paintCountsMatch": page["paintCounts"]["matched"],
        "unsupportedContentPresent": bool(page["issues"]),
    }
    complete["eligibleForNativeCellReasoning"] = (
        all(
            complete[k]
            for k in (
                "sourceStreamsComplete",
                "sourceOperatorsComplete",
                "pdfiumObjectsComplete",
                "paintCountsMatch",
            )
        )
        and not complete["unsupportedContentPresent"]
    )
    page["completeness"] = complete
    page["status"] = "complete" if complete["eligibleForNativeCellReasoning"] else "partial"
    page["fingerprint"] = fingerprint(page)


def inventory_pdf_native_objects(content, *, page_numbers=None, limits=None):
    """Read original bytes once per parser; never render, mutate, OCR or activate forms."""
    selected_limits = dict(DEFAULT_LIMITS)
    if limits:
        if set(limits) - set(DEFAULT_LIMITS):
            raise ValueError("unknown inventory limit")
        selected_limits.update(limits)
    if any(
        type(v) is not int or v <= 0 or v > DEFAULT_LIMITS[k] for k, v in selected_limits.items()
    ):
        raise ValueError("invalid inventory limit")
    if not isinstance(content, bytes):
        raise TypeError("PDF source must be immutable bytes")
    result = {
        "version": VERSION,
        "sourceSha256": hashlib.sha256(content).hexdigest(),
        "status": "partial",
        "pages": [],
        "limits": selected_limits,
        "usage": {k: 0 for k in selected_limits},
        "issues": [],
        "rendered": False,
        "ocrExecuted": False,
        "blankValueProven": False,
        "documentCompletenessVerified": False,
    }
    started = time.monotonic()
    deadline = started + selected_limits["maxSeconds"]
    usage = result["usage"]
    try:
        _bound(usage, selected_limits, "maxInputBytes", len(content))
        from importlib.metadata import version

        import pypdfium2 as pdfium
        from pdfminer.pdfpage import PDFPage

        result["engines"] = {
            "pypdfium2": str(pdfium.PYPDFIUM_INFO),
            "pdfium": str(pdfium.PDFIUM_INFO),
            "pdfminer.six": version("pdfminer.six"),
        }
        wanted = None if page_numbers is None else set(page_numbers)
        if wanted is not None and (not wanted or any(type(n) is not int or n < 1 for n in wanted)):
            raise ValueError("invalid selected pages")
        with pdfium.PdfDocument(content) as native:
            result["pageCount"] = len(native)
            if wanted is not None and any(n > len(native) for n in wanted):
                raise ValueError("selected page unavailable")
            for number, source_page in enumerate(
                PDFPage.get_pages(io.BytesIO(content), check_extractable=True), 1
            ):
                if number > selected_limits["maxPages"]:
                    raise ValueError("page_budget_exceeded")
                if wanted is not None and number not in wanted:
                    continue
                _bound(usage, selected_limits, "maxPages")
                page = {
                    "page": number,
                    "sourceSha256": result["sourceSha256"],
                    "objects": [],
                    "sourceStreams": [],
                    "sourceOperators": [],
                    "issues": [],
                    "coordinateOrigin": "TOPLEFT",
                    "objectSequenceBasis": "pdfium_page_object_order",
                    "operatorSequenceBasis": "source_stream_order_then_decoded_operator_order",
                }
                result["pages"].append(page)
                try:
                    _source_inventory(source_page, page, usage, selected_limits, deadline)
                    if page["issues"]:
                        _issue(page, "pdfium_objects_not_enumerated_for_unsupported_source")
                    else:
                        with closing(native[number - 1]) as pdf_page:
                            page.update(
                                pageSize=[pdf_page.get_width(), pdf_page.get_height()],
                                rotation=pdf_page.get_rotation(),
                                mediaBox=list(pdf_page.get_mediabox()),
                                cropBox=list(pdf_page.get_cropbox()),
                            )
                            if (
                                page["rotation"] != 0
                                or page["mediaBox"] != [0, 0, *page["pageSize"]]
                                or page["cropBox"] != page["mediaBox"]
                            ):
                                _issue(page, "page_coordinate_transform_unsupported")
                            if native.get_formtype() != 0:
                                _issue(page, "document_forms_unsupported")
                            _pdfium_objects(pdf_page, page, usage, selected_limits, deadline)
                            if time.monotonic() > deadline:
                                _issue(page, "time_budget_exceeded")
                except Exception as error:
                    _issue(
                        page,
                        "native_inventory_failed",
                        errorType=type(error).__name__,
                        reason=str(error),
                    )
                _finish(page)
            expected = wanted if wanted is not None else set(range(1, len(native) + 1))
            if {p["page"] for p in result["pages"]} != expected:
                raise ValueError("source_page_inventory_mismatch")
        if result["pages"] and all(p["status"] == "complete" for p in result["pages"]):
            result["status"] = "complete"
    except Exception as error:
        result["issues"].append(
            {
                "code": "native_inventory_unavailable",
                "errorType": type(error).__name__,
                "reason": str(error),
            }
        )
    usage["maxSeconds"] = time.monotonic() - started
    if usage["maxSeconds"] > selected_limits["maxSeconds"]:
        result["status"] = "partial"
        result["issues"].append({"code": "native_inventory_time_budget_exceeded"})
    result["fingerprint"] = fingerprint(result)
    return result


def validate_native_inventory(inventory, *, source_sha256, page):
    """Internal consistency only; original bytes are not reparsed by this receiver."""
    result = {"status": "unverified", "rawSourceReparsed": False}
    try:
        if (
            inventory["version"] != VERSION
            or inventory["fingerprint"] != fingerprint(inventory)
            or inventory["sourceSha256"] != source_sha256
            or type(page) is not int
            or not isinstance(source_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        ):
            raise ValueError("inventory identity mismatch")
        limits, usage = inventory["limits"], inventory["usage"]
        if set(limits) != set(DEFAULT_LIMITS) or set(usage) != set(DEFAULT_LIMITS):
            raise ValueError("inventory limits missing")
        if any(
            type(limits[k]) is not int
            or not 0 < limits[k] <= DEFAULT_LIMITS[k]
            or not _finite([usage[k]])
            or not 0 <= usage[k] <= limits[k]
            for k in limits
        ):
            raise ValueError("inventory budget not satisfied")
        if len(inventory["pages"]) > limits["maxPages"]:
            raise ValueError("page budget inconsistent")
        matches = [p for p in inventory["pages"] if p["page"] == page]
        if len(matches) != 1:
            raise ValueError("page inventory ambiguous")
        entry = matches[0]
        if entry["fingerprint"] != fingerprint(entry) or entry["sourceSha256"] != source_sha256:
            raise ValueError("page identity mismatch")
        for sequence, obj in enumerate(entry["objects"]):
            if (
                type(obj["sequence"]) is not int
                or obj["sequence"] != sequence
                or obj.get("id") != f"pdf-native:page:{page}:object:{sequence}"
                or obj.get("clipPathCount") != 0
                or obj.get("hasTransparency") is not False
                or obj.get("rawClipPathCount") not in (-1, 0)
                or (
                    obj.get("rawClipPathCount") == -1
                    and obj.get("clipBasis") != "source_no_clip_operators_and_pdfium_no_ref"
                )
                or obj.get("pdfiumType") not in (1, 2, 3)
                or (obj.get("pdfiumType") == 1 and obj.get("textRenderMode") not in (0, 1, 2, 3))
                or obj["fingerprint"] != fingerprint(obj)
                or len(obj["bounds"]) != 4
                or not _finite(obj["bounds"])
                or obj["bounds"][0] > obj["bounds"][2]
                or obj["bounds"][1] > obj["bounds"][3]
                or (obj["kind"] == "primitive_line" and not _primitive(obj))
            ):
                raise ValueError("object inventory inconsistent")
        for sequence, op in enumerate(entry["sourceOperators"]):
            if (
                type(op["sequence"]) is not int
                or op["sequence"] != sequence
                or not _valid_operation(op)
                or op["operator"] in UNSUPPORTED
                or (op["operator"] == "Tr" and op["operands"][0] not in (0, 1, 2, 3))
                or (op["operator"] == "Do" and op.get("xobjectSubtype") != "Image")
                or op.get("fontSubtype") == "Type3"
            ):
                raise ValueError("source operator inventory inconsistent")
        if entry.get("annotationCount") != 0 or entry.get("rotation") != 0:
            raise ValueError("page annotation or rotation unsupported")
        if (
            entry.get("mediaBox") != [0, 0, *entry["pageSize"]]
            or entry.get("cropBox") != entry["mediaBox"]
        ):
            raise ValueError("page coordinate transform unsupported")
        for stream_index, stream in enumerate(entry["sourceStreams"]):
            if (
                stream.get("index") != stream_index
                or stream.get("status") != "inspected"
                or not isinstance(stream.get("decodedSha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", stream["decodedSha256"])
                or type(stream.get("decodedBytes")) is not int
                or stream["decodedBytes"] < 0
            ):
                raise ValueError("source stream inconsistent")
        recomputed = deepcopy(entry)
        _finish(recomputed)
        if (
            entry["completeness"] != recomputed["completeness"]
            or entry["paintCounts"] != recomputed["paintCounts"]
            or not recomputed["completeness"]["eligibleForNativeCellReasoning"]
        ):
            raise ValueError("native source coverage incomplete")
        result.update(status="verified", pageInventory=deepcopy(entry))
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as error:
        result["errorType"] = type(error).__name__
    return result
