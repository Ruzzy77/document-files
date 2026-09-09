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

VERSION = "document-files.pdf-native-objects.v3"
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


def _font_stream(stream, usage, limits):
    _bound(usage, limits, "maxStreams")
    data, filters, encoded = _decoded(stream, limits["maxDecodedBytes"] - usage["maxDecodedBytes"])
    _bound(usage, limits, "maxDecodedBytes", len(data))
    return data, {
        "objectNumber": stream.objid,
        "generation": stream.genno,
        "encodedSha256": encoded,
        "decodedSha256": hashlib.sha256(data).hexdigest(),
        "decodedBytes": len(data),
        "filters": filters,
    }


def _one_byte_cmap(data, usage, limits, deadline):
    """Validate a narrow ToUnicode syntax using the existing PDF tokenizer."""
    from pdfminer.pdfinterp import PDFContentParser
    from pdfminer.pdftypes import PDFStream
    from pdfminer.psparser import PSEOF, PSKeyword, keyword_name

    parser = PDFContentParser([PDFStream({}, data)])
    tokens = []
    while True:
        if time.monotonic() > deadline:
            raise ValueError("time_budget_exceeded")
        try:
            _, value = parser.nextobject()
        except PSEOF:
            if parser.context or parser.curstack:
                raise ValueError("truncated_font_cmap") from None
            break
        _bound(usage, limits, "maxOperators")
        tokens.append(keyword_name(value) if isinstance(value, PSKeyword) else _operand(value))
    prologue = [
        {"name": "CIDInit"},
        {"name": "ProcSet"},
        "findresource",
        "begin",
        12,
        "dict",
        "begin",
        "begincmap",
    ]
    tail = [
        "endcmap",
        "CMapName",
        "currentdict",
        {"name": "CMap"},
        "defineresource",
        "pop",
        "end",
        "end",
    ]
    if tokens[:8] != prologue or tokens[-8:] != tail:
        raise ValueError("font_cmap_wrapper_unsupported")
    body = tokens[8:-8]
    attrs = {}
    while len(body) >= 3 and isinstance(body[0], dict) and set(body[0]) == {"name"}:
        name, value, op = body[:3]
        key = name["name"]
        if op != "def" or key not in {"CIDSystemInfo", "CMapName", "CMapType"} or key in attrs:
            raise ValueError("font_cmap_attributes_unsupported")
        attrs[key] = value
        body = body[3:]
    if attrs.get("CMapType") != 2 or set(attrs) != {"CIDSystemInfo", "CMapName", "CMapType"}:
        raise ValueError("font_cmap_type_unsupported")
    if len(body) < 5 or body[:2] != [1, "begincodespacerange"] or body[4] != "endcodespacerange":
        raise ValueError("font_cmap_codespace_unsupported")

    def code(v):
        if not isinstance(v, dict) or set(v) != {"bytesHex"}:
            raise ValueError("font_cmap_code_invalid")
        return bytes.fromhex(v["bytesHex"])

    low, high = code(body[2]), code(body[3])
    if len(low) != 1 or len(high) != 1 or low > high:
        raise ValueError("font_cmap_codespace_invalid")
    body = body[5:]
    mapping = {}
    while body:
        n = body[0]
        if type(n) is not int or not 0 < n <= 256 or len(body) < 3 or body[1] != "beginbfchar":
            raise ValueError("font_cmap_mapping_unsupported")
        if len(body) < 3 + 2 * n or body[2 + 2 * n] != "endbfchar":
            raise ValueError("font_cmap_mapping_truncated")
        for index in range(n):
            source, dest = code(body[2 + 2 * index]), code(body[3 + 2 * index])
            if len(source) != 1 or not low <= source <= high or source.hex() in mapping:
                raise ValueError("font_cmap_mapping_duplicate_or_outside")
            value = dest.decode("utf-16-be")
            if len(value) != 1 or ord(value) > 0xFFFF:
                raise ValueError("font_cmap_multichar_unsupported")
            mapping[source.hex()] = ord(value)
        body = body[3 + 2 * n :]
    if not mapping:
        raise ValueError("font_cmap_empty")
    return mapping


