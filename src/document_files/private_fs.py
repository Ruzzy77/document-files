"""Owner-only filesystem permissions for native and AI document state."""

from __future__ import annotations

import os
import re
from pathlib import Path


def private_path(path: Path, *, directory: bool = False) -> None:
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    # chmod on Windows does not restrict readers. Install a protected DACL for
    # the current user and SYSTEM rather than inheriting a possibly public ACL.
    import ctypes
    import subprocess
    from ctypes import wintypes

    try:
        import csv

        row = next(
            csv.reader(
                subprocess.check_output(
                    [
                        str(Path(os.environ["SYSTEMROOT"]) / "System32/whoami.exe"),
                        "/user",
                        "/fo",
                        "csv",
                        "/nh",
                    ],
                    text=True,
                    timeout=10,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                ).splitlines()
            )
        )
        sid = row[1]
        if not re.fullmatch(r"S-1-[0-9-]+", sid):
            raise ValueError("invalid SID")
        inherit = "OICI" if directory else ""
        sddl = f"D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)"
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        descriptor = ctypes.c_void_p()
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
        ]
        convert.restype = wintypes.BOOL
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("DACL conversion failed")
        try:
            setter = advapi.SetFileSecurityW
            setter.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
            setter.restype = wintypes.BOOL
            if not setter(str(path), 0x80000004, descriptor):
                raise OSError("DACL installation failed")
        finally:
            free = ctypes.WinDLL("kernel32").LocalFree
            free.argtypes = [ctypes.c_void_p]
            free(descriptor)
    except Exception as exc:
        raise OSError("Cannot establish private local storage") from exc
