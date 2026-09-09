"""Narrow blank proof fixtures; these are not document quality qualifications."""

from copy import deepcopy

import pytest

from document_files.document_model.recognition_coordinates import fingerprint
from document_files.document_model.recognition_native_cells import prove_native_blank


def cell_fixture():
    # The existing exact-pixel fixture uses a 2 x 2 table and full white interiors.
    from test_recognition_cell_observations import collect, fixture

    image, frame, grid, mapping = fixture()
    record = collect(image, frame, grid)
    from document_files.document_model.recognition_cell_observations import (
        validate_cell_observation,
    )

    validation = validate_cell_observation(record, mapping=mapping, source_sha256="a" * 64, page=7)
    objects = []
    for n, (x, y, u, v) in enumerate(
        [
            (1, 1, 59, 1),
            (1, 20, 59, 20),
            (1, 39, 59, 39),
            (1, 1, 1, 39),
            (30, 1, 30, 39),
            (59, 1, 59, 39),
        ]
    ):
        objects.append(
            {
                "id": f"object:{n}",
                "sequence": n,
                "kind": "primitive_line",
                "segments": [[x, y, u, v]],
                "bounds": [min(x, u) - 1, min(y, v) - 1, max(x, u) + 1, max(y, v) + 1],
                "strokeWidth": 2,
                "stroke": True,
                "fill": False,
                "dash": [],
                "strokeColor": [0, 0, 0, 255],
            }
        )
    page = {
        "page": 7,
        "pageSize": [60, 40],
        "rotation": 0,
        "coordinateOrigin": "TOPLEFT",
        "objects": objects,
    }
    return record, validation, page, image, frame, grid, mapping


def prove(record, validation, page, **kw):
    return prove_native_blank(
        record, record["slots"][0], validation, page, comparison_budget=kw.get("budget", 262144)
    )


def test_blank_requires_complete_source_borders_and_entire_white_interior():
    record, validation, page, *_ = cell_fixture()
    before = deepcopy((record, validation, page))
    result = prove(record, validation, page)
    assert result["status"] == "proven_blank"
    assert set(result["borderObjectRefs"]) == {"left", "right", "top", "bottom"}
    assert result["comparisons"] == 6
    assert before == (record, validation, page)


@pytest.mark.parametrize("kind", ["text", "image", "other", "form", "primitive_line"])
@pytest.mark.parametrize("position", ["inside", "boundary", "touching"])
def test_microtext_image_shape_and_extra_line_are_not_blank(kind, position):
    record, validation, page, *_ = cell_fixture()
    bounds = {
        "inside": [10, 10, 10.001, 10.001],
        "boundary": [1, 5, 1.001, 5.001],
        "touching": [31, 5, 31.001, 5.001],
    }[position]
    page["objects"].append({"id": "content", "kind": kind, "bounds": bounds})
    result = prove(record, validation, page)
    assert not result["blankValueProven"]
    assert result["conflictObjectRefs"] == ["content"]


@pytest.mark.parametrize(
    "variant",
    ["duplicate", "missing", "broken", "dash", "filled", "wide", "multi_segment", "invalid_bounds"],
)
def test_nonunique_or_incomplete_native_borders_stay_unresolved(variant):
    record, validation, page, *_ = cell_fixture()
    line = page["objects"][0]
    if variant == "duplicate":
        duplicate = deepcopy(line)
        duplicate["id"] = "duplicate"
        page["objects"].append(duplicate)
    elif variant == "missing":
        page["objects"].pop(0)
    elif variant == "broken":
        line["segments"][0][0] = 2  # Does not meet the left line's source center.
    elif variant == "dash":
        line["dash"] = [1, 2]
    elif variant == "filled":
        line["fill"] = True
    elif variant == "wide":
        line["strokeWidth"] = 3
    elif variant == "multi_segment":
        line["segments"].append([1, 1, 5, 1])
    else:
        line["bounds"] = None
    assert not prove(record, validation, page)["blankValueProven"]


