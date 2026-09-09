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
        "docs",
        "deployment",
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
        "CHANGELOG.md",
        "SECURITY.md",
        "CONTRIBUTING.md",
        "SUPPORT.md",
    ):
        shutil.copy2(ROOT / name, stage / name)
    (stage / "scripts").mkdir()
    for name in ("provision_rhwp.py", "build_patched_rhwp.py", "linux_abi.py"):
        shutil.copy2(ROOT / "scripts" / name, stage / "scripts" / name)


def portable_skill(body: str, *, windows: bool) -> str:
    start = body.index("- ChatGPT 또는 원격 Codex에서는")
    end = body.index("- 파일 작업에 필요한 실행 기능", start)
    body = (
        body[:start] + "- 이 배포본에는 로컬 Python 실행 환경이 포함되어 있다. "
        "ChatGPT 원격 실행에는 별도 `.skill` 배포본을 사용한다.\n" + body[end:]
    )
    start = body.index("배포 진입점은 다음처럼 호출한다.")
    end = body.index("\n```", body.index("```sh", start)) + len("\n```")
    example = (
        '```bat\n"${SKILL_DIR}/../../launchers/document-files.cmd" capabilities\n```'
        if windows
        else '```sh\nsh "${SKILL_DIR}/../../launchers/document-files" capabilities\n```'
    )
    return body[:start] + "포함된 실행기는 다음처럼 호출한다.\n\n" + example + body[end:]


def skill_bundle(stage: Path) -> None:
    shutil.copytree(ROOT / "skills/document-files", stage, ignore=IGNORE)
    runtime = stage / "scripts/document-files"
    shutil.copytree(ROOT / "openai-runtime", runtime, ignore=IGNORE)
    shutil.copytree(ROOT / "src/document_files", runtime / "src/document_files", ignore=IGNORE)
    (stage / "assets").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "assets/icon.png", stage / "assets/icon.png")
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(ROOT / name, stage / name)
    (stage / "agents").mkdir(exist_ok=True)
    (stage / "agents/openai.yaml").write_text(
        'interface:\n  display_name: "Document Files"\n'
        '  short_description: "문서·표·발표 자료를 읽고 만들고 편집합니다"\n'
        '  icon_small: "./assets/icon.png"\n  icon_large: "./assets/icon.png"\n'
        '  brand_color: "#E86D5B"\n'
        '  default_prompt: "Use $document-files to read, create, or edit this document."\n'
        "policy:\n  allow_implicit_invocation: true\n",
        encoding="utf-8",
    )
    skill = stage / "SKILL.md"
    body = skill.read_text(encoding="utf-8")
    body = body.replace(
        "${SKILL_DIR}/../../runtime/document-files/document-files",
        "${SKILL_DIR}/scripts/document-files/document-files",
    )
    skill.write_text(body, encoding="utf-8")


def command(*args: object, **kwargs) -> None:
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def prepare_output(output: Path) -> None:
    """Never blend fresh artifacts with a previous same-version build."""
    if output.is_symlink() or (
        output.exists() and any(path.name != ".gitignore" for path in output.iterdir())
    ):
        raise ValueError("Release output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)


def source_identity(*, development: bool) -> tuple[str, bool]:
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True))
    if dirty and not development:
        raise ValueError("Stable candidates require clean source; use --development for local work")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    for name in (".codex-plugin", ".claude-plugin"):
        if json.loads((ROOT / name / "plugin.json").read_text())["version"] != version:
            raise ValueError("Plugin and Python versions must match")
    return commit, dirty


