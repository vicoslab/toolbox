#! /usr/bin/env bash
function get_fragments {
    find $1 -name Dockerfile.fragment -execdir bash -c 'basename $PWD' \; | sort
}

target=$1
if [[ -z $target ]]; then
    target=.*
fi
models=$(get_fragments models)
apps=$(get_fragments apps)

# RUN commands with buildx cache attached
RUNUV="RUN --mount=type=cache,target=/root/.cache/uv uv"
RUNAPT="RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    apt"

echo "FROM $ROOT"
echo "$RUNAPT update && apt install -y git nano curl make supervisor ffmpeg libsm6 libxext6"
echo "RUN curl -LsSf https://astral.sh/uv/install.sh | sh"
echo "ENV PATH=\"/root/.local/bin:\$PATH\""

echo "RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash"
echo "ENV YARN_CACHE_FOLDER=/root/.yarn"
echo "RUN . \$HOME/.nvm/nvm.sh && nvm install 24 && corepack enable yarn && \
    ln -s \$(which yarn) /root/.local/bin/yarn && \
    ln -s \$(which node) /root/.local/bin/node"

for model in $models; do
    if [[ $model =~ $target ]]; then
        echo "ARG src=models/$model"
        echo "WORKDIR /opt/models/$model"
        sed "s|^RUN uv|$RUNUV|" models/$model/Dockerfile.fragment
    fi
done

for app in $apps; do
    if [[ $app =~ $target ]]; then
        echo "ARG src=apps/$app"
        echo "WORKDIR /opt/apps/$app"
        sed "s|^RUN uv|$RUNUV|" apps/$app/Dockerfile.fragment
    fi
done

echo "COPY ./supervisord.conf /etc/supervisord.conf"
echo "ENTRYPOINT [ \"supervisord\" ]"