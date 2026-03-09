**Nexus** is a simple web app that integrates together all components of the ViCoS toolbox. At the moment it uses flask, with the hope that a more involved frontend is not necessary, although this may change in the future.

## Contributing
Setup your environment:
```bash
$ uv venv
$ uv sync
```

Run development server:
```bash
$ uv run flask run --port 8079 --debug
```

You will likely need to run some or all of the other components to test the functionality of the toolbox. You may do so individually, or use the docker:
```bash
# depending on how nvidia-container-toolkit is configured, you may need to use nvidia.com/gpu=all
# usage for other devices varies
docker run --rm -it --network host --ipc host -v /home/user/datasets:/data --env-file .env --device gpus=all aibox
```
