from flask import Flask, render_template, abort, request
import requests
from pathlib import Path
import os
import json

forms = { p.parent.name: p.read_text() for p in Path(os.environ["MODEL_DIR"]).glob("*/ui.html") }
workers = {}

app = Flask(__name__)

def refresh_workers():
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

# proxy prediction requests for label-studio-ml-backend (only interactive at the moment)
@app.route("/predict", methods=["POST"])
def predict():
    if app.debug:
        refresh_workers()

    if (context := request.json["params"]["context"]) and (region := context.get("region")):
        if (model := get_region_label(region)) and (port := workers.get(model.lower())):
            response = requests.post(f"http://localhost:{port}/predict", json=request.json)
            return (response.text, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

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
        data = request.form.to_dict()
        files = [
            (field_name, (file.filename, file.stream, file.content_type))
            for field_name in request.files
            for file in request.files.getlist(field_name)
        ]

        port = workers[model]
        response = requests.post(f"http://localhost:{port}/infer", data=data, files=files)

        return (response.text, response.status_code, {'Content-Type': response.headers.get('Content-Type', 'text/plain')})

    return f"""
        <!DOCTYPE html>
        <html>
            <body>
                <div id="results" style="position: relative;"></div>
                <form action="">
                    <fieldset>
                        {forms[model]}
                    </fieldset>
                </form>
            </body>
            <script src="/static/opencv.js"></script>
            <script src="/static/model.js"></script>
            <script>window.endpoint = "{model}"</script>
            <style>
                fieldset {{
                    padding: 2rem;
                    position: relative;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                }}
            </style>
        </html>
    """
