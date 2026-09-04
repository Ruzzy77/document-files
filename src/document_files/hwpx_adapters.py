"""Content-aware routing for files presented with the HWPX extension."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .extraction_errors import ExtractionError
from .extraction_protocol import (
    AdapterCapabilities,
    AdapterDescriptor,
    ExtractionAdapter,
    ExtractionEnvelope,
    ExtractionIssue,
)

OLE_COMPOUND_FILE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_ROUTER_SOURCE = Path(__file__)


class HWPXContentRouter:
    """Keep normal HWPX extraction while recognizing binary HWP file bytes."""

    def __init__(
        self,
        hwpx_adapter: ExtractionAdapter,
        binary_hwp_adapter: ExtractionAdapter,
    ) -> None:
        try:
            source_hash = hashlib.sha256(_ROUTER_SOURCE.read_bytes()).hexdigest()
        except OSError as exc:
            raise ExtractionError("packaged HWPX router source is unavailable") from exc
        self._hwpx_adapter = hwpx_adapter
        self._binary_hwp_adapter = binary_hwp_adapter
        config = {
            "ole_compound_file_signature": OLE_COMPOUND_FILE_SIGNATURE.hex(),
            "hwpx_adapter": hwpx_adapter.descriptor.to_dict(),
            "binary_hwp_adapter": binary_hwp_adapter.descriptor.to_dict(),
        }
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id="document-files.hwpx.content-router",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=config,
            capabilities=AdapterCapabilities(
                format_ids=("hwpx",),
                structural_unit_types=tuple(
                    sorted(
                        set(hwpx_adapter.descriptor.capabilities.structural_unit_types)
                        | set(binary_hwp_adapter.descriptor.capabilities.structural_unit_types)
                    )
                ),
                execution_mode="in_process",
                preserves_reading_order=False,
                supports_geometry=False,
                supports_confidence=False,
                supports_ocr=False,
                may_emit_partial=True,
            ),
        )
        self.compatible_projection_identities = frozenset()

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        if format_id != "hwpx":
            raise ExtractionError(
                "HWPX router does not declare support for this format",
                details={"format_id": format_id},
            )
        path = Path(path)
        try:
            with path.open("rb") as source:
                signature = source.read(len(OLE_COMPOUND_FILE_SIGNATURE))
        except OSError as exc:
            raise ExtractionError("adapter input is not a readable regular file") from exc

        if signature == OLE_COMPOUND_FILE_SIGNATURE:
            delegated = self._binary_hwp_adapter.extract(path, format_id="hwp")
            issues = (
                *delegated.issues,
                ExtractionIssue(
                    code="hwpx_contains_binary_hwp",
                    message=(
                        "The .hwpx-named file contains binary HWP bytes and was read "
                        "with the binary HWP adapter."
                    ),
                    severity="info",
                    details={
                        "detected_format": "hwp",
                        "declared_format": "hwpx",
                        "delegated_adapter_id": (self._binary_hwp_adapter.descriptor.adapter_id),
                    },
                ),
            )
        else:
            delegated = self._hwpx_adapter.extract(path, format_id="hwpx")
            issues = delegated.issues

        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness=delegated.completeness,
            units=delegated.units,
            issues=issues,
            coverage=delegated.coverage,
        )
