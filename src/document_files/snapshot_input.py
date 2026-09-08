"""Validated Windows child input transport. Also importable by standalone HWP child."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def verified_snapshot(source: dict, *, max_bytes: int):
    raw_path = source.get("path")
    size = source.get("byte_size")
    digest = source.get("sha256")
    if (
        not isinstance(raw_path, str)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 <= size <= max_bytes
        or not isinstance(digest, str)
        or not re.fullmatch("[a-f0-9]{64}", digest)
    ):
        raise ValueError("invalid snapshot identity")
    path = Path(raw_path)
    if (
        not path.is_absolute()
        or path.name != "input.bin"
        or not path.parent.name.startswith("document-files-input-")
        or path.parent.parent.resolve() != Path(tempfile.gettempdir()).resolve()
        or path.is_symlink()
        or path.parent.is_symlink()
    ):
        raise ValueError("invalid snapshot location")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError("snapshot size or file type mismatch")
        actual = hashlib.sha256()
        count = 0
        while chunk := os.read(fd, 64 * 1024):
            count += len(chunk)
            if count > max_bytes:
                raise ValueError("snapshot exceeds byte budget")
            actual.update(chunk)
        if count != size or actual.hexdigest() != digest:
            raise ValueError("snapshot digest mismatch")
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
    finally:
        os.close(fd)
