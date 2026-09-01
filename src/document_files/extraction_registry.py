"""Format-to-adapter routing owned by Document Files.

Backend packages can change without making their identifiers, chunks, or
lifecycle part of the index schema.  The registry only selects an adapter;
the service continues to own revisions, projections, anchors, and authority.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .extraction_errors import ExtractionError
from .extraction_protocol import (
    AdapterDescriptor,
    ExtractionAdapter,
    ExtractionEnvelope,
    builtin_adapter_descriptor,
    run_builtin_extraction,
)
from .extractors import EXTRACTORS
from .formats import FORMAT_SPECS


class BuiltinExtractionAdapter:
    """Expose an in-process extractor through the neutral adapter API."""

    def __init__(self, adapter_name: str) -> None:
        self.adapter_name = adapter_name
        self.descriptor = builtin_adapter_descriptor(adapter_name)

    def extract(self, path: Path, *, format_id: str) -> ExtractionEnvelope:
        if format_id not in self.descriptor.capabilities.format_ids:
            raise ExtractionError(
                "built-in adapter does not declare support for this format",
                details={
                    "adapter_name": self.adapter_name,
                    "format_id": format_id,
                },
            )
        return run_builtin_extraction(path, self.adapter_name)


class AdapterRegistry:
    """Immutable routing table from source format to extraction adapter."""

    def __init__(self, adapters_by_format: Mapping[str, ExtractionAdapter]) -> None:
        routes = dict(adapters_by_format)
        for format_id, adapter in routes.items():
            if format_id not in adapter.descriptor.capabilities.format_ids:
                raise ExtractionError(
                    "adapter registry route is not declared by the adapter",
                    details={
                        "format_id": format_id,
                        "adapter_id": adapter.descriptor.adapter_id,
                    },
                )
        self._adapters_by_format = MappingProxyType(routes)

    @property
    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        by_identity = {
            (
                adapter.descriptor.adapter_id,
                adapter.descriptor.adapter_version,
                adapter.descriptor.config_hash,
            ): adapter.descriptor
            for adapter in self._adapters_by_format.values()
        }
        return tuple(by_identity[key] for key in sorted(by_identity))

    def resolve(self, format_id: str) -> ExtractionAdapter:
        try:
            return self._adapters_by_format[format_id]
        except KeyError as exc:
            raise ExtractionError(
                "no extraction adapter is registered for this format",
                details={"format_id": format_id},
            ) from exc

    def accepts_projection(
        self,
        format_id: str,
        adapter_id: str,
        adapter_version: str,
        config_hash: str,
    ) -> bool:
        """Return whether a persisted projection is current for one route.

        Most routes accept only their current descriptor.  Content routers may
        explicitly retain a bounded set of earlier identities when the new
        route preserves the meaning of those successful projections.
        """

        try:
            adapter = self.resolve(format_id)
        except ExtractionError:
            return False
        identity = (adapter_id, adapter_version, config_hash)
        current = (
            adapter.descriptor.adapter_id,
            adapter.descriptor.adapter_version,
            adapter.descriptor.config_hash,
        )
        if identity == current:
            return True
        compatible = getattr(adapter, "compatible_projection_identities", ())
        return identity in compatible


def build_default_registry(
    runtime_root: Path | None = None,
    *,
    overrides: Mapping[str, ExtractionAdapter] | None = None,
) -> AdapterRegistry:
    """Build the packaged registry, optionally replacing exact format routes."""

    builtin_adapters = {
        specification.adapter: BuiltinExtractionAdapter(specification.adapter)
        for specification in FORMAT_SPECS.values()
        if specification.adapter in EXTRACTORS
    }
    routes: dict[str, ExtractionAdapter] = {
        extension: builtin_adapters[specification.adapter]
        for extension, specification in FORMAT_SPECS.items()
        if specification.adapter in builtin_adapters
    }
    from .extraction_rhwp import RhwpPageTextAdapter
    from .hwp_adapters import HWP5ContentRouter, HWP5SpecPartialAdapter
    from .hwpx_adapters import HWPXContentRouter

    hwp_spec_adapter = HWP5SpecPartialAdapter()
    hwp_adapter = HWP5ContentRouter(
        hwp_spec_adapter,
        RhwpPageTextAdapter(runtime_root),
    )
    routes["hwp"] = hwp_adapter
    routes["hwpx"] = HWPXContentRouter(routes["hwpx"], hwp_adapter)
    if (
        runtime_root is not None
        and sys.platform == "darwin"
        and Path("/usr/bin/xcrun").is_file()
    ):
        from .native_adapters import PDFKitVisionAdapter
        from .office_ocr import OfficeVisionAdapter

        routes["pdf"] = PDFKitVisionAdapter(runtime_root)
        for format_id in ("docx", "pptx", "hwp", "hwpx"):
            routes[format_id] = OfficeVisionAdapter(
                routes[format_id], runtime_root, format_id=format_id
            )
    routes.update(overrides or {})
    return AdapterRegistry(routes)
