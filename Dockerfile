# check=skip=SecretsUsedInArgOrEnv
FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04 AS base

## Generic builder with uv and yarn
FROM base AS builder
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt update && \
    apt install -y git make curl python3

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
ENV YARN_CACHE_FOLDER=/root/.yarn
RUN \
    . $HOME/.nvm/nvm.sh && \
    nvm install 24 && \
    corepack enable yarn && \
    ln -s $(which yarn) /usr/local/bin/yarn && \
    ln -s $(which node) /usr/local/bin/node

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=90

## Label Studio builder
FROM builder AS build-ls
WORKDIR /opt/apps/label-studio

ADD https://github.com/HumanSignal/label-studio.git#1.23.0 .

COPY apps/label-studio/*.patch .
RUN git apply *.patch

ENV POETRY_CACHE_DIR="/.poetry-cache" \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_VIRTUALENVS_PREFER_ACTIVE_PYTHON=true \
    POETRY_VIRTUALENVS_OPTIONS_ALWAYS_COPY=true \
    PATH="/opt/poetry/bin:$PATH"
ADD https://install.python-poetry.org /tmp/install-poetry.py
RUN cd /tmp && python3 /tmp/install-poetry.py

ENV VENV_PATH="/build/.venv"
RUN --mount=type=cache,target=/.poetry-cache,id=poetry-cache,sharing=locked \
    poetry install --without test && \
    poetry run python label_studio/manage.py collectstatic
RUN --mount=type=cache,target=/root/.yarn \
    make frontend-install frontend-build

## MLFlow builder
FROM builder AS build-mlflow
WORKDIR /opt/apps/mlflow
ADD https://github.com/mlflow/mlflow.git#v3.10.1 .

COPY apps/mlflow/*.patch .
RUN git apply *.patch && rm *.patch

RUN --mount=type=cache,target=/root/.cache/uv uv sync
RUN --mount=type=cache,target=/root/.yarn cd mlflow/server/js && yarn install && yarn build
RUN --mount=type=cache,target=/root/.cache/uv uv pip install . tests/resources/mlflow-test-plugin

## Final image
FROM base
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt update && \
    apt install -y git nano curl make supervisor ffmpeg libsm6 libxext6 unzip

RUN curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL=/usr/local/bin sh
ENV UV_HTTP_TIMEOUT=90

## Caddy
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/var/cache/apt,type=cache,sharing=locked \
    apt update && apt install -y debian-keyring debian-archive-keyring apt-transport-https curl && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && \
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    chmod o+r /etc/apt/sources.list.d/caddy-stable.list && \
    apt update && apt install -y caddy
RUN XDG_DATA_HOME=/usr/local/share caddy start && caddy trust && caddy stop
# should probably reduce the scope of this
RUN chmod -R a=rwX /usr/local/share/caddy

## Label Studio
WORKDIR /opt/apps/label-studio
COPY --from=build-ls /opt/apps/label-studio/.venv .venv
COPY --from=build-ls /opt/apps/label-studio/label_studio label_studio
COPY --from=build-ls /opt/apps/label-studio/web/dist web/dist

## Label Studio Model Inference
ADD --chmod=a+w https://github.com/HumanSignal/label-studio-ml-backend.git /opt/apps/label-studio-ml-backend
# caddy will add self signed certs to the system, so adding this to inference workers makes so we don't need to disable VERIFY_SSL for them
RUN echo "pip-system-certs" >> /opt/apps/label-studio-ml-backend/requirements.txt

## Toolbox helpers for Label Studio
ARG src=apps/ls-utils
WORKDIR /opt/apps/ls-utils
COPY ${src}/templates ./templates
COPY ${src}/export.py ${src}/create.py ${src}/uv.lock ${src}/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv uv sync

## MLFlow
WORKDIR /opt/apps/mlflow
RUN XDG_DATA_HOME=/usr/local/share uv venv --python 3.10
COPY --from=build-mlflow --exclude=bin/python* /opt/apps/mlflow/.venv .venv

## ModelArgs
COPY apps/modelargs /opt/apps/modelargs
RUN chmod a+w /opt/apps/modelargs

## Nexus
ARG src=apps/nexus
WORKDIR /opt/apps/nexus
COPY ${src}/templates ./templates
COPY ${src}/static ./static

ADD https://github.com/opencv/opencv/releases/download/5.0.0/opencv-5.0.0-docs.zip opencv.zip
RUN unzip -p opencv.zip js/bin/opencv.js > static/opencv.js && rm opencv.zip

COPY ${src}/nexus.py ${src}/gateway.py ${src}/uv.lock ${src}/pyproject.toml .

RUN --mount=type=cache,target=/root/.cache/uv uv sync && uv pip install gunicorn

## Cache dirs
ENV TOOLBOX_CACHE=/cache \
    UV_CACHE_DIR=/cache/.uv \
    UV_PYTHON_INSTALL_DIR=/cache/.uv-python \
    HUGGINGFACE_HUB_CACHE=/cache/.huggingface \
    PATH=/opt/apps/mlflow/.venv/bin:/opt/apps/label-studio/.venv/bin:$PATH \
    TORCH_HOME=/cache/.torch

## Persist dirs
ENV LABEL_STUDIO_BASE_DATA_DIR=/persist/label-studio \
    MLFLOW_BACKEND_STORE_URI=sqlite:////persist/mlflow/mlflow.db \
    MLFLOW_ARTIFACTS_DESTINATION=/persist/mlflow/artifacts \
    TOOLBOX_DATA=/persist/toolbox

## label-studio
ENV LATEST_VERSION_CHECK=0 \
    COLLECT_ANALYTICS=false \
    SENTRY_DSN= \
    FRONTEND_SENTRY_DSN= \
    SENTRY_RATE=0 \
    fflag_feat_front_lsdv_e_297_increase_oss_to_enterprise_adoption_short=false \
    LOCAL_FILES_DOCUMENT_ROOT=/data \
    LOCAL_FILES_SERVING_ENABLED=true \
    LABEL_STUDIO_PASSWORD=hackme \
    LABEL_STUDIO_USERNAME=user@localhost \
    LABEL_STUDIO_USER_TOKEN=hackme123 \
    LABEL_STUDIO_API_KEY=hackme123 \
    LABEL_STUDIO_ENABLE_LEGACY_API_TOKEN=true

## mlflow
ENV MLFLOW_DISABLE_TELEMETRY="true" \
    DO_NOT_TRACK="true"

## Proxy/Init configuration
COPY ./Caddyfile /opt/apps/caddy/Caddyfile
COPY ./supervisord.conf /etc/supervisord.conf

## Deployment (you should overwrite these using --env, --env-file, in your compose, or otherwise)
ENV DOMAIN=localhost:443 \
    DOMAIN_PUBLIC=localhost:444 \
    HOST=https://localhost/app/label-studio \
    PUBLIC_URL=https://localhost/app/label-studio \
    LABEL_STUDIO_HOST=https://localhost/app/label-studio

WORKDIR /opt/supervisord
RUN chmod 777 .
ENTRYPOINT [ "supervisord", "-c", "/etc/supervisord.conf" ]
