ROOT = 'nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04'

all:
	ROOT=$(ROOT) ./collect.sh | docker build -t aibox -f- .

.phony: %
% : models/%/Dockerfile.fragment
	ROOT=$(ROOT) ./collect.sh "$@" | docker build -t "aibox-model-$@" -f- .

publish:
	docker tag aibox 192.168.1.114:5000/aibox
	docker push 192.168.1.114:5000/aibox