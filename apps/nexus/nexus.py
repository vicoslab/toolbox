from fastapi import FastAPI, Header, Request, HTTPException, Form, File, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.encoders import jsonable_encoder
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from typing import Annotated, List, Optional, Dict
from pydantic import BaseModel, AnyHttpUrl, Field
import asyncio

import time
import requests
import io
import json
import os
import base64
import hashlib
from pathlib import Path
import subprocess
from subprocess import Popen, PIPE, STDOUT, CalledProcessError
import shutil
import re
from enum import Enum
from datetime import datetime
import tempfile
import zipfile

import mlflow
mlflow.set_tracking_uri("http://localhost:8081")
ARTIFACTS = os.getenv("MLFLOW_ARTIFACTS_DESTINATION")
CACHE = Path(os.environ["TOOLBOX_CACHE"])
DATA = Path(os.environ["TOOLBOX_DATA"])
DATASET_DIR = Path(os.environ["LOCAL_FILES_DOCUMENT_ROOT"])
API_KEY = os.environ["LABEL_STUDIO_API_KEY"]

PRESETS = os.environ["TOOLBOX_PRESETS"]
if os.path.exists(PRESETS):
    with open(PRESETS) as f:
        PRESETS = json.load(f)
else:
    try:
        PRESETS = json.loads(PRESETS)
    except json.JSONDecodeError:
        print(f"Using empty presets because PRESETS didn't exist or was not valid json")
        PRESETS = {}

DATA.mkdir(exist_ok=True)
models_file = DATA / "models.json"

class ToolInfo(BaseModel):
    name: str
    model: str
    enabled: bool
    options: dict

def save_models(config):
    with models_file.open("w") as f:
        json.dump(jsonable_encoder(config), f)
try:
    if models_file.exists():
        models_config = json.loads(models_file.read_text())
        models_config["added"] = list(set(models_config.get("added", [])))
        models_config["tools"] = { cat: { k: ToolInfo(**v) for k, v in items.items() } for cat, items in models_config.get("tools", {}).items() }
    elif models_str := os.getenv("TOOLBOX_MODELS"):
        models_config = { "added": [], "sources": json.loads(base64.b64decode(models_str)), "tools": {} }
        save_models(models_config)
    else:
        models_config = { "added": [], "sources": [], "tools": {} }
        save_models(models_config)
    if type(models_config) != dict:
        print("Invalid models.json, overwriting")
        raise ValueError("models.json not an object")
except Exception as e:
    print(f"Could not load models.json, using empty. (Error: {e})")
    if models_file.exists():
        old = models_file.with_suffix(models_file.suffix + ".bak")
        print(f"Saving old sources config to {str(old)}")
        models_file.rename(old)
    models_config = { "added": [], "sources": [], "tools": {} }
    save_models(models_config)

model_manifest = {}
tags = set()
def refresh_manifest():
    model_manifest.clear()
    did_change = False
    for src in models_config["sources"]:
        if "sid" not in src:
            src["sid"] = hashlib.sha256(f"{src["owner"]}/{src["group"]}".encode()).hexdigest()[:7]
            did_change = True
        repo = CACHE / ".models" / src["owner"] / src["group"]
        if not repo.exists():
            branch = ["--branch", src["branch"]] if "branch" in src else []
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", *branch, src["url"], str(repo)], check=True)
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

            model_id = f"{src["sid"]}-{name}"
            if manifest.exists():
                manifest = json.loads(manifest.read_text())
                model["options"] = manifest["properties"]
                model["title"] = manifest["title"]
                model["description"] = manifest["description"]
                model["dir"] = model_path
                model["group"] = src["group"]
                model["owner"] = src["owner"]
                model["train"] = (model_path / "train.py").exists()
                model["tags"] = manifest.get("x-toolbox-tags", [])
                # if tools are a list (old way), the model doesn't distinguish between default/supported tools
                # default tools are ones that are available if started without any options
                if type(tool := manifest.get("x-toolbox-tools", [])) == dict:
                    model["tool"] = tool
                else:
                    model["tool"] = { "supported": tool, "default": tool }
                # add model's tools into config
                for category in model["tool"]["default"]:
                    if category not in models_config["tools"]:
                        models_config["tools"][category] = {}
                    if model_id not in models_config["tools"][category]:
                        models_config["tools"][category][model_id] = ToolInfo(name=manifest["title"], enabled=Path(CACHE, model_id).exists(), options={}, model=model_id)
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
    FINISH = 8

