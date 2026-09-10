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
    if target in {"linux-x86_64", "linux-aarch64"}:
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<HHI", data, 16, 3, 183 if target == "linux-aarch64" else 62, 1)
        struct.pack_into("<H", data, 52, 64)
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
            if package == "torch" and target == "linux-aarch64":
                wheel = root / f"{package}-{version}-cp312-cp312-manylinux_2_28_aarch64.whl"
            metadata = f"Name: {package}\nVersion: {version}\n"
            with zipfile.ZipFile(wheel, "w") as z:
                z.writestr(f"{package}-{version}.dist-info/METADATA", metadata)
                if package == "torch":
                    z.writestr(
                        "torch/version.py", b"__version__ = '2.14.0+cpu'\ncuda = None\nhip = None\n"
                    )
            digest = sha(wheel.read_bytes())
            sources[digest] = wheel
            source_rows.append({"sha256": digest, "role": "wheel", "licenseIds": ["test-license"]})
            provenance.append(
                {
                    "uri": (
                        "https://download.pytorch.org/whl/cpu/"
                        if target == "linux-aarch64"
                        else "https://example.org/"
                    )
                    + wheel.name,
                    "sha256": digest,
                }
            )
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
            "linux-aarch64": "libc.so.6",
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


@pytest.mark.parametrize(
    "target", ["linux-x86_64", "linux-aarch64", "windows-x86_64", "macos-aarch64"]
)
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


def test_arm_cpu_without_suffix_needs_original_official_wheel(tmp_path, monkeypatch):
    tool = module(monkeypatch)
    stage = tmp_path / "stage"
    version = stage / "python/lib/site-packages/torch/version.py"
    version.parent.mkdir(parents=True)
    content = b"__version__='2.8.0'\ncuda=None\nhip=None\n"
    version.write_bytes(content)
    wheel = tmp_path / "torch-2.8.0-cp312-cp312-manylinux_2_28_aarch64.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("torch/version.py", content)
        z.writestr(
            "torch-2.8.0.dist-info/METADATA",
            "Name: torch\nVersion: 2.8.0\nRequires-Dist: filelock\n",
        )
    digest = sha(wheel.read_bytes())
    name = version.relative_to(stage).as_posix()
    files = {name: {"sha256": sha(content), "sourceSha256": digest}}
    provenance = [{"sha256": digest, "uri": "https://download.pytorch.org/whl/cpu/" + wheel.name}]
    tool.cpu_torch(stage, files, "linux-aarch64")
    tool.verify_arm_cpu_wheel(
        stage, files, {digest: wheel}, {digest: ("torch", "2.8.0")}, provenance
    )
    with pytest.raises(tool.PackError, match="cpu_build_required"):
        tool.cpu_torch(stage, files, "linux-x86_64")
    provenance[0]["uri"] = "https://example.org/" + wheel.name
    with pytest.raises(tool.PackError, match="official_cpu_source"):
        tool.verify_arm_cpu_wheel(
            stage, files, {digest: wheel}, {digest: ("torch", "2.8.0")}, provenance
        )
    provenance[0]["uri"] = "https://download.pytorch.org/whl/cpu/" + wheel.name
    files[name]["sha256"] = "0" * 64
    with pytest.raises(tool.PackError, match="file_origin_mismatch"):
        tool.verify_arm_cpu_wheel(
            stage, files, {digest: wheel}, {digest: ("torch", "2.8.0")}, provenance
        )


def test_arm_elf_target_detected_not_x64(tmp_path, monkeypatch):
    tool = module(monkeypatch)
    p = tmp_path / "elf"
    p.write_bytes(native("linux-aarch64"))
    assert tool.binary_targets(p) == {"linux-aarch64"}