def _truetype_format6(data, usage, limits, deadline):
    """Narrow adapter over ReportLab's parser, not a general sfnt parser.

    Preflight the one bounded cmap before TTFontFile.extractInfo can expand
    arbitrary ranges. Do not use its inverted format-6 charToGlyph mapping.
    """
    from reportlab.pdfbase.ttfonts import TTFontFile, TTFontParser

    if data[:4] not in (b"\x00\x01\x00\x00", b"true"):
        raise ValueError("font_program_format_unsupported")
    _bound(usage, limits, "maxDecodedBytes", len(data))

    class BoundedFont(TTFontFile):
        pass

    def bounded_reader(name):
        original = getattr(TTFontParser, name)

        def read(self, *args):
            if time.monotonic() > deadline:
                raise ValueError("time_budget_exceeded")
            _bound(usage, limits, "maxOperators")
            return original(self, *args)

        return read

    for method in (
        "read_ushort",
        "read_ulong",
        "read_short",
        "read_uint8",
        "read_tag",
        "get_ushort",
        "get_ulong",
    ):
        setattr(BoundedFont, method, bounded_reader(method))
    parser = BoundedFont.__new__(BoundedFont)
    TTFontParser.__init__(parser, io.BytesIO(data), validate=0)
    if len(parser.tables) != len(parser.table) or len(parser.tables) > 64:
        raise ValueError("font_table_inventory_unsupported")
    spans = []
    for table in parser.tables:
        start, length = table["offset"], table["length"]
        if start < 12 + 16 * len(parser.tables) or start + length > len(data):
            raise ValueError("font_table_bounds_invalid")
        if length and any(start < b and start + length > a for a, b in spans):
            raise ValueError("font_table_overlap")
        spans.append((start, start + length))
    if any(
        k in parser.table for k in ("CFF ", "CFF2", "SVG ", "CBDT", "CBLC", "sbix", "COLR", "fvar")
    ):
        raise ValueError("font_outline_program_unsupported")
    start, size = parser.get_table_pos("cmap")
    parser.seek(start)
    if parser.read_ushort() != 0 or parser.read_ushort() != 1:
        raise ValueError("font_cmap_subtables_unsupported")
    if (parser.read_ushort(), parser.read_ushort(), parser.read_ulong()) != (1, 0, 12):
        raise ValueError("font_cmap_platform_unsupported")
    if parser.read_ushort() != 6:
        raise ValueError("font_cmap_format_unsupported")
    length, language, first, count = [parser.read_ushort() for _ in range(4)]
    if (
        not 0 < count <= 256
        or first + count > 256
        or language != 0
        or length != 10 + 2 * count
        or size != 12 + length
    ):
        raise ValueError("font_cmap_format6_bounds_invalid")
    glyph_map = {f"{first + i:02x}": parser.read_ushort() for i in range(count)}
    _bound(usage, limits, "maxOperators", count + len(parser.tables))
    if time.monotonic() > deadline:
        raise ValueError("time_budget_exceeded")
    # Bound the loops before ReportLab materializes glyph/metric arrays.
    parser.seek_table("maxp", 4)
    glyph_count = parser.read_ushort()
    if not 0 < glyph_count <= 4096:
        raise ValueError("font_glyph_inventory_unsupported")
    _bound(usage, limits, "maxSegments", glyph_count + 1)
    parsed = parser
    parsed.extractInfo(charInfo=1)
    if time.monotonic() > deadline:
        raise ValueError("time_budget_exceeded")
    if not 16 <= parsed.unitsPerEm <= 16384 or parsed.numGlyphs != glyph_count:
        raise ValueError("font_glyph_inventory_unsupported")
    positions = parsed.glyphPos
    glyf_start, glyf_size = parser.get_table_pos("glyf")
    if (
        len(positions) != parsed.numGlyphs + 1
        or any(type(v) is not int or not 0 <= v <= glyf_size for v in positions)
        or positions != sorted(positions)
    ):
        raise ValueError("font_loca_invalid")
    if any(not 0 <= g < parsed.numGlyphs for g in glyph_map.values()):
        raise ValueError("font_cmap_glyph_outside")
    return {
        "cmapFormat": 6,
        "cmapPlatform": [1, 0],
        "codeToGlyph": glyph_map,
        "unitsPerEm": parsed.unitsPerEm,
        "numGlyphs": parsed.numGlyphs,
        "glyphOffsets": positions,
        "glyfOffset": glyf_start,
        "glyfBytes": glyf_size,
    }


