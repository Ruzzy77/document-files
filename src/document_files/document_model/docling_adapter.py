"""Explicit offline Docling worker configuration and source-addressed result import."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import Path

from ..portability import WindowsJob, kill_process_tree, process_options, subprocess_environment
from .model import ObservationDocument
from .native import bind_spans


class RecognitionCheckpointError(RuntimeError):
    """Persistence failure is not a document-recognition quality limitation."""

    def __init__(self):
        super().__init__("recognition checkpoint persistence failed")


@dataclass(frozen=True)
class RecognitionConfig:
    artifacts_path: str
    tesseract_cmd: str
    tessdata_path: str
    python: str = sys.executable
    timeout_seconds: int = 600
    max_output_bytes: int = 64 * 1024 * 1024
    max_pages: int = 500
    threads: int = 2
    table_ocr_repair: str = "off"
    repair_max_tables: int = 8
    repair_max_calls: int = 8
    repair_batch_size: int = 1
    repair_max_images: int = 8
    repair_max_input_pixels: int = 16000000
    repair_max_pixels: int = 16000000
    repair_max_seconds: int = 60

    def validate(self) -> None:
        if self.table_ocr_repair not in {"off", "ruled_tables_v1", "ruled_cells_v2"}:
            raise ValueError("recognition repair policy invalid")
        if self.repair_batch_size == 2 and self.table_ocr_repair != "ruled_cells_v2":
            raise ValueError("cell image batching requires ruled_cells_v2")
        limits = (
            (self.repair_max_tables, 32),
            (self.repair_max_calls, 32),
            (self.repair_batch_size, 2),
            (self.repair_max_images, 64),
            (self.repair_max_input_pixels, 64000000),
            (self.repair_max_pixels, 64000000),
            (self.repair_max_seconds, 300),
        )
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in limits):
            raise ValueError("recognition repair budget invalid")
        if not 1 <= self.timeout_seconds <= 3600 or not 1 <= self.max_pages <= 2000:
            raise ValueError("recognition budget invalid")
        if not 1024 <= self.max_output_bytes <= 256 * 1024 * 1024 or not 1 <= self.threads <= 32:
            raise ValueError("recognition budget invalid")
        for path in (self.artifacts_path, self.tesseract_cmd, self.tessdata_path, self.python):
            if not Path(path).is_absolute() or not Path(path).exists():
                raise ValueError("recognition requires explicitly installed local paths")
        if not Path(self.artifacts_path).is_dir() or not Path(self.tessdata_path).is_dir():
            raise ValueError("recognition model directories unavailable")
        if not Path(self.python).is_file() or not Path(self.tesseract_cmd).is_file():
            raise ValueError("recognition executable unavailable")
        # OSD is orientation assistance, not a third body-text OCR language.
        for language in ("kor", "eng", "osd"):
            if not (Path(self.tessdata_path) / f"{language}.traineddata").is_file():
                raise ValueError("required local OCR data unavailable")
        if not (Path(self.tessdata_path) / "configs" / "tsv").is_file():
            raise ValueError("required local OCR TSV configuration unavailable")


def _worker_result(output, limit: int, *, interrupted: str | None = None) -> dict:
    """Recover only complete page frames; an interrupted trailing frame is not accepted."""
    output.seek(0)
    raw = output.read(limit + 1)
    pages, final, malformed = [], None, False
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError
            if record.get("type") == "page":
                page = record["pageResult"]
                if not isinstance(page, dict) or type(page.get("page")) is not int:
                    raise ValueError
                if any(p["page"] == page["page"] for p in pages):
                    raise ValueError
                pages.append(page)
            else:
                final = record
        except (ValueError, KeyError, TypeError):
            malformed = True
            break
    result = final if isinstance(final, dict) else {"status": "partial", "issues": []}
    if pages:
        result["pageResults"] = pages
        result["completedPages"] = [
            page["page"] for page in pages if page.get("status") == "complete"
        ]
        result["processedPages"] = [page["page"] for page in pages]
    code = interrupted
    if len(raw) > limit:
        code = "recognition_output_budget_exceeded"
    elif malformed or final is None:
        code = code or "recognition_worker_output_incomplete"
    if code:
        result["status"] = "partial"
        result.setdefault("issues", []).append({"code": code})
    return result


class DoclingRecognition:
    """A separate process owns heavy model memory and releases it before returning."""

    supports_checkpoints = True

    def __init__(
        self,
        config: RecognitionConfig,
        *,
        identity: dict | None = None,
        parent_managed: bool = False,
    ):
        self._config = config
        self.parent_managed = parent_managed
        supplied = deepcopy(identity) if identity is not None else {}
        if not isinstance(supplied, dict):
            raise ValueError("recognition identity must be an object")
        # Reject mutable/non-JSON identity objects before a checkpoint fingerprint is made.
        json.dumps(supplied, allow_nan=False, sort_keys=True)
        manifest = supplied.get("packManifestSha256", supplied.get("manifestSha256"))
        self._identity = {
            **supplied,
            "adapter": "docling-offline-worker",
            "adapterVersion": "24",
            "configuration": asdict(config),
            "modelPinning": (
                "caller_supplied_manifest"
                if isinstance(manifest, str) and re.fullmatch(r"[0-9a-fA-F]{64}", manifest)
                else "unverified"
            ),
        }

    @property
    def config(self) -> RecognitionConfig:
        return self._config

    @property
    def identity(self) -> dict:
        """Configuration paths identify setup, not immutable model contents."""
        return deepcopy(self._identity)

    def observe(
        self,
        content: bytes,
        *,
        restore: dict | None = None,
        checkpoint: Callable[[dict], None] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        attempt_timeout = self.config.timeout_seconds
        if timeout_seconds is not None:
            if (
                isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds)
                or timeout_seconds <= 0
            ):
                raise ValueError("invalid recognition attempt timeout")
            attempt_timeout = min(attempt_timeout, timeout_seconds)
        source_hash = hashlib.sha256(content).hexdigest()
        state = {
            "version": "document-files.recognition-checkpoint.v1",
            "sourceSha256": source_hash,
            "backendIdentity": self.identity,
            "pageResults": [],
            "completedPages": [],
        }
        if restore is not None:
            try:
                if (
                    not isinstance(restore, dict)
                    or any(
                        restore.get(key) != state[key]
                        for key in ("version", "sourceSha256", "backendIdentity")
                    )
                    or not isinstance(restore.get("pageResults"), list)
                    or not isinstance(restore.get("completedPages"), list)
                ):
                    raise ValueError
                pages = restore["pageResults"]
                if len(json.dumps(pages, allow_nan=False).encode()) > self.config.max_output_bytes:
                    raise ValueError
                indices = []
                for page in pages:
                    if (
                        not isinstance(page, dict)
                        or type(page.get("page")) is not int
                        or not 1 <= page["page"] <= self.config.max_pages
                        or page.get("sourceSha256") != source_hash
                        or page.get("status") not in {"complete", "partial"}
                        or not isinstance(page.get("document"), dict)
                    ):
                        raise ValueError
                    indices.append(page["page"])
                complete = sorted(p["page"] for p in pages if p["status"] == "complete")
                if (
                    len(set(indices)) != len(indices)
                    or any(type(p) is not int for p in restore["completedPages"])
                    or restore["completedPages"] != complete
                ):
                    raise ValueError
                state["pageResults"] = deepcopy(pages)
                state["completedPages"] = complete
            except (ValueError, TypeError, KeyError):
                raise ValueError("recognition checkpoint does not match input or backend") from None
        retained = {p["page"]: p for p in state["pageResults"]}

        def accept(page):
            if (
                not isinstance(page, dict)
                or type(page.get("page")) is not int
                or not 1 <= page["page"] <= self.config.max_pages
                or page.get("sourceSha256") != source_hash
                or page.get("status") not in {"complete", "partial"}
                or not isinstance(page.get("document"), dict)
            ):
                raise ValueError("recognition page frame invalid")
            if page["page"] in state["completedPages"]:
                if retained[page["page"]] != page:
                    raise ValueError("recognition rewrote a completed page")
                return
            candidate = {**retained, page["page"]: page}
            if (
                len(json.dumps(list(candidate.values()), allow_nan=False).encode())
                > self.config.max_output_bytes
            ):
                raise ValueError("recognition retained output budget exceeded")
            retained[page["page"]] = deepcopy(page)
            state["pageResults"] = [retained[p] for p in sorted(retained)]
            state["completedPages"] = sorted(
                p for p in retained if retained[p]["status"] == "complete"
            )
            if checkpoint:
                try:
                    checkpoint(deepcopy(state))
                except Exception:
                    raise RecognitionCheckpointError() from None

        def merged(result):
            for page in result.get("pageResults", []):
                accept(page)
            if retained:
                result["pageResults"] = deepcopy(state["pageResults"])
                result["completedPages"] = list(state["completedPages"])
                result["processedPages"] = sorted(retained)
            return result

        try:
            self.config.validate()
        except ValueError:
            return merged(
                {
                    "status": "unavailable",
                    "issues": [{"code": "recognition_runtime_unavailable"}],
                }
            )
        if len(content) > 128 * 1024 * 1024:
            return {"status": "partial", "issues": [{"code": "recognition_input_budget_exceeded"}]}
        environment = subprocess_environment()
        environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
                # Runtime packs are checksummed, immutable installations. Imports
                # must neither add bytecode files nor pick up host user packages.
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TESSDATA_PREFIX": self.config.tessdata_path,
            }
        )
        # Independently opened descriptors have independent file offsets. Never seek
        # the writer fd while the subprocess owns it, including on timeout.
        with tempfile.TemporaryDirectory(prefix="document-files-recognition-") as directory:
            path = Path(directory) / "output.ndjson"
            partial_path = Path(directory) / "partial-pages.json"
            partial_path.write_text(
                json.dumps(
                    [p for p in retained.values() if p["status"] == "partial"], ensure_ascii=False
                )
            )
            with path.open("w+b", buffering=0) as output, path.open("rb", buffering=0) as reader:
                process = None
                job = None
                communicator = None
                done = threading.Event()
                communication_error = []
                pending = bytearray()
                consumed = 0

                def read_progress():
                    nonlocal pending, consumed
                    while chunk := reader.read(65536):
                        consumed += len(chunk)
                        if consumed > self.config.max_output_bytes:
                            raise ValueError("recognition output budget exceeded")
                        pending.extend(chunk)
                        while (boundary := pending.find(b"\n")) >= 0:
                            line = bytes(pending[:boundary])
                            del pending[: boundary + 1]
                            record = json.loads(line)
                            if isinstance(record, dict) and record.get("type") == "page":
                                accept(record["pageResult"])

                try:
                    process = subprocess.Popen(
                        [
                            self.config.python,
                            "-I",
                            "-B",
                            str(Path(__file__).with_name("recognition_bootstrap.py").resolve()),
                            "--config",
                            json.dumps(asdict(self.config)),
                            "--completed-pages",
                            json.dumps(state["completedPages"]),
                            "--partial-pages-file",
                            str(partial_path),
                        ],
                        stdin=subprocess.PIPE,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                        env=environment,
                        close_fds=True,
                        **({} if self.parent_managed else process_options(supervised=True)),
                    )
                    if not self.parent_managed:
                        job = WindowsJob(process)

                    def communicate():
                        try:
                            process.communicate(content, timeout=attempt_timeout)
                        except BaseException as error:
                            communication_error.append(error)
                        finally:
                            done.set()

                    communicator = threading.Thread(target=communicate, name="recognition-stdin")
                    communicator.start()
                    while not done.wait(0.05):
                        read_progress()
                    read_progress()
                    error = communication_error[0] if communication_error else None
                    interrupted = (
                        "recognition_timeout"
                        if isinstance(error, subprocess.TimeoutExpired)
                        else "recognition_worker_failed"
                        if error or process.returncode != 0
                        else None
                    )
                    return merged(
                        _worker_result(
                            reader, self.config.max_output_bytes, interrupted=interrupted
                        )
                    )
                except (OSError, ValueError):
                    # Pages already accepted/checkpointed survive protocol failures.
                    return merged(
                        {
                            "status": "partial",
                            "issues": [{"code": "recognition_worker_unavailable"}],
                        }
                    )
                finally:
                    if process is not None:
                        if self.parent_managed:
                            if process.poll() is None:
                                process.kill()
                        else:
                            kill_process_tree(process)
                        process.wait()
                    if communicator is not None:
                        communicator.join()
                    if job is not None:
                        job.close()


def box(bbox: dict | None, page_height=None) -> dict | None:
    if not isinstance(bbox, dict):
        return None
    try:
        left, top, right, bottom = (float(bbox[k]) for k in ("l", "t", "r", "b"))
        if not all(math.isfinite(v) for v in (left, top, right, bottom)):
            return None
        origin = bbox.get("coord_origin", "TOPLEFT")
        result = {
            "left": min(left, right),
            "top": min(top, bottom),
            "right": max(left, right),
            "bottom": max(top, bottom),
            "origin": origin,
            "sourceBox": dict(bbox),
        }
        if origin == "BOTTOMLEFT" and page_height is not None:
            result.update(
                top=page_height - max(top, bottom),
                bottom=page_height - min(top, bottom),
                origin="TOPLEFT",
            )
        return result
    except (ValueError, KeyError, TypeError):
        return None


def import_docling(doc: ObservationDocument, exported: dict, *, prefix="docling") -> list[str]:
    """Use Docling's table_cells, never the duplicated grid computed by export helpers."""
    pages = exported.get("pages", {})
    ids = []

    def locator(item):
        prov = item.get("prov") or []
        result = {
            "doclingRef": item.get("self_ref"),
            "recognitionBatch": prefix,
            "provenance": prov,
            "sourceKind": "docling_native_or_ocr",
        }
        if len(prov) == 1:
            page = prov[0].get("page_no")
            result["page"] = page
            page_info = pages.get(str(page), pages.get(page, {}))
            result["bbox"] = box(prov[0].get("bbox"), page_info.get("size", {}).get("height"))
        return result

    for index, item in enumerate(exported.get("texts", [])):
        text = item.get("text", "")
        if not isinstance(text, str):
            doc.issue("recognition_text_invalid")
            continue
        node_id = doc.node(
            f"{prefix}:text:{index}",
            text,
            role=item.get("label", "text"),
            locator=locator(item),
            observationBasis="recognition",
            recognizedText=True,
        )
        if isinstance(item.get("orig"), str):
            doc.nodes[node_id]["originalRecognitionText"] = item["orig"]
            doc.nodes[node_id]["semantic"] = {
                "value": {
                    "kind": "text",
                    "value": text,
                    "raw": item["orig"],
                    "basis": "docling_original_text",
                }
            }
            doc.bind(node_id, path="/semantic/value/raw", candidateRole="recognized_original")
            if item["orig"] != text:
                doc.nodes[node_id]["normalization"] = "docling_text_normalization"
        bind_spans(doc, node_id)
        ids.append(node_id)
    for index, item in enumerate(exported.get("tables", [])):
        table_ref = f"{prefix}:table:{index}"
        loc = locator(item)
        table = {
            "id": table_ref,
            "cells": [],
            "basis": "docling_table_cells",
            "indexBase": 0,
            "locator": loc,
            "page": loc.get("page"),
            "contextNodeIds": [],
        }
        data = item.get("data", {})
        for source_key, target_key in (
            ("num_rows", "declaredRowCount"),
            ("num_cols", "declaredColCount"),
        ):
            value = data.get(source_key)
            if type(value) is int and 0 <= value <= 100000:
                table[target_key] = value
        table["contentCompleteness"] = "unverified"
        cells = data.get("table_cells")
        if not isinstance(cells, list):
            doc.issue("recognition_table_cells_missing", tableRef=table_ref)
            doc.tables[table_ref] = table
            continue
        for ordinal, cell in enumerate(cells):
            try:
                row, col = cell["start_row_offset_idx"], cell["start_col_offset_idx"]
                end_row, end_col = cell["end_row_offset_idx"], cell["end_col_offset_idx"]
                if not all(type(v) is int for v in (row, col, end_row, end_col)):
                    raise ValueError
                if not 0 <= row < end_row <= 100000 or not 0 <= col < end_col <= 100000:
                    raise ValueError
                rowspan, colspan = end_row - row, end_col - col
                if (
                    cell.get("row_span", rowspan) != rowspan
                    or cell.get("col_span", colspan) != colspan
                ):
                    raise ValueError
                text = cell.get("text", "")
                if not isinstance(text, str):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                doc.issue("recognition_table_cell_invalid", tableRef=table_ref, cellOrdinal=ordinal)
                continue
            cell_loc = {
                **loc,
                "tableRef": table_ref,
                "cellOrdinal": ordinal,
                "tableBBox": loc.get("bbox"),
                "bbox": None,
            }
            if "ref" in cell:
                cell_loc["richContentRef"] = cell["ref"]
                doc.issue(
                    "recognition_rich_cell_unresolved", tableRef=table_ref, cellOrdinal=ordinal
                )
            if cell.get("bbox"):
                page = cell_loc.get("page")
                height = pages.get(str(page), pages.get(page, {})).get("size", {}).get("height")
                cell_loc["bbox"] = box(cell["bbox"], height)
            if "page" not in cell_loc:
                doc.issue(
                    "recognition_cell_page_unresolved", tableRef=table_ref, cellOrdinal=ordinal
                )
            node_id = doc.node(
                f"{table_ref}/cell/{ordinal}",
                text,
                role="table_cell",
                locator=cell_loc,
                observationBasis="recognition",
                recognizedText=True,
            )
            bind_spans(doc, node_id)
            ids.append(node_id)
            table["cells"].append(
                {
                    "sourceRef": node_id,
                    "row": row,
                    "col": col,
                    "rowSpan": rowspan,
                    "colSpan": colspan,
                    "isHeader": bool(cell.get("column_header") or cell.get("row_header")),
                    "columnHeader": bool(cell.get("column_header")),
                    "rowHeader": bool(cell.get("row_header")),
                    "rowSection": bool(cell.get("row_section")),
                }
            )
        rows, cols = table.get("declaredRowCount"), table.get("declaredColCount")
        if rows is not None and cols is not None and rows * cols <= 100000:
            occupied = set()
            for cell in table["cells"]:
                if cell["row"] + cell["rowSpan"] > rows or cell["col"] + cell["colSpan"] > cols:
                    doc.issue("recognition_table_dimensions_conflict", tableRef=table_ref)
                    continue
                positions = {
                    (r, c)
                    for r in range(cell["row"], cell["row"] + cell["rowSpan"])
                    for c in range(cell["col"], cell["col"] + cell["colSpan"])
                }
                if occupied & positions:
                    doc.issue("recognition_table_cells_overlap", tableRef=table_ref)
                occupied.update(positions)
            missing = rows * cols - len(occupied)
            if missing:
                table["unobservedCellCount"] = missing
                doc.issue(
                    "recognition_table_cells_unobserved",
                    tableRef=table_ref,
                    count=missing,
                    meaning="not_observed_not_proven_blank",
                )
        doc.tables[table_ref] = table
    if exported.get("pictures"):
        doc.issue("recognized_picture_semantics_uninterpreted", count=len(exported["pictures"]))
    doc.provenance["doclingImport"] = {
        "tableRepresentation": "table_cells",
        "gridUsed": False,
        "recognitionIsSourceGroundTruth": False,
    }
    return ids
