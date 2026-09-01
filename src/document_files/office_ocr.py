"""Bounded local image OCR, preserving native text and resumable source order."""

from __future__ import annotations

import hashlib
import math
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from .extraction_errors import BudgetExceededError, DocumentExtractionError, ExtractionError
from .extraction_protocol import (
    AdapterBudgets,
    AdapterCapabilities,
    AdapterDescriptor,
    ExternalJSONLAdapter,
    ExtractedUnit,
    ExtractionEnvelope,
    ExtractionIssue,
)
from .extractors import MAX_UNIT_CHARS, normalize_text
from .hwp_images import EmbeddedImageArchive
from .metafile_images import single_bitmap_emf
from .metafile_text import stored_emf_strings
from .native_adapters import PDFKitVisionAdapter

_MESSAGES = {
    "office_image_placement_unresolved": "A cropped or ambiguous image placement was left unread.",
    "office_image_size_limit": "An embedded image exceeds the configured byte or pixel budget.",
    "office_image_ocr_budget_reached": (
        "Further image recognition exceeds the count or time budget."
    ),
    "office_image_ocr_failed": (
        "Vision could not process an embedded image; native content was retained."
    ),
    "office_image_format_unsupported": (
        "The local image decoder does not support an embedded image format."
    ),
    "office_image_decode_failed": (
        "An embedded image could not be decoded; native content was retained."
    ),
    "office_image_without_text": "Vision found no text in an embedded image.",
    "office_image_ocr_output_limit": "Image recognition exceeds the configured result size.",
    "office_image_ocr_observed": (
        "Local OCR recovered text at the original embedded image location."
    ),
    "office_image_ocr_padding_observed": (
        "A bounded margin let Vision process an otherwise rejected thin image."
    ),
    "office_image_visual_content_partial": (
        "OCR text does not reconstruct the image's non-text visual content."
    ),
    "office_metafile_text_observed": (
        "Stored Unicode text records were read without replaying metafile graphics."
    ),
}


def _source_crop(location, observations=None):
    """Return the visible source rectangle, recording how the source bounded it.

    A negative OOXML rectangle is a valid outset: it adds padding that has no
    source pixels, so only the intersection with the stored image is recognized.
    """
    if location.get("image_crop_unresolved"):
        raise ValueError("Hancom image crop or appearance is unresolved")
    if "source_crop_bbox" in location:
        values = location["source_crop_bbox"]
        if (
            len(values) != 4
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
            or not (0 <= values[0] < values[2] <= 1 and 0 <= values[1] < values[3] <= 1)
        ):
            raise ValueError("invalid normalized source crop")
        return None if list(values) == [0, 0, 1, 1] else tuple(values)
    crops = location.get("source_crops", [])
    if not crops:
        return None
    if len(crops) != 1 or set(crops[0]) - {"l", "t", "r", "b"}:
        raise ValueError("ambiguous source crop")
    values = [int(crops[0].get(key, "0")) for key in ("l", "t", "r", "b")]
    left, top, right, bottom = values
    if (
        any(not -100_000 < value < 100_000 for value in values)
        or left + right >= 100_000
        or top + bottom >= 100_000
    ):
        raise ValueError("source crop exceeds its bound or has no visible area")
    declared = (
        left / 100_000,
        top / 100_000,
        1 - right / 100_000,
        1 - bottom / 100_000,
    )
    visible = tuple(min(max(value, 0.0), 1.0) for value in declared)
    if visible[0] >= visible[2] or visible[1] >= visible[3]:
        raise ValueError("source crop keeps no visible source pixels")
    if observations is not None and visible != declared:
        observations.update(
            source_crop_outset=True,
            recognized_scope="visible_source_pixels_only",
        )
    return None if visible == (0.0, 0.0, 1.0, 1.0) else visible


def _image_position(location):
    return (
        location.get(
            "part", location.get("section_file", location.get("section_stream"))
        ),
        location.get("element", location.get("image_record")),
    )


