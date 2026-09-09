"""Notice/build contracts and bounded Windows-only process checks; no rights approval."""

import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import windows_build_evidence as evidence


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows Job Object execution required")
def test_actual_windows_job_completes_with_no_remaining_processes(tmp_path):
    import uuid

    state = evidence.supervise(
        [sys.executable, "-c", "import os; print(os.environ['MSBUILDDISABLENODEREUSE'])"],
        tmp_path,
        str(uuid.uuid4()),
        10,
    )
    assert state["status"] == "completed", state
    assert state["cleanupVerified"] and state["finalJobCounts"]["active"] == 0
    assert (tmp_path / "build-console.log").read_text().strip() == "1"


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows Job Object execution required")
def test_actual_windows_timeout_terminates_builder_and_inherited_child(tmp_path):
    import uuid

    code = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "time.sleep(30)"
    )
    state = evidence.supervise([sys.executable, "-c", code], tmp_path, str(uuid.uuid4()), 5)
    assert state["status"] == "failed" and state["timedOut"], state
    assert state["finalJobCounts"]["total"] >= 2, state
    assert state["terminationRequested"] and state["cleanupVerified"], state
    assert state["finalJobCounts"]["active"] == 0 and not state["errors"], state


@pytest.fixture
def host():
    return {
        "productId": "Microsoft.VisualStudio.Product.Enterprise",
        "installationVersion": "17.14.123.4",
        "installationPath": "C:/VS2022",
        "isPrerelease": False,
        "isComplete": True,
        "isLaunchable": True,
        "unneededPrivateMetadata": "must not survive",
    }


def docx(text="MICROSOFT VISUAL STUDIO ENTERPRISE 2022. Distributable code."):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>" + text + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return target.getvalue()


def prepared(tmp_path, host, raw=None, **changes):
    raw = docx() if raw is None else raw
    source = tmp_path / "input.docx"
    source.write_bytes(raw)
    args = dict(source=source, retrieved_at="2026-01-01T00:00:00+00:00") | changes
    return evidence.prepare(
        host, tmp_path / "output", evidence.TERMS_URL, evidence.digest(raw), **args
    )


def test_original_derivative_and_actual_edition_are_bound_without_approval(tmp_path, host):
    receipt = prepared(tmp_path, host)
    assert receipt["installedProduct"]["edition"] == "Enterprise"
    assert "unneededPrivateMetadata" not in receipt["installedProduct"]
    for key in ("source", "textDerivative"):
        artifact = tmp_path / "output" / receipt[key]["path"]
        assert evidence.digest(artifact.read_bytes()) == receipt[key]["sha256"]
    assert receipt["independentRedistributionReview"] == "pending"
    assert receipt["installedLicenseEntitlement"] == "not-assessed"
    assert receipt["staticCrtAndSdkReview"] == "not-assessed"
    assert receipt["releaseQualification"] is False
    assert json.loads((tmp_path / "output/terms-receipt.json").read_text()) == receipt


@pytest.mark.parametrize(
    "key,value",
    [
        ("productId", "Microsoft.VisualStudio.Product.BuildTools"),
        ("productId", "Microsoft.VisualStudio.Product.Professional"),
        ("installationVersion", "18.0.123.4"),
        ("isPrerelease", True),
        ("isComplete", False),
        ("isLaunchable", None),
    ],
)
def test_wrong_or_incomplete_installed_product_fails_before_download(tmp_path, host, key, value):
    host[key] = value
    with pytest.raises(ValueError, match="identity_required"):
        prepared(tmp_path, host)
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "text",
    [
        "MICROSOFT VISUAL STUDIO ENTERPRISE 2015 PRE-RELEASE. Distributable code.",
        "MICROSOFT VISUAL C++ 2015-2022 RUNTIME. Redistribution terms.",
        "MICROSOFT VISUAL STUDIO ENTERPRISE 2022 PRE-RELEASE SOFTWARE. Distributable code.",
    ],
)
def test_preview_and_runtime_terms_are_not_enterprise_terms(tmp_path, host, text):
    with pytest.raises(ValueError):
        prepared(tmp_path, host, docx(text))
    assert not (tmp_path / "output/terms-receipt.json").exists()


def test_checksum_is_checked_before_document_parsing(tmp_path, host, monkeypatch):
    monkeypatch.setattr(evidence, "fetch", lambda _: b"not the approved document")
    with pytest.raises(ValueError, match="checksum"):
        evidence.prepare(host, tmp_path / "output", evidence.TERMS_URL, "0" * 64)


def test_other_url_rejected_without_network(tmp_path, host):
    with pytest.raises(ValueError, match="explicit_official"):
        evidence.prepare(host, tmp_path / "output", "https://example.com/license", "0" * 64)


