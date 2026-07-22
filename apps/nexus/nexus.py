from fastapi import FastAPI, Header, Request, HTTPException, Form
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Annotated, List
from pydantic import BaseModel, AnyHttpUrl
import asyncio

import time
import json
import os
from pathlib import Path
import subprocess
from subprocess import Popen, PIPE, STDOUT
import shutil
import re
from enum import Enum
from datetime import datetime

import mlflow
mlflow.set_tracking_uri("http://localhost:8081")
ARTIFACTS = os.getenv("MLFLOW_ARTIFACTS_DESTINATION")
CACHE = Path(os.environ["TOOLBOX_CACHE"])
DATA = Path(os.environ["TOOLBOX_DATA"])

DATA.mkdir(exist_ok=True)
models_file = DATA / "models.json"

def save_models(config):
    with models_file.open("w") as f:
        json.dump(config, f)
try:
    models_config = json.loads(models_file.read_text())
    if type(models_config) != dict:
        print("Invalid models.json, overwriting")
        raise ValueError("models.json not an object")
except:
    models_config = { "added": [], "sources": [] }
    save_models(models_config)

model_manifest = {}
def refresh_manifest():
    model_manifest.clear()
    sources = {}
    did_change = False
    for src in models_config["sources"]:
        repo = CACHE / ".models" / src["owner"] / src["group"]
        if not repo.exists():
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", src["url"], str(repo)], check=True)
            subprocess.run(["git", "sparse-checkout", "init", "--cone"], cwd=repo, check=True)
            src["rev"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

        for name in src["models"]:
            model_path = repo / name
            if not model_path.exists():
                subprocess.run(["git", "sparse-checkout", "add", name], cwd=repo, check=True)
                subprocess.run(["git", "checkout"], cwd=repo, check=True)

            if name not in models_config["added"] and (CACHE / name).exists():
                models_config["added"].append(name)
                did_change = True

            manifest = model_path / "model.json"
            model = {}

            if manifest.exists():
                manifest = json.loads(manifest.read_text())
                model["options"] = manifest["properties"]
                model["title"] = manifest["title"]
                model["description"] = manifest["description"]
                model["dir"] = model_path
                with open(model_path / "ui.html") as f:
                    model["form"] = f.read()

            if model != {}:
                model_manifest[name] = model
            else:
                src["models"].remove(name)
                did_change = True
    if did_change:
        save_models(models_config)
refresh_manifest()

tasks = {}

class TourStep(Enum):
    START = 0
    MODEL_SELECTION = 1
    DATASET = 2
    LABELING = 3
    EXPORT = 4
    TRAINING = 5
    MONITORING = 6
    INFERENCE = 7

TOUR_STEPS = [
    (TourStep.START.value, "Start", "index"),
    (TourStep.MODEL_SELECTION.value, "Models", "models"),
    (TourStep.DATASET.value, "Dataset", "dataset"),
    (TourStep.LABELING.value, "Labeling", "label"),
    (TourStep.EXPORT.value, "Export", "export"),
    (TourStep.TRAINING.value, "Training", "model"),
    (TourStep.MONITORING.value, "Monitoring", "dashboard"),
    (TourStep.INFERENCE.value, "Inference", "model"),
]

# make sure virutal env doesn't bleed into subprocesses
if "VIRTUAL_ENV" in os.environ:
    del os.environ["VIRTUAL_ENV"]

def start_task(command, cwd, description, extra_env={}, blocking=False):
    proc = Popen(command, cwd = cwd, stdout = PIPE, stderr = STDOUT, text = True, env={ **os.environ, **extra_env })
    tasks[proc.pid] = dict(description=description, output=[], run_info=None, process=proc, code=None, start_time=datetime.now())
    os.set_blocking(proc.stdout.fileno(), blocking)
    return proc.pid

def build_model_options(options, values):
    flags = []
    for k, v in values:
        if v != "" and k in options:
            if v.startswith("mlflow-artifacts:") and ARTIFACTS:
                v = v.replace("mlflow-artifacts:", ARTIFACTS, count=1)
            flags.extend(["--" + k, v])
    return flags

def create_inference_worker(model, options):
    flags = build_model_options(model_manifest[model]["options"], options)

    port = 9091
    used = [task["inference"][0] for task in tasks.values() if task["process"] and "inference" in task]
    while True:
        if port not in used:
            break
        port += 1

    pid = start_task(
        ["uv", "run", "gunicorn", "--bind", f":{port}", "infer:app", "--"] + flags,
        model_manifest[model]["dir"],
        f"Inference service worker for: `{model}`",
        { "VIRTUAL_ENV": CACHE / model / ".venv" }
    )
    tasks[pid]["inference"] = (port, model)
    return pid

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def propagate(kwargs):
    r = {}
    try:
        r["tour"] = int(kwargs["tour"])
        for attr in ["model", "project", "experiment"]:
            if attr in kwargs:
                r[attr] = kwargs[attr]
    except:
        pass
    return r

brand_name_long = os.getenv("TOOLBOX_BRAND_NAME_LONG", "ViCoS Toolbox")
brand_name_short = os.getenv("TOOLBOX_BRAND_NAME_SHORT", "ViCoS")
templates.env.globals.update(dict(tour_steps=TOUR_STEPS, tour_enum=TourStep, brand_name_long=brand_name_long, brand_name_short=brand_name_short))

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    params = propagate(request.query_params)
    if "tour" not in params:
        params["tour"] = TourStep.START.value
    return templates.TemplateResponse(request=request, name="index.html", context=dict(params=params))

@app.get("/label", response_class=HTMLResponse)
def label(request: Request, project: str | None = None):
    page = f"/projects/{int(project)}" if project else ""
    return templates.TemplateResponse(request=request, name="label-studio.html", context=dict(page=page, params=propagate(request.query_params)))

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, experiment: int | None = None, run: str | None = None):
    page = "/#/experiments"
    if experiment:
        page += f"/{experiment}"
        if run:
            page += f"/runs/{run}"
    return templates.TemplateResponse(request=request, name="mlflow.html", context=dict(page=page, params=propagate(request.query_params)))

