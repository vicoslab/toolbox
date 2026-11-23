import shutil
import subprocess
from subprocess import CalledProcessError

from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import AnyHttpUrl, BaseModel

from . import config, tasks
from .http.common import TourStep


class ModelGroup(BaseModel):
    owner: str
    group: str
    rev: str | None = None
    branch: str | None = None


class ToolboxError:
    @staticmethod
    def mcp(msg: str, **kwargs):
        return ToolError(msg)

    @staticmethod
    def http(msg: str, http_status=400, **kwargs):
        return HTTPException(status_code=http_status, detail=msg)


class ModelGroupDefinition(ModelGroup):
    models: list[str]
    url: AnyHttpUrl


def models_add(data: list[ModelGroupDefinition]):
    """Make provided model groups available in the toolbox."""
    for defs in data:
        defs = dict(defs)
        if defs["branch"] is None:
            del defs["branch"]
        defs["url"] = str(defs["url"])
        if defs not in config.models_config["sources"]:
            config.models_config["sources"].append(defs)

    config.refresh_manifest()
    config.save_models(config.models_config)
    return {}


def models_update(group_info: ModelGroup):
    """Update git checkout for group described by group_info. Group must already be present."""
    src = None
    for s in config.models_config["sources"]:
        if s["owner"] == group_info.owner and s["group"] == group_info.group:
            src = s
            break

    groupdir = config.CACHE / ".models" / group_info.owner / group_info.group
    if not groupdir.exists() or not src:
        raise HTTPException(status_code=400, detail="Invalid group")

    try:
        subprocess.run(["git", "fetch"], cwd=groupdir, check=True)
        if group_info.rev:
            subprocess.run(
                ["git", "checkout", group_info.rev], cwd=groupdir, check=True
            )
        ref = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=groupdir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if ref != "HEAD":  # not detached
            if ref != src.get("branch"):
                src["branch"] = ref
            command = ["git", "pull", "--rebase"]
            if subprocess.run(command, cwd=groupdir, check=False).returncode:
                raise HTTPException(
                    status_code=409,
                    detail="Could not integrate remote changes. Are there local modifications?",
                )
        new = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=groupdir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if new != src["rev"]:
        src["rev"] = new
        config.refresh_manifest()
        config.save_models(config.models_config)

    return {"rev": new}


def models_remove(group_info: ModelGroup):
    """Remove ALL local files related to group described by group_info."""
    ownerdir = config.CACHE / ".models" / group_info.owner
    shutil.rmtree(ownerdir / group_info.group)
    if len(list(ownerdir.iterdir())) == 0:
        ownerdir.rmdir()

    for src in config.models_config["sources"]:
        if src["owner"] == group_info.owner and src["group"] == group_info.group:
            config.models_config["sources"].remove(src)
            for model in src["models"]:
                model_id = f"{src['sid']}-{model}"
                if (config.CACHE / model_id).exists():
                    shutil.rmtree(config.CACHE / model_id)
                for vals in config.models_config["tools"].values():
                    if model_id in vals:
                        del vals[model_id]
            config.refresh_manifest()
            config.save_models(config.models_config)


def tools_list():
    """List models which can be used for smart annotation in Label Studio."""
    return config.models_config["tools"]


def active_models():
    """List running inference workers."""
    return dict(
        [
            info
            for task in tasks.tasks.values()
            if (info := task.get(TourStep.INFERENCE)) and task["process"] is not None
        ]
    )


def train(err, options: dict, model: str):
    if model not in config.model_manifest:
        raise err(f"Model '{model}' does not exist", http_status=404)

    flags = tasks.build_model_options(
        config.model_manifest[model]["options"], options.items()
    )
    pid = tasks.start_task(
        ["uv", "run", "train.py"] + flags,
        config.model_manifest[model]["dir"],
        f"Model training: `{model}`",
        {
            "UV_PROJECT_ENVIRONMENT": config.CACHE / model / ".venv",
            "INSTALL_DIR": config.CACHE / model,
        },
    )
    tasks.tasks[pid][TourStep.TRAINING] = flags
    return pid


def infer(err, options: dict, model: str, alias: str | None = None):
    if model not in config.model_manifest:
        raise err(f"Model '{model}' does not exist", http_status=404)

    if not alias:
        alias = options.get("alias")

    for task in tasks.tasks.values():
        if (
            task["process"]
            and (info := task.get(TourStep.INFERENCE))
            and info[0] == (alias or model)
        ):
            raise err(f"Worker with alias '{alias or model}' already running")

    return tasks.create_inference_worker(model, options, alias)


def install(err, model: str):
    if model not in config.model_manifest:
        raise err(f"Model '{model}' does not exist", http_status=404)

    model_dir = config.model_manifest[model]["dir"]
    if (config.CACHE / model).exists():
        raise err(f"Model '{model}' already exists or installation is in progress")

    if model not in config.models_config["added"]:
        config.models_config["added"].append(model)
    config.save_models(config.models_config)
    return tasks.start_task(
        ["bash", "-c", f"./setup.sh && echo \"Finished installing '{model}'\""],
        model_dir,
        f"Installing model: `{model}`",
        extra_env={"INSTALL_DIR": str(config.CACHE / model)},
    )


def uninstall(err, model: str):
    if model not in config.model_manifest:
        raise err(f"Model '{model}' does not exist", http_status=404)

    install_dir = config.CACHE / model
    if not install_dir.exists():
        raise err(f"Model '{model}' is not installed")

    shutil.rmtree(install_dir)