@pytest.mark.parametrize("assignment", ["cuda='12.8'", "hip='6.3'"])
def test_arm_gpu_version_flags_rejected(fixture, assignment):
    tool, create = fixture
    f = create("linux-aarch64")
    f["update_file"](
        "python/lib/site-packages/torch/version.py",
        ("__version__='2.14.0+cpu'\ncuda=None\nhip=None\n" + assignment + "\n").encode(),
    )
    with pytest.raises(tool.PackError, match="gpu_torch_rejected"):
        f["verify"]()


def test_arm_linkage_cannot_borrow_x64_system_loader(fixture):
    tool, create = fixture
    f = create("linux-aarch64")
    f["linkage"]["binaries"][0]["dependencies"] = [
        {"origin": "system", "name": "ld-linux-x86-64.so.2"}
    ]
    with pytest.raises(tool.PackError, match="unbundled_native_dependency"):
        f["verify"]()


@pytest.mark.parametrize(
    "target,loader",
    [
        ("linux-x86_64", "ld-linux-x86-64.so.2"),
        ("linux-x86_64", "ld-linux-x86-64.5cbc5e90.so.2"),
        ("linux-aarch64", "ld-linux-aarch64.so.1"),
        ("linux-aarch64", "ld-linux-aarch64.5cbc5e90.so.1"),
    ],
)
def test_bundled_elf_loader_rejected_even_with_complete_linkage(fixture, target, loader):
    tool, create = fixture
    f = create(target)
    path = "python/lib/site-packages/torchvision.libs/" + loader
    f["add"](path, native(target), f["runtime"])
    f["linkage"]["binaries"].append(
        {
            "path": path,
            "sha256": f["rows"][path]["sha256"],
            "tool": "synthetic-test-only",
            "rawEvidence": f["raw_ref"],
            "dependencies": [],
        }
    )
    f["linkage"]["binaries"][0]["dependencies"].append(
        {"name": loader, "origin": "pack", "path": path}
    )
    with pytest.raises(tool.PackError, match="^recognition_bundled_linux_loader$"):
        f["verify"]()


@pytest.mark.parametrize(
    "target,loader",
    [
        ("linux-x86_64", "ld-linux-x86-64.so.2"),
        ("linux-aarch64", "ld-linux-aarch64.so.1"),
    ],
)
def test_system_loader_reference_and_nonelf_names_remain_allowed(fixture, target, loader):
    _, create = fixture
    f = create(target)
    f["linkage"]["binaries"][0]["dependencies"].append({"name": loader, "origin": "system"})
    # A data file with the same basename is not a bundled ELF loader.
    f["add"]("licenses/" + loader, b"Synthetic non-ELF text", f["runtime"])
    # Ordinary DSOs remain eligible, with their own complete linkage evidence.
    path = "python/lib/libcodec.so.1"
    f["add"](path, native(target), f["runtime"])
    f["linkage"]["binaries"].append(
        {
            "path": path,
            "sha256": f["rows"][path]["sha256"],
            "tool": "synthetic-test-only",
            "rawEvidence": f["raw_ref"],
            "dependencies": [{"name": loader, "origin": "system"}],
        }
    )
    _, report = f["verify"]()
    assert report["executionVerified"] is False


@pytest.mark.parametrize("target", ["linux-x86_64", "linux-aarch64"])
def test_qt_empty_interpreter_is_not_exempted(fixture, target):
    tool, create = fixture
    f = create(target)
    data = bytearray(native(target))
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    struct.pack_into("<IIQQQQQQ", data, 64, 3, 4, 120, 0, 0, 1, 1, 1)
    path = "python/lib/libQt5Core-example.so.5.15.19"
    f["add"](path, bytes(data), f["runtime"])
    with pytest.raises(tool.PackError, match="^recognition_foreign_native_binary$"):
        f["verify"]()