# regex for grouping log entries
# if line starts with eval:, visualise: or (for example) 10/300: (from something like a tqdm batch counter)
# consecutive log lines get grouped up (most recent one is shown)
tqdm_header = re.compile(r"^(\d+)/(\d+):|^(eval):|^(visualise):")
mlflow_info = re.compile(r"^Experiment (\d+): Run ([a-f0-9]+)$")

def refresh_logs(task):
    if proc := task.get("process"):
        out = task["output"]

        m = tqdm_header.match(out[-1]) if len(out) else None
        last = m.groups() if m else None

        while line := proc.stdout.readline():
            line = line.strip()
            if len(line) == 0:
                continue
            if m := tqdm_header.match(line):
                gs = m.groups()
                if gs == last:
                    out[-1] = line
                else:
                    out.append(line)
                last = gs
            else:
                if m := mlflow_info.match(line):
                    task["run_info"] = m.groups()
                out.append(line)
                last = None

        if (code := proc.poll()) is not None:
            task["code"] = code
            task["end_time"] = datetime.now()
            task["process"] = None

# debug mode is single-threaded, which makes it hang
if not app.debug:
    from threading import Thread

    def monitor_task():
        while True:
            time.sleep(1)
            for task in tasks.values():
                if task["process"] and not os.get_blocking(task["process"].stdout.fileno()):
                    refresh_logs(task)

    # this is needed because task's writes will start blocking if output is not consumed
    Thread(target=monitor_task, daemon=True).start()

@app.get("/models", response_class=HTMLResponse)
def models(request: Request):
    groups = {}
    installed = []
    for group in models_config["sources"]:
        installed = []
        available = {}
        for m in group["models"]:
            if (CACHE / m).exists():
                installed.append(m)
            available[m] = model_manifest[m]
        if rev := group.get("rev"): # make sure we can manage models even if rev is borked
            rev = rev[:7]
        groups[(group["group"], group["owner"])] = rev, installed, available
    return templates.TemplateResponse(request=request, name="models.html", context=dict(groups=groups, installed=installed, params=propagate(request.query_params)))

class ModelGroup(BaseModel):
    owner: str
    group: str

@app.post("/models/update")
def models_update(group_info: ModelGroup):
    src = None
    for s in models_config["sources"]:
        if s["owner"] == group_info.owner and s["group"] == group_info.group:
            src = s
            break

    groupdir = CACHE / ".models" / group_info.owner / group_info.group
    if not groupdir.exists() or not src:
        return { "error": "Invalid group" }, 400
    
    subprocess.run(["git", "fetch"], cwd=groupdir, check=True)
    subprocess.run(["git", "checkout", data.get("rev", "origin/HEAD")], cwd=groupdir, check=True)
    new = subprocess.run(["git", "rev-parse", "HEAD"], cwd=groupdir, capture_output=True, text=True, check=True).stdout.strip()

    if new != src["rev"]:
        src["rev"] = new
        refresh_manifest()
        save_models(models_config)

    return { "rev": new }, 200

