"""Public package surface for Document Files."""

from .engine import (
    EDIT_PLAN_SCHEMA_VERSION,
    PLUGIN_VERSION,
    DocumentFilesError,
    capabilities,
    convert_file,
    create_hwpx,
    edit_hwpx,
    extract_file,
    extract_structure,
    inspect_file,
    render_file,
    render_hwpx_preview,
    verify_hwpx,
)

__all__ = [
    "EDIT_PLAN_SCHEMA_VERSION",
    "DocumentFilesError",
    "capabilities",
    "convert_file",
    "create_hwpx",
    "edit_hwpx",
    "extract_file",
    "extract_structure",
    "inspect_file",
    "render_file",
    "render_hwpx_preview",
    "verify_hwpx",
]

__version__ = PLUGIN_VERSION
