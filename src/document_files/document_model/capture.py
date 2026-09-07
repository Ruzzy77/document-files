"""Self-contained native parts, preserving lexical text and resources without extraction to disk."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile


def capture(content: bytes, format_id: str, *, max_expanded_bytes: int) -> dict:
    result = {
        "schemaVersion": "document-files.reconstruction-context.v1",
        "format": format_id,
        "status": "unverified",
        "parts": [],
        "issues": [],
        "recipientReconstructionVerified": False,
    }
    if format_id in {"txt", "md", "markdown", "html", "htm"}:
        try:
            text = content.decode("utf-8")
            result["parts"] = [
                {"id": "source-text", "kind": "lexical_text", "text": text, "encoding": "utf-8"}
            ]
            result["nativeCaptureComplete"] = True
            return result
        except UnicodeDecodeError:
            result["issues"].append("source_encoding_not_decoded")
    if format_id in {"docx", "pptx", "xlsx", "hwpx"}:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            members = package.infolist()
            if (
                len(members) > 10000
                or sum(m.file_size for m in members) > max_expanded_bytes
                or any(m.flag_bits & 1 for m in members)
            ):
                result["issues"].append("native_parts_budget_or_encryption")
            else:
                for member in members:
                    data = package.read(member)
                    part = {
                        "id": member.filename,
                        "kind": "native_part",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "size": len(data),
                    }
                    if member.filename.endswith((".xml", ".rels")) or member.filename == "mimetype":
                        try:
                            part.update(text=data.decode("utf-8"), encoding="utf-8")
                        except UnicodeDecodeError:
                            part.update(data=base64.b64encode(data).decode(), encoding="base64")
                    else:
                        part.update(data=base64.b64encode(data).decode(), encoding="base64")
                    result["parts"].append(part)
                result["nativeCaptureComplete"] = True
                return result
    result["nativeCaptureComplete"] = False
    result["issues"].append("structured_native_capture_unavailable")
    return result
