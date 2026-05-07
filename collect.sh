#! /usr/bin/env bash
function get_fragments {
    find $1 -name Dockerfile.fragment -execdir bash -c 'basename $PWD' \; | sort
}

apps=$(get_fragments apps)

# RUN commands with buildx cache attached
RUNUV="RUN --mount=type=cache,target=/root/.cache/uv uv"
RUNAPT="RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt"

echo "# This file was automatically generated. Any changes may get overwritten without warning."
echo "FROM $ROOT"
echo "$RUNAPT update && apt install -y git nano curl make supervisor ffmpeg libsm6 libxext6"
echo "RUN curl -LsSf https://astral.sh/uv/install.sh | sh"
echo "ENV PATH=\"/root/.local/bin:\$PATH\""
echo "ENV UV_HTTP_TIMEOUT=90"

echo "RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash"
echo "ENV YARN_CACHE_FOLDER=/root/.yarn"
echo "RUN . \$HOME/.nvm/nvm.sh && nvm install 24 && corepack enable yarn && \
    ln -s \$(which yarn) /root/.local/bin/yarn && \
    ln -s \$(which node) /root/.local/bin/node"

for app in $apps; do
    echo "## App: $app"
    echo "ARG src=apps/$app"
    echo "WORKDIR /opt/apps/$app"
    sed "s|^RUN uv|$RUNUV|" apps/$app/Dockerfile.fragment
    echo
done

echo "## Apps end"
echo "COPY models /opt/models"

echo "ENV TOOLBOX_CACHE=/cache"
echo "ENV UV_CACHE_DIR=/cache/.uv"
echo "ENV HUGGINGFACE_HUB_CACHE=/cache/.huggingface"
echo "ENV TORCH_HOME=/cache/.torch"

echo "COPY apps/caddy/Caddyfile /opt/apps/caddy"
echo "COPY ./supervisord.conf /etc/supervisord.conf"
echo "ENTRYPOINT [ \"supervisord\" ]"