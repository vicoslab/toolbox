import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from subprocess import PIPE, STDOUT, Popen

from . import config
from .config import WorkerInfo
from .http.common import TourStep

tasks = {}


def start_task(command, cwd, description, extra_env={}, blocking=False, stdin=False):
    stdin = PIPE if stdin else None
    proc = Popen(
        command,
        cwd=cwd,
        stdin=stdin,
        stdout=PIPE,
        stderr=STDOUT,
        text=True,
        env={**os.environ, **extra_env},
    )
    tasks[proc.pid] = {
        "description": description,
        "output": [],
        "info": [],
        "process": proc,
        "code": None,
        "start_time": datetime.now(),
    }
    if proc.stdout:
        os.set_blocking(proc.stdout.fileno(), blocking)
    return proc.pid


def build_model_options(options, values):
    flags = []
    for k, v in values:
        if v != "" and k in options:
            if type(v) == str and v.startswith("mlflow-artifacts:"):
                v = v.replace("mlflow-artifacts:", config.ARTIFACTS, count=1)
            if type(v) == list:
                flags.extend(["--" + k, *map(str, v)])
            else:
                flags.extend(["--" + k, str(v)])
    return flags


def create_inference_worker(model, values, alias=None):
    options = config.model_manifest[model]["options"]
    flags = build_model_options(options, values.items())

    port = 9091
    used = [
        worker[1].port
        for task in tasks.values()
        if task["process"] and (worker := task.get(TourStep.INFERENCE))
    ]
    while True:
        if port not in used:
            break
        port += 1

    # --no-control-socket disables control-socket, which we aren't using, and logs errors in configurations with uid-only user
    pid = start_task(
        [
            "uv",
            "run",
            "gunicorn",
            "--bind",
            f":{port}",
            "--log-config",
            Path.cwd() / "worker-logging.conf",
            "--no-control-socket",
            "infer:app",
            "--",
            *flags,
        ],
        config.model_manifest[model]["dir"],
        f"Inference service worker for: `{model}`",
        {
            "UV_PROJECT_ENVIRONMENT": config.CACHE / model / ".venv",
            "INSTALL_DIR": config.CACHE / model,
            "LABEL_STUDIO_URL": "http://localhost:8080",
            "XDG_DATA_HOME": "/tmp",
        },
    )
    options = {
        opt["title"]: v
        for k, v in values.items()
        if (opt := options.get(k)) and opt.get("stage") in [None, "infer"]
    }
    tasks[pid][TourStep.INFERENCE] = (
        alias or model,
        WorkerInfo(port=port, model=model, options=options),
    )
    return pid


# regex for grouping log entries
# if line starts with eval:, visualise: or (for example) 10/300: (from something like a tqdm batch counter)
# consecutive log lines get grouped up (most recent one is shown)
tqdm_header = re.compile(r"^(\d+)/(\d+):|^(eval):|^(visualise):")
quick_action = re.compile(r"^Toolbox:(\w+): (.+)$")


def refresh_logs(task):
    if proc := task.get("process"):
        out = task["output"]

        last = None
        if len(out) and type(out[-1]) == str and (m := tqdm_header.match(out[-1])):
            last = m.groups()

        while line := proc.stdout.readline():
            line = line.rstrip()
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
                    kind, data = m.groups()
                    if kind == "Info":
                        task["info"].append(json.loads(data))
                    out.append((kind, data))
                else:
                    out.append(line)
                last = None

        if (code := proc.poll()) is not None:
            task["code"] = code
            task["end_time"] = datetime.now()
            task["process"] = None


# debug mode is single-threaded, which makes it hang
if not config.debug:
    from threading import Thread

    def monitor_task():
        while True:
            time.sleep(1)
            for task in tasks.values():
                if task["process"] and not os.get_blocking(
                    task["process"].stdout.fileno()
                ):
                    refresh_logs(task)

    # this is needed because task's writes will start blocking if output is not consumed
    Thread(target=monitor_task, daemon=True).start()
