from fastapi import FastAPI, Header, Request, HTTPException, Form, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from typing import Annotated, List, Optional, Dict
from pydantic import BaseModel, AnyHttpUrl
import asyncio

import time
import io
import json
import os
import base64
from pathlib import Path
import subprocess
from subprocess import Popen, PIPE, STDOUT
import shutil
import re
from enum import Enum
from datetime import datetime
import zipfile

import mlflow
mlflow.set_tracking_uri("http://localhost:8081")
ARTIFACTS = os.getenv("MLFLOW_ARTIFACTS_DESTINATION")
CACHE = Path(os.environ["TOOLBOX_CACHE"])
DATA = Path(os.environ["TOOLBOX_DATA"])
DATASET_DIR = Path(os.environ["LOCAL_FILES_DOCUMENT_ROOT"])

DATA.mkdir(exist_ok=True)
models_file = DATA / "models.json"

def save_models(config):
    with models_file.open("w") as f:
        json.dump(config, f)
try:
    if models_file.exists():
        models_config = json.loads(models_file.read_text())
    elif models_str := os.getenv("TOOLBOX_MODELS"):
        models_config = { "added": [], "sources": json.loads(base64.b64decode(models_str)) }
        save_models(models_config)
    else:
        models_config = { "added": [], "sources": [] }
        save_models(models_config)
    if type(models_config) != dict:
        print("Invalid models.json, overwriting")
        raise ValueError("models.json not an object")
except Exception as e:
    print(f"Could not load models.json, using empty. (Error: {e})")
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
                model["train"] = (model_path / "train.py").exists()
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
    FINISH = 8

