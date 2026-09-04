"""Source-declared Hancom image references and bounded embedded-byte reads.

본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
No external links, document conversion, or inferred image placement are used.
"""

from __future__ import annotations

import posixpath
import re
import struct
import zipfile
import zlib
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

try:
    import olefile
except ModuleNotFoundError:  # Optional in a reduced OpenAI host runtime.
    if __package__:
        from ._vendor import olefile
    else:
        from _vendor import olefile


def normalized_clip(clip, dimensions):
    left, top, right, bottom = map(int, clip)
    width, height = map(int, dimensions)
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("unsupported Hancom image clip")
    return [left / width, top / height, right / width, bottom / height]


def hwp_binary_items(records, members, *, compressed):
    """BinData references are one-based DocInfo entries, not storage IDs."""
    names = {}
    for name in members:
        names.setdefault(name.casefold(), []).append(name)
    items = []
    for record, tag, _level, data in records:
        if tag != 0x12:
            continue
        item = {"bindata_record": record}
        items.append(item)
        if len(data) < 6:
            continue
        flags, identifier, length = struct.unpack_from("<HHH", data)
        if flags & 15 != 1 or (flags >> 4) & 3 == 3:
            continue
        if len(data) != 6 + length * 2:
            continue
        try:
            extension = data[6:].decode("utf-16-le")
        except UnicodeError:
            continue
        if not re.fullmatch(r"[A-Za-z0-9]{1,16}", extension):
            continue
        matches = names.get(f"BinData/BIN{identifier:04X}.{extension}".casefold(), [])
        if len(matches) == 1:
            item.update(
                image_parts=matches,
                image_compressed=compressed if (flags >> 4) & 3 == 0 else (flags >> 4) & 3 == 1,
            )
    return items


def hwp_picture(data, items, *, version):
    result = {"object_type": "image", "image_crop_unresolved": True}
    if len(data) < 73:
        return result
    reference = struct.unpack_from("<H", data, 71)[0]
    result["bindata_ref"] = reference
    if 0 < reference <= len(items):
        result.update(items[reference - 1])
    result["image_clip"] = list(struct.unpack_from("<4i", data, 44))
    # The fixed 5.0.3.4+ tail has a fixed dimension pair. Variable effect
    # payloads are not interpreted using this offset (nor using display size).
    # Brightness, contrast and transparency must stay neutral; a grayscale
    # effect keeps the stored pixel geometry, so only its rendering is skipped.
    if (
        version >= 0x05000304
        and len(data) in {90, 91}
        and struct.unpack_from("<I", data, 78)[0] == 0
        and data[68:70] == b"\0\0"
        and data[70] in {0, 1}
        and (len(data) == 90 or data[90] == 0)
    ):
        dimensions = list(struct.unpack_from("<II", data, 82))
        result["image_dimensions"] = dimensions
        result["image_effect"] = "REAL_PIC" if data[70] == 0 else "GRAY_SCALE"
        if data[70]:
            result["image_effect_applied"] = False
        try:
            result["source_crop_bbox"] = normalized_clip(result["image_clip"], dimensions)
            result["image_crop_unresolved"] = False
        except ValueError:
            pass
    return result


def hwpx_binary_items(package, members):
    counts = Counter(members)
    candidates = {}
    for node in package.iter():
        if node.tag.rsplit("}", 1)[-1] != "item":
            continue
        identifier, href = node.get("id"), node.get("href", "")
        matches = []
        if (
            node.get("isEmbeded") in {"1", "true"}
            and href
            and not href.startswith("/")
            and not any(c in href for c in (":", "\\", "\0", "?", "#"))
        ):
            # HWPX writers use either package-root or content.hpf-relative hrefs.
            for candidate in {
                href,
                posixpath.normpath(posixpath.join("Contents", href)),
            }:
                if (
                    candidate.startswith("BinData/")
                    and ".." not in candidate.split("/")
                    and counts[candidate] == 1
                ):
                    matches.append(candidate)
        candidates.setdefault(identifier, []).append(matches)
    return {
        key: groups[0]
        for key, groups in candidates.items()
        if key and len(groups) == 1 and len(groups[0]) == 1
    }


def hwpx_picture(node, items):
    result = {"image_crop_unresolved": True}
    children = {}
    for child in node:
        children.setdefault(child.tag.rsplit("}", 1)[-1], []).append(child)
    if any(len(children.get(key, [])) != 1 for key in ("img", "imgClip", "imgDim")):
        return result
    image, clip, dimensions = (children[key][0] for key in ("img", "imgClip", "imgDim"))
    reference = image.get("binaryItemIDRef")
    result.update(binary_item_ref=reference, image_parts=items.get(reference, []))
    effect = image.get("effect", "REAL_PIC")
    # A grayscale effect only changes rendering, so the stored pixel geometry and
    # the source correspondence stay provable. Other effects remain unresolved.
    if effect not in {"REAL_PIC", "GRAY_SCALE"} or any(
        image.get(k, "0") != "0" for k in ("bright", "contrast", "alpha")
    ):
        return result
    result["image_effect"] = effect
    if effect != "REAL_PIC":
        result["image_effect_applied"] = False
    try:
        result["image_clip"] = [int(clip.get(k)) for k in ("left", "top", "right", "bottom")]
        result["image_dimensions"] = [int(dimensions.get(k)) for k in ("dimwidth", "dimheight")]
        result["source_crop_bbox"] = normalized_clip(
            result["image_clip"], result["image_dimensions"]
        )
        result["image_crop_unresolved"] = False
    except (TypeError, ValueError):
        pass
    return result


class EmbeddedImageArchive:
    """Read a uniquely named ZIP part or CFB stream without following links."""

    def __init__(self, path):
        self.path = Path(path)
        self.stack = ExitStack()

    def __enter__(self):
        try:
            source = self.stack.enter_context(self.path.open("rb"))
            signature = source.read(8)
            source.seek(0)
            self.binary = signature == bytes.fromhex("d0cf11e0a1b11ae1")
            if self.binary:
                self.archive = self.stack.enter_context(olefile.OleFileIO(source))
                self.members = Counter(
                    "/".join(p) for p in self.archive.listdir(streams=True, storages=False)
                )
            else:
                self.archive = self.stack.enter_context(zipfile.ZipFile(source))
                self.members = Counter(self.archive.namelist())
            return self
        except Exception:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def size(self, part):
        if self.members[part] != 1:
            raise ValueError("embedded image member is missing or ambiguous")
        return self.archive.get_size(part) if self.binary else self.archive.getinfo(part).file_size

    def read(self, part, location, limit):
        if self.size(part) > limit:
            raise OverflowError("embedded image exceeds its byte budget")
        opener = self.archive.openstream if self.binary else self.archive.open
        with opener(part) as source:
            raw = source.read(limit + 1)
        if len(raw) > limit:
            raise OverflowError("embedded image exceeds its byte budget")
        if self.binary and location.get("image_compressed"):
            inflater = zlib.decompressobj(-15)
            try:
                raw = inflater.decompress(raw, limit + 1)
            except zlib.error as exc:
                raise ValueError("embedded image compression is invalid") from exc
            if len(raw) > limit or inflater.unconsumed_tail:
                raise OverflowError("inflated image exceeds its byte budget")
            trailer = inflater.unused_data
            if trailer and (
                len(trailer) != 8 or struct.unpack("<II", trailer) != (zlib.crc32(raw), len(raw))
            ):
                raise ValueError("embedded image compression trailer is invalid")
            if not inflater.eof:
                raise ValueError("embedded image compression is incomplete")
        return raw
