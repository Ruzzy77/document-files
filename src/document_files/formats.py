"""Format support declarations kept separate from extractor implementations."""

from __future__ import annotations

from dataclasses import dataclass

from .hwp_structure import STRUCTURAL_UNIT_TYPES
from .office_structure import DOCX_UNITS, PPTX_UNITS


@dataclass(frozen=True)
class FormatSpec:
    extension: str
    media_type: str
    adapter: str
    structural_units: tuple[str, ...]
    # Increment only when unchanged documents should be analyzed again.  Exact
    # adapter and build identities remain useful provenance, but ordinary code,
    # packaging, runtime, and configuration changes must not imply a bulk refresh.
    reanalysis_generation: int = 1

    def __post_init__(self) -> None:
        if (
            isinstance(self.reanalysis_generation, bool)
            or not isinstance(self.reanalysis_generation, int)
            or self.reanalysis_generation < 1
        ):
            raise ValueError("reanalysis generation must be a positive integer")


FORMAT_SPECS = {
    "md": FormatSpec("md", "text/markdown", "markdown", ("heading", "paragraph")),
    "markdown": FormatSpec("markdown", "text/markdown", "markdown", ("heading", "paragraph")),
    "txt": FormatSpec("txt", "text/plain", "text", ("paragraph",)),
    "html": FormatSpec("html", "text/html", "html", ("heading", "paragraph", "table")),
    "htm": FormatSpec("htm", "text/html", "html", ("heading", "paragraph", "table")),
    "pdf": FormatSpec("pdf", "application/pdf", "pdf", ("page",)),
    "docx": FormatSpec(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
        DOCX_UNITS,
    ),
    "pptx": FormatSpec(
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pptx",
        PPTX_UNITS,
    ),
    "xlsx": FormatSpec(
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
        ("sheet", "sheet_cell"),
    ),
    "hwpx": FormatSpec(
        "hwpx",
        "application/vnd.hancom.hwpx",
        "hwpx",
        STRUCTURAL_UNIT_TYPES,
    ),
    "hwp": FormatSpec(
        "hwp",
        "application/x-hwp",
        "hwp5",
        ("section_paragraph",),
    ),
}

IGNORED_NAMES = {".DS_Store"}


def classify(name: str) -> tuple[str, str | None, str | None, str]:
    if name in IGNORED_NAMES:
        return "", None, None, "ignored"
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    spec = FORMAT_SPECS.get(extension)
    if spec is None:
        return extension, None, None, "unsupported"
    return extension, spec.media_type, spec.adapter, "supported"
