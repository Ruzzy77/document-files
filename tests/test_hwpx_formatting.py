"""Source formatting is evidence, never an automatic logical role or render."""

import hashlib
import zipfile
from xml.etree import ElementTree as ET

import pytest

from document_files.engine import extract_structure
from document_files.extraction_errors import ExtractionError
from document_files.hwpx_structure import extract_structured_hwpx

HEADER = """<header><refList>
<paraProperties><paraPr id="1"><align horizontal="CENTER"/>
<heading type="NONE" level="0"/></paraPr>
<paraPr id="2"><align horizontal="JUSTIFY"/></paraPr></paraProperties>
<charProperties><charPr id="7" height="2000" textColor="#000000"><bold/></charPr>
<charPr id="8" height="1100"><italic/></charPr></charProperties>
</refList></header>"""


def package(tmp_path, body, header=HEADER):
    path = tmp_path / "format.hwpx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Contents/header.xml", header)
        archive.writestr("Contents/section0.xml", f"<sec>{body}</sec>")
    return path


def at(root, address):
    node = root
    assert address.split(".")[0] == "0"
    for index in address.split(".")[1:]:
        node = node[int(index)]
    return node


def test_mixed_runs_keep_direct_properties_and_original_xml_addresses(tmp_path):
    body = """<p paraPrIDRef="1" styleIDRef="0">
    <run charPrIDRef="7"><t>  Large </t></run>
    <run charPrIDRef="8"><t>body<tab/>text  </t></run></p>
    <p paraPrIDRef="2"><run charPrIDRef="8"><t>Ordinary prose</t></run></p>"""
    p = package(tmp_path, body)
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    result = extract_structure(p)
    first, second = result["units"]
    assert first["text"] == "Large body\ttext"
    # Centering, boldness and large height do not promote the native paragraph.
    assert first["semanticRole"] == second["semanticRole"] == "paragraph"
    fmt = first["sourceStructure"]["formatting"]
    assert fmt["scope"] == "source_xml_text_elements_before_normalization"
    assert fmt["paragraph"]["definition"]["horizontal_alignment"] == "CENTER"
    assert [x["definition"]["height"] for x in fmt["text_elements"]] == ["2000", "1100"]
    assert [x["definition"]["direct_flags"] for x in fmt["text_elements"]] == [["bold"], ["italic"]]
    with zipfile.ZipFile(p) as archive:
        section = ET.fromstring(archive.read("Contents/section0.xml"))
        for x in fmt["text_elements"]:
            assert at(section, x["run_element"]).get("charPrIDRef") == x["char_pr_ref"]
            assert at(section, x["text_element"]).tag == "t"
            d = x["definition"]
            assert (
                at(ET.fromstring(archive.read(d["part"])), d["element"]).get("height")
                == d["height"]
            )
    assert (
        second["sourceStructure"]["formatting"]["paragraph"]["definition"]["horizontal_alignment"]
        == "JUSTIFY"
    )
    assert len(second["sourceStructure"]["formatting"]["text_elements"]) == 1
    assert hashlib.sha256(p.read_bytes()).hexdigest() == before


def test_text_segments_do_not_inherit_each_others_run_properties(tmp_path):
    p = package(
        tmp_path,
        """<p paraPrIDRef="1">
    <run charPrIDRef="7"><t>Before</t><fieldBegin id="f" type="FORMTEXT"/>
    <t>Stored</t><fieldEnd beginIDRef="f"/></run>
    <run charPrIDRef="8"><t>After</t></run></p>""",
    )
    units = [u for u in extract_structured_hwpx(p).units if u.content]
    assert [u.content for u in units] == ["Before", "Stored", "After"]
    assert [
        [x["char_pr_ref"] for x in u.structure_path["formatting"]["text_elements"]] for u in units
    ] == [["7"], ["7"], ["8"]]
    assert (
        len({u.structure_path["formatting"]["text_elements"][0]["text_element"] for u in units})
        == 3
    )


def test_conditional_formatting_is_not_flattened_into_active_properties(tmp_path):
    header = """<header><paraPr id="1"><switch><case><align horizontal="CENTER"/></case>
    <default><align horizontal="LEFT"/></default></switch></paraPr>
    <charPr id="7" height="1100"><switch><case><bold/></case></switch></charPr>
    <switch><case><charPr id="7" height="5000"/></case></switch></header>"""
    p = package(tmp_path, '<p paraPrIDRef="1"><run charPrIDRef="7"><t>Text</t></run></p>', header)
    fmt = extract_structured_hwpx(p).units[0].structure_path["formatting"]
    assert "horizontal_alignment" not in fmt["paragraph"]["definition"]
    assert fmt["paragraph"]["definition"]["conditional_properties_unresolved"]
    character = fmt["text_elements"][0]["definition"]
    assert character["height"] == "1100"
    assert character["direct_flags"] == []
    assert character["conditional_properties_unresolved"]


def test_unresolved_reference_and_metadata_limit_preserve_every_text_run(tmp_path):
    runs = '<run charPrIDRef="99"><t>X</t></run>' * 257
    p = package(tmp_path, f'<p paraPrIDRef="1">{runs}</p>')
    result = extract_structured_hwpx(p)
    assert result.units[0].content == "X" * 257
    formats = result.units[0].structure_path["formatting"]["text_elements"]
    assert len(formats) == 256 and all("definition" not in f for f in formats)
    assert any(i["code"] == "hwpx_formatting_partial" for i in result.issues)


@pytest.mark.parametrize(
    "header",
    [
        '<header><charPr id="7"/><charPr id="7"/></header>',
        '<header><charPr id="7" height="' + "1" * 257 + '"/></header>',
    ],
)
def test_ambiguous_or_oversized_format_definitions_fail_explicitly(tmp_path, header):
    with pytest.raises(ExtractionError, match="formatting"):
        extract_structured_hwpx(package(tmp_path, "<p><run><t>Text</t></run></p>", header))