def test_stage_passes_only_explicit_native_role_and_keeps_policy_evidence(fixture, monkeypatch):
    import linux_abi

    _, create = fixture
    f = create("linux-aarch64")
    library = "python/lib/libcodec.so.1"
    f["add"](library, native("linux-aarch64"), f["runtime"])
    f["rows"][library]["nativeRole"] = "shared-library"
    f["linkage"]["binaries"].append(
        {
            "path": library,
            "sha256": f["rows"][library]["sha256"],
            "tool": "synthetic-test-only",
            "rawEvidence": f["raw_ref"],
            "dependencies": [],
        }
    )
    original = linux_abi.inspect_header
    roles = []

    def inspect(path, target, *, role=None):
        roles.append((Path(path).name, role))
        return original(path, target, role=role)

    monkeypatch.setattr(linux_abi, "inspect_header", inspect)
    _, report = f["verify"]()
    assert ("libcodec.so.1", "shared-library") in roles
    assert all(role != "shared-library" for name, role in roles if name != "libcodec.so.1")
    header = next(x for x in report["nativeHeaderEvidence"]["elfHeaders"] if x["path"] == library)
    assert header["headerPolicyVersion"] == linux_abi.ELF_HEADER_POLICY
    assert header["nativeRole"] == "shared-library"
    assert header["interpreterDecision"] is None


def test_declared_executable_cannot_be_relabelled_shared_library(fixture):
    tool, create = fixture
    f = create("linux-aarch64")
    f["rows"]["python/bin/python"]["nativeRole"] = "shared-library"
    with pytest.raises(tool.PackError, match="recognition_foreign_native_binary"):
        f["verify"]()


@pytest.mark.parametrize(
    "target", ["linux-x86_64", "linux-aarch64", "windows-x86_64", "macos-aarch64"]
)
def test_vendored_dist_info_is_data_not_an_extra_installed_wheel(fixture, target):
    _, create = fixture
    f = create(target)
    old = next(digest for digest, path in f["sources"].items() if path.name.startswith("docling-"))
    wheel = f["sources"].pop(old)
    member = "docling/_vendor/example-1.dist-info/METADATA"
    data = b"Name: example\nVersion: 1\n"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(member, data)
    digest = sha(wheel.read_bytes())
    f["sources"][digest] = wheel
    for row in f["source_rows"] + f["provenance"]:
        if row["sha256"] == old:
            row["sha256"] = digest
    for row in f["files"]:
        if row["sourceSha256"] == old:
            row["sourceSha256"] = digest
    f["lock_path"].write_text(f["lock_path"].read_text().replace(old, digest))
    f["audit"]["wheelLock"]["sha256"] = sha(f["lock_path"].read_bytes())
    f["add"]("python/lib/site-packages/" + member, data, digest)
    f["verify"]()
    assert (f["stage"] / "python/lib/site-packages" / member).read_bytes() == data


@pytest.mark.parametrize("top_level", [[], ["one-1.dist-info", "two-2.dist-info"]])
def test_vendored_metadata_cannot_replace_or_disambiguate_top_level(
    tmp_path, monkeypatch, top_level
):
    tool = module(monkeypatch)
    path = tmp_path / "example.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pkg/_vendor/pkg-1.dist-info/METADATA", "Name: pkg\nVersion: 1\n")
        for name in top_level:
            archive.writestr(name + "/METADATA", "Name: pkg\nVersion: 1\n")
    with (
        zipfile.ZipFile(path) as archive,
        pytest.raises(tool.PackError, match="recognition_invalid_wheel"),
    ):
        tool.wheel_metadata(archive)


@pytest.mark.parametrize(
    "path",
    ["pkg/_vendor/pkg-1.dist-info/METADATA", "pkg\\pkg-1.dist-info/METADATA", "METADATA"],
)
def test_only_direct_distribution_metadata_is_counted(monkeypatch, path):
    tool = module(monkeypatch)
    assert not tool.distribution_metadata_path(path)
    assert tool.distribution_metadata_path("pkg-1.dist-info/METADATA")


