import io
import zipfile
from pathlib import Path

import mlflow
import requests
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config
from .common import *

router = APIRouter()


def file_tree(path: Path):
    tree = {}
    for root, dirs, files in path.walk():
        current = tree
        for part in root.relative_to(path).parts:
            current = current[part]
        for directory in dirs:
            current[directory] = {}
        for file in files:
            current[file] = None
    return tree


@router.get("/")
def finish(request: Request):
    return templates.TemplateResponse(request=request, name="finish.html")


@router.get("/list")
def finish_list(
    request: Request,
    model: str,
    manifest: str | None = None,
    project: int | None = None,
):
    if not (model_info := config.model_manifest.get(model)):
        raise HTTPException(status_code=404, detail="Model does not exist")
    if not manifest and not project:
        raise HTTPException(
            status_code=400, detail="You must provide either a project or a manifest"
        )
    if manifest:
        if not (manifest_ := Path(manifest)).exists():
            raise HTTPException(status_code=404, detail="Manifest does not exist")
        root = manifest_.parent
    if project:
        response = requests.get(
            "http://localhost:8080/api/storages/localfiles",
            headers={"Authorization": f"Token {config.API_KEY}"},
            params={"project": project},
            timeout=30,
        )
        if response.status_code != 200:
            return Response(response.text, status_code=response.status_code)
        storages = response.json()
        if len(storages) == 0:
            raise HTTPException(
                status_code=404, detail="No import storages associated with project"
            )
        root = Path(storages[0]["path"])

    if root.name.startswith(".export"):
        root = root.parent

    run_files = []
    experiment = None
    manifests = [str(x) for x in root.rglob("manifest.json")]
    runs = mlflow.search_runs(
        experiment_names=[model_info["title"]], max_results=100, output_format="list"
    )
    for run in runs:
        params = run.data.params.values()
        if not any(m in params for m in manifests):
            continue

        experiment = run.info.experiment_id
        path = Path(config.ARTIFACTS) / experiment / run.info.run_id / "artifacts"
        if len(tree := file_tree(path)) > 0:
            run_files.append((run.info.run_id, run.info.run_name, tree))

    return {
        "runs": run_files,
        "experiment": experiment,
        "dataset": file_tree(root),
        "dataset_root": str(root),
    }


def zip_response(dirs, filename):
    zip_buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for x in dirs:
                if x.is_dir():
                    for root, _, files in x.walk():
                        for f in files:
                            zip_file.write(root / f, (root / f).relative_to(x.parent))
                else:
                    zip_file.write(x, x.name)

        zip_buffer.seek(0)
        headers = {"Content-Disposition": "attachment; filename=" + filename}
        return Response(
            zip_buffer.getvalue(), headers=headers, media_type="application/zip"
        )
    except ValueError:
        raise HTTPException(
            detail="There was an error processing the data", status_code=400
        )
    finally:
        zip_buffer.close()


@router.get("/download/dataset")
def finish_dataset(path: str):
    if not (file := Path(path)).exists() or not file.is_relative_to(config.DATASET_DIR):
        raise HTTPException(status_code=400, detail="Invalid file")
    if file.is_file():
        return FileResponse(
            file, media_type="application/octet-stream", filename=file.name
        )
    else:
        return zip_response([file], "dataset.zip")


class DownloadRuns(BaseModel):
    experiment: int
    runs: dict[str, str | None]


@router.post("/download/runs")
def finish_runs(request: Request, data: DownloadRuns):
    paths = []
    for run, subpath in data.runs.items():
        path = Path(config.ARTIFACTS) / str(data.experiment) / run / "artifacts"
        if subpath:
            path = path / subpath
        if not path.exists():
            raise HTTPException(status_code=400, detail="Invalid path")
        paths.append(path)
    if len(paths) == 1 and paths[0].exists() and paths[0].is_file():
        return FileResponse(
            paths[0], media_type="application/octet-stream", filename=path.name
        )
    else:
        return zip_response(paths, "runs.zip")
