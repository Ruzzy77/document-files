#!/usr/bin/env python3
"""Build release candidates locally for the current platform; never publish.

Network is used only here to fetch checksum-pinned build inputs. Runtime entry
points do not provision Python, dependencies or backends while processing files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

from provision_rhwp import platform_key

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "*.egg-info")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archive_tree(source: Path, target: Path, *, prefix: str = "") -> None:
    """Dereference only internal links, retain executable bits, exclude caches."""
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if not path.resolve().is_relative_to(source.resolve()):
                raise ValueError(f"Archive contains escaping link: {path}")
            name = prefix + path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | stat.S_IMODE(path.stat().st_mode)) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def host_bundle(stage: Path) -> None:
    for name in (
        "src",
        "openai-runtime",
        "skills",
        "assets",
        "patches",
        ".claude-plugin",
        ".codex-plugin",
    ):
        shutil.copytree(ROOT / name, stage / name, ignore=IGNORE)
    for name in (
        "LICENSE",
        "NOTICE",
        "pyproject.toml",
        "uv.lock",
        ".mcp.json",
        "README.md",
        "DESIGN.md",
        "SCHEMA_EXTRACTION_DESIGN.md",
    ):
        shutil.copy2(ROOT / name, stage / name)
    (stage / "scripts").mkdir()
    for name in ("provision_rhwp.py", "build_patched_rhwp.py"):
        shutil.copy2(ROOT / "scripts" / name, stage / "scripts" / name)


def skill_bundle(stage: Path) -> None:
    shutil.copytree(ROOT / "skills/document-files", stage, ignore=IGNORE)
    runtime = stage / "scripts/document-files"
    shutil.copytree(ROOT / "openai-runtime", runtime, ignore=IGNORE)
    shutil.copytree(ROOT / "src/document_files", runtime / "src/document_files", ignore=IGNORE)
    (stage / "assets").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "assets/icon.png", stage / "assets/icon.png")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, stage / name)
    skill = stage / "SKILL.md"
    body = skill.read_text(encoding="utf-8")
    body = body.replace(
        "${SKILL_DIR}/../../runtime/document-files/document-files",
        "${SKILL_DIR}/scripts/document-files/document-files",
    )
    skill.write_text(body, encoding="utf-8")


def command(*args: object, **kwargs) -> None:
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--rhwp",
        type=Path,
        required=True,
        help="Patched rhwp executable built by build_patched_rhwp.py",
    )
    parser.add_argument("--rhwp-license", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    target = platform_key()
    pins = json.loads((ROOT / "scripts/python-runtimes.json").read_text())
    pin = pins["targets"][target]
    wheel = output / f"document_files-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise SystemExit("Build the wheel/sdist first: uv build --out-dir " + str(output))
    backend_version = subprocess.check_output([str(args.rhwp), "--version"], text=True).strip()
    if backend_version != "rhwp v0.8.6+pat.checkbox.1":
        raise SystemExit("A verified checkbox-patched rhwp build is required")
    build_metadata = args.rhwp.parent.parent / "build.json"
    provenance = json.loads(build_metadata.read_text())
    if (
        provenance["binarySha256"] != sha(args.rhwp)
        or provenance["patchSha256"] != sha(ROOT / "patches/rhwp/checkbox-preservation.patch")
        or provenance["baseCommit"] != "f1f9c6ae58344ee9368996d3543f76b9345cf227"
    ):
        raise SystemExit("rhwp build metadata does not match the binary/source patch")
    with tempfile.TemporaryDirectory(prefix="document-files-release-") as folder:
        work = Path(folder)
        stage = work / "document-files"
        stage.mkdir()
        download = work / "python.tar.gz"
        urllib.request.urlretrieve(pin["url"], download)  # build-time HTTPS with pinned digest
        if sha(download) != pin["sha256"]:
            raise SystemExit("Python archive checksum mismatch")
        with tarfile.open(download) as archive:
            archive.extractall(stage, filter="data")
        python = stage / ("python/python.exe" if os.name == "nt" else "python/bin/python3")
        actual = subprocess.check_output(
            [str(python), "-I", "-c", "import platform; print(platform.python_version())"],
            text=True,
        ).strip()
        if actual != pins["pythonVersion"]:
            raise SystemExit("Packaged Python version mismatch")
        requirements = work / "requirements.txt"
        command(
            args.uv,
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            requirements,
            cwd=ROOT,
        )
        command(
            args.uv,
            "pip",
            "install",
            "--python",
            python,
            "--require-hashes",
            "--no-deps",
            "-r",
            requirements,
        )
        command(args.uv, "pip", "install", "--python", python, "--no-deps", wheel)
        # No pip cache, user credentials or project workspace is copied into this tree.
        for name in ("launchers", "assets", "skills", ".claude-plugin", ".codex-plugin"):
            shutil.copytree(ROOT / name, stage / name, ignore=IGNORE)
        for name in ("LICENSE", "NOTICE", "README.md"):
            shutil.copy2(ROOT / name, stage / name)
        shutil.copy2(requirements, stage / "DEPENDENCIES.txt")
        shutil.copytree(ROOT / "scripts/python-licenses", stage / "python-licenses")
        (stage / "rhwp").mkdir()
        shutil.copy2(args.rhwp, stage / "rhwp" / args.rhwp.name)
        shutil.copy2(args.rhwp_license, stage / "rhwp/LICENSE")
        shutil.copy2(build_metadata, stage / "rhwp/build.json")
        # Keep PBS and wheel dist-info licenses in-place; additionally inventory every package.
        command(
            python,
            "-I",
            "-c",
            "import importlib.metadata,json,sys; json.dump(sorted("
            '[{"name":d.metadata["Name"],"version":d.version,"license":'
            'd.metadata.get("License-Expression") or d.metadata.get("License")} '
            'for d in importlib.metadata.distributions()], key=lambda x:x["name"]),'
            'open(sys.argv[1],"w"),indent=2)',
            stage / "DEPENDENCIES.json",
        )
        binary = "python/python.exe" if os.name == "nt" else "python/bin/python3"
        launch = {
            "command": "${CLAUDE_PLUGIN_ROOT}/" + binary,
            "args": ["-I", "${CLAUDE_PLUGIN_ROOT}/launchers/run.py", "mcp_server"],
        }
        write_json(stage / ".mcp.json", {"mcpServers": {"document-files": launch}})
        # Skill CLI points to the release launcher, not a Toolkit sibling directory.
        skill = stage / "skills/document-files/SKILL.md"
        body = skill.read_text(encoding="utf-8").replace(
            "${SKILL_DIR}/../../runtime/document-files/document-files",
            "${SKILL_DIR}/../../launchers/document-files" + (".cmd" if os.name == "nt" else ""),
        )
        skill.write_text(body, encoding="utf-8")
        write_json(
            stage / "BUILD.json",
            {
                "version": version,
                "target": target,
                "python": pin,
                "wheelSha256": sha(wheel),
                "rhwp": provenance,
                "qualification": "pending-external-client-and-model-evidence",
                "sourceCommit": commit,
                "dirtySource": dirty,
            },
        )
        command(python, "-I", stage / "launchers/run.py", "cli", "capabilities")
        archive_tree(
            stage, output / f"document-files-{version}-{target}.zip", prefix="document-files/"
        )
        archive_tree(stage, output / f"document-files-{version}-{target}-claude-code.zip")
        # Codex resolves an explicit relative cwd against the installed plugin root.
        # Its native MCP parser does not interpolate a plugin-root variable.
        codex_launch = (
            {
                "command": "cmd.exe",
                "args": ["/d", "/c", "launchers\\document-files-mcp.cmd"],
                "cwd": ".",
            }
            if os.name == "nt"
            else {"command": "/bin/sh", "args": ["./launchers/document-files-mcp"], "cwd": "."}
        )
        write_json(stage / ".mcp.json", {"mcpServers": {"document-files": codex_launch}})
        archive_tree(stage, output / f"document-files-{version}-{target}-codex.zip")
        if target != "linux-x86_64":
            manifest = {
                "manifest_version": "0.3",
                "name": "document-files",
                "version": version,
                "description": "AI-assisted document schema extraction",
                "author": {"name": "Ruzzy77"},
                "license": "Apache-2.0",
                "server": {
                    "type": "binary",
                    "entry_point": binary,
                    "mcp_config": {
                        "command": "${__dirname}/" + binary,
                        "args": ["-I", "${__dirname}/launchers/run.py", "mcp_server"],
                    },
                },
                "compatibility": {"platforms": ["win32" if os.name == "nt" else "darwin"]},
            }
            manifest["user_config"] = {
                "ai_endpoint": {
                    "type": "string",
                    "title": "Model endpoint",
                    "description": "Explicit Chat Completions endpoint; optional for native tools",
                    "required": False,
                    "default": "",
                },
                "ai_model": {
                    "type": "string",
                    "title": "Model name",
                    "required": False,
                    "default": "",
                },
                "ai_key": {
                    "type": "string",
                    "title": "API key",
                    "sensitive": True,
                    "required": False,
                    "default": "",
                },
            }
            manifest["server"]["mcp_config"]["env"] = {
                "DOCUMENT_FILES_AI_ENDPOINT": "${user_config.ai_endpoint}",
                "DOCUMENT_FILES_AI_MODEL": "${user_config.ai_model}",
                "DOCUMENT_FILES_AI_API_KEY": "${user_config.ai_key}",
            }
            write_json(stage / "manifest.json", manifest)
            archive_tree(stage, output / f"document-files-{version}-{target}.mcpb")
        host = work / "host"
        host.mkdir()
        host_bundle(host)
        write_json(
            host / "BUILD.json",
            {
                "version": version,
                "sourceCommit": commit,
                "dirtySource": dirty,
                "kind": "host-source",
            },
        )
        archive_tree(host, output / f"document-files-{version}-host.zip", prefix="document-files/")
        skill_bundle(work / "skill")
        archive_tree(work / "skill", output / f"document-files-{version}.skill")
    artifacts = {
        p.name: sha(p)
        for p in sorted(output.iterdir())
        if p.is_file() and p.suffix in {".zip", ".mcpb", ".skill", ".whl", ".gz"}
    }
    write_json(
        output / f"artifacts-{target}.json",
        {
            "version": version,
            "target": target,
            "sourceCommit": commit,
            "dirtySource": dirty,
            "artifacts": artifacts,
        },
    )
    (output / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in artifacts.items()))


if __name__ == "__main__":
    main()
