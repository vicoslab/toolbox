ROOT = 'nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04'
# ROOT = 'aibox-base'

all:
	ROOT=$(ROOT) ./collect.sh | docker build -t aibox -f- .

.phony: %
% : models/%/Dockerfile.fragment
	docker build -t aibox-model-$@ -f- . << EOF
		FROM $(ROOT)
		ARG src=models/$@
		$(cat models/$@/Dockerfile.fragment)
		EOF

apps: image-apps.nix
	nix-build image-apps.nix
	./result | docker load

publish:
	docker tag aibox 192.168.1.114:5000/aibox
	docker push 192.168.1.114:5000/aibox