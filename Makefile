all:
	git ls-files | tar Tzc - | docker build -t toolbox -

%:
	git ls-files | tar Tzc - | docker build -t toolbox --build-arg MODELS=$@ -
