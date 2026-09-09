"""Private content-addressed storage for native reconstruction part payloads only.

Link rows, not user-visible dictionary shapes, identify stored references. Nothing
outside reconstructionContext.parts[*].text/data is interpreted or rewritten.
All writes use the caller's result/checkpoint transaction and the same job database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from ..engine import DocumentFilesError

MIN_RESOURCE_BYTES = 4096
_REFERENCE = "$documentFilesResource"


def initialize(connection: sqlite3.Connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS resource_blobs (
        digest TEXT PRIMARY KEY, body BLOB NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS resource_links (
        owner TEXT NOT NULL CHECK(owner IN ('result','checkpoint')),
        part_index INTEGER NOT NULL CHECK(part_index >= 0),
        slot TEXT NOT NULL CHECK(slot IN ('text','data')),
        digest TEXT NOT NULL,
        PRIMARY KEY(owner,part_index,slot))""")


def _invalid():
    return DocumentFilesError(
        "extraction-store-invalid", "Stored reconstruction resource is invalid."
    )


def _parts(result):
    context = result.get("reconstructionContext") if isinstance(result, dict) else None
    return context.get("parts") if isinstance(context, dict) else None


def _copy_parts(result, parts):
    # Copy only the storage-rewritten path. Native contents and the rest of the
    # result are not mutated, duplicated recursively or interpreted as sentinels.
    output = dict(result)
    output["reconstructionContext"] = dict(result["reconstructionContext"])
    copied = [dict(part) if isinstance(part, dict) else part for part in parts]
    output["reconstructionContext"]["parts"] = copied
    return output, copied


def store(connection: sqlite3.Connection, result: dict | None, *, owner: str):
    if owner not in {"result", "checkpoint"}:
        raise ValueError("invalid_resource_owner")
    initialize(connection)
    connection.execute("DELETE FROM resource_links WHERE owner=?", (owner,))
    parts = _parts(result)
    if not isinstance(parts, list):
        return result
    output, copied = _copy_parts(result, parts)
    for index, part in enumerate(copied):
        if not isinstance(part, dict):
            continue
        for slot in ("text", "data"):
            value = part.get(slot)
            if not isinstance(value, str):
                continue
            # JSON string encoding also preserves literal sentinel-looking text,
            # exact Unicode strings and text/data slots with the same contents.
            try:
                raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except UnicodeEncodeError:
                raw = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if len(raw) < MIN_RESOURCE_BYTES:
                continue
            digest = hashlib.sha256(raw).hexdigest()
            previous = connection.execute(
                "SELECT body FROM resource_blobs WHERE digest=?", (digest,)
            ).fetchone()
            if previous is not None and previous[0] != raw:
                raise _invalid()
            if previous is None:
                connection.execute("INSERT INTO resource_blobs VALUES (?,?)", (digest, raw))
            connection.execute(
                "INSERT INTO resource_links VALUES (?,?,?,?)", (owner, index, slot, digest)
            )
            part[slot] = {_REFERENCE: digest}
    return output


def restore(connection: sqlite3.Connection, result: dict | None, *, owner: str):
    # Read-only compatibility: legacy databases are not migrated or rewritten.
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_links'"
    ).fetchone()
    if not exists:
        return result
    links = connection.execute(
        "SELECT part_index,slot,digest FROM resource_links WHERE owner=?", (owner,)
    ).fetchall()
    if not links:
        return result
    parts = _parts(result)
    if not isinstance(parts, list):
        raise _invalid()
    output, copied = _copy_parts(result, parts)
    cache = {}
    for index, slot, digest in links:
        if (
            type(index) is not int
            or not 0 <= index < len(copied)
            or slot not in {"text", "data"}
            or not isinstance(copied[index], dict)
            or copied[index].get(slot) != {_REFERENCE: digest}
        ):
            raise _invalid()
        if digest not in cache:
            row = connection.execute(
                "SELECT body FROM resource_blobs WHERE digest=?", (digest,)
            ).fetchone()
            if (
                not row
                or not isinstance(row[0], bytes)
                or hashlib.sha256(row[0]).hexdigest() != digest
            ):
                raise _invalid()
            try:
                value = json.loads(row[0])
            except (ValueError, UnicodeDecodeError):
                raise _invalid() from None
            if not isinstance(value, str):
                raise _invalid()
            cache[digest] = value
        copied[index][slot] = cache[digest]
    return output


def prune(connection: sqlite3.Connection):
    connection.execute(
        "DELETE FROM resource_blobs WHERE digest NOT IN (SELECT digest FROM resource_links)"
    )
