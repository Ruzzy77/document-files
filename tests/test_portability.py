"""Transport and candidate packaging regression tests, not AI qualification."""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from document_files.extraction_errors import BudgetExceededError, ExtractionError
from document_files.extraction_protocol import AdapterBudgets, _bounded_subprocess
from document_files.portability import descriptor_input, subprocess_environment


def test_windows_snapshot_isolated_bounded_and_removed(tmp_path):
    original = tmp_path / "private source.txt"
    original.write_bytes(b"001.2300\r\n")
    with original.open("rb") as stream:
        with descriptor_input(stream.fileno(), max_bytes=100, windows=True) as item:
            snapshot = Path(item["path"])
            assert snapshot != original
            assert item["kind"] == "read_only_snapshot"
            assert item["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
            assert snapshot.read_bytes() == original.read_bytes()
        assert not snapshot.exists()
    assert original.read_bytes() == b"001.2300\r\n"
    with (
        original.open("rb") as stream,
        pytest.raises(BudgetExceededError),
        descriptor_input(stream.fileno(), max_bytes=2, windows=True),
    ):
        pass


def test_snapshot_tamper_is_detected_without_changing_source(tmp_path):
    original = tmp_path / "source"
    original.write_bytes(b"original")
    with (
        original.open("rb") as stream,
        pytest.raises(ExtractionError),
        descriptor_input(stream.fileno(), max_bytes=100, windows=True) as item,
    ):
        snapshot = Path(item["path"])
        snapshot.chmod(0o600)
        snapshot.write_bytes(b"changed")
    assert original.read_bytes() == b"original"
    assert not snapshot.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor compatibility")
def test_posix_transport_unchanged(tmp_path):
    original = tmp_path / "source"
    original.write_bytes(b"original")
    with (
        original.open("rb") as stream,
        descriptor_input(stream.fileno(), max_bytes=100) as item,
    ):
        assert item == {
            "kind": "read_only_file_descriptor",
            "file_descriptor": stream.fileno(),
            "path": f"/dev/fd/{stream.fileno()}",
        }


def test_bounded_subprocess_runtime_and_timeout(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"input")
    with source.open("rb") as stream:
        stdout, _ = _bounded_subprocess(
            command=(sys.executable, "-c", 'print("ok")'),
            request=b"",
            budgets=AdapterBudgets(),
            input_fd=stream.fileno(),
            cwd=tmp_path,
            environment=subprocess_environment(),
        )
        assert stdout.strip() == b"ok"
        with pytest.raises(BudgetExceededError):
            _bounded_subprocess(
                command=(sys.executable, "-c", "import time; time.sleep(5)"),
                request=b"",
                budgets=AdapterBudgets(timeout_seconds=0.1),
                input_fd=stream.fileno(),
                cwd=tmp_path,
                environment=subprocess_environment(),
            )


def test_snapshot_child_verifies_digest(tmp_path):
    from document_files.snapshot_input import verified_snapshot

    source = tmp_path / "source"
    source.write_bytes(b"\x00\x1a\r\n")
    with (
        source.open("rb") as original,
        descriptor_input(original.fileno(), max_bytes=100, windows=True) as item,
    ):
        with verified_snapshot(item, max_bytes=100) as fd:
            assert os.read(fd, 100) == b"\x00\x1a\r\n"
        with (
            pytest.raises(ValueError),
            verified_snapshot({**item, "sha256": "0" * 64}, max_bytes=100),
        ):
            pass


def test_windows_transport_processor_end_to_end(tmp_path, monkeypatch):
    from functools import partial

    from document_files import extraction_protocol
    from document_files.engine import extract_file

    monkeypatch.setattr(
        extraction_protocol, "descriptor_input", partial(descriptor_input, windows=True)
    )
    source = tmp_path / "space 한글.txt"
    source.write_text("Amount: 001.2300\n", encoding="utf-8")
    result = extract_file(str(source))
    assert result["ok"] is True
    assert "001.2300" in str(result)