@pytest.mark.parametrize(
    "variant", ["faint", "transparent", "unexamined", "rotation", "source_transform", "budget"]
)
def test_pixel_or_original_page_uncertainty_is_never_a_blank(variant):
    record, validation, page, image, frame, grid, _ = cell_fixture()
    if variant == "faint":
        from test_recognition_cell_observations import collect

        from document_files.document_model.recognition_coordinates import image_identity

        image.putpixel((2, 5), (254, 254, 254))
        frame["canvas"] = image_identity(image)
        record = collect(image, frame, grid)
        assert record["slots"][0]["regions"]["ocrCellWindow"]["nonWhiteRGBPixels"] == 0
    elif variant == "transparent":
        record["slots"][0]["regions"]["boundaryBands"][0]["unknownAlphaPixels"] = 1
    elif variant == "unexamined":
        record["slots"][0]["measurementStatus"] = "unexamined"
    elif variant == "rotation":
        page["rotation"] = 90
    elif variant == "source_transform":
        validation["canvasPixelToOriginalPageAffine"][4] = 1
    result = prove(record, validation, page, budget=5 if variant == "budget" else 262144)
    assert not result["blankValueProven"]
    if variant == "budget":
        assert result["comparisons"] == 0


def import_fixture(monkeypatch):
    import document_files.document_model.pdf_native_objects as native
    from document_files.document_model.model import ObservationDocument

    record, validation, page, _, _, _, mapping = cell_fixture()
    page["completeness"] = {"eligibleForNativeCellReasoning": True}
    inventory = {"pages": [page], "sourceSha256": "a" * 64}
    inventory["fingerprint"] = fingerprint(inventory)
    # This import fixture tests table/state integration; the real parser and its
    # validator have independent actual-source tests in test_pdf_native_objects.
    monkeypatch.setattr(
        native,
        "validate_native_inventory",
        lambda value, **kw: {
            "status": "verified"
            if value is inventory and kw == {"source_sha256": "a" * 64, "page": 7}
            else "unverified",
            "pageInventory": page,
        },
    )
    doc = ObservationDocument()
    doc.provenance["pdfNativeObjects"] = inventory
    doc.provenance["recognitionCoordinateEvidence"] = [
        {
            "status": "verified",
            "sourceSha256": "a" * 64,
            "page": 7,
            "evidence": {"mapping": mapping},
        }
    ]
    table_ref = "docling:page:7:table:0"
    table = {
        "id": table_ref,
        "page": 7,
        "declaredRowCount": 2,
        "declaredColCount": 2,
        "cells": [],
        "locator": {"bbox": {"left": 0, "top": 0, "right": 60, "bottom": 40, "origin": "TOPLEFT"}},
        "unobservedCellCount": 1,
    }
    for slot in record["slots"][1:]:
        x, y, u, v = slot["interiorPixelBox"]
        ref = f"{table_ref}/cell/{slot['row']}:{slot['col']}"
        doc.node(
            ref,
            "known",
            role="table_cell",
            locator={
                "page": 7,
                "bbox": {"left": x, "top": y, "right": u, "bottom": v, "origin": "TOPLEFT"},
            },
        )
        table["cells"].append(
            {"row": slot["row"], "col": slot["col"], "rowSpan": 1, "colSpan": 1, "sourceRef": ref}
        )
    doc.tables[table_ref] = table
    doc.issue(
        "recognition_table_cells_unobserved",
        tableRef=table_ref,
        count=1,
        meaning="not_observed_not_proven_blank",
    )
    doc.issue("recognition_content_completeness_unverified")
    doc.provenance["recognitionCellPixelObservations"] = [
        {
            "batch": "docling:page:7",
            "sourceSha256": "a" * 64,
            "page": 7,
            "observations": [
                {
                    "observation": record,
                    "validation": validation,
                    "observationStatus": "verified",
                    "sourceCoordinateStatus": "verified",
                    "structureAssociation": {
                        "status": "unique_geometry_correspondence",
                        "tableRef": table_ref,
                    },
                }
            ],
        }
    ]
    return doc, table_ref