def declare_local_derivation(f, digest):
    """Synthetic provenance contract only; this helper does not build a wheel."""
    parent = f["source"]("parent-source.tar", b"Synthetic parent source", "build-source")
    f["provenance"][:] = [s for s in f["provenance"] if s["sha256"] != digest]
    recipe, evidence = "derivation/recipe.txt", "derivation/build.json"
    f["add"](recipe, b"Synthetic recipe, no build executed", parent)
    f["add"](evidence, b'{"synthetic":true,"executionVerified":false}', parent)
    f["declaration"]["provenance"].update(
        schemaVersion="document-files.pack-provenance.v2",
        derivedArtifacts=[
            {
                "sha256": digest,
                "inputs": [parent],
                "recipe": {k: f["rows"][recipe][k] for k in ("path", "sha256")},
                "buildEvidence": {k: f["rows"][evidence][k] for k in ("path", "sha256")},
            }
        ],
    )
    f["audit"]["schemaVersion"] = "document-files.recognition-stage-audit.v2"


def test_audit_v2_accepts_pinned_local_wheel_without_fabricated_upstream_url(fixture):
    _, create = fixture
    f = create()
    digest = next(d for d, p in f["sources"].items() if p.name.startswith("docling-"))
    declare_local_derivation(f, digest)
    _, report = f["verify"]()
    assert report["schemaVersion"] == "document-files.recognition-stage-verification.v2"
    assert report["derivedArtifactsVerified"] == 1
    assert report["originalArtifactsVerified"] == len(f["sources"]) - 1
    assert report["executionVerified"] is False
    assert report["modelQuality"] == "not-assessed"


def test_verified_v2_build_seals_derived_provenance_and_exact_audited_files(fixture):
    tool, create = fixture
    f = create()
    digest = next(d for d, p in f["sources"].items() if p.name.startswith("docling-"))
    declare_local_derivation(f, digest)
    f["verify"]()
    output = f["root"] / "derived-recognition.pack.zip"
    receipt = tool.build_verified(
        f["stage"],
        f["declaration_path"],
        f["audit_path"],
        sha(f["audit_path"].read_bytes()),
        f["sources"],
        output,
    )
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert receipt["schemaVersion"] == "document-files.recognition-stage-verification.v2"
    assert receipt["pack"]["sha256"] == sha(output.read_bytes())
    assert manifest["provenance"]["derivedArtifacts"][0]["sha256"] == digest
    assert {e["path"]: e["sha256"] for e in manifest["files"]} == {
        e["path"]: e["sha256"] for e in f["files"]
    }


@pytest.mark.parametrize("audit_version", ["v1", "v3"])
def test_derived_stage_requires_matching_v2_audit(fixture, audit_version):
    tool, create = fixture
    f = create()
    digest = next(d for d, p in f["sources"].items() if p.name.startswith("docling-"))
    declare_local_derivation(f, digest)
    f["audit"]["schemaVersion"] = "document-files.recognition-stage-audit." + audit_version
    with pytest.raises(tool.PackError, match="recognition_wrong_declaration"):
        f["verify"]()


def test_derived_model_cannot_be_counted_as_original_resource(fixture):
    tool, create = fixture
    f = create()
    digest = next(s["sha256"] for s in f["source_rows"] if s["role"] == "layout-model")
    declare_local_derivation(f, digest)
    with pytest.raises(tool.PackError, match="recognition_model_not_original_source_bytes"):
        f["verify"]()


def test_derived_arm_torch_cannot_bypass_original_official_cpu_wheel_check(fixture):
    tool, create = fixture
    f = create("linux-aarch64")
    digest = next(d for d, p in f["sources"].items() if p.name.startswith("torch-"))
    declare_local_derivation(f, digest)
    with pytest.raises(tool.PackError, match="recognition_arm_torch_official_cpu_source_required"):
        f["verify"]()


