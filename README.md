# Toolbox
ViCoS toolbox is a collection of tools and models packaged as a docker image.

## Building
The docker image is built by collecting dockerfile fragments for the various tools. Tools *should* install their dependencies using `RUN uv` (this exact form, as it gets replaced with flags for caching), where uv is used to make sure tools share dependencies where possible. A single docker image is used to fascilitate sharing between components and avoid the burden of orchestration.

To build the entire image, run `make`.

To build just a single model, run `make <model-name>`.

## Running

You will likely need to run some or all of the other components to test the functionality of the toolbox. You may do so individually, or use the docker:
```bash
# depending on how nvidia-container-toolkit is configured, you may need to use nvidia.com/gpu=all
# usage for other devices varies
docker run --rm -it --network host --ipc host -v /path/to/datasets:/data --env-file .env --device gpus=all toolbox
```

## Persistence
In order to persist label-studio projects, mlflow runs, etc., a docker volume is needed: `docker volume create toolbox-persist`.
This volume should be mounted to `/persist` when running containers, and additional environment variables must be passed to set the data dirs:
```bash
docker run --rm -it --network host --ipc host -v /path/to/datasets:/data --env-file .env --env-file .env.persist --device nvidia.com/gpu=all --mount type=volume,src=toolbox-persist,dst=/persist toolbox
``