def test_import_preserves_values_source_pixels_and_original_issue(monkeypatch):
    from document_files.document_model.recognition_native_cells import import_native_blank_cells

    doc, table_ref = import_fixture(monkeypatch)
    original = deepcopy(doc)
    new = import_native_blank_cells(doc, source_hash="a" * 64, page=7, prefix="docling:page:7")
    assert len(new) == 1 and doc.nodes[new[0]]["text"] == ""
    assert doc.nodes[new[0]]["recognizedText"] is False
    assert list(doc.bindings.values())[0]["blank"] is True
    assert doc.tables[table_ref]["unobservedCellCount"] == 0
    assert len(doc.tables[table_ref]["cells"]) == 4
    assert all(doc.nodes[k] == v for k, v in original.nodes.items())
    assert (
        doc.provenance["recognitionCellPixelObservations"]
        == original.provenance["recognitionCellPixelObservations"]
    )
    resolutions = doc.provenance["recognitionNativeCellDecisions"][0]["resolvedIssues"]
    assert resolutions[0]["originalIssue"] == original.issues[0]
    assert doc.issues == [{"code": "recognition_content_completeness_unverified"}]
    assert (
        import_native_blank_cells(doc, source_hash="a" * 64, page=7, prefix="docling:page:7") == []
    )


@pytest.mark.parametrize(
    "variant",
    [
        "other_source",
        "other_page",
        "incomplete",
        "duplicate_batch",
        "duplicate_table",
        "merged",
        "changed_record",
    ],
)
def test_import_does_not_promote_cross_source_ambiguous_or_unverified_cells(monkeypatch, variant):
    from document_files.document_model.recognition_native_cells import import_native_blank_cells

    doc, table_ref = import_fixture(monkeypatch)
    batch = doc.provenance["recognitionCellPixelObservations"][0]
    if variant == "incomplete":
        doc.provenance["pdfNativeObjects"]["pages"][0]["completeness"][
            "eligibleForNativeCellReasoning"
        ] = False
    elif variant == "duplicate_batch":
        doc.provenance["recognitionCellPixelObservations"].append(deepcopy(batch))
    elif variant == "duplicate_table":
        batch["observations"].append(deepcopy(batch["observations"][0]))
    elif variant == "merged":
        doc.tables[table_ref]["cells"][0]["colSpan"] = 2
    elif variant == "changed_record":
        batch["observations"][0]["observation"]["canvas"]["size"][0] = 10
    before = deepcopy(doc)
    assert (
        import_native_blank_cells(
            doc,
            source_hash="b" * 64 if variant == "other_source" else "a" * 64,
            page=8 if variant == "other_page" else 7,
            prefix="docling:page:7",
        )
        == []
    )
    assert doc == before


def test_actual_native_source_objects_join_exact_synthetic_blank_pixels():
    import hashlib
    import io

    from reportlab.pdfgen import canvas

    from document_files.document_model.pdf_native_objects import (
        inventory_pdf_native_objects,
        validate_native_inventory,
    )

    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=(60, 40))
    pdf.setLineWidth(2)
    for x in (1, 30, 59):
        pdf.line(x, 1, x, 39)
    for y in (1, 20, 39):
        pdf.line(1, y, 59, y)
    pdf.save()
    raw = output.getvalue()
    inventory = inventory_pdf_native_objects(raw)
    parsed = validate_native_inventory(
        inventory, source_sha256=hashlib.sha256(raw).hexdigest(), page=1
    )
    assert parsed["status"] == "verified"
    native_page = parsed["pageInventory"]
    assert native_page["completeness"]["eligibleForNativeCellReasoning"]
    record, validation, _, *_ = cell_fixture()
    # Pixel fixtures are supplied independently, not falsely labelled as a render
    # of this PDF. This tests the source-object/geometry consumer contract only.
    assert prove(record, validation, native_page)["blankValueProven"]
