"""Synthetic audited-stage contracts; not real recognition, upstream or target evidence."""

import hashlib
import importlib.util
import json
import struct
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "recognition_builder", ROOT / "scripts/prepare_recognition_pack.py"
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value))
    return {"path": path.name, "sha256": sha(path.read_bytes())}


def native(target):
    data = bytearray(128)
    if target == "linux-x86_64":
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 18, 62)
    elif target == "windows-x86_64":
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:70] = b"PE\0\0\x64\x86"
    else:
        data[:4] = b"\xcf\xfa\xed\xfe"
        struct.pack_into("<I", data, 4, 0x0100000C)
    return bytes(data)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    tool = module(monkeypatch)

    def create(target="linux-x86_64"):
        root = tmp_path / target
        root.mkdir()
        stage = root / "stage"
        stage.mkdir()
        sources, source_rows, files, provenance = {}, [], [], []
        rows = {}

        def source(name, data, role):
            path = root / name
            path.write_bytes(data)
            digest = sha(data)
            sources[digest] = path
            source_rows.append({"sha256": digest, "role": role, "licenseIds": ["test-license"]})
            provenance.append({"uri": "https://example.org/synthetic/" + name, "sha256": digest})
            return digest

        def add(name, data, origin):
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            item = {
                "path": name,
                "size": len(data),
                "sha256": sha(data),
                "sourceSha256": origin,
                "license": "test-license",
            }
            files.append(item)
            rows[name] = item

        runtime = source(
            "python-runtime.tar.gz", b"Synthetic runtime source only", "python-runtime"
        )
        tess = source("native-source.tar.gz", b"Synthetic native source only", "native")
        add("python/bin/python", native(target), runtime)
        add("native/tesseract", native(target), tess)
        add("licenses/NOTICE", b"Synthetic test fixture license, no real upstream claim", runtime)
        locked = []
        for package, version in (("torch", "2.14.0+cpu"), ("docling", "2.126.0")):
            wheel = root / f"{package}-{version}-py3-none-any.whl"
            metadata = f"Name: {package}\nVersion: {version}\n"
            with zipfile.ZipFile(wheel, "w") as z:
                z.writestr(f"{package}-{version}.dist-info/METADATA", metadata)
            digest = sha(wheel.read_bytes())
            sources[digest] = wheel
            source_rows.append({"sha256": digest, "role": "wheel", "licenseIds": ["test-license"]})
            provenance.append({"uri": "https://example.org/" + wheel.name, "sha256": digest})
            add(
                f"python/lib/site-packages/{package}-{version}.dist-info/METADATA",
                metadata.encode(),
                digest,
            )
            locked.append(f"{package}=={version} --hash=sha256:{digest}\n")
            if package == "torch":
                add(
                    "python/lib/site-packages/torch/version.py",
                    b"__version__ = '2.14.0+cpu'\ncuda = None\nhip = None\n",
                    digest,
                )
        resources = [
            (f"models/docling/{tool.HERON}/{name}", "layout-model")
            for name in ("config.json", "preprocessor_config.json", "model.safetensors")
        ]
        resources += [
            (f"models/docling/{tool.TABLE}/{name}", "table-model")
            for name in ("tm_config.json", "tableformer_accurate.safetensors")
        ]
        resources += [
            (f"models/tessdata/{name}", "ocr-data")
            for name in ("kor.traineddata", "eng.traineddata", "osd.traineddata", "configs/tsv")
        ]
        for index, (name, role) in enumerate(resources):
            data = ("Synthetic source " + name).encode()
            origin = source(f"resource-{index}", data, role)
            add(name, data, origin)
        lock_path = root / "requirements.lock"
        lock_path.write_text("".join(locked))
        lock_ref = {"path": lock_path.name, "sha256": sha(lock_path.read_bytes())}
        evidence = root / "native-tool-output.txt"
        evidence.write_text("Synthetic native-linkage contract only; no target command executed")
        raw_ref = {"path": evidence.name, "sha256": sha(evidence.read_bytes())}
        system = {
            "linux-x86_64": "libc.so.6",
            "windows-x86_64": "KERNEL32.dll",
            "macos-aarch64": "/usr/lib/libSystem.B.dylib",
        }[target]
        linkage = {
            "schemaVersion": "document-files.recognition-native-linkage.v1",
            "platform": target,
            "binaries": [
                {
                    "path": name,
                    "sha256": rows[name]["sha256"],
                    "tool": "synthetic-test-only",
                    "rawEvidence": raw_ref,
                    "dependencies": [{"name": system, "origin": "system"}],
                }
                for name in ("python/bin/python", "native/tesseract")
            ],
        }
        linkage_path = root / "linkage.json"
        linkage_ref = dump(linkage_path, linkage)
        declaration = {
            "schemaVersion": "document-files.pack.v1",
            "id": "test-recognition",
            "version": "1",
            "kind": "recognition",
            "platform": target,
            "minimumOS": {"name": target.split("-")[0], "version": "1.0"},
            "minimumGlibc": "2.28",
            "compatibleRuntimes": [],
            "defaultLicense": "test-license",
            "licenses": [{"id": "test-license", "spdx": "MIT", "path": "licenses/NOTICE"}],
            "executables": ["python/bin/python", "native/tesseract"],
            "provenance": {"sources": provenance},
            "recognition": {
                "backend": "docling",
                "python": "python/bin/python",
                "tesseract": "native/tesseract",
                "artifacts": "models/docling",
                "tessdata": "models/tessdata",
                "languages": ["kor", "eng"],
                "layout": "heron",
                "tableMode": "accurate",
                "offline": True,
                "device": "cpu",
            },
        }
        declaration_path = root / "declaration.json"
        declaration_ref = dump(declaration_path, declaration)
        audit = {
            "schemaVersion": "document-files.recognition-stage-audit.v1",
            "platform": target,
            "declarationSha256": declaration_ref["sha256"],
            "files": files,
            "sources": source_rows,
            "wheelLock": lock_ref,
            "nativeLinkage": linkage_ref,
        }
        audit_path = root / "audit.json"

        def verify():
            audit["declarationSha256"] = dump(declaration_path, declaration)["sha256"]
            audit["nativeLinkage"] = dump(linkage_path, linkage)
            audit_ref = dump(audit_path, audit)
            return tool.verify_stage(
                stage, declaration_path, audit_path, audit_ref["sha256"], sources
            )

        def update_file(name, value):
            (stage / name).write_bytes(value)
            rows[name].update(size=len(value), sha256=sha(value))

        return locals()

    return tool, create


