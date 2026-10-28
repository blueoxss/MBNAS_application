# MBNAS Research Console 0.1.0 - OWNER image (PRIVATE: contains mbnas_core; build and keep it on the owner host,
# never push it to a public registry or hand it to a customer). Standard pip installation path.
# Roles: core (core API, port 8700) | owner-worker (PROPOSE_GENERATION, CPU) | principal (token CLI) | core-doctor,
# and for an all-in-one owner host also gateway (port 8710, web console) | runner | register | version.
# Contents: Python 3.10 venv with requirements-owner.txt (the public third-party set + all five project wheels) and
# mbnas_web/dist. No secrets: tokens and keys come from env files (config/owner/*.env) at run time.
#
# Build context = the unpacked OWNER ZIP root (requirements-public.txt, requirements-owner.txt, wheels/, mbnas_web/dist);
# only the files named in the COPY lines below enter the image:
#   docker build -f deploy/owner/Dockerfile -t mbnas-owner:0.1.0 .
# The instructions up to the third-party layer are identical to deploy/public/Dockerfile (same layers on one host).
# Online build = network access to PyPI and download.pytorch.org/whl/cu124; local wheelhouse: see the header of
# deploy/public/Dockerfile (PIP_EXTRA_ARGS="--no-index --find-links http://127.0.0.1:8731/" with --network host).
# Run: deploy/owner/docker-compose.yml. The core and the owner worker need no GPU; the image also runs on CPU-only hosts.
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
# deploy/public/Dockerfile, so both images share it on one host), then the owner set on top of it
COPY requirements-public.txt ./
RUN grep -v '^\./wheels/' requirements-public.txt > requirements-thirdparty.txt \
 && python -m pip install ${PIP_EXTRA_ARGS} -r requirements-thirdparty.txt
# owner set on top: the five project wheels named one by one (requirements-owner.txt = public pins + mbnas_core)
COPY requirements-owner.txt ./
COPY wheels/mbnas_contracts-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_model_runtime-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_runner-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_server-${MBNAS_VERSION}-py3-none-any.whl \
     wheels/mbnas_core-${MBNAS_VERSION}-py3-none-any.whl \
     ./wheels/
RUN python -m pip install ${PIP_EXTRA_ARGS} -r requirements-owner.txt \
 && python -m pip check \
 && python -c "import mbnas_core, mbnas_runner, mbnas_server"

COPY mbnas_web/dist/ /opt/mbnas/web/dist/
COPY deploy/owner/docker-entrypoint.sh /usr/local/bin/mbnas-entrypoint
RUN chmod 0755 /usr/local/bin/mbnas-entrypoint \
 && test -f /opt/mbnas/web/dist/index.html \
 && groupadd --system --gid "${MBNAS_GID}" mbnas \
 && useradd --system --no-log-init --uid "${MBNAS_UID}" --gid "${MBNAS_GID}" --home-dir /srv/mbnas --no-create-home \
      --shell /usr/sbin/nologin mbnas \
 && mkdir -p /srv/mbnas/runtime /srv/mbnas/data /tmp/mbnas-home/.config \
 && chown -R "${MBNAS_UID}:${MBNAS_GID}" /srv/mbnas \
 && chmod 1777 /tmp/mbnas-home /tmp/mbnas-home/.config

# runtime defaults; compose/env files override them. MBNAS_HOME holds all state (core DB, runs, logs; mount ./runtime
# here), datasets are mounted read-only at /srv/mbnas/data. HOME is a scratch directory so any --user UID works.
ENV MBNAS_HOME=/srv/mbnas/runtime \
    MBNAS_CORE_HOST=0.0.0.0 \
    MBNAS_CORE_PORT=8700 \
    MBNAS_GW_HOST=0.0.0.0 \
    MBNAS_GW_PORT=8710 \
    MBNAS_GW_WEB_DIST=/opt/mbnas/web/dist \
    HOME=/tmp/mbnas-home \
    MPLBACKEND=Agg \
    YOLO_OFFLINE=1 \
    YOLO_AUTOINSTALL=false \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

LABEL org.opencontainers.image.title="MBNAS Research Console - owner (PRIVATE, CUDA 12.4)" \
      org.opencontainers.image.version="${MBNAS_VERSION}" \
      org.opencontainers.image.description="Roles core | owner-worker | principal | core-doctor | gateway | runner. Owner host only."
WORKDIR /srv/mbnas
USER ${MBNAS_UID}:${MBNAS_GID}
EXPOSE 8700 8710
ENTRYPOINT ["/usr/local/bin/mbnas-entrypoint"]
