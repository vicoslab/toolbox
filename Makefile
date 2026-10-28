all:
	docker build -t toolbox --build-context branding=branding .

git:
	git archive --format tar HEAD | docker build -t toolbox --build-context branding=branding -

test:
	cd apps/nexus/tests/frontend && TEST_ROOT=$(TEST_ROOT) npx codeceptjs run --steps

test-models: TEST_ROOT ?= $(realpath ../toolbox-models)
test-models: test