def _source_font(font, ref, output, usage, limits, deadline):
    from pdfminer.pdftypes import PDFStream, resolve1
    from pdfminer.psparser import literal_name

    record = {"id": ref, "status": "unverified", "streams": [], "nativeLoadAllowed": False}
    output["fontResources"].append(record)
    try:
        record["subtype"] = literal_name(font.get("Subtype"))
        descriptor = resolve1(font.get("FontDescriptor", {}))
        # An ordinary nonembedded font can still be inventoried, but cannot
        # bypass the later mandatory paint-support check.
        if (
            record["subtype"] == "Type1"
            and not any(k in descriptor for k in ("FontFile", "FontFile2", "FontFile3"))
            and not isinstance(resolve1(font.get("ToUnicode")), PDFStream)
            and not isinstance(resolve1(font.get("Encoding")), PDFStream)
        ):
            record["nativeLoadAllowed"] = True
        if record["subtype"] != "TrueType" or "Encoding" in font or "DescendantFonts" in font:
            raise ValueError("font_mapping_unsupported")
        descriptor = resolve1(font.get("FontDescriptor", {}))
        record["flags"] = descriptor.get("Flags")
        if type(record["flags"]) is not int or not record["flags"] & 4 or record["flags"] & 32:
            raise ValueError("font_symbolic_mapping_unsupported")
        if any(k in descriptor for k in ("FontFile", "FontFile3")):
            raise ValueError("font_program_unsupported")
        program, cmap = resolve1(descriptor.get("FontFile2")), resolve1(font.get("ToUnicode"))
        if not isinstance(program, PDFStream) or not isinstance(cmap, PDFStream):
            raise ValueError("font_program_or_cmap_missing")
        data, item = _font_stream(program, usage, limits)
        item["role"] = "FontFile2"
        record["streams"].append(item)
        text, item = _font_stream(cmap, usage, limits)
        item["role"] = "ToUnicode"
        record["streams"].append(item)
        record["codeToUnicode"] = _one_byte_cmap(text, usage, limits, deadline)
        record["trueType"] = _truetype_format6(data, usage, limits, deadline)
        record["status"] = "verified"
        record["nativeLoadAllowed"] = True
        return record, data
    except Exception as error:
        record.update(errorType=type(error).__name__, reason=str(error))
        return record, None
    finally:
        record["fingerprint"] = fingerprint(record)


def _text_source_bytes(operation):
    args = operation["operands"]
    parts = args[-1] if operation["operator"] == "TJ" else args[-1:]
    return b"".join(
        bytes.fromhex(p["bytesHex"]) for p in parts if isinstance(p, dict) and "bytesHex" in p
    )


def _text_membership(raw, obj, textpage, item, source_text, usage, limits, deadline):
    """Keep PDFium's projection separate from literal source membership."""
    item["sourceText"] = source_text
    address = ctypes.cast(obj.raw, ctypes.c_void_p).value
    count = raw.FPDFText_CountChars(textpage)
    if count < 0:
        raise ValueError("text_character_count_unavailable")
    # Three native observations per character, all in the existing total budget.
    _bound(usage, limits, "maxOperators", 3 * count)
    observed = []
    for index in range(count):
        if time.monotonic() > deadline:
            raise ValueError("time_budget_exceeded")
        candidate = raw.FPDFText_GetTextObject(textpage, index)
        owner = ctypes.cast(candidate, ctypes.c_void_p).value if candidate else None
        observed.append(
            {
                "textPageIndex": index,
                "unicode": raw.FPDFText_GetUnicode(textpage, index),
                "apiGenerated": raw.FPDFText_IsGenerated(textpage, index),
                "objectMembership": "same_object"
                if owner == address
                else "no_object"
                if owner is None
                else "other_object",
            }
        )
    members = [c for c in observed if c["objectMembership"] == "same_object"]
    item["nativeCharacters"] = members
    item["membershipScan"] = {"pageCharacterCount": count, "scannedCharacters": len(observed)}
    if not members or any(c["apiGenerated"] not in (0, 1) for c in members):
        raise ValueError("text_character_membership_unverified")
    literal = [c for c in members if c["apiGenerated"] == 0]
    if "".join(chr(c["unicode"]) for c in literal) != source_text:
        raise ValueError("text_source_native_mismatch")
    if not literal:
        raise ValueError("text_literal_membership_empty")
    first, last = members[0]["textPageIndex"], members[-1]["textPageIndex"]
    projection = observed[first : last + 1]
    # GetTextByObject can append one neighboring generated space. Only append
    # recorded API-generated characters needed by the exact native projection;
    # never strip a character or infer generated status from Unicode alone.
    end = last + 1
    projected = "".join(chr(c["unicode"]) for c in projection)
    while projected != item["text"] and end < count:
        candidate = observed[end]
        if candidate["apiGenerated"] != 1 or candidate["objectMembership"] != "no_object":
            break
        projection.append(candidate)
        projected += chr(candidate["unicode"])
        end += 1
    item["projectionCharacters"] = projection
    item["projectionRange"] = [first, end]
    if projected != item["text"] or not _membership_valid(item):
        raise ValueError("text_projection_not_explained_by_generated_characters")
    return [c["textPageIndex"] for c in literal]


