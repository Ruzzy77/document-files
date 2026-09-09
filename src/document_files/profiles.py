"""Resolve administrator-selected installed components without fallback or downloads."""

from __future__ import annotations

from contextlib import contextmanager

from .document_model.docling_adapter import DoclingRecognition, RecognitionConfig
from .interpretation.backends import ModelError
from .runtime_packs import PackError, PackStore, safe_relative


def build_observation_backend(profile, *, parent_managed=False):
    settings = profile.settings
    pack_id = settings.get("recognitionPackId")
    if not pack_id:
        return None
    try:
        pack = PackStore(settings["packRoot"]).resolve(pack_id)
        if pack.manifest["kind"] != "recognition":
            raise PackError("recognition_pack_kind_mismatch")
        config = pack.manifest["recognition"]
        repair_budget = config.get("repairBudget", {})

        def directory(key):
            path = pack.root / safe_relative(config[key])
            if (
                not path.is_dir()
                or path.is_symlink()
                or not path.resolve().is_relative_to(pack.root.resolve())
            ):
                raise PackError("recognition_pack_path_invalid")
            return str(path)

        backend = DoclingRecognition(
            RecognitionConfig(
                artifacts_path=directory("artifacts"),
                tesseract_cmd=str(pack.file(config["tesseract"])),
                tessdata_path=directory("tessdata"),
                python=str(pack.file(config["python"])),
                table_ocr_repair=config.get("tableOcrRepair", "off"),
                repair_max_tables=repair_budget.get("maxTables", 8),
                repair_max_calls=repair_budget.get("maxCalls", 8),
                repair_max_pixels=repair_budget.get("maxPixels", 16000000),
                repair_max_seconds=repair_budget.get("maxSeconds", 60),
            ),
            identity={
                "packManifestSha256": pack.manifest_sha256,
                "packId": pack_id,
                "packVersion": pack.manifest["version"],
            },
            parent_managed=parent_managed,
        )
        backend.config.validate()
        return backend
    except (PackError, ValueError, KeyError, OSError):
        raise ModelError("recognition_pack_unavailable") from None


def probe_profile(profile):
    """Probe installed configuration, not model quality or an authenticated network request."""
    from .server_worker import build_model_client

    client = None
    try:
        client = build_model_client(profile)
        recognition = build_observation_backend(profile)
        return {
            "configured": True,
            "model": client.identity,
            "recognition": {k: v for k, v in recognition.identity.items() if k != "configuration"}
            if recognition
            else {"configured": False},
            "inferenceVerified": False,
            "qualityQualified": False,
            "networkUsed": False,
        }
    except (ModelError, PackError, ValueError, OSError) as exc:
        return {
            "configured": False,
            "issue": getattr(exc, "code", "profile_unavailable"),
            "inferenceVerified": False,
            "qualityQualified": False,
            "networkUsed": False,
        }
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()


@contextmanager
def profile_clients(config_path, profile_name, *, parent_managed=False):
    from .http_server import build_service_from_config
    from .server_worker import build_model_client

    service = build_service_from_config(config_path)
    profile = service.profile(profile_name)
    client = build_model_client(profile)
    try:
        yield client, build_observation_backend(profile, parent_managed=parent_managed)
    finally:
        if hasattr(client, "close"):
            client.close()
