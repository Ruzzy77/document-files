"""Local process/file transport; never a security sandbox for untrusted adapters."""

from __future__ import annotations

import contextlib
import hashlib
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from .extraction_errors import BudgetExceededError, ExtractionError
from .private_fs import private_path


@contextlib.contextmanager
def descriptor_input(fd: int, *, max_bytes: int, windows: bool | None = None):
    """Keep POSIX descriptor transport; give Windows only an isolated immutable copy.

    The adapter never receives the caller's path. Windows ACLs inherit from the
    current user's private temp directory; the read-only flag is not a sandbox.
    """
    if not (os.name == "nt" if windows is None else windows):
        yield {"kind": "read_only_file_descriptor", "file_descriptor": fd, "path": f"/dev/fd/{fd}"}
        return
    with tempfile.TemporaryDirectory(prefix="document-files-input-") as directory:
        private_path(Path(directory), directory=True)
        snapshot = Path(directory) / "input.bin"
        digest = hashlib.sha256()
        count = 0
        position = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            with snapshot.open("xb") as stream:
                while chunk := os.read(fd, 64 * 1024):
                    count += len(chunk)
                    if count > max_bytes:
                        raise BudgetExceededError("adapter input exceeds its byte budget")
                    digest.update(chunk)
                    stream.write(chunk)
            snapshot.chmod(0o400)
            yield {
                "kind": "read_only_snapshot",
                "path": str(snapshot),
                "sha256": digest.hexdigest(),
                "byte_size": count,
            }
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest.hexdigest():
                raise ExtractionError("adapter modified the isolated input snapshot")
        finally:
            os.lseek(fd, position, os.SEEK_SET)
            if snapshot.exists():
                snapshot.chmod(0o600)


def process_options(input_fd: int | None = None) -> dict:
    if os.name == "posix":
        return {"start_new_session": True, "pass_fds": () if input_fd is None else (input_fd,)}
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}


def kill_process_tree(process: subprocess.Popen) -> None:
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.poll() is None:
        # /T also terminates descendants. Never invoke a shell or interpolate paths.
        subprocess.run(
            [
                str(Path(os.environ["SYSTEMROOT"]) / "System32/taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if process.poll() is None:
            process.kill()


def subprocess_environment() -> dict[str, str]:
    env = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    for key in ("SystemRoot", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


class WindowsJob:
    """Kill-on-close Job Object contains the worker and its descendants.

    A job is attached immediately after spawn. This is resource cleanup, not an
    adversarial process sandbox. Fail closed if job assignment is unavailable.
    """

    def __init__(self, process: subprocess.Popen):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_uint64)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            process.kill()
            process.wait()
            raise ctypes.WinError(ctypes.get_last_error())
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            kill_process_tree(process)
            process.wait()
            raise ctypes.WinError(error)
        self.handle = handle

    def close(self) -> None:
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