def declare_authored_metadata(f):
    """Authorship assertions in a synthetic test, not actual build or license evidence."""
    digest = next(d for d, p in f["sources"].items() if p.name.startswith("docling-"))
    declare_local_derivation(f, digest)
    entries = []
    for name, role in (
        ("provenance/derivations/docling/recipe.py", "build-recipe"),
        ("provenance/derivations/docling/record.json", "build-record"),
        ("licenses/pack-bindings/docling.txt", "license-collection"),
    ):
        f["add"](name, b"Synthetic authored documentation only\n", digest)
        row = f["rows"][name]
        del row["sourceSha256"]
        entries.append(
            {
                **{k: row[k] for k in ("path", "sha256", "license")},
                "author": "Synthetic fixture author (not authenticated)",
                "role": role,
                "relatedArtifacts": [digest],
                "basis": "Synthetic association only; no execution or redistribution approval.",
            }
        )
    derived = f["declaration"]["provenance"]["derivedArtifacts"][0]
    for field, index in (("recipe", 0), ("buildEvidence", 1)):
        derived[field] = {k: entries[index][k] for k in ("path", "sha256")}
    evidence = {"schemaVersion": "document-files.authored-metadata.v1", "files": entries}
    evidence_path = f["root"] / "authored.json"

    def write():
        ref = dump(evidence_path, evidence)
        for entry in entries:
            if entry["path"] in f["rows"]:
                f["rows"][entry["path"]]["authoredMetadata"] = ref
        return ref

    write()
    f["audit"]["schemaVersion"] = "document-files.recognition-stage-audit.v3"
    return evidence, evidence_path, write


def test_v3_distinguishes_authored_records_and_preserves_artifact_checks(fixture, monkeypatch):
    tool, create = fixture
    f = create()
    evidence, path, _ = declare_authored_metadata(f)
    original_load, loaded = tool.load, []

    def tracked_load(value):
        loaded.append(value)
        return original_load(value)

    monkeypatch.setattr(tool, "load", tracked_load)
    _, report = f["verify"]()
    assert loaded.count(path) == 1  # Shared evidence is checked once, not once per staged file.
    assert report["schemaVersion"] == "document-files.recognition-stage-verification.v3"
    assert report["authoredMetadataFilesVerified"] == 3
    assert report["authoredMetadataEvidence"] == [
        {"path": path.name, "sha256": sha(path.read_bytes()), "filesVerified": 3}
    ]
    assert report["authorshipAuthenticated"] is False
    assert report["executionVerified"] is False
    assert report["modelQuality"] == "not-assessed"
    assert report["originalArtifactsVerified"] == len(f["sources"]) - 1
    assert report["derivedArtifactsVerified"] == 1
    assert all("sourceSha256" not in f["rows"][e["path"]] for e in evidence["files"])


def test_v3_build_preserves_public_pack_v1_and_exact_inventory(fixture):
    tool, create = fixture
    f = create()
    declare_authored_metadata(f)
    f["verify"]()
    output = f["root"] / "authored-recognition.pack.zip"
    receipt = tool.build_verified(
        f["stage"],
        f["declaration_path"],
        f["audit_path"],
        sha(f["audit_path"].read_bytes()),
        f["sources"],
        output,
    )
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert receipt["schemaVersion"] == "document-files.recognition-stage-verification.v3"
    assert manifest["schemaVersion"] == "document-files.pack.v1"
    assert manifest["provenance"]["schemaVersion"] == "document-files.pack-provenance.v2"
    assert {e["path"]: e["sha256"] for e in manifest["files"]} == {
        e["path"]: e["sha256"] for e in f["files"]
    }


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_legacy_audits_cannot_silently_accept_authored_metadata(fixture, version):
    tool, create = fixture
    f = create()
    declare_authored_metadata(f)
    f["audit"]["schemaVersion"] = "document-files.recognition-stage-audit." + version
    with pytest.raises(tool.PackError, match="recognition_wrong_declaration"):
        f["verify"]()