@app.post("/models/remove")
def models_remove(group_info: ModelGroup):
    ownerdir = CACHE / ".models" / group_info.owner
    shutil.rmtree(ownerdir / group, ignore_errors=True)
    if len(list(ownerdir.iterdir())) == 0:
        ownerdir.rmdir()

    old = len(models_config["sources"])
    models_config["sources"] = [x for x in models_config["sources"] if x["owner"] != group_info.owner or x["group"] != group_info.group]
    if len(models_config["sources"]) != old:
        refresh_manifest()
        save_models(models_config)
    return {}, 200

class ModelGroupDefinition(ModelGroup):
    models: List[str]
    url: AnyHttpUrl
@app.post("/models/add")
def models_add(data: List[ModelGroupDefinition]):
    for defs in data:
        if defs not in models_config["sources"]:
            models_config["sources"].append(defs)

    refresh_manifest()
    save_models(models_config)
    return {}, 200

dataset_dir = Path(os.environ["LOCAL_FILES_DOCUMENT_ROOT"])
@app.get("/datasets/{path:path}")
def datasets(path: str):
    new = dataset_dir / path
    if new.is_relative_to(dataset_dir) and new.exists():
        return [str(x.name) for x in new.iterdir() if x.is_dir()]
    
    raise HTTPException(status_code=404, detail="Invalid path")

@app.get("/models/{model}", response_class=HTMLResponse)
def model(request: Request, model: str):
    if model not in model_manifest or not (CACHE / model).exists():
        return RedirectResponse(request.url_for("models", **propagate(request.query_params)))

    return templates.TemplateResponse(request=request, name="model.html", context=dict(
        **model_manifest[model],
        train=(model_manifest[model]["dir"] / "train.py").exists(),
        params={ **propagate(request.query_params), "model": model }
    ))

@app.get("/model/{model}/options")
def model_options(model: str):
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    runs = mlflow.search_runs(experiment_names=[model_info["title"]], max_results=100, output_format="list")
    completions = {}
    if len(runs) > 0 and ARTIFACTS:
        for k, v in model_manifest[model]["options"].items():
            if format := v.get("format"):
                if format.startswith("file:"):
                    _completions = []
                    for run in runs:
                        files = list(Path(ARTIFACTS).glob(f"{run.info.experiment_id}/{run.info.run_id}/artifacts/**/{format[5:]}"))
                        if len(files) > 0:
                            _completions.append((f"{datetime.fromtimestamp(run.info.start_time / 1000).strftime('%Y-%m-%d %H-%M')} :: {run.info.run_name}", [(file.name, f"mlflow-artifacts:/{file.relative_to(ARTIFACTS)}") for file in files]))
                    if len(_completions) > 0:
                        completions[k] = _completions

    return dict(options=model_info.get("options", {}), completions=completions)

class TaskResponse(BaseModel):
    pid: int
    logs: str

@app.post("/model/{model}/infer")
async def model_infer(request: Request, model: str):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    options = await request.json()
    params = propagate(request.query_params)
    params["model"] = model
    params["pid"] = create_inference_worker(model, options.items())

    if params.get("tour") == TourStep.MONITORING.value:
        params["tour"] = TourStep.INFERENCE.value
    return TaskResponse(pid=params["pid"], logs=str(url_for_query(request, "logs", **params)))

@app.post("/model/{model}/train")
async def model_train(request: Request, model: str):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    options = await request.json()
    flags = build_model_options(model_manifest[model]["options"], options.items())
    pid = start_task(
        ["uv", "run", "train.py"] + flags,
        model_manifest[model]["dir"],
        f"Model training: `{model}`",
        { "VIRTUAL_ENV": CACHE / model / ".venv" }
    )

    params = propagate(request.query_params)
    params["model"] = model
    params["pid"] = pid
    # skip labeling steps
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.TRAINING.value
    return TaskResponse(pid=pid, logs=str(url_for_query(request, "logs", **params)))

@app.post("/model/{model}/install")
def model_install(model: int):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    params = propagate(request.query_params)
    model_dir = model_manifest[model]["dir"]
    if (CACHE / model).exists():
        params["model"] = model
        return RedirectResponse(request.url_for("model"))

    models_config["added"].append(model)
    save_models(models_config)

    params["model"] = model
    params["pid"] = start_task(["bash", "-c", f"./setup.sh && echo \"Finished installing '{model}'\""], model_dir, f"Installing model: `{model}`")
    return RedirectResponse(request.url_for("logs"))

@app.post("/model/{model}/uninstall")
def model_uninstall(model: int):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    install_dir = CACHE / model
    if not install_dir.exists():
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not installed")

    shutil.rmtree(install_dir)
    return {}

@app.get("/active")
def active_models():
    return dict([task["inference"] for task in tasks.values() if "inference" in task and task["process"] is not None])

