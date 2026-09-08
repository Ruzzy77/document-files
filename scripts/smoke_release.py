#!/usr/bin/env python3
"""Exercise an unpacked candidate without relying on a system Python or uv."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path


async def smoke_mcp(python: Path, root: Path, fixture: Path, env: dict) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=str(python),
        args=["-I", str(root / "launchers/run.py"), "mcp_server"],
        env=env,
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        listing = await session.list_tools()
        if "document_extract_file" not in {tool.name for tool in listing.tools}:
            raise ValueError("Packaged MCP extraction tool is missing")
        result = await session.call_tool("document_extract_file", {"path": str(fixture)})
        if result.is_error or not result.structured_content.get("ok"):
            raise ValueError("Packaged MCP extraction failed")
        if "001.2300" not in json.dumps(result.structured_content):
            raise ValueError("Packaged MCP extraction lost source text")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    desktop = args.archive.with_suffix(".mcpb")
    if desktop.exists():
        with zipfile.ZipFile(desktop) as package:
            manifest = json.loads(package.read("manifest.json"))
            if manifest["manifest_version"] != "0.3":
                raise ValueError("Unexpected MCPB manifest version")
            for option in manifest.get("user_config", {}).values():
                if any(not option.get(field) for field in ("type", "title", "description")):
                    raise ValueError("MCPB user configuration is missing a required field")
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
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "TEMP",
                "TMP",
                "HOME",
                "LOCALAPPDATA",
                "USERPROFILE",
                "HOMEDRIVE",
                "HOMEPATH",
            }
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
                encoding="utf-8",
                capture_output=True,
                check=True,
                timeout=60,
            )
            payload = json.loads(completed.stdout)
            if payload.get("ok") is False:
                raise ValueError("Candidate operation failed: " + operation[0])
        asyncio.run(asyncio.wait_for(smoke_mcp(python, root, fixture, env), timeout=60))
        if (
            fixture.read_text(encoding="utf-8")
            != "Document Files portable smoke\nAmount: 001.2300\n"
        ):
            raise ValueError("Source modified")
    print(
        "Packaged CLI and MCP extraction smoke passed; "
        "not a substitute for installed-client/model qualification."
    )


if __name__ == "__main__":
    main()
