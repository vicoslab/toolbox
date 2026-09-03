# Toolbox
ViCoS toolbox is a collection of tools packaged as a docker image, alongside the machinery to manage installation and inference/training scripts for models.

## Running
Running the image is pretty straightforward as far as docker is concerned. You can try running the toolbox with the following command, but **anything you do
will not be saved**:

`docker run --rm -it --publish 443:443 --device nvidia.com/gpu=all vicoslab/toolbox`

The toolbox should then be accessible in your browser at [localhost](https://localhost).

Additionally, you may also:
- publish the public inference endpoint (container port 444 by default)
- run as your own user, to prevent permissions in your mounted folders from getting messed up
- mount your datasets to `/data`
- mount a cache folder to `/cache`
- mount data which should be persisted to `/persist`
- one thing which may be needed during training is larger shm sizes (i.e. running the toolbox with something like `--shm-size=2gb` or larger)

```bash
docker run --rm -it \
    --publish 443:443 --publish 444:444 \
    --device nvidia.com/gpu=all \
    --user $(id -u):$(id -g) \
    -v /path/to/datasets:/data \
    -v ~/.cache/toolbox:/cache \
    --mount type=volume,src=toolbox-persist,dst=/persist \
    toolbox
```

## Configuration
Additional configuration is done through environment variables

### TOOLBOX_AUTOSTART
If set, inference requests will be able to autostart corresponding models. For example '{"test": {"model": "geco2", "config": {}}}' will make the alias "test"
autostart the model "geco2" with default options.

> If not clear already, the value of TOOLBOX_AUTOSTART should be a json formatted string containing an object. It's keys should be worker aliases, while values
> are objects with key "model" for model id and "config" for model specific options.

### TOOLBOX_ACCESS_LOG
If set, inference requests *ONLY to the public endpoint* are logged into the file specified in the env var (e.g. "/persist/toolbox/access.log").

### TOOLBOX_MODELS
If set, it is used as the model import string used *ONLY* to initialize the models.json file.

## Deploying
When deploying, you need to additionally consider the following env variables:
```bash
DOMAIN=localhost:443
DOMAIN_PUBLIC=localhost:444
HOST=https://localhost/app/label-studio
PUBLIC_URL=https://localhost/app/label-studio
LABEL_STUDIO_HOST=https://localhost/app/label-studio
```
If you do not intend to host a public inference endpoint, you may omit `DOMAIN_PUBLIC`, although in any case the toolbox performs absolutely 0 authentication. The `HOST`,
`PUBLIC_URL` and `LABEL_STUDIO_HOST` from label studio do not seem to play well with ports.

## Building
Currently, everything is part of a single image to simplify deployment (since the apps are only single-tenant, its easier to manage as a whole unit).
The docker build is multistage and utilises caching where possible, so building shouldn't take too long, except the initial build, which may take up to 15 minutes.

## Testing
We are using integration tests for development, which require multi GB downloads and gpu-accelerated training. While it may be technically possible to use these in CI/CD, it
is easier to run these in the development environment:
```bash
    CONTAINER_ARGS="-v ~/.cache/toolbox/.uv:/cache/.uv -v ~/.cache/toolbox/.torch:/cache/.torch --publish 443:443 --device nvidia.com/gpu=all" make test
```

You can also add arguments to pytest using TEST_ARGS, for example `TEST_ARGS="test_nexus.py::test_import_export"`.

`make test` will build an image with local context, and run it with the test entrypoint. Providing a gpu and the uv and torch caches is required due to strict timeouts.
Publishing the toolbox port is useful as testing will not exit automatically if there were any failing tests. It is then possible to manually inspect the ui/exec into the docker.

`make test-git` should always be used before pushing as it will build image from git HEAD (i.e. without local changes) and run tests.