def _membership_valid(item):
    scan = item["membershipScan"]
    count = scan["pageCharacterCount"]
    members, projection = item["nativeCharacters"], item["projectionCharacters"]
    if (
        type(count) is not int
        or count < 1
        or type(scan["scannedCharacters"]) is not int
        or scan["scannedCharacters"] != count
        or not members
        or not projection
    ):
        return False
    for collection in (members, projection):
        indices = []
        for char in collection:
            index, unicode, generated = char["textPageIndex"], char["unicode"], char["apiGenerated"]
            if (
                type(index) is not int
                or not 0 <= index < count
                or type(unicode) is not int
                or not 0 < unicode <= 0xFFFF
                or type(generated) is not int
                or generated not in (0, 1)
            ):
                return False
            indices.append(index)
        if indices != sorted(set(indices)):
            return False
    if any(c["objectMembership"] != "same_object" for c in members):
        return False
    if [c for c in projection if c["objectMembership"] == "same_object"] != members:
        return False
    if any(
        c["objectMembership"] not in ("same_object", "no_object")
        or (c["objectMembership"] == "no_object" and c["apiGenerated"] != 1)
        for c in projection
    ):
        return False
    if (
        item["projectionRange"]
        != [projection[0]["textPageIndex"], projection[-1]["textPageIndex"] + 1]
        or projection[0]["textPageIndex"] != members[0]["textPageIndex"]
    ):
        return False
    if [c["textPageIndex"] for c in projection] != list(range(*item["projectionRange"])):
        return False
    literal = [c for c in members if c["apiGenerated"] == 0]
    return (
        bool(literal)
        and item["sourceText"] == "".join(chr(c["unicode"]) for c in literal)
        and item["text"] == "".join(chr(c["unicode"]) for c in projection)
    )