def test_authored_metadata_requires_provenance_v2(fixture):
    tool, create = fixture
    f = create()
    declare_authored_metadata(f)
    del f["declaration"]["provenance"]["schemaVersion"]
    with pytest.raises(tool.PackError, match="recognition_wrong_declaration"):
        f["verify"]()


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("author", " ", "invalid_authored_metadata"),
        ("author", "a" * 129, "invalid_authored_metadata"),
        ("author", ["author"], "invalid_authored_metadata"),
        ("basis", "", "invalid_authored_metadata"),
        ("basis", "contains\0control", "invalid_authored_metadata"),
        ("basis", "b" * 4097, "invalid_authored_metadata"),
        ("relatedArtifacts", [], "invalid_authored_metadata"),
        ("relatedArtifacts", ["0" * 64], "invalid_authored_metadata"),
        ("relatedArtifacts", [{}], "invalid_authored_metadata"),
        ("sha256", "0" * 64, "authored_metadata_mismatch"),
        ("license", "unknown", "authored_metadata_mismatch"),
        ("role", "wheel", "authored_metadata_not_documentation"),
        ("role", "build-record", "authored_derivation_mismatch"),
    ],
)
def test_authored_record_fields_are_bound_and_validated(fixture, field, value, error):
    tool, create = fixture
    f = create()
    evidence, _, write = declare_authored_metadata(f)
    evidence["files"][0][field] = value
    write()
    with pytest.raises(tool.PackError, match="recognition_" + error):
        f["verify"]()


@pytest.mark.parametrize(
    "fault", ["duplicate", "orphan", "schema", "extra", "missing", "duplicate-related"]
)
def test_authored_record_inventory_is_exact(fixture, fault):
    tool, create = fixture
    f = create()
    evidence, _, write = declare_authored_metadata(f)
    if fault == "duplicate":
        evidence["files"].append(dict(evidence["files"][0]))
    elif fault == "orphan":
        evidence["files"].append({**evidence["files"][0], "path": "licenses/orphan.txt"})
    elif fault == "schema":
        evidence["schemaVersion"] = "document-files.authored-metadata.v2"
    elif fault == "extra":
        evidence["files"][0]["authenticated"] = True
    elif fault == "missing":
        del evidence["files"][0]["author"]
    else:
        evidence["files"][0]["relatedArtifacts"] *= 2
    write()
    with pytest.raises(tool.PackError, match="recognition_.*authored_metadata"):
        f["verify"]()


@pytest.mark.parametrize(
    "fault",
    [
        "mixed-origin",
        "null-ref",
        "changed-evidence",
        "changed-file",
        "executable-flag",
        "executable-mode",
        "symlink",
    ],
)
def test_authored_metadata_cannot_bypass_file_and_evidence_checks(fixture, fault):
    tool, create = fixture
    f = create()
    evidence, path, _ = declare_authored_metadata(f)
    name = evidence["files"][0]["path"]
    if fault == "mixed-origin":
        f["rows"][name]["sourceSha256"] = f["runtime"]
    elif fault == "null-ref":
        f["rows"][name]["authoredMetadata"] = None
    elif fault == "changed-evidence":
        path.write_text(path.read_text() + " ")
    elif fault == "changed-file":
        (f["stage"] / name).write_text("changed\n")
    elif fault == "executable-flag":
        f["declaration"]["executables"].append(name)
    elif fault == "executable-mode":
        (f["stage"] / name).chmod(0o755)
        if not (f["stage"] / name).stat().st_mode & 0o111:
            pytest.skip("Filesystem does not expose executable permission bits")
    else:
        real = path.with_suffix(".real")
        path.rename(real)
        path.symlink_to(real)
    with pytest.raises(tool.PackError, match="recognition_"):
        f["verify"]()