TOUR_STEPS = [
    # id, title, endpoint, train_only
    (TourStep.START.value, "Start", "index", False),
    (TourStep.MODEL_SELECTION.value, "Models", "models", False),
    (TourStep.DATASET.value, "Dataset", "dataset_get", True),
    (TourStep.LABELING.value, "Labeling", "label", True),
    (TourStep.EXPORT.value, "Export", "export_get", True),
    (TourStep.TRAINING.value, "Training", "train", True),
    (TourStep.MONITORING.value, "Experiments", "dashboard",  True),
    (TourStep.INFERENCE.value, "Inference", "infer", False),
    (TourStep.FINISH.value, "Finish tour", "finish", False),
]

# make sure virutal env doesn't bleed into subprocesses
if "VIRTUAL_ENV" in os.environ:
    del os.environ["VIRTUAL_ENV"]

def start_task(command, cwd, description, extra_env={}, blocking=False):
    proc = Popen(command, cwd = cwd, stdout = PIPE, stderr = STDOUT, text = True, env={ **os.environ, **extra_env })
    tasks[proc.pid] = dict(description=description, output=[], info=[], process=proc, code=None, start_time=datetime.now())
    os.set_blocking(proc.stdout.fileno(), blocking)
    return proc.pid

class WorkerInfo(BaseModel):
    model: str
    port: int
    options: dict = Field(exclude=True)

def build_model_options(options, values):
    flags = []
    for k, v in values:
        if v != "" and k in options:
            if type(v) == str and v.startswith("mlflow-artifacts:") and ARTIFACTS:
                v = v.replace("mlflow-artifacts:", ARTIFACTS, count=1)
            if type(v) == list:
                flags.extend(["--" + k, *map(str, v)])
            else:
                flags.extend(["--" + k, str(v)])
    return flags

def create_inference_worker(model, values, alias=None):
    options = model_manifest[model]["options"]
    flags = build_model_options(options, values.items())

    port = 9091
    used = [worker[1].port for task in tasks.values() if task["process"] and (worker := task.get(TourStep.INFERENCE))]
    while True:
        if port not in used:
            break
        port += 1

    # --no-control-socket disables control-socket, which we aren't using, and logs errors in configurations with uid-only user
    pid = start_task(
        ["uv", "run", "gunicorn", "--bind", f":{port}", "--log-config", Path.cwd() / "worker-logging.conf", "--no-control-socket", "infer:app", "--", *flags],
        model_manifest[model]["dir"],
        f"Inference service worker for: `{model}`",
        { "VIRTUAL_ENV": CACHE / model / ".venv", "INSTALL_DIR": CACHE / model, "LABEL_STUDIO_URL": "http://localhost:8080", "XDG_DATA_HOME": "/tmp" }
    )
    options = { opt["title"]: v for k, v in values.items() if (opt := options.get(k)) and opt.get("stage") in [None, "infer"] }
    tasks[pid][TourStep.INFERENCE] = (alias or model, WorkerInfo(port=port, model=model, options=options))
    return pid

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def propagate(kwargs):
    r = {}
    try:
        for attr in ["model", "project", "experiment", "manifest"]:
            if attr in kwargs:
                r[attr] = kwargs[attr]
    except ValueError:
        pass
    return r

