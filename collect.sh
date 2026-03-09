#! /usr/bin/env bash
models=$(find models -name Dockerfile.fragment -execdir bash -c 'basename $PWD' \;)

echo "FROM aibox-apps AS apps"
echo "FROM $ROOT"
echo "RUN apt update && apt install -y git nano curl ffmpeg libsm6 libxext6"
echo "RUN curl -LsSf https://astral.sh/uv/install.sh | sh"
echo "RUN ln -s /root/.local/bin/uv /usr/local/bin/uv"

for model in $models; do
    echo "ARG src=models/$model"
    echo "WORKDIR /opt/models/$model"
    cat models/$model/Dockerfile.fragment
done

echo "COPY --from=apps /usr/local /usr/local"
echo "COPY --from=apps /nix /nix"
echo "COPY ./supervisord.conf /etc/supervisord.conf"
echo "ENTRYPOINT [ \"supervisord\" ]"