@pytest.mark.parametrize("timestamp", [None, "2026-01-01", "2999-01-01T00:00:00+00:00"])
def test_local_original_requires_explicit_valid_retrieval_time(tmp_path, host, timestamp):
    with pytest.raises(ValueError):
        prepared(tmp_path, host, retrieved_at=timestamp)


def test_does_not_overwrite_evidence(tmp_path, host):
    prepared(tmp_path, host)
    with pytest.raises(FileExistsError):
        prepared(tmp_path, host)


def test_powershell_lifecycle_preserves_early_failure_and_explicit_scope():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/prepare_windows_build.ps1").read_text()
    assert script.index("Save-Receipt\ntry") < script.index("$vswhere =")
    assert "finally {" in script and script.count("Save-Receipt") >= 5
    assert "-products Microsoft.VisualStudio.Product.Enterprise -version '[17.0,18.0)'" in script
    assert "/Bv /MT /c" in script
    assert "Get-ChildItem" not in script and "License.rtf" not in script
    assert "installedLicenseEntitlement='not-assessed'" in script
    for name, directory in [
        ("cpu-runtime", "windows-cpu-evidence"),
        ("recognition-native", "windows-native-evidence"),
    ]:
        workflow = (root / f".github/workflows/{name}.yml").read_text()
        assert evidence.TERMS_URL in workflow
        assert "prepare_windows_build.ps1" in workflow
        assert f"${{{{ runner.temp }}}}/{directory}/" in workflow
        assert "if: always()" in workflow


def test_redirects_are_not_followed_to_substitute_terms():
    with pytest.raises(ValueError, match="redirect"):
        evidence.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")


def test_large_original_is_rejected(tmp_path, host):
    with pytest.raises(ValueError, match="too_large"):
        prepared(tmp_path, host, b"x" * (evidence.MAX_BYTES + 1))


def test_xml_entities_are_rejected():
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>')
    with pytest.raises(ValueError, match="unsafe_xml"):
        evidence.document_text(target.getvalue())


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeJob:
    pid = 12345

    def __init__(self, code=0, active=0, interrupt=False, stuck=False):
        self.code, self.active = code, active
        self.interrupt, self.stuck = interrupt, stuck
        self.terminated = self.closed = False

    def poll(self):
        if self.interrupt:
            raise KeyboardInterrupt
        return self.code

    def counts(self):
        return {"active": self.active, "total": 2, "terminated": int(self.terminated)}

    def terminate(self):
        self.terminated = True
        if not self.stuck:
            self.active = 0

    def close(self):
        self.closed = True


@pytest.mark.parametrize("mode", ["ok", "crash", "timeout", "interrupt", "orphan", "stuck"])
def test_supervision_requires_exit_zero_and_verified_empty_job(tmp_path, mode):
    clock = FakeClock()
    job = FakeJob(
        code=None if mode in {"timeout", "stuck"} else 2 if mode == "crash" else 0,
        active=int(mode in {"timeout", "stuck", "interrupt", "orphan"}),
        interrupt=mode == "interrupt",
        stuck=mode == "stuck",
    )
    state = evidence.supervise(
        ["fake-builder"],
        tmp_path,
        "synthetic-run",
        1,
        launch=lambda *args: job,
        clock=clock,
        sleep=clock.sleep,
        cleanup_seconds=1,
    )
    assert json.loads((tmp_path / "build-execution.json").read_text()) == state
    assert state["status"] == ("completed" if mode == "ok" else "failed")
    assert state["timedOut"] == (mode in {"timeout", "stuck"})
    assert state["interrupted"] == (mode == "interrupt")
    assert state["cleanupVerified"] == (mode != "stuck")
    assert job.closed
    if mode in {"timeout", "stuck", "interrupt", "orphan"}:
        assert job.terminated
    if mode == "orphan":
        assert "builder_left_running_descendants" in state["errors"]


def test_supervision_launch_and_cleanup_exceptions_keep_failure_receipt(tmp_path):
    def fail(*args):
        raise OSError("synthetic")

    state = evidence.supervise(["fake"], tmp_path, "synthetic-run", 1, launch=fail)
    assert state["status"] == "failed" and not state["cleanupVerified"]
    assert (tmp_path / "build-execution.json").is_file()
    second = tmp_path / "second"
    second.mkdir()
    job = FakeJob()
    job.counts = fail
    state = evidence.supervise(["fake"], second, "synthetic-run", 1, launch=lambda *args: job)
    assert state["status"] == "failed" and "build_cleanup_unverified" in state["errors"]
    assert job.closed and not state["cleanupVerified"]