def _paint_text(raw, obj, textpage, item, source_op, fonts, usage, limits, deadline, height):
    """Bound actual native outlines by their control hull; never replace text."""
    ref = source_op.get("fontResourceRef")
    record, original = fonts.get(ref, ({}, None))
    item.update(
        fontResourceRef=ref,
        sourceOperatorSequence=source_op["sequence"],
        paintBoundsStatus="unverified",
    )
    if record.get("status") != "verified" or original is None or item["textRenderMode"] != 0:
        raise ValueError("text_font_or_paint_unsupported")
    font = raw.FPDFTextObj_GetFont(obj)
    if not font or raw.FPDFFont_GetIsEmbedded(font) != 1:
        raise ValueError("native_font_not_embedded")
    size = ctypes.c_size_t()
    if not raw.FPDFFont_GetFontData(font, None, 0, size) or size.value != len(original):
        raise ValueError("native_font_size_mismatch")
    _bound(usage, limits, "maxDecodedBytes", size.value)
    buffer = (ctypes.c_uint8 * size.value)()
    if (
        not raw.FPDFFont_GetFontData(font, buffer, len(buffer), size)
        or size.value != len(original)
        or memoryview(buffer).cast("B") != original
    ):
        raise ValueError("native_font_bytes_mismatch")
    item["nativeFontSha256"] = hashlib.sha256(buffer).hexdigest()
    source = _text_source_bytes(source_op)
    mapping = record["codeToUnicode"]
    codes = [f"{c:02x}" for c in source]
    unicodes = [mapping[c] for c in codes]
    if any(u == 0 or list(mapping.values()).count(u) != 1 for u in unicodes):
        raise ValueError("text_unicode_inverse_ambiguous")
    chars = _text_membership(
        raw, obj, textpage, item, "".join(chr(u) for u in unicodes), usage, limits, deadline
    )
    if len(chars) != len(codes):
        raise ValueError("text_character_membership_mismatch")
    font_size = ctypes.c_float()
    if (
        not raw.FPDFTextObj_GetFontSize(obj, font_size)
        or not _finite([font_size.value])
        or font_size.value <= 0
    ):
        raise ValueError("text_size_invalid")
    item["paintGlyphs"] = []
    hull = list(item["bounds"])
    tt = record["trueType"]
    for ordinal, (index, code, unicode) in enumerate(zip(chars, codes, unicodes, strict=True)):
        if time.monotonic() > deadline:
            raise ValueError("time_budget_exceeded")
        if (
            raw.FPDFText_GetUnicode(textpage, index) != unicode
            or raw.FPDFText_IsGenerated(textpage, index) != 0
        ):
            raise ValueError("text_character_source_mismatch")
        matrix = raw.FS_MATRIX()
        x, y = ctypes.c_double(), ctypes.c_double()
        if not raw.FPDFText_GetMatrix(textpage, index, matrix) or not raw.FPDFText_GetCharOrigin(
            textpage, index, x, y
        ):
            raise ValueError("text_character_geometry_unavailable")
        values = [getattr(matrix, k) for k in "abcdef"]
        if (
            not _finite(values + [x.value, y.value])
            or values[1] != 0
            or values[2] != 0
            or values[0] <= 0
            or values[3] <= 0
        ):
            raise ValueError("text_character_transform_unsupported")
        glyph = tt["codeToGlyph"][code]
        start, end = tt["glyphOffsets"][glyph : glyph + 2]
        path = raw.FPDFFont_GetGlyphPath(font, unicode, font_size.value)
        count = raw.FPDFGlyphPath_CountGlyphSegments(path) if path else -1
        points = []
        native_points = []
        if start == end:
            if count > 0:
                raise ValueError("source_empty_native_outline_mismatch")
            basis = "source_loca_zero_length"
        else:
            if not path or count <= 0:
                raise ValueError("native_glyph_outline_unavailable")
            _bound(usage, limits, "maxSegments", count)
            basis = "native_outline_control_hull"
            for n in range(count):
                if time.monotonic() > deadline:
                    raise ValueError("time_budget_exceeded")
                segment = raw.FPDFGlyphPath_GetGlyphPathSegment(path, n)
                px, py = ctypes.c_float(), ctypes.c_float()
                if (
                    not segment
                    or not raw.FPDFPathSegment_GetPoint(segment, px, py)
                    or not _finite([px.value, py.value])
                ):
                    raise ValueError("native_glyph_point_unavailable")
                # PDFium LoadGlyphPath uses a 64px em and divides FT 26.6
                # coordinates by 64*64; its points are normalized to one em.
                # Embedded simple fonts have no substitution-width adjustment.
                native_points.append([px.value, py.value])
                points.append(
                    [
                        x.value + px.value * font_size.value * values[0],
                        height - (y.value + py.value * font_size.value * values[3]),
                    ]
                )
            hull = [
                min(hull[0], min(p[0] for p in points)),
                min(hull[1], min(p[1] for p in points)),
                max(hull[2], max(p[0] for p in points)),
                max(hull[3], max(p[1] for p in points)),
            ]
        item["paintGlyphs"].append(
            {
                "sourceOrdinal": ordinal,
                "sourceCode": code,
                "unicode": unicode,
                "glyphId": glyph,
                "textPageIndex": index,
                "matrix": values,
                "originBottomLeft": [x.value, y.value],
                "fontSize": font_size.value,
                "loca": [start, end],
                "basis": basis,
                "controlPoints": points,
                "nativeOutlinePoints": native_points,
            }
        )
    item.update(paintSupportBounds=hull, paintBoundsStatus="verified")


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
    fonts = {}
    active_font = None
    font_stack = []
    output["fontResources"] = []
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
                    font_stack.append(active_font)
                    state["graphicsDepth"] += 1
                    if state["graphicsDepth"] > 64:
                        raise ValueError("graphics_depth_exceeded")
                elif op == "Q":
                    active_font = font_stack.pop() if font_stack else None
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
                    font_reference = resolve1(resources.get("Font", {}))[literal_name(operands[0])]
                    font = resolve1(font_reference)
                    operation["fontSubtype"] = literal_name(font.get("Subtype"))
                    if operation["fontSubtype"] == "Type3":
                        _issue(output, "type3_font_content_unsupported")
                    active_font = f"page:{output['page']}:font:{literal_name(operands[0])}"
                    operation["fontResourceRef"] = active_font
                    if active_font not in fonts:
                        fonts[active_font] = _source_font(
                            font, active_font, output, usage, limits, deadline
                        )
                        font_record = fonts[active_font][0]
                        font_record["objectNumber"] = getattr(font_reference, "objid", None)
                        font_record["fingerprint"] = fingerprint(font_record)
                if op in TEXT:
                    operation["fontResourceRef"] = active_font
                operands = []
            item["status"] = "inspected"
        except Exception as error:
            item.update(status="unresolved", errorType=type(error).__name__, reason=str(error))
            _issue(output, "source_stream_unresolved", streamIndex=stream_index)
    if state["graphicsDepth"] or state["textOpen"]:
        _issue(output, "unbalanced_content_state")
    for record, _ in fonts.values():
        if not record["nativeLoadAllowed"]:
            _issue(
                output,
                "font_program_or_mapping_unverified",
                fontResourceRef=record["id"],
                reason=record.get("reason"),
            )
    return fonts


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


