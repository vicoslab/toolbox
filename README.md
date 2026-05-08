# Toolbox
ViCoS toolbox is a collection of tools packaged as a docker image, alongside installation and inference/training scripts for models.

## Building
Currently, everything is part of a single image to simplify deployment (since the apps are only single-tenant, its easier to manage as a whole unit).

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
```

## Caching
When installing models, they place their source files, managed python versions and dependencies into `/cache`. In order to persist this state across container runs, you should also mount this directory somewhere:
```bash
docker run --rm -it --network host --ipc host -v /path/to/datasets:/data --env-file .env --env-file .env.persist --device nvidia.com/gpu=all -v ~/.cache/toolbox:/cache --mount type=volume,src=toolbox-persist,dst=/persist toolbox
```
