"""Compare native checkbox semantics independently of rhwp's shared IR.

Keep this guard when retiring the downstream rhwp build: a parser and its own
round-trip comparison can agree after both have already lost a source property.
"""

from __future__ import annotations

import struct
from pathlib import Path
from zipfile import ZipFile

from defusedxml import ElementTree as ET

from .hwp5_adapter_main import HWPAdapterError, _inflate_raw_deflate, _records, olefile

_LIMIT = 32 * 1024 * 1024


def _native_definitions(data: bytes) -> tuple[list[dict], dict[int, dict]]:
    bullets, shapes = {}, []
    for _, tag, _, payload in _records(data, max_records=100_000):
        if tag == 24:
            if len(payload) < 14:
                raise HWPAdapterError("truncated bullet record in preservation check")
            attr = struct.unpack_from("<I", payload)[0]
            if attr & 32 and len(payload) not in (24, 25):
                raise HWPAdapterError("unsupported checkbox record length for preservation check")
            bullets[len(bullets) + 1] = {
                "checkable": bool(attr & 32),
                "char": payload[12:14].decode("utf-16-le"),
                "checkedChar": payload[-2:].decode("utf-16-le") if attr & 32 else "",
            }
        elif tag == 25:
            if len(payload) < 42:
                raise HWPAdapterError("truncated paragraph shape in preservation check")
            attr1 = struct.unpack_from("<I", payload)[0]
            attr2 = struct.unpack_from("<I", payload, 42)[0] if len(payload) >= 46 else 0
            shapes.append(
                {
                    "bullet": struct.unpack_from("<H", payload, 30)[0]
                    if (attr1 >> 23) & 3 == 3
                    else None,
                    "checked": bool(attr2 & 128),
                    "keepLines": bool(attr1 & (1 << 18)),
                }
            )
    return shapes, bullets


def _stream(compound, name: str, compressed: bool) -> bytes:
    data = compound.openstream(name).read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise HWPAdapterError("checkbox preservation stream exceeds budget")
    return _inflate_raw_deflate(data, limit=_LIMIT) if compressed else data


def native_checkboxes(path: Path) -> list[dict]:
    result = []
    with olefile.OleFileIO(path) as compound:
        header = compound.openstream("FileHeader").read(40)
        compressed = bool(struct.unpack_from("<I", header, 36)[0] & 1)
        shapes, bullets = _native_definitions(_stream(compound, "DocInfo", compressed))
        sections = sorted(
            (
                p
                for p in compound.listdir()
                if len(p) == 2 and p[0] == "BodyText" and p[1].startswith("Section")
            ),
            key=lambda p: int(p[1][7:]),
        )
        for section in sections:
            for _, tag, _, data in _records(
                _stream(compound, "/".join(section), compressed), max_records=1_000_000
            ):
                if tag != 66:
                    continue
                shape = shapes[struct.unpack_from("<H", data, 8)[0]]
                bullet = bullets.get(shape["bullet"])
                if bullet and bullet["checkable"]:
                    result.append(
                        {
                            "section": section[1],
                            "checked": shape["checked"],
                            "keepLines": shape["keepLines"],
                            "char": bullet["char"],
                            "checkedChar": bullet["checkedChar"],
                        }
                    )
    return result


def hwpx_checkboxes(path: Path) -> list[dict]:
    result = []
    with ZipFile(path) as package:

        def read_xml(name):
            if package.getinfo(name).file_size > _LIMIT:
                raise HWPAdapterError("checkbox preservation XML exceeds budget")
            return ET.fromstring(package.read(name))

        header = read_xml("Contents/header.xml")
        bullets = {e.get("id"): e for e in header.findall(".//{*}bullet")}
        shapes = {e.get("id"): e for e in header.findall(".//{*}paraPr")}
        names = sorted(
            (
                n
                for n in package.namelist()
                if n.startswith("Contents/section") and n.endswith(".xml") and n[16:-4].isdigit()
            ),
            key=lambda n: int(n[16:-4]),
        )
        for name in names:
            for para in read_xml(name).iter():
                if para.tag.rsplit("}", 1)[-1] != "p":
                    continue
                shape = shapes[para.get("paraPrIDRef")]
                heading = shape.find("{*}heading")
                if heading is None or heading.get("type") != "BULLET":
                    continue
                bullet = bullets[heading.get("idRef")]
                head = bullet.find("{*}paraHead")
                if head is None or head.get("checkable") not in ("1", "true"):
                    continue
                breaks = shape.find("{*}breakSetting")
                result.append(
                    {
                        "section": "Section" + name[16:-4],
                        "checked": shape.get("checked") in ("1", "true"),
                        "keepLines": breaks is not None
                        and breaks.get("keepLines") in ("1", "true"),
                        "char": bullet.get("char"),
                        "checkedChar": bullet.get("checkedChar", ""),
                    }
                )
    return result


def compare_checkboxes(source: Path, output: Path) -> dict:
    before, after = native_checkboxes(source), hwpx_checkboxes(output)
    return {
        "preserved": before == after,
        "sourceCount": len(before),
        "outputCount": len(after),
        "sourceChecked": sum(item["checked"] for item in before),
        "outputChecked": sum(item["checked"] for item in after),
    }
