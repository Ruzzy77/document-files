"""One managed extraction attempt; launched by JobService, not an installed daemon."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import threading
from contextlib import suppress

from .interpretation.backends import ChatCompletionsClient, ModelError
from .interpretation.workflow import _ownership, extract_schema, resume_extraction
from .jobs import JobError, JobStore, ModelProfile, resolve_profile_identity
from .profiles import build_observation_backend


def build_model_client(profile: ModelProfile):
    """Resolve only a previously persisted administrator profile."""
    settings = profile.settings
    if profile.kind == "chat-completions":
        key_env = settings.get("apiKeyEnv")
        if key_env and not os.environ.get(key_env):
            raise ModelError("ai_authentication_missing")
        return ChatCompletionsClient(
            settings["endpoint"],
            settings["model"],
            os.environ.get(key_env, "") if key_env else "",
            response_format=settings.get("responseFormat", "json_object"),
            max_output_tokens=settings.get("maxOutputTokens"),
            strict_schema=settings.get("strictSchema", True),
            sampling=settings.get("sampling"),
        )
    if profile.kind == "local-pack":
        from .interpretation.backends import ManagedPackClient

        return ManagedPackClient(
            settings["packRoot"],
            settings["runtimeId"],
            settings["modelId"],
            threads=settings.get("threads"),
            threads_batch=settings.get("threadsBatch"),
        )
    raise ModelError("ai_profile_unsupported")


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_job(
    store: JobStore,
    job_id: str,
    *,
    resolver=build_model_client,
    observation_resolver=build_observation_backend,
) -> int:
    """Invoke the normal workflow; checkpoints remain readable during execution."""
    client = None
    with _ownership(store.root / "worker.sqlite3"):
        record = store.record(job_id)
        if record["status"] != "running":
            return 1
        try:
            path = store.snapshot(record)
            if path.stat().st_size != record["size"] or _digest(path) != record["sha256"]:
                raise JobError("snapshot-mismatch")
            profile = ModelProfile(**json.loads(record["profile"]))
            if resolve_profile_identity(profile) != json.loads(record["pack_identity"]):
                raise JobError("profile-pack-changed", 409)
            client = resolver(profile)
            if client is None:
                raise ModelError("ai_unavailable")
            if profile.kind == "local-pack":
                pinned = json.loads(record["pack_identity"])
                identity = client.identity
                if identity.get("runtimeManifestSha256") != pinned.get("runtimeId") or identity.get(
                    "modelManifestSha256"
                ) != pinned.get("modelId"):
                    raise JobError("profile-pack-changed", 409)
            observation = observation_resolver(profile, parent_managed=True)
            recognition_pin = json.loads(record["pack_identity"]).get("recognitionPackId")
            if recognition_pin and (
                observation is None
                or observation.identity.get("packManifestSha256") != recognition_pin
            ):
                raise JobError("profile-pack-changed", 409)
            if record["resume"] and (store.results / f"{job_id}.sqlite3").is_file():
                result = resume_extraction(
                    job_id,
                    path=str(path),
                    model_client=client,
                    storage_dir=store.results,
                    additional_budget=json.loads(record["budget_grant"])
                    if record["budget_grant"]
                    else None,
                    observation_backend=observation,
                )
            else:
                result = extract_schema(
                    str(path),
                    request_id=job_id,
                    model_client=client,
                    options=json.loads(record["options"]),
                    storage_dir=store.results,
                    observation_backend=observation,
                )
            if _digest(path) != record["sha256"]:
                raise JobError("snapshot-mismatch")
            status = (
                "complete"
                if (
                    result.get("extraction", {}).get("status") == "complete"
                    and result.get("validation", {}).get("valid") is True
                )
                else "partial"
            )
            store.transition(job_id, ("running",), status)
            # Exit zero means the attempt finished, not that extraction was complete.
            # Job status exposes these as executionStatus and extractionStatus.
            return 0
        except (KeyboardInterrupt, SystemExit):
            store.transition(job_id, ("running",), "interrupted", error="worker-stopped")
            return 1
        except Exception as exc:
            # Exception strings may contain input text, endpoint URLs or credentials.
            code = "extraction-failed"
            if (
                isinstance(exc, ModelError)
                and exc.code
                in {
                    "ai_unavailable",
                    "ai_authentication_missing",
                    "ai_profile_unsupported",
                    "ai_configuration_invalid",
                    "ai_authentication_failed",
                    "ai_rate_limited",
                    "recognition_pack_unavailable",
                }
                or isinstance(exc, JobError)
                and exc.code
                in {
                    "snapshot-mismatch",
                    "profile-pack-changed",
                    "profile-pack-unavailable",
                    "profile-pack-kind-mismatch",
                }
            ):
                code = exc.code
            store.transition(job_id, ("running",), "failed", error=code)
            return 1
        finally:
            if client is not None and hasattr(client, "close"):
                with suppress(Exception):
                    client.close()


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    logging.disable(logging.CRITICAL)

    def stop(_signum, _frame):
        raise SystemExit(1)

    signal.signal(signal.SIGTERM, stop)

    def parent_lifetime():
        # The owning server keeps this pipe open. No request or secret is sent.
        while os.read(0, 1):
            pass
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=parent_lifetime, daemon=True).start()
    try:
        return run_job(JobStore(sys.argv[1]), sys.argv[2])
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
