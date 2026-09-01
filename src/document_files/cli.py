"""Command-line interface for Document Files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .engine import (
    DocumentFilesError,
    capabilities,
    convert_file,
    create_hwpx,
    edit_hwpx,
    extract_file,
    inspect_file,
    render_file,
    verify_hwpx,
)


def _load_json(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DocumentFilesError(
            "json-input-invalid",
            "The JSON input file could not be read.",
            details={"path": path, "errorType": type(exc).__name__},
        ) from exc
    if not isinstance(payload, dict):
        raise DocumentFilesError(
            "json-input-invalid",
            "The JSON input must contain an object.",
            details={"path": path},
        )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="document-files")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("capabilities", help="Show exact headless capabilities")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a supported document")
    inspect_parser.add_argument("path")
    inspect_parser.add_argument("--max-chars", type=int, default=20_000)
    inspect_parser.add_argument("--no-text", action="store_true")
    inspect_parser.add_argument("--no-cells", action="store_true")

    extract_parser = subparsers.add_parser("extract", help="Extract text or Markdown")
    extract_parser.add_argument("path")
    extract_parser.add_argument("--format", choices=("text", "markdown"), default="text")
    extract_parser.add_argument("--max-chars", type=int, default=200_000)

    create_parser = subparsers.add_parser("create", help="Create HWPX from a JSON plan")
    create_parser.add_argument("plan")
    create_parser.add_argument("output")
    create_parser.add_argument("--overwrite", action="store_true")

    edit_parser = subparsers.add_parser("edit", help="Edit an HWPX copy")
    edit_parser.add_argument("input")
    edit_parser.add_argument("plan")
    edit_parser.add_argument("--output")
    edit_parser.add_argument("--apply", action="store_true")
    edit_parser.add_argument("--overwrite", action="store_true")

    verify_parser = subparsers.add_parser("verify", help="Verify HWPX")
    verify_parser.add_argument("path")
    verify_parser.add_argument("--reference")
    verify_parser.add_argument("--expect", action="append", default=[])
    verify_parser.add_argument("--forbid", action="append", default=[])

    convert_parser = subparsers.add_parser("convert", help="Convert HWP or HWPX")
    convert_parser.add_argument("input")
    convert_parser.add_argument("output")
    convert_parser.add_argument(
        "--format",
        choices=("auto", "hwpx", "text", "markdown", "svg", "pdf"),
        default="auto",
    )
    convert_parser.add_argument("--allow-lossy", action="store_true")
    convert_parser.add_argument("--page", type=int)
    convert_parser.add_argument("--overwrite", action="store_true")

    render_parser = subparsers.add_parser("render", help="Render without opening a native app")
    render_parser.add_argument("path")
    render_parser.add_argument("output")
    render_parser.add_argument(
        "--format",
        choices=("auto", "html", "svg", "pdf"),
        default="auto",
    )
    render_parser.add_argument("--page", type=int)
    render_parser.add_argument("--mode", choices=("pages", "long"), default="pages")
    render_parser.add_argument("--overwrite", action="store_true")

    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "capabilities":
        return capabilities()
    if args.command == "inspect":
        return inspect_file(
            args.path,
            include_text=not args.no_text,
            include_cells=not args.no_cells,
            max_chars=args.max_chars,
        )
    if args.command == "extract":
        return extract_file(
            args.path,
            output_format=args.format,
            max_chars=args.max_chars,
        )
    if args.command == "create":
        return create_hwpx(
            args.output,
            plan=_load_json(args.plan),
            overwrite=args.overwrite,
        )
    if args.command == "edit":
        return edit_hwpx(
            args.input,
            plan=_load_json(args.plan),
            output_path=args.output,
            dry_run=not args.apply,
            overwrite=args.overwrite,
        )
    if args.command == "verify":
        return verify_hwpx(
            args.path,
            reference_path=args.reference,
            expected_text=args.expect,
            forbidden_text=args.forbid,
        )
    if args.command == "convert":
        return convert_file(
            args.input,
            args.output,
            target_format=args.format,
            allow_lossy=args.allow_lossy,
            page=args.page,
            overwrite=args.overwrite,
        )
    if args.command == "render":
        return render_file(
            args.path,
            args.output,
            output_format=args.format,
            page=args.page,
            mode=args.mode,
            overwrite=args.overwrite,
        )
    raise AssertionError(f"unknown command: {args.command}")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "process":
        from .processor import main as process_main

        process_main(sys.argv[2:])
        return
    parser = _parser()
    try:
        result = _run(parser.parse_args())
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    except DocumentFilesError as exc:
        print(
            json.dumps({"ok": False, "error": exc.to_dict()}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "unexpected-error",
                        "message": "Document Files encountered an unexpected error.",
                        "details": {"errorType": type(exc).__name__, "message": str(exc)},
                        "suggestion": None,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
