#!/usr/bin/env python3
"""Exercise an unpacked candidate without relying on a system Python or uv."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="Document Files isolated smoke ") as temporary:
        work = Path(temporary)
        with zipfile.ZipFile(args.archive) as archive:
            for member in archive.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe candidate member")
                extracted = Path(archive.extract(member, work))
                extracted.chmod((member.external_attr >> 16) & 0o777)
        root = work / "document-files"
        python = root / ("python/python.exe" if os.name == "nt" else "python/bin/python3")
        # Do not expose the host PATH/PYTHONPATH, package manager or model credentials.
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "LOCALAPPDATA"}
        }
        env.update(PATH="", PYTHONNOUSERSITE="1")
        fixture = work / "input with spaces.txt"
        fixture.write_text("Document Files portable smoke\nAmount: 001.2300\n", encoding="utf-8")
        for operation in (["capabilities"], ["inspect", str(fixture)], ["extract", str(fixture)]):
            completed = subprocess.run(
                [str(python), "-I", str(root / "launchers/run.py"), "cli", *operation],
                env=env,
                cwd=work,
                text=True,
                capture_output=True,
                check=True,
                timeout=60,
            )
            payload = json.loads(completed.stdout)
            if payload.get("ok") is False:
                raise ValueError("Candidate operation failed: " + operation[0])
        if fixture.read_text() != "Document Files portable smoke\nAmount: 001.2300\n":
            raise ValueError("Source modified")
    print("Packaged CLI smoke passed; not a substitute for installed-client/model qualification.")


if __name__ == "__main__":
    main()
