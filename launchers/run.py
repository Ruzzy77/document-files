"""Runtime entry point shared by CLI, plugins and MCPB. Never installs anything."""

import os
import runpy
import sys
from pathlib import Path

for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

root = Path(__file__).resolve().parent.parent
backend = root / "rhwp" / ("rhwp.exe" if os.name == "nt" else "rhwp")
if backend.is_file():
    os.environ.setdefault("DOCUMENT_FILES_RHWP", str(backend))
mode = sys.argv.pop(1)
if mode not in {"cli", "mcp_server"}:
    raise SystemExit("Unknown Document Files entry point")
runpy.run_module(f"document_files.{mode}", run_name="__main__")
