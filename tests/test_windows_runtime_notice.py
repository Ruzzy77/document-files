"""Notice identity screening, not legal or installed-toolchain qualification."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from windows_runtime_notice import validate_notice


@pytest.mark.parametrize(
    "text",
    [
        "MICROSOFT VISUAL STUDIO 2015 PRODUCT FAMILY PRE-RELEASE SOFTWARE. "
        "Distributable code. EULAID:VS2015_CTP_EVAL_VS_ENU",
        "MICROSOFT VISUAL STUDIO 2022 PRE-RELEASE SOFTWARE. Distributable code.",
        "MICROSOFT VISUAL STUDIO 2026. Distributable code.",
        "unrelated extension notice with redistribution terms",
    ],
)
def test_old_preview_and_unmatched_product_notices_are_not_runtime_evidence(tmp_path, text):
    path = tmp_path / "License.rtf"
    path.write_text(r"{\rtf1 " + text + "}")
    with pytest.raises(ValueError, match="product_mismatch"):
        validate_notice(path)


def test_matching_identity_still_requires_independent_redistribution_review(tmp_path):
    path = tmp_path / "License.rtf"
    path.write_text(
        r"{\rtf1 MICROSOFT VISUAL STUDIO ENTERPRISE 2022. "
        "Distributable code. Other components may be pre-release.}"
    )
    assert validate_notice(path)["independentRedistributionReview"] == "pending"


def test_leptonica_cannot_select_the_network_package_manager_on_windows():
    from build_recognition_native import options

    assert "-DSW_BUILD=OFF" in options("leptonica", Path("prefix"), "windows-x86_64")


def test_native_attributions_include_required_acknowledgments_and_original_notices(tmp_path):
    from build_recognition_native import write_attributions

    write_attributions(
        {
            "sources": [
                {
                    "id": "jpeg",
                    "version": "fixture",
                    "license": "IJG",
                    "notices": [{"path": "README.ijg"}],
                }
            ]
        },
        tmp_path,
    )
    text = (tmp_path / "THIRD_PARTY_NOTICES.txt").read_text()
    assert "based in part on the work of the Independent JPEG Group" in text
    assert "University of California, Berkeley and its contributors" in text
    assert "licenses/jpeg-README.ijg" in text
    assert "not an independent redistribution approval" in text


def test_generic_runtime_terms_cannot_substitute_for_product_terms(tmp_path):
    path = tmp_path / "runtime.txt"
    path.write_text(
        "MICROSOFT VISUAL C++ RUNTIME. For MICROSOFT VISUAL STUDIO 2022. Distributable code.",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="product_mismatch"):
        validate_notice(path)


def test_undecodable_notice_is_not_silently_rewritten(tmp_path):
    path = tmp_path / "notice.txt"
    path.write_bytes(b"MICROSOFT VISUAL STUDIO 2022. Distributable code.\xff")
    with pytest.raises(UnicodeDecodeError):
        validate_notice(path)
