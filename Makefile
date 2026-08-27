all:
	docker build -t toolbox --build-context branding=branding .

git:
	git archive --format tar HEAD | docker build -t toolbox --build-context branding=branding -

