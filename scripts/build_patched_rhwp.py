#!/usr/bin/env python3
"""Build and install the pinned downstream patch; never run during document work."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from provision_rhwp import cache_root, platform_key

BASE_COMMIT = "f1f9c6ae58344ee9368996d3543f76b9345cf227"
VERSION = "0.8.6+pat.checkbox.1"
PATCH = Path(__file__).resolve().parents[1] / "patches/rhwp/checkbox-preservation.patch"


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def rustc_version(source, env):
    # rustup resolves the source's pinned toolchain relative to its working
    # directory, just as in the preceding Cargo invocation. Respect Cargo's
    # explicit RUSTC override when one is present instead of reporting a proxy.
    return subprocess.check_output(
        [env.get("RUSTC") or "rustc", "--version", "--verbose"],
        cwd=source,
        env=env,
        text=True,
    ).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="New clone directory; existing files are never reset",
    )
    parser.add_argument("--cargo", default="cargo")
    parser.add_argument(
        "--output", type=Path, help="New private output directory instead of user cache"
    )
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("output already exists; choose a new directory")
    source = args.source.expanduser().resolve()
    if source.exists():
        parser.error("source already exists; use a fresh directory to avoid losing changes")
    run(
        "git",
        "clone",
        "--filter=blob:none",
        "--no-checkout",
        "https://github.com/edwardkim/rhwp.git",
        str(source),
    )
    run("git", "checkout", "--detach", BASE_COMMIT, cwd=source)
    run("git", "apply", "--check", str(PATCH), cwd=source)
    run("git", "apply", str(PATCH), cwd=source)
    build_env = os.environ.copy()
    linux_toolchain = None
    if platform_key() == "linux-x86_64":
        from linux_abi import CC, CXX, toolchain

        linux_toolchain = toolchain()
        build_env.update(CC=CC, CXX=CXX)
        build_env["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER"] = CC
    run(args.cargo, "build", "--locked", "--release", "--bin", "rhwp", cwd=source, env=build_env)
    linux_abi = None
    if platform_key() == "linux-x86_64":
        from linux_abi import audit

        linux_abi = audit(source / "target/release/rhwp", source / "rhwp-abi.txt")
    name = "rhwp.exe" if os.name == "nt" else "rhwp"
    built = source / "target/release" / name
    output = subprocess.check_output([built, "--version"], text=True).strip()
    if output != f"rhwp v{VERSION}":
        raise RuntimeError(f"unexpected build identity: {output}")
    destination = (
        (args.output.resolve() / "bin")
        if args.output
        else cache_root() / "rhwp" / f"v{VERSION}" / platform_key() / "bin"
    )
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as staged:
        staged.write(built.read_bytes())
        staged_path = Path(staged.name)
    staged_path.chmod(0o755)
    staged_path.replace(destination / name)
    metadata = {
        "version": VERSION,
        "baseCommit": BASE_COMMIT,
        "patchSha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
        "binarySha256": hashlib.sha256(built.read_bytes()).hexdigest(),
        "linuxAbi": linux_abi,
        "linuxToolchain": linux_toolchain,
        "rustcVersion": rustc_version(source, build_env),
    }
    if linux_abi:
        shutil.copy2(source / "rhwp-abi.txt", destination.parent / "rhwp-abi.txt")
    (destination.parent / "build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(destination / name)


if __name__ == "__main__":
    main()
