from flask import Flask, render_template, request, redirect, url_for, abort
import json
import os
from pathlib import Path
import subprocess
from subprocess import Popen, PIPE, STDOUT
import re
from enum import Enum
from datetime import datetime
from urllib.parse import urlparse

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
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", src["url"], str(repo)])
            subprocess.run(["git", "sparse-checkout", "init", "--cone"], cwd=repo)

        for name in src["models"]:
            model_path = repo / name
            if not model_path.exists():
                subprocess.run(["git", "sparse-checkout", "add", name], cwd=repo)
                subprocess.run(["git", "checkout"], cwd=repo)

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
    (TourStep.START.value, "Introduction", "tour"),
    (TourStep.MODEL_SELECTION.value, "Select model", "models"),
    (TourStep.DATASET.value, "Pick dataset", "model"),
    (TourStep.LABELING.value, "Label images", "label"),
    (TourStep.EXPORT.value, "Export annotations", "export"),
    (TourStep.TRAINING.value, "Start training", "model"),
    (TourStep.MONITORING.value, "Monitor run", "dashboard"),
    (TourStep.INFERENCE.value, "Inference", "model"),
]

dataset_tasks = {
    "super-simple-net": "anomaly-detection",
    "cedirnet": "orientation-estimation",
}

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

app = Flask(__name__)

def propagate():
    r = {}
    try:
        r["tour"] = int(request.args["tour"])
        for attr in ["model", "project", "experiment"]:
            if attr in request.args:
                r[attr] = request.args[attr]
    except:
        pass
    return r

brand_name_long = os.getenv("TOOLBOX_BRAND_NAME_LONG", "ViCoS Toolbox")
brand_name_short = os.getenv("TOOLBOX_BRAND_NAME_SHORT", "ViCoS")
@app.context_processor
def inject_stage_and_region():
    return dict(tour_steps=TOUR_STEPS, tour_enum=TourStep, brand_name_long=brand_name_long, brand_name_short=brand_name_short)

@app.route("/")
def index():
    return render_template("index.html", params=propagate())

@app.route("/tour")
def tour():
    return render_template("tour.html", params=propagate())

@app.route("/label")
def label():
    page = ""
    if project := request.args.get("project"):
        page = f"/projects/{int(project)}"
    
    return render_template("label-studio.html", page=page, params=propagate())

@app.route("/dashboard")
def dashboard():
    page = "/#/experiments"
    if ex := request.args.get("experiment"):
        page = f"/{int(ex)}"
        if run := request.args.get("run"):
            page += f"/runs/{run}"
    return render_template("mlflow.html", page=page, params=propagate())

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
    import time

    def monitor_task():
        while True:
            time.sleep(1)
            for task in tasks.values():
                if task["process"] and not os.get_blocking(task["process"].stdout.fileno()):
                    refresh_logs(task)

    # this is needed because task's writes will start blocking if output is not consumed
    Thread(target=monitor_task, daemon=True).start()

@app.route("/models")
def models():
    groups = {}
    # if len(models_config["added"]):
    #     groups[("Installed models", None)] = [], { m: model_manifest[m] for m in models_config["added"] }
    for group in models_config["sources"]:
        installed = []
        available = {}
        for m in group["models"]:
            if False: #m in models_config["added"]:
                installed.append(m)
            else:
                available[m] = model_manifest[m]
        groups[(group["group"], group["owner"])] = installed, available
    return render_template("models.html", groups=groups, params=propagate())

@app.route("/models/add", methods=["POST"])
def models_add():
    data = request.json
    error = { "error": "Malformed sources definition" }, 400

    if type(data) != list:
        return error

    for defs in data:
        models, owner, group, url = map(defs.get, ["models", "owner", "group", "url"])
        result = urlparse(url)
        if not (len(defs) == 4 and type(owner) == str and type(group) == str and type(models) == list and all(map(lambda x: type(x) == str, models)) and all([result.scheme, result.netloc])):
            return error

    for defs in data:
        if defs not in models_config["sources"]:
            models_config["sources"].append(defs)

    refresh_manifest()
    save_models(models_config)
    return {}, 200

dataset_dir = Path(os.environ["LOCAL_FILES_DOCUMENT_ROOT"])
@app.route('/datasets', defaults={'path': ''})
@app.route("/datasets/<path:path>", methods=["GET"])
def datasets(path):
    new = dataset_dir / path
    if new.is_relative_to(dataset_dir) and new.exists():
        return [str(x.name) for x in new.iterdir() if x.is_dir()]
    
    return { "error": "Invalid path" }, 400

@app.route("/models/<model>", methods=["GET", "POST"])
def model(model):
    if model not in model_manifest:
        return redirect(url_for("models", **propagate()))
    runs = mlflow.search_runs(experiment_names=[model_manifest[model]["title"]], max_results=100, output_format="list")
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

    return render_template(
        "model.html",
        **model_manifest[model],
        completions=completions,
        installed=(CACHE / model).exists(),
        train=(model_manifest[model]["dir"] / "train.py").exists(),
        params={ **propagate(), "model": model }
    )

