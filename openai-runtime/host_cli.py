"""Run the canonical Document Files CLI in an OpenAI-provided host runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    try:
        from document_files.cli import main as cli_main
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "runtime_unavailable",
                        "message": ("The OpenAI host is missing a Document Files dependency."),
                        "details": {"missingModule": getattr(exc, "name", None)},
                    },
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    cli_main()


if __name__ == "__main__":
    main()