def source_fingerprint() -> str:
    """Detect even edits that leave an already-dirty status unchanged."""
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    digest = hashlib.sha256()
    for name in sorted(set(names) - {""}):
        path = ROOT / name
        digest.update(name.encode())
        if path.is_symlink():
            digest.update(b"link:" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(sha(path).encode())
        else:
            digest.update(b"missing")
    return digest.hexdigest()


def verify_native_target(binary: Path, target: str) -> None:
    if target.startswith("linux-"):
        from linux_abi import inspect_header

        inspect_header(binary.resolve(strict=True), target)


def verify_python_runtime(python: Path, target: str, expected_version: str) -> dict:
    verify_native_target(python, target)
    actual = json.loads(
        subprocess.check_output(
            [
                str(python),
                "-I",
                "-c",
                "import json,platform; print(json.dumps({"
                "'version':platform.python_version(),'machine':platform.machine()}))",
            ],
            text=True,
        )
    )
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}.get(
        actual.get("machine", "").casefold()
    )
    if actual.get("version") != expected_version or machine != target.split("-", 1)[1]:
        raise SystemExit("Packaged Python version/architecture mismatch")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--development",
        action="store_true",
        help="Allow dirty source for explicitly unqualified local candidates",
    )
    parser.add_argument(
        "--rhwp",
        type=Path,
        required=True,
        help="Patched rhwp executable built by build_patched_rhwp.py",
    )
    parser.add_argument("--rhwp-license", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument(
        "--python-archive",
        type=Path,
        help="Reuse a local Python archive; the platform's pinned SHA256 is still required",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    prepare_output(output)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    commit, dirty = source_identity(development=args.development)
    fingerprint = source_fingerprint()
    target = platform_key()
    pins = json.loads((ROOT / "scripts/python-runtimes.json").read_text())
    pin = pins["targets"][target]
    wheel = output / f"document_files-{version}-py3-none-any.whl"
    # Build from this source in the isolated output, never accept a pre-existing wheel.
    command(args.uv, "build", "--out-dir", output, cwd=ROOT)
    verify_native_target(args.rhwp, target)
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
        if args.python_archive:
            shutil.copyfile(args.python_archive, download)
        else:
            # Build-time acquisition only, with a bounded connection and pinned digest.
            with (
                urllib.request.urlopen(pin["url"], timeout=60) as response,
                download.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
        if sha(download) != pin["sha256"]:
            raise SystemExit("Python archive checksum mismatch")
        with tarfile.open(download) as archive:
            archive.extractall(stage, filter="data")
        python = stage / ("python/python.exe" if os.name == "nt" else "python/bin/python3")
        python_runtime = verify_python_runtime(python, target, pins["pythonVersion"])
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
        for name in (
            "launchers",
            "assets",
            "skills",
            "docs",
            "deployment",
            ".claude-plugin",
            ".codex-plugin",
        ):
            shutil.copytree(ROOT / name, stage / name, ignore=IGNORE)
        for name in (
            "LICENSE",
            "NOTICE",
            "README.md",
            "CHANGELOG.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "SUPPORT.md",
        ):
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
        body = portable_skill(skill.read_text(encoding="utf-8"), windows=os.name == "nt")
        skill.write_text(body, encoding="utf-8")
        write_json(
            stage / "BUILD.json",
            {
                "version": version,
                "target": target,
                "python": pin,
                "pythonRuntime": python_runtime,
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
        if not target.startswith("linux-"):
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
                "server_url": {
                    "type": "string",
                    "title": "Document Files service URL",
                    "description": "Running service for managed CPU jobs; never auto-started",
                    "required": False,
                    "default": "http://127.0.0.1:8765",
                },
                "server_token": {
                    "type": "string",
                    "title": "Document Files service token",
                    "description": "Bearer token for the configured Document Files service",
                    "sensitive": True,
                    "required": False,
                    "default": "",
                },
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
                    "description": "Model identifier served by the configured endpoint",
                    "required": False,
                    "default": "",
                },
                "ai_key": {
                    "type": "string",
                    "title": "API key",
                    "description": "Optional secret used only for the configured model endpoint",
                    "sensitive": True,
                    "required": False,
                    "default": "",
                },
            }
            manifest["server"]["mcp_config"]["env"] = {
                "DOCUMENT_FILES_SERVER_URL": "${user_config.server_url}",
                "DOCUMENT_FILES_SERVER_TOKEN": "${user_config.server_token}",
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
    if (
        source_identity(development=args.development) != (commit, dirty)
        or source_fingerprint() != fingerprint
    ):
        raise ValueError("Source identity changed while building")
    artifacts = {
        p.name: sha(p)
        for p in sorted(output.iterdir())
        if p.is_file() and p.suffix in {".zip", ".mcpb", ".skill", ".whl", ".gz"}
    }
    write_json(
        output / f"artifacts-{target}.json",
        {
            "schemaVersion": "document-files.build-inventory.v2",
            "version": version,
            "target": target,
            "sourceCommit": commit,
            "dirtySource": dirty,
            "candidateMode": "development" if args.development else "stable",
            "artifacts": artifacts,
        },
    )
    (output / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in artifacts.items()))


if __name__ == "__main__":
    main()
