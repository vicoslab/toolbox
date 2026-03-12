from flask import Flask, render_template, request, redirect
import json
import os
from pathlib import Path
from subprocess import Popen, PIPE, STDOUT
import re

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
    return render_template("label-studio.html")

@app.route("/dashboard")
def dashboard():
    return render_template("mlflow.html")

MODEL_DIR = Path(os.getenv("MODEL_DIR"))
model_manifest = {}
for p in sorted(MODEL_DIR.iterdir()):
    manifest = p / "manifest.json"
    model = {}
    if manifest.exists():
        model["train"] = json.loads(manifest.read_text())
        model["title"] = model["train"]["title"]
        model["description"] = model["train"]["description"]
    
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
    command = [MODEL_DIR / id / "train"]
    if len(request.form) > 0:
        for k, v in request.form.items():
            if v != "":
                command.extend(["--" + k, v])
        active_task["output"] = []
        active_task["description"] = f"Model training: `{id}`"
        active_task["process"] = Popen(command, cwd = MODEL_DIR / id, stdout = PIPE, stderr = STDOUT, text = True)
        os.set_blocking(active_task["process"].stdout.fileno(), False)

    return render_template("model.html", **model_manifest[id])

@app.route("/task/status")
def status():
    if active_task["process"] is not None:
        active_task["code"] = active_task["process"].poll()
        if active_task["code"] == None:
            return "🟡 Running"
    return "🟢 Idle" if active_task["code"] == 0 else "🔴 Exited"

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
    print(active_task, active_task["code"] is None)
    return render_template("logs.html", output=out, description=active_task["description"], running=active_task["code"] is None)

@app.route("/task/stop", methods=["POST"])
def kill():
    if active_task["process"] is not None:
        active_task["process"].kill()
        # hardcode because process would still respond as alive right now
        active_task["code"] = -9
    return redirect("/task/logs")
