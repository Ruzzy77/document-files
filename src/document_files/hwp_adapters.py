"""Packaged binary HWP extraction adapter."""

from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

try:
    import olefile
except ModuleNotFoundError:  # Optional in a reduced OpenAI host runtime.
    from ._vendor import olefile

from .extraction_errors import BudgetExceededError, ExtractionError
from .extraction_protocol import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractionAdapter,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .hwp_structure import STRUCTURAL_UNIT_TYPES

_ADAPTER_SOURCE = Path(__file__).with_name("hwp5_adapter_main.py")
_WRAPPER_SOURCE = Path(__file__)


class HWP5SpecPartialAdapter:
    """Extract exact HWP5 section/PARA_TEXT observations without semantic guesses."""

    def __init__(self) -> None:
        try:
            source_hash = hashlib.sha256(
                _ADAPTER_SOURCE.read_bytes()
                + b"\0"
                + _WRAPPER_SOURCE.read_bytes()
                + b"\0"
                + Path(__file__).with_name("hwp_structure.py").read_bytes()
                + b"\0"
                + Path(__file__).with_name("hwp_images.py").read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise ExtractionError("packaged HWP adapter source is unavailable") from exc
        self.config = {
            "max_sections": 512,
            "max_records_per_section": 500_000,
            "max_inflated_section_bytes": 128 * 1024 * 1024,
            "max_total_inflated_bytes": 512 * 1024 * 1024,
            "max_total_records": 2_000_000,
            "specification": "hancom-hwp5-revision-1.3",
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="document-files.hwp5.spec-partial",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=self.config,
            capabilities=AdapterCapabilities(
                format_ids=("hwp",),
                structural_unit_types=STRUCTURAL_UNIT_TYPES,
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_geometry=False,
                supports_confidence=False,
                supports_ocr=False,
                may_emit_partial=True,
            ),
        )
        self.budgets = AdapterBudgets(
            timeout_seconds=60,
            max_input_bytes=1024 * 1024 * 1024,
            max_stdout_bytes=64 * 1024 * 1024,
            max_units=200_000,
            max_total_content_chars=50_000_000,
        )

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        adapter = ExternalJSONLAdapter(
            self.descriptor,
            (sys.executable, str(_ADAPTER_SOURCE)),
            self.budgets,
            config=self.config,
        )
        return adapter.extract(path, format_id=format_id)


def _hwp_security_flags(path: Path) -> tuple[bool, bool]:
    """Return encrypted and distribution flags when a valid header is readable."""

    try:
        with path.open("rb") as source, olefile.OleFileIO(source) as compound:
            if not compound.exists("FileHeader"):
                return False, False
            header = compound.openstream("FileHeader").read(40)
    except OSError:
        return False, False
    if len(header) < 40 or not header.startswith(b"HWP Document File"):
        return False, False
    flags = struct.unpack_from("<I", header, 36)[0]
    return bool(flags & 0x02), bool(flags & 0x04)


class HWP5ContentRouter:
    """Prefer the specification parser and use pinned rhwp for safe recovery."""

    def __init__(
        self,
        specification_adapter: ExtractionAdapter,
        page_text_adapter: ExtractionAdapter,
    ) -> None:
        self._specification_adapter = specification_adapter
        self._page_text_adapter = page_text_adapter
        try:
            source_hash = hashlib.sha256(_WRAPPER_SOURCE.read_bytes()).hexdigest()
        except OSError as exc:
            raise ExtractionError("packaged HWP router source is unavailable") from exc
        config = {
            "encrypted_hwp": "refuse",
            "distribution_hwp": "rhwp_page_text",
            "fallback": "rhwp_page_text_after_specification_error",
            "specification_adapter": specification_adapter.descriptor.to_dict(),
            "page_text_adapter": page_text_adapter.descriptor.to_dict(),
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="document-files.hwp5.content-router",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=config,
            capabilities=AdapterCapabilities(
                format_ids=("hwp",),
                structural_unit_types=(*STRUCTURAL_UNIT_TYPES, "page_text"),
                execution_mode="in_process",
                preserves_reading_order=False,
                supports_geometry=False,
                supports_confidence=False,
                supports_ocr=False,
                may_emit_partial=True,
            ),
        )
        # Structure-bearing results replace the older paragraph-only projection.
        # Accepting it as current would prevent the required controlled reindex.
        self.compatible_projection_identities = frozenset()

    def _wrap(
        self,
        delegated: ExtractionEnvelope,
        *,
        reason: str | None = None,
    ) -> ExtractionEnvelope:
        issues = delegated.issues
        if reason is not None:
            issues = (
                *issues,
                ExtractionIssue(
                    code="hwp_alternate_backend_used",
                    message="HWP page text was recovered with the pinned alternate backend.",
                    severity="info",
                    details={
                        "reason": reason,
                        "delegated_adapter_id": delegated.descriptor.adapter_id,
                    },
                ),
            )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=delegated.completeness,
            units=delegated.units,
            issues=issues,
            coverage=delegated.coverage,
        )

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        if format_id != "hwp":
            raise ExtractionError(
                "HWP router does not declare support for this format",
                details={"format_id": format_id},
            )
        path = Path(path)
        encrypted, distributed = _hwp_security_flags(path)
        if encrypted:
            raise ExtractionError("encrypted HWP is not supported")
        if distributed:
            delegated = self._page_text_adapter.extract(path, format_id="hwp")
            return self._wrap(delegated, reason="distribution_hwp")

        try:
            delegated = self._specification_adapter.extract(path, format_id="hwp")
        except (ExtractionError, BudgetExceededError) as primary_error:
            try:
                delegated = self._page_text_adapter.extract(path, format_id="hwp")
            except (ExtractionError, BudgetExceededError) as fallback_error:
                raise ExtractionError(
                    "all packaged HWP extraction backends failed",
                    details={
                        "primary_error": primary_error.code,
                        "fallback_error": fallback_error.code,
                    },
                ) from fallback_error
            return self._wrap(delegated, reason="specification_adapter_error")
        return self._wrap(delegated)