def test_windows_job_source_assigns_before_resume_and_never_uses_global_kill():
    import inspect

    source = inspect.getsource(evidence.WindowsBuildJob)
    assert source.index("if not k.AssignProcessToJobObject") < source.index("if k.ResumeThread")
    assert "0x2000" in source and "0x604" in source
    assert "QueryInformationJobObject" in source and "TerminateJobObject(self.job" in source
    assert "taskkill" not in source and "Get-Process" not in source
    wrapper = (Path(evidence.__file__).parent / "prepare_windows_build.ps1").read_text()
    assert "BuildTimeoutSeconds = 2400" in wrapper
    assert "scripts/build_cpu_runtime.py" not in wrapper
    assert "scripts/build_recognition_native.py" not in wrapper
    assert "$receipt.outputs = $buildResult.outputs" in wrapper


def native_output(work, commit="a" * 40, builder_sha="b" * 64):
    binary = work / "candidate/bin/tesseract.exe"
    notice = work / "candidate/licenses/runtime.txt"
    binary.parent.mkdir(parents=True)
    notice.parent.mkdir()
    binary.write_bytes(b"synthetic-binary-not-Windows-evidence")
    notice.write_bytes(b"synthetic-notice-not-license-approval")
    files = [
        {
            "path": p.relative_to(work).as_posix(),
            "sha256": evidence.sha_file(p),
            "size": p.stat().st_size,
        }
        for p in (binary, notice)
    ]
    receipt = {
        "schemaVersion": "document-files.recognition-native-candidate.v1",
        "target": "windows-x86_64",
        "sourceCommit": commit,
        "dirtySource": False,
        "builderSha256": builder_sha,
        "releaseReady": False,
        "fullRecognitionQualified": False,
        "files": files,
        "binary": {"path": files[0]["path"], "sha256": files[0]["sha256"]},
        "runtimeNotices": {"notices": [{"path": notice.name, "sha256": evidence.sha_file(notice)}]},
    }
    evidence.write_json(work / "native-candidate.json", receipt)
    return receipt, evidence.sha_file(notice)


@pytest.mark.parametrize(
    "mutation", ["valid", "old_commit", "wrong_builder", "changed_binary", "extra", "missing"]
)
def test_native_internal_receipt_and_all_actual_files_must_match(tmp_path, mutation):
    work = tmp_path / "work"
    receipt, license_sha = native_output(work)
    if mutation == "old_commit":
        receipt["sourceCommit"] = "c" * 40
    elif mutation == "wrong_builder":
        receipt["builderSha256"] = "c" * 64
    elif mutation == "changed_binary":
        (work / "candidate/bin/tesseract.exe").write_bytes(b"changed")
    elif mutation == "extra":
        (work / "candidate/old.dll").write_bytes(b"old")
    elif mutation == "missing":
        (work / "candidate/bin/tesseract.exe").unlink()
    evidence.write_json(work / "native-candidate.json", receipt)

    def call():
        return evidence.linked_outputs(
            "recognition", work, tmp_path / "unused", "", "a" * 40, "b" * 64, license_sha
        )

    if mutation != "valid":
        with pytest.raises((ValueError, OSError)):
            call()
    else:
        linked = call()
        assert len(linked["artifacts"]) == 2
        assert linked["internalReceipt"]["sha256"] == evidence.sha_file(
            work / "native-candidate.json"
        )


def cpu_output(work, output):
    stage = work / "stage"
    stage.mkdir(parents=True)
    output.mkdir()
    receipt = {
        "schemaVersion": "document-files.cpu-runtime-build.v1",
        "target": "windows-x86_64",
        "windowsRuntimeLicenseSha256": "d" * 64,
        "executionQualification": False,
    }
    evidence.write_json(stage / "build.json", receipt)
    for name in ("llama-server.exe", "llama-quantize.exe"):
        (stage / name).write_bytes(b"synthetic-not-Windows-evidence")
    files = [
        {"path": p.name, "sha256": evidence.sha_file(p), "size": p.stat().st_size}
        for p in sorted(stage.iterdir())
    ]
    manifest = {
        "id": "llama-cpp-cpu",
        "version": "b10853-cpu.2",
        "platform": "windows-x86_64",
        "kind": "llama-cpp-runtime",
        "files": files,
        "provenance": {"build": {"sourceCommit": "a" * 40, "dirtySource": False}},
    }
    raw = json.dumps(manifest).encode()
    archive = output / "llama-cpp-cpu-b10853-cpu.2-windows-x86_64.pack.zip"
    with zipfile.ZipFile(archive, "x") as zipped:
        zipped.writestr("manifest.json", raw)
        for file in files:
            zipped.write(stage / file["path"], file["path"])
    archive.with_suffix(archive.suffix + ".manifest.json").write_bytes(raw)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{evidence.sha_file(archive)}  {archive.name}\n"
    )
    archive.with_suffix(archive.suffix + ".cdx.json").write_text("{}")
    return archive