class OfficeVisionAdapter:
    """Native XML always wins; OCR never replaces existing native units."""

    def __init__(self, native, runtime_root, *, format_id=None):
        self.native = native
        self.vision = PDFKitVisionAdapter(runtime_root)
        source_hash = hashlib.sha256(
            Path(__file__).read_bytes()
            + b"\0"
            + Path(__file__).with_name("hwp_images.py").read_bytes()
            + b"\0"
            + Path(__file__).with_name("metafile_images.py").read_bytes()
            + b"\0"
            + Path(__file__).with_name("metafile_text.py").read_bytes()
        ).hexdigest()
        self.config = {
            "native": native.descriptor.to_dict(),
            "vision_source": self.vision.source_hash,
            "runtime": self.vision.config["runtime"],
            "scope": "embedded_images_sparse_first",
            "continuation": "current_projection_image_order_v1",
            "native_text_min_alphanumeric_characters": 32,
            "max_images": 16,
            "max_image_bytes": 16 * 1024 * 1024,
            "max_total_image_bytes": 32 * 1024 * 1024,
            "timeout_seconds": 60,
            "max_edge_pixels": 3000,
            "recognition_languages": ["ko-KR", "en-US"],
            "cropped_images": "verified_visible_source_rect_v2",
            "non_displayed_images": "skip_proven_zero_display_area_v1",
            "metafile_stored_text": "emr_exttextoutw_unicode_records_v1",
        }
        caps = native.descriptor.capabilities
        self.descriptor = AdapterDescriptor.from_config(
            adapter_id=f"document-files.native.office-vision.{format_id or native.adapter_name}",
            adapter_version=f"1.0.0+source.{source_hash[:12]}",
            config=self.config,
            capabilities=replace(
                caps,
                structural_unit_types=(
                    *caps.structural_unit_types,
                    "image_text",
                    "image_native_text",
                ),
                supports_ocr=True,
                supports_geometry=True,
                supports_confidence=True,
            ),
        )
        recognition_config = {
            **{
                key: self.config[key]
                for key in (
                    "vision_source",
                    "runtime",
                    "max_edge_pixels",
                    "recognition_languages",
                )
            },
            "metafile_source": hashlib.sha256(
                Path(__file__).with_name("metafile_images.py").read_bytes()
            ).hexdigest(),
            "image_archive_source": hashlib.sha256(
                Path(__file__).with_name("hwp_images.py").read_bytes()
            ).hexdigest(),
        }

        def profile(config):
            return AdapterDescriptor.from_config(
                adapter_id="document-files.native.office-image-recognition",
                adapter_version="1",
                config=config,
                capabilities=self.descriptor.capabilities,
            ).config_hash

        self.recognition_profile = profile(recognition_config)

    def _image_text(self, image_path, seconds, *, crop=None):
        started = time.monotonic()
        config = {
            "max_edge_pixels": self.config["max_edge_pixels"],
            "recognition_languages": self.config["recognition_languages"],
        }
        if crop is not None:
            config["source_crop_bbox"] = list(crop)
        descriptor = AdapterDescriptor.from_config(
            adapter_id="document-files.native.vision-image",
            adapter_version=self.vision.descriptor.adapter_version,
            config=config,
            capabilities=AdapterCapabilities(
                format_ids=("image",),
                structural_unit_types=("page_region",),
                execution_mode="jsonl_subprocess",
                preserves_reading_order=False,
                supports_ocr=True,
                supports_geometry=True,
                supports_confidence=True,
            ),
        )
        executable = self.vision._build(timeout_seconds=max(0.01, seconds))
        seconds -= time.monotonic() - started
        if seconds <= 0:
            raise BudgetExceededError("Office OCR exhausted its runtime budget")
        adapter = ExternalJSONLAdapter(
            descriptor,
            (str(executable),),
            AdapterBudgets(
                timeout_seconds=max(0.01, seconds),
                max_input_bytes=self.config["max_image_bytes"],
                max_units=20_000,
                max_unit_content_chars=MAX_UNIT_CHARS,
                max_total_content_chars=2_000_000,
            ),
            config=config,
        )
        recovered = single_bitmap_emf(image_path.read_bytes())
        if recovered is None:
            return adapter.extract(image_path, format_id="image")
        bitmap, origin = recovered
        bitmap_path = image_path.with_suffix(".bitmap.bmp")
        try:
            bitmap_path.write_bytes(bitmap)
            result = adapter.extract(bitmap_path, format_id="image")
        finally:
            bitmap_path.unlink(missing_ok=True)
        return ExtractionEnvelope.create(
            descriptor=result.descriptor,
            completeness=result.completeness,
            coverage=result.coverage,
            units=tuple(
                replace(
                    u,
                    structure_path={
                        **u.to_dict()["structure_path"],
                        "source_bitmap": origin,
                    },
                )
                for u in result.units
            ),
            issues=result.issues,
        )

    def extract(self, path, *, format_id):
        return self._extract_range(path, format_id=format_id)

    def resume(self, path, *, format_id, previous):
        if previous.descriptor != self.descriptor:
            raise ExtractionError(
                "Office continuation requires the same adapter identity"
            )
        return self._extract_range(path, format_id=format_id, previous=previous)

    def _extract_range(self, path, *, format_id, previous=None):
        native = self.native.extract(path, format_id=format_id)
        with Path(path).open("rb") as source:
            source_digest = hashlib.file_digest(source, "sha256").hexdigest()
        native_characters = Counter()
        for unit in native.units:
            if unit.unit_type not in {"embedded_object", "speaker_notes", "field"}:
                native_characters[unit.structure_path.get("slide")] += sum(
                    c.isalnum() for c in unit.content
                )
        insertions, observations = {}, Counter()
        diagnostic_examples = defaultdict(list)
        failures = {}
        positions = {}
        for position, unit in enumerate(native.units):
            if unit.unit_type == "embedded_object" and unit.structure_path.get(
                "object_type"
            ) in {"image", "pic"}:
                positions.setdefault(
                    _image_position(unit.structure_path),
                    position,
                )
        if not positions:
            if previous is not None:
                raise ExtractionError("Image continuation lost its native positions")
            return ExtractionEnvelope.create(
                descriptor=self.descriptor,
                completeness=native.completeness,
                coverage=native.coverage,
                units=native.units,
                issues=native.issues,
            )
        image_order = sorted(
            positions.values(),
            key=lambda position: (
                native_characters[native.units[position].structure_path.get("slide")]
                >= self.config["native_text_min_alphanumeric_characters"]
            ),
        )
        start = 0
        if previous is not None:
            coverage = [
                i.details
                for i in previous.issues
                if i.code == "office_image_range_observed"
            ]
            pending = [
                i.details
                for i in previous.issues
                if i.code == "office_image_range_pending"
            ]
            if len(coverage) != 1 or len(pending) != 1:
                raise ExtractionError("Office continuation has no unique image range")
            start = pending[0].get("next_image")
            if (
                type(start) is not int
                or not 0 <= start < len(image_order)
                or coverage[0].get("next_image") != start
                or coverage[0].get("image_count") != len(image_order)
                or coverage[0].get("source_sha256") != source_digest
            ):
                raise ExtractionError(
                    "Office continuation source or image range changed"
                )
            for unit in previous.units:
                if not (
                    (unit.unit_type == "image_text" and unit.derivation_method == "ocr")
                    or (
                        unit.unit_type == "image_native_text"
                        and unit.derivation_method == "native_text"
                        and unit.structure_path.get("text_representation")
                        == "emf_stored_string"
                    )
                ):
                    continue
                key = _image_position(unit.structure_path)
                if key not in positions:
                    raise ExtractionError(
                        "Office OCR has no matching native image position"
                    )
                insertions.setdefault(positions[key], []).append(unit)
            for issue in previous.issues:
                if (
                    issue.code in _MESSAGES
                    and issue.code != "office_image_ocr_budget_reached"
                ):
                    observations[issue.code] += issue.details.get("occurrences", 0)
                    diagnostic_examples[issue.code].extend(
                        dict(example)
                        for example in issue.details.get("examples", ())[:16]
                    )

        def observe_image(code, location, part, details):
            observations[code] += 1
            if len(diagnostic_examples[code]) < 16:
                diagnostic_examples[code].append(
                    {
                        **{
                            k: location[k]
                            for k in (
                                "part",
                                "element",
                                "slide",
                                "section_file",
                                "section_stream",
                                "image_record",
                            )
                            if k in location
                        },
                        "image_part": part,
                        **details,
                    }
                )

        added_characters = sum(
            len(u.content) for group in insertions.values() for u in group
        )
        added_count = sum(len(group) for group in insertions.values())
        native_total = sum(len(u.content) for u in native.units)
        by_part, by_digest, source_data, metafile_strings = {}, {}, {}, {}
        bytes_read = 0
        next_image = start
        output_limited = False
        deadline = time.monotonic() + self.config["timeout_seconds"]
        with (
            EmbeddedImageArchive(path) as archive,
            tempfile.TemporaryDirectory(prefix="document-files-image-") as temporary,
        ):
            for image_index in range(start, len(image_order)):
                position = image_order[image_index]
                unit = native.units[position]
                location = unit.to_dict()["structure_path"]
                next_image = image_index + 1
                if position in insertions:
                    continue
                if location.get("display_state") == "not_displayed":
                    # The native reader already recorded this proven observation;
                    # another instance of the same member is still recognized.
                    continue
                parts = location.get("image_parts", [])
                if len(parts) != 1:
                    observe_image(
                        "office_image_placement_unresolved",
                        location,
                        None,
                        {"stage": "source_reference", "retryable": False},
                    )
                    continue
                part = parts[0]
                crop_notes = {}
                try:
                    crop = _source_crop(location, crop_notes)
                except (TypeError, ValueError):
                    observe_image(
                        "office_image_placement_unresolved",
                        location,
                        part,
                        {"stage": "source_crop", "retryable": False},
                    )
                    continue
                image_key = (part, crop)
                if image_key not in by_part:
                    try:
                        size = archive.size(part)
                    except (KeyError, ValueError, OSError):
                        observe_image(
                            "office_image_placement_unresolved",
                            location,
                            part,
                            {"stage": "source_member", "retryable": False},
                        )
                        continue
                    if size > self.config["max_image_bytes"]:
                        observe_image(
                            "office_image_size_limit",
                            location,
                            part,
                            {
                                "stage": "source_member",
                                "budget": "max_image_bytes",
                                "limit": self.config["max_image_bytes"],
                                "unit": "bytes",
                                "observed": size,
                                "retryable": False,
                            },
                        )
                        continue
                    if (
                        len(by_part) >= self.config["max_images"]
                        or bytes_read + (0 if part in source_data else size)
                        > self.config["max_total_image_bytes"]
                        or time.monotonic() >= deadline
                    ):
                        observations["office_image_ocr_budget_reached"] += 1
                        next_image = image_index
                        break
                    if part not in source_data:
                        remaining_bytes = (
                            self.config["max_total_image_bytes"] - bytes_read
                        )
                        read_limit = min(
                            self.config["max_image_bytes"], remaining_bytes
                        )
                        try:
                            source_data[part] = archive.read(part, location, read_limit)
                        except OverflowError:
                            if read_limit < self.config["max_image_bytes"]:
                                observations["office_image_ocr_budget_reached"] += 1
                                next_image = image_index
                                break
                            observe_image(
                                "office_image_size_limit",
                                location,
                                part,
                                {
                                    "stage": "source_decompression",
                                    "budget": "max_image_bytes",
                                    "limit": read_limit,
                                    "unit": "bytes",
                                    "observed_lower_bound": read_limit + 1,
                                    "stored_member_bytes": size,
                                    "retryable": False,
                                },
                            )
                            continue
                        except (OSError, ValueError, zipfile.BadZipFile):
                            observe_image(
                                "office_image_decode_failed",
                                location,
                                part,
                                {"stage": "source_decompression", "retryable": False},
                            )
                            continue
                        bytes_read += len(source_data[part])
                    raw = source_data[part]
                    digest = hashlib.sha256(raw).hexdigest()
                    recognition_key = (digest, crop)
                    if recognition_key not in by_digest:
                        image_path = Path(temporary) / "image.bin"
                        image_path.write_bytes(raw)
                        try:
                            by_digest[recognition_key] = self._image_text(
                                image_path, deadline - time.monotonic(), crop=crop
                            )
                        except (DocumentExtractionError, OSError) as exc:
                            by_digest[recognition_key] = None
                            failures[recognition_key] = {
                                "stage": "image_subprocess",
                                "error_type": type(exc).__name__,
                                "error_code": getattr(exc, "code", "os_error"),
                                "retryable": isinstance(exc, BudgetExceededError),
                            }
                        finally:
                            image_path.unlink(missing_ok=True)
                    by_part[image_key] = digest, by_digest[recognition_key]
                digest, recognized = by_part[image_key]
                if recognized is None:
                    details = failures[(digest, crop)]
                    code = (
                        "office_image_ocr_budget_reached"
                        if details["error_code"] == "budget_exceeded"
                        else "office_image_ocr_failed"
                    )
                    observe_image(code, location, part, details)
                    if code == "office_image_ocr_budget_reached":
                        next_image = image_index
                        break
                    continue
                codes = {i.code for i in recognized.issues}
                failures_by_code = {
                    "image_format_unsupported": "office_image_format_unsupported",
                    "image_decode_failed": "office_image_decode_failed",
                    "image_ocr_failed": "office_image_ocr_failed",
                    "image_crop_placement_unresolved": "office_image_placement_unresolved",
                }
                failure = next(
                    (i for i in recognized.issues if i.code in failures_by_code), None
                )
                if failure is not None:
                    observe_image(
                        failures_by_code[failure.code],
                        location,
                        part,
                        dict(failure.details),
                    )
                    if failure.code == "image_format_unsupported" and crop is None:
                        # Read only bytes already admitted by this image pass.
                        # Stored strings are not OCR or a visible rendering; a
                        # source crop would require graphics playback to locate.
                        if digest not in metafile_strings:
                            metafile_strings[digest] = stored_emf_strings(
                                source_data[part]
                            )
                        strings = metafile_strings[digest] or ()
                        stored = [
                            ExtractedUnit(
                                unit_type="image_native_text",
                                structure_path={
                                    **location,
                                    "image_part": part,
                                    "image_sha256": digest,
                                    "text_representation": "emf_stored_string",
                                    "source_metafile": origin,
                                },
                                content=normalize_text(text),
                                derivation_method="native_text",
                                quality_flags=(
                                    "metafile_stored_text",
                                    "reading_order_unverified",
                                    "visibility_unverified",
                                ),
                            )
                            for text, origin in strings
                            if normalize_text(text)
                        ]
                        characters = sum(len(u.content) for u in stored)
                        if (
                            len(native.units) + added_count + len(stored) > 200_000
                            or native_total + added_characters + characters > 50_000_000
                        ):
                            observations["office_image_ocr_output_limit"] += 1
                            output_limited = True
                            next_image = image_index
                            break
                        if stored:
                            insertions[position] = stored
                            added_count += len(stored)
                            added_characters += characters
                            observations["office_metafile_text_observed"] += 1
                    continue
                if "image_pixel_budget_exceeded" in codes:
                    pixel_issue = next(
                        i
                        for i in recognized.issues
                        if i.code == "image_pixel_budget_exceeded"
                    )
                    observe_image(
                        "office_image_size_limit",
                        location,
                        part,
                        {
                            "image_bytes": len(source_data[part]),
                            **dict(pixel_issue.details),
                        },
                    )
                    continue
                for issue in recognized.issues:
                    if issue.code == "image_ocr_padding_observed":
                        observe_image(
                            "office_image_ocr_padding_observed",
                            location,
                            part,
                            {
                                "image_bytes": len(source_data[part]),
                                **dict(issue.details),
                            },
                        )
                if any(
                    i.code == "image_text_budget_exceeded" for i in recognized.issues
                ):
                    observations["office_image_ocr_output_limit"] += 1
                    continue
                if not recognized.units:
                    observations["office_image_without_text"] += 1
                    continue
                if (
                    len(native.units) + added_count + len(recognized.units) > 200_000
                    or native_total
                    + added_characters
                    + sum(len(region.content) for region in recognized.units)
                    > 50_000_000
                ):
                    observations["office_image_ocr_output_limit"] += 1
                    output_limited = True
                    next_image = image_index
                    break
                regions = []
                for index, region in enumerate(recognized.units):
                    regions.append(
                        ExtractedUnit(
                            unit_type="image_text",
                            structure_path={
                                **location,
                                "image_part": part,
                                "image_sha256": digest,
                                "image_region": index,
                                "geometry_scope": "oriented_embedded_image_pixels",
                                **crop_notes,
                                "source_image_orientation": region.structure_path.get(
                                    "source_image_orientation", 1
                                ),
                                **(
                                    {
                                        "recognized_source_crop": region.to_dict()[
                                            "structure_path"
                                        ]["source_crop_bbox"]
                                    }
                                    if "source_crop_bbox" in region.structure_path
                                    else {}
                                ),
                                "text_representation": "vision_ocr",
                                **(
                                    {
                                        "recognition_padding": region.to_dict()[
                                            "structure_path"
                                        ]["recognition_padding"]
                                    }
                                    if "recognition_padding" in region.structure_path
                                    else {}
                                ),
                                **(
                                    {
                                        "source_bitmap": region.to_dict()[
                                            "structure_path"
                                        ]["source_bitmap"]
                                    }
                                    if "source_bitmap" in region.structure_path
                                    else {}
                                ),
                            },
                            content=region.content,
                            derivation_method="ocr",
                            geometry=region.geometry,
                            confidence=region.confidence,
                            quality_flags=(*region.quality_flags, "embedded_image_ocr"),
                            issues=region.issues,
                        )
                    )
                    added_characters += len(region.content)
                    added_count += 1
                if regions:
                    insertions[position] = regions
                    observations["office_image_ocr_observed"] += 1
        issues = []
        for issue in native.issues:
            if added_count and issue.code == "no_extractable_text":
                continue
            if (
                issue.code == "office_image_content_unread"
                and observations["office_image_ocr_observed"]
            ):
                remaining = max(
                    0,
                    issue.details.get("occurrences", 0)
                    - observations["office_image_ocr_observed"],
                )
                if remaining:
                    issues.append(
                        replace(
                            issue, details={**issue.details, "occurrences": remaining}
                        )
                    )
            else:
                issues.append(issue)
        if observations["office_image_ocr_observed"]:
            observations["office_image_visual_content_partial"] = observations[
                "office_image_ocr_observed"
            ]
        for code, count in sorted(observations.items()):
            if count:
                issues.append(
                    ExtractionIssue(
                        code=code,
                        severity="info"
                        if code
                        in {
                            "office_image_ocr_observed",
                            "office_image_ocr_padding_observed",
                            "office_image_without_text",
                            "office_metafile_text_observed",
                        }
                        else "warning",
                        message=_MESSAGES[code],
                        details={
                            "occurrences": count,
                            "scope": self.config["scope"],
                            **(
                                {
                                    "examples": diagnostic_examples[code],
                                    "examples_truncated": count
                                    > len(diagnostic_examples[code]),
                                }
                                if diagnostic_examples[code]
                                else {}
                            ),
                        },
                    )
                )
        if image_order:
            issues.append(
                ExtractionIssue(
                    "office_image_range_observed",
                    "The current result retains the processed native image order.",
                    "info",
                    {
                        "next_image": next_image,
                        "image_count": len(image_order),
                        "source_sha256": source_digest,
                        "recognition_profile": self.recognition_profile,
                    },
                )
            )
            if next_image < len(image_order) and not output_limited:
                issues.append(
                    ExtractionIssue(
                        "office_image_range_pending",
                        "Further images remain for a bounded continuation pass.",
                        "warning",
                        {"next_image": next_image, "image_count": len(image_order)},
                    )
                )
        units = tuple(
            value
            for position, unit in enumerate(native.units)
            for value in (unit, *insertions.get(position, ()))
        )
        return ExtractionEnvelope.create(
            descriptor=self.descriptor,
            completeness="partial"
            if (not units or any(i.severity in {"warning", "error"} for i in issues))
            else "complete",
            units=units,
            issues=issues,
            coverage=native.coverage,
        )