@pytest.mark.parametrize(
    "destination",
    [
        "python/lib/site-packages/docling/recipe.py",
        "models/docling/recipe.py",
        "native/recipe.py",
        "provenance/derivations/docling/recipe.so",
    ],
)
def test_authored_origin_is_limited_to_documentation_paths(fixture, destination):
    tool, create = fixture
    f = create()
    evidence, _, write = declare_authored_metadata(f)
    entry = evidence["files"][0]
    old = entry["path"]
    target = f["stage"] / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    (f["stage"] / old).rename(target)
    row = f["rows"].pop(old)
    entry["path"] = row["path"] = destination
    f["rows"][destination] = row
    write()
    with pytest.raises(tool.PackError, match="recognition_authored_metadata_not_documentation"):
        f["verify"]()


@pytest.mark.parametrize(
    "setting", ["python", "tesseract", "artifacts", "tessdata", "nativeLibraryDirectories"]
)
def test_runtime_configuration_cannot_repurpose_authored_metadata(fixture, setting):
    tool, create = fixture
    f = create()
    evidence, _, _ = declare_authored_metadata(f)
    name = evidence["files"][0]["path"]
    f["declaration"]["recognition"][setting] = (
        [str(Path(name).parent)] if setting == "nativeLibraryDirectories" else name
    )
    with pytest.raises(tool.PackError, match="recognition_authored_metadata_not_documentation"):
        f["verify"]()


@pytest.mark.parametrize("data", [b"text\0data", b"\xff\xfe"])
def test_authored_metadata_must_be_utf8_without_nul(fixture, data):
    tool, create = fixture
    f = create()
    evidence, _, write = declare_authored_metadata(f)
    entry = evidence["files"][0]
    f["update_file"](entry["path"], data)
    entry["sha256"] = sha(data)
    write()
    with pytest.raises(tool.PackError, match="recognition_authored_metadata_not_text"):
        f["verify"]()


@pytest.mark.parametrize(
    "limit", ["AUTHORED_FILE_LIMIT", "AUTHORED_BYTE_LIMIT", "AUTHORED_TOTAL_LIMIT"]
)
def test_authored_metadata_is_resource_bounded(fixture, monkeypatch, limit):
    tool, create = fixture
    f = create()
    declare_authored_metadata(f)
    monkeypatch.setattr(tool, limit, 2)
    with pytest.raises(tool.PackError, match="recognition_authored_metadata_limit"):
        f["verify"]()


@pytest.mark.parametrize(
    "fault", [None, "bytes", "hash", "duplicate", "unknown", "binary", "framing", "role"]
)
def test_license_collection_preserves_explicit_legacy_encoded_terms(fixture, fault):
    tool, create = fixture
    f = create()
    evidence, _, write = declare_authored_metadata(f)
    entry = evidence["files"][2]
    name = "licenses/upstream/legacy.txt"
    data = b"Copyright \xa9 synthetic legacy-encoded fixture\n"
    if fault == "binary":
        data += b"\0"
    f["add"](name, data, f["runtime"])
    ref = {k: f["rows"][name][k] for k in ("path", "sha256")}
    content = (
        b"Authored UTF-8 collection framing\n"
        + f"\n=== BEGIN {name}; SHA256 {ref['sha256']} ===\n".encode()
        + data
        + f"\n=== END {name} ===\n".encode()
    )
    if fault == "bytes":
        content = content.replace(b"Copyright", b"Altered")
    elif fault == "framing":
        content = b"\xff" + content
    entry["embeddedTexts"] = [ref]
    f["update_file"](entry["path"], content)
    entry["sha256"] = sha(content)
    if fault == "hash":
        ref["sha256"] = "0" * 64
    elif fault == "duplicate":
        entry["embeddedTexts"].append(dict(ref))
    elif fault == "unknown":
        ref["path"] = "licenses/not-in-stage.txt"
    elif fault == "role":
        evidence["files"][0]["embeddedTexts"] = [ref]
    write()
    if fault is None:
        _, receipt = f["verify"]()
        assert receipt["authoredMetadataFilesVerified"] == 3
        assert (f["stage"] / name).read_bytes() == data
        assert (f["stage"] / entry["path"]).read_bytes() == content
    else:
        with pytest.raises(tool.PackError, match="recognition_"):
            f["verify"]()