@app.get("/dataset", response_class=HTMLResponse)
def dataset_get(request: Request, model: str):
    if model not in model_manifest or not (CACHE / model).exists():
        return RedirectResponse(request.url_for("models"))

    return templates.TemplateResponse(request=request, name="dataset.html", context=dict(**model_manifest[model], params=dict(request.query_params)))

class DatasetCreation(BaseModel):
    dataset: str | None
    title: str | None

@app.post("/dataset", response_class=HTMLResponse)
def dataset(request: Request, data: DatasetCreation, model: str):
    params = propagate(request.query_params);
    if model not in model_manifest or not (CACHE / model).exists():
        raise HTTPException(status_code=404, detail="Model is not installed")

    env = { "MODEL_DIR": model_manifest[model]["dir"] }

    if data.dataset:
        env["DATASET"] = data.dataset
    if data.title:
        env["PROJECT_TITLE"] = data.title

    task = tasks[start_task(["uv", "run", "create.py"], "../ls-utils", f"Project creation", extra_env=env, blocking=True)]
    id = None
    while line_in := task["process"].stdout.readline():
        task["output"].append(line_in)
        try:
            id = int(line_in)
            break
        except:
            pass
    os.set_blocking(task["process"].stdout.fileno(), False)

    if id is None:
        raise HTTPException(status_code=400, detail="Project could not be created")

    params = propagate(request.query_params)
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.LABELING.value
    params["project"] = id
    return RedirectResponse(request.url_for("label", model=model))
        

@app.get("/export", response_class=HTMLResponse)
def export_get(request: Request):
    return templates.TemplateResponse(request=request, name="export.html", context=dict(params=propagate(request.query_params)))

@app.post("/export", response_class=HTMLResponse)
def export(request: Request, task: Annotated[str, Form()], project: Annotated[str, Form()], dir: Annotated[str | None, Form()] = None):
    env = dict(TASK=task, PROJECT_ID=project)

    if dir:
        env["EXPORT_DIR"] = dir

    start_task(["uv", "run", "export.py"], "../ls-utils", f"Export worker for project {env['PROJECT_ID']}", extra_env=env)

    return templates.TemplateResponse(request=request, name="export.html", context=dict(params=propagate(request.query_params)))

@app.get("/task/status", response_class=HTMLResponse)
def status(request: Request):
    now = datetime.now()
    timestamps = { pid: now - task.get("end_time", task["start_time"]) for pid, task in tasks.items() }
    return templates.TemplateResponse(request=request, name="status.html", context=dict(tasks=tasks, timestamps=timestamps, params=propagate(request.query_params)))

@app.get("/task/logs/{pid}/stream", response_class=EventSourceResponse)
async def logs_stream(pid: int, last_event_id: Annotated[int | None, Header()] = None):
    if not (task := tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    out = task["output"]
    i = 0 if last_event_id is None else last_event_id + 1
    old = None
    while True:
        if i < len(out) - 1:
            i += 1
            yield ServerSentEvent(data=out[i - 1], event="newline", id=str(i))
            continue
        elif i < len(out):
            if out[i] != old:
                old = out[i]
                yield ServerSentEvent(data=old)
            elif task["process"] is None:
                break
        await asyncio.sleep(1)
    yield ServerSentEvent(data="end of logs", event="eof")

@app.get("/task/logs/{pid}", response_class=HTMLResponse)
def logs(request: Request, pid: int):
    if not (task := tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    if app.debug: # in production mode this gets refreshed in a thread
        refresh_logs(tasks[pid])

    params = propagate(request.query_params)
    params["pid"] = pid

    override = { **params, "tour": TourStep.MONITORING.value } if params.get("tour") == TourStep.TRAINING.value else params
    if info := task.get("run_info"):
        override["experiment"], override["run"] = info
        run_url = request.url_for("dashboard", **override)
    else:
        run_url = None

    return templates.TemplateResponse(request=request, name="logs.html", context=dict(pid=pid, description=task["description"], running=task["code"] is None, run_shortcut=run_url, params=params))

@app.post("/task/stop/{pid}")
def kill(request: Request, pid: int):
    if not (task := tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    if task["process"] is not None:
        task["process"].terminate()
    params = propagate(request.query_params)
    params["pid"] = pid
    return RedirectResponse(request.url_for("logs"))

from starlette.routing import Route
routes = { r.name: set(r.param_convertors.keys()) for r in app.router.routes if isinstance(r, Route) }
def url_for_query(request, route, **params):
    path, query = {}, {}
    path_params = routes.get(route, {})
    for k, v in params.items():
        if k in path_params:
            path[k] = v
        else:
            query[k] = v
    return request.url_for(route, **path).include_query_params(**query)
templates.env.globals["url_for_query"] = url_for_query
