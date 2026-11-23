import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from .. import config, tasks
from .common import *

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dataset_get(request: Request, model: str):
    if model not in config.model_manifest or not (config.CACHE / model).exists():
        return RedirectResponse(request.state.url_for_query("models", model=None))

    now = datetime.now()
    old = {pid: task for pid, task in tasks.tasks.items() if TourStep.DATASET in task}

    tools_installed = {}
    for category, vals in config.models_config["tools"].items():
        if not vals:
            continue

        tools_installed[category] = []
        for alias, info in vals.items():
            manifest = config.model_manifest[info.model]
            custom = (
                alias not in config.model_manifest
                or category not in manifest["tool"]["default"]
            )
            src = manifest["source"]
            tools_installed[category].append(
                (
                    alias,
                    src["group"],
                    src["owner"],
                    (config.CACHE / info.model).exists(),
                    info,
                    custom,
                )
            )

    return templates.TemplateResponse(
        request=request,
        name="dataset.html",
        context=dict(
            now=now,
            tasks=old,
            tools=tools_installed,
            **config.model_manifest[model],
        ),
    )


async def receive_files(base_dir: Path, files: list[UploadFile]):
    for file in files:
        try:
            if not file.filename:
                raise ValueError("Invalid file")
            contents = await file.read()
            with (base_dir / file.filename).open("wb") as f:
                f.write(contents)
        except ValueError:
            shutil.rmtree(base_dir)
            raise HTTPException(status_code=500, detail="Something went wrong")
        finally:
            await file.close()


class DatasetCreation(BaseModel):
    dataset: str
    title: str | None = None
    group_size: int
    group_separation: str
    regex_include: str
    regex_exclude: str = ""
    shared: bool = False
    files: list[UploadFile] | None = []


@router.post("/", response_class=HTMLResponse)
async def dataset(
    request: Request, data: Annotated[DatasetCreation, Form()], model: str
):
    if model not in config.model_manifest or not (config.CACHE / model).exists():
        raise HTTPException(status_code=404, detail="Model is not installed")

    dataset = Path(data.dataset)
    if not dataset.is_relative_to(config.DATASET_DIR):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot place dataset outside '{config.DATASET_DIR!s}'",
        )
    dataset.mkdir(exist_ok=True, parents=True)
    data.files = [f for f in data.files or [] if (f.size or 0) > 0]
    if len(data.files) > 0:
        if next(dataset.iterdir(), False):
            raise HTTPException(
                status_code=400,
                detail="Cannot create dataset from upload if directory already exists",
            )
        await receive_files(dataset, data.files)

    data_dict = data.model_dump()
    data_dict["tools"] = {
        kind: [(alias, tool.name) for alias, tool in tools.items() if tool.enabled]
        for kind, tools in config.models_config["tools"].items()
    }
    del data_dict["files"]
    env = {
        "MODEL_DIR": config.model_manifest[model]["dir"],
        "CREATION_REQUEST": json.dumps(data_dict),
    }
    pid = tasks.start_task(
        ["uv", "run", "create.py"],
        "../ls-utils",
        "Project creation",
        extra_env=env,
        blocking=True,
    )
    task = tasks.tasks[pid]
    task[TourStep.DATASET] = None
    id = None
    while line_in := task["process"].stdout.readline():
        task["output"].append(line_in)
        try:
            id = int(line_in)
            break
        except ValueError:
            pass
    os.set_blocking(task["process"].stdout.fileno(), False)

    if id is None:
        return RedirectResponse(
            request.state.url_for_query("logs", pid=pid), status_code=303
        )
    task[TourStep.DATASET] = id

    return RedirectResponse(
        request.state.url_for_query("label", project=id), status_code=303
    )


class DatasetAddition(BaseModel):
    dataset: str
    group_separation: str
    files: list[UploadFile]


@router.post("/upload", response_class=HTMLResponse)
async def dataset_upload(
    request: Request, data: Annotated[DatasetAddition, Form()], project: int
):
    if not (base := Path(data.dataset)).exists():
        raise HTTPException(status_code=400, detail="Provided dataset does not exist")
    base = base / datetime.now().strftime("upload-%Y-%m-%d-%H-%M-%S")
    base.mkdir()
    await receive_files(base, data.files)
    addition = json.dumps(
        {
            "project": project,
            "upload_dir": str(base),
            "group_separation": data.group_separation,
        }
    )
    pid = tasks.start_task(
        ["uv", "run", "add.py"],
        "../ls-utils",
        "Adding tasks to project",
        extra_env={"ADDITION_REQUEST": addition},
    )
    tasks.tasks[pid][TourStep.DATASET] = project

    return RedirectResponse(request.state.url_for_query("label"), status_code=303)


@router.post("/external")
async def dataset_from_upload(request: Request, file: Annotated[UploadFile, File()]):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid file")
    _, ext = os.path.splitext(os.path.basename(file.filename))
    with tempfile.NamedTemporaryFile(suffix=ext, mode="wb") as f:
        while chunk := await file.read(1024):
            f.write(chunk)
        f.flush()

        with tempfile.TemporaryDirectory(delete=False) as d:
            shutil.unpack_archive(f.name, d)

            root = Path(d)
            if len(files := list(root.iterdir())) == 1:
                root = files[0]

            if (target := config.DATASET_DIR / root.name).exists():
                raise HTTPException(
                    status_code=400, detail=f"Dataset {target.name!s} already exists"
                )
            elif (root / "manifest.json").exists():
                shutil.move(root, target)
                return {"manifest": str(target / "manifest.json")}
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Uploaded archive doesn't contain a manifest",
                )