brand_name_long = os.environ["TOOLBOX_BRAND_NAME_LONG"]
brand_name_short = os.environ["TOOLBOX_BRAND_NAME_SHORT"]
templates.env.globals.update(dict(tour_steps=TOUR_STEPS, tour_enum=TourStep, brand_name_long=brand_name_long, brand_name_short=brand_name_short, model_manifest=model_manifest))

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context=dict(presets=PRESETS, params=propagate(request.query_params)))

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
quick_action = re.compile(r"^Toolbox:(\w+): (.+)$")

def refresh_logs(task):
    if proc := task.get("process"):
        out = task["output"]

        last = None
        if len(out) and type(out[-1]) == str and (m := tqdm_header.match(out[-1])):
            last = m.groups()

        while line := proc.stdout.readline():
            line = line.rstrip()
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
                if m := quick_action.match(line):
                    kind, data = m.groups()
                    if kind == "Info":
                        task["info"].append(json.loads(data))
                    out.append((kind, data))
                else:
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
    for group in models_config["sources"]:
        installed = []
        available = {}
        for m in group["models"]:
            model_id = f"{group["sid"]}-{m}"
            if (CACHE / model_id).exists():
                installed.append(model_id)
            if info := model_manifest.get(model_id):
                available[model_id] = info
        if rev := group.get("rev"): # make sure we can manage models even if rev is borked
            rev = rev[:7]
        groups[(group["group"], group["owner"])] = rev, installed, available
    return templates.TemplateResponse(request=request, name="models.html", context=dict(tags=tags, groups=groups, params=propagate(request.query_params)))

class ModelGroup(BaseModel):
    owner: str
    group: str
    rev: Optional[str] = None
    branch: Optional[str] = None

@app.post("/models/update")
def models_update(group_info: ModelGroup):
    src = None
    for s in models_config["sources"]:
        if s["owner"] == group_info.owner and s["group"] == group_info.group:
            src = s
            break

    groupdir = CACHE / ".models" / group_info.owner / group_info.group
    if not groupdir.exists() or not src:
        raise HTTPException(status_code=400, detail="Invalid group")
    
    try:
        subprocess.run(["git", "fetch"], cwd=groupdir, check=True)
        if group_info.rev:
            subprocess.run(["git", "checkout", group_info.rev], cwd=groupdir, check=True)
        ref = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=groupdir, capture_output=True, text=True, check=True).stdout.strip()
        if ref != "HEAD": # not detached
            if ref != src.get("branch"):
                src["branch"] = ref
            if subprocess.run(["git", "pull", "--rebase"], cwd=groupdir).returncode:
                raise HTTPException(status_code=409, detail="Could not integrate remote changes. Are there local modifications?")
        new = subprocess.run(["git", "rev-parse", "HEAD"], cwd=groupdir, capture_output=True, text=True, check=True).stdout.strip()
    except CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if new != src["rev"]:
        src["rev"] = new
        refresh_manifest()
        save_models(models_config)

    return { "rev": new }

@app.post("/models/remove")
def models_remove(group_info: ModelGroup):
    ownerdir = CACHE / ".models" / group_info.owner
    shutil.rmtree(ownerdir / group_info.group)
    if len(list(ownerdir.iterdir())) == 0:
        ownerdir.rmdir()

    for src in models_config["sources"]:
        if src["owner"] == group_info.owner and src["group"] == group_info.group:
            models_config["sources"].remove(src)
            for category, vals in models_config["tools"].items():
                for model in src["models"]:
                    if (model_id := f"{src["sid"]}-{model}") in vals:
                        del vals[model_id]
            refresh_manifest()
            save_models(models_config)

class ModelGroupDefinition(ModelGroup):
    models: List[str]
    url: AnyHttpUrl
@app.post("/models/add")
def models_add(data: List[ModelGroupDefinition]):
    for defs in data:
        defs = dict(defs)
        if defs["branch"] is None:
            del defs["branch"]
        defs["url"] = str(defs["url"])
        if defs not in models_config["sources"]:
            models_config["sources"].append(defs)

    refresh_manifest()
    save_models(models_config)
    return {}

