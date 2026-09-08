"""Non-network diagnostics without credentials, document paths or endpoint URLs."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from importlib.metadata import version

from .interpretation.backends import ChatCompletionsClient, ModelError
from .interpretation.workflow import storage_root


def diagnose() -> dict:
    try:
        client = ChatCompletionsClient.from_environment()
        model = {"configured": True, "identity": client.identity, "connectionVerified": False}
    except ModelError as exc:
        model = {"configured": False, "issue": exc.code, "connectionVerified": False}
    root = storage_root()
    ancestor = root
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    return {
        "schemaVersion": "document-files.diagnostics.v1",
        "version": version("document-files"),
        "runtime": {
            "python": platform.python_version(),
            "platform": sys.platform,
            "architecture": platform.machine(),
        },
        "model": model,
        "storage": {
            "configured": bool(os.environ.get("DOCUMENT_FILES_STORAGE_DIR")),
            "exists": root.is_dir(),
            "parentWritable": os.access(ancestor, os.W_OK),
            "permissionsVerified": False,
        },
        "dependencies": {
            name: importlib.util.find_spec(name) is not None
            for name in ("mcp", "jsonschema", "docx", "openpyxl", "pypdf")
        },
        "networkUsed": False,
    }
