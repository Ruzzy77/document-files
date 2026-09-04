"""Read stored EMF Unicode strings, without replaying graphics or joining runs.

MS-EMF 2.3.5.8 and 2.2.5 define the record-relative UTF-16LE string address.
Drawing order, logical reference points and clipping do not establish reading
order or visible text. Glyph-index, ANSI and other text encodings stay unread.
"""

from __future__ import annotations

import struct


def stored_emf_strings(raw):
    """Return source-addressed records only after the complete EMF walk validates.

    None leaves the existing unsupported-format diagnosis intact. The caller
    owns image byte/time budgets; the string result is also bounded here.
    """
    if len(raw) < 108 or raw[40:44] != b" EMF":
        return None
    kind, header_size = struct.unpack_from("<II", raw)
    byte_count, record_count = struct.unpack_from("<II", raw, 48)
    if (
        kind != 1
        or not 88 <= header_size <= len(raw) - 20
        or header_size % 4
        or byte_count != len(raw)
        or not 2 <= record_count <= 200_000
    ):
        return None
    result = []
    offset = header_size
    characters = 0
    for ordinal in range(1, record_count):
        if offset + 8 > len(raw):
            return None
        record_type, size = struct.unpack_from("<II", raw, offset)
        end = offset + size
        if size < 8 or size % 4 or end > len(raw) or record_type == 1:
            return None
        if record_type == 14:
            if ordinal != record_count - 1 or size < 20 or end != len(raw):
                return None
            return result
        if record_type == 84:
            if size < 56:
                return None
            count, start, options = struct.unpack_from("<III", raw, offset + 44)
            # Only the verified no-options Unicode form is read. In particular,
            # ETO_GLYPH_INDEX is a font lookup, never a Unicode code point.
            if options == 0 and count:
                if size < 76 or start < 76 or start % 2 or start + count * 2 > size:
                    return None
                if count > 50_000 or characters + count > 2_000_000 or len(result) >= 20_000:
                    return None
                try:
                    text = raw[offset + start : offset + start + count * 2].decode("utf-16-le")
                except UnicodeError:
                    return None
                characters += count
                result.append(
                    (
                        text,
                        {
                            "container_format": "emf",
                            "record_type": "EMR_EXTTEXTOUTW",
                            "record_ordinal": ordinal,
                            "record_offset": offset,
                            "record_bytes": size,
                            "string_offset": offset + start,
                            "utf16_code_units": count,
                            "encoding": "utf-16-le",
                            "basis": "stored_unicode_string_not_rendered_text",
                        },
                    )
                )
        offset = end
    return None
