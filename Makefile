all:
	git archive --format tar HEAD | docker build -t toolbox -

%:
	git archive --format tar HEAD | docker build -t toolbox --build-arg MODELS=$@ -
