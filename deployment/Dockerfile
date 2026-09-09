# The caller must supply an audited digest-pinned Python 3.12 Linux image.
# No OS packages, model assets or Python wheels are downloaded by this build.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG BASE_IMAGE
RUN python -c 'import re,sys; assert re.fullmatch(r"[^@]+@sha256:[a-f0-9]{64}",sys.argv[1]), "BASE_IMAGE must be digest pinned"' "$BASE_IMAGE"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DO_NOT_TRACK=1 \
    HOME=/state XDG_CACHE_HOME=/tmp/cache \
    DOCUMENT_FILES_RUNTIME_ROOT=/state/native \
    DOCUMENT_FILES_RHWP=/opt/document-files-native/rhwp/rhwp
COPY wheelhouse /opt/wheelhouse
COPY requirements.lock /opt/requirements.lock
RUN python -m pip install --force-reinstall --no-index --require-hashes --find-links=/opt/wheelhouse -r /opt/requirements.lock \
    && rm -rf /opt/wheelhouse \
    && mkdir -p /state/packs /config \
    && chown -R 10001:10001 /state /config
COPY rhwp /opt/document-files-native/rhwp
RUN chmod 755 /opt/document-files-native /opt/document-files-native/rhwp /opt/document-files-native/rhwp/rhwp \
    && chmod 644 /opt/document-files-native/rhwp/LICENSE /opt/document-files-native/rhwp/build.json
COPY entrypoint.py /opt/document-files-entrypoint.py
USER 10001:10001
WORKDIR /state
ENTRYPOINT ["python", "/opt/document-files-entrypoint.py"]
CMD ["--config", "/config/server.json", "--host", "0.0.0.0", "--port", "8765"]