TOUR_STEPS = [
    # id, title, endpoint, train_only
    (TourStep.START.value, "Start", "index", False),
    (TourStep.MODEL_SELECTION.value, "Models", "models", False),
    (TourStep.DATASET.value, "Dataset", "dataset", True),
    (TourStep.LABELING.value, "Labeling", "label", True),
    (TourStep.EXPORT.value, "Export", "export", True),
    (TourStep.TRAINING.value, "Training", "model", True),
    (TourStep.MONITORING.value, "Experiments", "dashboard",  True),
    (TourStep.INFERENCE.value, "Inference", "model", False),
    (TourStep.FINISH.value, "Finish tour", "finish", False),
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

def create_inference_worker(model, options, alias=None):
    flags = build_model_options(model_manifest[model]["options"], options)

    port = 9091
    used = [task[TourStep.INFERENCE][1][0] for task in tasks.values() if task["process"] and TourStep.INFERENCE in task]
    while True:
        if port not in used:
            break
        port += 1

    pid = start_task(
        ["uv", "run", "gunicorn", "--bind", f":{port}", "--log-config", Path.cwd() / "worker-logging.conf", "infer:app", "--"] + flags,
        model_manifest[model]["dir"],
        f"Inference service worker for: `{model}`",
        { "VIRTUAL_ENV": CACHE / model / ".venv" }
    )
    tasks[pid][TourStep.INFERENCE] = (alias or model, (port, model))
    return pid

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def propagate(kwargs):
    r = {}
    try:
        r["tour"] = int(kwargs["tour"])
        for attr in ["model", "project", "experiment", "manifest"]:
            if attr in kwargs:
                r[attr] = kwargs[attr]
    except:
        pass
    return r

brand_name_long = os.environ["TOOLBOX_BRAND_NAME_LONG"]
brand_name_short = os.environ["TOOLBOX_BRAND_NAME_SHORT"]
templates.env.globals.update(dict(tour_steps=TOUR_STEPS, tour_enum=TourStep, brand_name_long=brand_name_long, brand_name_short=brand_name_short, model_manifest=model_manifest))

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
quick_action = re.compile(r"^(\w+): (\S+)$")

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
                if m := quick_action.match(line):
                    out.append(m.groups())
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
    rev: str = "origin/HEAD"

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
    subprocess.run(["git", "checkout", group_info.rev], cwd=groupdir, check=True)
    new = subprocess.run(["git", "rev-parse", "HEAD"], cwd=groupdir, capture_output=True, text=True, check=True).stdout.strip()

    if new != src["rev"]:
        src["rev"] = new
        refresh_manifest()
        save_models(models_config)

    return { "rev": new }, 200

@app.post("/models/remove")
def models_remove(group_info: ModelGroup):
    ownerdir = CACHE / ".models" / group_info.owner
    shutil.rmtree(ownerdir / group_info.group, ignore_errors=True)
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
        defs = dict(defs)
        defs["url"] = str(defs["url"])
        if defs not in models_config["sources"]:
            models_config["sources"].append(defs)

    refresh_manifest()
    save_models(models_config)
    return {}, 200

@app.get("/datasets/{path:path}")
def datasets(path: str, files: Optional[bool]=False):
    new = DATASET_DIR / path
    if new.is_relative_to(DATASET_DIR) and new.exists():
        if not files:
            try:
                # this is substantially faster and we've checked that new exists and is not malicious
                count = int(subprocess.run(["bash", "-c", f"find {new} -type f | wc -l"], capture_output=True, text=True).stdout)
            except:
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

@app.get("/models/{model}", response_class=HTMLResponse)
def model(request: Request, model: str, manifest: str | None = None, weights: str | None = None):
    params = propagate(request.query_params)
    if model not in model_manifest or not (CACHE / model).exists():
        return RedirectResponse(request.url_for("models", **params))

    params["model"] = model
    if manifest:
        params["manifest"] = manifest
    if weights:
        params["weights"] = weights

    workers = { inf[0]: k for (k, task) in tasks.items() if (inf := task.get(TourStep.INFERENCE)) and task["process"] is not None }
    now = datetime.now()
    runs = { pid: task for pid, task in tasks.items() if TourStep.TRAINING in task }
    return templates.TemplateResponse(request=request, name="model.html", context=dict(
        **model_manifest[model],
        now=now,
        runs=runs,
        workers=workers,
        params=params
    ))

@app.get("/model/{model}/options")
def model_options(request: Request, model: str, manifest: Optional[str] = None, weights: Optional[str] = None):
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    runs = mlflow.search_runs(experiment_names=[model_info["title"]], max_results=100, output_format="list")
    completions = {}
    for k, v in model_manifest[model]["options"].items():
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

    options = {
        **model_info.get("options", {}),
        "alias": {
            "title": "Alias",
            "description": "Alias for inference worker",
            "type": "string",
        }
    }
    return dict(options=options, completions=completions)

class TaskResponse(BaseModel):
    pid: int
    logs: str
    duplicate: bool=False

@app.post("/model/{model}/infer")
async def model_infer(request: Request, model: str, alias: Optional[str]=None, force: bool=False):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    params = propagate(request.query_params)
    params["model"] = model

    options = await request.json()
    if not alias:
        alias = options.get("alias")

    for pid, task in tasks.items():
        if task["process"] and (info := task.get(TourStep.INFERENCE)) and info[0] == (alias or model):
            raise HTTPException(status_code=400, detail=f"Worker with alias '{alias or model}' already running")

    params["pid"] = create_inference_worker(model, options.items(), alias)

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
    tasks[pid][TourStep.TRAINING] = flags

    params = propagate(request.query_params)
    params["model"] = model
    params["pid"] = pid
    # skip labeling steps
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.TRAINING.value
    return TaskResponse(pid=pid, logs=str(url_for_query(request, "logs", **params)))

@app.post("/model/{model}/install")
def model_install(request: Request, model: str):
    if model not in model_manifest:
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")

    params = propagate(request.query_params)
    model_dir = model_manifest[model]["dir"]
    if (CACHE / model).exists():
        params["model"] = model
        raise HTTPException(status_code=400, detail=f"Model '{model}' already exists or installation is in progress")

    models_config["added"].append(model)
    save_models(models_config)

    params["model"] = model
    params["pid"] = start_task(["bash", "-c", f"./setup.sh && echo \"Finished installing '{model}'\""], model_dir, f"Installing model: `{model}`")
    return { "pid": params["pid"], "logs": str(url_for_query(request, "logs", **params)) }

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
    return dict([task[TourStep.INFERENCE] for task in tasks.values() if TourStep.INFERENCE in task and task["process"] is not None])

@app.get("/dataset", response_class=HTMLResponse)
def dataset_get(request: Request, model: str):
    if model not in model_manifest or not (CACHE / model).exists():
        return RedirectResponse(request.url_for("models"))

    now = datetime.now()
    old = { pid: task for pid, task in tasks.items() if TourStep.DATASET in task }

    return templates.TemplateResponse(request=request, name="dataset.html", context=dict(now=now, tasks=old, **model_manifest[model], params=propagate(request.query_params)))

async def receive_files(base_dir: Path, files: list[UploadFile]):
    base_dir.mkdir()
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
    title: str | None
    group_size: int
    group_separation: str
    regex_include: str
    regex_exclude: str
    files: Optional[list[UploadFile]] = []

@app.post("/dataset", response_class=HTMLResponse)
async def dataset(request: Request, data: Annotated[DatasetCreation, Form()], model: str):
    params = propagate(request.query_params);
    if model not in model_manifest or not (CACHE / model).exists():
        raise HTTPException(status_code=404, detail="Model is not installed")

    dataset = Path(data.dataset)
    data.files = [f for f in data.files if f.size > 0]
    if len(data.files) > 0:
        if dataset.exists():
            raise HTTPException(status_code=400, detail="Cannot create dataset from upload if directory already exists")
        await receive_files(dataset, data.files)

    data_dict = data.model_dump()
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
        except:
            pass
    os.set_blocking(task["process"].stdout.fileno(), False)

    if id is None:
        return RedirectResponse(url_for_query(request, "logs", pid=pid, **params), status_code=303)
    task[TourStep.DATASET] = id

    params = propagate(request.query_params)
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.LABELING.value
    params["project"] = id
    return RedirectResponse(url_for_query(request, "label", **params), status_code=303)

class DatasetAddition(BaseModel):
    dataset: str
    group_separation: str
    files: list[UploadFile]

@app.post("/dataset/upload", response_class=HTMLResponse)
async def dataset_upload(request: Request, data: Annotated[DatasetAddition, Form()], project: int):
    params = propagate(request.query_params);

    if not (base := Path(data.dataset)).exists():
        raise HTTPException(status_code=400, detail="Provided dataset does not exist")
    base = base / datetime.now().strftime("upload-%Y-%m-%d-%H-%M-%S")

    await receive_files(base, data.files)
    addition = json.dumps({ "project": project, "upload_dir": str(base), "group_separation": data.group_separation })
    pid = start_task(["uv", "run", "add.py"], "../ls-utils", f"Adding tasks to project", extra_env={ "ADDITION_REQUEST": addition })
    tasks[pid][TourStep.DATASET] = project

    return RedirectResponse(url_for_query(request, "label", **params), status_code=303)

@app.get("/export", response_class=HTMLResponse)
def export_get(request: Request):
    now = datetime.now()
    old = { pid: task for pid, task in tasks.items() if TourStep.EXPORT in task }
    return templates.TemplateResponse(request=request, name="export.html", context=dict(now=now, tasks=old, params=propagate(request.query_params)))

class ExportRequest(BaseModel):
    project: int
    task: str
    dir: str | None = None
    combine: str | None = None

@app.post("/export")
def export(request: Request, export_request: ExportRequest):
    env = dict(TASK=export_request.task, PROJECT_ID=str(export_request.project))

    if export_request.dir:
        env["EXPORT_DIR"] = export_request.dir
    if export_request.combine:
        env["COMBINE"] = export_request.combine
    params = propagate(request.query_params)
    params["pid"] = start_task(["uv", "run", "export.py"], "../ls-utils", f"Export worker", extra_env=env)
    tasks[params["pid"]][TourStep.EXPORT] = export_request.project
    return { "pid": params["pid"], "logs": str(url_for_query(request, "logs", **params)) }

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
def finish_list(request: Request, model: str, manifest: str):
    run_files = []
    if not (model_info := model_manifest.get(model)):
        raise HTTPException(status_code=404, detail="Model does not exist")
    if not (manifest := Path(manifest)).exists():
        raise HTTPException(status_code=404, detail="Manifest does not exist")

    root = manifest.parent
    if root.name.startswith(".export"):
        root = root.parent

    manifests = [str(x) for x in root.rglob("manifest.json")]
    runs = mlflow.search_runs(experiment_names=[model_info["title"]], max_results=100, output_format="list")
    for run in runs:
        params = run.data.params.values()
        if not any((m in params for m in manifests)):
            continue

        path = Path(ARTIFACTS) / run.info.experiment_id / run.info.run_id / "artifacts"
        if len(tree := file_tree(path)) > 0:
            run_files.append((run.info.run_id, run.info.run_name, tree))
    
    return { "runs": run_files, "experiment": run.info.experiment_id, "dataset": file_tree(root), "dataset_root": str(root) }

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

    override = { **params, "tour": TourStep.MONITORING.value } if params.get("tour") == TourStep.TRAINING.value else params
    if info := task.get("run_info"):
        override["experiment"], override["run"] = info
        run_url = str(url_for_query(request, "dashboard", **override))
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
    return RedirectResponse(url_for_query(request, "logs", **params), status_code=303) # 303 changes to GET

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