@app.get("/datasets/{path:path}")
def datasets(path: str, files: Optional[bool]=False):
    new = DATASET_DIR / path
    if new.is_relative_to(DATASET_DIR) and new.exists():
        if not files:
            try:
                # this is substantially faster and we've checked that new exists and is not malicious
                count = int(subprocess.run(["bash", "-c", f"find {new} -type f | wc -l"], capture_output=True, text=True).stdout)
            except ValueError:
                count = sum((x.is_file() for x in new.glob("**/*")))
            files = None
        else:
            files = sorted([x for x in new.rglob("*") if x.is_file()])
            count = len(files)

        if (new / "groups.json").exists():
            groups = json.loads((new / "groups.json").read_text())
        else:
            groups = None

        return {
            "root": DATASET_DIR,
            "dirs": [str(x.name) for x in new.iterdir() if x.is_dir()],
            "groups": groups,
            "count": count,
            "files": files
        }
    
    raise HTTPException(status_code=404, detail="Invalid path")

def handle_model(template: str, request: Request, model: str, manifest: str | None = None, weights: str | None = None):
    params = propagate(request.query_params)
    if model not in model_manifest or not (CACHE / model).exists():
        return RedirectResponse(url_for_query(request, "models", params))

    params["model"] = model
    if manifest:
        params["manifest"] = manifest
    if weights:
        params["weights"] = weights

    # alias: (pid, model)
    workers = { inf[0]: (pid, inf[1]) for (pid, task) in tasks.items() if (inf := task.get(TourStep.INFERENCE)) and task["process"] is not None }
    now = datetime.now()
    runs = { pid: task for pid, task in tasks.items() if TourStep.TRAINING in task }

    sources = {}
    for src in models_config["sources"]:
        models = [id for m in src["models"] if Path(CACHE, id := f"{src["sid"]}-{m}").exists()]
        if models:
            sources[f"{src["group"]} by {src["owner"]}"] = models

    return templates.TemplateResponse(request=request, name=template, context=dict(
        sources=sources,
        now=now,
        runs=runs,
        workers=workers,
        params=params
    ))

@app.get("/tools")
def tools_list():
    return models_config["tools"]

@app.patch("/tools/{tool}")
def tool_enable(request: Request, tool: str, enable: bool, category: str):
    if not (tools := models_config["tools"].get(category)):
        raise HTTPException(status_code=404, detail=f"Category '{category}' does not exist")
    if not (info := tools.get(tool)):
        raise HTTPException(status_code=404, detail=f"Tool '{tool}' doesn't exist in category '{category}'")
    info.enabled = enable
    save_models(models_config)

@app.post("/tools")
def tool_add(pid: int, name: str):
    if not (task := tasks.get(pid)) or not (worker := task.get(TourStep.INFERENCE)):
        raise HTTPException(status_code=404, detail=f"Invalid task")

    alias, info = worker
    if not (supported := model_manifest[info.model]["tool"]["supported"]):
        raise HTTPException(status_code=404, detail=f"Model '{info.model}' cannot be used as tool")
    
    for category in supported:
        if (items := models_config["tools"].get(category)) and alias in items:
            raise HTTPException(status_code=404, detail=f"Tool with alias '{alias}' already exists in category '{category}'")

    for category in supported:
        if category not in models_config["tools"]:
            models_config["tools"][category] = {}
        models_config["tools"][category][alias] = ToolInfo(name=name, enabled=True, model=info.model, options=info.options)
    save_models(models_config)
    refresh_manifest()

@app.delete("/tools/{tool}")
def tool_remove(tool: str, category: str):
    if not (tools := models_config["tools"].get(category)) or not tool in tools:
        raise HTTPException(status_code=404, detail=f"Tool does not exist")
    if (info := model_manifest.get(tool)) and category in info["tool"]["default"]:
        raise HTTPException(status_code=404, detail=f"Only custom tools can be deleted")
    del tools[tool]
    save_models(models_config)
    refresh_manifest()

