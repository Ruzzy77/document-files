"""Synthetic assembly contracts; no actual PBS, pip, ARM code or model execution."""

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
import stat
import struct
import sys
import tarfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def tool():
    spec = importlib.util.spec_from_file_location(
        "assemble_stage", Path(__file__).parents[1] / "scripts/assemble_linux_recognition_stage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record_row(name, data):
    return [
        name,
        "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(sha(data))).rstrip(b"=").decode(),
        str(len(data)),
    ]


def csv_bytes(rows):
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    return buffer.getvalue().encode()


def wheel(path, extra=None):
    files = {
        "pip/__init__.py": b"# inert\n",
        "pip-1.dist-info/METADATA": b"Name: pip\nVersion: 1\n",
        "pip-1.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files.update(extra or {})
    files["pip-1.dist-info/RECORD"] = csv_bytes(
        [record_row(n, d) for n, d in files.items()] + [["pip-1.dist-info/RECORD", "", ""]]
    )
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            entry = zipfile.ZipInfo(name)
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return files


def pbs(path, extras=(), machine=183):
    elf = bytearray(64)
    elf[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", elf, 18, machine)
    rows = [
        ("python/bin/python3.12", bytes(elf), 0o755, None),
        ("python/bin/python3", b"", 0o755, "python3.12"),
        ("python/lib/python3.12/site-packages/pip/old.py", b"old", 0o644, None),
        ("python/bin/pip3", b"#!/absolute/python\n", 0o755, None),
        *extras,
    ]
    with tarfile.open(path, "w:gz") as archive:
        for name, data, mode, link in rows:
            entry = tarfile.TarInfo(name)
            entry.mode = mode
            if link is not None:
                entry.type = tarfile.SYMTYPE
                entry.linkname = link
            else:
                entry.size = len(data)
            archive.addfile(entry, None if link is not None else io.BytesIO(data))


def identity(path):
    return {"path": path.name, "size": path.stat().st_size, "sha256": sha(path.read_bytes())}


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    pbs(root / "pbs.tar.gz")
    wheel(root / "pip-1-py3-none-any.whl")
    (root / "model.bin").write_bytes(b"original-model")
    m = {
        "schemaVersion": "document-files.linux-recognition-assembly.v1",
        "platform": "linux-aarch64",
        "pythonRuntime": {**identity(root / "pbs.tar.gz"), "executable": "python/bin/python3.12"},
        "wheels": [{**identity(root / "pip-1-py3-none-any.whl"), "name": "pip", "version": "1"}],
        "assets": [
            {
                **identity(root / "model.bin"),
                "destination": "models/data.bin",
                "role": "model",
                "provenance": {"source": "synthetic"},
                "executable": False,
            }
        ],
        "pythonOmissions": [
            {
                "path": "python/lib/python3.12/site-packages",
                "recursive": True,
                "reason": "replace PBS pip",
            },
            {"path": "python/bin/pip3", "recursive": False, "reason": "absolute build shebang"},
        ],
        "installedScriptPolicy": "omit-generated-bin",
        "limits": {
            "maxInputBytes": 1000000,
            "maxArchiveMembers": 1000,
            "maxExpandedBytes": 10000000,
            "maxOutputFiles": 1000,
            "maxOutputBytes": 10000000,
            "maxSeconds": 20,
            "maxLogBytes": 1000,
        },
    }
    m["pipWheelSha256"] = m["wheels"][0]["sha256"]
    return root, m, tmp_path / "output"


def execute(tool, setup, *, check=False):
    root, manifest, output = setup
    path = root / "inputs.json"
    path.write_text(json.dumps(manifest))
    return tool.assemble(path, sha(path.read_bytes()), root, output, check_only=check)


def fake_pip(command, env, log, budget):
    target = Path(command[command.index("--target") + 1])
    wh = Path(command[command.index("--find-links") + 1])
    provided_scripts = set()
    data_paths = set()
    for path in wh.glob("*.whl"):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                if ".data/scripts/" in name:
                    name = "bin/" + name.split(".data/scripts/", 1)[1]
                    provided_scripts.add(name)
                    first, sep, rest = data.partition(b"\n")
                    if first.rstrip(b"\r") in (b"#!python", b"#!pythonw"):
                        data = b"#!" + command[0].encode() + b"\n" + rest
                if ".data/data/" in name:
                    name = name.split(".data/data/", 1)[1]
                    data_paths.add(name)
                dest = target / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
    (target / "pip-1.dist-info/INSTALLER").write_bytes(b"pip\n")
    (target / "pip-1.dist-info/REQUESTED").write_bytes(b"")
    (target / "bin").mkdir(exist_ok=True)
    (target / "bin/pip").write_bytes(b"#!/temporary/python\n")
    entries = [
        record_row(p.relative_to(target).as_posix(), p.read_bytes())
        for p in target.rglob("*")
        if p.is_file()
        and p.name != "RECORD"
        and p != target / "bin/pip"
        and p.relative_to(target).as_posix() not in provided_scripts | data_paths
    ]
    # Preserve vendor RECORD as an ordinary package file.
    for p in target.rglob("RECORD"):
        if p.parent.name != "pip-1.dist-info":
            entries.append(record_row(p.relative_to(target).as_posix(), p.read_bytes()))
    entries += [
        record_row("../../" + name, (target / name).read_bytes())
        for name in sorted(provided_scripts | data_paths)
    ]
    entries += [
        record_row("../../bin/pip", (target / "bin/pip").read_bytes()),
        ["pip-1.dist-info/RECORD", "", ""],
    ]
    (target / "pip-1.dist-info/RECORD").write_bytes(csv_bytes(entries))
    log.write_text("synthetic installer; no pip executed\n")
    assert "LD_LIBRARY_PATH" not in env
    assert command[1:4] == ["-I", "-S", "-B"]
    return {"exitCode": 0, "attempts": 1}


def windows_posix_modes(monkeypatch):
    """Only simulated Linux assembly uses POSIX modes on a Windows filesystem.

    Production still requires Linux and checks real modes. Windows chmod cannot
    represent execute bits, so record chmod requests rather than weakening that check.
    """
    if os.name != "nt":
        return
    original_stat, original_chmod = Path.stat, Path.chmod
    requested = {}

    class ModeStat:
        def __init__(self, original, mode):
            self.original = original
            self.st_mode = stat.S_IFMT(original.st_mode) | mode

        def __getattr__(self, name):
            return getattr(self.original, name)

    def chmod(path, mode, *args, **kwargs):
        result = original_chmod(path, mode, *args, **kwargs)
        requested[str(path.absolute())] = stat.S_IMODE(mode)
        return result

    def metadata(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        mode = requested.get(str(path.absolute()))
        return ModeStat(result, mode) if mode is not None else result

    monkeypatch.setattr(Path, "chmod", chmod)
    monkeypatch.setattr(Path, "stat", metadata)


@pytest.fixture
def mocked(tool, monkeypatch):
    windows_posix_modes(monkeypatch)
    monkeypatch.setattr(tool.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tool.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(tool, "run_pip", fake_pip)
    return tool


def test_check_only_no_execution_or_output(tool, setup, monkeypatch):
    monkeypatch.setattr(tool.subprocess, "Popen", lambda *a, **k: pytest.fail("executed"))
    report = execute(tool, setup, check=True)
    assert report["status"] == "inputs-checked-not-executed"
    assert not setup[2].exists()


def test_assemble_preserves_bytes_regular_links_and_rewrites_record(mocked, setup):
    result = execute(mocked, setup)
    stage = setup[2] / "stage"
    assert result["status"] == "assembled-unapproved"
    assert not result["stageApproved"] and not result["networkIsolationVerified"]
    assert (stage / "python/bin/python3").read_bytes() == (
        stage / "python/bin/python3.12"
    ).read_bytes()
    assert not (stage / "python/bin/python3").is_symlink()
    assert not (stage / "python/bin/pip3").exists()
    assert not (stage / "python/lib/python3.12/site-packages/pip/old.py").exists()
    final = stage / "python/lib/python3.12/site-packages/pip-1.dist-info/RECORD"
    assert "../../bin/" not in final.read_text()
    assert "../../bin/" in (setup[2] / "installed/pip-1.dist-info/RECORD").read_text()
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    assert origins["python/bin/python3"]["linkChain"]
    assert origins["models/data.bin"]["sha256"] == sha(b"original-model")
    assert result["pipAttempts"] == 1 and result["originalInputsUnchanged"]


def test_vendor_metadata_record_preserved(mocked, setup):
    root, m, _ = setup
    path = root / m["wheels"][0]["path"]
    extra = {
        "pip/_vendor/demo-2.dist-info/METADATA": b"Name: demo\nVersion: 2\n",
        "pip/_vendor/demo-2.dist-info/RECORD": b"vendor original",
    }
    wheel(path, extra)
    m["wheels"][0].update(identity(path))
    m["pipWheelSha256"] = sha(path.read_bytes())
    execute(mocked, setup)
    assert (
        setup[2]
        / "stage/python/lib/python3.12/site-packages"
        / "pip/_vendor/demo-2.dist-info/RECORD"
    ).read_bytes() == b"vendor original"


@pytest.mark.parametrize(
    "target,code",
    [
        ("/etc/passwd", "absolute"),
        ("../../../escape", "escape"),
        ("missing", "dangling"),
        ("cycle", "cycle"),
    ],
)
def test_unsafe_archive_links(tool, setup, target, code):
    root, m, _ = setup
    path = root / "pbs.tar.gz"
    pbs(path, [("python/bin/cycle", b"", 0o644, target)])
    m["pythonRuntime"].update(identity(path))
    with pytest.raises(tool.AssemblyError, match=code):
        execute(tool, setup, check=True)


def test_relative_dotdot_regular_copy(mocked, setup):
    root, m, _ = setup
    path = root / "pbs.tar.gz"
    pbs(path, [("python/lib/alias", b"", 0o755, "../bin/python3.12")])
    m["pythonRuntime"].update(identity(path))
    execute(mocked, setup)
    assert (setup[2] / "stage/python/lib/alias").is_file()


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("maxInputBytes", 1, "input_byte_budget"),
        ("maxArchiveMembers", 1, "member_budget"),
        ("maxExpandedBytes", 1, "expanded_byte_budget"),
        ("maxSeconds", True, "invalid_limits"),
    ],
)
def test_finite_budgets(tool, setup, field, value, code):
    setup[1]["limits"][field] = value
    with pytest.raises(tool.AssemblyError, match=code):
        execute(tool, setup, check=True)


@pytest.mark.parametrize("path", ["../out", "/out", "a/../../out", "a\\b", "C:drive", "a//b"])
def test_asset_path_escape(tool, setup, path):
    setup[1]["assets"][0]["destination"] = path
    with pytest.raises(tool.AssemblyError, match="invalid_relative_path"):
        execute(tool, setup, check=True)


def test_asset_collision(tool, setup):
    setup[1]["assets"][0]["destination"] = "python/bin/python3.12"
    with pytest.raises(tool.AssemblyError, match="asset_collision"):
        execute(tool, setup, check=True)


def test_hash_mismatch(tool, setup):
    (setup[0] / "model.bin").write_bytes(b"changed")
    with pytest.raises(tool.AssemblyError, match="input_identity_mismatch"):
        execute(tool, setup, check=True)


def test_source_symlink(tool, setup):
    root, _, _ = setup
    (root / "model.bin").rename(root / "real.bin")
    (root / "model.bin").symlink_to("real.bin")
    with pytest.raises(tool.AssemblyError, match="input_symlink"):
        execute(tool, setup, check=True)


def test_host_mismatch(tool, setup, monkeypatch):
    monkeypatch.setattr(tool.platform, "system", lambda: "Darwin")
    with pytest.raises(tool.AssemblyError, match="execution_host_mismatch"):
        execute(tool, setup)
    assert not setup[2].exists()


def test_existing_output(mocked, setup):
    setup[2].mkdir()
    with pytest.raises(mocked.AssemblyError, match="output_not_fresh"):
        execute(mocked, setup)
    assert not (setup[2] / "receipt.json").exists()


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("bytes", "installed_wheel_bytes_changed"),
        ("record", "installed_record_mutation"),
        ("extra", "installed_inventory_mismatch"),
        ("source", "original_input_changed"),
    ],
)
def test_install_mutations_partial_preserved(mocked, setup, monkeypatch, mutation, code):
    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        target = Path(command[command.index("--target") + 1])
        if mutation == "bytes":
            (target / "pip/__init__.py").write_bytes(b"altered")
        elif mutation == "record":
            path = target / "pip-1.dist-info/RECORD"
            path.write_text(path.read_text().replace("sha256=", "sha512=", 1))
        elif mutation == "extra":
            (target / "extra.py").write_bytes(b"unknown")
        else:
            (setup[0] / "model.bin").write_bytes(b"different byte")
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(
        mocked.AssemblyError, match=code if mutation != "source" else "copy_source_changed"
    ):
        execute(mocked, setup)
    receipt = json.loads((setup[2] / "receipt.json").read_text())
    assert receipt["status"] == "failed" and receipt["pipAttempts"] == 1
    assert (setup[2] / "stage/python/bin/python3.12").exists()


def test_pip_failure_partial(mocked, setup, monkeypatch):
    def fail(*args):
        raise mocked.AssemblyError("pip_failed:1")

    monkeypatch.setattr(mocked, "run_pip", fail)
    with pytest.raises(mocked.AssemblyError, match="pip_failed"):
        execute(mocked, setup)
    assert json.loads((setup[2] / "receipt.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("header", [b"#!python", b"#!pythonw", b"#!/upstream/python"])
def test_wheel_provided_script_preserved_without_temporary_shebang(mocked, setup, header):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    original = header + b"\nprint('upstream CLI')\n"
    wheel(path, {"pip-1.data/scripts/tool": original})
    setup[1]["wheels"][0].update(identity(path))
    setup[1]["pipWheelSha256"] = sha(path.read_bytes())
    result = execute(mocked, setup)
    name = ".document-files-wheel-scripts/pip-1.dist-info/tool"
    site = setup[2] / "stage/python/lib/python3.12/site-packages"
    assert (site / name).read_bytes() == original
    assert not (site / "bin/tool").exists()
    record = list(csv.reader((site / "pip-1.dist-info/RECORD").read_text().splitlines()))
    assert record_row(name, original) in record
    assert not any(row[0] == "../../bin/tool" for row in record)
    installed = (setup[2] / "installed/bin/tool").read_bytes()
    if header in (b"#!python", b"#!pythonw"):
        assert installed.startswith(b"#!" + str(setup[2]).encode())
    else:
        assert installed == original
    for row in record:
        final_path = (site / row[0]).resolve()
        assert final_path.is_relative_to(site.resolve())
        if row[1]:
            assert row == record_row(row[0], final_path.read_bytes())
    assert result["preservedUpstreamCLIs"][0]["executionSupport"] == "not-provided"
    assert len(result["generatedBinsOmitted"]) == 1


def windows_single_child_posix_boundary(tool, monkeypatch):
    import ctypes
    import msvcrt
    from ctypes import wintypes

    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    peek.restype = wintypes.BOOL

    class PipeSelector:
        def __init__(self):
            self.keys = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.keys.clear()

        def register(self, stream, events):
            self.keys[stream] = SimpleNamespace(fd=stream.fileno(), fileobj=stream)

        def unregister(self, stream):
            self.keys.pop(stream)

        def get_map(self):
            return self.keys

        def select(self, timeout):
            ready = []
            for key in self.keys.values():
                count = wintypes.DWORD()
                ok = peek(msvcrt.get_osfhandle(key.fd), None, 0, None, ctypes.byref(count), None)
                if not ok:
                    error = ctypes.get_last_error()
                    if error != 109:  # ERROR_BROKEN_PIPE: EOF is readable.
                        raise OSError(error, "PeekNamedPipe failed")
                if not ok or count.value:
                    ready.append((key, tool.selectors.EVENT_READ))
            if not ready:
                time.sleep(timeout)
            return ready

    processes = {}
    original_popen = tool.subprocess.Popen

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes[process.pid] = process
        return process

    def signal_single_child(pid, sig):
        process = processes[pid]
        if process.poll() is not None:
            raise ProcessLookupError(pid)
        if sig:
            process.kill()

    monkeypatch.setattr(tool.subprocess, "Popen", popen)
    monkeypatch.setattr(tool.selectors, "DefaultSelector", PipeSelector)
    monkeypatch.setattr(tool.os, "killpg", signal_single_child, raising=False)
    monkeypatch.setattr(tool.signal, "SIGKILL", 9, raising=False)


@pytest.mark.parametrize(
    "behavior,error",
    [
        ("import time; time.sleep(5)", "deadline_exceeded"),
        ("print('x'*5000)", "pip_log_budget"),
        ("raise SystemExit(7)", "pip_failed:7"),
    ],
)
def test_supervisor_real_synthetic_child(tool, tmp_path, behavior, error, monkeypatch):
    # Keep real Python-child deadline/output/exit checks on every OS. On Windows
    # only the POSIX pipe-select and signal boundary is adapted for this single
    # child; this is not a claim of Windows process-group support in the product.
    if os.name == "nt":
        windows_single_child_posix_boundary(tool, monkeypatch)
    limits = dict(zip(tool.LIMITS, [1000000, 1000, 1000000, 1000, 1000000, 1, 100], strict=True))
    (tmp_path / "tmp").mkdir()
    with pytest.raises(tool.AssemblyError, match=error):
        tool.run_pip(
            [sys.executable, "-c", behavior],
            {"TMPDIR": str(tmp_path / "tmp")},
            tmp_path / "log",
            tool.Budget(limits),
        )
    assert (tmp_path / "log").stat().st_size <= 100
    process = json.loads((tmp_path / "log.process.json").read_text())
    assert process["attempts"] == 1 and process["exitCode"] is not None
    assert process["cleanupSteps"]["stdoutClose"] == "closed"
    assert process["originalError"]["code"] == error
    if not process["processGroupAbsent"]:
        assert process["cleanupConfirmed"] is False
        assert process["cleanupSteps"]["groupProbe"] in {"present", "failed"}
        if process["cleanupSteps"]["groupProbe"] == "failed":
            assert any(e["stage"] == "groupProbe" for e in process["cleanupErrors"])


def test_python_elf_machine_must_match(tool, setup):
    setup[1]["platform"] = "linux-x86_64"
    with pytest.raises(tool.AssemblyError, match="python_elf_target_mismatch"):
        execute(tool, setup, check=True)


def test_original_wheel_record_hash_must_match(tool, setup):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    with zipfile.ZipFile(path) as archive:
        files = {n: archive.read(n) for n in archive.namelist()}
    files["pip/__init__.py"] = b"corrupted without RECORD update"
    with zipfile.ZipFile(path, "w") as archive:
        for n, d in files.items():
            archive.writestr(n, d)
    setup[1]["wheels"][0].update(identity(path))
    with pytest.raises(tool.AssemblyError, match="wheel_record_mismatch"):
        execute(tool, setup, check=True)


def test_archive_directory_link_rejected(tool, setup):
    root, m, _ = setup
    path = root / "pbs.tar.gz"
    pbs(path, [("python/bin/alias", b"", 0o755, "../bin")])
    with tarfile.open(path, "r:gz") as archive:
        rows = [(t, archive.extractfile(t).read() if t.isfile() else None) for t in archive]
    with tarfile.open(path, "w:gz") as archive:
        directory = tarfile.TarInfo("python/bin")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        for entry, data in rows:
            archive.addfile(entry, io.BytesIO(data) if data else None)
    m["pythonRuntime"].update(identity(path))
    with pytest.raises(tool.AssemblyError, match="archive_link_not_regular"):
        execute(tool, setup, check=True)


def test_original_pbs_script_is_not_implicitly_omitted(mocked, setup):
    setup[1]["pythonOmissions"] = setup[1]["pythonOmissions"][:1]
    execute(mocked, setup)
    path = setup[2] / "stage/python/bin/pip3"
    assert path.read_bytes() == b"#!/absolute/python\n"
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    assert origins["python/bin/pip3"]["originalShebang"]["standalonePortability"] == "unverified"


def test_manifest_sha_rejected(tool, setup):
    p = setup[0] / "manifest.json"
    p.write_text(json.dumps(setup[1]))
    with pytest.raises(tool.AssemblyError, match="manifest_hash_mismatch"):
        tool.assemble(p, "0" * 64, setup[0], check_only=True)


def test_installer_cannot_mutate_pbs(mocked, setup, monkeypatch):
    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        Path(command[0]).write_bytes(b"mutated-python")
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(mocked.AssemblyError, match="final_stage_changed"):
        execute(mocked, setup)
    assert json.loads((setup[2] / "receipt.json").read_text())["status"] == "failed"


def test_native_asset_executable_policy(mocked, setup):
    setup[1]["assets"][0]["executable"] = True
    setup[1]["assets"][0]["role"] = "native"
    data = bytearray(64)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<H", data, 18, 183)
    path = setup[0] / "model.bin"
    path.write_bytes(data)
    setup[1]["assets"][0].update(identity(path))
    execute(mocked, setup)
    assert (setup[2] / "stage/models/data.bin").stat().st_mode & 0o777 == 0o755


def test_nonboolean_executable_rejected(tool, setup):
    setup[1]["assets"][0]["executable"] = 1
    with pytest.raises(tool.AssemblyError, match="asset_executable_required"):
        execute(tool, setup, check=True)


def test_output_symlink_rejected(mocked, setup, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    setup[2].symlink_to(real)
    with pytest.raises(mocked.AssemblyError, match="output_symlink"):
        execute(mocked, setup)
    assert not list(real.iterdir())


def test_x64_matching_host_supported(mocked, setup, monkeypatch):
    monkeypatch.setattr(mocked.platform, "machine", lambda: "x86_64")
    setup[1]["platform"] = "linux-x86_64"
    pbs(setup[0] / "pbs.tar.gz", machine=62)
    setup[1]["pythonRuntime"].update(identity(setup[0] / "pbs.tar.gz"))
    assert execute(mocked, setup)["status"] == "assembled-unapproved"


def test_original_pbs_stdlib_shebang_and_mode_preserved(mocked, setup):
    path = setup[0] / "pbs.tar.gz"
    original = b"#!/usr/local/bin/python3.12\n# original cgi source\n"
    pbs(path, [("python/lib/python3.12/cgi.py", original, 0o755, None)])
    setup[1]["pythonRuntime"].update(identity(path))
    execute(mocked, setup)
    staged = setup[2] / "stage/python/lib/python3.12/cgi.py"
    assert staged.read_bytes() == original
    assert staged.stat().st_mode & 0o777 == 0o755
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    entry = origins["python/lib/python3.12/cgi.py"]
    assert entry["originalShebang"]["sourceBytesAndModePreserved"]
    assert bytes.fromhex(entry["originalShebang"]["firstLinePrefixHex"]) == original.splitlines()[0]
    assert entry["sha256"] == sha(original)


def executable_wheel_module(path, name, data):
    wheel(path, {name: data})
    with zipfile.ZipFile(path) as archive:
        entries = [(item, archive.read(item)) for item in archive.infolist()]
    with zipfile.ZipFile(path, "w") as archive:
        for item, content in entries:
            if item.filename == name:
                item.external_attr = 0o100755 << 16
            archive.writestr(item, content)


def test_original_wheel_module_shebang_preserved(mocked, setup):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    original = b"#!/upstream/build/python\n# import source\n"
    executable_wheel_module(path, "pip/module.py", original)
    setup[1]["wheels"][0].update(identity(path))
    setup[1]["pipWheelSha256"] = sha(path.read_bytes())
    execute(mocked, setup)
    staged = setup[2] / "stage/python/lib/python3.12/site-packages/pip/module.py"
    assert staged.read_bytes() == original
    assert staged.stat().st_mode & 0o777 == 0o755
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    assert origins["python/lib/python3.12/site-packages/pip/module.py"]["originalShebang"]


@pytest.mark.parametrize("name", ["bin/tool.py", "scripts/tool.py", "pip/script.sh"])
def test_original_wheel_support_scripts_preserved(mocked, setup, name):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    original = b"#!/upstream/support/interpreter\n"
    executable_wheel_module(path, name, original)
    setup[1]["wheels"][0].update(identity(path))
    setup[1]["pipWheelSha256"] = sha(path.read_bytes())
    execute(mocked, setup)
    dest = "python/lib/python3.12/site-packages/" + name
    assert (setup[2] / "stage" / dest).read_bytes() == original
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    assert origins[dest]["originalShebang"]["disposition"] == "not-selected-entrypoint"
    assert (
        name
        in (
            setup[2] / "stage/python/lib/python3.12/site-packages/pip-1.dist-info/RECORD"
        ).read_text()
    )


def test_executable_asset_cannot_use_import_source_exception(tool, setup):
    path = setup[0] / "model.bin"
    path.write_bytes(b"#!/temporary/installer/python\n")
    setup[1]["assets"][0].update(identity(path))
    setup[1]["assets"][0]["executable"] = True
    setup[1]["assets"][0]["destination"] = "python/lib/python3.12/not_a_module.py"
    setup[1]["assets"][0]["role"] = "native"
    with pytest.raises(tool.AssemblyError, match="executable_asset_absolute_shebang"):
        execute(tool, setup, check=True)


def test_pbs_config_support_script_preserves_original_bytes_and_mode(mocked, setup):
    name = "python/lib/python3.12/config-3.12-aarch64-linux-gnu/install-sh"
    original = b"#!/bin/sh\n# original upstream support script\n"
    path = setup[0] / "pbs.tar.gz"
    pbs(path, [(name, original, 0o755, None)])
    setup[1]["pythonRuntime"].update(identity(path))
    execute(mocked, setup)
    assert (setup[2] / "stage" / name).read_bytes() == original
    assert (setup[2] / "stage" / name).stat().st_mode & 0o777 == 0o755
    origins = json.loads((setup[2] / "file-origins.json").read_text())
    assert origins[name]["originalShebang"]["standalonePortability"] == "unverified"


def test_selected_executable_asset_requires_native_role(tool, setup):
    setup[1]["assets"][0]["executable"] = True
    with pytest.raises(tool.AssemblyError, match="unsupported_executable_asset_role"):
        execute(tool, setup, check=True)


def test_selected_native_asset_requires_correct_elf(tool, setup):
    setup[1]["assets"][0].update(executable=True, role="native")
    with pytest.raises(tool.AssemblyError, match="native_asset_elf_target_mismatch"):
        execute(tool, setup, check=True)


@pytest.mark.parametrize("kind", ["body", "shebang", "record"])
def test_provided_script_installation_mutation_rejected(mocked, setup, monkeypatch, kind):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    wheel(path, {"pip-1.data/scripts/tool": b"#!python\n# original body\n"})
    setup[1]["wheels"][0].update(identity(path))
    setup[1]["pipWheelSha256"] = sha(path.read_bytes())

    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        target = Path(command[command.index("--target") + 1])
        script = target / "bin/tool"
        if kind == "body":
            script.write_bytes(script.read_bytes() + b"# changed")
        elif kind == "shebang":
            script.write_bytes(b"#!/unrelated/python\n# original body\n")
        record = target / "pip-1.dist-info/RECORD"
        rows = list(csv.reader(record.read_text().splitlines()))
        for i, row in enumerate(rows):
            if row[0] == "../../bin/tool":
                rows[i] = record_row(row[0], script.read_bytes())
                if kind == "record":
                    rows[i][1] = "sha256=wrong"
        record.write_bytes(csv_bytes(rows))
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(mocked.AssemblyError, match="installed_script_(changed|record_mutation)"):
        execute(mocked, setup)
    assert (setup[2] / "receipt.json").exists()


def test_provided_script_preservation_namespace_collision(tool, setup):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    wheel(
        path,
        {
            "pip-1.data/scripts/tool": b"#!python\n",
            ".document-files-wheel-scripts/pip-1.dist-info/tool": b"other",
        },
    )
    setup[1]["wheels"][0].update(identity(path))
    with pytest.raises(tool.AssemblyError, match="wheel_destination_collision"):
        execute(tool, setup, check=True)


def test_provided_script_path_escape(tool, setup):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    wheel(path, {"pip-1.data/scripts/../../tool": b"#!python\n"})
    setup[1]["wheels"][0].update(identity(path))
    with pytest.raises(tool.AssemblyError, match="invalid_relative_path"):
        execute(tool, setup, check=True)


def test_provided_script_byte_comparison_obeys_budget(tool, tmp_path):
    original, installed = tmp_path / "original", tmp_path / "installed"
    original.write_bytes(b"#!python\nbody\n")
    installed.write_bytes(b"#!/pinned/python\nbody\n")
    limits = dict(zip(tool.LIMITS, [1000000, 1000, 1, 1000, 1000000, 1, 100], strict=True))
    with pytest.raises(tool.AssemblyError, match="expanded_byte_budget"):
        tool.script_installation_matches(original, installed, "/pinned/python", tool.Budget(limits))


def test_original_bin_and_wheel_script_collision(tool, setup):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    wheel(path, {"pip-1.data/scripts/tool": b"#!python\n", "bin/tool": b"other"})
    setup[1]["wheels"][0].update(identity(path))
    with pytest.raises(tool.AssemblyError, match="wheel_script_installation_collision"):
        execute(tool, setup, check=True)


def add_data_wheel(setup, extra=None):
    path = setup[0] / setup[1]["wheels"][0]["path"]
    data = b".TH isympy 1\nOriginal upstream manual.\n"
    members = {"pip-1.data/data/share/man/man1/isympy.1": data}
    members.update(extra or {})
    wheel(path, members)
    setup[1]["wheels"][0].update(identity(path))
    setup[1]["pipWheelSha256"] = sha(path.read_bytes())
    return data


def test_data_scheme_actual_pip_layout_relocated_with_record(mocked, setup):
    data = add_data_wheel(setup)
    result = execute(mocked, setup)
    site = setup[2] / "stage/python/lib/python3.12/site-packages"
    final = setup[2] / "stage/python/share/man/man1/isympy.1"
    assert final.read_bytes() == data
    original = setup[2] / "installed/pip-1.dist-info/RECORD"
    assert "../../share/man/man1/isympy.1" in original.read_text()
    rows = list(csv.reader((site / "pip-1.dist-info/RECORD").read_text().splitlines()))
    assert record_row("../../../share/man/man1/isympy.1", data) in rows
    for row in rows:
        file = (site / row[0]).resolve()
        assert file.is_relative_to((setup[2] / "stage").resolve())
        if row[1]:
            assert row == record_row(row[0], file.read_bytes())
    origin = json.loads((setup[2] / "file-origins.json").read_text())[
        "python/share/man/man1/isympy.1"
    ]
    assert origin["member"] == "pip-1.data/data/share/man/man1/isympy.1"
    assert origin["sha256"] == sha(data)
    assert result["relocatedWheelData"][0]["installedRecordPath"] == "../../share/man/man1/isympy.1"


@pytest.mark.parametrize("change", ["path", "sha", "body", "other-record"])
def test_data_record_or_content_mutation_rejected(mocked, setup, monkeypatch, change):
    add_data_wheel(setup)

    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        target = Path(command[command.index("--target") + 1])
        if change == "body":
            (target / "share/man/man1/isympy.1").write_bytes(b"changed")
        else:
            record = target / "pip-1.dist-info/RECORD"
            rows = list(csv.reader(record.read_text().splitlines()))
            for row in rows:
                if row[0] == "../../share/man/man1/isympy.1":
                    if change == "path":
                        row[0] = "../../../escape"
                    elif change == "sha":
                        row[1] = "sha256=changed"
                    else:
                        row[0] = "../../share/man/man1/another-distribution.1"
            record.write_bytes(csv_bytes(rows))
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(mocked.AssemblyError):
        execute(mocked, setup)
    assert json.loads((setup[2] / "receipt.json").read_text())["status"] == "failed"


@pytest.mark.parametrize(
    "name,error",
    [
        ("pip-1.data/data/../../escape", "invalid_relative_path"),
        ("another-1.data/data/share/man/man1/other.1", "wheel_data_distribution_mismatch"),
        ("share/ordinary-package-file", "wheel_data_target_directory_collision"),
    ],
)
def test_data_scheme_escape_and_other_distribution_rejected(tool, setup, name, error):
    add_data_wheel(setup, {name: b"data"})
    with pytest.raises(tool.AssemblyError, match=error):
        execute(tool, setup, check=True)


def test_data_destination_asset_collision(tool, setup):
    add_data_wheel(setup)
    setup[1]["assets"][0]["destination"] = "python/share/man/man1/isympy.1"
    with pytest.raises(tool.AssemblyError, match="asset_collision"):
        execute(tool, setup, check=True)


def test_data_cannot_replace_pbs_entrypoint(tool, setup):
    add_data_wheel(setup, {"pip-1.data/data/bin/python3.12": b"unrelated"})
    with pytest.raises(tool.AssemblyError, match="wheel_pbs_destination_collision"):
        execute(tool, setup, check=True)


def test_data_final_destination_collision_with_library_file(tool, setup):
    add_data_wheel(
        setup, {"pip-1.data/data/lib/python3.12/site-packages/pip/__init__.py": b"other"}
    )
    with pytest.raises(tool.AssemblyError, match="wheel_pbs_destination_collision"):
        execute(tool, setup, check=True)


def test_data_requires_pip_home_relative_record_not_target_relative(mocked, setup, monkeypatch):
    add_data_wheel(setup)

    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        target = Path(command[command.index("--target") + 1])
        record = target / "pip-1.dist-info/RECORD"
        record.write_text(record.read_text().replace("../../share/man/", "share/man/"))
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(mocked.AssemblyError, match="unexpected_installed_data_record_path"):
        execute(mocked, setup)


def shared_script_case(tool, tmp_path, second="pkg.cli:main", *, provided=False):
    """Two fixed synthetic wheel definitions plus pip-style installed RECORDs."""
    target = tmp_path / "installed"
    target.mkdir()
    expected, distributions = {}, {}
    source_bytes = {}
    for pkg, definition in (("meta", "pkg.cli:main"), ("slim", second)):
        dist = pkg + "-1.dist-info"
        files = {
            dist + "/METADATA": f"Name: {pkg}\nVersion: 1\n".encode(),
            dist + "/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        }
        if definition is not None:
            files[dist + "/entry_points.txt"] = (
                f"[console_scripts]\nshared = {definition}\n".encode()
            )
        if provided and pkg == "meta":
            files["meta-1.data/scripts/shared"] = b"#!python\n# same body\n"
        record = dist + "/RECORD"
        files[record] = csv_bytes([record_row(n, d) for n, d in files.items()] + [[record, "", ""]])
        wheel_path = tmp_path / (pkg + "-1-py3-none-any.whl")
        with zipfile.ZipFile(wheel_path, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        input_wheel = {**identity(wheel_path), "name": pkg, "version": "1"}
        limits = dict(
            zip(tool.LIMITS, [1000000, 1000, 1000000, 1000, 1000000, 10, 1000], strict=True)
        )
        rows, dist = tool.wheel_inventory(wheel_path, input_wheel, tool.Budget(limits))
        expected.update(rows)
        distributions[dist] = input_wheel["sha256"]
        for name, row in rows.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[row["member"]])
            source_bytes[name] = files[row["member"]]
    actual_script = b"#!/fixed/python\n# same body\n"
    (target / "bin").mkdir()
    (target / "bin/shared").write_bytes(actual_script)
    for dist, digest in distributions.items():
        entries = [
            record_row(name, source_bytes[name])
            for name, row in expected.items()
            if row["sourceSha256"] == digest and not row["record"] and "providedScript" not in row
        ]
        entries += [record_row("../../bin/shared", actual_script), [dist + "/RECORD", "", ""]]
        (target / dist / "RECORD").write_bytes(csv_bytes(entries))
    return target, expected, distributions, tool.Budget(limits)


def test_shared_generated_console_identical_definitions_and_all_owners(tool, tmp_path):
    target, expected, distributions, budget = shared_script_case(tool, tmp_path)
    _, _, scripts, records = tool.installed_files(
        target, expected, distributions, budget, "/fixed/python"
    )
    assert len(scripts) == 1
    assert scripts[0]["sharedGeneratedConsoleScript"] is True
    assert {x["distribution"] for x in scripts[0]["owners"]} == {
        "meta-1.dist-info",
        "slim-1.dist-info",
    }
    assert all(x["definition"]["target"] == "pkg.cli:main" for x in scripts[0]["owners"])
    assert all(x["definition"]["sourceMemberSha256"] for x in scripts[0]["owners"])
    assert all(b"../../bin/shared" not in row["bytes"] for row in records.values())


@pytest.mark.parametrize("definition", ["different.cli:run", None])
def test_shared_generated_different_or_missing_definition_rejected(tool, tmp_path, definition):
    target, expected, distributions, budget = shared_script_case(tool, tmp_path, definition)
    with pytest.raises(tool.AssemblyError, match="shared_script_definition_mismatch"):
        tool.installed_files(target, expected, distributions, budget, "/fixed/python")


def test_shared_generated_record_sha_mismatch_rejected(tool, tmp_path):
    target, expected, distributions, budget = shared_script_case(tool, tmp_path)
    p = target / "slim-1.dist-info/RECORD"
    rows = list(csv.reader(p.read_text().splitlines()))
    for row in rows:
        if row[0] == "../../bin/shared":
            row[1] = "sha256=different"
    p.write_bytes(csv_bytes(rows))
    with pytest.raises(tool.AssemblyError, match="installed_script_record_mutation"):
        tool.installed_files(target, expected, distributions, budget, "/fixed/python")


def test_shared_provided_script_still_rejected(tool, tmp_path):
    target, expected, distributions, budget = shared_script_case(tool, tmp_path, provided=True)
    with pytest.raises(tool.AssemblyError, match="duplicate_provided_script_owner"):
        tool.installed_files(target, expected, distributions, budget, "/fixed/python")


def test_modified_entrypoint_metadata_not_a_shared_ownership_proof(tool, tmp_path):
    target, expected, distributions, budget = shared_script_case(tool, tmp_path)
    (target / "slim-1.dist-info/entry_points.txt").write_bytes(
        b"[console_scripts]\nshared = changed\n"
    )
    with pytest.raises(tool.AssemblyError, match="installed_wheel_bytes_changed"):
        tool.installed_files(target, expected, distributions, budget, "/fixed/python")


def test_digest_uses_same_handle_metadata_api(tool, tmp_path, monkeypatch):
    path = tmp_path / "source"
    path.write_bytes(b"original")
    original = Path.stat

    def different_path_metadata(p, *args, **kwargs):
        result = original(p, *args, **kwargs)
        return SimpleNamespace(
            st_dev=result.st_dev,
            st_ino=result.st_ino,
            st_size=result.st_size,
            st_mtime_ns=result.st_mtime_ns + 1,
            st_ctime_ns=result.st_ctime_ns,
        )

    monkeypatch.setattr(Path, "stat", different_path_metadata)
    assert tool.digest_file(path) == sha(b"original")


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
def test_digest_handle_mutation_still_rejected(tool, tmp_path, monkeypatch, field):
    path = tmp_path / "source"
    path.write_bytes(b"original")
    original = tool.os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        result = original(fd)
        values = {
            name: getattr(result, name)
            for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        if calls >= 2:
            values[field] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(tool.os, "fstat", changed)
    with pytest.raises(tool.AssemblyError, match="input_changed_while_hashing"):
        tool.digest_file(path)


def test_digest_reopened_path_replacement_rejected(tool, tmp_path, monkeypatch):
    path, replacement = tmp_path / "source", tmp_path / "replacement"
    path.write_bytes(b"original")
    replacement.write_bytes(b"original")
    original = Path.open
    calls = 0

    def replaced(p, *args, **kwargs):
        nonlocal calls
        if p == path:
            calls += 1
            if calls == 2:
                return original(replacement, *args, **kwargs)
        return original(p, *args, **kwargs)

    monkeypatch.setattr(Path, "open", replaced)
    with pytest.raises(tool.AssemblyError, match="input_changed_while_hashing"):
        tool.digest_file(path)


def test_installer_cannot_change_pbs_mode(mocked, setup, monkeypatch):
    def mutate(command, env, log, budget):
        result = fake_pip(command, env, log, budget)
        Path(command[0]).chmod(0o644)
        return result

    monkeypatch.setattr(mocked, "run_pip", mutate)
    with pytest.raises(mocked.AssemblyError, match="final_stage_changed"):
        execute(mocked, setup)


@pytest.mark.parametrize(
    "primary,kill_error,wait_error,probe_error,group_present,child_error,expected",
    [
        (False, False, False, False, False, False, None),
        (True, False, False, False, False, False, "pip_log_budget"),
        (True, True, False, True, False, False, "pip_log_budget"),
        (True, True, False, True, False, True, "pip_log_budget"),
        (False, True, False, False, False, False, "pip_cleanup_unconfirmed"),
        (False, False, True, False, False, False, "pip_cleanup_unconfirmed"),
        (True, False, True, False, False, False, "pip_log_budget"),
        (False, False, False, True, False, False, "pip_cleanup_unconfirmed"),
        (False, False, False, False, True, False, "pip_cleanup_unconfirmed"),
    ],
)
def test_supervisor_cleanup_failures_preserve_primary_and_all_steps(
    tool,
    tmp_path,
    monkeypatch,
    primary,
    kill_error,
    wait_error,
    probe_error,
    group_present,
    child_error,
    expected,
):
    events = []
    process = SimpleNamespace(pid=123456, returncode=None)
    waits = 0

    def wait(*, timeout):
        nonlocal waits
        waits += 1
        cleanup = primary or waits == 2
        events.append("cleanup_wait" if cleanup else "execution_wait")
        if cleanup:
            assert timeout == 5
            if wait_error:
                raise OSError(5, "synthetic wait failure")
        process.returncode = -9 if primary else 0
        return process.returncode

    def close():
        events.append("stdout_close")

    def poll():
        events.append("owned_poll")
        return process.returncode

    def kill():
        events.append("owned_kill")
        if child_error:
            raise PermissionError(1, "synthetic child signal denial")

    process.wait, process.poll, process.kill = wait, poll, kill
    process.stdout = SimpleNamespace(close=close)

    class Selector:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def register(self, *args):
            pass

        def get_map(self):
            return {1: True} if primary else {}

        def select(self, timeout):
            return [(SimpleNamespace(fd=321), None)]

    def group_signal(pid, sig):
        assert pid == process.pid  # No other PID or group may be selected.
        if sig:
            assert sig == tool.signal.SIGKILL
            events.append("group_kill")
            if kill_error:
                raise PermissionError(1, "synthetic group signal denial")
        else:
            events.append("group_probe")
            if probe_error:
                raise PermissionError(1, "synthetic group probe denial")
            if not group_present:
                raise ProcessLookupError(pid)

    monkeypatch.setattr(tool.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(tool.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(tool.os, "killpg", group_signal, raising=False)
    monkeypatch.setattr(tool.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(tool.os, "read", lambda fd, amount: b"x" * 101)
    limits = dict(zip(tool.LIMITS, [1000000, 1000, 1000000, 1000, 1000000, 1, 100], strict=True))
    temporary = tmp_path / "tmp"
    temporary.mkdir()

    def call():
        return tool.run_pip(
            ["never-executed"], {"TMPDIR": str(temporary)}, tmp_path / "log", tool.Budget(limits)
        )

    if expected:
        with pytest.raises(tool.AssemblyError, match=expected):
            call()
    else:
        assert call() == {"exitCode": 0, "logBytes": 0, "attempts": 1}
    receipt = json.loads((tmp_path / "log.process.json").read_text())
    assert events.index("group_kill") < events.index("cleanup_wait")
    assert events.index("cleanup_wait") < events.index("stdout_close") < events.index("group_probe")
    assert receipt["cleanupSteps"]["stdoutClose"] == "closed"
    assert receipt["processGroupAbsent"] is (not probe_error and not group_present)
    assert receipt["cleanupConfirmed"] is not (
        kill_error or wait_error or probe_error or group_present
    )
    assert receipt["originalError"] == (
        {"type": "AssemblyError", "code": "pip_log_budget"} if primary else None
    )
    stages = {e["stage"] for e in receipt["cleanupErrors"]}
    assert ("groupKill" in stages) is kill_error
    assert ("wait" in stages) is wait_error
    assert ("groupProbe" in stages) is probe_error
    if kill_error and primary:
        assert "owned_kill" in events
        assert receipt["cleanupSteps"]["ownedChildKill"] == ("failed" if child_error else "sent")
    else:
        assert "owned_kill" not in events