@pytest.mark.parametrize(
    "mutation", ["valid", "stage_changed", "manifest_changed", "checksum_changed"]
)
def test_cpu_archive_is_bound_to_fresh_stage_and_exact_sidecars(tmp_path, mutation):
    work, output = tmp_path / "work", tmp_path / "output"
    archive = cpu_output(work, output)
    if mutation == "stage_changed":
        (work / "stage/llama-server.exe").write_bytes(b"old-other-build")
    elif mutation == "manifest_changed":
        archive.with_suffix(archive.suffix + ".manifest.json").write_bytes(b"{}")
    elif mutation == "checksum_changed":
        archive.with_suffix(archive.suffix + ".sha256").write_text("0" * 64)

    def call():
        return evidence.linked_outputs(
            "cpu", work, output, "b10853-cpu.2", "a" * 40, "b" * 64, "d" * 64
        )

    if mutation == "valid":
        assert len(call()["artifacts"]) == 4
    else:
        with pytest.raises(ValueError):
            call()


@pytest.mark.parametrize(
    "mode", ["ok", "old_work", "wrong_run", "missing_output", "timeout", "receipt_mismatch"]
)
def test_candidate_success_requires_fresh_paths_execution_and_linked_output(tmp_path, mode):
    import uuid

    work, output, directory = tmp_path / "work", tmp_path / "output", tmp_path / "evidence"
    directory.mkdir()
    license_path = tmp_path / "terms.txt"
    license_path.write_bytes(b"synthetic-notice-not-license-approval")
    calls = []
    if mode == "old_work":
        work.mkdir()

    def supervisor(command, evidence_dir, run_id, timeout):
        calls.append(command)
        if mode != "missing_output":
            native_output(work, builder_sha=evidence.sha_file(Path(command[1])))
        state = {
            "status": "completed",
            "executionRunId": str(uuid.uuid4()) if mode == "wrong_run" else run_id,
            "exitCode": 0,
            "timedOut": mode == "timeout",
            "cleanupVerified": True,
            "interrupted": False,
            "errors": [],
        }
        evidence.write_json(evidence_dir / "build-execution.json", state)
        return state | ({"exitCode": 1} if mode == "receipt_mismatch" else {})

    result = evidence.run_candidate(
        "recognition",
        Path(sys.executable),
        directory,
        work,
        output,
        "",
        tmp_path / "cache",
        license_path,
        "a" * 40,
        str(uuid.uuid4()),
        supervisor=supervisor,
    )
    assert result["status"] == ("built-unreviewed" if mode == "ok" else "failed")
    assert result["releaseQualification"] is False
    assert result["independentRedistributionReview"] == "pending"
    assert bool(calls) == (mode != "old_work")
    assert json.loads((directory / "build-result.json").read_text()) == result


def test_msbuild_node_reuse_override_is_child_only(monkeypatch):
    monkeypatch.setenv("MSBUILDDISABLENODEREUSE", "0")
    monkeypatch.setenv("DOCUMENT_FILES_TEST_PARENT_VALUE", "preserved")
    import os

    before = dict(os.environ)
    child = evidence.child_environment()
    assert child["MSBUILDDISABLENODEREUSE"] == "1"
    assert child["DOCUMENT_FILES_TEST_PARENT_VALUE"] == "preserved"
    assert dict(os.environ) == before
    assert evidence.child_environment({"MSBuildDisableNodeReuse": "0"}) == {
        "MSBUILDDISABLENODEREUSE": "1"
    }


def test_cpu_review_metadata_are_original_archive_bytes_without_binary_copy(tmp_path):
    work, output, metadata = tmp_path / "work", tmp_path / "output", tmp_path / "evidence"
    metadata.mkdir()
    archive = cpu_output(work, output)
    linked = evidence.linked_outputs(
        "cpu",
        work,
        output,
        "b10853-cpu.2",
        "a" * 40,
        "b" * 64,
        "d" * 64,
        metadata_output=metadata,
    )
    assert {p.name for p in metadata.iterdir()} == {"cpu-build.json", "cpu-manifest.json"}
    with zipfile.ZipFile(archive) as packed:
        for item, member in zip(
            linked["reviewMetadata"], ["build.json", "manifest.json"], strict=True
        ):
            assert Path(item["path"]).read_bytes() == packed.read(member)
            assert item["sha256"] == evidence.digest(packed.read(member))