@app.get("/model/{model}/options")
def model_options(request: Request, model: str, manifest: Optional[str] = None, weights: Optional[str] = None):
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")
    options = model_info.get("options", {})

    runs = mlflow.search_runs(experiment_names=[model_info["title"]], max_results=100, output_format="list")
    completions = {}
    for k, v in options.items():
        if not (format := v.get("format")):
            continue
        if format == "file:manifest.json":
            manifests = {}
            for m in sorted(DATASET_DIR.rglob("manifest.json"), key=lambda x: str(x)):
                parent = m.parent
                if parent.name.startswith(".export"):
                    parent = parent.parent
                if parent not in manifests:
                    manifests[parent] = []
                manifests[parent].append((m.relative_to(parent), str(m)))
            if manifest or len(manifests) > 0:
                completions[k] = [manifest, list(manifests.items())]
        elif format.startswith("file:"):
            _completions = []
            if len(runs) > 0 and ARTIFACTS:
                for run in runs:
                    files = list(Path(ARTIFACTS).glob(f"{run.info.experiment_id}/{run.info.run_id}/artifacts/**/{format[5:]}"))
                    if len(files) > 0:
                        _completions.append((f"{datetime.fromtimestamp(run.info.start_time / 1000).strftime('%Y-%m-%d %H-%M')} :: {run.info.run_name}", [(file.name, f"mlflow-artifacts:/{file.relative_to(ARTIFACTS)}") for file in files]))
            if weights or len(_completions) > 0:
                completions[k] = [weights, _completions]

    alias = dict(title="Alias", description="Alias for inference worker", type="string")
    return dict(options={**options, "alias": alias}, completions=completions)

class TaskResponse(BaseModel):
    pid: int
    logs: str
    duplicate: bool=False

@app.get("/model/{model}/infer", response_class=HTMLResponse)
def infer(request: Request, model: str, manifest: str | None = None, weights: str | None = None):
    return handle_model("infer.html", request, model, manifest, weights)
@app.post("/model/{model}/infer")
async def model_infer(request: Request, model: str, alias: Optional[str]=None):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    options = await request.json()
    if not alias:
        alias = options.get("alias")

    for pid, task in tasks.items():
        if task["process"] and (info := task.get(TourStep.INFERENCE)) and info[0] == (alias or model):
            raise HTTPException(status_code=400, detail=f"Worker with alias '{alias or model}' already running")

    pid = create_inference_worker(model, options, alias)
    return TaskResponse(pid=pid, logs=str(url_for_query(request, "logs", propagate(request.query_params), model=model, pid=pid)))

@app.get("/model/{model}/train", response_class=HTMLResponse)
def train(request: Request, model: str, manifest: str | None = None, weights: str | None = None):
    return handle_model("train.html", request, model, manifest, weights)
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
        { "VIRTUAL_ENV": CACHE / model / ".venv", "INSTALL_DIR": CACHE / model }
    )
    tasks[pid][TourStep.TRAINING] = flags

    return TaskResponse(pid=pid, logs=str(url_for_query(request, "logs", propagate(request.query_params), model=model, pid=pid)))

@app.post("/model/{model}/install")
def model_install(request: Request, model: str):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    params = propagate(request.query_params)
    model_dir = model_manifest[model]["dir"]
    if (CACHE / model).exists():
        params["model"] = model
        raise HTTPException(status_code=400, detail=f"Model '{model}' already exists or installation is in progress")

    if model not in models_config["added"]:
        models_config["added"].append(model)
    save_models(models_config)

    params["model"] = model
    params["pid"] = start_task(["bash", "-c", f"./setup.sh && echo \"Finished installing '{model}'\""], model_dir, f"Installing model: `{model}`", extra_env={ "INSTALL_DIR": str(CACHE / model)})
    return { "pid": params["pid"], "logs": str(url_for_query(request, "logs", params)) }

