from flask import Flask, render_template, request, redirect
import json
import os
from pathlib import Path
from subprocess import Popen, PIPE, STDOUT
import re

import mlflow
mlflow.set_tracking_uri("http://localhost:8081")
ARTIFACTS = os.getenv("MLFLOW_ARTIFACTS_DESTINATION", "")

# make sure virutal env doesn't bleed into subprocesses
if "VIRTUAL_ENV" in os.environ:
    del os.environ["VIRTUAL_ENV"]

app = Flask(__name__)

@app.route("/")
def index():
    return """
    <h2>Available pages</h2>
    <ul>
        <li><a href="/label">LabelStudio</a></li>
        <li><a href="/dashboard">MLFlow</a></li>
    </ul>
    """

@app.route("/label")
def label():
    page = ""
    if "id" in request.args:
        page = f"/projects/{int(request.args['id'])}"
    
    return render_template("label-studio.html", page=page)

@app.route("/dashboard")
def dashboard():
    return render_template("mlflow.html")

MODEL_DIR = Path(os.getenv("MODEL_DIR"))
model_manifest = {}
for p in sorted(MODEL_DIR.iterdir()):
    manifest = p / "model.json"
    model = {}
    if manifest.exists():
        manifest = json.loads(manifest.read_text())
        model["options"] = manifest["properties"]
        model["title"] = manifest["title"]
        model["description"] = manifest["description"]
    
    if model != {}:
        model_manifest[p.name] = model

active_task = {
    "description": "",
    "output": [],
    "process": None,
    "code": 0,
}

@app.route("/models")
def models():
    return render_template("models.html", models=model_manifest)

@app.route("/models/<id>", methods=["GET", "POST"])
def model(id):
    if request.method == "POST":
        if "start-inference-worker" in request.form:
            command = ["uv", "run", "gunicorn", "--bind", ":9090", "infer:app", "--"]
            active_task["description"] = f"Inference service worker for: `{id}`"
        else:
            command = ["uv", "run", "train.py"]
            active_task["description"] = f"Model training: `{id}`"
        for k, v in request.form.items():
            if v != "" and k in model_manifest[id]["options"]:
                if v.startswith("mlflow-artifacts:") and ARTIFACTS:
                    v = v.replace("mlflow-artifacts:", ARTIFACTS, count=1)
                elif v.startswith("run:/") and ARTIFACTS:
                    run_name, rest = v[5:].split("/", maxsplit=1)
                    runs = mlflow.search_runs(filter_string=f"run_name = '{run_name}'", experiment_names=["SuperSimpleNet"], max_results=1, output_format="list")
                    if len(runs) > 0:
                        info = runs[0].info
                        v = f"{ARTIFACTS}/{info.experiment_id}/{info.run_id}/artifacts/{rest}"
                command.extend(["--" + k, v])
        active_task["output"] = []
        active_task["process"] = Popen(command, cwd = MODEL_DIR / id, stdout = PIPE, stderr = STDOUT, text = True)
        os.set_blocking(active_task["process"].stdout.fileno(), False)

    return render_template("model.html", **model_manifest[id], train=(MODEL_DIR / id / "train.py").exists())

@app.route("/create", methods=["POST"])
def create():
    if type(request.json) == dict:
        env = {
            "TASK": request.json["task"],
            "DATASET": request.json["dataset"],
        }
        if "title" in request.json:
            env["PROJECT_TITLE"] = request.json["title"]
    else:
        return "Request body must be an object with keys 'task' and 'dataset'. Can optionally include 'title'.", 400
    command = ["uv", "run", "create.py"]
    active_task["description"] = f"Project creation"
    active_task["output"] = []
    active_task["process"] = Popen(command, cwd = "../ls-utils", stdout = PIPE, stderr = STDOUT, text = True, env = { **os.environ, **env })
    id = None
    while line_in := active_task["process"].stdout.readline():
        active_task["output"].append(line_in)
        try:
            id = int(line_in)
            break
        except:
            pass
    os.set_blocking(active_task["process"].stdout.fileno(), False)

    if id is None:
        return "Project could not be created", 400

    return redirect("/label?id=" + str(id))

@app.route("/export", methods=["POST"])
def export():
    try:
        env = { "PROJECT_ID": str(int(request.json)) }
    except:
        if type(request.json) == dict:
            env = {
                "PROJECT_ID": str(int(request.json["id"])),
                "EXPORT_DIR": request.json["dir"],
            }
        else:
            return "Request body must be int or object", 400
    command = ["uv", "run", "export.py"]
    active_task["description"] = f"Export worker for project {env['PROJECT_ID']}"
    active_task["output"] = []
    active_task["process"] = Popen(command, cwd = "../ls-utils", stdout = PIPE, stderr = STDOUT, text = True, env = { **os.environ, **env })
    os.set_blocking(active_task["process"].stdout.fileno(), False)

    return "", 204

@app.route("/task/status")
def status():
    if active_task["process"] is not None:
        active_task["code"] = active_task["process"].poll()
        if active_task["code"] == None:
            return "running"
    return "idle" if active_task["code"] == 0 else "exited"

# regex for grouping log entries
# if line starts with eval:, visualise: or (for example) 10/300: (from something like a tqdm batch counter)
# consecutive log lines get grouped up (most recent one is shown)
tqdm_header = re.compile(r"^(\d+)/(\d+):|^(eval):|^(visualise):")

@app.route("/task/logs")
def logs():
    out = active_task["output"]

    m = tqdm_header.match(out[-1]) if len(out) else None
    last = m.groups() if m else None

    if active_task["process"]:
        while line := active_task["process"].stdout.readline():
            line = line.strip()
            if len(line) == 0:
                continue
            m = tqdm_header.match(line)
            if m:
                gs = m.groups()
                if gs == last:
                    out[-1] = line
                else:
                    out.append(line)
                last = gs
            else:
                out.append(line)
                last = None

    return render_template("logs.html", output=out, description=active_task["description"], running=active_task["code"] is None)

@app.route("/task/stop", methods=["POST"])
def kill():
    if active_task["process"] is not None:
        active_task["process"].terminate()
        # hardcode because process would still respond as alive right now
        active_task["code"] = -9
    return redirect("/task/logs")
