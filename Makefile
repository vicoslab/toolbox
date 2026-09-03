CONTAINER_ARGS ?= ''
TEST_ARGS ?= ''

all:
	docker build -t toolbox --build-context branding=branding .

git:
	git archive --format tar HEAD | docker build -t toolbox --build-context branding=branding -

test-git: ID = $$(git archive --format tar HEAD | docker build --build-context branding=branding -q -)
test-git: do-test

test: ID = $$(docker build --build-context branding=branding -q .)
test: do-test

do-test:
	docker run --rm --entrypoint ./run-tests.sh -it --shm-size 2G --workdir /opt/apps/nexus $(CONTAINER_ARGS) $(ID) $(TEST_ARGS)
