all:
	git ls-files | tar Tzc - | docker build -t toolbox -
