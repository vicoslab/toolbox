ROOT = 'nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04'

all:
	ROOT=$(ROOT) ./collect.sh > Dockerfile.generated
	(echo Dockerfile.generated && git ls-files) | tar Tzc - | docker build -t toolbox -f Dockerfile.generated -
