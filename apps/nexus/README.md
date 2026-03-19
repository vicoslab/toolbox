**Nexus** is a simple web app that integrates together all components of the ViCoS toolbox. At the moment it uses flask, with the hope that a more involved frontend is not necessary, although this may change in the future.

## Contributing
Setup your environment:
```bash
$ uv venv
$ uv sync
```

Run development server:
```bash
$ uv run flask --app nexus run --port 8079 --debug
```

Run in docker:
```bash
docker exec -it (docker ps -q --filter "ancestor=aibox") bash -c "cd /opt/apps/nexus && MODEL_DIR=/opt/models uv run flask --app nexus run --port 8079 --debug"
```
