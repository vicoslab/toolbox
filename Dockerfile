FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt update && \
    apt install -y git nano curl make supervisor ffmpeg libsm6 libxext6

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"
ENV UV_HTTP_TIMEOUT=90

## Node
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash
ENV YARN_CACHE_FOLDER=/root/.yarn
RUN \
    . $HOME/.nvm/nvm.sh && \
    nvm install 24 && \
    corepack enable yarn && \
    ln -s $(which yarn) /root/.local/bin/yarn && \
    ln -s $(which node) /root/.local/bin/node

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
ADD https://github.com/HumanSignal/label-studio.git#1.23.0 .

COPY apps/label-studio/*.patch .
RUN git apply *.patch && rm *.patch

ADD https://install.python-poetry.org /tmp/install-poetry.py
RUN cd /tmp && uv run python /tmp/install-poetry.py

RUN poetry install
RUN poetry run python label_studio/manage.py collectstatic
RUN --mount=type=cache,target=/root/.yarn make frontend-install frontend-build

RUN poetry install

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
ADD https://github.com/mlflow/mlflow.git#v3.10.1 .

COPY apps/mlflow/*.patch .
RUN git apply *.patch && rm *.patch

RUN --mount=type=cache,target=/root/.cache/uv uv sync
RUN --mount=type=cache,target=/root/.yarn cd mlflow/server/js && yarn install && yarn build
RUN --mount=type=cache,target=/root/.cache/uv uv pip install .

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
ENV TOOLBOX_CACHE=/cache
ENV UV_CACHE_DIR=/cache/.uv
ENV UV_PYTHON_INSTALL_DIR=/cache/.uv-python
ENV HUGGINGFACE_HUB_CACHE=/cache/.huggingface
ENV TORCH_HOME=/cache/.torch

## Proxy/Init configuration
WORKDIR /opt/apps/caddy
COPY apps/caddy/Caddyfile /opt/apps/caddy
COPY ./supervisord.conf /etc/supervisord.conf
ENTRYPOINT [ "supervisord" ]
