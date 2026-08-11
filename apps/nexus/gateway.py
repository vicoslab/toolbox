from flask import Flask, render_template, abort, request
import requests
from pathlib import Path
import os
import json
import time
import logging

forms = {}
workers = {}
projects = {}
autostart = json.loads(os.environ.get("TOOLBOX_AUTOSTART", "{}"))
keepalive = {}
lifetime = 60 * 15 # 15min

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

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

DOMAIN = os.environ["DOMAIN"]
def proxy_inference_request(port, data):
    for task in data["tasks"]:
        for k, v in task["data"].items():
            if v.startswith("/app/label-studio/data/upload/"):
                task["data"][k] = f"https://{DOMAIN}{v}"
    response = requests.post(f"http://localhost:{port}/predict", json=data)
    return (response.text, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

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
    if (worker := workers.get(alias)):
        port, _ = worker
        return proxy_inference_request(port, data)

    abort(404)

# PUBLIC :: ui endpoint and proxy for individual models
@app.route("/infer/<alias>", methods=["GET", "POST"])
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
