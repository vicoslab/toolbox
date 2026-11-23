import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .. import config, tasks
from .common import *

router = APIRouter()


@router.get("/logs/{pid}/stream", response_class=EventSourceResponse)
async def logs_stream(pid: int, last_event_id: Annotated[int | None, Header()] = None):
    if not (task := tasks.tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    out = task["output"]
    i = 0 if last_event_id is None else last_event_id + 1
    old = None
    while True:
        if i < len(out) - 1:
            i += 1
            data = out[i - 1]
            yield ServerSentEvent(
                data=data,
                event="newline" if type(data) == str else "quick-action",
                id=str(i),
            )
            continue
        elif i < len(out):
            if out[i] != old:
                old = out[i]
                if type(old) == str:
                    yield ServerSentEvent(data=old)
                else:
                    yield ServerSentEvent(data=old, event="quick-action")
            elif task["process"] is None:
                break
        await asyncio.sleep(1)
    yield ServerSentEvent(data="end of logs", event="eof")


@router.get("/logs/{pid}", response_class=HTMLResponse)
def logs(request: Request, pid: int):
    if not (task := tasks.tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    if config.debug:  # in production mode this gets refreshed in a thread
        tasks.refresh_logs(tasks.tasks[pid])

    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "pid": pid,
            "description": task["description"],
            "info": task["info"],
            "running": task["code"] is None,
        },
    )


@router.get("/status", response_class=HTMLResponse)
def status(request: Request):
    now = datetime.now()
    timestamps = {
        pid: now - task.get("end_time", task["start_time"])
        for pid, task in tasks.tasks.items()
    }
    return templates.TemplateResponse(
        request=request,
        name="status.html",
        context={"tasks": tasks.tasks, "timestamps": timestamps},
    )


@router.post("/stop/{pid}")
def kill(request: Request, pid: int):
    if not (task := tasks.tasks.get(pid)):
        raise HTTPException(status_code=404, detail="Task does not exist")
    if task["process"] is not None:
        task["process"].terminate()
    return RedirectResponse(
        request.state.url_for_query("logs", pid=pid), status_code=303
    )  # 303 changes to GET