def _pdfium_objects(page, output, usage, limits, deadline, fonts):
    import pypdfium2.raw as raw

    height = output["pageSize"][1]
    source_clip_safe = not output["issues"]
    text_ops = iter(
        o for o in output["sourceOperators"] if o["operator"] in TEXT and _text_source_bytes(o)
    )
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
                source_clip_safe
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
                try:
                    _paint_text(
                        raw,
                        obj,
                        textpage,
                        item,
                        next(text_ops),
                        fonts,
                        usage,
                        limits,
                        deadline,
                        height,
                    )
                except Exception as error:
                    item.update(
                        paintBoundsStatus="unverified",
                        paintBoundsError=type(error).__name__,
                        paintBoundsReason=str(error),
                    )
                    _issue(output, "text_paint_bounds_unverified", objectRef=item["id"])
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


def _font_and_paint_valid(page):
    """Recheck retained support relationships, not original font bytes."""
    fonts = {f["id"]: f for f in page["fontResources"]}
    if len(fonts) != len(page["fontResources"]):
        return False
    for f in fonts.values():
        if f.get("fingerprint") != fingerprint(f):
            return False
    source = [o for o in page["sourceOperators"] if o["operator"] in TEXT and _text_source_bytes(o)]
    texts = [o for o in page["objects"] if o["pdfiumType"] == 1]
    if len(source) != len(texts):
        return False
    for op, obj in zip(source, texts, strict=True):
        font = fonts.get(op.get("fontResourceRef"), {})
        if (
            font.get("status") != "verified"
            or font.get("subtype") != "TrueType"
            or obj.get("fontResourceRef") != font.get("id")
            or obj.get("sourceOperatorSequence") != op["sequence"]
            or obj.get("paintBoundsStatus") != "verified"
            or obj.get("textRenderMode") != 0
        ):
            return False
        streams = font["streams"]
        if len(streams) != 2 or [s["role"] for s in streams] != ["FontFile2", "ToUnicode"]:
            return False
        for stream in streams:
            if (
                type(stream["decodedBytes"]) is not int
                or stream["decodedBytes"] <= 0
                or not re.fullmatch(r"[0-9a-f]{64}", stream["decodedSha256"])
            ):
                return False
        if obj.get("nativeFontSha256") != streams[0]["decodedSha256"]:
            return False
        tt = font["trueType"]
        offsets = tt["glyphOffsets"]
        if (
            tt["cmapFormat"] != 6
            or tt["cmapPlatform"] != [1, 0]
            or type(tt["numGlyphs"]) is not int
            or not 0 < tt["numGlyphs"] <= 4096
            or len(offsets) != tt["numGlyphs"] + 1
        ):
            return False
        if any(
            type(v) is not int or not 0 <= v <= tt["glyfBytes"] for v in offsets
        ) or offsets != sorted(offsets):
            return False
        mapping = font["codeToUnicode"]
        codes = [f"{c:02x}" for c in _text_source_bytes(op)]
        glyphs = obj["paintGlyphs"]
        if (
            not _membership_valid(obj)
            or len(glyphs) != len(codes)
            or obj["sourceText"] != "".join(chr(mapping[c]) for c in codes)
            or [g["textPageIndex"] for g in glyphs]
            != [c["textPageIndex"] for c in obj["nativeCharacters"] if c["apiGenerated"] == 0]
        ):
            return False
        hull = list(obj["bounds"])
        indices = []
        for ordinal, (code, g) in enumerate(zip(codes, glyphs, strict=True)):
            u = mapping[code]
            gid = tt["codeToGlyph"][code]
            if (
                type(u) is not int
                or not 0 < u <= 0xFFFF
                or list(mapping.values()).count(u) != 1
                or type(gid) is not int
                or not 0 <= gid < tt["numGlyphs"]
                or g["sourceOrdinal"] != ordinal
                or g["sourceCode"] != code
                or g["unicode"] != u
                or g["glyphId"] != gid
                or g["loca"] != offsets[gid : gid + 2]
            ):
                return False
            m, origin, size = g["matrix"], g["originBottomLeft"], g["fontSize"]
            if (
                len(m) != 6
                or len(origin) != 2
                or not _finite(m + origin + [size])
                or size <= 0
                or m[1] != 0
                or m[2] != 0
                or m[0] <= 0
                or m[3] <= 0
            ):
                return False
            if type(g["textPageIndex"]) is not int or g["textPageIndex"] < 0:
                return False
            indices.append(g["textPageIndex"])
            points, native = g["controlPoints"], g["nativeOutlinePoints"]
            empty = offsets[gid] == offsets[gid + 1]
            if empty:
                if g["basis"] != "source_loca_zero_length" or points or native:
                    return False
            else:
                if (
                    g["basis"] != "native_outline_control_hull"
                    or not native
                    or any(len(p) != 2 or not _finite(p) for p in native)
                ):
                    return False
                projected = [
                    [
                        origin[0] + p[0] * size * m[0],
                        page["pageSize"][1] - (origin[1] + p[1] * size * m[3]),
                    ]
                    for p in native
                ]
                if points != projected:
                    return False
                hull = [
                    min(hull[0], min(p[0] for p in points)),
                    min(hull[1], min(p[1] for p in points)),
                    max(hull[2], max(p[0] for p in points)),
                    max(hull[3], max(p[1] for p in points)),
                ]
        if indices != sorted(set(indices)) or obj.get("paintSupportBounds") != hull:
            return False
    return True