@app.route("/model/<model>/infer", methods=["POST"])
def model_infer(model):
    if model not in model_manifest:
        return f"Model '{model}' does not exist", 404

    data = request.json if request.is_json else request.form.to_dict()
    params = propagate()
    params["model"] = model
    params["pid"] = create_inference_worker(model, data.items())

    if params.get("tour") == TourStep.MONITORING.value:
        params["tour"] = TourStep.INFERENCE.value
    return redirect(url_for("logs", **params))

@app.route("/model/<model>/train", methods=["POST"])
def model_train(model):
    if model not in model_manifest:
        return f"Model '{model}' does not exist", 404

    data = request.json if request.is_json else request.form.to_dict()
    flags = build_model_options(model_manifest[model]["options"], data.items())
    pid = start_task(
        ["uv", "run", "train.py"] + flags,
        model_manifest[mode]["dir"],
        f"Model training: `{model}`",
        { "VIRTUAL_ENV": CACHE / model / ".venv" }
    )

    params = propagate()
    params["model"] = model
    params["pid"] = pid
    # skip labeling steps
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.TRAINING.value
    return redirect(url_for("logs", **params))

@app.route("/model/<model>/install", methods=["POST"])
def model_install(model):
    if model not in model_manifest:
        return f"Model '{model}' does not exist", 404

    model_dir = model_manifest[model]["dir"]
    if (CACHE / model).exists():
        print(f"Skipping installation request for `{model}`")
        return render_template("model.html", **model_manifest[model], installed=(CACHE / model).exists(), train=(model_dir / "train.py").exists(), params=params)

    models_config["added"].append(model)
    save_models(models_config)

    params = propagate()
    params["model"] = model
    params["pid"] = start_task(["bash", "-c", f"./setup.sh && echo \"Finished installing '{model}'\""], model_dir, f"Installing model: `{model}`")
    return redirect(url_for("logs", **params))

@app.route("/active", methods=["GET"])
def active_models():
    return dict([task["inference"] for task in tasks.values() if "inference" in task and task["process"] is not None])

@app.route("/dataset", methods=["POST"])
def dataset():
    data = request.json if request.is_json else request.form.to_dict()

    if type(data) == dict:
        env = {}

        if "task" in data:
            env["TASK"] = data["task"]
        elif (model := request.args.get("model")) in dataset_tasks:
            env["TASK"] = dataset_tasks[model]
        else:
            return "Request body must contain key 'task' or known model must be provided in query parameter 'model'.", 400

        if dataset := data.get("dataset"):
            env["DATASET"] = dataset
        if title := data.get("title"):
            env["PROJECT_TITLE"] = title
    else:
        return "Request body must be an object with keys 'task'. Can optionally include 'title' and 'dataset'.", 400

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
        return "Project could not be created", 400

    params = propagate()
    if params.get("tour") == TourStep.DATASET.value:
        params["tour"] = TourStep.LABELING.value
    params["project"] = id
    return redirect(url_for("label", **params))

@app.route("/export", methods=["GET", "POST"])
def export():
    if request.method == "POST":
        data = request.json if request.is_json else request.form.to_dict()
        env = {}

        if (task := data.get("task")) or (task := dataset_tasks.get(request.args.get("model"))):
            env["TASK"] = task
        else:
            return "Request body must contain key 'task' or known model must be provided in query parameter 'model'.", 400

        if (project := data.get("project")) or (project := request.args.get("project")):
            env["PROJECT_ID"] = project
        else:
            return "Project id must be passed as 'project' in body or through query params.", 400

        if export_dir := data.get("dir"):
            env["EXPORT_DIR"] = export_dir

        start_task(["uv", "run", "export.py"], "../ls-utils", f"Export worker for project {env['PROJECT_ID']}", extra_env=env)

    return render_template("export.html", params=propagate())

@app.route("/task/status")
def status():
    now = datetime.now()
    timestamps = { pid: now - task.get("end_time", task["start_time"]) for pid, task in tasks.items() }
    return render_template("status.html", tasks=tasks, timestamps=timestamps, params=propagate())

@app.route("/task/logs/<int:pid>")
def logs(pid):
    if pid not in tasks:
        abort(404)
    if app.debug: # in production mode this gets refreshed in a thread
        refresh_logs(tasks[pid])

    params = propagate()
    params["pid"] = pid

    override = { **params, "tour": TourStep.MONITORING.value } if params.get("tour") == TourStep.TRAINING.value else params
    task = tasks[pid]
    if info := task.get("run_info"):
        override["experiment"], override["run"] = info
        run_url = url_for("dashboard", **override)
    else:
        run_url = None

    return render_template("logs.html", output=task["output"], description=task["description"], running=task["code"] is None, run_shortcut=run_url, params=params)

@app.route("/task/stop/<int:pid>", methods=["POST"])
def kill(pid):
    if pid not in tasks:
        abort(404)
    if tasks[pid]["process"] is not None:
        tasks[pid]["process"].terminate()
    params = propagate()
    params["pid"] = pid
    return redirect(url_for("logs", **params))
