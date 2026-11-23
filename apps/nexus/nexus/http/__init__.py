import json
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .. import config, logic, tasks
from . import dataset, finish, model, task
from .common import *

PRESETS = os.environ["TOOLBOX_PRESETS"]
if os.path.exists(PRESETS):
    with open(PRESETS) as f:
        PRESETS = json.load(f)
else:
    try:
        PRESETS = json.loads(PRESETS)
    except json.JSONDecodeError:
        print("Using empty presets because PRESETS didn't exist or was not valid json")
        PRESETS = {}


router = APIRouter()
router.mount("/dataset", dataset.router)
router.mount("/finish", finish.router)
router.mount("/model", model.router)
router.mount("/task", task.router)
router.post("/models/add")(logic.models_add)
router.post("/models/update")(logic.models_update)
router.post("/models/remove")(logic.models_remove)
router.get("/tools")(logic.tools_list)
router.get("/active")(logic.active_models)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"presets": PRESETS},
    )


@router.get("/label", response_class=HTMLResponse)
def label(request: Request, project: str | None = None):
    page = f"/projects/{int(project)}" if project else ""
    return templates.TemplateResponse(
        request=request,
        name="label-studio.html",
        context={"page": page},
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, experiment: int | None = None, run: str | None = None):
    page = "/#/experiments"
    if experiment:
        page += f"/{experiment}"
        if run:
            page += f"/runs/{run}"
    return templates.TemplateResponse(
        request=request,
        name="mlflow.html",
        context={"page": page},
    )


@router.get("/models", response_class=HTMLResponse)
def models(request: Request):
    groups = {}
    for group in config.models_config["sources"]:
        installed = []
        available = {}
        for m in group["models"]:
            model_id = f"{group['sid']}-{m}"
            if (config.CACHE / model_id).exists():
                installed.append(model_id)
            if info := config.model_manifest.get(model_id):
                available[model_id] = info
        if rev := group.get(
            "rev"
        ):  # make sure we can manage models even if rev is borked
            rev = rev[:7]
        groups[(group["group"], group["owner"])] = rev, installed, available
    return templates.TemplateResponse(
        request=request,
        name="models.html",
        context={"tags": config.tags, "groups": groups},
    )


@router.patch("/tools/{tool}")
def tool_enable(request: Request, tool: str, enable: bool, category: str):
    if not (tools := config.models_config["tools"].get(category)):
        raise HTTPException(
            status_code=404, detail=f"Category '{category}' does not exist"
        )
    if not (info := tools.get(tool)):
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{tool}' doesn't exist in category '{category}'",
        )
    info.enabled = enable
    config.save_models(config.models_config)


@router.post("/tools")
def tool_add(pid: int, name: str):
    if not (task := tasks.tasks.get(pid)) or not (
        worker := task.get(TourStep.INFERENCE)
    ):
        raise HTTPException(status_code=404, detail="Invalid task")

    alias, info = worker
    if not (supported := config.model_manifest[info.model]["tool"]["supported"]):
        raise HTTPException(
            status_code=404, detail=f"Model '{info.model}' cannot be used as tool"
        )

    for category in supported:
        if (items := config.models_config["tools"].get(category)) and alias in items:
            raise HTTPException(
                status_code=404,
                detail=f"Tool with alias '{alias}' already exists in category '{category}'",
            )

    for category in supported:
        if category not in config.models_config["tools"]:
            config.models_config["tools"][category] = {}
        config.models_config["tools"][category][alias] = config.ToolInfo(
            name=name, enabled=True, model=info.model, options=info.options
        )
    config.save_models(config.models_config)
    config.refresh_manifest()


@router.delete("/tools/{tool}")
def tool_remove(tool: str, category: str):
    if not (tools := config.models_config["tools"].get(category)) or not tool in tools:
        raise HTTPException(status_code=404, detail="Tool does not exist")
    if (info := config.model_manifest.get(tool)) and category in info["tool"][
        "default"
    ]:
        raise HTTPException(status_code=404, detail="Only custom tools can be deleted")
    del tools[tool]
    config.save_models(config.models_config)
    config.refresh_manifest()


@router.get("/datasets/{path:path}")
def datasets(path: str, files: bool | None = False):
    new = config.DATASET_DIR / path
    if new.is_relative_to(config.DATASET_DIR) and new.exists():
        if not files:
            try:
                # this is substantially faster and we've checked that new exists and is not malicious
                count = int(
                    subprocess.check_output(
                        ["bash", "-c", f"find {new} -type f | wc -l"],
                        text=True,
                    )
                )
            except ValueError:
                count = sum(x.is_file() for x in new.rglob("*"))
            files_ = None
        else:
            files_ = sorted([x for x in new.rglob("*") if x.is_file()])
            count = len(files_)

        if (new / "groups.json").exists():
            groups = json.loads((new / "groups.json").read_text())
        else:
            groups = None

        return {
            "root": config.DATASET_DIR,
            "dirs": [str(x.name) for x in new.iterdir() if x.is_dir()],
            "groups": groups,
            "count": count,
            "files": files_,
        }

    raise HTTPException(status_code=404, detail="Invalid path")


@router.get("/export", response_class=HTMLResponse)
def export_get(request: Request):
    now = datetime.now()
    old = {pid: task for pid, task in tasks.tasks.items() if TourStep.EXPORT in task}
    return templates.TemplateResponse(
        request=request,
        name="export.html",
        context={"now": now, "tasks": old},
    )


class ExportRequest(BaseModel):
    project: int
    dir: str | None = None
    combine: str | None = None


@router.post("/export")
def export(request: Request, model: str, export_request: ExportRequest):
    if not (model_info := config.model_manifest.get(model)):
        raise HTTPException(status_code=404, detail="Model does not exist")
    env = {"MODEL_FILES": model_info["dir"], "PROJECT_ID": str(export_request.project)}

    if export_request.dir:
        env["EXPORT_DIR"] = export_request.dir
    if export_request.combine:
        env["COMBINE"] = export_request.combine
    pid = tasks.start_task(
        ["uv", "run", "export.py"], "../ls-utils", "Export worker", extra_env=env
    )
    tasks.tasks[pid][TourStep.EXPORT] = export_request.project
    return {"pid": pid, "logs": str(request.state.url_for_query("logs", pid=pid))}


@router.get("/chat")
def chat(request: Request):
    return templates.TemplateResponse(request=request, name="chat.html")


@router.post("/chat")
async def chat_input(request: Request):
    for t in tasks.tasks.values():
        if "agent" in t and (agent := t["process"]):
            body = await request.body()
            agent.stdin.write(json.dumps(body.decode("utf-8")) + "\n")
            agent.stdin.flush()
            return
    raise HTTPException(status_code=404, detail="Agent is not running")


@router.get("/agent")
def agent(request: Request, flavor: str):
    old = next(
        (
            pid
            for pid, task in tasks.tasks.items()
            if "agent" in task and task["process"]
        ),
        None,
    )
    if old:
        if tasks.tasks[old]["agent"] != flavor:
            raise HTTPException(
                status_code=400, detail="Flavor does not match existing agent"
            )
        return RedirectResponse(request.state.url_for_query("logs_stream", pid=old))

    pid = tasks.start_task(["python", "agent.py", flavor], None, "Agent", stdin=True)
    tasks.tasks[pid]["agent"] = flavor
    return RedirectResponse(request.state.url_for_query("logs_stream", pid=pid))


@router.post("/agent")
def agent_stop():
    for t in tasks.tasks.values():
        if (proc := t["process"]) and "agent" in t:
            proc.terminate()
