# Toolbox
ViCoS toolbox is a collection of tools packaged as a docker image, alongside the machinery to manage installation and inference/training scripts for models.

## Running
Running the image is pretty straightforward as far as docker is concerned. You can try running the toolbox with the following command, but **anything you do
will not be saved**:

`docker run --rm -it --publish 443:443 --device nvidia.com/gpu=all vicoslab/toolbox`

The toolbox should then be accessible in your browser at [localhost](https://localhost).

Additionally, you may also:
- publish the public inference endpoint (container port 444 by default)
- mount your datasets to `/data`
- mount a cache folder to `/cache`
- mount data which should be persisted to `/persist`
- one thing which may be needed during training is larger shm sizes (i.e. running the toolbox with something like `--shm-size=2gb` or larger)

```bash
docker run --rm -it \
    --publish 443:443 --publish 444:444 \
    --device nvidia.com/gpu=all \
    -v /path/to/datasets:/data \
    -v ~/.cache/toolbox:/cache \
    --mount type=volume,src=toolbox-persist,dst=/persist \
    toolbox
```

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
