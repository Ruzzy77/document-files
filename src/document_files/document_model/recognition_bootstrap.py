"""Run the private worker with pack dependencies, not the core's site-packages.

This file is executed by the recognition interpreter with ``-I -B``. Register
only the product package's source location; adding its parent to sys.path would
also import the core bundle's unrelated, differently pinned dependencies. The
private worker needs leaf modules, not the public package's eager API exports.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import runpy
import sys
from pathlib import Path


def main() -> None:
    spec = importlib.machinery.ModuleSpec("document_files", loader=None, is_package=True)
    spec.submodule_search_locations = [str(Path(__file__).resolve().parents[1])]
    sys.modules["document_files"] = importlib.util.module_from_spec(spec)
    runpy.run_module("document_files.document_model.recognition_worker", run_name="__main__")


if __name__ == "__main__":
    main()
