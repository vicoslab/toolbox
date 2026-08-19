from flask import Flask, render_template, abort, request
from flask_limiter import Limiter
import requests
from pathlib import Path
import os
import json
import time
import logging
from datetime import datetime

forms = {}
workers = {}
projects = {}
autostart = json.loads(os.environ.get("TOOLBOX_AUTOSTART", "{}"))
keepalive = {}
lifetime = 60 * 15 # 15min

DOMAIN = os.environ["DOMAIN"]
access_log = (access_log_path := os.getenv("TOOLBOX_ACCESS_LOG")) and open(access_log_path, 'w+')
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
    forms.update({ p.parent.name: p.read_text() for p in (Path(os.environ["TOOLBOX_CACHE"]) / ".models").glob("**/ui.html") })
    try:
        response = requests.get("http://localhost:8079/active")
        if response.status_code == 200:
            workers.clear()
            workers.update(response.json())
            app.logger.debug("Workers: %s", workers)

            now = time.monotonic()
            for k in keepalive:
                if k not in workers:
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

def get_region_label(region):
    for r in region["results"]:
        for k in r["value"]:
            if k.endswith("labels"):
                return r["value"][k][0]
    return None

def try_autostart(alias):
    if auto := autostart.get(alias):
        if alias in keepalive:
            keepalive[alias][1] = time.monotonic()
        else:
            model, config = auto["model"], auto["config"]
            app.logger.info("Autostart '%s'", alias)
            response = requests.post(f"http://localhost:8079/model/{model}/infer?alias={alias}", json=config)
            if response.status_code == 200:
                keepalive[alias] = [response.json()["pid"], time.monotonic()]
            for _ in range(10):
                if worker := workers.get(alias):
                    port, _ = worker
                    try:
                        requests.get(f"http://localhost:{port}/health")
                        if response.status_code == 200:
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
    if (context := data["params"]["context"]) and (region := context.get("region")) and (alias := get_region_label(region)): pass
    # otherwise try to run the model associated with the project
    elif (alias := projects.get(data["project"])): pass
    else: abort(404)

    alias = alias.lower()
    try_autostart(alias)
    if not (worker := workers.get(alias)):
        abort(404)

    port, _ = worker

    for task in data["tasks"]:
        for k, v in task["data"].items():
            if type(v) == list:
                for i in range(len(v)):
                    if v[i].startswith("/app/label-studio/data/upload/"):
                        v[i] = f"https://{DOMAIN}{v[i]}"
            elif v.startswith("/app/label-studio/data/upload/"):
                task["data"][k] = f"https://{DOMAIN}{v}"
    response = requests.post(f"http://localhost:{port}/predict", json=data)
    return (response.text, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

private_host, private_port = [*DOMAIN.split(":"), "443"][:2]
if not private_host:
    private_host = "localhost"
def is_private_endpoint():
    req_host, req_port = [*request.host.split(":"), "443"][:2]
    return private_host == req_host and private_port == req_port

# PUBLIC :: ui endpoint and proxy for individual models
@app.route("/infer/<alias>", methods=["GET", "POST"])
@limiter.limit("1 per 5 seconds", methods=["POST"], exempt_when=is_private_endpoint)
@limiter.limit("100 per day", methods=["POST"], exempt_when=is_private_endpoint)
def infer(alias):
    if app.debug:
        refresh_workers()

    try_autostart(alias)

    if not (worker := workers.get(alias)):
        abort(404)

    port, model = worker
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
        except:
            abort(404)

    if not (form := forms.get(model)):
        abort(404)
    return f"""
        <!DOCTYPE html>
        <html>
            <body>
                <form action="">
                    {form}
                    <div class="toolbar">
                        <div class="toolbar-left"></div>
                        <div class="toolbar-right"></div>
                    </div>
                </form>
            </body>
            <script src="/static/opencv.js" async></script>
            <script src="/static/UTIF.js" async></script>
            <script src="/static/model.js"></script>
            <script>window.endpoint = "{alias}"</script>
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
        </html>
    """
