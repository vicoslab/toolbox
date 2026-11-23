from datetime import datetime
from pathlib import Path

import mlflow
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, logic, tasks
from ..logic import ToolboxError
from .common import *

router = APIRouter(prefix="/{model}")


@router.post("/train")
async def model_train(request: Request, model: str):
    """Start training run with given options."""
    options = await request.json()
    pid = logic.train(ToolboxError.http, options, model)

    return TaskResponse(
        pid=pid, logs=str(request.state.url_for_query("logs", model=model, pid=pid))
    )


@router.post("/infer")
async def model_infer(request: Request, model: str, alias: str | None = None):
    options = await request.json()
    pid = logic.infer(ToolboxError.http, options, model, alias)
    return TaskResponse(
        pid=pid, logs=str(request.state.url_for_query("logs", model=model, pid=pid))
    )


@router.get("/options")
def model_options(
    request: Request,
    model: str,
    manifest: str | None = None,
    weights: str | None = None,
):
    if not (model_info := config.model_manifest.get(model)):
        raise HTTPException(status_code=404, detail=f"Model '{model}' does not exist")
    options = model_info.get("options", {})

    runs = mlflow.search_runs(
        experiment_names=[model_info["title"]], max_results=100, output_format="list"
    )
    completions = {}
    for k, v in options.items():
        if not (format := v.get("format")):
            continue
        if format == "file:manifest.json":
            manifests = {}
            found = config.DATASET_DIR.rglob("manifest.json")
            for m in sorted(found, key=lambda x: str(x)):
                parent = m.parent
                if parent.name.startswith(".export"):
                    parent = parent.parent
                if parent not in manifests:
                    manifests[parent] = []
                manifests[parent].append((m.relative_to(parent), str(m)))
            if manifest or len(manifests) > 0:
                completions[k] = [manifest, list(manifests.items())]
        elif format.startswith("file:"):
            _completions = []
            if len(runs) > 0 and config.ARTIFACTS:
                for run in runs:
                    files = list(
                        Path(config.ARTIFACTS).glob(
                            f"{run.info.experiment_id}/{run.info.run_id}/artifacts/**/{format[5:]}"
                        )
                    )
                    if len(files) > 0:
                        _completions.append(
                            (
                                f"{datetime.fromtimestamp(run.info.start_time / 1000).strftime('%Y-%m-%d %H-%M')} :: {run.info.run_name}",
                                [
                                    (
                                        file.name,
                                        f"mlflow-artifacts:/{file.relative_to(config.ARTIFACTS)}",
                                    )
                                    for file in files
                                ],
                            )
                        )
            if weights or len(_completions) > 0:
                completions[k] = [weights, _completions]

    alias = {
        "title": "Alias",
        "description": "Alias for inference worker",
        "type": "string",
    }
    return {"options": {**options, "alias": alias}, "completions": completions}


def handle_model(
    template: str,
    request: Request,
    model: str,
    manifest: str | None = None,
    weights: str | None = None,
):
    if model not in config.model_manifest or not (config.CACHE / model).exists():
        return RedirectResponse(request.state.url_for_query("models"))

    params = {"model": model}
    if manifest:
        params["manifest"] = manifest
    if weights:
        params["weights"] = weights

    # alias: (pid, model)
    workers = {
        inf[0]: (pid, inf[1])
        for (pid, task) in tasks.tasks.items()
        if (inf := task.get(TourStep.INFERENCE)) and task["process"] is not None
    }
    now = datetime.now()
    runs = {pid: task for pid, task in tasks.tasks.items() if TourStep.TRAINING in task}

    sources = {}
    for src in config.models_config["sources"]:
        models = []
        for m in src["models"]:
            if Path(config.CACHE, id := f"{src['sid']}-{m}").exists():
                models.append(id)
        if models:
            sources[f"{src['group']} by {src['owner']}"] = models

    print(params, flush=True)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "sources": sources,
            "now": now,
            "runs": runs,
            "workers": workers,
            "params": params,
        },
    )


@router.get("/infer", response_class=HTMLResponse)
def infer(
    request: Request,
    model: str,
    manifest: str | None = None,
    weights: str | None = None,
):
    return handle_model("infer.html", request, model, manifest, weights)


@router.get("/train", response_class=HTMLResponse)
def train(
    request: Request,
    model: str,
    manifest: str | None = None,
    weights: str | None = None,
):
    return handle_model("train.html", request, model, manifest, weights)


@router.post("/install")
def model_install(request: Request, model: str):
    """Start model installation for given model id."""
    pid = logic.install(ToolboxError.http, model)
    return TaskResponse(
        pid=pid, logs=str(request.state.url_for_query("logs", model=model, pid=pid))
    )


@router.post("/uninstall")
def model_uninstall(model: str):
    """Remove installation directory of given model id."""
    return logic.uninstall(ToolboxError.http, model)
