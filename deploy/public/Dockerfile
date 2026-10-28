# MBNAS Research Console 0.1.0 - PUBLIC runtime image, CUDA 12.4 (standard pip installation path).
# One image, two roles: `gateway` (user API /api/v1 + the built web console, port 8710) and `runner` (GPU job daemon).
# Contents: Python 3.10 venv with requirements-public.txt (pinned third-party set + the four public project wheels)
# and mbnas_web/dist. It holds no private core package and no secrets: tokens and keys come from env files at run time.
#
# Build context = the unpacked public ZIP root (the directory holding requirements-public.txt, wheels/ and
# mbnas_web/dist); only the files named in the COPY lines below enter the image:
#   docker build -f deploy/public/Dockerfile -t mbnas-public:0.1.0 .
# Online build = network access to PyPI and download.pytorch.org/whl/cu124. Build from a local wheelhouse instead:
#   python3 -m http.server --bind 127.0.0.1 8731 --directory <wheelhouse> &
#   docker build --network host --build-arg PIP_EXTRA_ARGS="--no-index --find-links http://127.0.0.1:8731/" ...
# (PIP_EXTRA_ARGS is visible in the image history: never put credentials in it.)
# Run: deploy/public/docker-compose.yml, or
#   docker run --rm --init --env-file config/public/gateway.env -p 127.0.0.1:8710:8710 mbnas-public:0.1.0 gateway
#   docker run --rm --init --gpus device=0 --shm-size 8g --env-file config/public/runner.env mbnas-public:0.1.0 runner
# GPU jobs need an NVIDIA driver for CUDA 12.4 and the NVIDIA Container Toolkit on the host.
ARG BASE_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04
FROM ${BASE_IMAGE}

ARG MBNAS_VERSION=0.1.0
ARG PIP_EXTRA_ARGS=""
ARG MBNAS_UID=10001
ARG MBNAS_GID=10001

# Python 3.10 (Ubuntu 22.04) and the shared libraries of the pinned OpenCV wheels (libGL, GLib)
RUN export DEBIAN_FRONTEND=noninteractive \
 && apt-get update \
 && apt-get install -y --no-install-recommends python3.10 python3.10-venv ca-certificates libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/mbnas/venv \
    PATH=/opt/mbnas/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONNOUSERSITE=1 \
    PIP_CONFIG_FILE=/dev/null \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /opt/mbnas/install
RUN python3.10 -m venv /opt/mbnas/venv \
 && python -m pip install ${PIP_EXTRA_ARGS} "pip==25.3"

# third-party layer first (rebuilt only when the pins change; the instructions up to this layer are identical in
# deploy/owner/Dockerfile, so both images share it on one host), then the project wheels on top of it
COPY requirements-public.txt ./
RUN grep -v '^\./wheels/' requirements-public.txt > requirements-thirdparty.txt \
 && python -m pip install ${PIP_EXTRA_ARGS} -r requirements-thirdparty.txt
# public wheels only, named one by one: a build from an owner tree must not copy the private core wheel into a layer
COPY wheels/mbnas_contracts-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_model_runtime-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_runner-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_server-${MBNAS_VERSION}-py3-none-any.whl \
     ./wheels/
RUN python -m pip install ${PIP_EXTRA_ARGS} -r requirements-public.txt \
 && python -m pip check \
 && ! python -c "import mbnas_core" 2>/dev/null

COPY mbnas_web/dist/ /opt/mbnas/web/dist/
COPY deploy/public/docker-entrypoint.sh /usr/local/bin/mbnas-entrypoint
RUN chmod 0755 /usr/local/bin/mbnas-entrypoint \
 && test -f /opt/mbnas/web/dist/index.html \
 && groupadd --system --gid "${MBNAS_GID}" mbnas \
 && useradd --system --no-log-init --uid "${MBNAS_UID}" --gid "${MBNAS_GID}" --home-dir /srv/mbnas --no-create-home \
      --shell /usr/sbin/nologin mbnas \
 && mkdir -p /srv/mbnas/runtime /srv/mbnas/data /tmp/mbnas-home/.config \
 && chown -R "${MBNAS_UID}:${MBNAS_GID}" /srv/mbnas \
 && chmod 1777 /tmp/mbnas-home /tmp/mbnas-home/.config

# runtime defaults; compose/env files override them. MBNAS_HOME holds all state (mount ./runtime here), datasets are
# mounted read-only at /srv/mbnas/data. HOME is a scratch directory so any --user UID works.
ENV MBNAS_HOME=/srv/mbnas/runtime \
    MBNAS_GW_HOST=0.0.0.0 \
    MBNAS_GW_PORT=8710 \
    MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist \
    HOME=/tmp/mbnas-home \
    MPLBACKEND=Agg \
    YOLO_OFFLINE=1 \
    YOLO_AUTOINSTALL=false \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

LABEL org.opencontainers.image.title="MBNAS Research Console - public runtime (CUDA 12.4)" \
      org.opencontainers.image.version="${MBNAS_VERSION}" \
      org.opencontainers.image.description="Roles gateway | runner. Requires a reachable MBNAS core. See THIRD_PARTY_NOTICES.md."
WORKDIR /srv/mbnas
USER ${MBNAS_UID}:${MBNAS_GID}
EXPOSE 8710
ENTRYPOINT ["/usr/local/bin/mbnas-entrypoint"]