@app.post("/model/{model}/uninstall")
def model_uninstall(model: str):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    install_dir = CACHE / model
    if not install_dir.exists():
        raise HTTPException(status_code=400, detail=f"Model '{model}' is not installed")

    shutil.rmtree(install_dir)
    return {}

@app.get("/active")
def active_models():
    return dict([info for task in tasks.values() if (info := task.get(TourStep.INFERENCE)) and task["process"] is not None])

@app.get("/dataset", response_class=HTMLResponse)
def dataset_get(request: Request, model: str):
    params = propagate(request.query_params)
    if model not in model_manifest or not (CACHE / model).exists():
        del params["model"]
        return RedirectResponse(url_for_query(request, "models", params))

    now = datetime.now()
    old = { pid: task for pid, task in tasks.items() if TourStep.DATASET in task }

    tools_installed = {}
    for category, vals in models_config["tools"].items():
        if not vals:
            continue

        tools_installed[category] = []
        for alias, info in vals.items():
            manifest = model_manifest[info.model]
            custom = alias not in model_manifest or category not in manifest["tool"]["default"]
            tools_installed[category].append((alias, manifest["group"], manifest["owner"], (CACHE / info.model).exists(), info, custom))

    return templates.TemplateResponse(request=request, name="dataset.html", context=dict(now=now, tasks=old, tools=tools_installed, **model_manifest[model], params=params))

@app.post("/dataset/external")
async def dataset_from_upload(request: Request, file: Annotated[UploadFile, File()]):
    name, ext = os.path.splitext(os.path.basename(file.filename))
    with tempfile.NamedTemporaryFile(suffix=ext, mode="wb") as f:
        while (chunk := await file.read(1024)):
            f.write(chunk)
        f.flush()

        with tempfile.TemporaryDirectory(delete=False) as d:
            shutil.unpack_archive(f.name, d)

            root = Path(d)
            if len(files := list(root.iterdir())) == 1:
                root = files[0]

            if (target := DATASET_DIR / root.name).exists():
                raise HTTPException(status_code=400, detail=f"Dataset {str(target.name)} already exists")
            elif (manifest := root / "manifest.json").exists():
                shutil.move(root, target)
                return { "manifest": str(target / "manifest.json") }
            else:
                raise HTTPException(status_code=400, detail=f"Uploaded archive doesn't contain a manifest")

async def receive_files(base_dir: Path, files: list[UploadFile]):
    for file in files:
        try:
            contents = await file.read()
            with (base_dir / file.filename).open("wb") as f:
                f.write(contents)
        except Exception:
            shutil.rmtree(base_dir)
            raise HTTPException(status_code=500, detail='Something went wrong')
        finally:
            await file.close()

class DatasetCreation(BaseModel):
    dataset: str
    title: Optional[str] = None
    group_size: int
    group_separation: str
    regex_include: str
    regex_exclude: str
    shared: bool = False
    files: Optional[list[UploadFile]] = []

@app.post("/dataset", response_class=HTMLResponse)
async def dataset(request: Request, data: Annotated[DatasetCreation, Form()], model: str):
    if model not in model_manifest or not (CACHE / model).exists():
        raise HTTPException(status_code=404, detail="Model is not installed")

    dataset = Path(data.dataset)
    if not dataset.is_relative_to(DATASET_DIR):
        raise HTTPException(status_code=400, detail=f"Cannot place dataset outside '{str(DATASET_DIR)}'")
    dataset.mkdir(exist_ok=True, parents=True)
    data.files = [f for f in data.files if f.size > 0]
    if len(data.files) > 0:
        if next(dataset.iterdir(), False):
            raise HTTPException(status_code=400, detail="Cannot create dataset from upload if directory already exists")
        await receive_files(dataset, data.files)

    data_dict = data.model_dump()
    data_dict["tools"] = { kind: [(alias, tool.name) for alias, tool in tools.items() if tool.enabled] for kind, tools in models_config["tools"].items() }
    del data_dict["files"]
    env = { "MODEL_DIR": model_manifest[model]["dir"], "CREATION_REQUEST": json.dumps(data_dict) }
    pid = start_task(["uv", "run", "create.py"], "../ls-utils", f"Project creation", extra_env=env, blocking=True)
    task = tasks[pid]
    task[TourStep.DATASET] = None
    id = None
    while line_in := task["process"].stdout.readline():
        task["output"].append(line_in)
        try:
            id = int(line_in)
            break
        except ValueError:
            pass
    os.set_blocking(task["process"].stdout.fileno(), False)

    if id is None:
        return RedirectResponse(url_for_query(request, "logs", propagate(request.query_params), pid=pid), status_code=303)
    task[TourStep.DATASET] = id

    return RedirectResponse(url_for_query(request, "label", propagate(request.query_params), project=id), status_code=303)

