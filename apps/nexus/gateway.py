from flask import Flask, render_template, abort, request
from flask_limiter import Limiter
import requests
from pathlib import Path
import hashlib
import os
import json
import time
import logging
from datetime import datetime

CACHE = os.environ["TOOLBOX_CACHE"]
forms = {}
model_titles = {}
workers = {}
projects = {}
autostart_public = json.loads(os.environ.get("TOOLBOX_AUTOSTART", "{}"))
autostart_tools = {}
keepalive = {}
lifetime = 60 * 15 # 15min

access_log = None
if access_log_path := os.getenv("TOOLBOX_ACCESS_LOG"):
    access_log_path = Path(access_log_path)
    access_log_path.parent.mkdir(parents=True, exist_ok=True)
    access_log = access_log_path.open(mode='w+')
client_timeout = os.getenv("TOOLBOX_RATELIMIT_INTERVAL")
clients = {}

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

limiter = Limiter(
    lambda: request.remote_addr,
    app=app,
    default_limits=[],
    storage_uri="memory://",
    strategy="moving-window",
)

def refresh_workers():
    model_dir = Path(os.environ["TOOLBOX_CACHE"], ".models")
    for p in model_dir.rglob("ui.html"):
        model, group, owner, *_ = map(lambda x: x.name, p.parents)
        sid = hashlib.sha256(f"{owner}/{group}".encode()).hexdigest()[:7]
        forms[f"{sid}-{model}"] = p.read_text()

    try:
        response = requests.get("http://localhost:8079/tools")
        if response.status_code == 200:
            autostart_tools.clear()
            for vals in response.json().values():
                autostart_tools.update(vals)
            app.logger.debug("Tools: %s", autostart_tools)
    except Exception as e:
        app.logger.error("Refresh tools: %s", e)

    try:
        response = requests.get("http://localhost:8079/active")
        if response.status_code == 200:
            workers.clear()
            workers.update(response.json())
            app.logger.debug("Workers: %s", workers)

            now = time.monotonic()
            for k in list(filter(lambda x: x in workers, keepalive)):
                del keepalive[k]
            for k, (pid, timestamp) in keepalive.items():
                if now - timestamp > lifetime:
                    app.logger.info("Autostart: stopping '%s'", k)
                    r = requests.post(f"http://localhost:8079/task/stop/{pid}")
                    if r.status_code == 200:
                        del keepalive[k]
            
    except Exception as e:
        app.logger.error("Refresh workers: %s", e)

if not app.debug:
    from threading import Thread
    import time

    def track_workers():
        while True:
            time.sleep(5)
            refresh_workers()

    Thread(target=track_workers, daemon=True).start()

# stub label-studio-ml-backend api endpoints
@app.route("/setup", methods=["POST"])
def setup():
    data = request.json
    if extra := data.get("extra_params"):
        extra = json.loads(extra)
        # note: project seems to be a float value for some reason
        projects[data["project"]] = extra["model"]

    return { "model_version": "0.0.1" }

@app.route("/health")
def health():
    return {"model_class":"Proxy","status":"UP"}

def get_region_label(regions):
    # regions should have the same labels due to filtering in the frontend
    for region in regions:
        for r in region["results"]:
            for k, v in r["value"].items():
                if k.endswith("labels"):
                    return v[0]
    return None

def try_autostart(autostart, alias):
    if not (auto := autostart.get(alias)):
        return

    if alias in keepalive:
        keepalive[alias][1] = time.monotonic()
        return

    app.logger.info("Autostart '%s'", alias)
    response = requests.post(f"http://localhost:8079/model/{auto["model"]}/infer?alias={alias}", json=auto["options"])
    if response.status_code != 200:
        return

    keepalive[alias] = [response.json()["pid"], time.monotonic()]
    for _ in range(10):
        if worker := workers.get(alias):
            port = worker["port"]
            try:
                if requests.get(f"http://localhost:{port}/health").status_code == 200:
                    return
            except Exception as e:
                app.logger.debug("Health %s", e)
        else:
            refresh_workers()
        time.sleep(0.05)

