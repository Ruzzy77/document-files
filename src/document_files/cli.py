"""Command-line interface for Document Files."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .engine import (
    DocumentFilesError,
    capabilities,
    convert_file,
    create_hwpx,
    edit_hwpx,
    extract_file,
    extract_structure,
    inspect_file,
    render_file,
    verify_hwpx,
)
from .interpretation.backends import ModelError
from .jobs import JobError
from .runtime_packs import PackError


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
    subparsers.add_parser("diagnose", help="Diagnose local configuration without network calls")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a supported document")
    inspect_parser.add_argument("path")
    inspect_parser.add_argument("--max-chars", type=int, default=20_000)
    inspect_parser.add_argument("--no-text", action="store_true")
    inspect_parser.add_argument("--no-cells", action="store_true")

    extract_parser = subparsers.add_parser("extract", help="Extract text or Markdown")
    extract_parser.add_argument("path")
    extract_parser.add_argument("--format", choices=("text", "markdown"), default="text")
    extract_parser.add_argument("--max-chars", type=int, default=200_000)

    structure_parser = subparsers.add_parser(
        "extract-structure",
        help="Extract paged source structure and explicit values",
    )
    structure_parser.add_argument("path")
    structure_parser.add_argument("--unit-offset", type=int, default=0)
    structure_parser.add_argument("--max-units", type=int, default=500)
    structure_parser.add_argument("--no-text", action="store_true")

    schema_parser = subparsers.add_parser("extract-schema", help="AI-assisted schema extraction")
    schema_parser.add_argument("path")
    schema_parser.add_argument("--options", help="Extraction options JSON file")
    schema_parser.add_argument("--request-id")
    schema_parser.add_argument("--storage-dir")
    schema_parser.add_argument("--no-retain", action="store_true")
    schema_parser.add_argument("--config", help="Administrator runtime profile JSON")
    schema_parser.add_argument("--profile", help="Configured model profile name")
    result_parser = subparsers.add_parser("get-extraction", help="Read retained extraction result")
    result_parser.add_argument("job_id")
    result_parser.add_argument("--section")
    result_parser.add_argument("--offset", type=int, default=0)
    result_parser.add_argument("--limit", type=int, default=100)
    result_parser.add_argument("--storage-dir")
    resume_parser = subparsers.add_parser("resume-extraction", help="Resume a retained extraction")
    resume_parser.add_argument("job_id")
    resume_parser.add_argument("--path", help="Optional relocated, byte-identical input")
    resume_parser.add_argument("--storage-dir")
    resume_parser.add_argument("--config")
    resume_parser.add_argument("--profile")
    resume_parser.add_argument("--additional-budget", help="Explicit additional budget JSON file")
    delete_parser = subparsers.add_parser(
        "delete-extraction", help="Delete retained result and checkpoint"
    )
    delete_parser.add_argument("job_id")
    delete_parser.add_argument("--storage-dir")

    create_parser = subparsers.add_parser("create", help="Create HWPX from a JSON plan")
    create_parser.add_argument("plan")
    create_parser.add_argument("output")
    create_parser.add_argument("--overwrite", action="store_true")

    edit_parser = subparsers.add_parser("edit", help="Edit an HWPX copy")
    edit_parser.add_argument("input")
    edit_parser.add_argument("plan")
    edit_parser.add_argument("--output")
    edit_parser.add_argument("--dry-run", action="store_true")
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

    serve = subparsers.add_parser(
        "serve", help="Run an explicit authenticated Document Files HTTP service"
    )
    serve.add_argument("--config", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    start = subparsers.add_parser(
        "start-job", help="Submit to an explicitly running Document Files service"
    )
    start.add_argument("path")
    start.add_argument("--profile", required=True)
    start.add_argument("--options")
    start.add_argument("--idempotency-key")
    for name in ("job-status", "job-result", "cancel-job", "resume-job", "delete-job"):
        managed = subparsers.add_parser(name)
        managed.add_argument("job_id")
        if name == "job-result":
            managed.add_argument("--section")
            managed.add_argument("--offset", type=int, default=0)
            managed.add_argument("--limit", type=int, default=100)
        if name == "resume-job":
            managed.add_argument("--additional-budget")
    packs = subparsers.add_parser(
        "packs", help="Inspect or explicitly install/activate/rollback offline packs"
    )
    packs.add_argument("action", choices=("list", "install", "activate", "rollback"))
    packs.add_argument("--root", required=True)
    packs.add_argument("--archive")
    packs.add_argument("--sha256")
    packs.add_argument("--id")
    packs.add_argument("--version")
    return parser


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "serve":
        from .http_server import serve

        serve(args.config, host=args.host, port=args.port)
        return {"status": "stopped"}
    if args.command == "packs":
        from .runtime_packs import PackStore

        store = PackStore(args.root)
        if args.action == "list":
            return store.inspect()
        if args.action == "install":
            if not args.archive or not args.sha256:
                raise DocumentFilesError(
                    "pack-arguments-required",
                    "Supply the archive and independently verified SHA256.",
                )
            installed = store.install(args.archive, args.sha256)
            return {
                "id": installed.manifest["id"],
                "version": installed.manifest["version"],
                "manifestSha256": installed.manifest_sha256,
                "activated": False,
            }
        if not args.id or args.action == "activate" and not args.version:
            raise DocumentFilesError(
                "pack-arguments-required", "Supply the pack ID and activation version."
            )
        return (
            store.activate(args.id, args.version)
            if args.action == "activate"
            else store.rollback(args.id)
        )
    if args.command in {
        "start-job",
        "job-status",
        "job-result",
        "cancel-job",
        "resume-job",
        "delete-job",
    }:
        from .job_client import JobClient

        client = JobClient.from_environment()
        if args.command == "start-job":
            return client.start(
                args.path,
                profile=args.profile,
                options=_load_json(args.options) if args.options else None,
                idempotency_key=args.idempotency_key,
            )
        actions = {
            "job-status": "status",
            "job-result": "result",
            "cancel-job": "cancel",
            "resume-job": "resume",
            "delete-job": "delete",
        }
        return client.job(
            args.job_id,
            actions[args.command],
            section=getattr(args, "section", None),
            offset=getattr(args, "offset", 0),
            limit=getattr(args, "limit", 100),
            additional_budget=_load_json(args.additional_budget)
            if getattr(args, "additional_budget", None)
            else None,
        )
    if args.command == "capabilities":
        return capabilities()
    if args.command == "diagnose":
        from .diagnostics import diagnose

        return diagnose()
    if args.command == "resume-extraction":
        from .interpretation.workflow import resume_extraction
        from .profiles import profile_clients

        if bool(args.config) != bool(args.profile):
            raise DocumentFilesError("profile-required", "Use --config and --profile together.")
        with (
            profile_clients(args.config, args.profile)
            if args.profile
            else nullcontext((None, None)) as clients
        ):
            return resume_extraction(
                args.job_id,
                path=args.path,
                storage_dir=args.storage_dir,
                model_client=clients[0],
                observation_backend=clients[1],
                additional_budget=_load_json(args.additional_budget)
                if args.additional_budget
                else None,
            )
    if args.command == "delete-extraction":
        from .interpretation.workflow import delete_extraction

        return delete_extraction(args.job_id, storage_dir=args.storage_dir)
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
    if args.command == "extract-structure":
        return extract_structure(
            args.path,
            unit_offset=args.unit_offset,
            max_units=args.max_units,
            include_text=not args.no_text,
        )
    if args.command == "extract-schema":
        from .interpretation.workflow import extract_schema
        from .profiles import profile_clients

        if bool(args.config) != bool(args.profile):
            raise DocumentFilesError("profile-required", "Use --config and --profile together.")
        with (
            profile_clients(args.config, args.profile)
            if args.profile
            else nullcontext((None, None)) as clients
        ):
            return extract_schema(
                args.path,
                options=_load_json(args.options) if args.options else None,
                request_id=args.request_id,
                storage_dir=args.storage_dir,
                retain=not args.no_retain,
                model_client=clients[0],
                observation_backend=clients[1],
            )
    if args.command == "get-extraction":
        from .interpretation.workflow import get_extraction

        return get_extraction(
            args.job_id,
            section=args.section,
            offset=args.offset,
            limit=args.limit,
            storage_dir=args.storage_dir,
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
            dry_run=args.dry_run,
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
    except (ModelError, PackError, JobError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": DocumentFilesError(
                        exc.code, "The requested configuration or operation is unavailable."
                    ).to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "runtime_unavailable",
                        "message": "This host is missing a required Document Files dependency.",
                        "details": {"missingModule": getattr(exc, "name", None)},
                        "suggestion": None,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
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
                        "details": {"errorType": type(exc).__name__},
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