class DatasetAddition(BaseModel):
    dataset: str
    group_separation: str
    files: list[UploadFile]

@app.post("/dataset/upload", response_class=HTMLResponse)
async def dataset_upload(request: Request, data: Annotated[DatasetAddition, Form()], project: int):
    if not (base := Path(data.dataset)).exists():
        raise HTTPException(status_code=400, detail="Provided dataset does not exist")
    base = base / datetime.now().strftime("upload-%Y-%m-%d-%H-%M-%S")
    base.mkdir()
    await receive_files(base, data.files)
    addition = json.dumps({ "project": project, "upload_dir": str(base), "group_separation": data.group_separation })
    pid = start_task(["uv", "run", "add.py"], "../ls-utils", f"Adding tasks to project", extra_env={ "ADDITION_REQUEST": addition })
    tasks[pid][TourStep.DATASET] = project

    return RedirectResponse(url_for_query(request, "label", propagate(request.query_params)), status_code=303)

@app.get("/export", response_class=HTMLResponse)
def export_get(request: Request):
    now = datetime.now()
    old = { pid: task for pid, task in tasks.items() if TourStep.EXPORT in task }
    return templates.TemplateResponse(request=request, name="export.html", context=dict(now=now, tasks=old, params=propagate(request.query_params)))

class ExportRequest(BaseModel):
    project: int
    dir: str | None = None
    combine: str | None = None

@app.post("/export")
def export(request: Request, model: str, export_request: ExportRequest):
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail="Model does not exist")
    env = dict(MODEL_FILES=model_info["dir"], PROJECT_ID=str(export_request.project))

    if export_request.dir:
        env["EXPORT_DIR"] = export_request.dir
    if export_request.combine:
        env["COMBINE"] = export_request.combine
    params = propagate(request.query_params)
    params["pid"] = start_task(["uv", "run", "export.py"], "../ls-utils", f"Export worker", extra_env=env)
    tasks[params["pid"]][TourStep.EXPORT] = export_request.project
    return { "pid": params["pid"], "logs": str(url_for_query(request, "logs", params)) }

def file_tree(path: Path):
    tree = {}
    for root, dirs, files in path.walk():
        current = tree
        for part in root.relative_to(path).parts:
            current = current[part]
        for directory in dirs:
            current[directory] = {}
        for file in files:
            current[file] = None
    return tree

@app.get("/finish")
def finish(request: Request):
    return templates.TemplateResponse(request=request, name="finish.html", context=dict(params=propagate(request.query_params)))

