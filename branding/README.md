## Building a branded image
Creating a branded image is done by changing the values in `properties.css` and building the `Dockerfile` found in `branding`. You can try running the example branding with:
```
docker build -t toolbox \
    --build-arg "TOOLBOX_BRAND_NAME_LONG=AI toolbox deluxe" \
    --build-arg "TOOLBOX_BRAND_NAME_SHORT=AITBD" \
    --build-context branding=branding \
    branding
```

Your own branding configuration does not need to live in this repo (you don't even need to have it cloned):
```
docker build -t toolbox \
    --build-arg "TOOLBOX_BRAND_NAME_LONG=AI toolbox deluxe" \
    --build-arg "TOOLBOX_BRAND_NAME_SHORT=AITBD" \
    --build-context branding=/path/to/my/branding \
    https://github.com/vicoslab/toolbox.git?subdir=branding
```

> Note: `properties.css` is the only file you can control by default. If you need additional customisations you need to change the base/branding `Dockerfile` (depending on your needs).