def _finish(page):
    used = {
        op.get("fontResourceRef")
        for op in page["sourceOperators"]
        if op["operator"] in TEXT and _text_source_bytes(op)
    }
    fonts = {f["id"]: f for f in page.get("fontResources", [])}
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
        "fontResourcesComplete": all(
            fonts.get(ref, {}).get("status") == "verified" for ref in used
        ),
        "nativeFontBytesLinked": all(
            o.get("nativeFontSha256")
            == next(
                (
                    s.get("decodedSha256")
                    for s in fonts.get(o.get("fontResourceRef"), {}).get("streams", [])
                    if s.get("role") == "FontFile2"
                ),
                None,
            )
            and o.get("nativeFontSha256") is not None
            for o in page["objects"]
            if o["pdfiumType"] == 1
        ),
        "textPaintBoundsVerified": all(
            o.get("paintBoundsStatus") == "verified"
            for o in page["objects"]
            if o["pdfiumType"] == 1
        ),
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
                "fontResourcesComplete",
                "nativeFontBytesLinked",
                "textPaintBoundsVerified",
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
                    fonts = _source_inventory(source_page, page, usage, selected_limits, deadline)
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
                            _pdfium_objects(pdf_page, page, usage, selected_limits, deadline, fonts)
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
        if not _font_and_paint_valid(entry):
            raise ValueError("native font or paint evidence inconsistent")
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