# proxy prediction requests for label-studio-ml-backend
@app.route("/predict", methods=["POST"])
def predict():
    if app.debug:
        refresh_workers()

    data = request.json
    # interactive requests will have a context, and should be set up such that the region contains info on which model to run
    if (context := data["params"]["context"]):
        # if there is a context, but we cant get model from it, we want to fail
        # only if there is no context, we fall back to project associated model
        if not (regions := context.get("regions")) or not (alias := get_region_label(regions)):
            abort(404)
    # otherwise try to run the model associated with the project
    elif (alias := projects.get(data["project"])): pass
    else: abort(404)

    try_autostart(autostart_tools, alias)
    if (worker := workers.get(alias)): pass
    elif (worker := next((v for k,v in workers.items() if Path(CACHE, k).exists() and k[8:] == alias), None)): pass # compatibility with loosely specified models
    else: abort(404)

    port = worker["port"]

    for task in data["tasks"]:
        for k, v in task["data"].items():
            if type(v) == list:
                for i in range(len(v)):
                    if v[i].startswith("/app/label-studio/data/upload/"):
                        v[i] = f"http://localhost:8080{v[i]}"
                    v[i] = v[i].replace(os.environ["LABEL_STUDIO_HOST"], "http://localhost:8080")
            else:
                if v.startswith("/app/label-studio/data/upload/"):
                    task["data"][k] = f"http://localhost:8080{v}"
                task["data"][k] = task["data"][k].replace(os.environ["LABEL_STUDIO_HOST"], "http://localhost:8080")
    response = requests.post(f"http://localhost:{port}/predict", json=data)
    return (response.text, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

private_host, private_port = [*os.environ["DOMAIN"].split(":"), "443"][:2]
if not private_host:
    private_host = "localhost"
def is_private_endpoint():
    req_host, req_port = [*request.host.split(":"), "443"][:2]
    return private_host == req_host and private_port == req_port

# PUBLIC :: ui endpoint and proxy for individual models
# @limiter.limit("1 per 5 seconds", methods=["POST"], exempt_when=is_private_endpoint)
# @limiter.limit("100 per day", methods=["POST"], exempt_when=is_private_endpoint)
@app.route("/infer/<alias>", methods=["GET", "POST"])
def infer(alias):
    if app.debug:
        refresh_workers()

    try_autostart(autostart_public, alias)

    if not (worker := workers.get(alias)):
        abort(404)

    model, port = worker["model"], worker["port"]
    # forward requests to actual backend
    if request.method == "POST":
        data = request.json if request.is_json else request.form.to_dict()
        files = [
            (field_name, (file.filename, file.stream, file.content_type))
            for field_name in request.files
            for file in request.files.getlist(field_name)
        ]

        if access_log and not is_private_endpoint():
            entry = json.dumps(dict(
                files=[filename for (_, (filename, _, _)) in files],
                ip=request.remote_addr,
                timestamp=str(datetime.now()),
                endpoint=alias,
            ))
            access_log.write(entry + "\n")
            access_log.flush()

        try:
            response = requests.post(f"http://localhost:{port}/infer", data=data, files=files)

            return (response.content, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})
        except requests.RequestException:
            abort(404)

    if not (form := forms.get(model)):
        abort(404)
    return f"""
        <!DOCTYPE html>
        <html>
            <body style="background: var(--bg-primary)">
                <form action="{alias}">
                    {form.replace("@ALIAS@", alias)}
                    <div class="toolbar">
                        <div class="toolbar-left"></div>
                        <div class="toolbar-right"></div>
                    </div>
                </form>
            </body>
            <script src="/static/opencv.js" async></script>
            <script src="/static/UTIF.js" async></script>
            <script src="/static/model.js"></script>
            <style>
                html, body {{
                    height: 100%;
                    margin: 0;
                }}
                form {{
                    padding: 2rem;
                    position: relative;
                    box-sizing: border-box;
                    height: 100%;
                    width: 100%;
                }}
            </style>
            <script src="/static/common.js"></script>
            <link rel="stylesheet" href="/static/style.css"></link>
        </html>
    """