@pytest.mark.parametrize("target", ["linux-x86_64", "windows-x86_64", "macos-aarch64"])
def test_audited_platform_bytes_verify_without_execution(fixture, target):
    _, create = fixture
    f = create(target)
    _, report = f["verify"]()
    assert report["platform"] == target
    assert report["executionVerified"] is False
    assert report["modelQuality"] == "not-assessed"
    assert report["filesVerified"] == len(f["files"])
    assert not list(f["root"].glob("*.pack.zip"))


@pytest.mark.parametrize("fault", ["hash", "missing", "license", "role"])
def test_upstream_sources_must_exist_match_hash_and_license(fixture, fault):
    tool, create = fixture
    f = create()
    digest = f["runtime"]
    if fault == "hash":
        f["sources"][digest].write_text("Other archive")
    elif fault == "missing":
        f["sources"].pop(digest)
    elif fault == "license":
        f["source_rows"][0]["licenseIds"] = []
    else:
        f["source_rows"][0]["role"] = "unreviewed"
    with pytest.raises(tool.PackError):
        f["verify"]()


@pytest.mark.parametrize("fault", ["extra", "changed", "missing", "symlink"])
def test_stage_inventory_is_exact(fixture, fault):
    tool, create = fixture
    f = create()
    path = f["stage"] / "licenses/NOTICE"
    if fault == "extra":
        (f["stage"] / "extra.txt").write_text("extra")
    elif fault == "changed":
        path.write_text("mutated")
    elif fault == "missing":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to(f["root"] / "native-tool-output.txt")
    with pytest.raises(tool.PackError):
        f["verify"]()


@pytest.mark.parametrize("target", ["macos-x86_64", "any", "linux-aarch64"])
def test_unsupported_native_target_rejected(fixture, target):
    tool, create = fixture
    f = create()
    f["declaration"]["platform"] = target
    f["audit"]["platform"] = target
    with pytest.raises(tool.PackError):
        f["verify"]()


def test_foreign_entrypoint_rejected(fixture):
    tool, create = fixture
    f = create()
    f["update_file"]("python/bin/python", native("windows-x86_64"))
    with pytest.raises(tool.PackError, match="non_native_entrypoint"):
        f["verify"]()


@pytest.mark.parametrize(
    "settings",
    [
        b"__version__='2.14.0+cpu'\ncuda='12.4'\nhip=None\n",
        b"__version__='2.14.0+cpu'\ncuda=None\nhip='6'\n",
        b"__version__='2.14.0'\ncuda=None\nhip=None\n",
    ],
)
def test_gpu_or_unpinned_linux_torch_rejected(fixture, settings):
    tool, create = fixture
    f = create()
    f["update_file"]("python/lib/site-packages/torch/version.py", settings)
    with pytest.raises(tool.PackError):
        f["verify"]()