@app.get("/finish/list")
def finish_list(request: Request, model: str, manifest: Optional[str]=None, project: Optional[int]=None):
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail="Model does not exist")
    if not manifest and not project:
        raise HTTPException(status_code=400, detail="You must provide either a project or a manifest")
    if manifest:
        if not (manifest := Path(manifest)).exists():
            raise HTTPException(status_code=404, detail="Manifest does not exist")
        root = manifest.parent
    if project:
        response = requests.get(
            f"http://localhost:8080/api/storages/localfiles",
            headers={ "Authorization": f"Token {API_KEY}" },
            params={ "project": project },
            timeout=30,
        )
        if response.status_code != 200:
            return Response(response.text, status_code=response.status_code)
        storages = response.json()
        if len(storages) == 0:
            raise HTTPException(status_code=404, detail="No import storages associated with project")
        root = Path(storages[0]["path"])

    if root.name.startswith(".export"):
        root = root.parent

    run_files = []
    experiment = None
    manifests = [str(x) for x in root.rglob("manifest.json")]
    runs = mlflow.search_runs(experiment_names=[model_info["title"]], max_results=100, output_format="list")
    for run in runs:
        params = run.data.params.values()
        if not any((m in params for m in manifests)):
            continue

        experiment = run.info.experiment_id
        path = Path(ARTIFACTS) / experiment / run.info.run_id / "artifacts"
        if len(tree := file_tree(path)) > 0:
            run_files.append((run.info.run_id, run.info.run_name, tree))
    
    return { "runs": run_files, "experiment": experiment, "dataset": file_tree(root), "dataset_root": str(root) }

def zip_response(dirs, filename):
    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for x in dirs:
                if x.is_dir():
                    for root, dirs, files in x.walk():
                        for f in files:
                            zip_file.write(root / f, (root / f).relative_to(x.parent))
                else:
                    zip_file.write(x, x.name)
            
        zip_buffer.seek(0)
        headers = {"Content-Disposition": "attachment; filename=" + filename}
        return Response(zip_buffer.getvalue(), headers=headers, media_type="application/zip")
    except Exception as e:
        raise HTTPException(detail='There was an error processing the data', status_code=400)
    finally:
        zip_buffer.close()

@app.get("/finish/download/dataset")
def finish_dataset(request: Request, path: str):
    if not (path := Path(path)).exists() or not path.is_relative_to(DATASET_DIR):
        raise HTTPException(status_code=400, detail="Invalid path")
    if path.is_file():
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)
    else:
        return zip_response([path], "dataset.zip")

class DownloadRuns(BaseModel):
    experiment: int
    runs: Dict[str, Optional[str]]
@app.post("/finish/download/runs")
def finish_runs(request: Request, data: DownloadRuns):
    paths = []
    for run, subpath in data.runs.items():
        path = Path(ARTIFACTS) / str(data.experiment) / run / "artifacts"
        if subpath:
            path = path / subpath
        if not path.exists():
            raise HTTPException(status_code=400, detail="Invalid path")
        paths.append(path)
    if len(paths) == 1 and paths[0].exists() and paths[0].is_file():
        return FileResponse(paths[0], media_type="application/octet-stream", filename=path.name)
    else:
        return zip_response(paths, "runs.zip")

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
            data = out[i - 1]
            yield ServerSentEvent(data=data, event="newline" if type(data) == str else "quick-action", id=str(i))
            continue
        elif i < len(out):
            if out[i] != old:
                old = out[i]
                if type(old) == str:
                    yield ServerSentEvent(data=old)
                else:
                    yield ServerSentEvent(data=old, event="quick-action")
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

    return templates.TemplateResponse(request=request, name="logs.html", context=dict(pid=pid, description=task["description"], info=task["info"], running=task["code"] is None, params=params))

@app.post("/task/stop/{pid}")
def kill(request: Request, pid: int):
    if not (task := tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    if task["process"] is not None:
        task["process"].terminate()
    params = propagate(request.query_params)
    params["pid"] = pid
    return RedirectResponse(url_for_query(request, "logs", params), status_code=303) # 303 changes to GET

from starlette.routing import Route
routes = { r.name: set(r.param_convertors.keys()) for r in app.router.routes if isinstance(r, Route) }
def url_for_query(request, route, params, **overrides):
    path, query = {}, {}
    path_params = routes.get(route, {})
    for k, v in { **params, **overrides }.items():
        if k in path_params:
            path[k] = v
        else:
            query[k] = v
    return request.url_for(route, **path).include_query_params(**query)
templates.env.globals["url_for_query"] = url_for_query
