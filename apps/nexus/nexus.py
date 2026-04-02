from flask import Flask, render_template, request, redirect, send_file
import json
import os
from pathlib import Path
from subprocess import Popen, PIPE, STDOUT
import re
import threading
import hashlib
from optimizer.run_optimizer import run_optimization
from optimizer.optimization_classes import SceneConfig, CameraConfig

app = Flask(__name__)

@app.route("/")
def index():
    return """
    <h2>Available pages</h2>
    <ul>
        <li><a href="/label">LabelStudio</a></li>
        <li><a href="/dashboard">MLFlow</a></li>
        <li><a href="/optimize">CameraOptimizer</a></li>
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
    manifest = p / "model.json"
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
    command = [MODEL_DIR / id / "train.py"]
    if request.method == "POST":
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
        active_task["process"].kill()
        # hardcode because process would still respond as alive right now
        active_task["code"] = -9
    return redirect("/task/logs")

CAM_RESULTS_DIR = Path(os.getenv("CAM_OPTIMIZER_RESULTS_DIR"))
CAM_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

running_optimizations = set()

def make_result_id(svg_content: str, params:dict) -> str:
    combined = svg_content + json.dumps(params, sort_keys=True)
    return hashlib.md5(combined.encode()).hexdigest()[:8]

def launch_optimization(result_dir: Path, params: dict, num_runs: int, description: str):
    """Start optimization in a background thread."""
    result_id = result_dir.name

    if result_id in running_optimizations:
        print(f"Optimization for {result_id} already running. Please wait.")
        return
    
    running_optimizations.add(result_id)
 
    def run():
        try:
            run_optimization(result_dir, params, num_runs)
        except Exception as e:
            print(f"Error: {e}")
        finally:
            running_optimizations.discard(result_id)
 
    threading.Thread(target=run, daemon=True).start()

@app.route("/optimize/status/")
def get_running_optimizations():
    return json.dumps(list(running_optimizations))


@app.route("/optimize/<id>/status")
def optimization_status(id):
    if id in running_optimizations:
        return "running"
    return "done"


@app.route("/optimize", methods=["GET", "POST"])
def camera_optimization():
    if request.method == "POST":
        svg_file = request.files['svg_file']
        svg_content = svg_file.read().decode('utf-8')
        num_runs = int(request.form['num_runs'])

        params = {
            'num_cameras':      int(request.form['num_cameras']),
            'focal_length':     float(request.form['focal_length']),
            'sensor_width':     float(request.form['sensor_width']),
            'f_number':         float(request.form['f_number']),
            'pixel_size':       float(request.form['pixel_size']),
            'max_pixel_on_obj': float(request.form['max_pixel_on_obj']),
            'optimize_focus':   'optimize_focus' in request.form,
            'box_width':        float(request.form['box_width']),
            'box_height':       float(request.form['box_height']),
            'real_width_mm':    float(request.form['object_width']),
            'resolution_mm':    float(request.form['resolution_mm']),
        }

        result_id  = make_result_id(svg_content, params)
        result_dir = CAM_RESULTS_DIR / result_id

        if not result_dir.exists():
            result_dir.mkdir(parents=True)
            (result_dir / 'object.svg').write_text(svg_content)
            (result_dir / 'params.json').write_text(json.dumps({
                'title':        f"{svg_file.filename} ({result_id})",
                'svg_filename': svg_file.filename,
                **params,
            }, indent=2))

            cam_conf = CameraConfig(
                focal_length     = params['focal_length'],
                sensor_width     = params['sensor_width'],
                pixel_size       = params['pixel_size'],
                f_number         = params['f_number'],
                max_pixel_on_obj = params['max_pixel_on_obj'],
                optimize_focus   = params['optimize_focus'],
            )
            scene = SceneConfig.from_svg(
                svg_file      = str(result_dir / 'object.svg'),
                real_width_mm = params['real_width_mm'],
                box_width     = params['box_width'],
                box_height    = params['box_height'],
                cam_conf      = cam_conf,
                num_cameras   = params['num_cameras'],
                res_mm        = params['resolution_mm'],
            )
            issues = scene.validate()
            if issues:
                (result_dir / 'warnings.json').write_text(json.dumps(issues, indent=2))
        
        launch_optimization(result_dir, params, num_runs, f"Camera optimization: {svg_file.filename}")
        return redirect(f'/optimize/{result_id}')
    
    past_runs = []
    for p in sorted(CAM_RESULTS_DIR.iterdir(), reverse=True):
        params_file = p / 'params.json'
        runs_file   = p / 'runs.json'
        if params_file.exists():
            params_data = json.loads(params_file.read_text())
            num_runs    = len(json.loads(runs_file.read_text())) if runs_file.exists() else 0
            past_runs.append({
                'id':       p.name,
                'title':    params_data['title'],
                'num_runs': num_runs,
            })
 
    return render_template('cam-optimization.html', past_runs=past_runs)
    

@app.route("/optimize/<id>")
def camera_optimization_result(id):
    result_dir = CAM_RESULTS_DIR / id
    if not result_dir.exists():
        return "Result not found", 404
 
    params     = json.loads((result_dir / 'params.json').read_text())
    runs_file  = result_dir / 'runs.json'
    runs       = json.loads(runs_file.read_text()) if runs_file.exists() else []
    warnings_file = result_dir / 'warnings.json'
    warnings      = json.loads(warnings_file.read_text()) if warnings_file.exists() else []
 
    return render_template('cam-optimization-result.html', id=id, title=params['title'], params=params, runs=runs, warnings=warnings)


@app.route("/optimize/<id>/plot/<int:run_index>")
def optimization_plot(id, run_index):
    """Serve a specific run's plot image directly from disk."""
    plot_path = CAM_RESULTS_DIR / id / f'run_{run_index}.png'
    if not plot_path.exists():
        return "Plot not found", 404
    return send_file(plot_path, mimetype='image/png')
 
 
@app.route("/optimize/<id>/run", methods=["POST"])
def optimization_run_again(id):
    """Append more runs to an existing result."""
    result_dir = CAM_RESULTS_DIR / id
    if not result_dir.exists():
        return "Result not found", 404
 
    params   = json.loads((result_dir / 'params.json').read_text())
    num_runs = int(request.form.get('num_runs', 1))
 
    launch_optimization(result_dir, params, num_runs, f"Camera optimization: {params['svg_filename']}")
    return redirect(f'/optimize/{id}')

