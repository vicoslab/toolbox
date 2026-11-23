import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import mlflow
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

mlflow.set_tracking_uri("http://localhost:8081/app/mlflow")
ARTIFACTS = os.environ["MLFLOW_ARTIFACTS_DESTINATION"]
CACHE = Path(os.environ["TOOLBOX_CACHE"])
DATA = Path(os.environ["TOOLBOX_DATA"])
DATASET_DIR = Path(os.environ["LOCAL_FILES_DOCUMENT_ROOT"])
API_KEY = os.environ["LABEL_STUDIO_API_KEY"]

# make sure virutal env doesn't bleed into subprocesses
if "VIRTUAL_ENV" in os.environ:
    del os.environ["VIRTUAL_ENV"]

# leave false; does nothing at the moment
debug = False


class WorkerInfo(BaseModel):
    model: str
    port: int
    options: dict = Field(exclude=True)


class ToolInfo(BaseModel):
    name: str
    model: str
    enabled: bool
    options: dict


(toolbox_dir := DATA / "toolbox").mkdir(exist_ok=True)
models_file = toolbox_dir / "models.json"


def save_models(config):
    with models_file.open("w") as f:
        json.dump(jsonable_encoder(config), f)


try:
    if models_file.exists():
        models_config: dict = json.loads(models_file.read_text())
        models_config["added"] = list(set(models_config.get("added", [])))
        models_config["tools"] = {
            cat: {k: ToolInfo(**v) for k, v in items.items()}
            for cat, items in models_config.get("tools", {}).items()
        }
    elif models_str := os.getenv("TOOLBOX_MODELS"):
        models_config: dict = {
            "added": [],
            "sources": json.loads(base64.b64decode(models_str)),
            "tools": {},
        }
        save_models(models_config)
    else:
        models_config: dict = {"added": [], "sources": [], "tools": {}}
        save_models(models_config)
    if type(models_config) != dict:
        print("Invalid models.json, overwriting")
        raise ValueError("models.json not an object")
except ValueError as e:
    print(f"Could not load models.json, using empty. (Error: {e})")
    if models_file.exists():
        old = models_file.with_suffix(models_file.suffix + ".bak")
        print(f"Saving old sources config to {old!s}")
        models_file.rename(old)
    models_config = {"added": [], "sources": [], "tools": {}}
    save_models(models_config)

model_manifest = {}
tags = set()


def refresh_manifest():
    model_manifest.clear()
    did_change = False
    for src in models_config["sources"]:
        if "sid" not in src:
            src["sid"] = hashlib.sha256(
                f"{src['owner']}/{src['group']}".encode()
            ).hexdigest()[:7]
            did_change = True
        repo = CACHE / ".models" / src["owner"] / src["group"]
        if not repo.exists():
            branch = ["--branch", src["branch"]] if "branch" in src else []
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    *branch,
                    src["url"],
                    str(repo),
                ],
                check=True,
            )
            subprocess.run(
                ["git", "sparse-checkout", "init", "--cone"], cwd=repo, check=True
            )
        src["rev"] = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        for name in src["models"]:
            model_path = repo / name
            if not model_path.exists():
                subprocess.run(
                    ["git", "sparse-checkout", "add", name], cwd=repo, check=True
                )
                subprocess.run(["git", "checkout"], cwd=repo, check=True)

            if name not in models_config["added"] and (CACHE / name).exists():
                models_config["added"].append(name)
                did_change = True

            manifest = model_path / "model.json"
            model = {}

            model_id = f"{src['sid']}-{name}"
            if manifest.exists():
                manifest = json.loads(manifest.read_text())
                model["options"] = manifest["properties"]
                model["title"] = manifest["title"]
                model["description"] = manifest["description"]
                model["dir"] = model_path
                model["source"] = src
                model["train"] = (model_path / "train.py").exists()
                model["tags"] = manifest.get("x-toolbox-tags", [])
                # if tools are a list (old way), the model doesn't distinguish between default/supported tools
                # default tools are ones that are available if started without any options
                if type(tool := manifest.get("x-toolbox-tools", [])) == dict:
                    model["tool"] = tool
                else:
                    model["tool"] = {"supported": tool, "default": tool}
                # add model's tools into config
                for category in model["tool"]["default"]:
                    if category not in models_config["tools"]:
                        models_config["tools"][category] = {}
                    if model_id not in models_config["tools"][category]:
                        models_config["tools"][category][model_id] = ToolInfo(
                            name=manifest["title"],
                            enabled=Path(CACHE, model_id).exists(),
                            options={},
                            model=model_id,
                        )
                tags.update(model["tags"])
                if notice := manifest.get("x-toolbox-notice"):
                    model["notice"] = notice
                with open(model_path / "ui.html") as f:
                    model["form"] = f.read()

            if model != {}:
                model_manifest[model_id] = model
            else:
                src["models"].remove(name)
                did_change = True
    if did_change:
        save_models(models_config)


refresh_manifest()
