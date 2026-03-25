ROOT = 'nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04'

all:
	ROOT=$(ROOT) ./collect.sh | docker build -t toolbox -f- .

.phony: %
% : models/%/Dockerfile.fragment
	ROOT=$(ROOT) ./collect.sh "$@" | docker build -t "toolbox-model-$@" -f- .
