FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04 AS base

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
    apt install -y git nano curl make supervisor ffmpeg libsm6 libxext6

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=90

## Caddy
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/var/cache/apt,type=cache,sharing=locked \
    apt update && apt install -y debian-keyring debian-archive-keyring apt-transport-https curl && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list && \
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    chmod o+r /etc/apt/sources.list.d/caddy-stable.list && \
    apt update && apt install -y caddy

## Label Studio
WORKDIR /opt/apps/label-studio
COPY --from=build-ls /opt/apps/label-studio/.venv .venv
COPY --from=build-ls /opt/apps/label-studio/label_studio label_studio
COPY --from=build-ls /opt/apps/label-studio/web/dist web/dist

## Label Studio Model Inference
ADD https://github.com/HumanSignal/label-studio-ml-backend.git /opt/apps/label-studio-ml-backend

## Toolbox helpers for Label Studio
ARG src=apps/ls-utils
WORKDIR /opt/apps/ls-utils
COPY ${src}/templates ./templates
COPY ${src}/export.py ${src}/create.py ${src}/uv.lock ${src}/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv uv sync

## MLFlow
WORKDIR /opt/apps/mlflow
COPY --from=build-mlflow /opt/apps/mlflow/.venv .venv

## ModelArgs
COPY apps/modelargs /opt/apps/modelargs

## Nexus
ARG src=apps/nexus
WORKDIR /opt/apps/nexus
COPY ${src}/templates ./templates
COPY ${src}/static ./static
COPY ${src}/nexus.py ${src}/uv.lock ${src}/pyproject.toml .

RUN --mount=type=cache,target=/root/.cache/uv uv sync
RUN --mount=type=cache,target=/root/.cache/uv uv pip install gunicorn

## Models
COPY models /opt/models

## Cache dirs
ENV TOOLBOX_CACHE=/cache \
    UV_CACHE_DIR=/cache/.uv \
    UV_PYTHON_INSTALL_DIR=/cache/.uv-python \
    HUGGINGFACE_HUB_CACHE=/cache/.huggingface \
    PATH=/opt/apps/mlflow/.venv/bin:/opt/apps/label-studio/.venv/bin:$PATH \
    TORCH_HOME=/cache/.torch

## Proxy/Init configuration
COPY ./Caddyfile /opt/apps/caddy/Caddyfile
COPY ./supervisord.conf /etc/supervisord.conf
ENTRYPOINT [ "supervisord" ]
