"""Recover only an EMF that consists of one complete, unmodified RGB bitmap.

Layout follows Microsoft MS-EMF 2.3.1.7 (EMR_STRETCHDIBITS). Text, clipping,
transforms, palettes, raster combinations, and general vector rendering are not
approximated. Unsupported records continue to the existing format diagnostic.
"""

from __future__ import annotations

import struct


def single_bitmap_emf(raw):
    if len(raw) < 88 or raw[40:44] != b" EMF":
        return None
    header_type, header_size = struct.unpack_from("<II", raw)
    if (
        header_type != 1
        or header_size < 88
        or header_size > len(raw) - 100
        or struct.unpack_from("<II", raw, 48) != (len(raw), 3)
    ):
        return None
    kind, size = struct.unpack_from("<II", raw, header_size)
    end = header_size + size
    if kind != 81 or size < 120 or end + 20 != len(raw):
        return None
    if struct.unpack_from("<III", raw, end) != (14, 20, 0):
        return None
    record = memoryview(raw)[header_size:end]
    bounds = struct.unpack_from("<4i", raw, 8)
    if struct.unpack_from("<4i", record, 8) != bounds:
        return None
    x, y, sx, sy, sw, sh = struct.unpack_from("<6i", record, 24)
    info_offset, info_size, bits_offset, bits_size, usage, operation = struct.unpack_from(
        "<6I", record, 48
    )
    width, height = struct.unpack_from("<2i", record, 72)
    if (
        (x, y, x + width - 1, y + height - 1) != bounds
        or sx != 0
        or sy != 0
        or sw != width
        or sh != height
        or width <= 0
        or height <= 0
        or usage != 0
        or operation != 0x00CC0020
        or info_size != 40
        or info_offset < 80
        or info_offset + info_size > size
        or bits_offset < info_offset + info_size
        or bits_offset + bits_size > size
    ):
        return None
    info = record[info_offset : info_offset + info_size]
    header, bitmap_width, bitmap_height, planes, depth, compression, stored_size = (
        struct.unpack_from("<IiiHHII", info)
    )
    expected_size = ((width * depth + 31) // 32) * 4 * height
    if (
        header != 40
        or bitmap_width != width
        or bitmap_height != height
        or planes != 1
        or depth not in {24, 32}
        or compression != 0
        or bits_size != expected_size
        or stored_size not in {0, expected_size}
        or struct.unpack_from("<II", info, 32) != (0, 0)
    ):
        return None
    bitmap = (
        struct.pack("<2sIHHI", b"BM", 14 + info_size + bits_size, 0, 0, 14 + info_size)
        + bytes(info)
        + bytes(record[bits_offset : bits_offset + bits_size])
    )
    return bitmap, {
        "container_format": "emf",
        "record_offset": header_size,
        "record_type": "EMR_STRETCHDIBITS",
        "bitmap_info_offset": header_size + info_offset,
        "bitmap_bits_offset": header_size + bits_offset,
        "bitmap_bytes": bits_size,
        "basis": "single_full_canvas_rgb_bitmap_srccopy",
    }
