from __future__ import annotations

import struct
from zipfile import ZipFile

import pytest

from document_files import hwp_checkbox_guard as guard


def record(tag, data):
    return struct.pack("<I", tag | len(data) << 20) + data


@pytest.mark.parametrize("length", [24, 25])
def test_native_bullet_character_and_checked_bit(length):
    bullet = bytearray(length)
    struct.pack_into("<I", bullet, 0, 32)
    bullet[12:14] = "☐".encode("utf-16-le")
    bullet[-2:] = "☑".encode("utf-16-le")
    shape = bytearray(58)
    struct.pack_into("<I", shape, 0, 3 << 23)
    struct.pack_into("<H", shape, 30, 1)
    struct.pack_into("<I", shape, 42, 0x88)
    shapes, bullets = guard._native_definitions(record(24, bullet) + record(25, shape))
    assert shapes == [{"bullet": 1, "checked": True, "keepLines": False}]
    assert bullets[1] == {"checkable": True, "char": "☐", "checkedChar": "☑"}


def make_hwpx(path, *, checked="1", char="☑", keep="0", checkable="1"):
    with ZipFile(path, "w") as z:
        z.writestr(
            "Contents/header.xml",
            f'''<head>
          <bullet id="1" char="☐" checkedChar="{char}"><paraHead checkable="{checkable}"/></bullet>
          <paraPr id="0" checked="{checked}"><heading type="BULLET" idRef="1"/>
          <breakSetting keepLines="{keep}"/></paraPr>
          <paraPr id="1" checked="0"><heading type="BULLET" idRef="1"/>
          <breakSetting keepLines="0"/></paraPr></head>''',
        )
        z.writestr("Contents/section0.xml", '<sec><p paraPrIDRef="0"/><p paraPrIDRef="1"/></sec>')


def expected():
    return [
        {
            "section": "Section0",
            "checked": checked,
            "keepLines": False,
            "char": "☐",
            "checkedChar": "☑",
        }
        for checked in [True, False]
    ]


@pytest.mark.parametrize(
    "change", [dict(checked="0"), dict(char="ᄀ"), dict(keep="1"), dict(checkable="0")]
)
def test_loss_detected_independently_of_backend_ir(tmp_path, monkeypatch, change):
    output = tmp_path / "output.hwpx"
    make_hwpx(output, **change)
    monkeypatch.setattr(guard, "native_checkboxes", lambda _: expected())
    assert not guard.compare_checkboxes(tmp_path / "source.hwp", output)["preserved"]


def test_mixed_checkbox_states_preserved(tmp_path, monkeypatch):
    output = tmp_path / "output.hwpx"
    make_hwpx(output)
    monkeypatch.setattr(guard, "native_checkboxes", lambda _: expected())
    result = guard.compare_checkboxes(tmp_path / "source.hwp", output)
    assert result == {
        "preserved": True,
        "sourceCount": 2,
        "outputCount": 2,
        "sourceChecked": 1,
        "outputChecked": 1,
    }


def test_resolver_prefers_downstream_without_overwriting_official(tmp_path, monkeypatch):
    from document_files import rhwp_backend as backend

    official = tmp_path / "rhwp/v0.8.6/macos-aarch64/bin/rhwp"
    patched = tmp_path / f"rhwp/v{backend.PATCHED_RHWP_VERSION}/macos-aarch64/bin/rhwp"
    for path in [official, patched]:
        path.parent.mkdir(parents=True)
        path.write_text("test executable")
        path.chmod(0o755)
    monkeypatch.delenv("DOCUMENT_FILES_RHWP", raising=False)
    monkeypatch.setattr(backend.shutil, "which", lambda _: None)
    monkeypatch.setattr(backend, "_cache_executable", lambda: official)
    assert backend.resolve_rhwp() == patched
    monkeypatch.setenv("DOCUMENT_FILES_RHWP", str(official))
    assert backend.resolve_rhwp() == official


def test_short_ordinary_bullet_does_not_require_checkbox_tail():
    bullet = bytearray(14)
    bullet[12:14] = "•".encode("utf-16-le")
    _, bullets = guard._native_definitions(record(24, bullet))
    assert not bullets[1]["checkable"]