def test_license_notice_must_not_be_empty(fixture):
    tool, create = fixture
    f = create()
    f["update_file"]("licenses/NOTICE", b"  ")
    with pytest.raises(tool.PackError, match="empty_license"):
        f["verify"]()


def test_ocr_resources_cannot_be_omitted_from_audit(fixture):
    tool, create = fixture
    f = create()
    name = "models/tessdata/osd.traineddata"
    (f["stage"] / name).unlink()
    f["files"].remove(f["rows"][name])
    with pytest.raises(tool.PackError, match="missing_model_resource"):
        f["verify"]()


def test_model_files_must_match_original_downloads(fixture):
    tool, create = fixture
    f = create()
    f["update_file"]("models/tessdata/kor.traineddata", b"unapproved model substitution")
    with pytest.raises(tool.PackError, match="model_not_original"):
        f["verify"]()


@pytest.mark.parametrize("fault", ["missing", "hash", "external", "unbundled", "raw"])
def test_native_dependency_evidence_must_cover_exact_binaries(fixture, fault):
    tool, create = fixture
    f = create()
    binary = f["linkage"]["binaries"][0]
    if fault == "missing":
        f["linkage"]["binaries"].pop()
    elif fault == "hash":
        binary["sha256"] = "0" * 64
    elif fault == "external":
        binary["dependencies"] = [{"name": "/opt/homebrew/lib/libtesseract.so", "origin": "system"}]
    elif fault == "unbundled":
        binary["dependencies"] = [
            {"name": "libtesseract.so", "origin": "pack", "path": "lib/missing.so"}
        ]
    else:
        binary["rawEvidence"]["sha256"] = "0" * 64
    with pytest.raises(tool.PackError):
        f["verify"]()


def test_wheel_hash_from_other_package_stanza_does_not_qualify(fixture):
    tool, create = fixture
    f = create()
    lines = f["lock_path"].read_text().splitlines()
    left, right = [line.split(" --hash=") for line in lines]
    f["lock_path"].write_text(
        left[0] + " --hash=" + right[1] + "\n" + right[0] + " --hash=" + left[1] + "\n"
    )
    f["audit"]["wheelLock"]["sha256"] = sha(f["lock_path"].read_bytes())
    with pytest.raises(tool.PackError, match="unpinned_wheel"):
        f["verify"]()


def test_audit_requires_independently_supplied_digest(fixture):
    tool, create = fixture
    f = create()
    f["verify"]()
    with pytest.raises(tool.PackError, match="untrusted_audit"):
        tool.verify_stage(
            f["stage"], f["declaration_path"], f["audit_path"], "0" * 64, f["sources"]
        )


def test_verified_builder_preserves_existing_outputs_and_seals_exact_stage(fixture):
    tool, create = fixture
    f = create()
    f["verify"]()
    output = f["root"] / "recognition.pack.zip"
    result = tool.build_verified(
        f["stage"],
        f["declaration_path"],
        f["audit_path"],
        sha(f["audit_path"].read_bytes()),
        f["sources"],
        output,
    )
    assert result["executionVerified"] is False
    assert result["pack"]["sha256"] == sha(output.read_bytes())
    before = output.read_bytes()
    with pytest.raises(tool.PackError, match="output_exists"):
        tool.build_verified(
            f["stage"],
            f["declaration_path"],
            f["audit_path"],
            sha(f["audit_path"].read_bytes()),
            f["sources"],
            output,
        )
    assert output.read_bytes() == before


def test_audit_to_build_race_is_rejected_and_only_new_outputs_removed(fixture, monkeypatch):
    tool, create = fixture
    f = create()
    f["verify"]()
    output = f["root"] / "recognition.pack.zip"
    original = tool.build_pack

    def changed(stage, declaration, path, sources):
        (stage / "licenses/NOTICE").write_text("Changed after audit verification")
        return original(stage, declaration, path, sources)

    monkeypatch.setattr(tool, "build_pack", changed)
    with pytest.raises(tool.PackError, match="changed_during_build"):
        tool.build_verified(
            f["stage"],
            f["declaration_path"],
            f["audit_path"],
            sha(f["audit_path"].read_bytes()),
            f["sources"],
            output,
        )
    assert not list(f["root"].glob("recognition.pack.zip*"))
    assert f["audit_path"].exists()
