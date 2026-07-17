from flask import Flask, render_template, abort, request
import requests
from pathlib import Path
import os
import json

forms = {}
workers = {}
projects = {}

app = Flask(__name__)

def refresh_workers():
    forms.update({ p.parent.name: p.read_text() for p in (Path(os.environ["TOOLBOX_CACHE"]) / ".models").glob("**/ui.html") })
    response = requests.get("http://localhost:8079/active")
    if response.status_code == 200:
        workers.update({ v: k for k, v in response.json().items()})

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

# proxy prediction requests for label-studio-ml-backend
@app.route("/predict", methods=["POST"])
def predict():
    if app.debug:
        refresh_workers()

    data = request.json
    # interactive requests will have a context, and should be set up such that the region contains info on which model to run
    if (context := data["params"]["context"]) and (region := context.get("region")) and (model := get_region_label(region)) and (port := workers.get(model.lower())):
        return proxy_inference_request(port, data)
    # otherwise try to run the model associated with the project, if running
    if (model := projects.get(data["project"])) and (port := workers.get(model)):
        return proxy_inference_request(port, data)

    abort(404)

# PUBLIC :: ui endpoint and proxy for individual models
@app.route("/infer/<model>", methods=["GET", "POST"])
def infer(model):
    if app.debug:
        refresh_workers()

    if model not in forms or model not in workers:
        abort(404)

    # forward requests to actual backend
    if request.method == "POST":        
        data = request.json if request.is_json else request.form.to_dict()
        files = [
            (field_name, (file.filename, file.stream, file.content_type))
            for field_name in request.files
            for file in request.files.getlist(field_name)
        ]

        port = workers[model]
        response = requests.post(f"http://localhost:{port}/infer", data=data, files=files)

        return (response.content, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

    return f"""
        <!DOCTYPE html>
        <html>
            <body>
                <form action="">
                    {forms[model]}
                    <div class="toolbar">
                        <div class="toolbar-left"></div>
                        <div class="toolbar-right"></div>
                    </div>
                </form>
            </body>
            <script src="/static/opencv.js" async></script>
            <script src="/static/model.js"></script>
            <script>window.endpoint = "{model}"</script>
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
