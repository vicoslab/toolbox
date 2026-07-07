all:
	docker build -t toolbox .

git:
	git archive --format tar HEAD | docker build -t toolbox -

